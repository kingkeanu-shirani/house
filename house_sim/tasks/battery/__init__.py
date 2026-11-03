"""The battery: generated cheaters that must fail, legitimate variants that must pass, randomisation audits and the
private families (docs/design/VERIFY_HARDENING.md 9.5-9.7, 10, 11, 12.2, 22; work package WP6).

The verifier (house_sim/tasks/verify.py, WP5) calls, for a schema-4 spec:

    run(spec, builds, traces, seeds, mode, level, explain=None, families=None)
        S5's public gaming battery B2-B14 (B1 is the verifier's idle rollout). builds: {"mode", "references",
        "extras": the allowances (reference_resources, reference_open_s)[, "budget": the verifier's budget record,
        "expected": E(entry)]}; traces: {seed: the verifier's reference data (mode_trials "references")}; seeds: V.
        -> {"ran": True, "runs": [run record], "summary": {...}, "guess": {"rate", "roles"}, "b5a_discordant_failure_
        share", "protect_live": [refs], "harness_errors": [...], "lint": [...], "tier", "cpu_s"}
    mustpass(spec, builds, traces, seeds, mode, level, prechecked=None)
        S5's must-pass battery M1-M6 -> {"ran": True, "runs": [...], "summary": {"applicable", "realised", "passed",
        "failed_unexempt"}, ...}
    audit(spec, n=200)
        S6's 200-build audits (9.7) -> audits.audit's record (cached per spec hash for the process: run() at the full
        level computes it first for B5c's and B7's rate records)
    private(spec, references, seeds, mode, level, salt=None, key=None, pool=None)
        S7p's private families P3, P4, P5, P7, P8, PM (gate only) -> {"results": [{"family", "outcome",
        "label_class"}], "runs": [...], "details": [...]}

Run records follow 10.6: {"id", "family", "sub", "seed", "mode", "outcome", "realised", "labels", "accepted",
"goal_fraction", "unmet", "dropped_steps", "fallbacks", "plan_digest", "cpu_s"} plus the reason of a skip, the hint
of an unmet outcome (10.6, 11.4), the refusals of an unrealised run, the decided-early stop, fork and fast-forward
flags and the audits' re-runs. Nothing is silent: every skipped, reduced, forked, fast-forwarded or early-stopped run
is in the record with its reason (10.5).
"""
import copy
import json
import os
import time

from .. import verify
from ..spec import expand
from . import audits as audits_mod
from . import budget as budget_mod
from . import generate as gen
from .mustpass import GENERATORS as MUSTPASS_GENERATORS
from .mustpass import HINTS as MUSTPASS_HINTS
from .mustpass import summary as mustpass_summary
from . import policies as pol_mod
from . import trace as trace_mod

FORK_AUDIT_SHARE = 0.05
FF_AUDIT_SHARE = 0.10
HINTS = {
    "B2": "success is satisfied by talking: add a state-kind success predicate or score content",
    "B3": "the destination is not scored: declare the instruction role, narrow the `to` scope, check the mess kinds",
    "B4": "the instance is not decisive: add a predicate on the decoy, declare evidence, or use more candidates",
    "B10": "the instance is not decisive: add a predicate on the decoy, declare evidence, or use more candidates",
    "B11": "the instance is not decisive: add a predicate on the decoy, declare evidence, or use more candidates",
    "B5": "the answer does not vary or is not scored: randomise `{answer}` and compare report content",
    "B6": "success reads a latch or an event: register derived field `{field}` or score the final placement",
    "B7": "the hazard resolves on a timer: randomise `{event}` (with `never_p`) and use `timing`",
    "B8": "the needless action is free: penalise it in branch `{branch}` (failure predicate or per-variant budget)",
    "B9": "asking is free or never needed: set fact necessity per branch",
    "B12": "speech content is not scored: use `said` claims, typed reports or relay `key_claims`",
    "B13": "the task can be done without looking: declare `answers.evidence` or make the hidden state matter",
    "B14": "the guard does not bite: check its protections, envelope and the probe's labels",
    "unrealised": "battery debt: the attack could not be carried out here (reported to WP6's owner with the refusals)",
}
_AUDITS = {}
_AUDIT_RUN = []


