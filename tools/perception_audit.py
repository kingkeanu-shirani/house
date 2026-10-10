"""Realistic-perception leak auditor.

usage: python tools/perception_audit.py [houses] [steps] [--first N] [--workers N] [--json OUT] [--verbose]

Generates houses, runs random walks in perception="realistic" (approach / inspect / read / pick up / place / move /
wait and random domain actions, all with handles taken from observations) and checks every observation and action
message for leaks:
  id        a true entity id appears (handles, room, person and pet ids are fine)
  secret    an underscore field or a secret_fields field appears in a perceived record
  hidden    a never-tier field (owner, home, value, intent, ...) appears unlabeled
  tier      an inspect/read/measure-tier field shows its exact true value while the robot's knowledge is lower
  hazard    obs.grid.hazards names a source the robot does not perceive
  message   an action message contains the exact value of a measure-tier field of its target that was not measured
Violations are attributed to the emitting module (kernel realism, the type's domain, an observer, or an action's
owner). Exit status 1 if any leak is found.
"""
import json
import os
import random
import re
import sys
import traceback
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
NUM = re.compile(r"-?\d+\.\d+")
OPTS = [{}, {"dwelling": "apartment"}, {"dwelling": "house", "require": ["garage", "laundry_room"]}, {"dwelling": "townhouse"},
        {"residents": "minimal", "pets": "none"}]


def _owner_of_type(reg, type_name):
    src = getattr(reg, "sources", {}).get(type_name, "?")
    return "types:" + src.replace(".json", "")


def check_obs(env, obs, leaks, where):
    from house_sim.kernel.realism import TIER_NEED, MEASURE
    state = env.world.s
    realism = env.realism
    entities = state["entities"]
    plain = set(state["rooms"]) | set(state.get("people", {})) | set(state.get("pets", {})) | {"robot"}
    observer_keys = getattr(env, "_observer_keys", {})

    def scan(value, path):
        if isinstance(value, str):
            for t in TOKEN.findall(value):
                if t in entities and t not in plain:
                    top = path.split(".")[0]
                    leaks.append(("id", observer_keys.get(top, "kernel:" + top), "{} {}: {}".format(where, path, t)))
        elif isinstance(value, dict):
            for k, v in value.items():
                scan(k, path)
                scan(v, path + "." + str(k))
        elif isinstance(value, list):
            for i, v in enumerate(value):
                scan(v, path)
    scan(obs, "obs")
    for handle, rec in obs.get("visible_entities", {}).items():
        eid = realism.handles.resolve(handle)
        if eid is None or eid not in entities:
            continue
        true = entities[eid]
        owner = _owner_of_type(env.registry, true.get("type"))
        for field, shown in rec.items():
            if field.startswith("_") or field in (true.get("secret_fields") or []):
                leaks.append(("secret", owner, "{} {}.{}".format(where, eid, field)))
                continue
            if field not in true:
                continue
            tier = realism.tier(true, field)
            if tier == "never":
                leaks.append(("hidden", owner, "{} {}.{}".format(where, eid, field)))
                continue
            need = TIER_NEED[tier]
            known = realism.knowledge.level(eid, true, field)
            value = true[field]
            informative = value not in (None, False, 0, 0.0, "", [], {}) and not isinstance(value, bool)
            if field == "size_m" and isinstance(value, list) and all(abs(_r05(v) - float(v)) < 1e-9 for v in value if isinstance(v, (int, float))):
                continue    # coincidence of the 5 cm visual estimate with a size that is a multiple of 5 cm
            if tier != "glance" and known < need and informative and _same(shown, value):
                if isinstance(value, float) or tier in ("read", "measure") or isinstance(value, str):
                    leaks.append(("tier", owner, "{} {}.{} ({} tier, knowledge {}) = {!r}".format(where, eid, field, tier, known, value)))
    for item in obs.get("grid", {}).get("hazards", []):
        src = item.get("source")
        if isinstance(src, str) and src.startswith("obj_") and src not in obs.get("visible_entities", {}):
            leaks.append(("hazard", "kernel:hazards", "{} unperceived source {}".format(where, src)))


