"""Micro-benchmarks of the tier-1 sweep (kernel v3.1; docs/design/TIER1_SWEEP.md 6.7 and 20, TIER1_INTEGRATION 3.4).

usage: python tools/sweep_bench.py [--seed N] [--repeats N] [--json]

Measured on a generated house (default seed 0, best of --repeats rounds), each against its budget:
    notices      notice derivation + a listening wait's interrupt check per substep            <= 0.03 ms
    until        a wait's until check (path predicate + perceivable) per substep               <= 0.02 ms
    monitor_idle monitor sampling per substep with nothing touched                             <= 0.05 ms
    monitor_quant monitor sampling with quantified predicates over touched entities           <= 0.5 ms
    calendar     loading data/calendar and a year of holidays and DST bounds (fresh caches)   <= 20 ms
    ask, report, mind_update   PEOPLE-IO's (2 / 1 / 1 ms; the verbs as a step minus a wait step of the same length;
                               skipped while kernel/dialogue.py, reports.py or mind.py are stubs or the verbs do not
                               take the new parameters)
Exit status 1 when a measurement is above 3x its budget (the tier-1 gate's G9 "sweep" extra; micro-benchmarks fail at
3x so they are not flaky), else 0.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from house_sim.generator import generate_house  # noqa: E402
from house_sim.kernel import clock, timeline  # noqa: E402
from house_sim.kernel.engine import HouseEnv  # noqa: E402
from house_sim.kernel.plugins import load_domains  # noqa: E402
from house_sim.kernel.predicates import check  # noqa: E402

BUDGETS_MS = {"notices": 0.03, "until": 0.02, "monitor_idle": 0.05, "monitor_quant": 0.5, "calendar": 20.0,
              "ask": 2.0, "report": 1.0, "mind_update": 1.0}
FAIL_FACTOR = 3.0


def best_ms(fn, calls, repeats):
    best = None
    for _ in range(repeats):
        start = time.perf_counter()
        for _ in range(calls):
            fn()
        spent = (time.perf_counter() - start) / calls
        best = spent if best is None else min(best, spent)
    return 1000.0 * best


def _env(seed, task=None, notices=False):
    config = generate_house(seed, residents="random")
    if notices:
        config["notices"] = []
    return HouseEnv(config, task, seed=seed, trace=False, observe_on_step=False)


def bench_notices(seed, repeats):
    env = _env(seed, notices=True)
    w = env.world
    run = timeline.WaitRun(env, {"seconds": 3600, "kinds": sorted(timeline.expand_kinds(["urgent", "doorbell"]))})
    w._listening = True
    timeline.derive_notices(env)

    def once():
        timeline.derive_notices(env)
        run.check()
    try:
        return best_ms(once, 2000, repeats)
    finally:
        w._listening = False


def bench_until(seed, repeats):
    env = _env(seed)
    w = env.world
    door = next((e for e, r in sorted(w.s["entities"].items()) if r.get("category") == "door"
                 and env.perception.visible(w.s, e)), None)
    # a condition that holds, so the whole per-substep check runs (the value, then what the robot sees of it, per
    # referenced id); every call counts as a new substep (a World write: no memo carries over)
    rule = {"path": "entities.{}.open".format(door), "op": "eq", "value": bool(w.s["entities"][door].get("open"))} \
        if door else {"path": "time_s", "op": "gte", "value": 0}

    def once():
        w.mutation_count += 1
        return timeline.holds_perceived(w, rule, None, env)
    return best_ms(once, 5000, repeats)


def _monitor_env(seed, task):
    env = _env(seed, task=task)
    env.world.begin()
    env._monitor.sample(env, "end")       # fill the footprint caches
    return env


def bench_monitor_idle(seed, repeats):
    task = {"success": [{"path": "time_s", "op": "gt", "value": 0}],
            "milestones": {"door": {"path": "robot.room", "op": "eq", "value": "nowhere"},
                           "held": {"pred": {"path": "stats.invalid", "op": "eq", "value": 0}, "held_for_s": 60}},
            "never": [{"path": "stats.damage", "op": "gt", "value": 0}],
            "always": [{"path": "robot.stopped", "op": "is_false", "value": True}]}
    env = _monitor_env(seed, task)

    def once():
        env._monitor.sample(env, "substep")
    return best_ms(once, 3000, repeats)


def bench_monitor_quant(seed, repeats):
    env = _env(seed)
    kind = None
    counts = {}
    for record in env.world.s["entities"].values():
        if record.get("portable"):
            counts[record.get("type")] = counts.get(record.get("type"), 0) + 1
    kind = max(sorted(counts), key=lambda k: counts[k]) if counts else "object"
    task = {"success": [{"path": "time_s", "op": "gt", "value": 0}],
            "milestones": {"all_dry": {"forall": {"where": {"type": kind}, "check": {"path": "wetness", "op": "lte",
                                                                                       "value": 0.5}}}},
            "never": [{"exists": {"where": {"category": "spill"}}},
                      {"count": {"where": {"type": kind}, "check": {"path": "broken", "op": "is_true", "value": True}},
                       "op": "gt", "value": 0}]}
    env = _monitor_env(seed, task)
    w = env.world
    targets = [e for e, r in sorted(w.s["entities"].items()) if r.get("type") == kind][:5]

    def once():
        for eid in targets:
            w.mut(eid)
        env._monitor.sample(env, "substep")
    return best_ms(once, 300, repeats)


def bench_calendar(repeats):
    def once():
        clock._DATA.clear()
        clock._YEARS.clear()
        state = {"clock": {"start": "2026-10-09T07:00:00", "model": 2, "region": "us", "latitude": 40.0,
                           "dst": True}, "time_s": 0.0}
        for region in ("us", "uk", "eu", "jp", "ca"):
            state["clock"]["region"] = region
            clock.holidays_between(state, "2026-01-01", "2026-12-31")
            clock.dst_active(state)
    return best_ms(once, 5, repeats)


def bench_people_io(seed, repeats):
    """ask / report / mind.update when PEOPLE-IO's modules are implemented (None otherwise)."""
    out = {"ask": None, "report": None, "mind_update": None}
    env = _env(seed)
    if env.mind is not None and hasattr(env.perception, "perceived_ids"):
        try:
            ids = env.perception.perceived_ids(env)
            out["mind_update"] = best_ms(lambda: env.mind.update(env, ids), 50, repeats)
        except NotImplementedError:
            pass
    state = env.world.s
    from house_sim.kernel.plugins import ACTIONS

    def step_cost(action, **kwargs):
        """ms of the verb itself: a step of it minus a wait step of the same simulated length (the advance, the
        evaluation and the bookkeeping every step pays)."""
        probe = env.step(action, **kwargs)
        if probe.get("category") == "argument":
            return None
        duration = ACTIONS[action].duration
        seconds = max(1, int(round(duration))) if isinstance(duration, (int, float)) else 1
        verb = best_ms(lambda: env.step(action, **kwargs), 20, repeats)
        base = best_ms(lambda: env.step("wait", seconds=seconds), 20, repeats)
        return max(0.0, verb - base)

    here = sorted(p for p, rec in state.get("people", {}).items() if rec.get("location") == state["robot"]["room"])
    if here:
        out["ask"] = step_cost("ask", target=here[0], about="house", aspect="status")
    out["report"] = step_cost("report", kind="status", subject="house", state="ok", to="household")
    return out