def _audit_run():
    """tools/perception_audit.py's audit_run, loaded from the repository beside the package (None when the tools
    folder is not there, as in an installed package)."""
    if not _AUDIT_RUN:
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))), "tools", "perception_audit.py")
        fn = None
        if os.path.exists(path):
            try:
                spec = importlib.util.spec_from_file_location("perception_audit_for_battery", path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                fn = getattr(module, "audit_run", None)
            except Exception:               # noqa: BLE001  (an auditor that cannot load audits nothing)
                fn = None
        _AUDIT_RUN.append(fn)
    return _AUDIT_RUN[0]


def _hint(rec, policy=None):
    outcome = rec.get("outcome")
    if outcome == "unrealised":
        return HINTS["unrealised"]
    if outcome in ("passes", "fails_other", "void", "skipped:cap"):
        text = HINTS.get(rec.get("family"), "")
        meta = (policy.meta if policy is not None else {}) or {}
        return text.format(answer=meta.get("answer", "the answer"), field=meta.get("field", "the goal field"),
                           event=meta.get("event", "the event time"), branch=meta.get("branch", "the branch"))
    return None


def _analysis(cid):
    try:
        return verify._analysis_record(cid) or {}
    except Exception:                       # noqa: BLE001  (no record: no analysis-driven families)
        return {}


def _expected(builds, cid, analysis):
    if (builds or {}).get("expected") is not None:
        return list(builds["expected"])
    try:
        from .. import strength
        entry = verify._entry(cid) or {}
        return strength.expected_families(analysis, entry.get("text"), entry.get("kind"))
    except Exception:                       # noqa: BLE001
        return []


def _branch_diffs(ctxs):
    traces = []
    for ctx in ctxs:
        forms = [verify._canon_command(e, ctx.bindings) for e in ctx.trace or () if e.get("ok")]
        commands = [{"action": e["action"], "args": dict(e.get("args") or {}, target=e.get("target"),
                                                         destination=e.get("destination"))}
                    for e in ctx.trace or () if e.get("ok")]
        traces.append((ctx.seed, ctx.branch, forms, commands))
    return verify.branch_diff_sets(traces)


def allowances(traces):
    """The allowances the verifier judges wrong plans with (verify.mode_trials "extras"), recomputed from the
    references when a caller passes none: the references' largest resource use and open time."""
    resources, opened = {}, {}
    for ref in (traces or {}).values():
        rec = (ref or {}).get("reference") or {}
        for name, value in (rec.get("resources") or {}).items():
            resources[name] = max(resources.get(name, 0.0), float(value))
        for eid, value in (rec.get("open_s") or {}).items():
            opened[eid] = max(opened.get(eid, 0.0), float(value))
    return {"reference_resources": resources, "reference_open_s": opened}


def prepare(spec, builds, traces, seeds, mode, snapshots=True, root=None):
    """The seeds' material (trace.Seed per seed of V with a reference) and the harness errors found preparing it."""
    extras = (builds or {}).get("extras")
    if extras is None:
        extras = allowances(traces)
    ctxs, errors = [], []
    for seed in seeds or ():
        ref = (traces or {}).get(seed)
        if ref is None:
            continue
        ctx = trace_mod.build_seed(spec, seed, ref, mode, extras, snapshots=snapshots, root=root)
        if ctx.task is None:
            errors.extend(ctx.errors)
            ctx.cleanup()
            continue
        gen.prepare(ctx)
        errors.extend(ctx.errors)
        ctxs.append(ctx)
    return ctxs, errors


class Session(object):
    """One stage's execution state: the budget, the fork and fast-forward switches and audits, the records."""

    def __init__(self, context, budget, mode, diffs):
        self.c, self.budget, self.mode, self.diffs = context, budget, mode, diffs
        self.fork = True
        self.ff = False
        self.records = []
        self.forked = []
        self.audited = 0
        self.errors = []
        self.notes = []
        self.ff_runs = []
        self.perception_audited = set()

    def execute(self, policy, ctx, count_as=None):
        family = count_as or policy.family
        why = self.budget.allow(family)
        if why:
            self.budget.note_cut(family, why)
            rec = {"id": policy.id, "family": policy.family, "sub": policy.sub, "seed": policy.seed,
                   "mode": self.mode, "outcome": "skipped:cap", "why": why, "realised": False, "labels": [],
                   "accepted": [], "plan_digest": policy.digest(), "cpu_s": 0.0}
            rec["hint"] = _hint(rec, policy)
            return rec
        rec, env = pol_mod.execute(policy, ctx, self.mode, ctx.plan, self.diffs.get(ctx.seed), fork=self.fork,
                                   ff=self.ff)
        self.budget.spend(family, rec.get("cpu_s"), rec.get("sim_s"))
        if env is not None and policy.family not in self.perception_audited and \
                getattr(env, "realism", None) is not None:
            self.perception_audited.add(policy.family)
            self._perception_audit(policy, env, rec)
        if rec.get("forked"):
            self.forked.append((policy, ctx, rec))
            if self.audited == 0 or pol_mod.stable_share(policy.id, FORK_AUDIT_SHARE):
                self._fork_audit(policy, ctx, rec)
        if self.ff and rec.get("fast_forward"):
            self.ff_runs.append(policy.id)
            if len(self.ff_runs) == 1 or pol_mod.stable_share(policy.id, FF_AUDIT_SHARE):
                self._ff_audit(policy, ctx, rec)
        hint = _hint(rec, policy)
        if hint:
            rec["hint"] = hint
        if rec.get("outcome") == "error":
            self.errors.append("{}: {}".format(policy.id, rec.get("error")))
        return rec

    def _perception_audit(self, policy, env, rec):
        """WP7's post-run leak audit (tools/perception_audit.py audit_run) over the first generated run of each
        family in a realistic mode: a leak means the evaluation mode told the policy something it could not
        perceive, so the run's verdict is not evidence (a harness error, G7)."""
        audit_run = _audit_run()
        if audit_run is None:
            self.notes.append("perception audit: tools/perception_audit.py is not available here")
            return
        try:
            leaks = audit_run(env)
        except Exception as exc:            # noqa: BLE001  (the auditor itself failing is a harness error)
            self.errors.append("perception audit of {} raised {}: {}".format(policy.id, type(exc).__name__,
                                                                            str(exc)[:160]))
            return
        rec["perception_audit"] = len(leaks)
        for kind, owner, detail in leaks[:3]:
            self.errors.append("perception audit of {}: {} leak ({}): {}".format(policy.id, kind, owner,
                                                                                str(detail)[:160]))

    def _ff_audit(self, policy, ctx, rec):
        """10.5: one fast-forwarded run per spec and 10 % of them re-run with fine ticks; a different outcome class
        disables fast-forward for the spec (its later runs use fine ticks; this one is replaced by the fine run)."""
        fine, _ = pol_mod.execute(policy, ctx, self.mode, ctx.plan, self.diffs.get(ctx.seed), fork=self.fork,
                                  ff=False)
        if fine.get("outcome") != rec.get("outcome"):
            self.ff = False
            self.notes.append("fast-forward audit: {} {} with fast-forwarded waits, {} with fine ticks: "
                              "fast-forward disabled for the spec".format(policy.id, rec.get("outcome"),
                                                                          fine.get("outcome")))
            rec.clear()
            rec.update(fine)
            rec["audit"] = {"fast_forward": "differs: replaced by the fine run"}
        else:
            rec["audit"] = dict(rec.get("audit") or {}, fast_forward="same")

    def _fork_audit(self, policy, ctx, rec):
        """10.5: a forked run re-run from the build must give the same end-state digest and labels; a mismatch
        disables forking for the spec, re-runs the forked runs from the build and is a harness error (G7)."""
        self.audited += 1
        again, _ = pol_mod.execute(policy, ctx, self.mode, ctx.plan, self.diffs.get(ctx.seed), fork=False,
                                   ff=self.ff)
        same = again.get("state_digest") == rec.get("state_digest") and again.get("labels") == rec.get("labels") \
            and again.get("outcome") == rec.get("outcome")
        rec["audit"] = {"fork": "same" if same else "differs"}
        if not same:
            self.errors.append("fork audit: {} forked {} / from the build {} (digest {} != {}); forking disabled for "
                               "the spec (WP2 / WP5 save-restore determinism)".format(
                                   policy.id, rec.get("outcome"), again.get("outcome"),
                                   str(rec.get("state_digest"))[:12], str(again.get("state_digest"))[:12]))
            self.fork = False
            for p, c2, r in self.forked:
                if r is rec:
                    continue
                fresh, _ = pol_mod.execute(p, c2, self.mode, c2.plan, self.diffs.get(c2.seed), fork=False,
                                           ff=self.ff)
                r.clear()
                r.update(fresh)
                r["audit"] = {"fork": "re-run from the build after a fork audit mismatch"}
            rec.clear()
            rec.update(again)
            rec["audit"] = {"fork": "differs: re-run from the build"}


def _subkey(policy):
    """A sub-policy's key across seeds for the breadth-first sweep (family, variant, rank on its seed)."""
    return (policy.family, policy.sub, policy.meta.get("_rank", 0))


def _rank(policies):
    seen = {}
    for p in policies:
        key = (p.family, p.sub, p.seed)
        p.meta["_rank"] = seen.get(key, 0)
        seen[key] = seen.get(key, 0) + 1


def sweep(session, policies, ctx_of, level):
    """Run policies breadth-first (10.5): at the standard level every sub-policy once on the first seed it has, a
    sub-policy that passes is reported and not repeated on later seeds; at the full level everything."""
    _rank(policies)
    out = []
    if level == "full":
        for p in policies:
            out.append(session.execute(p, ctx_of[p.seed]))
        return out
    first, rest = [], []
    seen = set()
    for p in policies:
        key = _subkey(p)
        (first if key not in seen else rest).append(p)
        seen.add(key)
    passed = set()
    for p in first:
        rec = session.execute(p, ctx_of[p.seed])
        out.append(rec)
        if rec.get("outcome") == "passes":
            passed.add(_subkey(p))
    for p in rest:
        if _subkey(p) in passed:
            out.append({"id": p.id, "family": p.family, "sub": p.sub, "seed": p.seed, "mode": session.mode,
                        "outcome": "skipped:tier", "realised": False, "labels": [], "accepted": [],
                        "why": "breadth-first sweep: this sub-policy already passed on another seed (reported "
                               "there; the full level repeats it)", "plan_digest": p.digest(), "cpu_s": 0.0})
            continue
        out.append(session.execute(p, ctx_of[p.seed]))
    return out


def _ff_check(session, ctxs):
    """10.5's equivalence check before fast-forwarded waits: the reference replayed on the first seed with its waits
    of at least 600 s fast-forwarded gives the same outcome and labels as fine ticks."""
    ctx = ctxs[0]
    long_waits = [e for e in ctx.trace if e.get("action") == "wait" and not trace_mod.conditioned(e) and
                  float((e.get("args") or {}).get("seconds") or 0) >= pol_mod.FF_MIN_S]
    if not long_waits:
        return False, "no wait of 600 s or more in the reference"
    policy = pol_mod.Policy("FF.check@{}#0".format(ctx.seed), "FF", "check", ctx.seed, gen._replays(ctx),
                            effect={"all": []}, mustpass=True)
    policy.fork_ok = False
    fine, _ = pol_mod.execute(policy, ctx, session.mode, ctx.plan, session.diffs.get(ctx.seed), fork=False, ff=False)
    fast, _ = pol_mod.execute(policy, ctx, session.mode, ctx.plan, session.diffs.get(ctx.seed), fork=False, ff=True)
    same = fine.get("outcome") == fast.get("outcome") and fine.get("labels") == fast.get("labels")
    return same, "equivalence check on seed {}: {}".format(ctx.seed, "same" if same else "differs (fine ticks kept)")


def _error_record(family, exc, mode):
    import traceback
    return {"id": "{}.generation@all#error".format(family), "family": family, "sub": "generation", "seed": None,
            "mode": mode, "outcome": "error", "realised": False, "labels": [], "accepted": [],
            "error": "{}: {}".format(type(exc).__name__, exc)[:300],
            "trace": traceback.format_exc().splitlines()[-5:]}


def _generate(c, family, ctxs, ctx_of, session, records, out):
    """(policies, skips) of one family; B12 first runs its load-bearing pre-checks (recorded, not counted), B4 brings
    B11's blind picks."""
    if family == "B12":
        pre = gen.precheck_policies(c, ctxs[0])
        bearing = {}
        for p in pre:
            rec = session.execute(p, ctx_of[p.seed], count_as="B12")
            rec["outcome_run"] = rec.get("outcome")
            rec["outcome"] = "skipped:tier"
            rec["why"] = "B12 pre-check: the reference without this COMM command {} (load-bearing when it " \
                         "fails)".format("fails" if not rec.get("success") else "passes")
            rec.pop("hint", None)
            records.append(rec)
            if rec.get("success") is False:
                bearing.setdefault(p.seed, []).append(p.meta["index"])
        out["load_bearing"] = {str(k): v for k, v in sorted(bearing.items())}
        prechecked = None
        if pre:
            loaded = bearing.get(ctxs[0].seed, [])
            prechecked = {x.seed: None for x in ctxs}
            prechecked[ctxs[0].seed] = loaded
            # the other seeds' COMM commands are load-bearing when their first seed's twin is (same rank)
            ranks = [i for i, e in enumerate(gen._comm_entries(ctxs[0])) if e["i"] in loaded]
            for x in ctxs[1:]:
                comm = gen._comm_entries(x)
                prechecked[x.seed] = [comm[r]["i"] for r in ranks if r < len(comm)]
        return gen.B12(c, prechecked)
    policies, skips = gen.GENERATORS[family](c)
    if family == "B4":
        more, more_skips = gen.B11(c)
        policies = policies + more
        skips = skips + more_skips
    return policies, skips


def guess_rates(records):
    """B11 (10.3): p(r, s) = passing blind picks / k per decision role and seed, p(r) the mean over seeds, the
    episode rate the product over roles."""
    by = {}
    for rec in records:
        if rec.get("family") != "B11" or rec.get("outcome") in ("skipped:na", "skipped:cap", "skipped:tier"):
            continue
        key = (rec.get("decision") or str(rec.get("id")).split("#")[-1].split("c")[0], rec.get("seed"))
        row = by.setdefault(key, [0, 0])
        row[0] += 1
        row[1] += 1 if rec.get("success") else 0
    roles = {}
    for (role, seed), (k, passes) in by.items():
        roles.setdefault(role, []).append(passes / float(k) if k else 0.0)
    rates = {role: round(sum(v) / len(v), 4) for role, v in roles.items() if v}
    episode = 1.0
    for v in rates.values():
        episode *= v
    return {"roles": rates, "rate": max(rates.values()) if rates else None,
            "episode": round(episode, 4) if rates else None}


def _b11_records(records, policies):
    """One requirement record per decision role from its blind runs: met when p(r) <= 0.2 and the episode rate <=
    0.1 (10.3 B11), the blind runs kept beside it as diagnosis (not counted: their requirement is the rate)."""
    meta = {p.id: p.meta for p in policies}
    for rec in records:
        if rec.get("family") == "B11":
            m = meta.get(rec["id"]) or {}
            rec["decision"] = m.get("decision")
            rec["candidate"] = m.get("candidate")
            rec["true"] = m.get("true")
    rates = guess_rates(records)
    out = []
    for role, rate in sorted((rates.get("roles") or {}).items()):
        ok = rate <= 0.2 + 1e-9 and (rates.get("episode") or 0.0) <= 0.1 + 1e-9
        out.append({"id": "B11.rate@all#{}".format(role), "family": "B11", "sub": "rate", "seed": None,
                    "outcome": "fails" if ok else "passes", "realised": True, "labels": [], "accepted": [],
                    "rate": rate, "episode": rates.get("episode"),
                    "why": "guess rate {:.2f} per decision (bound 0.2), {:.2f} per episode (bound 0.1)".format(
                        rate, rates.get("episode") or 0.0),
                    "hint": None if ok else HINTS["B11"]})
    for rec in records:
        if rec.get("family") == "B11" and rec.get("sub") == "blind":
            rec["counted_by"] = "B11.rate"
            if rec.get("outcome") not in ("skipped:na",):
                rec["outcome_run"] = rec["outcome"]
                rec["outcome"] = "skipped:tier"
                rec["why"] = "a blind pick of B11: its requirement is the guess rate (B11.rate)"
    return out, rates


def _b5a_share(records):
    discordant = [r for r in records if r.get("family") == "B5" and r.get("sub") == "a" and r.get("discordant")]
    counted = [r for r in discordant if r.get("outcome") not in ("skipped:na", "skipped:tier")]
    if not counted:
        return None
    return round(sum(1 for r in counted if r.get("outcome") in ("fails", "blocked")) / float(len(counted)), 4)


def _guarded(stage):
    """A stage that raises returns an error record and a harness error (G7, 10.2 "error") instead of stopping the
    verifier."""
    def wrap(fn):
        def inner(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:        # noqa: BLE001
                import traceback
                why = "battery {} raised {}: {}".format(stage, type(exc).__name__, str(exc)[:200])
                return {"ran": True, "runs": [{"id": "{}@all#error".format(stage), "family": stage, "sub": "stage",
                                               "seed": None, "outcome": "error", "realised": False, "labels": [],
                                               "accepted": [], "error": why,
                                               "trace": traceback.format_exc().splitlines()[-5:]}],
                        "summary": {"applicable": 1, "realised": 0, "passed": 0, "failed_unexempt": 0},
                        "results": [], "harness_errors": [why]}
        inner.__name__, inner.__doc__ = fn.__name__, fn.__doc__
        return inner
    return wrap


@_guarded("S5")
def run(spec, builds, traces, seeds, mode, level, explain=None, families=None, root=None):
    """S5's public gaming battery (module docstring)."""
    t0 = time.process_time()
    started = time.monotonic()
    out = {"ran": True, "runs": [], "summary": {}, "harness_errors": [], "lint": [], "mode": mode, "level": level}
    ctxs, errors = prepare(spec, builds, traces, seeds, mode)
    out["harness_errors"].extend(errors)
    try:
        if not ctxs:
            out["ran"] = False
            out["why"] = "no seed of V has a reference to attack"
            return out
        cid = expand(spec).get("catalog_id")
        analysis = _analysis(cid)
        builds = dict(builds or {})
        builds.setdefault("expected", _expected(builds, cid, analysis))
        tier = budget_mod.tier_of(builds, ctxs)
        c = gen.Context(spec, builds, ctxs, mode, level, tier, analysis)
        out["tier"] = tier["id"]
        # the core trials' CPU counts towards the tier's cap (22.6): the verifier passes it as builds["cpu_s"]
        budget = budget_mod.Budget(level, tier, "battery", budget_s=max(x.time_budget() for x in ctxs),
                                   cpu_spent=float(builds.get("cpu_s") or 0.0))
        diffs = _branch_diffs(ctxs)
        session = Session(c, budget, mode, diffs)
        if tier.get("waits") == "fast_forward":
            session.ff, why = _ff_check(session, ctxs)
            out["fast_forward"] = {"enabled": session.ff, "why": why}
        ctx_of = {x.seed: x for x in ctxs}
        records, policies_all = [], []
        wanted = [f for f in budget_mod.ORDER if families is None or f in families]
        for family in wanted:
            if family == "B14":
                continue
            try:
                policies, skips = _generate(c, family, ctxs, ctx_of, session, records, out)
            except Exception as exc:        # noqa: BLE001  (a generator bug: an error record, G7; 10.2 "error")
                records.append(_error_record(family, exc, mode))
                out["harness_errors"].append("battery generation of {} raised {}: {}".format(
                    family, type(exc).__name__, str(exc)[:200]))
                continue
            policies_all.extend(policies)
            records.extend(skips)
            records.extend(sweep(session, policies, ctx_of, level))
        if "B14" in wanted:
            refs = gen.mapped_refs(c.full)
            live = gen.live_refs(refs, records, {x.seed: x.bindings for x in ctxs})
            try:
                policies, skips = gen.B14(c, live)
            except Exception as exc:        # noqa: BLE001
                records.append(_error_record("B14", exc, mode))
                out["harness_errors"].append("battery generation of B14 raised {}: {}".format(
                    type(exc).__name__, str(exc)[:200]))
                policies, skips = [], []
            policies_all.extend(policies)
            records.extend(skips)
            records.extend(sweep(session, policies, ctx_of, "full"))
            out["protect_live"] = sorted(gen.live_refs(refs, records, {x.seed: x.bindings for x in ctxs}))
        meta = {p.id: p.meta for p in policies_all}
        for rec in records:
            m = meta.get(rec.get("id")) or {}
            if m.get("discordant") is not None:
                rec["discordant"] = m["discordant"]
        b11, rates = _b11_records(records, policies_all)
        records.extend(b11)
        out["guess"] = rates
        out["b5a_discordant_failure_share"] = _b5a_share(records)
        if level == "full":
            audit_rec = audit(spec, builds=builds)
            out["harness_errors"].extend(audit_rec.get("harness_errors") or [])
            out["rates"] = audits_mod.rate_records(audit_rec)
            records.extend(out["rates"])
            out["lint"].extend(audit_rec.get("lint") or [])
        out["runs"] = [pol_mod.compact(r) if "plan_digest" in r and r.get("outcome") not in (
            "skipped:na",) else r for r in records]
        out["harness_errors"].extend(session.errors)
        out["notes"] = list(session.notes)
        out["summary"] = summarise(out["runs"])
        out["summary"]["budget"] = budget.summary()
        out["summary"]["forked"] = len(session.forked)
        out["summary"]["fork_audits"] = session.audited
        out["summary"]["forking"] = session.fork
        out["applicable_families"] = sorted({r["family"] for r in out["runs"]
                                             if r.get("outcome") != "skipped:na" and r.get("family")})
        if explain:
            write_explain(explain, policies_all)
    finally:
        for x in ctxs:
            x.cleanup()
    out["cpu_s"] = round(time.process_time() - t0, 3)
    out["wall_s"] = round(time.monotonic() - started, 3)
    return out


def summarise(runs):
    """Per-family counts of outcomes and the met share of the counted instances."""
    families = {}
    for rec in runs:
        fam = rec.get("family") or "?"
        row = families.setdefault(fam, {"runs": 0, "counted": 0, "met": 0, "outcomes": {}})
        row["runs"] += 1
        outcome = rec.get("outcome")
        row["outcomes"][outcome] = row["outcomes"].get(outcome, 0) + 1
        if outcome in ("skipped:na", "skipped:tier"):
            continue
        row["counted"] += 1
        if outcome in ("fails", "blocked", "passes") and rec.get("mustpass_family"):
            row["met"] += 1 if outcome == "passes" else 0
        elif outcome in ("fails", "blocked"):
            row["met"] += 1
    for row in families.values():
        row["met_share"] = round(row["met"] / float(row["counted"]), 4) if row["counted"] else None
    return {"families": dict(sorted(families.items())),
            "passes": sorted(r["id"] for r in runs if r.get("outcome") == "passes"),
            "unmet": sorted(r["id"] for r in runs if r.get("outcome") in ("passes", "fails_other", "unrealised",
                                                                           "void", "skipped:cap", "error"))}


def write_explain(folder, policies):
    """--explain DIR (10.6): every generated policy as a plan JSON file in the wrong-plan format."""
    os.makedirs(folder, exist_ok=True)
    for p in policies:
        name = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in p.id) + ".json"
        with open(os.path.join(folder, name), "w", newline="\n", encoding="utf-8") as fh:
            json.dump(pol_mod.dumps(p), fh, indent=1, sort_keys=True, default=str)
            fh.write("\n")


@_guarded("S5-mustpass")
def mustpass(spec, builds, traces, seeds, mode, level, prechecked=None, root=None):
    """S5's must-pass battery (module docstring)."""
    t0 = time.process_time()
    out = {"ran": True, "runs": [], "summary": {}, "harness_errors": [], "mode": mode, "level": level}
    ctxs, errors = prepare(spec, builds, traces, seeds, mode)
    out["harness_errors"].extend(errors)
    try:
        if not ctxs:
            out["ran"] = False
            out["why"] = "no seed of V has a reference"
            return out
        tier = budget_mod.tier_of(builds, ctxs)
        c = gen.Context(spec, builds, ctxs, mode, level, tier, _analysis(expand(spec).get("catalog_id")))
        out["tier"] = tier["id"]
        budget = budget_mod.Budget(level, tier, "must-pass", budget_s=max(x.time_budget() for x in ctxs),
                                   caps=budget_mod.MUSTPASS_CAPS, spec_runs=budget_mod.MUSTPASS_SPEC)
        session = Session(c, budget, mode, _branch_diffs(ctxs))
        if tier.get("waits") == "fast_forward":
            session.ff, why = _ff_check(session, ctxs)
            out["fast_forward"] = {"enabled": session.ff, "why": why}
        ctx_of = {x.seed: x for x in ctxs}
        records = []
        for family, generator in MUSTPASS_GENERATORS:
            policies, skips = generator(c, prechecked) if family == "M5" else generator(c)
            records.extend(skips)
            for p in policies:
                rec = session.execute(p, ctx_of[p.seed])
                if rec.get("outcome") == "fails":
                    rec["hint"] = MUSTPASS_HINTS.get(family)
                    rec["rejected_by"] = (rec.get("labels") or []) + ["goal:" + u for u in rec.get("unmet") or ()]
                elif rec.get("outcome") == "unrealised":
                    rec["hint"] = HINTS["unrealised"]
                records.append(rec)
        out["runs"] = [pol_mod.compact(r) if "plan_digest" in r else r for r in records]
        for r in out["runs"]:
            r["mustpass_family"] = True
        out["harness_errors"].extend(session.errors)
        out["summary"] = mustpass_summary(out["runs"], spec)
        out["summary"]["budget"] = budget.summary()
    finally:
        for x in ctxs:
            x.cleanup()
    out["cpu_s"] = round(time.process_time() - t0, 3)
    return out


def audit(spec, n=200, builds=None):
    """S6's audits over n extra houses (9.7; audits.py), cached per spec hash in this process."""
    key = (verify.spec_hash(spec), int(n))
    if key not in _AUDITS:
        try:
            _AUDITS[key] = audits_mod.audit(spec, n=n, informative=(spec or {}).get("informative"))
        except Exception as exc:            # noqa: BLE001  (an audit that cannot run is a harness error, never a pass)
            why = "the 9.7 audit raised {}: {}".format(type(exc).__name__, str(exc)[:200])
            _AUDITS[key] = {"ran": False, "ok": False, "why": why, "harness_errors": [why],
                            "lint": [{"id": "L-AUDIT", "severity": "error", "message": why}]}
    return copy.deepcopy(_AUDITS[key])


@_guarded("S7p")
def private(spec, references, seeds, mode, level, salt=None, key=None, pool=None):
    """S7p's private families (hidden.py); nothing runs without the salt."""
    from . import hidden
    return hidden.run(spec, references, seeds, mode, level, salt=salt, key=key, pool=pool)


def heldout_rates(spec, builds, traces, seeds, mode, level):
    """S7's rate families on held-out seeds (16.2): B4 blind (with B11), B5a / B5b / B5c, B7 and B8 cross, inside
    the held-out cap of the tier."""
    return run(spec, builds, traces, seeds, mode, level, families=("B4", "B5", "B7", "B8"))
