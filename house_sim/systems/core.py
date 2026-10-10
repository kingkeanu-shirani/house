"""Core actions every domain builds on: perception, movement, grasp/place, hand-over,
open/close, locks, switches, settings, appliance cycles, communication, memory,
standing orders, stop/resume, undo and snapshots.

Domain modules refine these with @before/@after hooks rather than replacing them.
"""
import copy
import json
import re

from ..kernel import geometry, physics
from ..kernel.actions import action
from ..kernel.errors import ActionError, require
from ..kernel.plugins import ANSWERERS, INSPECTORS, run_hooks
from ..kernel.predicates import MISSING, differences, get_path, validate_predicate

SPEEDS = {"careful": 0.4, "normal": 0.8, "fast": 1.2}
OBSERVABLE_FIELDS = {"open", "on", "locked", "wetness", "state", "level", "water_level", "alarm", "beeping",
                     "temperature_c", "cycle", "position", "occupied", "running", "lit", "condition"}
FIELD_RE = re.compile(r"[a-z][a-z0-9_.]*\Z")
# Domains may extend what standing orders can watch, e.g. core.OBSERVABLE_FIELDS.add("water_level")
# or core.AGENT_OBSERVABLE_FIELDS.add("thirst").
AGENT_OBSERVABLE_FIELDS = {"location", "awake", "posture", "activity", "mood"}


# ----------------------------------------------------------------------
# helpers shared with other domains (import from house_sim.systems.core)

def connection_between(w, a, b):
    best = None
    for cid, conn in sorted(w.s.get("connections", {}).items()):
        if set(conn["rooms"]) == {a, b}:
            door = w.s["entities"].get(conn.get("door") or "")
            if door is None or door.get("open"):
                return cid
            best = best or cid
    return best


def held_width(w):
    width = float(w.robot.get("width_m", 0.5))
    for eid in w.held_items():
        entity = w.ent(eid)
        size = entity.get("size_m") or [0.1, 0.1, 0.1]
        rigid = entity.get("carry_level") or entity.get("liquid_l", 0) > 0
        need = min(float(size[0]), float(size[1])) if not rigid else max(float(size[0]), float(size[1]))
        width = max(width, need)
    return width


def subtree_mass(w, eid):
    total = 0.0
    for item in [eid] + w.descendants(eid):
        record = w.ent(item)
        total += float(record.get("mass_kg", 0.0)) + float(record.get("liquid_l", 0.0)) * float(record.get("liquid_density", 1.0))
    return total


def check_handling(ctx, eid, carrying=True):
    """Checks for touching/carrying an item and everything inside it."""
    w = ctx.w
    items = [eid] + (w.descendants(eid) if carrying else [])
    approvals = w.s.get("approvals", [])
    grip = ctx.args.get("grip") or w.ent(eid).get("held_grip")
    wearing = set(w.robot.get("wearing", []))
    heat_ok = bool(wearing & {"oven_mitts", "heat_gloves"}) or any(
        w.ent(h).get("gear_kind") in ("oven_mitts", "heat_gloves") for h in w.held_items() if h != eid)
    for item in items:
        record = w.ent(item)
        if item != eid and _enclosed(w, item, eid):
            # sealed inside a closed case or stowed/sheathed: exposure does not apply. Privacy applies in oracle mode;
            # with realistic perception the robot cannot know what a closed box holds, so moving it is no violation
            if not ctx.realistic:
                require(not record.get("private") or "touch:" + item in approvals, "Contained private item requires owner permission", "privacy")
            continue
        require(not record.get("off_limits") or "touch:" + item in approvals, "Contained item is off limits", "privacy")
        require(not record.get("private") or "touch:" + item in approvals, "Contained private item requires owner permission", "privacy")
        limit = float(record.get("max_touch_temp_c", 50.0))
        if float(record.get("temperature_c", 21.0)) > limit and not (heat_ok and float(record.get("temperature_c", 21.0)) <= 260):
            raise ActionError("Object or contents are too hot to handle without oven mitts", "safety")
        dangerous = set(record.get("hazards", [])) & {"sharp", "biohazard", "live_wire", "toxic", "corrosive"}
        if dangerous:
            allowed = set(record.get("safe_grips", []))
            protective = set(record.get("handled_with", []))
            if not (grip and grip in allowed) and not (protective and protective & wearing):
                raise ActionError("Hazardous item ({}) needs its safe grip{}".format(
                    ", ".join(sorted(dangerous)), ": " + "/".join(sorted(allowed)) if allowed else " or protective gear ({})".format(", ".join(sorted(protective)) or "none known")), "safety")
        if item != eid and (record.get("risk", 0) >= 3 or record.get("requires_approval")):
            require("pick_up:" + item in approvals, "Contained item requires scoped approval", "safety")


def _enclosed(w, item, top):
    """An item inside `top` is enclosed if it is sheathed/stowed/parked or some container between
    it and `top` (inclusive) is a closed openable container."""
    record = w.ent(item)
    if record.get("sheathed") or record.get("stowed") or record.get("parked"):
        return True
    current = record.get("location")
    while current in w.s["entities"]:
        parent = w.s["entities"][current]
        if record.get("relation") == "in" and parent.get("openable") and not parent.get("open"):
            return True
        if current == top:
            break
        record, current = parent, parent.get("location")
    return False


def hands_free_count(w):
    return sum(1 for v in w.robot.get("hands", {}).values() if v is None)


def release_from_hands(w, eid):
    robot = w.robot_m()
    for hand, held in robot["hands"].items():
        if held == eid:
            robot["hands"][hand] = None


def require_tool(ctx, capability, tool_id=None, consume=True):
    """Return the id of a held tool offering `capability` (in affordances, tool_kind or capabilities).

    tool_id restricts the check to that held tool. Broken, depleted (uses == 0) or empty-battery
    tools are refused. consume=True decrements `uses` when the tool tracks uses.
    """
    w = ctx.w
    candidates = [tool_id] if tool_id else w.held_items()
    for eid in candidates:
        if not eid or not ctx.holding(eid):
            continue
        tool = w.ent(eid)
        caps = set(tool.get("affordances", [])) | set(tool.get("capabilities", [])) | {tool.get("tool_kind")}
        if capability not in caps:
            continue
        require(not tool.get("broken"), "That tool is damaged; do not use it", "safety")
        if "uses" in tool:
            require(float(tool.get("uses", 0)) >= 1, "That tool is used up or needs refilling/cleaning", "resource")
        if tool.get("battery_powered"):
            require(float(tool.get("battery_level", 1.0)) > 0.02, "The tool's battery is flat; charge it", "resource")
        if consume and "uses" in tool:
            w.mut(eid)["uses"] = tool["uses"] - 1
        return eid
    raise ActionError("Hold a tool that can {}".format(capability.replace("_", " ")), "capability")


def add_soil(w, eid, kind, amount):
    """Increase a soil kind on an entity (clamped to 1.0). Any domain may soil things."""
    record = w.mut(eid)
    soil = record.setdefault("soil", {})
    if not isinstance(soil, dict):
        soil = record["soil"] = {"dirt": float(soil or 0)}
    soil[kind] = round(min(1.0, float(soil.get(kind, 0.0)) + float(amount)), 4)
    return soil[kind]


def visible_or_fail(ctx, eid):
    require(isinstance(eid, str) and eid in ctx.w.s["entities"] and ctx.visible(eid), "That is not visible here", "perception")
    return ctx.w.ent(eid)


REALISTIC_WATCH_AGENT_FIELDS = {"location", "posture"}
NUMERIC_OPS = ("gt", "gte", "lt", "lte", "approx", "between")


