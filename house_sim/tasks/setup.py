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
  robot       {"op":"robot","room":"{k}","near":"{s}","cell":[c,r],"profile":"humanoid_compact",
               "profile_overrides":{...},"sensors":{"camera_on":false,"mic_on":true,"privacy_policy":{...}},
               "props":{...},"hold":{"right":"{p}"}}      the body (kernel v3.0): profile, then overrides, then the
                                                    sensor switches, then props (robot fields, e.g. "max_hand_kg":
                                                    "{cap}" after a uniform op); normalize materializes the rest
  robot_fault {"op":"robot_fault","kind":"arm_weak","part":"left","severity":0.5,"latent":true,"at_s":0,"until_s":600}
               or {"op":"robot_fault","var":"f","choose":["camera_dirty","arm_weak:left","none"]}
                                                    a robot fault at reset (at_s 0 / absent) or at at_s, ended at
                                                    until_s; choose draws one (binds f and f_id)
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

Physics (kernel v3.0, docs/design/TIER1_PHYSICS.md 7.5; docs/PHYSICS.md):

  phys        {"op":"phys","target":"{x}","props":{...}}            validated physical overrides (PHYS_FIELDS, ranges
                                                                    checked): materials, strength thresholds, ratings,
                                                                    stability geometry, liquids, heat, friction, feet
  load        {"op":"load","target":"{shelf}","shelf":2,"kg":18 | "fraction":0.8,"items":"books|cans|boxes|towels|auto",
               "var":"v"}                                           fills one slot with filler items to exactly kg (or
                                                                    a fraction of its nominal rating), each placed like
                                                                    spawn (fit and capacity respected); binds the ids
  edge        {"op":"edge","target":"{glass}","on":"{table}","margin_m":0.02,"side":"front|back|left|right"}
                                                                    puts an item on a surface that far from one edge
                                                                    (negative: it overhangs, down to 30 % of its
                                                                    footprint; it stays statically stable)
  stage       {"op":"stage","target":"{shelf}","state":"overloaded","creep_s":600}
                                                                    no scene allowance for the support's slots (the
                                                                    overload law uses the true rating); with creep_s a
                                                                    slot still over its rating then collapses
  check_physics {"op":"check_physics","strict":true}               SpecError for a physically impossible staged scene
                                                                    (strict false: warnings in the bindings)

After the ops the same physics check runs as a lint over what the setup created or changed (and the supports under
it): an error (SpecError) for a spec with "schema": 3 or more, otherwise warnings recorded in the bindings under
"_physics_warnings" (the verifier copies them into its trial record). An entity carrying _phys_staged is exempt.

Geometric camera (kernel v3.0, docs/design/TIER1_CAMERA.md 10.5): only in specs that declare "camera": "geometric"
(SpecError otherwise); validated always, applied only when the run's camera is geometric (a run forced to the room
camera skips them):

  camera      {"op":"camera","face":90 | "north" | "{thing}" | [c, r],"pan":0,"tilt":-10,"track":["{pid}"],"hw":{...}}
                                                                    the robot's starting body yaw (grid degrees, a
                                                                    compass word, or toward a target from its cell),
                                                                    head pan / tilt, people and pets the head keeps in
                                                                    frame and camera hardware overrides (keys of
                                                                    camera.HW); sensor switches belong to the robot op
  view        {"op":"view","target":"{x}","props":{...}}           merges validated view properties into the record's
                                                                    "view" (porosity, clear_below_m, behind_gap_m,
                                                                    opening, interior_light, faces, text_m, glossy,
                                                                    camera, beam, height_m, ...)
  track_view  {"op":"track_view","ids":["{pid}","{toy}"]}          camera_log.sightings follows these too
  view_watch  {"op":"view_watch","name":"n","viewer":{"entity":"{cam}"} | {"person":"{pid}","attention":true} |
               {"room":"{r}","cell":[c,r],"z":1.5,"yaw":180,"hfov":78,"range_m":6},"targets":["{x}","robot"],
               "every_s":60}                                        what another viewer sees, in camera_log.watch

  spawn ... "in" gains "row": "front" | "back" (both camera modes): inner_spos puts the item in the front row at the
  opening or the back row against the back wall of its shelf, at the leftmost free place (sight.row_spos); give every
  item of a shelf whose arrangement matters a row.
