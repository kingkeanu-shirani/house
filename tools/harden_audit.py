"""R0, the audit of every authored spec as if it were schema 4, and the cost calibration (docs/design/VERIFY_HARDENING.md
23.1, 19.4, 22.6, 21.1; WP10).

usage: python tools/harden_audit.py [--files GLOB] [--ids ID ...] [--level standard|full] [--workers N] [--out DIR] [--cost]
                                    [--check-log DIR] [--census]

Without --cost: every authored spec (or the files matching --files) is evaluated as if it were schema 4, with heuristic
drafts filled in: the generalised envelope (6.6), the classifier's protect map, draft answers, instruction roles from the
defaults of 13.7 (never `context` for a placeholder after a destination preposition), proposed `process` labels and the
look-ahead trigger. Nothing in tasks/authored changes and nothing is written to the check log.

Wrong plans get no `expect`: gate G2 is reported as "expect missing", with the labels each plan was observed to fail on as
suggestions. To let the stages run at all, the as-if spec carries those observed labels as a stand-in `expect` and the
static lints do not stop the spec (they are reported in full, and G5 is reported false when one is an error): that is
the only way the audit differs from `--check`. The private stages run when this machine has the split key.

Output (DIR, default reports/harden_audit):
  specs.jsonl    per spec: gates, verdict, score, kind class, what failed the reference per mode and seed (G1 stops the later
                 stages), every unmet policy with its evidence, every must-pass failure, the escapes the spec would need, the
                 lint errors, the expect suggestions, the cost
  summary.json   per domain: counts by verdict, the counterfactual charges on references (the residual of 6.2), the battery
                 debt (unrealised runs by family); the HARDENED rate per (kind class, domain) (19.4); the cost by tier
  summary.md     the same as a report
With --census the run also refreshes tools/field_census.py's output (R0 re-reads what the systems write, WP1's request):
DIR/field_census.json.

--cost: the measured mean CPU cost per tier and stage from the check log (catalog/review/check_log/*.jsonl, its cpu_s and
cpu_by_stage; with --from FILE an audit's specs.jsonl) against the planning figures of budget.json (22.4). A tier or stage
more than 25 % above plan raises a calibration alarm: its owner (WP5 for the core trials and audits, WP6 for the batteries)
gets the most expensive specs as work items. The figures change only by the owner's decision, never by raising a cap
silently. R0's run is the first calibration; the schema-4 authoring wave starts only when its projection is within the
25 % contingency, or the owner accepted the new figure.

Stdlib only. No network, no paid calls.
"""
import argparse
import copy
import fnmatch
import glob
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

UNMET = ("passes", "fails_other", "unrealised", "void", "skipped:cap", "error")
STAGE_OWNERS = {"S0": "WP8", "S1": "WP5", "S2": "WP5", "S3": "WP5", "S5": "WP6", "S6": "WP6", "S7": "WP5", "S7p": "WP6",
                "S8": "WP5"}
CATALOG_MIX = {"H0": 0.88, "H1": 0.08, "H2": 0.03, "H3": 0.01}     # 22.2's planning shares of the catalog
CATALOG_ENTRIES = 2720


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _answer_kind(values):
    """A draft answer's kind from the binding's values over the seeds (9.1)."""
    parsed = []
    for value in values:
        try:
            parsed.append(json.loads(value))
        except (TypeError, ValueError):
            parsed.append(value)
    if all(isinstance(v, bool) for v in parsed):
        return "word"
    if all(isinstance(v, (int, float)) for v in parsed):
        return "number"
    if all(isinstance(v, list) for v in parsed):
        return "set"
    return "entity"