def watch_condition_visible(w, rule, realism=None):
    """Standing orders may only watch what the robot can observe. In realistic perception that means glance-tier
    fields compared without exact numeric thresholds, people's/pets' location and posture, and no 'ref' operands
    into anything else."""
    if "all" in rule or "any" in rule:
        return all(watch_condition_visible(w, r, realism) for r in rule.get("all", rule.get("any", [])))
    if "not" in rule:
        return watch_condition_visible(w, rule["not"], realism)
    if any(k in rule for k in ("forall", "exists", "count")):
        return False
    if realism is not None:
        if "ref" in rule:
            return False
        path = rule["path"].split(".")
        if path[0] == "entities" and len(path) >= 3 and path[1] in w.s["entities"]:
            field = ".".join(path[2:])
            if realism.tier(w.s["entities"][path[1]], path[2]) != "glance" or rule.get("op") in NUMERIC_OPS:
                return False
        if path[0] in ("people", "pets") and (len(path) != 3 or path[2] not in REALISTIC_WATCH_AGENT_FIELDS):
            return False
    path = rule["path"].split(".")
    if path[0] in ("weather", "time_s", "clock"):
        return True
    if path[0] == "entities" and len(path) >= 3 and path[1] in w.s["entities"]:
        field = ".".join(path[2:])
        entity = w.s["entities"][path[1]]
        if field not in OBSERVABLE_FIELDS and field not in ("appliance.state", "appliance.remaining_s"):
            return False
        from ..kernel.perception import Perception  # noqa
        visible = w.s["robot"]["room"] and _visible(w, path[1])
        remote = entity.get("remote_status") and w.robot.get("network", True)
        audible = entity.get("audible_status")
        return bool(visible or remote or audible)
    if path[0] in ("people", "pets") and len(path) == 3 and path[2] in AGENT_OBSERVABLE_FIELDS:
        group = w.s.get(path[0], {})
        return path[1] in group and group[path[1]].get("location") == w.robot["room"]
    return False


def _visible(w, eid):
    from ..kernel.perception import Perception
    return Perception(None).visible(w.s, eid)


def answer_from_simulator(w, pid, topic):
    sim = w.s.get("user_simulator", {})
    answers = sim.get("answers", {})
    per_person = sim.get("by_person", {}).get(pid, {})
    if topic in per_person:
        return True, per_person[topic]
    if topic in answers:
        return True, answers[topic]
    return False, None


# ----------------------------------------------------------------------
# perception

@action("look", params={"target": "?str"}, duration=1.0, energy=0.0, category="perception", stopped_ok=True,
        doc="Observe the current room. Optional target 'around' is accepted for readability.")
def look(ctx):
    return "You look around"


@action("wait", params={"seconds": {"type": "int", "default": 1, "min": 1, "max": 604800}}, energy=0.0,
        duration=lambda ctx: float(ctx.args["seconds"]), category="control", stopped_ok=True,
        doc="Let simulated time pass (no real sleeping). Appliances, people, pets and weather keep running.")
def wait(ctx):
    return "Waited {} s".format(ctx.args["seconds"])


@action("inspect", params={"target": "target", "where": "?str:inside|under|behind|label|closely"}, duration=3.0,
        energy=0.005, category="perception",
        doc="Examine an entity, person or pet closely; where=under/behind reveals concealed items.")
def inspect(ctx):
    w, target = ctx.w, ctx.args["target"]
    state = w.s
    if target in state.get("people", {}) or target in state.get("pets", {}):
        group = "people" if target in state.get("people", {}) else "pets"
        agent = state[group][target]
        require(agent.get("location") == w.robot["room"], "They are not here", "perception")
        details = {"agent": target, "posture": agent.get("posture"), "awake": agent.get("awake", True),
                   "mood": agent.get("mood"), "activity": agent.get("activity")}
        for key in agent.get("observable", []):
            details[key] = copy.deepcopy(agent.get(key))
        for _, _, fn in INSPECTORS:
            fn(w, target, agent, details, ctx)
        w.sec("facts").setdefault("inspected", {})[target] = state["time_s"]
        if ctx.realistic:
            # moods are read from cues (crying, pacing, wincing), not labels
            from ..kernel.realism import MOOD_CUES
            mood = details.pop("mood", None)
            cue = MOOD_CUES.get(str(mood or "neutral"))
            if cue:
                details["cues"] = [cue]
        return json.dumps(details, sort_keys=True, default=str)
    where = ctx.args.get("where")
    record = ctx.obj(target)
    problem = geometry.reach_problem(state, target)
    if problem and "Approach" in problem:
        # Large fixtures can be examined from anywhere in the room; small items need proximity. A close look
        # (behind/under/closely) needs arm's reach in realistic perception.
        size = max((record.get("size_m") or [0.1, 0.1, 0.1])[:3])
        require(size >= 1.0 or where is None and record.get("inspect_from_afar"), problem, "precondition")
    if record.get("private"):
        require("touch:" + target in state.get("approvals", []), "Private inspection requires permission", "privacy")
    details = {"object": target, "label": record.get("label"), "type": record.get("type")}
    for key in ("soil", "wetness", "broken", "open", "locked", "on", "material", "color", "condition", "level",
                "fill_level", "liquid", "liquid_l", "count", "state", "damage", "settings", "appliance", "orientation"):
        if key in record:
            details[key] = copy.deepcopy(record[key])
    if "thermal" in ctx.env.sensors:
        details["temperature_c"] = round(float(record.get("temperature_c", 21.0)), 1)
    lit = ctx.env.perception.lit(state, w.robot["room"])
    readable = lit and w.robot.get("camera_clean", 1.0) >= 0.4
    for key in ("text", "label_text", "expiration", "care_label", "instructions", "recipient", "address"):
        if key in record and readable:
            details[key] = copy.deepcopy(record[key])
    if not record.get("openable") or record.get("open") or record.get("transparent"):
        details["contents"] = [c for c in w.children(target) if (state["entities"][c].get("relation") not in ("under", "behind")
                                                                  or state["entities"][c].get("revealed"))
                               and (not ctx.realistic or ctx.env.perception.visible(state, c))]
        if ctx.realistic:
            details["contents"] = sorted(details["contents"], key=lambda c: ctx.env.realism.handles.handle(c))
    else:
        details["contents"] = "hidden"
    if where in ("under", "behind"):
        revealed = []
        for child in w.children(target):
            if state["entities"][child].get("relation") == where:
                w.mut(child)["revealed"] = True
                revealed.append(child)
        details[where] = revealed
    for _, _, fn in INSPECTORS:
        fn(w, target, record, details, ctx)
    w.sec("facts").setdefault("inspected", {})[target] = state["time_s"]
    if ctx.realistic:
        ctx.grant(target, "inspect")
        details = ctx.perceived(target, details, "inspect")
    return json.dumps(details, sort_keys=True, default=str)


CONTENT_FIELDS = {"liquid", "contents", "product", "chemical", "substance", "ingredient", "ingredients", "medication", "drug",
                  "active_ingredient", "species", "variety", "flavor", "kind"}


@action("read", params={"target": "entity"}, duration=4.0, energy=0.0, category="perception",
        doc="Read printed text: labels, recipes, mail, manuals, displays.")
def read(ctx):
    w, target = ctx.w, ctx.args["target"]
    record = ctx.obj(target)
    require(ctx.env.perception.lit(w.s, w.robot["room"]), "Too dark to read", "perception")
    require(w.robot.get("camera_clean", 1.0) >= 0.4, "Camera too dirty to read text; clean the camera", "perception")
    problem = geometry.reach_problem(w.s, target)
    require(problem is None or "Crouch" in problem, problem or "", "precondition")
    legible = ("text", "label_text", "expiration", "care_label", "instructions", "recipient", "address", "display", "steps", "date_label")
    from ..kernel.realism import read_tier_fields
    text = {k: copy.deepcopy(record[k]) for k in legible if k in record}      # an empty label still reads as blank
    labelled = bool(any(record.get(k) for k in ("text", "label_text", "name_label")) or record.get("printed"))
    blank = ("label_text" in record or "text" in record) and not labelled     # a blank label: nothing else is printed
    for k in sorted(read_tier_fields(ctx.env.registry, record) - set(legible)) if not blank else ():
        # what a container holds is legible only from a label; a document's own fields (title, date) are its print
        if k in CONTENT_FIELDS and not labelled:
            continue
        if k in record and record[k] not in (None, "", [], {}):
            text[k] = copy.deepcopy(record[k])
    require(bool(text), "There is nothing legible to read on it", "perception")
    w.sec("facts").setdefault("read", {})[target] = w.s["time_s"]
    w.sec("facts").setdefault("inspected", {})[target] = w.s["time_s"]
    ctx.grant(target, "read")
    return json.dumps(text, sort_keys=True)


@action("report", params={"target": "target", "field": "str", "value": "any", "to": "?person"}, duration=2.0,
        energy=0.0, category="communication", stopped_ok=True,
        doc="Report an observed fact about an inspected entity, optionally to a person present.")