def _r05(v):
    return round(float(v) * 20.0) / 20.0


def _same(shown, value):
    if isinstance(value, float) and isinstance(shown, (int, float)) and not isinstance(shown, bool):
        return abs(shown - value) < 1e-9
    return shown == value


def check_message(env, action, target_eid, message, leaks):
    from house_sim.kernel.plugins import OWNERS
    from house_sim.kernel.realism import MEASURE
    state = env.world.s
    entities = state["entities"]
    plain = set(state["rooms"]) | set(state.get("people", {})) | set(state.get("pets", {})) | {"robot"}
    owner = OWNERS.get(("action", action), "?")
    for t in TOKEN.findall(message or ""):
        if t in entities and t not in plain:
            leaks.append(("id", "action:" + owner, "{} message: {}".format(action, t)))
    if target_eid and target_eid in entities:
        rec = entities[target_eid]
        realism = env.realism
        try:
            data = json.loads(message)
        except (TypeError, ValueError):
            data = None
        for field, value in rec.items():
            if not isinstance(value, float) or isinstance(value, bool) or abs(value) < 1e-6:
                continue
            if realism.tier(rec, field) != "measure" or realism.knowledge.level(target_eid, rec, field) >= MEASURE:
                continue
            if isinstance(data, dict):
                shown = data.get(field)
                if isinstance(shown, (int, float)) and not isinstance(shown, bool) and abs(shown - value) < 1e-9:
                    leaks.append(("message", "action:" + owner, "{} reveals {}.{} = {}".format(action, target_eid, field, value)))
                continue
            stem = field.split("_")[0]
            for m in NUM.finditer(message or ""):
                if abs(float(m.group(0)) - value) < 1e-9 or any(abs(float(m.group(0)) - round(value, d)) < 1e-9 and round(value, d) != round(value)
                                                                for d in (1, 2)):
                    if stem in (message or "")[max(0, m.start() - 40):m.start()].lower():
                        leaks.append(("message", "action:" + owner, "{} reveals {}.{} = {}".format(action, target_eid, field, value)))
                        break


def run_house(job):
    seed, opts, steps = job
    from house_sim.generator import generate_house
    from house_sim.kernel import plugins
    from house_sim.kernel.engine import HouseEnv
    plugins.load_domains()
    leaks, errors = [], []
    rng = random.Random("percaudit:{}".format(seed))
    try:
        env = HouseEnv(generate_house(seed, **opts), seed=seed, perception="realistic", trace=False, observe_on_step=False)
    except Exception as exc:  # noqa: BLE001
        return {"seed": seed, "leaks": [], "errors": ["generate: {}: {}".format(type(exc).__name__, exc)], "steps": 0}
    env._observer_keys = _observer_keys(env)
    names = sorted(n for n in plugins.ACTIONS if n not in ("undo", "watch", "run_pending"))
    obs = env.observe()
    check_obs(env, obs, leaks, "step0")
    for i in range(steps):
        handles = sorted(obs.get("visible_entities", {}))
        roll = rng.random()
        if roll < 0.25 and handles:
            cmd = {"action": "approach", "target": rng.choice(handles)}
        elif roll < 0.45 and handles:
            cmd = {"action": rng.choice(["inspect", "read", "inspect"]), "target": rng.choice(handles)}
        elif roll < 0.55 and handles:
            cmd = {"action": "pick_up", "target": rng.choice(handles)}
        elif roll < 0.6 and obs.get("holding"):
            cmd = {"action": "place", "target": rng.choice(obs["holding"]), "destination": obs["room_id"]}
        elif roll < 0.7:
            exits = obs.get("room", {}).get("exits") or []
            cmd = {"action": "move", "target": rng.choice(exits)} if exits else {"action": "look"}
        elif roll < 0.73:
            cmd = {"action": "wait", "seconds": 300}
        else:
            name = rng.choice(names)
            spec = plugins.ACTIONS[name]
            cmd = {"action": name}
            for pname, p in spec.params.items():
                if p.get("optional") and rng.random() < 0.6:
                    continue
                kind = p["type"]
                if "choices" in p:
                    cmd[pname] = rng.choice(p["choices"])
                elif kind in ("entity", "target", "agent") and handles:
                    cmd[pname] = rng.choice(handles)
                elif kind == "room":
                    cmd[pname] = obs["room_id"]
                elif kind in ("number", "float"):
                    cmd[pname] = 1.0
                elif kind == "int":
                    cmd[pname] = 1
                elif kind == "bool":
                    cmd[pname] = True
                elif kind == "text":
                    cmd[pname] = "hello"
                elif kind == "list":
                    cmd[pname] = []
                elif kind == "dict":
                    cmd[pname] = {}
                else:
                    cmd[pname] = "on"
        args = dict(cmd)
        action = args.pop("action")
        target = args.pop("target", "")
        destination = args.pop("destination", "")
        target_eid = env.realism.handles.resolve(target) if isinstance(target, str) else None
        try:
            result = env.step(action, target, destination, **args)
        except Exception as exc:  # noqa: BLE001
            errors.append("{} {}: {}: {}".format(action, target, type(exc).__name__, str(exc)[:200]))
            try:
                env.world.rollback()
            except Exception:  # noqa: BLE001
                pass
            continue
        check_message(env, action, target_eid, result.get("message"), leaks)
        obs = env.observe()
        check_obs(env, obs, leaks, "step{}".format(i + 1))
    return {"seed": seed, "leaks": leaks, "errors": errors, "steps": steps}


