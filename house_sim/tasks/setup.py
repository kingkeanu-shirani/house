"""Declarative scenario setup operations, applied to a generated house before the episode.

Each op is a dict with "op". Strings may reference earlier bindings as "{name}".

  room        {"op":"room","var":"k","type":"kitchen"}                bind a room id of a type
  find        {"op":"find","var":"s","type":"sink","room":"{k}","where":{...},"pick":"random|first|all"}
  spawn       {"op":"spawn","var":"p","type":"plate","on":"{t}"|"in":"{c}"|"floor":"{room}"|"hang":"{h}"|
               "furniture":"{room}"|"mount":"{room}"|"held_by":"{pid}"|"robot_hand":"right",
               "props":{...},"count":3,"shelf":0,"spos":[x,y],"near":[c,r],"eid":"id","wall":true,"required":true}
  set         {"op":"set","target":"{p}","props":{...}}               shallow update of entity fields
  set_path    {"op":"set_path","path":"utilities.power","value":false} any config path
  move        {"op":"move","target":"{p}","to":"{c}","relation":"in","shelf":0}
  remove      {"op":"remove","target":"{p}"}                          also removes descendants
  person      {"op":"person","var":"m","id":"resident_1","room":"{k}","props":{...}}  create or update
  remove_people {"op":"remove_people","except":["resident_1"]}
  pet         {"op":"pet","var":"d","id":"dog_1","room":"{k}","props":{...}}
  remove_pets {"op":"remove_pets","except":[]}
  robot       {"op":"robot","room":"{k}","near":"{s}","props":{...},"hold":{"right":"{p}"}}
  event       {"op":"event","at_s":30,"changes":{...}} or {"effect":"start_rain","args":{...}}
  user_sim    {"op":"user_sim","answers":{...},"by_person":{"resident_1":{...}},"intent":{...}}
  weather / utilities / household / clock / metadata  {"op":"weather","condition":"rain",...}  (shallow update)
  memory      {"op":"memory","key":"preferred_location:mug","value":"cabinet_1"}
  approvals   {"op":"approvals","add":["touch:box_1"]}
  lights      {"op":"lights","room":"{k}","on":false}
  choose      {"op":"choose","var":"x","from":[...]}; randint {"op":"randint","var":"n","min":2,"max":5}
  uniform     {"op":"uniform","var":"t","min":0.2,"max":0.9,"round":2}; bind {"op":"bind","var":"x","value":...}
  helper      {"op":"helper","name":"domain.helper","args":{...}}  -> registered @scenario_helper, returns bindings
  assert      {"op":"assert","pred":{...}}                          sanity check on the built config
"""
import copy
import random

from ..generator.place import Placer
from ..kernel import geometry
from ..kernel.errors import SpecError
from ..kernel.plugins import SCENARIO_HELPERS
from ..kernel.predicates import check, root_room, set_path
from .spec import substitute


class SetupCtx:
    def __init__(self, config, registry, rng, seed, spec):
        self.config, self.reg, self.rng, self.seed, self.spec = config, registry, rng, seed, spec
        self.placer = Placer(config, registry, rng)
        self.bindings = {"seed": seed}

    def state(self):
        return self.placer.state()

    def bind(self, name, value):
        self.bindings[name] = value
        return value


def _room_ids(ctx, room_type):
    return ctx.placer.rooms(room_type)


def _resolve_room(ctx, value):
    if value in ctx.config["rooms"]:
        return value
    rooms = _room_ids(ctx, value)
    if not rooms:
        raise SpecError("No room of type " + str(value))
    return ctx.rng.choice(rooms)


def op_room(ctx, op):
    rooms = _room_ids(ctx, op["type"])
    if not rooms:
        if op.get("required", True):
            raise SpecError("House has no room of type {} (add it to house.require)".format(op["type"]))
        return ctx.bind(op["var"], None)
    pick = op.get("pick", "random")
    if pick == "all":
        return ctx.bind(op["var"], rooms)
    return ctx.bind(op["var"], rooms[0] if pick == "first" else ctx.rng.choice(rooms))