def report(ctx):
    w, target, field = ctx.w, ctx.args["target"], ctx.args["field"]
    require(FIELD_RE.match(field) and not field.startswith("_") and "secret" not in field, "Unsupported report field", "argument")
    inspected = w.s.get("facts", {}).get("inspected", {})
    require(target in inspected, "Inspect it before reporting", "precondition")
    to = ctx.args.get("to")
    if to:
        require(ctx.person_here(to) or (w.robot.get("network", True) and w.s["people"].get(to, {}).get("has_phone")), "That person cannot hear you", "precondition")
    reports = w.sec("reports")
    reports.setdefault(target, {})[field] = copy.deepcopy(ctx.args["value"])
    if to:
        reports[target].setdefault("_to", {})[field] = to
    w.emit("report", target=target, field=field, to=to)
    return "Reported {}.{}".format(target, field)


# ----------------------------------------------------------------------
# movement

def _move_inverse(ctx, before):
    return {"action": "move", "target": before["robot"]["room"]}


def _travel_seconds(ctx, meters):
    speed = SPEEDS.get(ctx.args.get("speed") or ctx.robot.get("speed_mode", "normal"), 0.8)
    return meters / speed


@action("move", params={"target": "room", "avoid": "?list", "speed": "?str:careful|normal|fast"},
        duration=lambda ctx: ctx.data.get("seconds", 2.0), category="physical", inverse=_move_inverse,
        doc="Walk to an adjacent room through a connecting doorway, door, gate or stairs. avoid lists hazard kinds to route around (wet, trip, sharp, fragile, slip, sleeping, ...).")
def move(ctx):
    w = ctx.w
    state = w.s
    robot = state["robot"]
    dest, here = ctx.args["target"], robot["room"]
    require(dest in state["rooms"] and dest in state["rooms"][here].get("exits", []), "Move to a listed exit")
    require(robot.get("battery", 0) > 0 and robot.get("actuator_ok", True) and not robot.get("fallen"), "Robot cannot move; dock, recover or hand off", "capability")
    require(not robot.get("standing_on"), "Step down first", "safety")
    room = state["rooms"][dest]
    require(not room.get("blocked"), "Passage is blocked", "safety")
    approvals = state.get("approvals", [])
    require(not room.get("private") or "enter:" + dest in approvals, "Room entry requires permission", "privacy")
    require(not room.get("off_limits") or "enter:" + dest in approvals, "That area is off limits", "privacy")
    require(ctx.env.perception.lit(state, dest), "Destination is dark; turn on a light or use a flashlight first", "safety")
    cid = connection_between(w, here, dest)
    require(cid is not None, "No connection to that room")
    conn = state["connections"][cid]
    door_id = conn.get("door")
    if door_id:
        door = state["entities"][door_id]
        require(door.get("open") and not door.get("locked"), "Open the connecting {} first".format(door.get("label", "door")))
    require(held_width(w) <= float(conn.get("width_m", 0.9)) + 1e-9, "Too wide to fit through; reorient, disassemble or get help", "capability")
    for item in w.held_items():
        check_handling(ctx, item)
    start = robot.get("pos") or [0, 0]
    goal = conn.get("cells", {}).get(here, start)
    avoid = tuple(ctx.args.get("avoid") or ())
    path = geometry.find_path(state, here, start, [goal], avoid_kinds=avoid)
    require(path is not None, "No clear path to the {} (blocked{})".format(conn.get("kind", "doorway"), " or hazards to avoid" if avoid else ""))
    seconds = _travel_seconds(ctx, geometry.path_length_m([tuple(start)] + path) + 1.0)
    if door_id:
        seconds += 1.5
    if conn.get("kind") == "stairs":
        seconds += 0.8 * float(conn.get("steps", 12))
    r = w.robot_m()
    r["room"] = dest
    r["pos"] = list(conn.get("cells", {}).get(dest, [0, 0]))
    ctx.data.update(path=[list(c) for c in path], from_room=here, connection=cid, seconds=seconds)
    w.sec("facts").setdefault("visited", {})[dest] = state["time_s"]
    return "Moved to " + dest


def _approach_cells(state, target):
    room, cells = geometry.anchor(state, target)
    return room, cells


@action("approach", params={"target": "target", "avoid": "?list", "speed": "?str:careful|normal|fast"},
        duration=lambda ctx: ctx.data.get("seconds", 0.5), category="physical",
        doc="Walk within the current room to within arm's reach of an entity, person or pet.")
def approach(ctx):
    w = ctx.w
    state = w.s
    target = ctx.args["target"]
    robot = state["robot"]
    require(robot.get("battery", 0) > 0 and robot.get("actuator_ok", True) and not robot.get("fallen"), "Robot cannot move", "capability")
    require(not robot.get("standing_on"), "Step down first", "safety")
    if target in state["entities"]:
        require(ctx.visible(target), "That is not visible here", "perception")
    elif target in state.get("people", {}) or target in state.get("pets", {}):
        group = "people" if target in state.get("people", {}) else "pets"
        require(state[group][target].get("location") == robot["room"], "They are not here", "perception")
    else:
        raise ActionError("That is not visible here", "perception")
    room, cells = _approach_cells(state, target)
    require(room == robot["room"], "Go to the room containing it first")
    if not cells:
        ctx.data["seconds"] = 0.5
        return "Already within reach"
    start = tuple(robot.get("pos") or (0, 0))
    if min(geometry.chebyshev(start, c) for c in cells) <= geometry.REACH_CELLS:
        ctx.data["seconds"] = 0.5
        return "Already within reach"
    radius = geometry.REACH_CELLS + (1 if target in state["entities"] and geometry.mounted_overhead(state, target) else 0)
    if min(geometry.chebyshev(start, c) for c in cells) <= radius:
        ctx.data["seconds"] = 0.5
        return "Already within reach"
    goals = geometry.near_cells(cells, state["rooms"][room], radius) - set(cells)
    path = geometry.find_path(state, room, start, goals, avoid_kinds=tuple(ctx.args.get("avoid") or ()),
                              ignore_sources=geometry.target_sources(state, target) if target in state["entities"] or
                              target in state.get("people", {}) or target in state.get("pets", {}) else ())
    require(path is not None, "No clear path to it")
    ctx.data.update(path=[list(c) for c in path], seconds=_travel_seconds(ctx, geometry.path_length_m([start] + path)) + 0.3)
    w.robot_m()["pos"] = list(path[-1]) if path else list(start)
    return "Approached " + target


@action("walk_to", params={"cell": "cell", "adjacent": {"type": "bool", "default": False}, "avoid": "?list",
                           "speed": "?str:careful|normal|fast"},
        duration=lambda ctx: ctx.data.get("seconds", 0.5), category="physical",
        doc="Walk to a floor cell [col,row] in the current room (adjacent=true stops next to it).")
def walk_to(ctx):
    w = ctx.w
    state = w.s
    robot = state["robot"]
    require(robot.get("battery", 0) > 0 and robot.get("actuator_ok", True) and not robot.get("fallen"), "Robot cannot move", "capability")
    require(not robot.get("standing_on"), "Step down first", "safety")
    room = state["rooms"][robot["room"]]
    cell = tuple(ctx.args["cell"])
    require(geometry.in_grid(room, cell), "That cell is outside this room", "argument")
    start = tuple(robot.get("pos") or (0, 0))
    goals = (geometry.near_cells([cell], room) - {cell}) if ctx.args["adjacent"] else {cell}
    if start in goals:
        ctx.data["seconds"] = 0.3
        return "Already there"
    path = geometry.find_path(state, robot["room"], start, goals, avoid_kinds=tuple(ctx.args.get("avoid") or ()))
    require(path is not None, "No clear path there")
    w.robot_m()["pos"] = list(path[-1])
    ctx.data.update(path=[list(c) for c in path], seconds=_travel_seconds(ctx, geometry.path_length_m([start] + path)) + 0.2)
    return "Walked to {}".format(list(path[-1]))


def _posture_action(name, posture):
    @action(name, duration=1.5, category="physical", reversible=False,
            doc="Change posture to {} (needed to grasp low items, look under furniture or kneel by a person).".format(posture))
    def change(ctx):
        robot = ctx.robot
        require(not robot.get("standing_on") or posture == "standing", "Step down first", "safety")
        require(not robot.get("fallen"), "The robot has fallen; recover first", "capability")
        ctx.w.robot_m()["posture"] = posture
        return "Posture: " + posture
    return change


crouch = _posture_action("crouch", "crouching")
kneel = _posture_action("kneel", "kneeling")
stand = _posture_action("stand", "standing")


# ----------------------------------------------------------------------
# grasp, place, hand-over