def main(argv):
    seed = int(argv[argv.index("--seed") + 1]) if "--seed" in argv else 0
    repeats = int(argv[argv.index("--repeats") + 1]) if "--repeats" in argv else 5
    load_domains()
    results = {"notices": bench_notices(seed, repeats), "until": bench_until(seed, repeats),
               "monitor_idle": bench_monitor_idle(seed, repeats), "monitor_quant": bench_monitor_quant(seed, repeats),
               "calendar": bench_calendar(repeats)}
    results.update(bench_people_io(seed, repeats))
    over = []
    rows = []
    for name, budget in BUDGETS_MS.items():
        value = results.get(name)
        if value is None:
            rows.append({"name": name, "ms": None, "budget_ms": budget, "status": "skipped (PEOPLE-IO not landed)"})
            continue
        status = "ok" if value <= budget else ("OVER (warning)" if value <= FAIL_FACTOR * budget else "FAIL")
        if status == "FAIL":
            over.append(name)
        rows.append({"name": name, "ms": round(value, 5), "budget_ms": budget, "status": status})
    if "--json" in argv:
        print(json.dumps({"seed": seed, "repeats": repeats, "results": rows, "failed": over}, indent=1))
    else:
        for row in rows:
            shown = "-" if row["ms"] is None else "{:.4f} ms".format(row["ms"])
            print("{:<14} {:>12}  (budget {} ms)  {}".format(row["name"], shown, row["budget_ms"], row["status"]))
        print("sweep_bench: {}".format("FAIL ({})".format(", ".join(over)) if over else "ok"))
    return 1 if over else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