def op_find(ctx, op):
    room = op.get("room")
    if room and room not in ctx.config["rooms"]:
        room = _resolve_room(ctx, room)
    found = ctx.placer.find(op.get("type"), room=room, where=op.get("where"))
    if op.get("room_type"):
        state = ctx.state()
        found = [e for e in found if ctx.config["rooms"].get(root_room(state, e), {}).get("type") == op["room_type"]]
    if op.get("within"):
        state = ctx.state()
        from ..kernel.predicates import is_within
        found = [e for e in found if is_within(state, e, op["within"])]
    if not found:
        if op.get("required", True):
            raise SpecError("find: nothing matches {}".format({k: v for k, v in op.items() if k != "op"}))
        return ctx.bind(op["var"], None if op.get("pick") != "all" else [])
    pick = op.get("pick", "random")
    if pick == "all":
        return ctx.bind(op["var"], found)
    return ctx.bind(op["var"], found[0] if pick == "first" else ctx.rng.choice(found))


def op_spawn(ctx, op):
    count = int(op.get("count", 1))
    props = copy.deepcopy(op.get("props", {}))
    created = []
    for i in range(count):
        eid = op.get("eid") if count == 1 else (op["eid"] + "_" + str(i + 1) if op.get("eid") else None)
        p = ctx.placer
        type_name = op["type"]
        if "on" in op:
            new = p.on(op["on"], type_name, eid=eid, spos=op.get("spos") if count == 1 else None, **props)
        elif "in" in op:
            new = p.inside(op["in"], type_name, eid=eid, shelf=op.get("shelf"), **props)
        elif "hang" in op:
            new = p.hang(op["hang"], type_name, eid=eid, **props)
        elif "floor" in op:
            new = p.floor_item(_resolve_room(ctx, op["floor"]), type_name, eid=eid, near=op.get("near"),
                               cell=op.get("cell") if count == 1 else None, keep_access=not op.get("obstruct"), **props)
        elif "furniture" in op:
            new = p.furniture(_resolve_room(ctx, op["furniture"]), type_name, eid=eid, wall=op.get("wall", True), near=op.get("near"), **props)
        elif "mount" in op:
            new = p.mount(_resolve_room(ctx, op["mount"]), type_name, eid=eid, height=op.get("height"), cell=op.get("cell"), **props)
        elif "under" in op or "behind" in op:
            parent = op.get("under") or op.get("behind")
            relation = "under" if "under" in op else "behind"
            new = p.make(type_name, parent, eid=eid, **props)
            ctx.config["entities"][new]["relation"] = relation
        elif "held_by" in op:
            new = p.make(type_name, op["held_by"], eid=eid, **props)
            ctx.config["entities"][new]["relation"] = "held"
        elif "robot_hand" in op:
            new = p.make(type_name, "robot", eid=eid, **props)
            record = ctx.config["entities"][new]
            record["relation"], record["held_hand"] = "held", op["robot_hand"]
            robot = ctx.config.setdefault("robot", {})
            hands = robot.setdefault("hands", {"left": None, "right": None})
            for hand in (("left", "right") if op["robot_hand"] == "both" else (op["robot_hand"],)):
                hands[hand] = new
        elif "at" in op:
            new = p.make(type_name, op["at"], eid=eid, **props)
        else:
            raise SpecError("spawn needs a placement (on/in/hang/floor/furniture/mount/under/behind/held_by/robot_hand/at)")
        if new is None:
            if op.get("required", True):
                raise SpecError("spawn: no room for {} ({})".format(type_name, {k: v for k, v in op.items() if k not in ("op", "props")}))
            continue
        created.append(new)
    if "var" in op:
        if count == 1:
            ctx.bind(op["var"], created[0] if created else None)
        else:
            ctx.bind(op["var"], created)
            for i, eid in enumerate(created, 1):
                ctx.bind("{}_{}".format(op["var"], i), eid)
    return created


def op_set(ctx, op):
    targets = op["target"] if isinstance(op["target"], list) else [op["target"]]
    for target in targets:
        if target in ctx.config["entities"]:
            record = ctx.config["entities"][target]
        elif target in ctx.config["rooms"]:
            record = ctx.config["rooms"][target]
        elif target in ctx.config.get("people", {}):
            record = ctx.config["people"][target]
        elif target in ctx.config.get("pets", {}):
            record = ctx.config["pets"][target]
        else:
            raise SpecError("set: unknown target " + str(target))
        for key, value in op["props"].items():
            if isinstance(value, dict) and isinstance(record.get(key), dict) and op.get("deep", True) and key not in ("soil",):
                record[key].update(copy.deepcopy(value))
            else:
                record[key] = copy.deepcopy(value)