def as_if(spec, draft):
    """The spec as if it were schema 4, from the drafts (23.1): a copy, never written anywhere. Returns (spec, notes)."""
    out = copy.deepcopy(spec)
    out["schema"] = 4
    notes = {"expect_missing": [], "filled": []}
    if isinstance(draft.get("envelope"), dict) and "envelope" not in out:
        out["envelope"] = draft["envelope"]
        notes["filled"].append("envelope")
    protect = draft.get("protect_map")
    if isinstance(protect, dict) and "protect_map" not in out:
        # lint.draft_protect_map: the classifier's candidates; what it could not map stays unmapped (L-PROTECT reports it)
        mapped = [{"item": row["item"], "by": row["by"]} for row in protect.get("protect_map") or () if row.get("by")]
        if mapped:
            out["protect_map"] = mapped
            notes["filled"].append("protect_map")
        if protect.get("restore") and "protections" not in out:
            out["protections"] = {"restore": list(protect["restore"])}
            notes["filled"].append("restore")
    elif isinstance(protect, list) and "protect_map" not in out:
        out["protect_map"] = protect
        notes["filled"].append("protect_map")
    roles = draft.get("instruction_roles")
    if isinstance(roles, dict) and roles and "instruction_roles" not in out:
        out["instruction_roles"] = roles
        notes["filled"].append("instruction_roles")
    if draft.get("process") and "process" not in out:
        out["process"] = list(draft["process"])
        notes["filled"].append("process")
    if draft.get("lookahead_triggered") and "lookahead_s" not in out and draft.get("lookahead_s"):
        out["lookahead_s"] = draft["lookahead_s"]
        notes["filled"].append("lookahead_s")
    answers = (draft.get("answers") or {}).get("candidates") or []
    if answers and "answers" not in out:
        out["answers"] = {name: {"bind": name, "kind": "entity"} for name in answers}
        notes["filled"].append("answers")
    observed = draft.get("wrong_observed") or {}

    def fill(plans):
        for item in plans or ():
            if not isinstance(item, dict):
                continue
            labels = observed.get(item.get("name")) or []
            if not item.get("expect"):
                notes["expect_missing"].append({"name": item.get("name"), "observed": labels})
                item["expect"] = labels[:3] or ["goal:unobserved"]
            if not item.get("mistake"):
                item["mistake"] = [max(0, len(item.get("plan") or ()) - 1)]      # a stand-in, as the expect is
    fill(out.get("wrong"))
    for variant in out.get("variants") or ():
        if isinstance(variant, dict):
            fill(variant.get("wrong"))
            fill(variant.get("+wrong"))
    return out, notes


def audit_spec(job):
    """One spec's audit in a worker: draft, as-if spec, the hardened pipeline without check log; the static lints are read
    and kept but do not stop the spec."""
    spec, level, source = job["spec"], job["level"], job.get("file")
    os.environ.setdefault("HOUSE_SIM_WORKER", "1")
    from house_sim.kernel.plugins import load_domains
    from house_sim.tasks import verify
    load_domains()
    started = time.process_time()
    row = {"catalog_id": spec.get("catalog_id"), "file": source, "schema": spec.get("schema"), "level": level}
    try:
        draft = verify.draft_hardening(spec)
        target, notes = as_if(spec, draft)
        kept = []
        original = verify.static_lints

        def lints(*args, **kwargs):
            rows = original(*args, **kwargs)
            kept.extend(rows)
            return [dict(r, severity="warning") if r.get("severity") == "error" and r.get("id") != "STRUCTURE" else r
                    for r in rows]
        verify.static_lints = lints
        try:
            record = verify.verify_hardened(target, level, check_log=False, cache=False)
        finally:
            verify.static_lints = original
        row.update(summarise(record, kept, notes, draft))
    except Exception as exc:        # noqa: BLE001  (an audit row, not a crash: a harness error is a finding)
        row.update(verdict="FAIL", gates={"G7": False}, score=0,
                   harness_errors=["{}: {}".format(type(exc).__name__, str(exc)[:300])])
    row["audit_cpu_s"] = round(time.process_time() - started, 2)
    return row