def _observer_keys(env):
    """Which observer adds which top-level observation key (for attribution)."""
    from house_sim.kernel.plugins import OBSERVERS
    keys = {}
    base = env.perception.observe.__self__
    for order, name, fn in OBSERVERS:
        probe = {}
        try:
            fn(env, env.world, probe)
        except Exception:  # noqa: BLE001
            continue
        for key in probe:
            keys.setdefault(key, "observer:" + name)
    return keys


def main(argv):
    houses = int(argv[0]) if argv and argv[0].isdigit() else 20
    steps = int(argv[1]) if len(argv) > 1 and argv[1].isdigit() else 120
    first = int(argv[argv.index("--first") + 1]) if "--first" in argv else 0
    workers = int(argv[argv.index("--workers") + 1]) if "--workers" in argv else max(1, (os.cpu_count() or 2) - 2)
    out = argv[argv.index("--json") + 1] if "--json" in argv else None
    jobs = [(first + k, OPTS[k % len(OPTS)], steps) for k in range(houses)]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run_house, jobs))
    by_kind, by_owner, examples = Counter(), Counter(), defaultdict(list)
    for r in results:
        for kind, owner, detail in r["leaks"]:
            by_kind[kind] += 1
            by_owner[(kind, owner)] += 1
            if len(examples[(kind, owner)]) < 3:
                examples[(kind, owner)].append("seed {}: {}".format(r["seed"], detail))
    errors = [e for r in results for e in r["errors"]]
    print("houses {}  steps {}  leaks {}  crashes {}".format(len(results), sum(r["steps"] for r in results), sum(by_kind.values()), len(errors)))
    for kind, n in by_kind.most_common():
        print("  {:6d}  {}".format(n, kind))
    for (kind, owner), n in by_owner.most_common():
        print("  {:6d}  {:8s} {}".format(n, kind, owner))
        if "--verbose" in argv:
            for ex in examples[(kind, owner)]:
                print("            " + ex[:300])
    for e in errors[:15]:
        print("  crash:", e)
    if out:
        Path(out).write_text(json.dumps({"leaks": [dict(seed=r["seed"], kind=k, owner=o, detail=d) for r in results for k, o, d in r["leaks"]],
                                         "errors": errors}, indent=1), encoding="utf-8")
    return 1 if by_kind or errors else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
