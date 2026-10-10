"""Random-action fuzzer: the simulator must never crash, never mutate state behind the journal, and replay
deterministically.

usage: python tools/fuzz.py [houses] [steps_per_house] [--first N] [--workers N] [--json OUT] [--no-determinism]

For each house (seeds first..first+houses-1, rotating dwelling/resident options) it plays `steps` random but
plausible commands: every registered action with arguments drawn from its parameter spec, biased toward things
near the robot (so actions actually succeed and the episode gets deep), interleaved with approaches, room moves
and waits. It runs with HOUSE_SIM_STRICT=1 (untracked mutations raise). Any exception other than a refused action
(ActionError) is a defect; they are deduplicated by (action, exception type, innermost project frame) and printed
with a repro (seed, opts, step, command). Determinism: each house's command log is replayed in a fresh process
with a different PYTHONHASHSEED and the final state digests must match.
Exit status 1 if anything was found.
"""
import json
import os
import random
import subprocess
import sys
import traceback
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HOUSE_SIM_STRICT", "1")

OPTS = [{}, {"dwelling": "apartment"}, {"dwelling": "house", "require": ["garage", "laundry_room"]},
        {"dwelling": "townhouse"}, {"residents": "minimal", "pets": "none"}, {"dwelling": "house", "require": ["basement", "patio"]}]
WORDS = ["on", "off", "in", "under", "behind", "left", "right", "both", "high", "low", "medium", "normal", "careful",
         "fast", "hot", "cold", "warm", "auto", "eco", "open", "closed", "yes", "no", "help", "done", "all", "1", "0"]
PHRASES = ["Dinner is ready.", "Please wait.", "Is this yours?", "I will tidy this up.", "Careful, the floor is wet.",
           "Can I move this?", "Where should this go?", "All done."]
SKIP = {"undo"}   # undo is exercised, but rarely (it replays inverses that the fuzzer cannot reason about)


def _env(seed, opts):
    from house_sim.generator import generate_house
    from house_sim.kernel import plugins
    from house_sim.kernel.engine import HouseEnv
    plugins.load_domains()
    env = HouseEnv(generate_house(seed, **opts), seed=seed, trace=False, observe_on_step=False)
    env.reset()
    return env


def _near_entities(state):
    from house_sim.kernel.predicates import root_room
    room = state["robot"]["room"]
    ids = [e for e in state["entities"] if root_room(state, e) == room]
    return sorted(ids)


def _value(rng, state, name, spec, near):
    kind = spec["type"]
    rooms = sorted(state["rooms"])
    people = sorted(state.get("people", {}))
    pets = sorted(state.get("pets", {}))
    held = [v for v in state["robot"].get("hands", {}).values() if v]
    if "choices" in spec and spec["choices"]:
        return rng.choice(spec["choices"])
    if kind in ("entity", "target", "agent"):
        pool = (held * 3 + near) if rng.random() < 0.85 else sorted(state["entities"])
        if kind in ("target", "agent") and rng.random() < 0.15:
            pool = people + pets + rooms + pool
        return rng.choice(pool) if pool else "nothing"
    if kind == "room":
        here = state["robot"]["room"]
        adj = sorted({r for c in state.get("connections", {}).values() if here in c["rooms"] for r in c["rooms"] if r != here})
        return rng.choice(adj if adj and rng.random() < 0.8 else rooms)
    if kind == "person":
        return rng.choice(people) if people else "nobody"
    if kind == "pet":
        return rng.choice(pets) if pets else "nopet"
    if kind in ("float", "number"):
        lo, hi = spec.get("min"), spec.get("max")
        if lo is not None or hi is not None:
            lo = float(lo if lo is not None else 0.0)
            hi = float(hi if hi is not None else lo + 100.0)
            return round(rng.uniform(lo, min(hi, lo + 3600.0)), 3)
        return rng.choice([0.0, 0.1, 0.5, 1.0, 2.0, 5.0, 20.0, 60.0, 180.0, -1.0])
    if kind == "int":
        lo, hi = int(spec.get("min", 0)), int(spec.get("max", 10))
        return rng.randint(lo, max(lo, min(hi, lo + 20)))
    if kind == "bool":
        return rng.random() < 0.5
    if kind == "list":
        return rng.choice([[], ["wet", "trip"], [rng.choice(near)] if near else []])
    if kind == "dict":
        return {}
    if kind == "cell":
        from house_sim.kernel import geometry
        cols, rows = geometry.grid_size(state["rooms"][state["robot"]["room"]])
        return [rng.randrange(cols), rng.randrange(rows)]
    if kind == "text":
        return rng.choice(PHRASES)
    if kind == "str":
        return rng.choice(WORDS + (near[:3] if near else []))
    return rng.choice(WORDS)