def summarise(record, static, notes, draft):
    """The per-spec row of 23.1 from an evidence record."""
    gates = dict(record.get("gates") or {})
    errors = [p for p in list(static) + list(record.get("lint") or []) if p.get("severity") == "error" and
              p.get("id") != "L-EXPECT"]      # the stand-in expects and mistakes are G2's "expect missing", below
    seen, lint = set(), []
    for p in errors:
        ident = (p.get("id"), p.get("message"))
        if ident not in seen:
            seen.add(ident)
            lint.append({"id": p.get("id"), "message": str(p.get("message"))[:300]})
    if lint:
        gates["G5"] = False
    missing = notes.get("expect_missing") or []
    g2_note = None
    if missing:
        gates["G2"] = False
        g2_note = "expect missing ({} wrong plan(s)): the observed labels are suggestions".format(len(missing))
    battery = record.get("battery") or {}
    mustpass = record.get("mustpass") or {}
    unmet = [{k: r.get(k) for k in ("id", "family", "seed", "outcome", "labels", "accepted", "hint", "why", "refusals")
              if r.get(k) not in (None, [])} for r in battery.get("runs") or ()
             if isinstance(r, dict) and r.get("outcome") in UNMET]
    must = [{k: r.get(k) for k in ("id", "seed", "outcome", "rejected_by", "labels", "hint") if r.get(k) not in (None, [])}
            for r in mustpass.get("runs") or () if isinstance(r, dict) and r.get("outcome") not in ("passes", "skipped:na",
                                                                                                    "skipped:tier")]
    debt = {}
    for r in battery.get("runs") or ():
        if isinstance(r, dict) and r.get("outcome") == "unrealised":
            debt[str(r.get("family"))] = debt.get(str(r.get("family")), 0) + 1
    charges = sum(1 for t in record.get("trials") or () if isinstance(t, dict) for f in
                  (t.get("reference") or {}).get("envelope_violations") or ())
    escapes = record.get("escapes") or {}
    strength = record.get("strength") or {}
    failing = {}
    for t in record.get("trials") or ():
        ref = t.get("reference") if isinstance(t, dict) else None
        if isinstance(ref, dict) and not ref.get("passes") and "{}/{}".format(t.get("mode"), t.get("seed")) not in failing:
            failing["{}/{}".format(t.get("mode"), t.get("seed"))] = (list(ref.get("failures") or []) + list(
                ref.get("unmet") or []) + ([str(ref["error"])[:200]] if ref.get("error") else []))[:8]
    return {"verdict": record.get("verdict"), "gates": gates, "score": strength.get("score"),
            "reference_failures": failing,
            "components": strength.get("components"), "g2_note": g2_note, "expect_suggestions": missing, "lint": lint,
            "unmet": unmet, "mustpass_failures": must, "battery_debt": debt, "counterfactual_charges": charges,
            "escapes": [{k: e.get(k) for k in ("policy", "points", "review", "needs_review")}
                        for e in escapes.get("escapes") or ()], "escape_points": escapes.get("points"),
            "kind_class": (record.get("coverage") or {}).get("kind_class"), "expected": (record.get("coverage") or {}).get(
                "expected"), "tier": record.get("tier"), "cpu_s": record.get("cpu_s"),
            "cpu_by_stage": record.get("cpu_by_stage"), "drafted": notes.get("filled"),
            "private": {"ran": bool((record.get("private") or {}).get("ran")),
                        "summary": (record.get("private") or {}).get("summary")},
            "battery_budget": (battery.get("summary") or {}).get("budget"),
            "harness_errors": list(record.get("harness_errors") or [])[:5],
            "memorisation_candidates": (draft.get("answers") or {}).get("constant_success_values")}


def domain_of(source):
    source = str(source or "")
    return source.split("__")[0] if "__" in source else Path(source).stem


