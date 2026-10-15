"""Per-entry verification: initial/idle/reference/wrong checks on every seed.

An entry passes only if, on every seed:
  1. the initial state does not satisfy the task,
  2. waiting out the whole time budget does not satisfy it,
  3. the reference plan (strict: no refused step) satisfies it within budget,
  4. every plausible wrong plan fails,
  5. every wrong plan actually ran (no spec/plan construction error).

Setup is linted for physically impossible staged scenes (tasks/setup.py physics_problems, kernel v3.0): a spec with
"schema": 3 or more fails to build; for older specs the problems are recorded as the trial's "physics_warnings".

camera (kernel v3.0): None runs each spec with its own camera ("camera", default room); "room" | "geometric" forces
one for every spec (CLI --camera). The trial records the mode it ran in ("camera").

Kernel v3.1 (TIME-EVAL; TIER1_SWEEP 6.6, 6.7, 8.1, 9): gen forces the house generation (CLI --gen). Multi-phase
tasks: the initial state must not satisfy phase 1 either, the idle check waits out each phase's budget and declares
it done (it must fail), plans contain phase_done steps. Schema-3 idle checks stop waiting once the evaluation says
"decided": "fail" (a latched violation, a missed deadline, the budget, a global flag: success is impossible). Lints:
a never predicate that holds in the initial state without an "after" window is an error ("temporal_lint");
milestones that already hold in the initial state are recorded as "temporal_warnings". Schema-2 reference plans
that wait on hidden state (wait_until) are counted in "privileged_waits" (migration debt; tools/verify.py lists
them). A request gesture without the people domain is recorded as "gesture_dropped".

Questions and reports (TIER1_SWEEP 4.11, 5.5; PEOPLE-IO's kernel/dialogue.py and reports.py, wired by the stage-C
merge): a schema-3 reference run must pass the dialogue lints ("dialogue_lint": facts well formed and reachable,
required questions asked and answered, a wrong plan beside them, no legacy-topic ask), else the reference fails; with
realistic perception every typed report a report / report_set success predicate scores must rest on perceived
evidence, else its gap joins "perceivability_gaps" ({"report": ...}). Both checks read the phases' success too.
"""
import copy
import json
import math
import os
import traceback
from concurrent.futures import ProcessPoolExecutor

from ..kernel import camera as camera_mod
from ..kernel.errors import SpecError
from ..kernel.plugins import load_domains
from ..kernel.predicates import digest
from ..kernel.predicates import check
from .runner import PlanError, build, make_env, run_plan
from .spec import expand

SEEDS = (0, 1, 2, 41, 97)


def _early_exit(env, task):
    """Schema-3 idle checks stop once success is impossible (TIER1_SWEEP 6.7)."""
    schema = task.get("schema", 2)
    return isinstance(schema, int) and schema >= 3 and env.evaluate().get("decided") == "fail"


MAX_WAIT_S = 604800         # the wait action's own limit (a week)


def _wait_out(env, task, seconds, max_steps=30):
    """Wait `seconds` in at most max_steps waits: hour-long ones as before, longer ones when the budget is over
    max_steps hours (a multi-day budget is waited out to its end, not cut at 30 h)."""
    remaining, steps = float(seconds), 0
    size = min(MAX_WAIT_S, max(3600, int(math.ceil(float(seconds) / max_steps))))
    while remaining > 0 and steps < max_steps:
        chunk = int(min(size, remaining))
        if chunk < 1:
            break
        env.step("wait", seconds=chunk)
        remaining -= chunk
        steps += 1
        if _early_exit(env, task):
            break


def idle(env, task):
    """Wait out the whole time budget (each phase's budget, then declare it done, for multi-phase tasks): the task
    must not be satisfied."""
    phases = task.get("phases") or []
    if phases:
        default = float(task.get("time_budget_s", 7200)) / max(1, len(phases))
        for _ in range(len(phases) + 1):
            active = env._phases.active()
            if active is None or _early_exit(env, task):
                break
            _wait_out(env, task, float((active.get("budget") or {}).get("time_s", default)))
            current = env._phases.active()
            if current is not None and current["id"] == active["id"]:
                env.phase_done()        # its budget did not end it: declare it done
        return env.evaluate()
    _wait_out(env, task, float(task.get("time_budget_s", 7200)))
    return env.evaluate()


