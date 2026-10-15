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
"""
import copy
import json
import os
import traceback
from concurrent.futures import ProcessPoolExecutor

from ..kernel import camera as camera_mod
from ..kernel.errors import SpecError
from ..kernel.plugins import load_domains
from ..kernel.predicates import digest
from .runner import PlanError, build, make_env, run_plan
from .spec import expand

SEEDS = (0, 1, 2, 41, 97)


def idle(env, task):
    remaining = float(task.get("time_budget_s", 7200))
    steps = 0
    while remaining > 0 and steps < 30:
        chunk = int(min(3600, remaining))
        if chunk < 1:
            break
        env.step("wait", seconds=chunk)
        remaining -= chunk
        steps += 1
    return env.evaluate()


def run_trial(spec, seed, perception=None, camera=None):
    """perception: None -> each spec's own mode ("perception" field, default oracle); "realistic" forces it.
    camera: None -> each spec's own camera ("camera" field, default room); "room" | "geometric" forces it."""
    trial = {"seed": seed, "passed": False}
    try:
        config, task, oracle = build(spec, seed, camera=camera, perception=perception)
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
        idle_env = make_env(config, task, seed, perception=mode, camera=camera)
        trial["idle_fails"] = not idle(idle_env, task)["success"]
        ref = make_env(config, task, seed, perception=mode, camera=camera)
        if ref.realism is not None:
            ref.realism.record_observations = True
        try:
            run_plan(ref, oracle["reference"], oracle["bindings"], strict=True)
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
        if ref.realism is not None and trial.get("reference_passes"):
            missing = perceivability_gaps(ref, task)
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
    for rule in task.get("success", []):
        check_rule(rule)
    return gaps


def verify_spec(spec, seeds=SEEDS, perception=None, camera=None):
    record = {"catalog_id": spec.get("catalog_id"), "spec_hash": spec_hash(spec), "trials": [], "passed": False}
    for seed in seeds:
        record["trials"].append(run_trial(spec, seed, perception, camera))
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
    spec, seeds, perception, camera = args
    return verify_spec(spec, seeds, perception, camera)


def verify_many(specs, seeds=SEEDS, workers=None, progress=None, perception=None, camera=None):
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    jobs = [(copy.deepcopy(s), tuple(seeds), perception, camera) for s in specs]
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
