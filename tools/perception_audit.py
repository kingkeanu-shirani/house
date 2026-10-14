"""Realistic-perception leak auditor.

usage: python tools/perception_audit.py [houses] [steps] [--first N] [--workers N] [--json OUT] [--verbose]

Generates houses, runs random walks in perception="realistic" (approach / inspect / read / pick up / place / move /
wait and random domain actions, all with handles taken from observations) and checks every observation and action
message for leaks:
  id        a true entity id appears (handles, room, person and pet ids are fine), including the id of an entity
            that existed at the start of the step and was destroyed during it (merged, consumed, pulled) or in any
            earlier step of the episode (realism._ever); a descriptive word (label, kind, category...) that merely
            equals a hand-written id such as "sink" is not one (descriptive fields are never id-translated), an
            id-shaped word there is
  order     id-keyed JSON (a dict keyed by handles, or a list of handles) in an action message or an observer
            channel is in true-id order rather than handle order: the order ranks look-alikes by their true type.
            Judged over the whole run per container (owner + path): reported when it was never out of true-id order
            and its distinct id sets make a chance match unlikely (a pseudo-random order of two things matches half
            the time)
  secret    an underscore field or a secret_fields field appears in a perceived record
  hidden    a never-tier field (owner, home, value, intent, ...) appears unlabeled
  tier      an inspect/read/measure-tier field shows its exact true value while the robot's knowledge is lower --
            unless Realism shows it by design from memory: the robot learned that same value at the field's tier
            and the knowledge only lapsed because the thing moved unseen (realism.knowledge.stale)
  hazard    obs.grid.hazards names a source the robot does not perceive
  (the id rule also covers the ids of people and pets that are only shapes in the robot's dark room)
  message   an action message contains the exact value of a measure-tier field of its target that was not measured
Violations are attributed to the emitting module (kernel realism, the type's domain, an observer, or an action's
owner). Exit status 1 if any leak is found.
"""
import json
import math
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


HANDLE = re.compile(r"^obj_[0-9a-f]{8,16}$")
HANDLE_IN = re.compile(r"obj_[0-9a-f]{8,16}")
# observation keys the kernel builds (handle-ordered or ordered by meaning, e.g. hands); the rest are observer channels
KERNEL_OBS_KEYS = {"room_id", "room", "connections", "robot", "holding", "visible_entities", "people", "pets", "grid",
                   "time", "steps", "time_s", "battery", "stopped", "pending", "dark", "weather", "instruction",
                   "last_answer", "messages", "perception"}


def _known_ids(state, start_ids, realism=None):
    """Entity ids now, those that existed when the step started (destroyed during it) and every id that existed earlier
    in the episode (realism._ever: destroyed in an earlier step)."""
    ever = {e for e in (getattr(realism, "_ever", None) or ()) if isinstance(e, str) and ":" not in e}
    return set(state["entities"]) | set(start_ids or ()) | ever


def _descriptive_word(path, token):
    """A token under a descriptive key (label, kind, category, material...) that is a plain word, not an id-shaped one:
    realism never id-translates descriptive fields, so "sink" as the sink's own label is no leak."""
    from house_sim.kernel.realism import DESCRIPTIVE_KEYS, id_shaped
    last = path.rsplit(".", 1)[-1]
    return last in DESCRIPTIVE_KEYS and not id_shaped(token.lower())


def id_order_findings(realism, value, path=""):
    """[(path, ids in shown order, in_true_order)] for each dict keyed by handles and each list of handles naming two or
    more distinct things whose true-id order differs from their handle order (where the two orders agree, the shown
    order tells nothing). in_true_order: the things are listed in true-id order, which ranks look-alikes by type."""
    found = []

    def check(items, where):
        pairs, seen = [], set()
        for h in items:
            if isinstance(h, str) and HANDLE.match(h):
                e = realism.handles.resolve(h)
                if e is not None and e not in seen:
                    seen.add(e)
                    pairs.append((h, e))
        if len(pairs) < 2:
            return
        by_true = [h for h, _ in sorted(pairs, key=lambda p: p[1])]
        if by_true == sorted(h for h, _ in pairs):
            return
        ids = tuple(e for _, e in pairs)
        found.append((where, ids, list(ids) == sorted(ids)))

    def walk(v, where):
        if isinstance(v, dict):
            check(list(v), where)
            for k, item in v.items():
                walk(item, "{}.{}".format(where, k) if where else str(k))
        elif isinstance(v, list):
            check(v, where)
            for item in v:
                walk(item, where)
    walk(value, path)
    return found


def _report_order(findings, owner, label, leaks, order, where=""):
    """One-off checks (order=None) report every true-id-ordered container; an audit run passes an accumulator list and
    decides later with order_leaks, so a container whose order is merely pseudo-random is not reported. The container
    is named by label and path with handles masked (the same container in every step and house)."""
    for path, ids, in_true in findings:
        template = HANDLE_IN.sub("*", "{} {}".format(label, path or "(top)"))
        if order is not None:
            order.append((owner, template, ids, in_true))
        elif in_true:
            leaks.append(("order", owner, "{}{} is in true-id order".format(where + " " if where else "", template)))