def summary_of(rows):
    """Per domain: counts by verdict, counterfactual charges, battery debt; the HARDENED rate per (kind class, domain)."""
    domains, strata, verdicts = {}, {}, {}
    for row in rows:
        verdict = row.get("verdict") or "FAIL"
        verdicts[verdict] = verdicts.get(verdict, 0) + 1
        domain = domain_of(row.get("file"))
        block = domains.setdefault(domain, {"specs": 0, "verdicts": {}, "counterfactual_charges": 0, "battery_debt": {},
                                            "expect_missing": 0, "lint_errors": 0})
        block["specs"] += 1
        block["verdicts"][verdict] = block["verdicts"].get(verdict, 0) + 1
        block["counterfactual_charges"] += int(row.get("counterfactual_charges") or 0)
        block["expect_missing"] += len(row.get("expect_suggestions") or ())
        block["lint_errors"] += len(row.get("lint") or ())
        for family, count in (row.get("battery_debt") or {}).items():
            block["battery_debt"][family] = block["battery_debt"].get(family, 0) + count
        cell = strata.setdefault("{}/{}".format(row.get("kind_class") or "plain", domain), {"specs": 0, "hardened": 0})
        cell["specs"] += 1
        cell["hardened"] += verdict == "HARDENED"
    for cell in strata.values():
        cell["rate"] = round(cell["hardened"] / float(cell["specs"]), 3)
    return {"specs": len(rows), "verdicts": verdicts, "domains": domains, "strata": strata}


# ----------------------------------------------------------------------------------------------------------------
# --cost: the calibration of 22.6

def _planning():
    with open(ROOT / "house_sim" / "data" / "verify" / "budget.json", encoding="utf-8") as stream:
        return json.load(stream)["planning"]


def _check_log_rows(folder=None):
    from house_sim.tasks import checklog
    out = []
    root = Path(folder) if folder else checklog.log_dir()
    for path in sorted(root.glob("*.jsonl")):
        if path.name == "orchestrator.jsonl":
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("cpu_s") is not None:
                out.append(row)
    return out


def _tier_of(row):
    return row.get("tier") or "H0"


def cost_report(records=None, folder=None, planning=None, d3=False):
    """The measured mean cost per (tier, level) and stage against the plan (22.6): {"rows": [...], "alarms": [...],
    "projection": {...}, "work_items": [...]}. records: rows with tier, level, cpu_s, cpu_by_stage (a check log's or an
    audit's); default the check log."""
    planning = planning or _planning()
    rows = list(records) if records else _check_log_rows(folder)
    by_cell = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("cpu_s") is None:
            continue
        cell = by_cell.setdefault((_tier_of(row), row.get("level") or "full"), [])
        cell.append(row)
    out, alarms, items = [], [], []
    families = {}
    for row in rows:
        # the battery's own account (WP6's summary.budget), for the work item: which family the CPU went to
        budget = row.get("battery_budget") if isinstance(row, dict) else None
        for family, count in ((budget or {}).get("by_family") or {}).items():
            cell = families.setdefault(family, {"runs": 0, "cut": 0})
            cell["runs"] += count if isinstance(count, (int, float)) else (count or {}).get("runs", 0)
        for family, count in ((budget or {}).get("cut") or {}).items():
            families.setdefault(family, {"runs": 0, "cut": 0})["cut"] += count if isinstance(count, (int, float)) else 1
    limit = float(planning.get("calibration_alarm", 0.25))
    column = 1 if d3 else 0
    projected = plan_total = 0.0
    measured_full = {}
    for (tier, level), group in sorted(by_cell.items()):
        mean = sum(float(r["cpu_s"]) for r in group) / len(group)
        plan = ((planning.get("per_spec") or {}).get(tier) or {}).get(level)
        plan_s = float(plan[column]) if plan else None
        ratio = mean / plan_s if plan_s else None
        row = {"tier": tier, "level": level, "specs": len(group), "mean_cpu_s": round(mean, 1), "plan_cpu_s": plan_s,
               "ratio": None if ratio is None else round(ratio, 3)}
        stages = {}
        for r in group:
            for stage, value in (r.get("cpu_by_stage") or {}).items():
                stages[stage] = stages.get(stage, 0.0) + float(value)
        row["by_stage"] = {k: round(v / len(group), 1) for k, v in sorted(stages.items())}
        out.append(row)
        if level == "full":
            measured_full[tier] = mean
        if ratio is not None and ratio > 1.0 + limit:
            alarms.append("{} at {}: {:.0f} CPU-s against {:.0f} planned (+{:.0%})".format(tier, level, mean, plan_s,
                                                                                         ratio - 1.0))
            heavy = sorted(group, key=lambda r: -float(r["cpu_s"]))[:5]
            top = sorted(stages.items(), key=lambda kv: -kv[1])[:1]
            owner = STAGE_OWNERS.get(top[0][0], "WP5") if top else "WP5"
            items.append({"owner": owner, "tier": tier, "level": level, "stage": top[0][0] if top else None,
                          "specs": [r.get("catalog_id") for r in heavy]})
    for tier, share in CATALOG_MIX.items():
        plan = ((planning.get("per_spec") or {}).get(tier) or {}).get("full")
        if plan:
            plan_total += share * float(plan[column]) * CATALOG_ENTRIES / 3600.0
            projected += share * measured_full.get(tier, float(plan[column])) * CATALOG_ENTRIES / 3600.0
    projection = {"planned_cpu_hours": round(plan_total, 1), "measured_cpu_hours": round(projected, 1),
                  "within_contingency": projected <= plan_total * (1.0 + limit),
                  "tiers_measured": sorted(measured_full)}
    return {"rows": out, "alarms": alarms, "work_items": items, "projection": projection, "entries": len(rows),
            "battery_families": families,
            "note": "the planning figures, tier shares and caps of budget.json change only by the owner's decision"}