def _command(rng, env, names, stats):
    from house_sim.kernel.plugins import ACTIONS
    state = env.world.s
    near = _near_entities(state)
    held = [v for v in state["robot"].get("hands", {}).values() if v]
    roll = rng.random()
    if roll < 0.16 and near:
        return {"action": "approach", "target": rng.choice(near)}
    if roll < 0.21:
        return {"action": "move", "target": _value(rng, state, "target", {"type": "room"}, near)}
    if roll < 0.24:
        return {"action": "wait", "seconds": rng.choice([30.0, 120.0, 600.0])}
    if roll < 0.32 and near and len(held) < 2:
        portable = [e for e in near if state["entities"][e].get("portable")]
        if portable:
            if rng.random() < 0.3:
                return {"action": rng.choice(["crouch", "stand"])}
            return {"action": "pick_up", "target": rng.choice(portable)}
    if roll < 0.40 and held:
        dest = [e for e in near if state["entities"][e].get("surface") or state["entities"][e].get("container")]
        return {"action": "place", "target": rng.choice(held), "destination": rng.choice(dest + [state["robot"]["room"]])}
    # actions that have worked before get picked more often (deeper episodes)
    weights = [1.0 + 4.0 * stats.get(n, (0, 0))[0] / (1.0 + stats.get(n, (0, 0))[1]) for n in names]
    name = rng.choices(names, weights)[0]
    spec = ACTIONS[name]
    cmd = {"action": name}
    for pname, pspec in spec.params.items():
        if pspec.get("optional") and rng.random() < 0.5:
            continue
        cmd[pname] = _value(rng, state, pname, pspec, near)
    if name == "wait":
        cmd["seconds"] = min(float(cmd.get("seconds", 60.0) or 60.0), 900.0)
    return cmd


def _step(env, cmd):
    args = dict(cmd)
    action = args.pop("action")
    target = args.pop("target", "")
    destination = args.pop("destination", "")
    return env.step(action, target if isinstance(target, str) else json.dumps(target),
                    destination if isinstance(destination, str) else json.dumps(destination), **args)


def _frame(tb):
    frames = [f for f in traceback.extract_tb(tb) if "house_sim" in f.filename]
    f = frames[-1] if frames else traceback.extract_tb(tb)[-1]
    return "{}:{} ({})".format(os.path.relpath(f.filename, ROOT), f.lineno, f.name)


def run_house(job):
    seed, opts, steps = job
    from house_sim.kernel.plugins import ACTIONS
    from house_sim.kernel.predicates import digest
    rng = random.Random("fuzz:{}".format(seed))
    defects, log = [], []
    try:
        env = _env(seed, opts)
    except Exception as exc:  # noqa: BLE001
        return {"seed": seed, "opts": opts, "defects": [{"step": -1, "command": "generate/reset", "error": "{}: {}".format(type(exc).__name__, exc),
                                                         "where": _frame(exc.__traceback__), "trace": traceback.format_exc()[-2500:]}], "log": [], "digest": None, "ok": 0}
    names = sorted(n for n in ACTIONS if n not in SKIP)
    ok, stats = 0, {}
    for i in range(steps):
        cmd = _command(rng, env, names, stats)
        log.append(cmd)
        try:
            result = _step(env, cmd)
            ok += 1 if result.get("ok") else 0
            good, tries = stats.get(cmd["action"], (0, 0))
            stats[cmd["action"]] = (good + (1 if result.get("ok") else 0), tries + 1)
        except Exception as exc:  # noqa: BLE001
            defects.append({"step": i, "command": cmd, "error": "{}: {}".format(type(exc).__name__, str(exc)[:300]),
                            "where": _frame(exc.__traceback__), "trace": traceback.format_exc()[-2500:]})
            log.pop()
            try:
                env.world.rollback()
            except Exception:  # noqa: BLE001
                pass
            if len(defects) >= 8:
                break
    try:
        final = digest(env.world.s)
    except Exception as exc:  # noqa: BLE001
        final = None
        defects.append({"step": steps, "command": "digest", "error": "{}: {}".format(type(exc).__name__, exc), "where": _frame(exc.__traceback__), "trace": ""})
    return {"seed": seed, "opts": opts, "defects": defects, "log": log, "digest": final, "ok": ok}