def order_leaks(records, chance=0.125):
    """Leaks from accumulated (owner, template, ids, in_true_order) records: a container (owner and path, handles
    masked) is reported when it was never seen out of true-id order and its distinct id sets make a chance match
    unlikely (product of 1/n! over the sets <= chance; e.g. three different pairs, or one triple and a pair)."""
    groups = {}
    for owner, template, ids, in_true in records:
        groups.setdefault((owner, template), []).append((ids, in_true))
    out = []
    for (owner, template), seen in sorted(groups.items()):
        if not all(in_true for _, in_true in seen):
            continue
        sets = {frozenset(ids) for ids, _ in seen}
        p = 1.0
        for ids in sets:
            p /= math.factorial(len(ids))
        if p <= chance:
            out.append(("order", owner, "{} listed in true-id order {} times ({} distinct sets, chance {:.3f})".format(
                template, len(seen), len(sets), p)))
    return out


def _remembered(realism, eid, field, value, need):
    """True if Realism shows `value` from memory by design (v2.8 'knowledge that lapses shows the remembered value'):
    the robot learned this same value at the field's tier or better, and its knowledge lapsed only because the thing
    moved unseen (the placement signature changed), not because the value changed."""
    stale = getattr(realism.knowledge, "stale", None)
    old = stale(eid, field) if stale is not None else None
    return bool(old) and old[0] >= need and _same(value, old[1])


def check_obs(env, obs, leaks, where, start_ids=None, order=None):
    from house_sim.kernel.realism import TIER_NEED, MEASURE
    state = env.world.s
    realism = env.realism
    entities = state["entities"]
    known_ids = _known_ids(state, start_ids, realism)
    plain = set(state["rooms"]) | set(state.get("people", {})) | set(state.get("pets", {})) | {"robot"}
    if obs.get("dark"):
        # in the dark the people and pets of the robot's room are shapes: their ids would say who is who
        room = state["robot"].get("room")
        shapes = {a for g in ("people", "pets") for a, rec in (state.get(g) or {}).items() if rec.get("location") == room}
        plain -= shapes
        known_ids = known_ids | shapes
    observer_keys = getattr(env, "_observer_keys", {})

    def scan(value, path):
        if isinstance(value, str):
            for t in TOKEN.findall(value):
                if t in known_ids and t not in plain and not _descriptive_word(path, t):
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
    for key in sorted(k for k in obs if k not in KERNEL_OBS_KEYS):
        _report_order(id_order_findings(realism, obs[key], key), observer_keys.get(key, "kernel:" + key), "obs", leaks, order, where)
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
                if _remembered(realism, eid, field, value, need):
                    continue     # remembered from when it was learned (it moved unseen since): shown by design
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


def check_message(env, action, target_eid, message, leaks, start_ids=None, order=None):
    """start_ids: entity ids at the start of the step (ids of things destroyed during it are leaks too); order: an
    accumulator for id-order findings (see _report_order; None reports each true-id-ordered container at once)."""
    from house_sim.kernel.plugins import OWNERS
    from house_sim.kernel.realism import MEASURE
    state = env.world.s
    entities = state["entities"]
    known_ids = _known_ids(state, start_ids, env.realism)
    plain = set(state["rooms"]) | set(state.get("people", {})) | set(state.get("pets", {})) | {"robot"}
    owner = OWNERS.get(("action", action), "?")
    for t in TOKEN.findall(message or ""):
        if t in known_ids and t not in plain:
            leaks.append(("id", "action:" + owner, "{} message: {}".format(action, t)))
    try:
        data = json.loads(message)
    except (TypeError, ValueError):
        data = None
    if isinstance(data, (dict, list)):
        _report_order(id_order_findings(env.realism, data), "action:" + owner, action + " message JSON", leaks, order)
    if target_eid and target_eid in entities:
        rec = entities[target_eid]
        realism = env.realism
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
    order = []        # id-order findings, judged across the whole run (order_leaks)
    obs = env.observe()
    check_obs(env, obs, leaks, "step0", order=order)
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
        start_ids = set(env.world.s["entities"])
        try:
            result = env.step(action, target, destination, **args)
        except Exception as exc:  # noqa: BLE001
            errors.append("{} {}: {}: {}".format(action, target, type(exc).__name__, str(exc)[:200]))
            try:
                env.world.rollback()
            except Exception:  # noqa: BLE001
                pass
            continue
        check_message(env, action, target_eid, result.get("message"), leaks, start_ids=start_ids, order=order)
        obs = env.observe()
        check_obs(env, obs, leaks, "step{}".format(i + 1), start_ids=start_ids, order=order)
    return {"seed": seed, "leaks": leaks, "errors": errors, "steps": steps, "order": [list(r[:2]) + [list(r[2]), r[3]] for r in order]}


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
    seeds_of = defaultdict(set)
    for r in results:
        for owner, template, ids, in_true in r.get("order", []):
            seeds_of[(owner, template)].add(r["seed"])
    ordered = order_leaks([(o, t, tuple(ids), v) for r in results for o, t, ids, v in r.get("order", [])])
    # run-level findings: one entry per container, credited to the seeds where it was seen
    judged = [{"seed": ",".join(str(s) for s in sorted(seeds_of[(owner, detail.split(" listed in", 1)[0])])),
               "leaks": [(kind, owner, detail)], "errors": []} for kind, owner, detail in ordered]
    for r in results + judged:
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
        Path(out).write_text(json.dumps({"leaks": [dict(seed=r["seed"], kind=k, owner=o, detail=d) for r in results + judged for k, o, d in r["leaks"]],
                                         "errors": errors}, indent=1), encoding="utf-8", newline="\n")
    return 1 if by_kind or errors else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