def render_cost(report):
    lines = ["# Cost calibration (VERIFY_HARDENING 22.6)", "", "| Tier | Level | Specs | Mean CPU-s | Plan | Ratio |",
             "|---|---|---:|---:|---:|---:|"]
    for row in report["rows"]:
        lines.append("| {tier} | {level} | {specs} | {mean_cpu_s} | {plan_cpu_s} | {ratio} |".format(**row))
    lines += ["", "Catalog projection at the full level: {measured_cpu_hours} CPU-hours measured, {planned_cpu_hours} "
                  "planned ({state}).".format(state="within the 25 % contingency" if report["projection"][
                      "within_contingency"] else "OUTSIDE the 25 % contingency: the owner decides",
                                              **report["projection"])]
    for alarm in report["alarms"]:
        lines.append("ALARM: " + alarm)
    for item in report["work_items"]:
        lines.append("work item for {owner}: {tier} at {level}, stage {stage}: {specs}".format(**item))
    return "\n".join(lines) + "\n"


def render_summary(summary):
    lines = ["# Hardening audit (VERIFY_HARDENING 23.1)", "", "{} specs: {}".format(summary["specs"], ", ".join(
        "{} {}".format(n, v) for v, n in sorted(summary["verdicts"].items()))), "",
        "| Domain | Specs | Verdicts | Counterfactual charges | Battery debt | Expect missing | Lint errors |",
        "|---|---:|---|---:|---|---:|---:|"]
    for name, block in sorted(summary["domains"].items()):
        lines.append("| {} | {} | {} | {} | {} | {} | {} |".format(
            name, block["specs"], ", ".join("{} {}".format(n, v) for v, n in sorted(block["verdicts"].items())),
            block["counterfactual_charges"], ", ".join("{} {}".format(k, n) for k, n in sorted(
                block["battery_debt"].items())) or "-", block["expect_missing"], block["lint_errors"]))
    lines += ["", "| Kind class / domain | Specs | HARDENED | Rate |", "|---|---:|---:|---:|"]
    for name, cell in sorted(summary["strata"].items()):
        lines.append("| {} | {} | {} | {} |".format(name, cell["specs"], cell["hardened"], cell["rate"]))
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------------------------------------------------

