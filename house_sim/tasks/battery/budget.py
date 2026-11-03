"""Keeping the battery bounded (VERIFY_HARDENING 10.5, 11.4, 12.2, 22.2, 22.6; WP6).

    tier_of(spec, builds, ctxs) -> tier row of budget.json   from the formula time budget (verify's budget record
                                   when the caller passes it, else 1.25-3 x the references' times: 22.2's estimate,
                                   rounded towards the long tier), never from the authored budget
    Budget(level, tier, what="battery")
        .allow(family, sim_s=0) -> None | reason      a run may start (run caps per family and per spec, the CPU cap
                                                      of the tier and level, the simulated-hours cap)
        .spend(family, cpu_s, sim_s)                   book a run
        .summary()
    family_seeds(family, ctxs, tier) -> [seed]        10.5's seed subsets: one per variant or branch value first, then
                                                      the lowest seeds
FAMILY_CAPS (10.5), MUSTPASS_CAPS (11.4), PRIVATE_CAPS (12.2) and the per-spec totals are the spec's numbers.
"""
import json
import os

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "verify")
BUDGET_PATH = os.path.join(DATA, "budget.json")
FAMILY_CAPS = {"B2": 9, "B3": 24, "B4": 18, "B11": 24, "B5a": 0, "B5b": 0, "B5c": 12, "B6": 9, "B7": 24,
               "B8": 0, "B9": 6, "B10": 9, "B12": 40, "B13": 3, "B14": 6}
SPEC_RUNS = {"quick": 0, "standard": 150, "full": 200}
MUSTPASS_CAPS = {"M1": 6, "M2": 12, "M3": 3, "M4": 3, "M5": 4, "M6": 3}
MUSTPASS_SPEC = 30
PRIVATE_CAPS = {"P3": 5, "P4": 3, "P5": 3, "P7": 8, "P8": 2, "PM": 3}
PRIVATE_SPEC = 30
ORDER = ("B2", "B3", "B6", "B12", "B4", "B13", "B8", "B5", "B7", "B9", "B10", "B14")
_TABLE = None


def table():
    global _TABLE
    if _TABLE is None:
        with open(BUDGET_PATH, encoding="utf-8") as fh:
            _TABLE = json.load(fh)
    return _TABLE


def tier_row(tier_id):
    for row in table()["tiers"]:
        if row["id"] == tier_id:
            return row
    return table()["tiers"][0]


def tier_by_seconds(seconds):
    for row in table()["tiers"]:
        if row["max_s"] is None or seconds <= row["max_s"]:
            return row
    return table()["tiers"][-1]


def formula_estimate(ctxs):
    """22.2's planning estimate of the formula time budget when the verifier's budget record is not at hand: 3 x
    the reference's active time + 1.25 x its waits + 180 s, over the seeds' references (the longest)."""
    f = table()["formula"]
    best = 0.0
    for ctx in ctxs:
        ref = ctx.record or {}
        active = float(ref.get("active_s") or ref.get("time_s") or 0.0)
        waited = float(ref.get("waited_s") or 0.0)
        best = max(best, f["active_factor"] * active + f["wait_factor"] * waited + f["time_slack_s"])
    return best


def tier_of(builds, ctxs):
    budget = (builds or {}).get("budget") or {}
    tier = (builds or {}).get("tier") or budget.get("tier")
    if isinstance(tier, str):
        return tier_row(tier)
    seconds = budget.get("formula_time_s")
    if not isinstance(seconds, (int, float)):
        seconds = formula_estimate(ctxs)
    return tier_by_seconds(float(seconds))


def sim_hours_cap(tier, budget_s):
    hours = tier.get("sim_hours_factor", 80) * float(budget_s or 0.0) / 3600.0
    if tier.get("sim_hours_min") is not None:
        hours = max(hours, float(tier["sim_hours_min"]))
    if tier.get("sim_hours_max") is not None:
        hours = min(hours, float(tier["sim_hours_max"]))
    return hours


class Budget(object):
    """Run, CPU and simulated-hour caps for one stage of one spec (module docstring)."""

    def __init__(self, level, tier, what="battery", budget_s=0.0, caps=None, spec_runs=None, cpu_spent=0.0):
        self.level, self.tier, self.what = level, tier, what
        self.caps = dict(caps if caps is not None else FAMILY_CAPS)
        self.spec_runs = spec_runs if spec_runs is not None else SPEC_RUNS.get(level, 150)
        self.cpu_cap = float((tier.get("cpu_cap_s") or {}).get(level if level != "quick" else "standard", 600))
        self.cpu = float(cpu_spent)
        self.sim_cap_s = sim_hours_cap(tier, budget_s) * 3600.0
        self.sim_s = 0.0
        self.runs = 0
        self.by_family = {}
        self.cut = {}

    def allow(self, family, sim_s=0.0):
        cap = self.caps.get(family)
        used = self.by_family.get(family, 0)
        if cap and used >= cap:
            return "{} cap of {} runs".format(family, cap)
        if self.spec_runs and self.runs >= self.spec_runs:
            return "the spec's cap of {} {} runs".format(self.spec_runs, self.what)
        if self.cpu >= self.cpu_cap:
            return "the {} tier's CPU cap of {:.0f} s".format(self.tier.get("id"), self.cpu_cap)
        if self.sim_s + float(sim_s or 0.0) > self.sim_cap_s + 1e-6:
            return "the simulated-hours cap of {:.1f} h".format(self.sim_cap_s / 3600.0)
        return None

    def spend(self, family, cpu_s, sim_s):
        self.runs += 1
        self.by_family[family] = self.by_family.get(family, 0) + 1
        self.cpu += float(cpu_s or 0.0)
        self.sim_s += max(0.0, float(sim_s or 0.0))

    def note_cut(self, family, why):
        self.cut.setdefault(family, why)

    def summary(self):
        return {"runs": self.runs, "by_family": dict(sorted(self.by_family.items())), "cpu_s": round(self.cpu, 3),
                "cpu_cap_s": self.cpu_cap, "sim_hours": round(self.sim_s / 3600.0, 3),
                "sim_hours_cap": round(self.sim_cap_s / 3600.0, 3), "tier": self.tier.get("id"),
                "cut": dict(sorted(self.cut.items()))}


def family_seeds(ctxs, n):
    """n seeds: one per variant (or branch value) first, then the lowest remaining seeds, in V's order."""
    ordered = list(ctxs)
    out, seen = [], set()
    for ctx in ordered:
        key = json.dumps([ctx.variant, ctx.branch], default=str)
        if key not in seen:
            seen.add(key)
            out.append(ctx)
    for ctx in ordered:
        if ctx not in out:
            out.append(ctx)
    variants = len(seen)
    return out[:max(int(n), 1) if n else 1] if variants <= n else out[:variants]