"""
import copy
import math
import random

from ..generator.place import Placer
from ..kernel import camera as camera_mod
from ..kernel import geometry, sight
from ..kernel.errors import SpecError
from ..kernel.plugins import SCENARIO_HELPERS
from ..kernel.predicates import check, root_room, set_path
from .spec import substitute

# what the physics lint compares to tell the entities a setup created or changed from the generated house
_PHYS_SIG = ("location", "relation", "spos", "pos", "shelf", "orientation", "liquid_l", "capacity_l", "mass_kg",
             "size_m", "temperature_c", "inner_size_m", "_phys_staged")


def _phys_sig(record):
    return tuple(record.get(key) for key in _PHYS_SIG)


class SetupCtx:
    def __init__(self, config, registry, rng, seed, spec, camera=None, perception=None):
        self.config, self.reg, self.rng, self.seed, self.spec = config, registry, rng, seed, spec
        self.placer = Placer(config, registry, rng)
        self.bindings = {"seed": seed}
        self.phys_base = {eid: _phys_sig(rec) for eid, rec in config.get("entities", {}).items()}
        self.physics_warnings = []
        # the run's camera mode (TIER1_INTEGRATION 1.13: the run's choice, then the spec's "camera", then the default)
        own = spec if isinstance(spec, dict) else {}
        try:
            self.camera = camera_mod.resolve_mode(camera, own, perception or own.get("perception") or "oracle")
        except ValueError as exc:
            raise SpecError(str(exc))

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
    if "row" in op:
        _place_in_row(ctx, op, created)
    if "var" in op:
        if count == 1:
            ctx.bind(op["var"], created[0] if created else None)
        else:
            ctx.bind(op["var"], created)
            for i, eid in enumerate(created, 1):
                ctx.bind("{}_{}".format(op["var"], i), eid)
    return created


def _place_in_row(ctx, op, created):
    """spawn "row": "front" | "back" with "in" (kernel v3.0 camera, TIER1_CAMERA 5.4e / 10.5): each new item's
    inner_spos puts it in that row of its shelf (sight.row_spos), in both camera modes (inert in room mode)."""
    row = op.get("row")
    if "in" not in op:
        raise SpecError("spawn: row needs an \"in\" placement")
    if row not in ("front", "back"):
        raise SpecError("spawn: row must be front or back")
    for new in created:
        spos = sight.row_spos(ctx.state(), op["in"], new, row)
        if spos is None:
            raise SpecError("spawn: no room in the {} row of {} for {}".format(row, op["in"], new))
        ctx.config["entities"][new]["inner_spos"] = spos


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


ROBOT_OP_KEYS = ("op", "room", "near", "cell", "profile", "profile_overrides", "sensors", "props", "hold")
ROBOT_SENSOR_KEYS = ("camera_on", "mic_on", "privacy_policy")


def _robot_body(ctx, robot, op):
    """The body parts of the robot op (TIER1_INTEGRATION 1.13, TIER1_EMBODIMENT 5.8), in this order: the profile
    (robot.body_profile), then profile_overrides (deep-merged over it for this episode), then the sensor switches
    (body.set_sensors' fields); the op's props come after. normalize materializes the body from them (body.apply_profile:
    profile values first, explicit robot fields win)."""
    from ..kernel import body
    if "profile" in op:
        if op["profile"] not in body.profile_names():
            raise SpecError("robot: unknown body profile {!r} (known: {})".format(
                op["profile"], ", ".join(body.profile_names())))
        robot["body_profile"] = op["profile"]
    if "profile_overrides" in op:
        overrides = op["profile_overrides"]
        if not isinstance(overrides, dict) or not overrides:
            raise SpecError("robot: profile_overrides must be a non-empty object")
        robot["profile_overrides"] = copy.deepcopy(overrides)
    if "profile" in op or "profile_overrides" in op:
        try:
            body.profile({"robot": robot})          # the merged profile must still be complete and consistent
        except ValueError as exc:
            raise SpecError("robot: {}".format(exc))
    if "sensors" in op:
        sensors = op["sensors"]
        if not isinstance(sensors, dict) or not sensors or set(sensors) - set(ROBOT_SENSOR_KEYS):
            raise SpecError("robot: sensors must be an object with {}".format(", ".join(ROBOT_SENSOR_KEYS)))
        for key in ("camera_on", "mic_on"):
            if key in sensors:
                if not isinstance(sensors[key], bool):
                    raise SpecError("robot: sensors.{} must be true or false".format(key))
                robot[key] = sensors[key]
        if "privacy_policy" in sensors:
            policy = sensors["privacy_policy"]
            try:
                robot["privacy_policy"] = body.check_policy(policy) if policy else None
            except ValueError as exc:
                raise SpecError("robot: sensors.{}".format(exc))


def op_robot(ctx, op):
    unknown = sorted(set(op) - set(ROBOT_OP_KEYS))
    if unknown:
        raise SpecError("robot: unknown keys {} (allowed: {})".format(", ".join(unknown), ", ".join(ROBOT_OP_KEYS[1:])))
    robot = ctx.config.setdefault("robot", {})
    _robot_body(ctx, robot, op)
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
        if _arm_out(robot, hand):
            raise SpecError("robot: hold: the {} arm has failed (robot_fault arm_failed)".format(hand))
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


def _arm_out(robot, hand):
    """A hand whose arm the robot config has put out of order (a setup arm_failed fault, arm_health 0, arms_ok
    false)."""
    record = (robot.get("fault_state") or {}).get("arm_failed_" + str(hand))
    if isinstance(record, dict) and record.get("cleared_s") is None:
        return True
    try:
        return float((robot.get("arm_health") or {}).get(hand, 1.0)) <= 0 or (robot.get("arms_ok") or {}).get(hand) is False
    except (TypeError, ValueError):
        return False


ROBOT_FAULT_KEYS = ("op", "kind", "part", "severity", "latent", "at_s", "until_s", "choose", "var")
# consequences a fault has at onset that a reset-time fault (written into the config) must carry itself
FAULT_IMPLIES = {"water_ingress": ("camera_failed", "actuator_failed")}


def _fault_option(option):
    """(kind, part) of a robot_fault choice: "kind", "kind:part" or "none" -> (None, None)."""
    if not isinstance(option, str) or not option.strip():
        raise SpecError("robot_fault: choose options are \"kind\", \"kind:part\" or \"none\"")
    if option == "none":
        return None, None
    kind, _, part = option.partition(":")
    return kind, part or None


def _fault_target(kind, part):
    """The fault id of kind / part, validated against the fault registry (body_faults.FAULT_KINDS)."""
    from ..kernel import body_faults
    if kind not in body_faults.FAULT_KINDS:
        raise SpecError("robot_fault: unknown fault kind {!r} (known: {})".format(
            kind, ", ".join(sorted(body_faults.FAULT_KINDS))))
    own = body_faults.FAULT_KINDS[kind]["part"]
    if own not in body_faults.SIDED_PARTS and part not in (None, own):
        raise SpecError("robot_fault: {} has no part {!r}".format(kind, part))
    try:
        return body_faults.fault_id(kind, part if own in body_faults.SIDED_PARTS else None)
    except ValueError as exc:
        raise SpecError("robot_fault: {}".format(exc))


def op_robot_fault(ctx, op):
    """A robot fault (TIER1_EMBODIMENT 5.8): {"kind", "part" (left / right for arm and gripper faults), "severity"
    (0-1, the kind's default when absent), "latent" (only for faults the robot can miss: arm_weak, gripper_slip,
    battery_degraded, speaker_failed), "at_s", "until_s"}, or "choose": ["camera_dirty", "arm_weak:left", "none", ...]
    drawn with the setup RNG. At reset (at_s absent or 0) it is written into the robot's fault_state and normalize
    applies it (water_ingress brings its camera_failed and actuator_failed with it); a later at_s schedules the event
    robot.fault. until_s ends it with a robot.clear_fault event (network_down: the network comes back on its own).
    var binds the choice ("none" when no fault was drawn) and var + "_id" its fault id (None)."""
    from ..kernel import body_faults
    unknown = sorted(set(op) - set(ROBOT_FAULT_KEYS))
    if unknown:
        raise SpecError("robot_fault: unknown keys {} (allowed: {})".format(", ".join(unknown),
                                                                            ", ".join(ROBOT_FAULT_KEYS[1:])))
    if "choose" in op:
        if "kind" in op or "part" in op:
            raise SpecError("robot_fault: give kind (and part) or choose, not both")
        options = op["choose"]
        if not isinstance(options, list) or not options:
            raise SpecError("robot_fault: choose must be a non-empty list")
        parsed = [_fault_option(o) for o in options]
        for kind, part in parsed:
            if kind is not None:
                _fault_target(kind, part)
        index = ctx.rng.randrange(len(options))
        label, (kind, part) = options[index], parsed[index]
    else:
        kind, part = op.get("kind"), op.get("part")
        if not isinstance(kind, str):
            raise SpecError("robot_fault: kind is required (or choose)")
        label = kind + (":" + part if isinstance(part, str) else "")
    fid = _fault_target(kind, part) if kind is not None else None
    if op.get("var"):
        ctx.bind(op["var"], label)
        ctx.bind(op["var"] + "_id", fid)
    severity, latent, at_s, until_s = op.get("severity"), op.get("latent", False), op.get("at_s"), op.get("until_s")
    if severity is not None and (not _is_number(severity) or not 0.0 <= severity <= 1.0):
        raise SpecError("robot_fault: severity must be a number from 0 to 1")
    if not isinstance(latent, bool):
        raise SpecError("robot_fault: latent must be true or false")
    if at_s is not None and (not _is_number(at_s) or at_s < 0):
        raise SpecError("robot_fault: at_s must be a number of seconds, at least 0")
    if until_s is not None and (not _is_number(until_s) or until_s <= float(at_s or 0.0)):
        raise SpecError("robot_fault: until_s must be a time after at_s")
    if kind is None:
        return
    spec = body_faults.FAULT_KINDS[kind]
    if latent and spec["detect"] != body_faults.LATENT:
        raise SpecError("robot_fault: {} cannot be latent (the robot notices it at once)".format(kind))
    sided = spec["part"] in body_faults.SIDED_PARTS
    args = {"kind": kind}
    if sided:
        args["part"] = part
    if severity is not None:
        args["severity"] = float(severity)
    if latent:
        args["latent"] = True
    if until_s is not None and kind == "network_down":
        args["until_s"] = float(until_s)
    robot = ctx.config.setdefault("robot", {})
    if not at_s:
        if kind == "arm_failed" and (robot.get("hands") or {}).get(part):
            raise SpecError("robot_fault: arm_failed on the {} hand, which holds {}".format(part, robot["hands"][part]))
        states = robot.setdefault("fault_state", {})
        states[fid] = dict(args, source="setup")
        for implied in FAULT_IMPLIES.get(kind, ()):
            states.setdefault(implied, {"kind": implied, "source": "water"})
    else:
        ctx.config.setdefault("events", []).append({"at_s": float(at_s), "effect": "robot.fault", "args": args})
    if until_s is not None and kind != "network_down":
        clear = {"kind": kind}
        if sided:
            clear["part"] = part
        ctx.config.setdefault("events", []).append({"at_s": float(until_s), "effect": "robot.clear_fault",
                                                    "args": clear})


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


# ----- physics (kernel v3.0) ------------------------------------------------------------------------------------------

def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _range(lo=None, hi=None, lo_open=False):
    def check(value):
        if not _is_number(value):
            return "must be a finite number"
        if lo is not None and (value < lo or (lo_open and value <= lo)):
            return "must be {} {}".format("above" if lo_open else "at least", lo)
        if hi is not None and value > hi:
            return "must be at most {}".format(hi)
        return None
    return check


def _boolean(value):
    return None if isinstance(value, bool) else "must be true or false"


def _text(value):
    return None if isinstance(value, str) and value.strip() else "must be a non-empty string"


def _material(value):
    from ..kernel.phys import props
    if not isinstance(value, str) or not props.class_known(value):
        return "must be a known material (data/physics/materials.json aliases)"
    return None


def _shape(value):
    from ..kernel.phys import props
    return None if value in props.SHAPES else "must be one of " + ", ".join(props.SHAPES)


def _opening(value):
    if _is_number(value) and value > 0:
        return None
    if isinstance(value, list) and len(value) == 2 and all(_is_number(v) and v > 0 for v in value):
        return None
    return "must be a positive diameter or [width, depth]"


def _secured(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, dict) and set(value) <= {"rating_n", "by"} and _range(0)(value.get("rating_n", 0)) is None:
        return None
    return "must be true/false or {\"rating_n\": N}"


def _pads(value):
    if isinstance(value, dict) and set(value) <= {"kind", "count", "legs", "adhered"} and \
            all(isinstance(value.get(k, 1), int) and value.get(k, 1) >= 0 for k in ("count", "legs")):
        return None
    return "must be {\"kind\", \"count\", \"legs\", \"adhered\"}"


_NONNEG = _range(0)
_POSITIVE = _range(0, lo_open=True)
_FRACTION = _range(0, 1)
_TEMP = _range(-273.15, 3000)
# the physical fields the phys op may set (TIER1_PHYSICS 4.8 and the record fields kernel/phys/props.py reads)
PHYS_FIELDS = {
    "material": _material, "fragile": _boolean, "brittle": _boolean, "rigid": _boolean, "soft": _boolean,
    "softness": _FRACTION, "drop_tolerance_m": _range(0, 50), "crush_kg": _NONNEG, "max_stack_load_kg": _NONNEG,
    "crush_force_n": _NONNEG, "crushable": _boolean, "max_load_kg": _NONNEG, "load_rating_kg": _NONNEG,
    "load_capacity_kg": _NONNEG, "shelf_load_kg": _NONNEG, "hook_rating_kg": _NONNEG, "hang_load_kg": _NONNEG,
    "handle_rating_kg": _NONNEG, "rated_kg": _NONNEG, "anchored": _boolean, "secured": _secured,
    "com_frac": _range(0, 1, lo_open=True), "base_m": _POSITIVE, "shape": _shape, "tilt_deg": _range(-90, 90),
    "tilt_dir_deg": _range(0, 360), "waterproof": _boolean, "absorb_l_per_kg": _NONNEG, "perforated": _boolean,
    "rim_m": _NONNEG, "mouth_frac": _range(0, 1, lo_open=True), "opening_m": _opening, "opening_width_m": _opening,
    "nest_group": _text, "nest_frac": _FRACTION, "compressible": _boolean, "floats": _boolean, "airtight": _boolean,
    "liquid_density": _POSITIVE, "heat_limit_c": _TEMP, "surface_heat_limit_c": _TEMP, "heat_rating_c": _TEMP,
    "heat_resistant": _boolean, "melt_c": _TEMP, "thermal_shock_c": _range(0, 2000), "thermal_owner": _text,
    "specific_heat": _POSITIVE, "friction_mu": _range(0, 2, lo_open=True), "foot_material": _text,
    "casters": _boolean, "wheeled": _boolean, "pads": _pads, "on_sliders": _boolean, "tempered": _boolean,
    "drag_cd": _POSITIVE, "wind_lift_mps": _POSITIVE, "cushion_m": _NONNEG, "cushion_j": _NONNEG,
    "two_handed": _boolean, "load_rating_text": _text, "phys_exact": _boolean}


def _targets(ctx, op, name):
    targets = op["target"] if isinstance(op["target"], list) else [op["target"]]
    for target in targets:
        if target not in ctx.config["entities"]:
            raise SpecError("{}: unknown entity {}".format(name, target))
    return targets


def op_phys(ctx, op):
    props = op.get("props")
    if not isinstance(props, dict) or not props:
        raise SpecError("phys: props must be a non-empty object")
    for key in sorted(props):
        check = PHYS_FIELDS.get(key)
        if check is None:
            raise SpecError("phys: {} is not a physical field (allowed: {})".format(key, ", ".join(sorted(PHYS_FIELDS))))
        problem = check(props[key])
        if problem:
            raise SpecError("phys: {} {}".format(key, problem))
    for target in _targets(ctx, op, "phys"):
        ctx.config["entities"][target].update(copy.deepcopy(props))


LOAD_ITEMS = ("books", "cans", "boxes", "towels")
LOAD_MIN_KG = 0.01           # a remainder lighter than this rides on the previous filler


def _load_slot(record, op):
    heights = record.get("shelf_heights")
    if op.get("shelf") is not None:
        if not heights:
            raise SpecError("load: {} has no shelves".format(op["target"]))
        k = op["shelf"]
        if not isinstance(k, int) or isinstance(k, bool) or not 0 <= k < len(heights):
            raise SpecError("load: shelf must be an index below {}".format(len(heights)))
        return ("shelf", k)
    slot = op.get("slot")
    if slot is None:
        slot = "in" if (record.get("container") and not record.get("surface")) or heights else "surface"
    if slot == "in" and heights:
        return ("shelf", 0)
    if slot not in ("surface", "in"):
        raise SpecError("load: slot must be surface or in (or give a shelf)")
    return (slot,)


def op_load(ctx, op):
    from ..kernel.phys import props as P
    entities = ctx.config["entities"]
    target = op.get("target")
    if target not in entities:
        raise SpecError("load: unknown support {}".format(target))
    record = entities[target]
    slot = _load_slot(record, op)
    if slot[0] == "surface" and not record.get("accepts_items"):
        raise SpecError("load: {} cannot hold items".format(target))
    if "kg" in op:
        kg = op["kg"]
    elif "fraction" in op:
        nominal = P.props_of(record)
        rating = nominal.shelf_rating_kg if slot[0] == "shelf" else nominal.rating_kg
        if not _is_number(rating) or not _is_number(op["fraction"]):
            raise SpecError("load: {} has no rating to take a fraction of; give kg".format(target))
        kg = op["fraction"] * rating
    else:
        raise SpecError("load: give kg or fraction")
    if not _is_number(kg) or kg <= 0:
        raise SpecError("load: the load must be a positive number of kg")
    kind = op.get("items", "auto")
    if kind == "auto":
        kind = "books" if slot[0] in ("shelf", "surface") else "cans"
    if kind not in LOAD_ITEMS:
        raise SpecError("load: items must be one of {} or auto".format(", ".join(LOAD_ITEMS)))
    filler = P.SUPPORTS["fillers"][kind]
    type_name = next((t for t in filler["types"] if ctx.reg.known(t)), "generic_item")
    unit = float(filler["props"]["mass_kg"])
    count = int(kg / unit + 1e-9)
    masses = [unit] * count
    rest = kg - unit * count
    if rest >= LOAD_MIN_KG or not masses:
        masses.append(rest)
    elif rest > 1e-9:
        masses[-1] += rest
    created = []
    for mass in masses:
        props = copy.deepcopy(dict(filler["props"]))
        props["mass_kg"] = round(mass, 4)
        if slot[0] == "surface":
            new = ctx.placer.on(target, type_name, **props)
        else:
            new = ctx.placer.inside(target, type_name, shelf=slot[1] if slot[0] == "shelf" else None, **props)
        if new is None:
            raise SpecError("load: no room on {} for more {} ({} placed, {} kg asked)".format(
                target, kind, len(created), round(kg, 3)))
        created.append(new)
    if "var" in op:
        ctx.bind(op["var"], created)
    return created


EDGE_SIDES = {"front": 0.0, "left": 90.0, "back": 180.0, "right": 270.0}
EDGE_OVERHANG_MAX = 0.3      # an edge placement may overhang by at most this fraction of the item's footprint
EDGE_STEP_M = 0.05


def op_edge(ctx, op):
    from ..kernel.phys import statics
    from ..kernel.sight import front_deg
    entities = ctx.config["entities"]
    item, on = op.get("target"), op.get("on")
    if item not in entities or on not in entities:
        raise SpecError("edge: unknown target or support")
    parent = entities[on]
    if not (parent.get("accepts_items") or parent.get("surface")):
        raise SpecError("edge: {} is not a surface".format(on))
    side = op.get("side", "front")
    if side not in EDGE_SIDES:
        raise SpecError("edge: side must be one of " + ", ".join(EDGE_SIDES))
    margin = op.get("margin_m", 0.0)
    if not _is_number(margin):
        raise SpecError("edge: margin_m must be a number")
    record = entities[item]
    for key in ("spos", "pos", "shelf", "held_hand", "held_grip"):
        record.pop(key, None)
    record["location"], record["relation"] = on, "on"
    state = ctx.state()
    sw, sd = [float(v) for v in (parent.get("surface_size_m") or (parent.get("size_m") or [0.5, 0.5])[:2])[:2]]
    iw, idp = geometry.footprint_m(record)
    if iw > sw + 1e-9 or idp > sd + 1e-9:
        raise SpecError("edge: {} is larger than {}".format(item, on))
    # the edge's outward normal in the surface frame (0: +x, 90: +y, 180: -x, 270: -y)
    normal = (front_deg(state, on) + EDGE_SIDES[side] - statics.world_yaw(state, on)) % 360.0
    index = int(round(normal / 90.0)) % 4
    extent = iw if index in (0, 2) else idp
    if margin < -EDGE_OVERHANG_MAX * extent - 1e-9:
        raise SpecError("edge: {} would overhang by more than {:.0%} of its footprint".format(item, EDGE_OVERHANG_MAX))
    others = [(entities[o]["spos"], geometry.footprint_m(entities[o])) for o in geometry.surface_items(state, on)
              if o != item and entities[o].get("spos") is not None]
    along_max = (sd - idp) if index in (0, 2) else (sw - iw)
    centre = along_max / 2.0
    offsets = [0.0]
    step = EDGE_STEP_M
    while step <= along_max / 2.0 + 1e-9:
        offsets += [-step, step]
        step += EDGE_STEP_M
    for offset in offsets:
        along = min(max(0.0, centre + offset), max(0.0, along_max))
        if index == 0:
            spos = [sw - iw - margin, along]
        elif index == 2:
            spos = [margin, along]
        elif index == 1:
            spos = [along, sd - idp - margin]
        else:
            spos = [along, margin]
        if not any(geometry.rect_overlap(spos, (iw, idp), opos, osize) for opos, osize in others):
            record["spos"] = [round(spos[0], 3), round(spos[1], 3)]
            return item
    raise SpecError("edge: no free spot along that edge of {}".format(on))


def op_stage(ctx, op):
    from ..kernel.phys import impact, support
    from ..kernel.world import World
    if op.get("state", "overloaded") != "overloaded":
        raise SpecError("stage: state must be overloaded")
    creep = op.get("creep_s")
    if creep is not None and (not _is_number(creep) or creep <= 0):
        raise SpecError("stage: creep_s must be a positive number of seconds")
    entities = ctx.config["entities"]
    for sid in _targets(ctx, op, "stage"):
        record = entities[sid]
        record["_phys_staged"] = True
        if creep is None:
            continue
        due = round(float(ctx.config.get("time_s", 0.0) or 0.0) + float(creep), 3)
        record["phys_creep_s"] = due
        state = ctx.state()
        slots = [("shelf", k) for k in range(len(record.get("shelf_heights") or []))]
        slots += [("surface",), ("in",), ("hung",)]
        w = World(ctx.config)
        for slot in slots:
            if support.rating(state, sid, slot) is not None:
                impact.schedule_creep(w, sid, slot, due)


def stage_cues(ctx):
    """The glance cue of every staged support once all ops ran (a load op may follow the stage op): a shelf staged
    "about to give way" sags visibly (sagging and its _phys_sag marker) when a slot carries more than 0.9 x its rating,
    whatever the overload law's state (TIER1_DECISIONS: cues are computed lazily for staged entities)."""
    staged = sorted(eid for eid, rec in ctx.config["entities"].items() if rec.get("_phys_staged"))
    if not staged:
        return
    from ..kernel.phys import impact
    from ..kernel.world import World
    w = World(ctx.config)
    for sid in staged:
        impact.sag_cue(w, sid)


def _slot_name(slot):
    return "shelf {}".format(slot[1]) if slot[0] == "shelf" else slot[0]


def physics_problems(ctx):
    """Physically impossible parts of the scene a setup staged (TIER1_PHYSICS 7.5): for every entity the setup created
    or changed, and the supports under them, unless they carry _phys_staged: a support slot over its rating, an item
    on something that is no surface, larger than its surface or with its centre of mass off it, an item that does not
    fit its container (box, opening) or an overfull container, more liquid than a holder holds, an open container
    lying on its side or upside down with liquid in it, and a hot item on a support whose finish it marks. Returns
    sorted messages (empty: nothing to report)."""
    from ..kernel.phys import contain, liquids, statics, support, thermal
    entities = ctx.config["entities"]
    base = getattr(ctx, "phys_base", {})
    changed = [eid for eid in sorted(entities) if base.get(eid) != _phys_sig(entities[eid])]
    if not changed:
        return []
    state = ctx.state()
    cmap = support.children_map(state)
    candidates = set(changed)
    for eid in changed:
        loc = entities[eid].get("location")
        if loc in entities:
            candidates.add(loc)
    problems = set()
    for eid in sorted(candidates):
        rec = entities[eid]
        if rec.get("_phys_staged"):
            continue
        for slot in support.slots(state, eid, cmap):
            if slot[0] in ("floor", "held"):
                continue
            rated = support.rating(state, eid, slot)
            if rated is not None and support.load_on(state, eid, slot, cmap) > rated + 1e-9:
                problems.add("{} ({}) carries more than its rating".format(eid, _slot_name(slot)))
        if eid not in changed:
            continue
        loc = rec.get("location")
        parent = entities.get(loc)
        relation = rec.get("relation", "on")
        if parent is not None and relation == "on":
            if not (parent.get("accepts_items") or parent.get("surface")):
                problems.add("{} is on {}, which is not a surface".format(eid, loc))
            else:
                sw, sd = (parent.get("surface_size_m") or (parent.get("size_m") or [0.5, 0.5])[:2])[:2]
                iw, idp = geometry.footprint_m(rec)
                if iw > float(sw) + 1e-9 or idp > float(sd) + 1e-9:
                    problems.add("{} is larger than {}".format(eid, loc))
                margin = statics.edge_margin_m(state, eid)
                if margin is not None and margin < -1e-9:
                    problems.add("{} has its centre of mass off {}".format(eid, loc))
            limit = thermal.heat_limit_c(state, loc) if "temperature_c" in rec else None
            if limit is not None and thermal.contact_temp_c(state, eid) > limit + 1e-9:
                problems.add("{} is too hot for the finish of {}".format(eid, loc))
        if parent is not None and relation == "in":
            if not contain.box_fits(rec.get("size_m") or [0.1, 0.1, 0.1], parent.get("inner_size_m")) or \
                    not contain.opening_ok(rec, parent):
                problems.add("{} does not fit in {}".format(eid, loc))
            capacity = float(parent.get("capacity_l", 0) or 0)
            if capacity > 0 and contain.nested_volume_l(state, loc) > capacity + 1e-6:
                problems.add("{} is overfull".format(loc))
        liters = rec.get("liquid_l") or 0.0
        if _is_number(liters) and liters > 0:
            hold = liquids.hold_capacity_l(state, eid)
            if hold is not None and liters > hold + 1e-6:
                problems.add("{} holds more liquid than it can".format(eid))
            orientation = rec.get("orientation")
            if orientation in liquids.SIDE_ORIENTATIONS + liquids.INVERTED_ORIENTATIONS and \
                    liquids.tip_spill_l(rec, orientation) >= liquids.droplet_l():
                problems.add("{} lies {} open with liquid in it".format(eid, orientation.replace("_", " ")))
    return sorted(problems)


def op_check_physics(ctx, op):
    problems = physics_problems(ctx)
    if not problems:
        return
    if op.get("strict", True):
        raise SpecError("check_physics: physically impossible staged scene: " + "; ".join(problems[:10]))
    ctx.physics_warnings.extend(p for p in problems if p not in ctx.physics_warnings)



# ----- geometric camera (kernel v3.0, TIER1_CAMERA 10.5) --------------------------------------------------------------

CAMERA_OP_KEYS = ("face", "pan", "tilt", "track", "hw")
SENSOR_KEYS = ("camera_on", "mic_on", "privacy_policy")
VIEWER_KEYS = {"entity": ("entity",), "person": ("person", "attention"),
               "room": ("room", "cell", "z", "yaw", "tilt", "hfov", "range_m")}
CAMERA_SPEC_KEYS = ("hfov_deg", "vfov_deg", "range_m", "focal_px", "facing_deg", "tilt_deg", "height_m", "ir")


def _geometric_op(ctx, name):
    """A geometric-camera setup op: SpecError unless the spec declares "camera": "geometric" (whatever camera the run
    uses); True when it applies (the run's camera is geometric), False for a run forced to the room camera."""
    if (ctx.spec or {}).get("camera") != "geometric":
        raise SpecError("{}: a geometric camera op; the spec must declare \"camera\": \"geometric\"".format(name))
    return ctx.camera == "geometric"


def _check_hw(value, base, path):
    """Hardware overrides must name keys of camera.HW with values of the same shape (numbers, lists of numbers)."""
    if not isinstance(value, dict):
        raise SpecError("camera: hw{} must be an object".format(path))
    for key, item in sorted(value.items()):
        here = "{}.{}".format(path, key)
        if key not in base:
            raise SpecError("camera: hw{} is not a camera hardware value (keys of camera.HW)".format(here))
        ref = base[key]
        if isinstance(ref, dict):
            _check_hw(item, ref, here)
        elif isinstance(ref, list):
            if not (isinstance(item, list) and len(item) == len(ref) and all(_is_number(v) for v in item)):
                raise SpecError("camera: hw{} must be a list of {} numbers".format(here, len(ref)))
        elif not _is_number(item) or item <= 0 and key not in ("look", "walk", "low_row", "up", "down"):
            raise SpecError("camera: hw{} must be a positive number".format(here))


def _target_yaw(ctx, target):
    """Grid yaw from the robot's configured cell toward a thing, person or pet (box centre) or a cell [c, r]."""
    robot = ctx.config.get("robot") or {}
    pos = robot.get("pos")
    if not pos:
        raise SpecError("camera: face a target needs the robot placed first (robot op)")
    x, y = (int(pos[0]) + 0.5) * geometry.CELL, (int(pos[1]) + 0.5) * geometry.CELL
    cell = camera_mod.parse_cell(target)
    if cell is not None:
        tx, ty = (cell[0] + 0.5) * geometry.CELL, (cell[1] + 0.5) * geometry.CELL
    else:
        state = ctx.state()
        if not isinstance(target, str) or not (target in state["entities"] or target in state["people"] or
                                               target in state["pets"]):
            raise SpecError("camera: face must be degrees, a compass word, a cell or a thing / person / pet id")
        box = sight.entity_box(dict(state, robot=dict(robot)), target)
        if box is None or box.rid != robot.get("room"):
            raise SpecError("camera: {} is not in the robot's room".format(target))
        tx, ty = (box.x0 + box.x1) / 2.0, (box.y0 + box.y1) / 2.0
    if math.hypot(tx - x, ty - y) < 1e-9:
        raise SpecError("camera: the robot stands at {}".format(target))
    return math.degrees(math.atan2(ty - y, tx - x))


def op_camera(ctx, op):
    apply = _geometric_op(ctx, "camera")
    keys = set(op) - {"op"}
    sensors = sorted(keys & set(SENSOR_KEYS))
    if sensors:
        raise SpecError("camera: {} are the robot's sensor switches; set them with the robot op".format(
            ", ".join(sensors)))
    unknown = sorted(keys - set(CAMERA_OP_KEYS))
    if unknown:
        raise SpecError("camera: unknown keys {} (allowed: {})".format(", ".join(unknown), ", ".join(CAMERA_OP_KEYS)))
    hw = camera_mod.HW
    view = {}
    if "hw" in op:
        _check_hw(op["hw"], hw, "")
        view["hw"] = copy.deepcopy(op["hw"])
    head = camera_mod._deep_merge(hw, view.get("hw") or {})["head"]
    if "pan" in op:
        if not _is_number(op["pan"]) or abs(op["pan"]) > head["pan_limit_deg"]:
            raise SpecError("camera: pan must be a number within +-{} degrees".format(head["pan_limit_deg"]))
        view["pan_deg"] = round(float(op["pan"]), 2)
    if "tilt" in op:
        lo, hi = head["tilt_limits_deg"]
        if not _is_number(op["tilt"]) or not lo <= op["tilt"] <= hi:
            raise SpecError("camera: tilt must be a number from {} to {} degrees".format(lo, hi))
        view["tilt_deg"] = round(float(op["tilt"]), 2)
    if "track" in op:
        ids = op["track"]
        agents = set(ctx.config.get("people", {})) | set(ctx.config.get("pets", {}))
        if not isinstance(ids, list) or any(i not in agents for i in ids):
            raise SpecError("camera: track must list people and pets of the house")
        view["track"] = sorted(set(ids))
    yaw = None
    if "face" in op:
        face = op["face"]
        if _is_number(face):
            yaw = float(face)
        elif isinstance(face, str) and face in camera_mod.COMPASS_DEG:
            yaw = camera_mod.COMPASS_DEG[face]
        else:
            yaw = _target_yaw(ctx, face)
    if not apply:
        return
    robot = ctx.config.setdefault("robot", {})
    if yaw is not None:
        robot["facing_deg"] = round(yaw % 360.0, 2)
    if view:
        own = robot.get("view") if isinstance(robot.get("view"), dict) else {}
        if "hw" in view and isinstance(own.get("hw"), dict):
            view["hw"] = camera_mod._deep_merge(own["hw"], view["hw"])
        own.update(view)
        robot["view"] = own


def _view_number(lo=None, hi=None, none_ok=False):
    check = _range(lo, hi)

    def number(value):
        return None if none_ok and value is None else check(value)
    return number


def _view_choice(*choices):
    return lambda value: None if value in choices else "must be one of " + ", ".join(str(c) for c in choices)


def _view_faces(value):
    if isinstance(value, dict) and all(isinstance(k, str) and (_is_number(v) or v in ("up", "down", "all"))
                                       for k, v in value.items()):
        return None
    return "must map face names to a yaw (degrees from the front), \"up\", \"down\" or \"all\""


def _view_text(value):
    if isinstance(value, dict) and all(isinstance(k, str) and _is_number(v) and v > 0 for k, v in value.items()):
        return None
    return "must map field names to a character height in metres"


def _view_camera(value):
    if not isinstance(value, dict) or set(value) - set(CAMERA_SPEC_KEYS):
        return "must be an object with " + ", ".join(CAMERA_SPEC_KEYS)
    for key, item in value.items():
        if key == "ir" and not isinstance(item, bool) or key != "ir" and not _is_number(item):
            return "{} must be {}".format(key, "true or false" if key == "ir" else "a number")
    return None


def _view_beam(value):
    if isinstance(value, dict) and set(value) == {"power", "half_deg"} and _is_number(value["power"]) and \
            value["power"] > 0 and _is_number(value["half_deg"]) and 0 < value["half_deg"] <= 90:
        return None
    return "must be {\"power\": > 0, \"half_deg\": 0-90}"


# the view properties the view op may set (TIER1_CAMERA 4.3; sight.VIEW_DEFAULTS)
VIEW_FIELDS = {"porosity": _view_number(0, 1), "below_porosity": _view_number(0, 1),
               "clear_below_m": _view_number(0, 3), "behind_gap_m": _view_number(0, 3),
               "slab_m": _view_number(0, 1, none_ok=True), "height_m": _view_number(0, 10, none_ok=True),
               "opening": _view_choice("front", "top", "none"), "interior_light": _boolean, "glossy": _boolean,
               "front_deg": _view_number(-360, 360, none_ok=True), "faces": _view_faces, "text_m": _view_text,
               "camera": _view_camera, "beam": _view_beam}


def op_view(ctx, op):
    apply = _geometric_op(ctx, "view")
    props = op.get("props")
    if not isinstance(props, dict) or not props:
        raise SpecError("view: props must be a non-empty object")
    for key in sorted(props):
        check = VIEW_FIELDS.get(key)
        if check is None:
            raise SpecError("view: {} is not a view property (allowed: {})".format(key, ", ".join(sorted(VIEW_FIELDS))))
        problem = check(props[key])
        if problem:
            raise SpecError("view: {} {}".format(key, problem))
    targets = _targets(ctx, op, "view")
    if not apply:
        return
    for target in targets:
        record = ctx.config["entities"][target]
        view = record.get("view") if isinstance(record.get("view"), dict) else {}
        for key, value in props.items():
            if isinstance(value, dict) and isinstance(view.get(key), dict):
                view[key] = dict(view[key], **copy.deepcopy(value))
            else:
                view[key] = copy.deepcopy(value)
        record["view"] = view


def _known_ids(ctx):
    return set(ctx.config["entities"]) | set(ctx.config.get("people", {})) | set(ctx.config.get("pets", {}))


def op_track_view(ctx, op):
    apply = _geometric_op(ctx, "track_view")
    ids = op.get("ids")
    if not isinstance(ids, list) or not ids or any(not isinstance(i, str) or i not in _known_ids(ctx) for i in ids):
        raise SpecError("track_view: ids must list things, people or pets of the house")
    if not apply:
        return
    log = ctx.config.setdefault("camera_log", {})
    log["track_ids"] = sorted(set(log.get("track_ids") or []) | set(ids))


def _check_viewer(ctx, viewer):
    if not isinstance(viewer, dict):
        raise SpecError("view_watch: viewer must be an object")
    if "aperture" in viewer:
        raise SpecError("view_watch: aperture viewers (outside a window or door) arrive with camera phase 3")
    forms = [form for form in VIEWER_KEYS if form in viewer]
    if len(forms) != 1 or set(viewer) - set(VIEWER_KEYS[forms[0]]):
        raise SpecError("view_watch: viewer must be {\"entity\"}, {\"person\", \"attention\"} or {\"room\", \"cell\", "
                        "\"z\", \"yaw\", \"tilt\", \"hfov\", \"range_m\"}")
    form = forms[0]
    if form == "entity" and viewer["entity"] not in ctx.config["entities"]:
        raise SpecError("view_watch: unknown device {}".format(viewer["entity"]))
    if form == "person":
        if viewer["person"] not in ctx.config.get("people", {}):
            raise SpecError("view_watch: unknown person {}".format(viewer["person"]))
        if "attention" in viewer and not isinstance(viewer["attention"], bool):
            raise SpecError("view_watch: attention must be true or false")
    if form == "room":
        room = ctx.config["rooms"].get(viewer["room"])
        cell = viewer.get("cell")
        if room is None or not (isinstance(cell, list) and len(cell) == 2 and all(type(v) is int for v in cell)
                                and geometry.in_grid(room, tuple(cell))):
            raise SpecError("view_watch: a room viewer needs a room and a cell [c, r] in it")
        for key in ("z", "yaw", "tilt", "hfov", "range_m"):
            if key in viewer and not _is_number(viewer[key]):
                raise SpecError("view_watch: {} must be a number".format(key))


def op_view_watch(ctx, op):
    apply = _geometric_op(ctx, "view_watch")
    name = op.get("name")
    if not isinstance(name, str) or not name.strip():
        raise SpecError("view_watch: name must be a non-empty string")
    _check_viewer(ctx, op.get("viewer"))
    targets = op.get("targets")
    known = _known_ids(ctx) | {"robot"}
    if not isinstance(targets, list) or not targets or any(t not in known for t in targets):
        raise SpecError("view_watch: targets must list things, people, pets or \"robot\"")
    every = op.get("every_s", camera_mod.WATCH_EVERY_S)
    if not _is_number(every) or every < camera_mod.WATCH_MIN_EVERY_S:
        raise SpecError("view_watch: every_s must be a number of at least {:g} s".format(camera_mod.WATCH_MIN_EVERY_S))
    if not apply:
        return
    specs = ctx.config.setdefault("camera_log", {}).setdefault("watch_specs", {})
    specs[name] = {"viewer": copy.deepcopy(op["viewer"]), "targets": list(targets), "every_s": float(every)}


OPS = {"room": op_room, "find": op_find, "spawn": op_spawn, "set": op_set, "set_path": op_set_path, "move": op_move,
       "remove": op_remove, "person": op_person, "remove_people": op_remove_people, "pet": op_pet,
       "remove_pets": op_remove_pets, "robot": op_robot, "robot_fault": op_robot_fault, "event": op_event, "user_sim": op_user_sim,
       "weather": _section("weather"), "utilities": _section("utilities"), "household": _section("household"),
       "clock": _section("clock"), "metadata": _section("metadata"), "memory": op_memory, "approvals": op_approvals,
       "lights": op_lights, "choose": op_choose, "randint": op_randint, "uniform": op_uniform, "bind": op_bind,
       "helper": op_helper, "assert": op_assert, "phys": op_phys, "load": op_load, "edge": op_edge,
       "stage": op_stage, "check_physics": op_check_physics, "camera": op_camera, "view": op_view,
       "track_view": op_track_view, "view_watch": op_view_watch}
NEW_SPEC_SCHEMA = 3          # specs authored under tier 1: the physics lint is an error for them (TIER1_INTEGRATION 1.13)


def apply_setup(config, ops, registry, seed, spec, bindings=None, camera=None, perception=None):
    """Apply the setup ops to a generated house config; returns the bindings. camera / perception: the run's modes
    when the caller forces them (None: the spec's own), which decide whether the geometric camera ops apply."""
    rng = random.Random("setup:{}:{}".format(spec.get("catalog_id", "custom"), seed))
    ctx = SetupCtx(config, registry, rng, seed, spec, camera, perception)
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
    stage_cues(ctx)
    # the physics lint over what the setup staged: an error for tier-1 specs, warnings for older ones
    problems = physics_problems(ctx)
    if problems:
        schema = spec.get("schema") if isinstance(spec, dict) else None
        if isinstance(schema, int) and not isinstance(schema, bool) and schema >= NEW_SPEC_SCHEMA:
            raise SpecError("physically impossible staged scene: " + "; ".join(problems[:10]))
        ctx.physics_warnings.extend(p for p in problems if p not in ctx.physics_warnings)
    if ctx.physics_warnings:
        ctx.bindings["_physics_warnings"] = list(ctx.physics_warnings)
    return ctx.bindings