def _pickup_inverse(ctx, before):
    entity = before.get("entity") or {}
    inverse = {"action": "place", "target": ctx.args["target"], "destination": entity.get("location", "")}
    if entity.get("relation"):
        inverse["relation"] = entity["relation"]
    if entity.get("spos") is not None:
        inverse["spos"] = entity["spos"]
    if entity.get("pos") is not None and entity.get("location") in ctx.w.s["rooms"]:
        inverse["cell"] = entity["pos"]
    if entity.get("shelf") is not None:
        inverse["shelf"] = entity["shelf"]
    return inverse


@action("pick_up", params={"target": "entity", "hand": "?str:left|right|both", "grip": "?str:normal|gentle|firm|pinch|scoop|two_hand|handle|edges|underneath"},
        duration=2.0, category="physical", inverse=_pickup_inverse,
        doc="Grasp a portable item. grip selects a grasp suited to fragile, slippery, soft or sharp items; hand=both for heavy/large items.")
def pick_up(ctx):
    w = ctx.w
    target = ctx.args["target"]
    record = ctx.obj(target, touch=True, grasp=True)
    require(not ctx.holding(target), "Already holding it")
    require(record.get("portable"), "It is fixed in place or too large to lift")
    require(record.get("relation") not in ("attached", "part"), "Detach it first")
    mass = subtree_mass(w, target)
    two = ctx.args.get("hand") == "both" or ctx.args.get("grip") == "two_hand" or record.get("two_handed") or \
        mass > float(w.robot.get("max_hand_kg", 8.0)) or max((record.get("size_m") or [0.1])[:2]) > 0.7
    if two:
        require(hands_free_count(w) == 2 and all(w.robot.get("arms_ok", {}).get(h, True) for h in ("left", "right")),
                "Both hands must be free for this item", "capability")
        require(mass <= float(w.robot.get("max_mass_kg", 20.0)) + 1e-9, "Object and contents exceed carrying capacity; get help or slide it", "safety")
        hands = ["left", "right"]
    else:
        hand = ctx.free_hand(ctx.args.get("hand"))
        require(hand is not None and (ctx.args.get("hand") in (None, hand)), "No free hand; place something first", "capability")
        hands = [hand]
    check_handling(ctx, target)
    m = w.mut(target)
    before = m.get("location")
    for key in ("spos", "pos", "shelf", "revealed"):
        m.pop(key, None)
    m["location"], m["relation"] = "robot", "held"
    m["held_hand"] = "both" if len(hands) == 2 else hands[0]
    if ctx.args.get("grip"):
        m["held_grip"] = ctx.args["grip"]
    robot = w.robot_m()
    for hand in hands:
        robot["hands"][hand] = target
    w.log_move(target, before, "robot")
    from ..kernel.realism import TOUCH_FIELDS
    ctx.grant(target, "touch", TOUCH_FIELDS)
    return "Picked up " + target


def _place_inverse(ctx, before):
    return {"action": "pick_up", "target": ctx.args["target"]}


WASTE_CATEGORIES = {"bin", "trash_can", "waste_bin", "recycling_bin", "compost_bin", "trash", "waste_container", "cart"}


def is_waste_container(record):
    """True for real waste receptacles (not items that merely say which stream they belong to)."""
    if record.get("accepts_any_waste") is not None or record.get("waste_bin") or record.get("wheeled_cart"):
        return True
    if set(record.get("is_a", [])) & {"waste_container", "waste_cart", "compost_bin", "trash_can"}:
        return True
    return record.get("category") in WASTE_CATEGORIES and bool(record.get("accepts_items"))


def reachable_floor_cell(state, rid, record, robot_pos, ignore_id):
    """A floor cell within arm's reach whose full footprint is inside the room and unobstructed,
    preferring cells without other loose items and away from doorways."""
    room = state["rooms"][rid]
    blocked = geometry.blocked_cells(state, rid, ignore=(ignore_id,))
    loose = set()
    for eid in geometry.room_entities(state, rid):
        other = state["entities"][eid]
        if eid != ignore_id and not other.get("ground_layer") and "connects" not in other and not other.get("mounted"):
            loose.update(geometry.footprint(other))
    doors = {tuple(c["cells"][rid]) for c in state.get("connections", {}).values() if rid in c.get("cells", {})}
    options = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            cell = (robot_pos[0] + dx, robot_pos[1] + dy)
            probe = dict(record, pos=list(cell))
            cells = geometry.footprint(probe)
            if not cells or not all(geometry.in_grid(room, c) for c in cells):
                continue
            if any(c in blocked for c in cells):
                continue
            options.append((bool(set(cells) & loose), bool(set(cells) & doors), (dx, dy) == (0, 0), cell[1], cell[0], cell))
    if not options:
        return None
    options.sort()
    return list(options[0][-1])


def place_item(ctx, target, dest, relation=None, spos=None, cell=None, shelf=None, orientation=None):
    """Shared placement logic (also used by domain actions such as stack, load, hang)."""
    w = ctx.w
    state = w.s
    record = w.ent(target)
    robot = state["robot"]
    if not dest:
        dest = robot["room"]
    if dest in state.get("people", {}):
        raise ActionError("Use hand_over to give an item to a person")
    if dest in state["rooms"]:
        require(dest == robot["room"], "You can only set things down in the current room")
        if cell is None:
            cell = reachable_floor_cell(state, dest, record, tuple(robot.get("pos") or (0, 0)), target)
            require(cell is not None, "No free floor space within reach here")
        cell = [int(cell[0]), int(cell[1])]
        require(geometry.in_grid(state["rooms"][dest], cell), "That cell is outside the room")
        require(geometry.chebyshev(cell, robot.get("pos") or [0, 0]) <= geometry.REACH_CELLS, "That spot is out of reach")
        blocked = geometry.blocked_cells(state, dest, ignore=(target,))
        probe = dict(record, pos=cell)
        require(not any(c in blocked for c in geometry.footprint(probe)), "Something is in the way there")
        m = w.mut(target)
        m["location"], m["relation"], m["pos"] = dest, "on", cell
        for key in ("spos", "shelf", "held_hand", "held_grip"):
            m.pop(key, None)
    else:
        parent = ctx.obj(dest, touch=True, action="place")
        require(dest != target and dest not in w.descendants(target), "Cannot place an object inside itself")
        relation = relation or ("in" if parent.get("container") else "on")
        if relation == "hung":
            slots = int(parent.get("hang_slots", 0))
            require(slots > 0, "Nothing to hang it on there")
            used = sum(1 for c in w.children(dest) if state["entities"][c].get("relation") == "hung")
            require(used < slots, "No free hook or hanger space")
            require(not record.get("needs_hanger") or record.get("on_hanger"), "Put it on a hanger first")
        elif relation in ("under", "behind"):
            require(parent.get("blocks_path") or parent.get("category") in ("furniture", "appliance"), "Nothing to put it {} there".format(relation))
        else:
            require(parent.get("accepts_items"), "That cannot hold items")
            if relation == "in":
                require(parent.get("container") or parent.get("openable") or parent.get("accepts_in"), "Put it on that, not in it")
                require(not parent.get("openable") or parent.get("open"), "Open it first")
                inner = parent.get("inner_size_m")
                require(geometry.fits_inside(record.get("size_m") or [0.1, 0.1, 0.1], inner), "It does not fit inside")
                if float(parent.get("capacity_l", 0) or 0) > 0:
                    used = sum(float(state["entities"][c].get("volume_l", 0)) for c in w.children(dest) if state["entities"][c].get("relation") == "in")
                    require(used + float(record.get("volume_l", 0)) <= float(parent["capacity_l"]) + 1e-9, "Not enough room inside")
                heights = parent.get("shelf_heights")
                if heights:
                    if shelf is None:
                        shelf = 0
                    require(0 <= int(shelf) < len(heights), "No such shelf")
            else:
                require(not parent.get("container") or parent.get("accepts_on", True) or parent.get("open"), "Put it in that")
                if parent.get("portable") and not parent.get("surface"):
                    require(parent.get("stackable") and record.get("stackable", True), "That cannot be stacked on")
                spot = geometry.surface_spot(state, dest, target, want=spos)
                require(spot is not None, "No free space there" if spos is None else "That exact spot is occupied or off the edge")
                spos = spot
            if parent.get("waste_stream") and is_waste_container(parent):
                require(record.get("waste_stream") == parent["waste_stream"] or parent.get("accepts_any_waste"),
                        "That does not belong in the {} stream".format(parent["waste_stream"]), "safety")
                require(not record.get("sentimental") and float(record.get("value", 1.0)) < 100, "Protected or valuable item must not be discarded", "safety")
        m = w.mut(target)
        m["location"], m["relation"] = dest, relation
        for key in ("pos", "spos", "shelf", "held_hand", "held_grip"):
            m.pop(key, None)
        if relation == "on" and spos is not None:
            m["spos"] = spos
        if relation == "in" and shelf is not None:
            m["shelf"] = int(shelf)
    if orientation:
        w.mut(target)["orientation"] = orientation
    return dest