def op_set_path(ctx, op):
    parts = op["path"].split(".")
    node = ctx.config
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = copy.deepcopy(op["value"])


def op_move(ctx, op):
    record = ctx.config["entities"][op["target"]]
    record["location"] = op["to"]
    for key in ("spos", "pos", "shelf", "held_hand"):
        record.pop(key, None)
    if op["to"] in ctx.config["rooms"]:
        record["relation"] = "on"
        record["pos"] = op.get("cell") or geometry.free_floor_cell(ctx.state(), op["to"], near=op.get("near"), ignore=(op["target"],),
                                                                  record=record, keep_access=not op.get("obstruct"))
        if not record["pos"]:
            raise SpecError("move: no free floor cell for {} in {}".format(op["target"], op["to"]))
    else:
        parent = ctx.config["entities"].get(op["to"]) or {}
        record["relation"] = op.get("relation") or ("in" if parent.get("container") or parent.get("openable") else "on")
        if record["relation"] == "on" and parent:
            spot = geometry.surface_spot(ctx.state(), op["to"], op["target"], want=op.get("spos"))
            if spot is None:
                raise SpecError("move: no space on " + op["to"])
            record["spos"] = spot
        if record["relation"] == "in" and parent.get("shelf_heights"):
            record["shelf"] = int(op.get("shelf", 0))


def op_remove(ctx, op):
    targets = op["target"] if isinstance(op["target"], list) else [op["target"]]
    for target in targets:
        doomed = [target]
        changed = True
        while changed:
            changed = False
            for eid, record in ctx.config["entities"].items():
                if record.get("location") in doomed and eid not in doomed:
                    doomed.append(eid)
                    changed = True
        for eid in doomed:
            ctx.config["entities"].pop(eid, None)


def op_person(ctx, op):
    people = ctx.config.setdefault("people", {})
    pid = op.get("id") or ctx.placer.new_id(op.get("role", "person"))
    record = people.setdefault(pid, {"label": op.get("label", pid.replace("_", " ")), "role": op.get("role", "adult"),
                                    "authority": 2 if op.get("role", "adult") == "adult" else (1 if op.get("role") == "elderly" else 0),
                                    "consent": True, "awake": True, "mood": "neutral", "permissions": [], "preferences": {}})
    if "role" in op:
        record["role"] = op["role"]
    record.update(copy.deepcopy(op.get("props", {})))
    if "room" in op:
        record["location"] = _resolve_room(ctx, op["room"])
        record["pos"] = None
        record["pos"] = op.get("cell") or _agent_cell(ctx, record["location"], op, pid)
        if op.get("cell"):
            record["_placed"] = True     # deliberate position: settling logic leaves it alone
    elif "location" not in record:
        record["location"] = ctx.config["start_room"]
        record["pos"] = None
        record["pos"] = _agent_cell(ctx, record["location"], op, pid)
    if "var" in op:
        ctx.bind(op["var"], pid)
    return pid


def _agent_cell(ctx, rid, op, aid):
    """A free cell for a person/pet that blocks no doorway or route (Placer.agent_cell); `near` (a cell or an entity
    id) prefers cells close to it. Raises SpecError when the room has no such cell."""
    near = op.get("near")
    if isinstance(near, str):
        _, cells = geometry.anchor(dict(ctx.state(), robot={"room": None, "hands": {}, "pos": None}), near)
        near = cells[0] if cells else None
    posture = (op.get("props") or {}).get("posture") or op.get("posture")
    ring = 1 if posture in ("sleeping", "lying", "fallen") else 0
    cell = ctx.placer.agent_cell(rid, near=tuple(near) if near else None, ring=ring)
    if cell is None and ring:
        cell = ctx.placer.agent_cell(rid, near=tuple(near) if near else None)
    if cell is None:
        raise SpecError("no free cell for {} in {}".format(aid, rid))
    return cell