def temporal_lint(env, task):
    """(errors, warnings) of the temporal keys on the initial state: a never predicate without an "after" window that
    already holds is an error (it is violated before the robot acts); a milestone that already holds is a warning (it
    is reached at the first sample: deadlines and orders on it are met before anything happens)."""
    state = env.world.s
    errors, warnings = [], []
    for scope, prefix in [(task, "")] + [(p, p.get("id", "?") + "/") for p in task.get("phases") or []]:
        for i, raw in enumerate(scope.get("never") or []):
            pred, after = (raw["pred"], raw.get("after")) if isinstance(raw, dict) and "pred" in raw else (raw, None)
            if after is None and not scope.get("id") and check(state, pred):
                label = raw.get("label", i) if isinstance(raw, dict) else i
                errors.append("never:{}{} holds in the initial state".format(prefix, label))
        for name, raw in sorted((scope.get("milestones") or {}).items()):
            pred = raw["pred"] if isinstance(raw, dict) and "pred" in raw else raw
            if check(state, pred):
                warnings.append("milestone {} holds in the initial state".format(name))
    return errors, warnings


def run_trial(spec, seed, perception=None, camera=None, gen=None):
    """perception: None -> each spec's own mode ("perception" field, default oracle); "realistic" forces it.
    camera: None -> each spec's own camera ("camera" field, default room); "room" | "geometric" forces it.
    gen: None -> the spec's house generation; 1 | 2 forces it (CLI --gen)."""
    trial = {"seed": seed, "passed": False}
    try:
        config, task, oracle = build(spec, seed, camera=camera, perception=perception, gen=gen)
        mode = perception or task.get("perception") or "oracle"
        trial["perception"] = mode
        trial["camera"] = camera_mod.resolve_mode(camera, task, mode)
        trial["variant"] = oracle["variant"]
        # the physics lint after setup (tasks/setup.py physics_problems): an error for "schema": 3 specs (build raised),
        # warnings for older ones
        lint = oracle["bindings"].get("_physics_warnings")
        if lint:
            trial["physics_warnings"] = list(lint)[:20]
        trial["initial_hash"] = digest(config)
        trial["task_hash"] = digest(task)
        env = make_env(config, task, seed, perception=mode, camera=camera)
        initial = env.evaluate()
        trial["initial_fails"] = not initial["success"]
        if task.get("phases"):
            first = task["phases"][0]
            satisfied = bool(first.get("success")) and all(check(env.world.s, rule) for rule in first["success"])
            trial["initial_fails"] = trial["initial_fails"] and not satisfied
        errors, warnings = temporal_lint(env, task)
        if warnings:
            trial["temporal_warnings"] = warnings[:20]
        if errors:
            raise SpecError("temporal lint: " + "; ".join(errors[:5]))
        if any(r.get("gesture_dropped") for r in env.world.s.get("requests") or ()):
            trial["gesture_dropped"] = True
        idle_env = make_env(config, task, seed, perception=mode, camera=camera)
        trial["idle_fails"] = not idle(idle_env, task)["success"]
        ref = make_env(config, task, seed, perception=mode, camera=camera)
        if ref.realism is not None:
            ref.realism.record_observations = True
        try:
            runner = run_plan(ref, oracle["reference"], oracle["bindings"], strict=True)
            if runner.privileged_waits:
                trial["privileged_waits"] = runner.privileged_waits
            result = ref.evaluate()
            trial["reference_passes"] = result["success"]
            trial["reference_steps"] = result["steps"]
            trial["reference_time_s"] = result["time_s"]
            if not result["success"]:
                trial["reference_failures"] = result["failures"]
                trial["reference_goal_fraction"] = result["goal_fraction"]
        except PlanError as exc:
            trial["reference_passes"] = False
            trial["reference_error"] = str(exc)
        trial["reference_state_hash"] = digest(ref.world.s)
        schema = task.get("schema", 2)
        if isinstance(schema, int) and schema >= 3:
            lint = dialogue_lint(ref, task, oracle)
            if lint:
                trial["dialogue_lint"] = lint[:20]
                trial["reference_passes"] = False
        if ref.realism is not None and trial.get("reference_passes"):
            missing = perceivability_gaps(ref, task) + report_perceivability_gaps(ref, task)
            trial["perceivable"] = not missing
            if missing:
                trial["reference_passes"] = False
                trial["perceivability_gaps"] = missing
        wrongs = []
        for item in oracle["wrong"]:
            bad = make_env(config, task, seed, perception=mode, camera=camera)
            record = {"name": item.get("name", "wrong")}
            try:
                runner = run_plan(bad, item["plan"], oracle["bindings"], strict=False)
                record["ran"] = True
                record["refusals"] = len(runner.failures)
            except (PlanError, SpecError) as exc:
                record["ran"] = isinstance(exc, PlanError)
                record["error"] = str(exc)
            outcome = bad.evaluate()
            record["fails"] = not outcome["success"]
            record["why"] = outcome["failures"][:5] if outcome["failures"] else ("goal_fraction={}".format(outcome["goal_fraction"]))
            wrongs.append(record)
        trial["wrong"] = wrongs
        trial["wrong_fails"] = bool(wrongs) and all(w["fails"] for w in wrongs)
        trial["wrong_ran"] = bool(wrongs) and all(w.get("ran") for w in wrongs)
        trial["passed"] = all(trial.get(k) for k in ("initial_fails", "idle_fails", "reference_passes", "wrong_fails", "wrong_ran"))
    except Exception as exc:  # recorded as evidence of failure, never swallowed silently
        trial["error"] = "{}: {}".format(type(exc).__name__, exc)
        trial["trace"] = traceback.format_exc().splitlines()[-6:]
    return trial