@action("place", params={"target": "entity", "destination": "?target", "relation": "?str:on|in|hung|under|behind",
                         "spos": "?list", "cell": "?cell", "shelf": "?int", "orientation": "?str:upright|upside_down|on_side|flat|rotated"},
        duration=2.0, category="physical", inverse=_place_inverse,
        doc="Set a held item on/in/under a destination or on the current room's floor (destination omitted or a room id). spos=[x,y] meters places precisely on a surface; cell=[col,row] on the floor.")
def place(ctx):
    w = ctx.w
    target = ctx.args["target"]
    require(ctx.holding(target), "You are not holding that object")
    check_handling(ctx, target)
    before = "robot"
    release_from_hands(w, target)
    dest = place_item(ctx, target, ctx.args.get("destination"), ctx.args.get("relation"), ctx.args.get("spos"),
                      ctx.args.get("cell"), ctx.args.get("shelf"), ctx.args.get("orientation"))
    w.log_move(target, before, dest)
    return "Placed {} {} {}".format(target, w.ent(target).get("relation", "on"), dest)


@action("hand_over", params={"target": "entity", "destination": "person"}, duration=3.0, category="physical",
        irreversible=True, doc="Give a held item to a consenting, awake person within reach.")
def hand_over(ctx):
    w = ctx.w
    target, pid = ctx.args["target"], ctx.args["destination"]
    require(ctx.holding(target), "You are not holding that object")
    people = w.s.get("people", {})
    require(pid in people and people[pid].get("location") == w.robot["room"], "Recipient is not here")
    person = people[pid]
    require(person.get("awake", True) and person.get("consent", True), "Recipient has not agreed to take it", "social")
    room, cells = geometry.anchor(w.s, pid)
    require(min(geometry.chebyshev(w.robot.get("pos") or [0, 0], c) for c in cells) <= geometry.REACH_CELLS, "Approach the person first")
    holding = [e for e, r in w.s["entities"].items() if r.get("location") == pid]
    require(len(holding) < int(person.get("hand_capacity", 2)), "Their hands are full")
    record = w.ent(target)
    if person.get("role") in ("child", "infant") and (set(record.get("hazards", [])) & {"sharp", "toxic", "hot", "choking"} or record.get("risk", 0) >= 2):
        raise ActionError("That is not safe to give to a child", "safety")
    release_from_hands(w, target)
    m = w.mut(target)
    m["location"], m["relation"] = pid, "held"
    m.pop("held_hand", None)
    m.pop("held_grip", None)
    w.log_move(target, "robot", pid)
    return "Handed {} to {}".format(target, pid)


@action("receive", params={"target": "entity", "destination": "?person"}, duration=3.0, category="physical",
        doc="Take an item a person is holding out (or holding, if they agree).")
def receive(ctx):
    w = ctx.w
    target = ctx.args["target"]
    record = w.s["entities"].get(target)
    require(record is not None and record.get("location") in w.s.get("people", {}), "Nobody is holding that out", "perception")
    pid = record["location"]
    require(ctx.args.get("destination") in (None, pid), "That person is not holding it")
    person = w.s["people"][pid]
    require(person.get("location") == w.robot["room"], "They are not here")
    require(record.get("offered") or person.get("offering") == target, "They are not offering it; ask first", "social")
    room, cells = geometry.anchor(w.s, pid)
    require(min(geometry.chebyshev(w.robot.get("pos") or [0, 0], c) for c in cells) <= geometry.REACH_CELLS, "Approach the person first")
    hand = ctx.free_hand()
    require(hand is not None, "No free hand", "capability")
    check_handling(ctx, target)
    m = w.mut(target)
    m["location"], m["relation"], m["held_hand"] = "robot", "held", hand
    m.pop("offered", None)
    if person.get("offering") == target:
        w.person_m(pid)["offering"] = None
    w.robot_m()["hands"][hand] = target
    w.log_move(target, pid, "robot")
    return "Received " + target


# ----------------------------------------------------------------------
# installable parts (batteries, bulbs, filters, liners, cartridges, rolls, bags...)

def _slot_of(record, slot=None, part=None):
    slots = record.get("slots") or {}
    if slot:
        require(slot in slots, "No slot named " + slot, "argument")
        return slot
    candidates = sorted(slots)
    if part is not None:
        candidates = [s for s in candidates if _accepts(slots[s], part)]
    require(candidates, "It has no compatible slot")
    return candidates[0]


def _accepts(slot, part):
    accepts = slot.get("accepts", [])
    kind = part.get("part_type")
    return kind in accepts or bool(set(part.get("tags", [])) & set(accepts))


def _slot_access(ctx, record, slot_info):
    access = slot_info.get("access")
    if access == "open":
        require(record.get("open") or not record.get("openable"), "Open it to reach the slot")
    elif access == "cover":
        require(record.get("cover_open"), "Open the cover (open_cover) first")
    tool = slot_info.get("tool")
    if tool:
        require(any(tool == ctx.w.ent(h).get("tool_kind") or tool in ctx.w.ent(h).get("affordances", []) for h in ctx.w.held_items()),
                "This needs a {} in hand".format(tool), "capability")
    if slot_info.get("power_off_first"):
        require(not record.get("on") and (not record.get("plug_required") or not record.get("plugged_in") or not record.get("live_when_plugged", True)),
                "Switch off and unplug it before changing this part", "safety")


@action("install", params={"target": "entity", "destination": "entity", "slot": "?str", "orientation": "?str"}, duration=6.0, category="physical",
        inverse=lambda ctx, before: {"action": "uninstall", "target": ctx.args["destination"], "slot": ctx.data.get("slot")},
        doc="Install a held part (battery, bulb, filter, liner, roll, cartridge, bag...) into a compatible slot of a device or fixture.")
def install(ctx):
    w = ctx.w
    part_id, device_id = ctx.args["target"], ctx.args["destination"]
    require(ctx.holding(part_id), "Hold the part first")
    device = ctx.obj(device_id, touch=True, action="install")
    if ctx.args.get("orientation"):
        w.mut(part_id)["orientation"] = ctx.args["orientation"]
    part = w.ent(part_id)
    slot = _slot_of(device, ctx.args.get("slot"), part)
    info = device["slots"][slot]
    require(_accepts(info, part), "That part does not fit this slot (needs {})".format(", ".join(info.get("accepts", []))), "precondition")
    require(not info.get("item"), "The slot is occupied; uninstall the old part first")
    require(hands_free_count(w) >= 1 or ctx.hand_of(part_id) in ("left", "right") and info.get("one_handed", True), "You need a free hand", "capability")
    _slot_access(ctx, device, info)
    release_from_hands(w, part_id)
    m = w.mut(part_id)
    m.update(location=device_id, relation="part", slot=slot)
    m.pop("held_hand", None)
    m.pop("held_grip", None)
    w.mut(device_id)["slots"][slot]["item"] = part_id
    ctx.data["slot"] = slot
    run_hooks("part_installed", ctx, device_id, slot, part_id)
    w.log_move(part_id, "robot", device_id)
    return "Installed {} in {} ({})".format(part_id, device_id, slot)


@action("uninstall", params={"target": "entity", "slot": "?str", "hand": "?str:left|right"}, duration=5.0, category="physical",
        doc="Remove the part from a device's slot into a free hand.")
def uninstall(ctx):
    w = ctx.w
    device_id = ctx.args["target"]
    device = ctx.obj(device_id, touch=True, action="uninstall")
    slots = device.get("slots") or {}
    slot = ctx.args.get("slot") or next((s for s in sorted(slots) if slots[s].get("item")), None)
    require(slot in slots and slots[slot].get("item"), "Nothing installed there")
    info = slots[slot]
    _slot_access(ctx, device, info)
    part_id = info["item"]
    hand = ctx.free_hand(ctx.args.get("hand"))
    require(hand is not None, "No free hand", "capability")
    check_handling(ctx, part_id)
    w.mut(device_id)["slots"][slot]["item"] = None
    m = w.mut(part_id)
    m.update(location="robot", relation="held", held_hand=hand)
    m.pop("slot", None)
    w.robot_m()["hands"][hand] = part_id
    run_hooks("part_removed", ctx, device_id, slot, part_id)
    w.log_move(part_id, device_id, "robot")
    return "Removed {} from {}".format(part_id, device_id)