def op_remove_people(ctx, op):
    keep = set(op.get("except", []))
    for pid in list(ctx.config.get("people", {})):
        if pid not in keep:
            del ctx.config["people"][pid]
            for eid in [e for e, r in ctx.config["entities"].items() if r.get("location") == pid]:
                del ctx.config["entities"][eid]


def op_pet(ctx, op):
    pets = ctx.config.setdefault("pets", {})
    pid = op.get("id") or ctx.placer.new_id(op.get("species", "pet"))
    record = pets.setdefault(pid, {"label": op.get("label", pid.replace("_", " ")), "species": op.get("species", "dog")})
    if "species" in op:
        record["species"] = op["species"]
    record.update(copy.deepcopy(op.get("props", {})))
    if "room" in op:
        record["location"] = _resolve_room(ctx, op["room"])
        record["pos"] = None
        record["pos"] = op.get("cell") or _agent_cell(ctx, record["location"], op, pid)
        if op.get("cell"):
            record["_placed"] = True     # deliberate position: settling logic leaves it alone
    elif "location" not in record:
        record["location"] = ctx.config["start_room"]
        record["pos"] = None
        record["pos"] = _agent_cell(ctx, record["location"], op, pid)
    if "var" in op:
        ctx.bind(op["var"], pid)
    return pid


def op_remove_pets(ctx, op):
    keep = set(op.get("except", []))
    for pid in list(ctx.config.get("pets", {})):
        if pid not in keep:
            del ctx.config["pets"][pid]
            for eid in [e for e, r in ctx.config["entities"].items() if r.get("location") == pid]:
                del ctx.config["entities"][eid]


def op_robot(ctx, op):
    robot = ctx.config.setdefault("robot", {})
    if "room" in op:
        robot["room"] = _resolve_room(ctx, op["room"])
        robot["pos"] = None
        robot["docked"] = False
    if "near" in op:
        state = ctx.state()
        room, cells = geometry.anchor(dict(state, robot={"room": robot.get("room"), "hands": {}, "pos": None}), op["near"])
        if room is None:
            raise SpecError("robot.near: cannot locate " + op["near"])
        robot["room"] = room
        if cells:
            options = sorted(geometry.near_cells(cells, ctx.config["rooms"][room]) - set(cells))
            blocked = geometry.blocked_cells(state, room)
            options = [c for c in options if c not in blocked]
            doors = [tuple(c["cells"][room]) for _, c in sorted(ctx.config.get("connections", {}).items()) if room in c.get("cells", {})]
            reachable = [c for c in options if not doors or any(geometry.find_path(state, room, d, [c]) is not None for d in doors[:2])]
            pick = (reachable or options)
            robot["pos"] = list(pick[0]) if pick else None
        robot["docked"] = False
    if "cell" in op:
        robot["pos"] = list(op["cell"])
    robot.update(copy.deepcopy(op.get("props", {})))
    for hand, eid in op.get("hold", {}).items():
        robot.setdefault("hands", {"left": None, "right": None})[hand] = eid
        record = ctx.config["entities"][eid]
        record.update(location="robot", relation="held", held_hand=hand)
        for key in ("spos", "pos", "shelf"):
            record.pop(key, None)
    if robot.get("room") and not robot.get("pos"):
        state = ctx.state()
        robot["pos"] = geometry.free_floor_cell(state, robot["room"], keep_access=True) or \
            geometry.free_floor_cell(state, robot["room"], clearance=False)
        if not robot["pos"]:
            raise SpecError("robot: no free cell in " + robot["room"])


def op_event(ctx, op):
    event = {k: copy.deepcopy(v) for k, v in op.items() if k != "op"}
    ctx.config.setdefault("events", []).append(event)


def op_user_sim(ctx, op):
    sim = ctx.config.setdefault("user_simulator", {})
    for key in ("answers", "by_person", "intent"):
        if key in op:
            target = sim.setdefault(key, {})
            if key == "by_person":
                for pid, answers in op[key].items():
                    target.setdefault(pid, {}).update(copy.deepcopy(answers))
            else:
                target.update(copy.deepcopy(op[key]))
    for key, value in op.items():
        if key not in ("op", "answers", "by_person", "intent"):
            sim[key] = copy.deepcopy(value)