def perceivability_gaps(env, task):
    """Realistic specs: every value a success predicate compares a report against must have appeared in what the
    reference actually observed (observations and action messages, handles translated back)."""
    from ..kernel.predicates import MISSING, get_path
    realism = env.realism
    seen_numbers, seen_strings = [], set()

    def walk(value):
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, (int, float)):
            seen_numbers.append(float(value))
        elif isinstance(value, str):
            seen_strings.add(value)
            for token in value.replace('"', " ").replace(",", " ").replace(":", " ").split():
                seen_strings.add(token.strip("{}[]()'."))
                try:
                    seen_numbers.append(float(token.strip("{}[]()'.,")))
                except ValueError:
                    pass
        elif isinstance(value, dict):
            for k, v in value.items():
                walk(k)
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
    for item in realism.observed:
        item = realism.from_handles(item)
        if isinstance(item, dict) and isinstance(item.get("message"), str):
            try:
                walk(json.loads(item["message"]))
            except ValueError:
                pass
        walk(item)
    gaps = []
    state = env.world.s

    def check_rule(rule):
        if not isinstance(rule, dict):
            return
        for key in ("all", "any"):
            for sub in rule.get(key, []) or []:
                check_rule(sub)
        if "not" in rule:
            return
        path = rule.get("path", "")
        if not isinstance(path, str) or not path.startswith("reports.") or "._to." in path or path.endswith("._to"):
            return      # recipients are chosen by the robot (it knows its residents), not perceived
        if "ref" in rule:
            expected = get_path(state, rule["ref"])
        else:
            expected = rule.get("value", MISSING)
        if expected is MISSING or expected is None or isinstance(expected, bool):
            return
        tol = float(rule.get("tolerance", rule.get("tol", 0.0)) or 0.0)
        if rule.get("op") == "approx" and isinstance(expected, list) and len(expected) == 2:
            expected, tol = expected[0], float(expected[1])
        if rule.get("op") == "between" and isinstance(expected, list) and len(expected) == 2:
            lo, hi = float(expected[0]), float(expected[1])
            expected, tol = (lo + hi) / 2.0, (hi - lo) / 2.0
        values = expected if isinstance(expected, list) and rule.get("op") in ("in", "subset") else [expected]
        for value in values:
            if isinstance(value, (int, float)):
                if any(abs(n - float(value)) <= max(tol, 1e-9) for n in seen_numbers):
                    return
            elif isinstance(value, str):
                if value in seen_strings:
                    return
            else:
                return
        gaps.append({"path": path, "expected": expected})
    for rule in success_rules(task):
        check_rule(rule)
    return gaps


def success_rules(task):
    """The success predicates verification checks for perceivability: the task's, then each phase's."""
    rules = list(task.get("success") or [])
    for phase in task.get("phases") or []:
        rules.extend(phase.get("success") or [])
    return rules


def _observed_fields(env):
    """{id: {field: True}} of every thing, person and pet whose field an observation of the reference run showed
    (realistic records, handles turned back into ids): evidence the robot perceived (TIER1_SWEEP 5.5)."""
    realism = env.realism
    seen = {}
    for item in realism.observed:
        if not isinstance(item, dict) or "visible_entities" not in item:
            continue
        item = realism.from_handles(item)
        for key in ("visible_entities", "people", "pets"):
            for aid, record in (item.get(key) or {}).items():
                if isinstance(record, dict):
                    seen.setdefault(aid, {}).update((field, True) for field in record)
    return seen