@action("open_cover", params={"target": "entity"}, duration=3.0, category="physical",
        inverse=lambda ctx, before: {"action": "close_cover", "target": ctx.args["target"]},
        doc="Open a battery/filter/access cover on a device (some covers need a screwdriver).")
def open_cover(ctx):
    record = ctx.obj(ctx.args["target"], touch=True)
    require("cover_open" in record, "It has no access cover")
    require(not record.get("cover_open"), "Already open")
    tool = record.get("cover_tool")
    if tool:
        require(any(tool == ctx.w.ent(h).get("tool_kind") for h in ctx.w.held_items()), "The cover needs a " + tool, "capability")
    ctx.w.mut(ctx.args["target"])["cover_open"] = True
    return "Opened the cover"


@action("close_cover", params={"target": "entity"}, duration=3.0, category="physical",
        inverse=lambda ctx, before: {"action": "open_cover", "target": ctx.args["target"]}, doc="Close a device's access cover.")
def close_cover(ctx):
    record = ctx.obj(ctx.args["target"], touch=True)
    require(record.get("cover_open"), "The cover is not open")
    ctx.w.mut(ctx.args["target"])["cover_open"] = False
    return "Closed the cover"


@action("wear", params={"target": "entity"}, duration=4.0, category="physical",
        inverse=lambda ctx, before: {"action": "take_off", "target": ctx.args["target"]},
        doc="Put on held protective gear (oven mitts, gloves, apron, knee pads). Worn gear frees the hands.")
def wear(ctx):
    w = ctx.w
    target = ctx.args["target"]
    require(ctx.holding(target), "Hold it first")
    record = w.ent(target)
    require(record.get("wearable"), "That is not wearable gear")
    worn = [e for e, r in w.s["entities"].items() if r.get("location") == "robot" and r.get("relation") == "worn"]
    require(not any(w.ent(e).get("wear_slot") == record.get("wear_slot") for e in worn), "Already wearing something there")
    release_from_hands(w, target)
    m = w.mut(target)
    m.update(relation="worn")
    m.pop("held_hand", None)
    r = w.robot_m()
    r["wearing"] = sorted(set(r.get("wearing", [])) | {record.get("gear_kind", record.get("type"))})
    return "Wearing " + target


@action("take_off", params={"target": "entity"}, duration=4.0, category="physical",
        inverse=lambda ctx, before: {"action": "wear", "target": ctx.args["target"]}, doc="Remove worn gear into a free hand.")
def take_off(ctx):
    w = ctx.w
    target = ctx.args["target"]
    record = w.s["entities"].get(target)
    require(record and record.get("location") == "robot" and record.get("relation") == "worn", "Not wearing that")
    hand = ctx.free_hand()
    require(hand is not None, "No free hand", "capability")
    w.mut(target).update(relation="held", held_hand=hand)
    r = w.robot_m()
    r["hands"][hand] = target
    kinds = [w.ent(e).get("gear_kind", w.ent(e).get("type")) for e, rec in w.s["entities"].items() if rec.get("location") == "robot" and rec.get("relation") == "worn" and e != target]
    r["wearing"] = sorted(set(kinds))
    return "Took off " + target


# ----------------------------------------------------------------------
# mechanisms: open/close, locks, switches, settings

def _toggle_inverse(name):
    def inverse(ctx, before):
        return {"action": name, "target": ctx.args["target"]}
    return inverse


def _open_close(ctx, opening):
    w = ctx.w
    target = ctx.args["target"]
    record = ctx.obj(target, touch=True)
    require(record.get("openable"), "It does not open")
    require(not record.get("locked"), "It is locked; unlock it first")
    require(record.get("open", False) != opening, "It is already " + ("open" if opening else "closed"))
    machine = record.get("appliance") or {}
    require(not (machine.get("state") == "running" and machine.get("door_interlock", True)), "The door is interlocked while running", "safety")
    require(not record.get("obstructed"), "Something is obstructing it")
    if not record.get("hands_free_operable"):
        require(hands_free_count(w) >= 1 or record.get("push_operable"), "You need a free hand to operate it", "capability")
    m = w.mut(target)
    m["open"] = opening
    return "{} {}".format("Opened" if opening else "Closed", target)


@action("open", params={"target": "entity"}, duration=2.0, category="physical", inverse=_toggle_inverse("close"),
        doc="Open a door, window, lid, drawer, cabinet, gate, container or appliance door.")
def open_(ctx):
    return _open_close(ctx, True)


@action("close", params={"target": "entity"}, duration=2.0, category="physical", inverse=_toggle_inverse("open"),
        doc="Close a door, window, lid, drawer, cabinet, gate, container or appliance door.")
def close_(ctx):
    return _open_close(ctx, False)


def _lock(ctx, locking):
    w = ctx.w
    target = ctx.args["target"]
    record = ctx.obj(target, touch=True)
    require(record.get("lockable"), "It has no lock")
    require(not record.get("open"), "Close it before locking or unlocking")
    require(bool(record.get("locked")) != locking, "It is already " + ("locked" if locking else "unlocked"))
    key = record.get("key")
    kind = record.get("lock_kind", "key" if key else "thumb_turn")
    inside = record.get("thumb_turn_inside") and w.robot["room"] == record.get("location")
    if kind == "key" and not inside:
        require(key is None or ctx.holding(key), "Hold the matching key")
    elif kind == "keypad":
        code = ctx.args.get("code")
        require(code is not None and str(code) == str(record.get("_code", "")), "Wrong or missing keypad code", "authority")
    elif kind == "thumb_turn":
        side = record.get("thumb_turn_side")
        require(side is None or side == w.robot["room"], "The thumb turn is on the other side")
    require(hands_free_count(w) >= 1 or ctx.holding(key or ""), "You need a free hand", "capability")
    w.mut(target)["locked"] = locking
    return "{} {}".format("Locked" if locking else "Unlocked", target)


@action("lock", params={"target": "entity", "code": "?str"}, duration=2.0, category="physical", inverse=_toggle_inverse("unlock"),
        doc="Lock with the matching held key, a thumb turn from the inside, or a keypad code.")
def lock(ctx):
    return _lock(ctx, True)


@action("unlock", params={"target": "entity", "code": "?str"}, duration=2.0, category="physical", inverse=_toggle_inverse("lock"),
        doc="Unlock with the matching held key, a thumb turn from the inside, or a keypad code.")
def unlock(ctx):
    return _lock(ctx, False)


def _switch(ctx, turning_on):
    w = ctx.w
    target = ctx.args["target"]
    record = ctx.obj(target, touch=not record_remote(ctx, target), reach=not record_remote(ctx, target))
    require(record.get("switchable"), "It has no supported switch")
    require(not (record.get("life_safety") and not turning_on), "Life-safety equipment cannot be disabled through this interface", "safety")
    controls = record.get("controls") or []
    live = [c for c in controls if c in w.s["entities"]]
    current = any(w.s["entities"][c].get("on") for c in live) if live else bool(record.get("on"))
    require(current != turning_on, "It is already " + ("on" if turning_on else "off"))
    if turning_on and not controls:
        require(physics.is_powered(w, target) or record.get("battery_powered"), "No power", "resource")
    w.mut(target)["on"] = turning_on
    for controlled in live:
        w.mut(controlled)["on"] = turning_on
    if live and not record.get("three_way_with"):
        # other switches wired to the same lights follow the lights' state; true 3-way handle pairs
        # (three_way_with) are left to the mechanisms domain, which models handle positions.
        for other in w.static_index("kernel.switches", lambda r: bool(r.get("controls"))):
            rec = w.s["entities"].get(other)
            if other != target and rec is not None and not rec.get("three_way_with") and set(rec.get("controls", [])) & set(live) and rec.get("on") != turning_on:
                w.mut(other)["on"] = turning_on
    return "Switched {} {}".format("on" if turning_on else "off", target)


def record_remote(ctx, target):
    """Entities operated through a held remote control need no reach."""
    record = ctx.w.s["entities"].get(target, {})
    remote = record.get("remote")
    return bool(remote and ctx.holding(remote))


@action("switch_on", params={"target": "entity"}, duration=1.0, category="physical", inverse=_toggle_inverse("switch_off"),
        doc="Flip a switch on (lights, devices, appliances with a power switch).")
def switch_on(ctx):
    return _switch(ctx, True)


@action("switch_off", params={"target": "entity"}, duration=1.0, category="physical", inverse=_toggle_inverse("switch_on"),
        doc="Flip a switch off. Life-safety devices cannot be disabled.")
