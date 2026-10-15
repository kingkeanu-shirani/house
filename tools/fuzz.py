"""Random-action fuzzer: the simulator must never crash, never mutate state behind the journal, and replay
deterministically.

usage: python tools/fuzz.py [houses] [steps_per_house] [--first N] [--workers N] [--json OUT] [--no-determinism]
                            [--camera room|geometric] [--perception oracle|realistic|mixed]
                            [--battery PCT] [--faults random|none]

For each house (seeds first..first+houses-1, rotating dwelling/resident options) it plays `steps` random but
plausible commands: every registered action with arguments drawn from its parameter spec, biased toward things
near the robot (so actions actually succeed and the episode gets deep), interleaved with approaches, room moves
and waits. It runs with HOUSE_SIM_STRICT=1 (untracked mutations raise). Any exception other than a refused action
(ActionError) is a defect; they are deduplicated by (action, exception type, innermost project frame) and printed
with a repro (seed, opts, step, command). Determinism: each house's command log is replayed in a fresh process
with a different PYTHONHASHSEED and the final state digests must match.

--camera geometric runs the robot's posed cameras (kernel v3.0; default: each env's own, room) and adds the camera's
looks and turns to the mix. --perception realistic plays the same privileged commands through realistic perception
(ids become handles; every step is observed, so the realistic renderer is fuzzed too, and the final digest includes
the realistic observation); "mixed" (the default with --camera geometric) alternates oracle and realistic houses.

--battery PCT starts the houses with an almost flat pack (kernel v3.0 robot body): at PCT %, PCT / 4 % or PCT / 20 %,
rotating by seed, so some packs run flat within a few hundred commands; even seeds off their dock (battery cuts,
"Powered down partway", PowerOff refusals, the stranded robot), odd seeds left seated where the house put them
(charging back up and waking at 3 %). --faults random schedules random robot faults per house
(body_faults.sample_fault_events: three robot.fault events within the first half hour, the first at 0 s, random kinds,
sides, severities and latent flags, some cleared later), seeded by the house seed so the replay draws the same ones;
failed arms drop what they hold, cameras and microphones fail, the network drops, the robot loses its pose. Both
options are part of every house's replay and default to --perception mixed (the realistic proprioception and the
sensor gates are rendered too). The tier-1 gate runs `--battery 5 --faults random` from BODY-FAULTS on.
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


BATTERY_SHARES = (1.0, 0.25, 0.05)     # --battery PCT: the start level of a house is PCT x one of these (by seed)
FAULT_HORIZON_S = 1800.0                # --faults random: when the random faults start


def robot_options(config, seed, battery=None, faults=None):
    """Apply --battery / --faults to a generated house config (in place): the pack at battery x BATTERY_SHARES[seed % 3]
    % (even seeds undocked), and with faults "random" three random robot fault events seeded by the house seed."""
    robot = config.setdefault("robot", {})
    if battery is not None:
        robot["battery"] = round(float(battery) * BATTERY_SHARES[seed % len(BATTERY_SHARES)], 6)
        if seed % 2 == 0:
            robot.update(docked=False, charging=False)
    if faults == "random":
        from house_sim.kernel import body_faults
        rng = random.Random("fuzz-faults:{}".format(seed))
        config["events"] = list(config.get("events") or []) + body_faults.sample_fault_events(
            rng, count=3, horizon_s=FAULT_HORIZON_S)
    return config


def _env(seed, opts, camera=None, perception=None, battery=None, faults=None):
    from house_sim.generator import generate_house
    from house_sim.kernel import plugins
    from house_sim.kernel.engine import HouseEnv
    plugins.load_domains()
    config = robot_options(generate_house(seed, **opts), seed, battery, faults)
    env = HouseEnv(config, seed=seed, trace=False, observe_on_step=False, camera=camera, perception=perception)
    env.reset()
    return env


def _perception_of(mode, seed):
    """The perception mode of one house: oracle / realistic, or alternating by seed for "mixed"."""
    if mode == "mixed":
        return "realistic" if seed % 2 else "oracle"
    return mode or "oracle"


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
    if roll < 0.44 and isinstance(state["robot"].get("view"), dict):
        # the geometric camera's own verbs (look's forms, turning to a thing when the face verb exists)
        if near and "face" in ACTIONS and rng.random() < 0.4:
            return {"action": "face", "target": rng.choice(near)}
        return {"action": "look", "target": rng.choice(["around", "forward", "down", "up", "left", "right", "behind"])}
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
    if env.realism is not None:
        args = env.realism.to_handles(args)      # privileged ids -> the handles a realistic agent would use
    action = args.pop("action")
    target = args.pop("target", "")
    destination = args.pop("destination", "")
    result = env.step(action, target if isinstance(target, str) else json.dumps(target),
                      destination if isinstance(destination, str) else json.dumps(destination), **args)
    if env.realism is not None:
        result["observation"] = env.observe()    # the realistic renderer runs every step
    return result


def _final_digest(env):
    from house_sim.kernel.predicates import digest
    if env.realism is None:
        return digest(env.world.s)
    return digest({"state": env.world.s, "observation": env.observe()})


def _frame(tb):
    frames = [f for f in traceback.extract_tb(tb) if "house_sim" in f.filename]
    f = frames[-1] if frames else traceback.extract_tb(tb)[-1]
    return "{}:{} ({})".format(os.path.relpath(f.filename, ROOT), f.lineno, f.name)


def run_house(job):
    seed, opts, steps = job[:3]
    camera = job[3] if len(job) > 3 else None
    perception = job[4] if len(job) > 4 else None
    battery = job[5] if len(job) > 5 else None
    faults = job[6] if len(job) > 6 else None
    from house_sim.kernel.plugins import ACTIONS
    rng = random.Random("fuzz:{}".format(seed))
    defects, log = [], []
    try:
        env = _env(seed, opts, camera, perception, battery, faults)
    except Exception as exc:  # noqa: BLE001
        return {"seed": seed, "opts": opts, "camera": camera, "perception": perception, "battery": battery,
                "faults": faults,
                "defects": [{"step": -1, "command": "generate/reset", "error": "{}: {}".format(type(exc).__name__, exc),
                             "where": _frame(exc.__traceback__), "trace": traceback.format_exc()[-2500:]}],
                "log": [], "digest": None, "ok": 0}
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
        final = _final_digest(env)
    except Exception as exc:  # noqa: BLE001
        final = None
        defects.append({"step": steps, "command": "digest", "error": "{}: {}".format(type(exc).__name__, exc), "where": _frame(exc.__traceback__), "trace": ""})
    return {"seed": seed, "opts": opts, "camera": camera, "perception": perception, "battery": battery,
            "faults": faults, "defects": defects, "log": log, "digest": final, "ok": ok}


def replay(seed, opts, log, camera=None, perception=None, battery=None, faults=None):
    env = _env(seed, opts, camera, perception, battery, faults)
    for cmd in log:
        try:
            _step(env, cmd)
        except Exception:  # noqa: BLE001
            env.world.rollback()
    return _final_digest(env)


def main(argv):
    houses = int(argv[0]) if argv and argv[0].isdigit() else 12
    steps = int(argv[1]) if len(argv) > 1 and argv[1].isdigit() else 300
    first = int(argv[argv.index("--first") + 1]) if "--first" in argv else 0
    workers = int(argv[argv.index("--workers") + 1]) if "--workers" in argv else max(1, (os.cpu_count() or 2) - 2)
    out = argv[argv.index("--json") + 1] if "--json" in argv else None
    camera = argv[argv.index("--camera") + 1] if "--camera" in argv else None
    faults = argv[argv.index("--faults") + 1] if "--faults" in argv else None
    try:
        battery = float(argv[argv.index("--battery") + 1]) if "--battery" in argv else None
    except (IndexError, ValueError):
        battery = -1.0
    body_run = battery is not None or faults == "random"
    perception = argv[argv.index("--perception") + 1] if "--perception" in argv else \
        ("mixed" if camera == "geometric" or body_run else None)
    if camera not in (None, "room", "geometric") or perception not in (None, "oracle", "realistic", "mixed") or \
            faults not in (None, "random", "none") or (battery is not None and not 0.0 <= battery <= 100.0):
        print("--camera must be room or geometric; --perception oracle, realistic or mixed; --battery a % from 0 to "
              "100; --faults random or none")
        return 2
    faults = None if faults == "none" else faults
    jobs = [(first + k, OPTS[k % len(OPTS)], steps, camera, _perception_of(perception, first + k) if perception else None,
             battery, faults) for k in range(houses)]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run_house, jobs))
    seen, rows = {}, []
    for r in results:
        for d in r["defects"]:
            key = (d["command"]["action"] if isinstance(d["command"], dict) else d["command"], d["error"].split(":")[0], d["where"])
            if key not in seen:
                seen[key] = dict(d, seed=r["seed"], opts=r["opts"], camera=r.get("camera"), perception=r.get("perception"),
                                 battery=r.get("battery"), faults=r.get("faults"), count=0)
            seen[key]["count"] += 1
    nondet = []
    if "--no-determinism" not in argv:
        for r in results[: min(len(results), 4)]:
            if r["digest"] is None or r["defects"]:
                continue
            env = dict(os.environ, PYTHONHASHSEED=str(12345 + r["seed"]))
            code = ("import json,sys; sys.path.insert(0, {root!r}); sys.argv=['x']; from tools.fuzz import replay; "
                    "d=json.load(open({f!r})); print(replay(d['seed'], d['opts'], d['log'], d.get('camera'), "
                    "d.get('perception'), d.get('battery'), d.get('faults')))")
            # per process and variant: the gate runs the room, geometric and battery fuzz at the same time
            body_variant = r.get("battery") is not None or r.get("faults")
            variant = "{}{}".format(r.get("camera") or "env", "_body" if body_variant else "")
            tmp = ROOT / "tools" / ".fuzz_replay_{}_{}_{}.json".format(r["seed"], variant, os.getpid())
            tmp.write_text(json.dumps({"seed": r["seed"], "opts": r["opts"], "log": r["log"], "camera": r.get("camera"),
                                       "perception": r.get("perception"), "battery": r.get("battery"),
                                       "faults": r.get("faults")}), encoding="utf-8")
            try:
                proc = subprocess.run([sys.executable, "-c", code.format(root=str(ROOT), f=str(tmp))], env=env,
                                      capture_output=True, text=True, timeout=1800)
                other = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "ERROR " + proc.stderr[-500:]
            finally:
                tmp.unlink()
            if other != r["digest"]:
                nondet.append({"seed": r["seed"], "opts": r["opts"], "camera": r.get("camera"), "perception": r.get("perception"),
                               "battery": r.get("battery"), "faults": r.get("faults"), "first": r["digest"], "replay": other})
    total_ok = sum(r["ok"] for r in results)
    total = sum(len(r["log"]) for r in results)
    print("houses {}  commands {}  succeeded {} ({:.0%})  distinct defects {}  nondeterministic {}{}{}".format(
        len(results), total, total_ok, total_ok / max(1, total), len(seen), len(nondet),
        "  camera {} perception {}".format(camera, perception or "oracle") if camera or perception else "",
        "  battery {} faults {}".format(battery, faults or "none") if battery is not None or faults else ""))
    for key, d in sorted(seen.items(), key=lambda kv: -kv[1]["count"]):
        print("- [{}x] {} -> {} at {}".format(d["count"], key[0], d["error"], d["where"]))
        print("    repro: seed {} opts {}{} step {} command {}".format(
            d["seed"], json.dumps(d["opts"]), "".join(" {} {}".format(k, d[k]) for k in ("camera", "perception", "battery",
                                                                                       "faults") if d.get(k) is not None),
            d["step"], json.dumps(d["command"])[:300]))
    for n in nondet:
        print("- NONDETERMINISTIC replay: seed {} opts {} digest {} vs {}".format(n["seed"], json.dumps(n["opts"]), n["first"][:12], n["replay"][:60]))
    if out:
        Path(out).write_text(json.dumps({"defects": list(seen.values()), "nondeterministic": nondet}, indent=1, default=str), encoding="utf-8")
    return 1 if seen or nondet else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