def report_perceivability_gaps(env, task):
    """Realistic specs, typed reports (TIER1_SWEEP 5.5; kernel/reports.py perceivability_gaps): every finding, status
    or measurement record that a report / report_set success predicate scores (report_set: its correct records) must
    rest on evidence the reference run perceived -- a field of the subject an observation showed or realism knows at
    its tier, the same field near it, or a notice of the listed kind (state["notices"]: perceived notices only).
    Reports a predicate forbids (under "not") are not evidence of anything and are skipped."""
    from ..kernel import reports
    from ..kernel.evaluation import evaluation_view
    from ..kernel.predicates import report_bounds
    view = evaluation_view(env.world.s, getattr(env, "_monitor", None), getattr(env, "mind", None))
    scored, keys = [], set()

    def collect(rule):
        if not isinstance(rule, dict) or "not" in rule:
            return
        for key in ("all", "any"):
            for sub in rule.get(key) or []:
                collect(sub)
        for key in ("report", "report_set"):
            if isinstance(rule.get(key), dict):
                spec = report_bounds(view, rule[key])
                if spec is None:
                    continue
                found = reports.matching_records(view, spec) if key == "report" else \
                    reports.matching_set_records(view, spec)
                for rec in found:
                    ident = (rec.get("n"), (rec.get("subject") or {}).get("id"))
                    if ident not in keys:
                        keys.add(ident)
                        scored.append(rec)
    for rule in success_rules(task):
        collect(rule)
    if not scored:
        return []
    notices = {n.get("kind") for n in env.world.s.get("notices") or () if isinstance(n, dict)}
    return [{"report": gap} for gap in reports.perceivability_gaps(env, scored, notices=notices,
                                                                     granted=_observed_fields(env))]


def dialogue_lint(env, task, oracle):
    """Schema-3 specs after the reference run (TIER1_SWEEP 4.11; kernel/dialogue.py lints): every fact well formed and
    reachable (an entity fact's entity perceived by the reference run, or aliases given), every questions.required
    fact (the task's and each phase's) asked with a canonical ask and answered, a wrong plan beside a required
    question, no legacy-topic ask in the reference plan. Returns problem strings."""
    from ..kernel import dialogue
    state = env.world.s
    problems = dialogue.lint_plan(oracle.get("reference"))
    problems.extend(dialogue.lint_facts(state, env, env))
    for scope in [task] + list(task.get("phases") or []):
        if scope.get("questions"):
            prefix = "{}: ".format(scope["id"]) if scope.get("id") else ""
            problems.extend(prefix + p for p in dialogue.lint_reference(
                {"questions": scope["questions"], "wrong": oracle.get("wrong")}, state))
    return problems


def verify_spec(spec, seeds=SEEDS, perception=None, camera=None, gen=None):
    record = {"catalog_id": spec.get("catalog_id"), "spec_hash": spec_hash(spec), "trials": [], "passed": False}
    for seed in seeds:
        record["trials"].append(run_trial(spec, seed, perception, camera, gen=gen) if gen is not None
                                else run_trial(spec, seed, perception, camera))
    record["passed"] = bool(record["trials"]) and all(t["passed"] for t in record["trials"])
    record["fidelity"] = spec.get("fidelity", "S")
    return record


def spec_hash(spec):
    try:
        return digest(expand(spec))
    except SpecError:
        return digest(spec)


def _worker_init():
    os.environ.setdefault("HOUSE_SIM_WORKER", "1")
    load_domains()


def _job(args):
    spec, seeds, perception, camera, gen = args
    return verify_spec(spec, seeds, perception, camera, gen=gen)


def verify_many(specs, seeds=SEEDS, workers=None, progress=None, perception=None, camera=None, gen=None):
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    jobs = [(copy.deepcopy(s), tuple(seeds), perception, camera, gen) for s in specs]
    results = []
    if workers == 1 or len(jobs) <= 2:
        load_domains()
        for i, job in enumerate(jobs, 1):
            results.append(_job(job))
            if progress:
                progress(i, len(jobs), results[-1])
        return results
    with ProcessPoolExecutor(max_workers=workers, initializer=_worker_init) as pool:
        for i, result in enumerate(pool.map(_job, jobs, chunksize=4), 1):
            results.append(result)
            if progress:
                progress(i, len(jobs), result)
    return results