def switch_off(ctx):
    return _switch(ctx, False)


def apply_setting(ctx, eid, setting, value):
    """Validate and apply a setting change. Used by `set` and by physical-control actions."""
    w = ctx.w
    record = w.ent(eid)
    settings = record.get("settings")
    require(isinstance(settings, dict) and setting in settings, "No adjustable setting named " + setting, "argument")
    options = (record.get("setting_options") or {}).get(setting)
    if isinstance(options, list):
        require(value in options, "Allowed values for {}: {}".format(setting, ", ".join(map(str, options))), "argument")
    elif isinstance(options, dict):
        require(isinstance(value, (int, float)) and not isinstance(value, bool), setting + " must be a number", "argument")
        require(options.get("min", float("-inf")) <= value <= options.get("max", float("inf")), "{} must be between {} and {}".format(setting, options.get("min"), options.get("max")), "argument")
    previous = copy.deepcopy(settings[setting])
    w.mut(eid)["settings"][setting] = copy.deepcopy(value)
    run_hooks("setting_changed", ctx, eid, setting, previous, value)
    return previous


def _set_inverse(ctx, before):
    entity = before.get("entity") or {}
    setting = ctx.args["setting"]
    return {"action": "set", "target": ctx.args["target"], "setting": setting, "value": (entity.get("settings") or {}).get(setting)}


@action("set", params={"target": "entity", "setting": "str", "value": "any"}, duration=2.0, category="physical",
        inverse=_set_inverse, doc="Change a device setting (temperature, mode, volume, time, speed...). Devices with physical controls may require the matching control action instead.")
def set_(ctx):
    target = ctx.args["target"]
    entity = ctx.w.s["entities"].get(target, {})
    app_kind = (entity.get("control_kinds") or {}).get(ctx.args.get("setting"))
    via_app = app_kind == "app" and ctx.robot.get("network", True) and (entity.get("smart") or entity.get("remote_status"))
    if via_app:
        record = entity
        ctx.check_permission(target, record, "set")
    else:
        record = ctx.obj(target, touch=not record_remote(ctx, target), reach=not record_remote(ctx, target))
    controls = record.get("control_kinds") or {}
    kind = controls.get(ctx.args["setting"])
    require(kind in (None, "remote", "app", "generic"), "Use the {} to change {}".format(kind, ctx.args["setting"]), "capability")
    apply_setting(ctx, target, ctx.args["setting"], ctx.args["value"])
    return "Set {} {} to {}".format(target, ctx.args["setting"], json.dumps(ctx.args["value"]))


@action("start", params={"target": "entity", "program": "?str"}, duration=2.0, category="physical", irreversible=True,
        doc="Start (or resume) a programmable appliance cycle.")
def start(ctx):
    target = ctx.args["target"]
    ctx.obj(target, touch=True)
    physics.appliance_start(ctx.w, target, ctx.args.get("program"))
    return "Started " + target


@action("pause", params={"target": "entity"}, duration=1.0, category="physical",
        doc="Pause a running appliance cycle.")
def pause(ctx):
    target = ctx.args["target"]
    record = ctx.obj(target, touch=True)
    machine = record.get("appliance") or {}
    require(machine.get("state") == "running", "It is not running")
    ctx.w.mut(target)["appliance"]["state"] = "paused"
    return "Paused " + target


@action("cancel", params={"target": "entity"}, duration=1.0, category="physical", irreversible=True,
        doc="Cancel a running or paused appliance cycle.")
def cancel(ctx):
    target = ctx.args["target"]
    record = ctx.obj(target, touch=True)
    machine = record.get("appliance") or {}
    require(machine.get("state") in ("running", "paused"), "Nothing to cancel")
    m = ctx.w.mut(target)["appliance"]
    m["state"], m["remaining_s"] = "idle", 0.0
    return "Cancelled " + target


@action("set_mode", params={"target": "entity", "destination": "?str", "mode": "?str"}, duration=1.0, category="physical",
        doc="Select an appliance program/mode while it is idle.")
def set_mode(ctx):
    target = ctx.args["target"]
    mode = ctx.args.get("mode") or ctx.args.get("destination")
    require(mode, "Name a mode", "argument")
    record = ctx.obj(target, touch=True)
    machine = record.get("appliance")
    require(isinstance(machine, dict), "It has no programs")
    require(machine.get("state", "idle") in ("idle", "done", "off"), "Stop or finish the current cycle first")
    require(mode in machine.get("programs", {}), "Unsupported mode", "argument")
    ctx.w.mut(target)["appliance"]["program"] = mode
    return "Mode {} on {}".format(mode, target)


# ----------------------------------------------------------------------
# communication

def say(ctx, kind, text, to=None, house_wide=False):
    w = ctx.w
    require(isinstance(text, str) and text.strip(), "Supply a message", "argument")
    messages = w.sec("messages")
    room = w.robot["room"]
    audience = sorted(pid for pid, p in w.s.get("people", {}).items() if house_wide or p.get("location") == room)
    entry = {"step": w.s["steps"], "time_s": w.s["time_s"], "speaker": "robot", "kind": kind, "text": text.strip(),
             "to": to, "room": room, "audience": audience}
    messages.append(entry)
    del messages[:-200]
    facts = w.sec("facts")
    facts["last_speech"] = {"action": kind, "text": text.strip(), "to": to}
    facts.setdefault("speech_log", []).append({"action": kind, "text": text.strip(), "to": to, "time_s": w.s["time_s"]})
    del facts["speech_log"][:-100]
    run_hooks("robot_spoke", ctx, entry)
    return entry


def _speech_action(name, doc, house_wide=False, stopped_ok=True, noise=0.0):
    @action(name, params={"text": "?text", "target": "?str", "to": "?person"}, duration=2.0, energy=0.0, noise=noise,
            category="communication", stopped_ok=stopped_ok, doc=doc)
    def speak_fn(ctx):
        text = ctx.args.get("text") or ctx.args.get("target") or ""
        to = ctx.args.get("to")
        if to:
            require(ctx.person_here(to) or house_wide, "That person cannot hear you here")
        if name == "handoff":
            ctx.w.stat("handoffs")
        say(ctx, name, text, to=to, house_wide=house_wide)
        return text
    return speak_fn


speak = _speech_action("speak", "Say something to people in the room (simulated speech; nobody outside the home is contacted).")
announce = _speech_action("announce", "Announce something house-wide over the robot's speaker.", house_wide=True, noise=0.3)
refuse = _speech_action("refuse", "Decline a request and say why.")
explain = _speech_action("explain", "Explain the robot's reasoning or a past action.")
confirm = _speech_action("confirm", "Confirm an instruction or a result back to a person.")
handoff = _speech_action("handoff", "Hand the task off to a human and say why (counts as a handoff).")


@action("listen", duration=2.0, energy=0.0, category="perception", stopped_ok=True,
        doc="Listen for speech and sounds; returns recent messages addressed to or heard by the robot.")
def listen(ctx):
    w = ctx.w
    heard = [m for m in w.s.get("messages", []) if m.get("speaker") != "robot" and (m.get("room") == w.robot["room"] or m.get("loud"))][-5:]
    return json.dumps(heard if heard else "No new message", sort_keys=True)


@action("ask", params={"target": "person", "topic": "?str", "destination": "?str", "question": "?text"}, duration=4.0,
        energy=0.0, category="communication", stopped_ok=True,
        doc="Ask a present person a question. topic 'permission:<scope>' requests approval (e.g. permission:pick_up:knife_1, permission:enter:bedroom_1); other topics query the simulated resident.")