def load_specs(pattern=None, ids=None, root=ROOT):
    out = []
    for path in sorted(glob.glob(str(Path(root) / "tasks" / "authored" / "*.json"))):
        name = Path(path).name
        if pattern and not fnmatch.fnmatchcase(name, pattern):
            continue
        with open(path, encoding="utf-8") as stream:
            data = json.load(stream)
        for spec in (data.get("specs", []) if isinstance(data, dict) else data):
            if isinstance(spec, dict) and (not ids or spec.get("catalog_id") in ids):
                out.append((spec, name))
    return out


def _worker_init():
    os.environ.setdefault("HOUSE_SIM_WORKER", "1")
    from house_sim.kernel.plugins import load_domains
    load_domains()


def main(argv=None):
    p = argparse.ArgumentParser(description="The R0 hardening audit and the cost calibration")
    p.add_argument("--files", help="spec files matching this glob, e.g. 'cleaning__*'")
    p.add_argument("--ids", nargs="*", help="only these catalog ids")
    p.add_argument("--level", choices=("standard", "full"), default="standard")
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--out", help="output folder (default reports/harden_audit)")
    p.add_argument("--cost", action="store_true", help="the cost calibration from the check log (22.6)")
    p.add_argument("--check-log", help="check log folder (default catalog/review/check_log)")
    p.add_argument("--from", dest="source", help="--cost: an audit's specs.jsonl instead of the check log")
    p.add_argument("--census", action="store_true", help="also refresh tools/field_census.py's output (R0)")
    args = p.parse_args(argv)
    out = Path(args.out or ROOT / "reports" / "harden_audit")
    if args.cost:
        records = None
        if args.source:
            records = []
            with open(args.source, encoding="utf-8") as stream:
                for line in stream:
                    records.append(json.loads(line))
        report = cost_report(records=records, folder=args.check_log)
        out.mkdir(parents=True, exist_ok=True)
        with open(out / "cost.json", "w", encoding="utf-8", newline="\n") as stream:
            json.dump(report, stream, indent=1, sort_keys=True)
            stream.write("\n")
        print(render_cost(report))
        return 1 if report["alarms"] else 0
    specs = load_specs(args.files, set(args.ids) if args.ids else None)
    if not specs:
        print("no spec matches")
        return 2
    jobs = [{"spec": spec, "level": args.level, "file": name} for spec, name in specs]
    out.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    rows = []
    with open(out / "specs.jsonl", "w", encoding="utf-8", newline="\n") as stream:
        if args.workers > 1 and len(jobs) > 1:
            with ProcessPoolExecutor(max_workers=args.workers, initializer=_worker_init) as pool:
                results = pool.map(audit_spec, jobs)
                for row in results:
                    rows.append(row)
                    stream.write(json.dumps(row, sort_keys=True, default=str) + "\n")
                    stream.flush()
                    print("{:14} {} score {}".format(str(row.get("verdict")), row.get("catalog_id"), row.get("score")),
                          flush=True)
        else:
            for job in jobs:
                row = audit_spec(job)
                rows.append(row)
                stream.write(json.dumps(row, sort_keys=True, default=str) + "\n")
                stream.flush()
                print("{:14} {} score {}".format(str(row.get("verdict")), row.get("catalog_id"), row.get("score")),
                      flush=True)
    summary = summary_of(rows)
    summary.update(date=now_iso(), level=args.level, wall_s=round(time.perf_counter() - started, 1),
                   cost=cost_report(records=rows))
    with open(out / "summary.json", "w", encoding="utf-8", newline="\n") as stream:
        json.dump(summary, stream, indent=1, sort_keys=True, default=str)
        stream.write("\n")
    with open(out / "summary.md", "w", encoding="utf-8", newline="\n") as stream:
        stream.write(render_summary(summary))
        stream.write("\n" + render_cost(summary["cost"]))
    print(render_summary(summary))
    if args.census:
        import subprocess
        done = subprocess.run([sys.executable, str(ROOT / "tools" / "field_census.py"), "--out",
                               str(out / "field_census.json")], cwd=str(ROOT))
        if done.returncode:
            print("field census failed (exit status {})".format(done.returncode))
            return done.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