def _section(name):
    def apply(ctx, op):
        ctx.config.setdefault(name, {}).update({k: copy.deepcopy(v) for k, v in op.items() if k != "op"})
    return apply


def op_memory(ctx, op):
    ctx.config.setdefault("memory", {})[op["key"]] = {"value": copy.deepcopy(op["value"]), "time_s": -1, "scope": op.get("scope", "household")}


def op_approvals(ctx, op):
    approvals = ctx.config.setdefault("approvals", [])
    approvals.extend(a for a in op.get("add", []) if a not in approvals)


def op_lights(ctx, op):
    room = _resolve_room(ctx, op["room"])
    state = ctx.state()
    for eid, record in ctx.config["entities"].items():
        if root_room(state, eid) == room and record.get("category") in ("light", "switch") and not record.get("battery_powered"):
            if record.get("category") == "switch" and record.get("location") != room:
                continue
            record["on"] = bool(op["on"])
        if record.get("category") == "switch" and any(ctx.config["entities"].get(c, {}).get("location") == room for c in record.get("controls", [])):
            record["on"] = bool(op["on"])
            for controlled in record.get("controls", []):
                if controlled in ctx.config["entities"]:
                    ctx.config["entities"][controlled]["on"] = bool(op["on"])
    if op.get("daylight") is False:
        for eid, record in ctx.config["entities"].items():
            if record.get("category") == "window" and record.get("location") == room:
                record["covered"] = 1.0


def op_choose(ctx, op):
    options = op["from"]
    if not options:
        raise SpecError("choose: empty option list")
    return ctx.bind(op["var"], copy.deepcopy(ctx.rng.choice(options)))


def op_randint(ctx, op):
    return ctx.bind(op["var"], ctx.rng.randint(int(op["min"]), int(op["max"])))


def op_uniform(ctx, op):
    return ctx.bind(op["var"], round(ctx.rng.uniform(float(op["min"]), float(op["max"])), int(op.get("round", 3))))


def op_bind(ctx, op):
    return ctx.bind(op["var"], copy.deepcopy(op["value"]))


def op_helper(ctx, op):
    name = op["name"]
    if name not in SCENARIO_HELPERS:
        raise SpecError("Unknown scenario helper " + name)
    result = SCENARIO_HELPERS[name](ctx, **op.get("args", {})) or {}
    if not isinstance(result, dict):
        raise SpecError("Scenario helper {} must return a dict of bindings".format(name))
    prefix = op.get("prefix", "")
    for key, value in result.items():
        ctx.bind(prefix + key, value)
    if "var" in op and "result" in result:
        ctx.bind(op["var"], result["result"])
    return result


def op_assert(ctx, op):
    if not check(ctx.config, op["pred"]):
        raise SpecError("setup assertion failed: {}".format(op["pred"]))


OPS = {"room": op_room, "find": op_find, "spawn": op_spawn, "set": op_set, "set_path": op_set_path, "move": op_move,
       "remove": op_remove, "person": op_person, "remove_people": op_remove_people, "pet": op_pet,
       "remove_pets": op_remove_pets, "robot": op_robot, "event": op_event, "user_sim": op_user_sim,
       "weather": _section("weather"), "utilities": _section("utilities"), "household": _section("household"),
       "clock": _section("clock"), "metadata": _section("metadata"), "memory": op_memory, "approvals": op_approvals,
       "lights": op_lights, "choose": op_choose, "randint": op_randint, "uniform": op_uniform, "bind": op_bind,
       "helper": op_helper, "assert": op_assert}


def apply_setup(config, ops, registry, seed, spec, bindings=None):
    rng = random.Random("setup:{}:{}".format(spec.get("catalog_id", "custom"), seed))
    ctx = SetupCtx(config, registry, rng, seed, spec)
    if bindings:
        ctx.bindings.update(bindings)
    for index, raw in enumerate(ops):
        op = substitute(raw, ctx.bindings)
        kind = op.get("op")
        if kind not in OPS:
            raise SpecError("Unknown setup op {!r} at index {}".format(kind, index))
        try:
            OPS[kind](ctx, op)
        except SpecError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise SpecError("setup op {} ({}) failed: {}: {}".format(index, kind, type(exc).__name__, exc))
    return ctx.bindings