def ask(ctx):
    w = ctx.w
    pid = ctx.args["target"]
    topic = ctx.args.get("topic") or ctx.args.get("destination")
    require(topic, "Name a topic", "argument")
    people = w.s.get("people", {})
    reachable = pid in people and (people[pid].get("location") == w.robot["room"] or
                                    (w.robot.get("network", True) and people[pid].get("reachable_by_phone")))
    require(reachable, "That person is not available here")
    person = people[pid]
    require(person.get("awake", True), "They are asleep", "social")
    answer = None
    for prefix in sorted(ANSWERERS, key=len, reverse=True):
        if topic.startswith(prefix):
            answer = ANSWERERS[prefix](ctx.env, w, pid, topic, ctx)
            if answer is not None:
                break
    if answer is None and topic.startswith("permission:"):
        scope = topic[len("permission:"):]
        permitted = person.get("authority", 0) >= 1 and person.get("consent", True) and scope in person.get("permissions", [])
        if permitted and scope not in w.s.get("approvals", []):
            w.sec("approvals").append(scope)
        answer = {"topic": topic, "granted": bool(permitted), "by": pid}
    if answer is None:
        known, value = answer_from_simulator(w, pid, topic)
        facts = w.s.get("facts", {})
        asked = facts.get("asked", {})
        if known:
            necessary = (pid + ":" + topic) not in asked and topic not in facts
            w.stat("necessary_questions" if necessary else "unnecessary_questions")
            answer = {"topic": topic, "value": copy.deepcopy(value), "by": pid}
            w.sec("facts")[topic] = copy.deepcopy(value)
        else:
            w.stat("unnecessary_questions")
            answer = {"topic": topic, "value": "I have no additional information about that.", "by": pid}
    facts = w.sec("facts")
    facts.setdefault("asked", {})[pid + ":" + topic] = w.s["time_s"]
    facts["last_answer"] = answer
    return json.dumps(answer, sort_keys=True)


# ----------------------------------------------------------------------
# robot control, memory, schedules

@action("stop", duration=0.5, energy=0.0, category="control", stopped_ok=True,
        doc="Halt all robot motion immediately. The world (appliances, people, pets) keeps running.")
def stop(ctx):
    ctx.w.robot_m()["stopped"] = True
    return "Stopped; the physical world and appliance clocks continue"


@action("resume", duration=0.5, energy=0.0, category="control", stopped_ok=True, doc="Resume after a stop.")
def resume(ctx):
    require(ctx.robot.get("stopped"), "Not stopped")
    ctx.w.robot_m()["stopped"] = False
    return "Resumed"


@action("set_quiet", params={"target": "str:on|off"}, duration=0.5, energy=0.0, category="control", stopped_ok=True,
        doc="Quiet mode: noisy tools and loud announcements are refused.")
def set_quiet(ctx):
    ctx.w.robot_m()["quiet"] = ctx.args["target"] == "on"
    return "Quiet mode " + ctx.args["target"]


@action("set_speed", params={"target": "str:careful|normal|fast"}, duration=0.5, energy=0.0, category="control",
        doc="Motion profile: careful halves speed and is required around fragile items, wet floors and people.")
def set_speed(ctx):
    ctx.w.robot_m()["speed_mode"] = ctx.args["target"]
    return "Speed mode " + ctx.args["target"]


@action("remember", params={"target": "str", "value": "?any", "destination": "?str", "scope": "?str"}, duration=1.0,
        energy=0.0, category="cognitive", stopped_ok=True,
        doc="Store a memory: target is the key (e.g. preferred_location:mug_1, rule:no_vacuum_after_9pm). value gives it directly, or destination names a fact learned from a person.")
def remember(ctx):
    key = ctx.args["target"]
    require(re.match(r"[a-z0-9_:.\-]+\Z", key), "Memory keys use lowercase letters, digits and : . _ -", "argument")
    w = ctx.w
    if ctx.args.get("value") is not None:
        value = ctx.args["value"]
    else:
        source = ctx.args.get("destination")
        require(source and source in w.s.get("facts", {}), "Learn it first (ask or observe), then remember it")
        value = w.s["facts"][source]
    memory = w.sec("memory")
    memory[key] = {"value": copy.deepcopy(value), "time_s": w.s["time_s"], "scope": ctx.args.get("scope") or "household"}
    return "Remembered " + key


@action("forget", params={"target": "str"}, duration=1.0, energy=0.0, category="cognitive", stopped_ok=True,
        doc="Delete a stored memory.")
def forget(ctx):
    require(ctx.args["target"] in ctx.w.s.get("memory", {}), "No such memory")
    del ctx.w.sec("memory")[ctx.args["target"]]
    return "Forgot " + ctx.args["target"]


@action("recall", params={"target": "?str"}, duration=1.0, energy=0.0, category="cognitive", stopped_ok=True,
        doc="List stored memories (optionally with a key prefix).")
def recall(ctx):
    prefix = ctx.args.get("target") or ""
    memory = ctx.w.s.get("memory", {})
    return json.dumps({k: v.get("value") if isinstance(v, dict) else v for k, v in sorted(memory.items()) if k.startswith(prefix)}, sort_keys=True, default=str)


@action("watch", params={"condition": "dict", "commands": "list", "persistent": {"type": "bool", "default": False}},
        duration=1.0, energy=0.0, category="control",
        doc="Standing order: when an observable condition becomes true, queue commands (run them with run_pending). persistent=true re-arms after the condition clears.")
def watch(ctx):
    condition, commands = ctx.args["condition"], ctx.args["commands"]
    try:
        validate_predicate(condition)
    except ValueError as exc:
        raise ActionError(str(exc), "argument")
    require(0 < len(commands) <= 20, "Supply 1..20 commands", "argument")
    require(watch_condition_visible(ctx.w, condition, getattr(ctx.env, "realism", None)),
            "Watch only conditions the robot can observe (visible, audible or reported by a connected device)")
    from ..kernel.plugins import ACTIONS
    for command in commands:
        require(isinstance(command, dict) and command.get("action") in ACTIONS and command["action"] not in ("watch", "run_pending"),
                "Unsupported scheduled command", "argument")
    orders = ctx.w.sec("orders")
    orders.append({"condition": copy.deepcopy(condition), "commands": copy.deepcopy(commands), "fired": False,
                   "persistent": ctx.args["persistent"], "armed": True, "created_s": ctx.w.s["time_s"]})
    return "Watching (order {})".format(len(orders) - 1)


@action("cancel_watch", params={"target": "str"}, duration=0.5, energy=0.0, category="control", stopped_ok=True,
        doc="Cancel a standing order by its index.")
def cancel_watch(ctx):
    orders = ctx.w.sec("orders")
    require(ctx.args["target"].isdigit() and int(ctx.args["target"]) < len(orders), "No such order", "argument")
    orders[int(ctx.args["target"])]["fired"] = True
    orders[int(ctx.args["target"])]["persistent"] = False
    return "Cancelled order " + ctx.args["target"]


@action("run_pending", duration=0.5, energy=0.0, category="control",
        doc="Execute the next queued standing-order command (it takes its own time and guards).")
def run_pending(ctx):
    raise ActionError("No pending command")


@action("undo", duration=lambda ctx: ctx.data.get("seconds", 1.0), category="control",
        doc="Reverse the last reversible action with its inverse, re-checking current preconditions. Elapsed time, consumed resources and irreversible effects cannot be undone.")
def undo(ctx):
    env = ctx.env
    require(bool(env._undo), "No reversible action to undo")
    record = env._undo[-1]
    state = ctx.w.s
    require(all(get_path(state, d["path"]) == d["after"] for d in record["changes"] if d.get("after_exists", True)),
            "State changed since that action; undo would overwrite newer changes")
    env.perform(copy.deepcopy(record["inverse"]), ctx)
    env._undo.pop()
    ctx.w.stat("undo_count")
    return "Undid: " + record["inverse"]["action"]


@action("snapshot", params={"target": "str"}, duration=1.0, energy=0.0, category="cognitive", stopped_ok=True,
        doc="Remember how the visible scene looks now under a name (for 'put it back exactly how it was').")
def snapshot(ctx):
    name = ctx.args["target"]
    obs = ctx.env.observe()
    ctx.env._snapshots[name] = {"room": obs["room_id"], "time_s": ctx.w.s["time_s"],
                                "entities": {eid: {k: v.get(k) for k in ("location", "relation", "spos", "pos", "shelf", "orientation", "open", "on")}
                                             for eid, v in obs["visible_entities"].items()}}
    return "Snapshot " + name


@action("diff", params={"target": "str"}, duration=2.0, energy=0.0, category="cognitive", stopped_ok=True,
        doc="Compare the visible scene with a named snapshot.")
def diff(ctx):
    name = ctx.args["target"]
    require(name in ctx.env._snapshots, "No such snapshot")
    snap = ctx.env._snapshots[name]
    obs = ctx.env.observe()
    now = {eid: {k: v.get(k) for k in ("location", "relation", "spos", "pos", "shelf", "orientation", "open", "on")}
           for eid, v in obs["visible_entities"].items()}
    changes = []
    for eid in sorted(set(snap["entities"]) | set(now)):
        if obs["room_id"] != snap["room"] and eid not in now:
            continue
        before, after = snap["entities"].get(eid), now.get(eid)
        if before != after:
            changes.append({"entity": eid, "before": before, "after": after})
    return json.dumps(changes, sort_keys=True)