def replay(seed, opts, log):
    from house_sim.kernel.predicates import digest
    env = _env(seed, opts)
    for cmd in log:
        try:
            _step(env, cmd)
        except Exception:  # noqa: BLE001
            env.world.rollback()
    return digest(env.world.s)


def main(argv):
    houses = int(argv[0]) if argv and argv[0].isdigit() else 12
    steps = int(argv[1]) if len(argv) > 1 and argv[1].isdigit() else 300
    first = int(argv[argv.index("--first") + 1]) if "--first" in argv else 0
    workers = int(argv[argv.index("--workers") + 1]) if "--workers" in argv else max(1, (os.cpu_count() or 2) - 2)
    out = argv[argv.index("--json") + 1] if "--json" in argv else None
    jobs = [(first + k, OPTS[k % len(OPTS)], steps) for k in range(houses)]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run_house, jobs))
    seen, rows = {}, []
    for r in results:
        for d in r["defects"]:
            key = (d["command"]["action"] if isinstance(d["command"], dict) else d["command"], d["error"].split(":")[0], d["where"])
            if key not in seen:
                seen[key] = dict(d, seed=r["seed"], opts=r["opts"], count=0)
            seen[key]["count"] += 1
    nondet = []
    if "--no-determinism" not in argv:
        for r in results[: min(len(results), 4)]:
            if r["digest"] is None or r["defects"]:
                continue
            env = dict(os.environ, PYTHONHASHSEED=str(12345 + r["seed"]))
            code = ("import json,sys; sys.path.insert(0, {root!r}); sys.argv=['x']; from tools.fuzz import replay; "
                    "d=json.load(open({f!r})); print(replay(d['seed'], d['opts'], d['log']))")
            tmp = ROOT / "tools" / ".fuzz_replay_{}.json".format(r["seed"])
            tmp.write_text(json.dumps({"seed": r["seed"], "opts": r["opts"], "log": r["log"]}), encoding="utf-8")
            try:
                proc = subprocess.run([sys.executable, "-c", code.format(root=str(ROOT), f=str(tmp))], env=env,
                                      capture_output=True, text=True, timeout=1800)
                other = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "ERROR " + proc.stderr[-500:]
            finally:
                tmp.unlink()
            if other != r["digest"]:
                nondet.append({"seed": r["seed"], "opts": r["opts"], "first": r["digest"], "replay": other})
    total_ok = sum(r["ok"] for r in results)
    total = sum(len(r["log"]) for r in results)
    print("houses {}  commands {}  succeeded {} ({:.0%})  distinct defects {}  nondeterministic {}".format(
        len(results), total, total_ok, total_ok / max(1, total), len(seen), len(nondet)))
    for key, d in sorted(seen.items(), key=lambda kv: -kv[1]["count"]):
        print("- [{}x] {} -> {} at {}".format(d["count"], key[0], d["error"], d["where"]))
        print("    repro: seed {} opts {} step {} command {}".format(d["seed"], json.dumps(d["opts"]), d["step"], json.dumps(d["command"])[:300]))
    for n in nondet:
        print("- NONDETERMINISTIC replay: seed {} opts {} digest {} vs {}".format(n["seed"], json.dumps(n["opts"]), n["first"][:12], n["replay"][:60]))
    if out:
        Path(out).write_text(json.dumps({"defects": list(seen.values()), "nondeterministic": nondet}, indent=1, default=str), encoding="utf-8")
    return 1 if seen or nondet else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
