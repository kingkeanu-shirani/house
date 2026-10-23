"""Core actions every domain builds on: perception, movement, grasp/place, hand-over,
open/close, locks, switches, settings, appliance cycles, communication, memory,
stop/resume, undo and snapshots.

The time verbs and standing orders (wait, watch, cancel_watch, run_pending) and the rule for what an order may watch
live in kernel/timeline.py since the sweep's Phase 0 (tier-1 A0); this module re-exports those names unchanged.

Geometric camera mode (kernel v3.0, docs/design/TIER1_CAMERA.md 9): look takes frames (around, up, at a thing, ...),
face turns the body, peek looks with the wrist camera, and inspect / read follow the cameras' acuity, faces and
reach; the camera mechanics are kernel/camera.py's. In room mode look is unchanged and face / peek refuse.

Robot body (kernel v3.0, docs/design/TIER1_EMBODIMENT.md 4.7-4.10; kernel/body.py): pick_up, receive and uninstall
plan the lift (ctx.lift_plan), take the item with a grasp its size allows (ctx.require_grasp: a pinch for small
things, too small refused) and enforce the derated capacity (ctx.check_lift: an overload is refused, counted and felt);
look, peek, inspect and read need the colour cameras (ctx.require_imaging) and look in a lit room finds a lost pose
again; listen and ask need the microphones (ctx.require_hearing: privacy mode hears only speech addressed to the
robot); speech through a failed speaker is inaudible; set_speed fast is refused while quiet or limp; stairs need a
robot that climbs them; the motion verbs time themselves from the walking speed (timing="speed", so careful speed
slows them once).

People and memory (kernel v3.1, docs/design/TIER1_SWEEP.md 4, 5, 7; docs/DIALOGUE.md): ask takes the canonical
question form (about / aspect / detail / act / options / channel / language; kernel/dialogue.py) beside the legacy
topic, whose answer path (legacy_answer) is unchanged; report takes typed kinds (finding / measurement / status /
count / none_found with subject, issue, state, quantity, value, unit, scope, to, channel; kernel/reports.py) beside the
legacy target / field / value form, whose store is unchanged; recall reads the robot's Mind (map, seen:, snap:) as
well as its stored memories; snapshot and diff keep their scenes in the Mind; the realistic messages of these memory
actions name every thing whose handle was issued before (kernel/mind.py, realism.memory_output).

Domain modules refine these with @before/@after hooks rather than replacing them.
"""
import copy
import json
import math
import re

from ..kernel import body, camera, dialogue, geometry, physics, reports
from ..kernel import mind as mind_mod
from ..kernel.actions import action, owner_asks, require_access
from ..kernel.phys import contain, impact, lawbook, liquids, support
from ..kernel.errors import ActionError, require
from ..kernel.plugins import ANSWERERS, INSPECTORS, after, run_hooks
from ..kernel.predicates import MISSING, differences, get_path, validate_predicate
# re-exported for domains and tests (the same objects: extending core.OBSERVABLE_FIELDS extends the timeline rule)
from ..kernel.timeline import (AGENT_OBSERVABLE_FIELDS, NUMERIC_OPS, OBSERVABLE_FIELDS,  # noqa: F401
                               REALISTIC_WATCH_AGENT_FIELDS, cancel_watch, run_pending, wait, watch,
                               watch_condition_visible)

# walking speeds (m/s) of the default body profile, for compatibility; a robot walks at body.speed_mps of its own
# profile and effective speed (body.effective_speed)
SPEEDS = {mode: body.speed_mps({"robot": {}}, mode) for mode in body.SPEED_MODES}
FIELD_RE = re.compile(r"[a-z][a-z0-9_.]*\Z")


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
    """Mass (kg) of eid and everything in, on or attached to it, liquids at their density (kernel v3.0: delegates to
    phys.support.subtree_mass, the kernel's single implementation; a liquid without liquid_density takes its
    liquids.json density)."""
    w.ent(eid)                      # an unknown id refuses as before ("Unknown entity")
    return support.subtree_mass(w.s, eid)


HANDLING_HAZARDS = {"sharp", "biohazard", "live_wire", "toxic", "corrosive"}
UNIDENTIFIED_HAZARD = ("You cannot tell what this is yet and it could be hazardous: identify it first (inspect or read it), "
                       "or use a grip or protective gear that is safe for anything it could be")


def handling_rule_met(rule, grip, wearing):
    """One hazard rule (a record or type props with hazards / safe_grips / handled_with) is met by the grip in use or
    the protective gear worn; things without a handling hazard need nothing."""
    if not set(rule.get("hazards") or []) & HANDLING_HAZARDS:
        return True
    allowed = set(rule.get("safe_grips") or [])
    protective = set(rule.get("handled_with") or [])
    return bool(grip and grip in allowed) or bool(protective & set(wearing))


def lookalike_rules(ctx, eid, relevant):
    """Realistic mode: the type props of everything an unresolved look-alike `eid` could be, filtered by
    `relevant(rule)`: every member type of its group (what the robot knows about such things). The list is the same
    for every member of the group and does not depend on any hidden instance -- not on what other members elsewhere in
    the house hold or carry -- so a guard that requires all of them to pass cannot tell members apart. None when eid
    has no group (a novel object, or a portion of one)."""
    from ..kernel.actions import group_member_types, lookalike_group
    realism = ctx.env.realism
    state = ctx.w.s
    name = lookalike_group(realism, state, state["entities"].get(eid))
    if name is None:
        return None
    registry = ctx.env.registry
    return [registry.resolve(t) for t in group_member_types(realism, name) if relevant(registry.resolve(t))]


def type_props(ctx, record):
    """The registry props of a record's type ({} for a type the registry does not know)."""
    registry = ctx.env.registry
    type_name = record.get("type")
    try:
        if type_name and type_name in getattr(registry, "types", {}):
            return registry.resolve(type_name)
    except Exception:  # noqa: BLE001
        pass
    return {}


def hazards_known(ctx, eid, record):
    """Realistic mode: whether the robot knows this instance's own hazards -- the hazards field at its tier (an
    inspect for the default inspect tier) or a visible break (shattered/smashed)."""
    realism = ctx.env.realism
    if realism is None:
        return True
    if any(record.get(k) for k in ("shattered", "smashed")):
        return True
    from ..kernel.realism import TIER_NEED
    need = TIER_NEED.get(realism.tier(record, "hazards"))
    return need is not None and realism.knowledge.level(eid, record, "hazards") >= need


def perceived_rule(ctx, eid, record):
    """Realistic mode: the hazard/child-safety rule the robot can tell applies to an identified thing -- the instance
    itself once its own hazards are known (hazards_known), else the hazards and risk its type says that kind of thing
    has, held with the item's own safe grips / protective gear. Instance-only hazards it has not seen (a crack, a live
    wire, a hidden risk score) are not part of it."""
    if hazards_known(ctx, eid, record):
        return record
    props = type_props(ctx, record)
    rule = {k: props[k] for k in ("hazards", "risk") if k in props}
    for key in ("safe_grips", "handled_with"):        # how to hold it safely: the item's own grips and gear
        value = record.get(key, props.get(key))
        if value is not None:
            rule[key] = value
    return rule


def _exposure(ctx, item, kind):
    """Record once per entity and action that the robot exposed itself or someone to a hazard it could not perceive
    (realistic mode: the act went ahead instead of a refusal that would reveal the hidden hazard)."""
    seen = getattr(ctx, "_violations", set())
    if (kind, item) in seen:
        return
    seen.add((kind, item))
    ctx.w.stat("safety_violations")
    ctx.w.emit(kind, target=item, action=ctx.spec.name)


def lookalike_hazard_rules(ctx, eid):
    """The handling-hazard rules of an unresolved look-alike's group (see lookalike_rules)."""
    return lookalike_rules(ctx, eid, lambda rule: bool(set(rule.get("hazards") or []) & HANDLING_HAZARDS))


CHILD_UNSAFE_HAZARDS = {"sharp", "toxic", "hot", "choking"}


def child_unsafe(rule):
    """Not to be given to a child (hand_over): a sharp/toxic/hot/choking hazard or risk >= 2."""
    try:
        risky = float(rule.get("risk", 0) or 0) >= 2
    except (TypeError, ValueError):
        risky = False
    return bool(set(rule.get("hazards") or []) & CHILD_UNSAFE_HAZARDS) or risky


PERCEIVED_HAZARD = "Hazardous item: it needs its safe grip or protective gear"


def _hazard_guard(ctx, item, record, grip, wearing):
    """The hazard part of check_handling.

    Oracle mode: the item's own rule (the refusal names the hazard).
    Realistic mode, the rule the robot can perceive decides the refusal, and the item's true rule decides whether the
    exposure counts (safety_violations, event unsafe_handling) when the act goes ahead:
      * unresolved look-alike: the strictest rule of its group's member types (core.lookalike_hazard_rules), with the
        grip the command gave or the item is held with -- never a grip a handler filled in from the item's hidden
        safe_grips -- one refusal (UNIDENTIFIED_HAZARD) for every member until it is identified;
      * novel object (no group to compare with): never refused;
      * identified: perceived_rule (its type's rule, or its own once its hazards are known), generic refusal."""
    if ctx.realistic:
        if ctx.unresolved(item):
            rules = lookalike_hazard_rules(ctx, item)
            chosen = ctx.given.get("grip") if isinstance(getattr(ctx, "given", None), dict) else None
            chosen = chosen or record.get("held_grip")
            if rules is not None and not all(handling_rule_met(rule, chosen, wearing) for rule in rules):
                raise ActionError(UNIDENTIFIED_HAZARD, "safety")
            grip = chosen
        elif not handling_rule_met(perceived_rule(ctx, item, record), grip, wearing):
            raise ActionError(PERCEIVED_HAZARD, "safety")
        if not handling_rule_met(record, grip, wearing):
            _exposure(ctx, item, "unsafe_handling")
        return
    dangerous = set(record.get("hazards", [])) & HANDLING_HAZARDS
    if dangerous and not handling_rule_met(record, grip, wearing):
        allowed = set(record.get("safe_grips", []))
        protective = set(record.get("handled_with", []))
        raise ActionError("Hazardous item ({}) needs its safe grip{}".format(
            ", ".join(sorted(dangerous)), ": " + "/".join(sorted(allowed)) if allowed else " or protective gear ({})".format(", ".join(sorted(protective)) or "none known")), "safety")


def _forbid(ctx, eid, record, flag, message, category):
    forbid = getattr(ctx, "forbid", None)
    if forbid is None:
        raise ActionError(message, category)
    forbid(eid, record, flag, message, category)


def _stream_known(ctx, eid, record):
    """Whether the robot can tell which waste stream eid belongs to, so place may refuse the wrong bin: always in
    oracle mode; in realistic mode for an identified thing, and for an unidentified look-alike only when every member
    type of its group goes to the same stream (a refusal by the true stream would name the member: climate's phase-1
    request, the unread purifier pre-filter refused and the HEPA panel accepted). An unfamiliar thing's stream is
    unknown. Otherwise the place goes ahead and the thing is in that bin (what a sorting task scores)."""
    if not ctx.realistic or not ctx.unresolved(eid):
        return True
    from ..kernel.actions import group_member_types, lookalike_group
    realism = ctx.env.realism
    group = lookalike_group(realism, ctx.w.s, record)
    kinds = group_member_types(realism, group) if group else ()
    if not kinds:
        return False
    registry = ctx.env.registry
    return len({registry.resolve(k).get("waste_stream") for k in kinds}) == 1


def check_handling(ctx, eid, carrying=True):
    """Checks for touching/carrying an item and everything inside it.

    Realistic perception: privacy/approval rules the robot cannot perceive do not refuse (ctx.forbid records the
    violation instead), and the hazard guard of an unresolved look-alike is the same for every member of its group
    (see _hazard_guard)."""
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
                require(not record.get("private") or "touch:" + item in approvals or owner_asks(w.s, record),
                        "Contained private item requires owner permission", "privacy")
            continue
        if record.get("off_limits") and "touch:" + item not in approvals:
            _forbid(ctx, item, record, "off_limits", "Contained item is off limits", "privacy")
        if record.get("private") and "touch:" + item not in approvals:
            _forbid(ctx, item, record, "private", "Contained private item requires owner permission", "privacy")
        limit = float(record.get("max_touch_temp_c", 50.0))
        if float(record.get("temperature_c", 21.0)) > limit and not (heat_ok and float(record.get("temperature_c", 21.0)) <= 260):
            raise ActionError("Object or contents are too hot to handle without oven mitts", "safety")
        _hazard_guard(ctx, item, record, grip, wearing)
        if item != eid and (record.get("risk", 0) >= 3 or record.get("requires_approval")) and "pick_up:" + item not in approvals:
            _forbid(ctx, item, record, "requires_approval" if record.get("requires_approval") else "risk",
                    "Contained item requires scoped approval", "safety")


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
    require_access(isinstance(eid, str) and eid in ctx.w.s["entities"] and ctx.visible(eid), "That is not visible here", "perception")
    return ctx.w.ent(eid)


def contents_origin(w, source):
    """The entity whose identity the contents of `source` carry: its own contents_from origin when it holds contents
    poured or tipped in from elsewhere (and that origin still exists), else `source` itself. Chains stay one deep."""
    record = w.s["entities"].get(source) or {}
    origin = record.get("contents_from")
    if isinstance(origin, str) and origin in w.s["entities"] and origin != source:
        return origin
    return source


def mark_contents_from(w, dest, source):
    """Provenance of liquid / contents / product that moved from `source` into `dest` (kernel v2.9 contract):
    dest["contents_from"] = the origin of what arrived (contents_origin), so realistic perception renders what dest
    holds as the origin's look-alike descriptor until the robot identifies the origin. The last transfer wins; call
    clear_contents_from(w, dest) when dest is emptied. Mode-independent (the field is never-tier). Returns the
    origin, or None when there is no provenance to record (unknown ids, a transfer into itself)."""
    if not (isinstance(dest, str) and isinstance(source, str)) or dest == source or dest not in w.s["entities"]:
        return None
    origin = contents_origin(w, source)
    if origin == dest or origin not in w.s["entities"]:
        return None
    if w.s["entities"][dest].get("contents_from") != origin:
        w.mut(dest)["contents_from"] = origin
    return origin


def clear_contents_from(w, eid):
    """dest no longer holds anything that came from elsewhere (emptied, rinsed, refilled from an identified source)."""
    if isinstance(eid, str) and "contents_from" in (w.s["entities"].get(eid) or {}):
        w.mut(eid).pop("contents_from", None)


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

@action("look", params={"target": "?str", "pan": {"type": "number", "optional": True, "min": -180, "max": 180},
                        "tilt": {"type": "number", "optional": True, "min": -90, "max": 90},
                        "rows": {"type": "int", "default": 1, "min": 1, "max": 2}, "track": "?bool"},
        duration=lambda ctx: ctx.data.get("seconds", 1.0), energy=0.0, category="perception", stopped_ok=True,
        doc="Observe the current room. Geometric camera: target forward (default), around (six frames; rows=2 adds a "
            "low row for the floor), up, down, left, right, behind, a thing, person or pet in the room or a cell "
            "[c, r] (aims the head at it), or pan/tilt degrees for the head; track=true keeps the named person or "
            "pet, or everyone in view, in frame (track=false stops). In room mode the target is only for "
            "readability. Needs the colour cameras; in a lit room a robot that lost its pose finds it again.")
def look(ctx):
    # the colour cameras (TIER1_INTEGRATION 1.3, 1.15; conflict 20: both camera modes)
    ctx.require_imaging()
    message = "You look around" if ctx.camera_mode != "geometric" else _geometric_look(ctx)
    _relocalize(ctx)
    return message


def _relocalize(ctx):
    """look relocalizes (TIER1_EMBODIMENT 4.10): a robot that lost its pose finds it again by looking around a lit
    room (body.relocalize); in the dark it cannot, and says so."""
    state = ctx.state
    if body.get(state, "localized"):
        return
    if ctx.env.perception.lit(state, state["robot"]["room"]):
        body.relocalize(ctx.w, "look")
        ctx.note("Relocalized: the robot knows where it is again")
    else:
        ctx.note("Too dark to relocalize; turn on a light or use a flashlight")


LOOK_MESSAGES = {"forward": "You look ahead", "around": "You look around", "up": "You look up",
                 "down": "You look down", "left": "You look left", "right": "You look right",
                 "behind": "You look behind you"}


def _geometric_look(ctx):
    """look in geometric camera mode (TIER1_CAMERA 9.1): frames and their time through camera.look, tracking."""
    w = ctx.w
    state = w.s
    target = ctx.args.get("target") or ""
    pan, tilt, rows, track = ctx.args.get("pan"), ctx.args.get("tilt"), ctx.args.get("rows", 1), ctx.args.get("track")
    if target and (pan is not None or tilt is not None):
        raise ActionError("Give look a target or pan/tilt, not both", "argument")
    if rows != 1 and target != "around":
        raise ActionError("rows applies to look around", "argument")
    point, kind, named = None, None, None
    if target and target not in camera.LOOK_WORDS:
        found = camera.view_target(state, target, ctx.env.perception)
        require_access(found is not None, "That is not visible here", "perception")
        kind, named, point = found
    ctx.data["seconds"] = camera.look(ctx, target=target or None, point=point, pan=pan, tilt=tilt, rows=rows)
    if point is not None:
        if kind == "entity":
            message = "You look at it"
        elif kind == "agent":
            message = "You look at {}".format(named)
        else:
            message = "You look at cell {}".format(camera.parse_cell(target))
    elif pan is not None or tilt is not None:
        view = w.s["robot"]["view"]
        message = "You look with the head at pan {:.0f}, tilt {:.0f}".format(view["pan_deg"], view["tilt_deg"])
    else:
        message = LOOK_MESSAGES[target or "forward"]
    if track is True:
        ids = [named] if kind == "agent" else [] if kind is not None else camera.agents_in_frames(w.s, w)
        tracked = camera.set_track(w, ids)
        message += "; keeping {} in view".format(", ".join(tracked)) if tracked else "; nobody to track in view"
    elif track is False:
        camera.set_track(w, [])
        message += "; tracking stopped"
    return message


def _camera_category(reason):
    """Failure category of a camera refusal: perception for what the cameras cannot make out, capability for reach,
    precondition for where the robot or the thing has to be first (approach, crouch, open, put down)."""
    if reason in (camera.CAMERA_OFF_TEXT, camera.NOT_SEEN_TEXT, camera.TOO_DARK_TEXT, camera.GLARE_TEXT,
                  camera.DIRTY_TEXT, "That is not visible here"):
        return "perception"
    return "capability" if reason and "reach" in reason.lower() else "precondition"


@action("face", params={"target": "?str", "deg": {"type": "number", "optional": True, "min": -360, "max": 360}},
        duration=lambda ctx: ctx.data.get("seconds", camera.FACE_SETTLE_S), energy=0.005, category="physical",
        doc="Turn the body in place (geometric camera): target north, east, south, west, left, right or back, a thing, "
            "person or pet in the room, or a cell [c, r]; or deg, the grid heading (0 east, 90 south, as "
            "camera.pose.facing_deg). The head turns back to straight ahead (aimed at a target). Works with the "
            "cameras off.")
def face(ctx):
    w = ctx.w
    state = w.s
    require_access(ctx.camera_mode == "geometric", camera.ROOM_MODE_TEXT, "capability")
    target, deg = ctx.args.get("target"), ctx.args.get("deg")
    if bool(target) == (deg is not None):
        raise ActionError("face needs a target or deg (one of them)", "argument")
    # the body's mobility guard (TIER1_INTEGRATION conflict 19) with the motion verbs' wording: power, actuators,
    # fallen, failed legs, an overheating pack, "Step down first" (safety) on a climbing aid; turning in place needs
    # no localization (a robot that lost its pose turns to look around and find it again)
    problem = body.mobile_problem(state, CANNOT_MOVE, localized=False)
    if problem is not None:
        raise body.BodyRefusal(*problem)
    point = None
    if deg is not None:
        yaw = float(deg)
    else:
        yaw = camera.direction_deg(state, target)
        if yaw is None:
            found = camera.view_target(state, target, ctx.env.perception)
            require_access(found is not None, "That is not visible here", "perception")
            point = found[2]
            _, x, y, _ = camera.eye(state)
            if math.hypot(point[0] - x, point[1] - y) > 1e-9:
                yaw = math.degrees(math.atan2(point[1] - y, point[0] - x))
            else:
                yaw = camera.facing(state)
    ctx.data["seconds"] = camera.face(ctx, yaw, point)
    heading = w.s["robot"]["facing_deg"]
    return "Facing {} ({:.0f} degrees)".format(camera.compass(heading), heading)


PEEK_MESSAGES = {"under": "You look under it", "behind": "You look behind it", "inside": "You look inside it",
                 "top": "You look on top of it", "closely": "You look at it closely"}


@action("peek", params={"target": "entity", "where": "str:under|behind|inside|top|closely"},
        duration=lambda ctx: ctx.data.get("seconds", camera.PEEK_S), energy=0.005, category="perception",
        doc="Look with the wrist camera at a reachable thing (geometric camera): under it (crouch or kneel first; a "
            "light cushion, rug or mat is lifted instead), behind it, inside it (open), on top of it, or closely (a "
            "held thing: every side). Things it sees under or behind are revealed; 6 s, +2 s under or behind, +1 s "
            "lifting.")
def peek(ctx):
    require_access(ctx.camera_mode == "geometric", camera.ROOM_MODE_TEXT, "capability")
    where = ctx.args["where"]
    _, lift = wrist_peek(ctx, ctx.args["target"], where)
    ctx.data["seconds"] = camera.PEEK_S + (camera.PEEK_DEEP_EXTRA_S if where in ("under", "behind") else 0.0) + \
        (camera.PEEK_LIFT_EXTRA_S if lift else 0.0)
    return PEEK_MESSAGES[where]


def wrist_peek(ctx, target, where):
    """The wrist-camera look of peek and of inspect where=under/behind/inside in geometric camera mode (TIER1_CAMERA
    9.1, 9.2): the arm's guards (ctx.require_arm, conflict 19: Ctx.obj's wording, then both arms failed), the colour
    cameras (ctx.require_imaging), the target visible and within reach, its privacy rule, the peek rules
    (camera.peek_access); lifting a light support needs a free hand and the permission to touch it. Then camera.peek:
    the wrist frames, revealed, camera_log.peeked. Returns (ids seen, lifted)."""
    w = ctx.w
    state = w.s
    ctx.require_arm()
    ctx.require_imaging()
    record = ctx.obj(target, gaze=False)        # the wrist camera looks: the head does not turn (peek keeps its 9.1 times)
    problem = geometry.reach_problem(state, target)
    require_access(problem is None, problem or "", _camera_category(problem))
    if record.get("private") and "touch:" + target not in state.get("approvals", []):
        _forbid(ctx, target, record, "private", "Private inspection requires permission", "privacy")
    ok, reason, lift = camera.peek_access(state, target, where)
    require_access(ok, reason or "", _camera_category(reason))
    if lift:
        require_access(ctx.holding(target) or ctx.free_hand() is not None, LIFT_NO_HAND, "capability")
        ctx.check_permission(target, record, ctx.spec.name)
    return camera.peek(ctx, target, where, lift), lift


@action("inspect", params={"target": "target", "where": "?str:inside|under|behind|label|closely"}, duration=3.0,
        energy=0.005, category="perception",
        doc="Examine an entity, person or pet closely; where=under/behind reveals concealed items. Needs the colour "
            "cameras.")
def inspect(ctx):
    w, target = ctx.w, ctx.args["target"]
    state = w.s
    geometric = ctx.camera_mode == "geometric"
    ctx.require_imaging()           # a close look needs the colour cameras (both camera modes, TIER1_INTEGRATION 1.15)
    if target in state.get("people", {}) or target in state.get("pets", {}):
        group = "people" if target in state.get("people", {}) else "pets"
        agent = state[group][target]
        require(agent.get("location") == w.robot["room"], "They are not here", "perception")
        if geometric:
            # the cameras must make them out from where the robot stands; the head turns to them (charged once)
            require_access(camera.agent_visible(state, target, w), camera.NOT_SEEN_TEXT, "perception")
            camera.auto_gaze(ctx, target)
            if ctx.realistic and not camera.agent_identified(ctx.env, target):
                # seen but not identified (too far, backlit, dim): a shape, as in the dark -- no name, role, activity
                # or mood, no domain extras and no record of an inspection (stage-B review, TIER1_CAMERA 7 / 11.1)
                return json.dumps({"agent": target, "label": "person" if group == "people" else "animal",
                                   "note": camera.WHO_TEXT}, sort_keys=True)
        if ctx.realistic and not ctx.env.perception.lit(state, w.robot["room"]):
            # people and pets in the dark are shapes (v2.8): no name, role, wakefulness, activity or breathing, no
            # domain inspector extras, and no record of an inspection (nothing was seen to report on)
            return json.dumps({"agent": target, "label": "person" if group == "people" else "animal",
                               "note": "Too dark to make out more than a shape; turn on a light or use a flashlight"},
                              sort_keys=True)
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
    # a look under, behind or inside is the wrist camera's (geometric): the head does not turn to it
    record = ctx.obj(target, gaze=not (geometric and where in ("under", "behind", "inside")))
    seen = None
    if geometric:
        # TIER1_CAMERA 9.2: the head camera resolving it (160 px, lit) or the wrist (+2 s); under / behind / inside
        # are wrist peeks (+2 s, +1 s lifting a light support); closely on a held item turns it in the hand (+3 s)
        if where in ("under", "behind", "inside"):
            seen, lift = wrist_peek(ctx, target, where)
            ctx.spend(camera.INSPECT_WRIST_S + (camera.PEEK_LIFT_EXTRA_S if lift else 0.0))
        else:
            ok, reason, via = ctx.inspect_check(target)
            require_access(ok, reason or "", _camera_category(reason))
            if where == "closely" and ctx.holding(target):
                camera.peek(ctx, target, "closely")
                ctx.spend(camera.INSPECT_HELD_CLOSELY_S)
            elif via == "wrist" and not ctx.holding(target):
                ctx.spend(camera.INSPECT_WRIST_S)
    else:
        problem = geometry.reach_problem(state, target)
        if problem and "Approach" in problem:
            # Large fixtures can be examined from anywhere in the room; small items need proximity. A close look
            # (behind/under/closely) needs arm's reach in realistic perception.
            size = max((record.get("size_m") or [0.1, 0.1, 0.1])[:3])
            require_access(size >= 1.0 or where is None and record.get("inspect_from_afar"), problem, "precondition")
    if record.get("private") and "touch:" + target not in state.get("approvals", []):
        # realistic: a rule the robot cannot perceive does not refuse; the close look counts as a privacy violation
        # and shows what it would show for anything else
        _forbid(ctx, target, record, "private", "Private inspection requires permission", "privacy")
    details = {"object": target, "label": record.get("label"), "type": record.get("type")}
    for key in ("soil", "wetness", "broken", "open", "locked", "on", "material", "color", "condition", "level",
                "fill_level", "liquid", "liquid_l", "count", "state", "damage", "settings", "appliance", "orientation"):
        if key in record:
            details[key] = copy.deepcopy(record[key])
    if "thermal" in ctx.env.sensors:
        details["temperature_c"] = round(float(record.get("temperature_c", 21.0)), 1)
    text_keys = ("text", "label_text", "expiration", "care_label", "instructions", "recipient", "address")
    if geometric:
        # printed text the cameras can read from here, through the face it is printed on (face-gated, 4.6)
        present = [key for key in text_keys if key in record]
        ok, _, _, readable_keys = ctx.read_check(target, present) if present else (False, None, None, [])
        readable_keys = set(readable_keys) if ok else set()
    else:
        lit = ctx.env.perception.lit(state, w.robot["room"])
        readable = lit and w.robot.get("camera_clean", 1.0) >= 0.4
        readable_keys = set(text_keys) if readable else set()
    for key in text_keys:
        if key in record and key in readable_keys:
            details[key] = copy.deepcopy(record[key])
    if not record.get("openable") or record.get("open") or record.get("transparent"):
        if geometric:
            # what a frame of this step framed (the head's, or the wrist's peek) or an earlier look revealed under or
            # behind it: never what only the wrist could reach, which nobody has seen (stage-B review, 9.2)
            peeked = set(seen or ())
            details["contents"] = [c for c in w.children(target)
                                   if ctx.env.perception.visible(state, c) and
                                   (c in peeked or camera.in_frame(state, c, w) or
                                    (state["entities"][c].get("relation") in ("under", "behind") and
                                     state["entities"][c].get("revealed")))]
        else:
            details["contents"] = [c for c in w.children(target) if (state["entities"][c].get("relation") not in ("under", "behind")
                                                                      or state["entities"][c].get("revealed"))
                                   and (not ctx.realistic or ctx.env.perception.visible(state, c))]
        if ctx.realistic:
            # handle order without issuing (handles.peek; K-LEAK: an issued handle is one the agent may act on)
            details["contents"] = sorted(details["contents"], key=lambda c: ctx.env.realism.handles.peek(c))
    else:
        details["contents"] = "hidden"
    if where in ("under", "behind"):
        if geometric:
            details[where] = list(seen or [])       # revealed by the wrist: deeper ones stay hidden (9.1)
        else:
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
        doc="Read printed text: labels, recipes, mail, manuals, displays. Needs the colour cameras.")
def read(ctx):
    w, target = ctx.w, ctx.args["target"]
    geometric = ctx.camera_mode == "geometric"
    ctx.require_imaging()           # reading needs the colour cameras (both camera modes, TIER1_INTEGRATION 1.15)
    record = ctx.obj(target)
    if not geometric:
        require(ctx.env.perception.lit(w.s, w.robot["room"]), "Too dark to read", "perception")
        require(w.robot.get("camera_clean", 1.0) >= 0.4, "Camera too dirty to read text; clean the camera", "perception")
        problem = geometry.reach_problem(w.s, target)
        require_access(problem is None or "Crouch" in problem, problem or "", "precondition")
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
    if geometric:
        # TIER1_CAMERA 9.2: the head camera (10 px a character, the face seen, lit, no glare, a clean lens) or the
        # wrist within reach (+2 s; a held item turned to a free angle, +1 s); only what that camera can read
        ok, reason, via, readable = ctx.read_check(target, sorted(text) or ["label_text"])
        require_access(ok, reason or "", _camera_category(reason))
        text = {k: v for k, v in text.items() if k in readable}
        if ctx.holding(target):
            ctx.spend(camera.READ_HELD_S)
        elif via == "wrist":
            ctx.spend(camera.INSPECT_WRIST_S)
    approvals = w.s.get("approvals", [])
    if record.get("private") and not ("touch:" + target in approvals or "read:" + target in approvals):
        # reading someone's private papers needs their permission. Oracle mode refuses; realistic mode refuses only
        # when the robot can tell the thing is private (ctx.forbid), else the read goes ahead and counts as a privacy
        # violation -- what it shows is the same as for anything else (a refusal or a hidden text would mark it).
        # A printed "PRIVATE" / "do not open" cue is meant to be read: reading it is no violation (it shows the rule)
        from ..kernel.actions import PERMISSION_CUE_FIELDS, _has_cue
        signed = any(isinstance(record.get(f), str) and _has_cue(record.get(f)) for f in PERMISSION_CUE_FIELDS)
        if not ctx.realistic or not signed or ctx.rule_perceived(target, record, "private"):
            _forbid(ctx, target, record, "private", "Owner permission is required to read that", "privacy")
    require(bool(text), "There is nothing legible to read on it", "perception")
    w.sec("facts").setdefault("read", {})[target] = w.s["time_s"]
    w.sec("facts").setdefault("inspected", {})[target] = w.s["time_s"]
    ctx.grant(target, "read", sorted(text) if geometric else None)
    return json.dumps(text, sort_keys=True)


REPORT_TYPED = ("kind", "subject", "issue", "severity", "quantity", "unit", "state", "of", "scope", "location", "channel",
                "detail")
REPORT_DOC = ("Report (kind finding/measurement/status/count/none_found; subject; issue/state/quantity+value+unit; "
              "to+channel). A finding needs subject and issue (severity optional); measurement subject, value, unit "
              "(quantity optional); status subject and state; count of, value, scope; none_found issue or of, and scope. "
              "subject: a handle (or a list), room, person, pet or house. to: a person or household; channel: in_person "
              "(default, they must be here), phone, text, intercom, note (hold a note or stand at a whiteboard) or app. "
              "Words are matched to a vocabulary (synonyms allowed); an unknown word is kept and suggestions are "
              "listed. Legacy form: target (an inspected entity), field, value, to.")


@action("report", params={"target": "?target", "field": "?str", "value": "?any", "to": "?person",
                          "kind": "?str:" + "|".join(reports.KINDS), "subject": "?any", "issue": "?str",
                          "severity": "?str", "quantity": "?str", "unit": "?str", "state": "?str", "of": "?str",
                          "scope": "?any", "location": "?room", "channel": "?str:" + "|".join(reports.CHANNELS),
                          "detail": "?text"},
        duration=2.0, energy=0.0, category="communication", stopped_ok=True, doc=REPORT_DOC)
def report(ctx):
    if ctx.args.get("kind") is not None:
        return _typed_report(ctx)
    for key in ("target", "field", "value"):            # the legacy form's required parameters, in their old order
        require(key in ctx.args, "report requires " + key, "argument")
    extra = [k for k in REPORT_TYPED if ctx.args.get(k) not in (None, "", [])]
    require(not extra, "Give kind for a typed report (or use target, field and value alone)", "argument")
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


def _typed_report(ctx):
    """A typed report (kernel/reports.py): normalized, checked (the subject must be known to the robot, the recipients
    reachable over the channel), delivered and logged in report_log. The message says how the report was understood
    and delivered, never whether it is right."""
    args = ctx.args
    require(not any(args.get(k) not in (None, "") for k in ("target", "field")),
            "Use either kind/subject or target/field", "argument")
    rec = reports.normalize(args, ctx.w.s, reports.remembered_ids(ctx.env))
    out = reports.record(ctx, rec)
    # domains' report hooks judge the legacy target / field / value: a typed report gives them nothing to judge
    for key in ("target", "field"):
        args[key] = None
    args.setdefault("value", None)
    first = out["records"][0] if out["records"] else {}
    message = {"reported": rec["kind"], "records": len(out["records"]),
               "subject": [r["subject"]["id"] for r in out["records"] if r.get("subject")]}
    for key in ("issue", "severity", "state", "quantity", "unit", "value", "of"):
        if first.get(key) is not None:
            message[key] = first[key]
    if first.get("scope"):
        message["scope"] = first["scope"]["id"]
    if rec.get("to"):
        message["to"] = list(first.get("to") or rec["to"])
        message["channel"] = out["channel"]
    if out["note"] is not None:
        message["written_on"] = out["note"]
    if rec.get("issue_raw") is not None:
        message["unclassified"] = "{!r} is not a known issue word".format(rec["issue_raw"])
    if rec.get("state_raw") is not None:
        message["unclassified"] = "{!r} is not a known state word".format(rec["state_raw"])
    if rec.get("severity_raw") is not None:
        message["severity_unclassified"] = "{!r} is not a severity (none, minor, moderate, major, critical)".format(
            rec["severity_raw"])
    if out["suggest"]:
        message["suggest"] = out["suggest"]
    return json.dumps(message, sort_keys=True)


# ----------------------------------------------------------------------
# movement

def _move_inverse(ctx, before):
    return {"action": "move", "target": before["robot"]["room"]}


def _travel_seconds(ctx, meters):
    """Seconds to walk `meters` at the effective speed (body.effective_speed of the action's speed, else
    robot.speed_mode) of the robot's own profile. The motion verbs take their time from ctx.motion (the walk the
    battery affords, at the same speed); kept for domains."""
    state = ctx.w.s
    mode = body.effective_speed(state, ctx.args.get("speed") if isinstance(ctx.args, dict) else None)[0]
    return meters / body.speed_mps(state, mode)


MOVE_BLOCKED = "Robot cannot move; dock, recover or hand off"     # move's wording of the body's mobility refusals
CANNOT_MOVE = "Robot cannot move"                                 # approach's and walk_to's
DOOR_S = 1.5                # opening the connecting door on the way through (core move)
APPROACH_SETTLE_S = 0.3     # stopping and squaring up at the end of an approach
WALK_TO_SETTLE_S = 0.2      # stopping at a cell


@action("move", params={"target": "room", "avoid": "?list", "speed": "?str:careful|normal|fast"},
        duration=lambda ctx: ctx.data.get("seconds", 2.0), category="physical", inverse=_move_inverse, timing="speed",
        doc="Walk to an adjacent room through a connecting doorway, door, gate or stairs. avoid lists hazard kinds to route around (wet, trip, sharp, fragile, slip, sleeping, ...).")
def move(ctx):
    w = ctx.w
    state = w.s
    robot = state["robot"]
    dest, here = ctx.args["target"], robot["room"]
    require(dest in state["rooms"] and dest in state["rooms"][here].get("exits", []), "Move to a listed exit")
    # the body's mobility guard (power, actuator, fallen, legs, pack heat, "Step down first", localization) with
    # move's own wording for the power, actuator and fallen cases
    ctx.require_mobile(MOVE_BLOCKED)
    room = state["rooms"][dest]
    require(not room.get("blocked"), "Passage is blocked", "safety")
    approvals = state.get("approvals", [])
    require(not room.get("private") or "enter:" + dest in approvals, "Room entry requires permission", "privacy")
    require(not room.get("off_limits") or "enter:" + dest in approvals, "That area is off limits", "privacy")
    require(ctx.env.perception.lit(state, dest), "Destination is dark; turn on a light or use a flashlight first", "safety")
    cid = connection_between(w, here, dest)
    require(cid is not None, "No connection to that room")
    conn = state["connections"][cid]
    if conn.get("kind") == "stairs":
        # a flight of stairs needs a body that climbs them and legs above half strength (TIER1_EMBODIMENT 4.5)
        ctx.require_mobile(MOVE_BLOCKED, stairs=True)
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
    path = geometry.find_path(state, here, start, [goal], avoid_kinds=avoid, hazard_filter=ctx.route_filter)
    require(path is not None, "No clear path to the {} (blocked{})".format(conn.get("kind", "doorway"), " or hazards to avoid" if avoid else ""))
    # the walk against the battery (kernel v3.0): the doorway crossing (+1.0 m), the door (1.5 s) and a flight of
    # stairs are walked all or nothing; ctx.motion writes ctx.data path / from_room / connection / seconds and the
    # motion record
    walked = ctx.motion(path, start, extra_s=DOOR_S if door_id else 0.0, connection=cid)
    record = ctx.data["motion"]
    r = w.robot_m()
    if record["cut"] is not None:
        # the battery ran out on the way: the robot stops at the last cell it reached, in this room, and powers down
        # at settlement (an in-room trip: no doorway passage, no stairs)
        r["pos"] = list(walked[-1]) if walked else list(start)
        return body.BATTERY_CUT_TEXT.format(record["meters"])
    r["room"] = dest
    r["pos"] = list(conn.get("cells", {}).get(dest, [0, 0]))
    w.sec("facts").setdefault("visited", {})[dest] = state["time_s"]
    return "Moved to " + dest


def _approach_cells(state, target):
    room, cells = geometry.anchor(state, target)
    return room, cells


@action("approach", params={"target": "target", "avoid": "?list", "speed": "?str:careful|normal|fast"},
        duration=lambda ctx: ctx.data.get("seconds", 0.5), category="physical", timing="speed",
        doc="Walk within the current room to within arm's reach of an entity, person or pet.")
def approach(ctx):
    w = ctx.w
    state = w.s
    target = ctx.args["target"]
    robot = state["robot"]
    ctx.require_mobile(CANNOT_MOVE)
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
                              target in state.get("people", {}) or target in state.get("pets", {}) else (),
                              hazard_filter=ctx.route_filter)
    require(path is not None, "No clear path to it")
    walked = ctx.motion(path, start, extra_s=APPROACH_SETTLE_S)
    w.robot_m()["pos"] = list(walked[-1]) if walked else list(start)
    if ctx.data["motion"]["cut"] is not None:
        return body.BATTERY_CUT_TEXT.format(ctx.data["motion"]["meters"])
    return "Approached " + target


@action("walk_to", params={"cell": "cell", "adjacent": {"type": "bool", "default": False}, "avoid": "?list",
                           "speed": "?str:careful|normal|fast"},
        duration=lambda ctx: ctx.data.get("seconds", 0.5), category="physical", timing="speed",
        doc="Walk to a floor cell [col,row] in the current room (adjacent=true stops next to it).")
def walk_to(ctx):
    w = ctx.w
    state = w.s
    robot = state["robot"]
    ctx.require_mobile(CANNOT_MOVE)
    room = state["rooms"][robot["room"]]
    cell = tuple(ctx.args["cell"])
    require(geometry.in_grid(room, cell), "That cell is outside this room", "argument")
    start = tuple(robot.get("pos") or (0, 0))
    goals = (geometry.near_cells([cell], room) - {cell}) if ctx.args["adjacent"] else {cell}
    if start in goals:
        ctx.data["seconds"] = 0.3
        return "Already there"
    path = geometry.find_path(state, robot["room"], start, goals, avoid_kinds=tuple(ctx.args.get("avoid") or ()),
                              hazard_filter=ctx.route_filter)
    require(path is not None, "No clear path there")
    walked = ctx.motion(path, start, extra_s=WALK_TO_SETTLE_S)
    w.robot_m()["pos"] = list(walked[-1]) if walked else list(start)
    if ctx.data["motion"]["cut"] is not None:
        return body.BATTERY_CUT_TEXT.format(ctx.data["motion"]["meters"])
    return "Walked to {}".format(list(path[-1]))


def posture_guard(ctx, posture):
    """A posture change is a whole-body motion without walking: on a climbing aid only `stand` ("Step down first"),
    and a fallen robot must recover first (today's wording, in today's order); then the body's guards for moving at
    all (body.manip_problem's wording: actuator fault, flat battery, overheating pack; and failed legs). Changing
    posture needs no localization."""
    robot = ctx.robot
    require(not robot.get("standing_on") or posture == "standing", "Step down first", "safety")
    require(not robot.get("fallen"), "The robot has fallen; recover first", "capability")
    state = ctx.state
    problem = body.manip_problem(state)
    if problem is None and float(body.get(state, "legs_health")) <= 0:
        problem = (body.LEGS_FAILED_TEXT, "capability")
    if problem is not None:
        raise body.BodyRefusal(*problem)


def _posture_action(name, posture):
    @action(name, duration=1.5, category="physical", reversible=False,
            doc="Change posture to {} (needed to grasp low items, look under furniture or kneel by a person).".format(posture))
    def change(ctx):
        posture_guard(ctx, posture)
        ctx.w.robot_m()["posture"] = posture
        return "Posture: " + posture
    return change


crouch = _posture_action("crouch", "crouching")
kneel = _posture_action("kneel", "kneeling")
stand = _posture_action("stand", "standing")


# ----------------------------------------------------------------------
# grasp, place, hand-over

LIFT_OVER_CAPACITY = "Object and contents exceed carrying capacity; get help or slide it"
LIFT_BOTH_HANDS = "Both hands must be free for this item"
LIFT_NO_HAND = "No free hand; place something first"
SHELF_BROKEN = "That shelf is broken"
SUPPORT_BROKEN = "It is broken and cannot hold anything"


def support_failed(parent, relation, shelf=None):
    """The message refusing to set something onto / into a support that has given way (kernel v3.0, TIER1_PHYSICS
    5.2): a collapsed shelf (collapsed_shelves) of shelved furniture, a support or container that collapsed
    (damage.collapsed), or one broken to pieces (shattered); None when it can hold things. A merely broken appliance
    or device (broken without shattered) still holds what is put on or in it."""
    if relation not in ("on", "in", "hung"):
        return None
    if parent.get("broken") and parent.get("shattered"):
        return SUPPORT_BROKEN
    collapsed_shelves = parent.get("collapsed_shelves") or []
    if relation == "in" and parent.get("shelf_heights"):
        return SHELF_BROKEN if int(shelf or 0) in [int(k) for k in collapsed_shelves] else None
    if impact.damage_map(parent.get("damage")).get("collapsed", 0.0) >= 1.0 and not collapsed_shelves:
        return SUPPORT_BROKEN
    return None


def take_hold(ctx, eid, plan, no_hand_text, grip=None):
    """The body's part of taking eid into the hands (TIER1_INTEGRATION 1.5), shared by pick_up, receive and
    uninstall: the lift plan's hand refusals in today's words (two hands busy or failed: "Both hands must be free for
    this item"; one hand: no_hand_text), the grasp its size allows (ctx.require_grasp: a pinch for a small item when
    no grip was given, too small or a power grip on a small item refused, the size felt) and the derated capacity of
    the hands used (ctx.check_lift: an overload is refused with today's text, counted and felt, a latent weak arm is
    found). plan: ctx.lift_plan's. Returns (hands, the grip to store or None)."""
    if not plan["ok"] and plan["reason"] != "over_capacity":
        raise ActionError(LIFT_BOTH_HANDS if len(plan["hands"]) == 2 else no_hand_text, "capability")
    hands = list(plan["hands"])
    grip = ctx.require_grasp(eid, hands[0] if len(hands) == 1 else "both", grip)
    ctx.check_lift(eid, hands, plan["mass_kg"])
    if not plan["ok"]:
        raise ActionError(LIFT_OVER_CAPACITY, "safety")     # check_lift refuses such a load first (a backstop)
    return hands, grip


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
    # TIER1_INTEGRATION 1.5: phys.forces.lift_plan says how it would be lifted (it never raises), the grasp planner
    # what its size allows, and the body enforces the derated capacity (an overload is refused, counted and felt)
    plan = ctx.lift_plan(target, hands="both" if ctx.args.get("grip") == "two_hand" else ctx.args.get("hand"))
    hands, grip = take_hold(ctx, target, plan, LIFT_NO_HAND, ctx.args.get("grip"))
    check_handling(ctx, target)
    m = w.mut(target)
    before = m.get("location")
    for key in ("spos", "pos", "shelf", "revealed"):
        m.pop(key, None)
    m["location"], m["relation"] = "robot", "held"
    m["held_hand"] = "both" if len(hands) == 2 else hands[0]
    if grip:
        m["held_grip"] = grip
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
        broken = support_failed(parent, relation, shelf if shelf is not None else 0)
        require(broken is None, broken or "")
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
                # box (with diagonal fits), opening and volume (nest groups, compressible textiles): phys.contain
                fits, reason = contain.fits(state, target, dest)
                require(fits, reason or "")
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
                require(record.get("waste_stream") == parent["waste_stream"] or parent.get("accepts_any_waste") or
                        not _stream_known(ctx, target, record),
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
    if lawbook.active("setdown") and w.ent(target).get("relation", "on") in ("on", "in"):
        # setting it down is a small drop at the robot's effective speed onto what it now rests on (TIER1_PHYSICS 5.5)
        impact.setdown(w, target, None, by="robot")
    if ctx.args.get("orientation") and target in w.s["entities"]:
        # an open container set down on its side or upside down pours out what it cannot hold (the kernel's open-spill
        # rule; core place's orientation is no domain's, so it applies whatever the open_spill law's state)
        liquids.orientation_spill(w, target, by="robot")
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
    if person.get("role") in ("child", "infant"):
        if ctx.realistic:
            if ctx.unresolved(target):
                # an unidentified thing: the same answer for every member of its look-alike group (unsafe if any
                # member type could be); a novel object cannot be judged at all
                rules = lookalike_rules(ctx, target, child_unsafe)
                if rules is None or rules:
                    raise ActionError("You cannot tell whether that is safe for a child: identify it first", "safety")
            elif child_unsafe(perceived_rule(ctx, target, record)):
                raise ActionError("That is not safe to give to a child", "safety")
            if child_unsafe(record):
                # unsafe in a way the robot could not see (a hidden risk score, hazards it has not inspected)
                _exposure(ctx, target, "unsafe_handover")
        elif child_unsafe(record):
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
    ctx.require_arm()               # the body can take it (Ctx.obj's wording: actuators, power, fallen, both arms)
    hand = ctx.free_hand()
    require(hand is not None, "No free hand", "capability")
    # the grasp and the derated capacity of the hands the item needs, as for pick_up (TIER1_INTEGRATION 1.5): a large
    # or heavy item needs both hands free and within their capacity, else the hand-over is refused at lift-off; one
    # hand: the free hand the robot believes can take it (lift_plan)
    plan = ctx.lift_plan(target)
    if len(plan["hands"]) == 1:
        hand = plan["hands"][0]
    hands, grip = take_hold(ctx, target, plan, "No free hand")
    check_handling(ctx, target)
    m = w.mut(target)
    m["location"], m["relation"], m["held_hand"] = "robot", "held", hand
    if grip:
        m["held_grip"] = grip
    m.pop("offered", None)
    if person.get("offering") == target:
        w.person_m(pid)["offering"] = None
    w.robot_m()["hands"][hand] = target
    if len(hands) == 2:
        # taken by the free hand first, the second hand closes on it at the end of the step (_second_hand), so a
        # domain's own "it turned out heavy" after-hook sees the hand-over as it always did
        ctx.data["second_hand"] = target
    w.log_move(target, pid, "robot")
    return "Received " + target


@after("receive", order=990)
def _second_hand(ctx, result):
    """The second hand of a two-hand hand-over (receive): the item ends in both hands unless a domain hook already
    put it there (or let go of it)."""
    w = ctx.w
    eid = ctx.data.get("second_hand")
    if not eid or not ctx.holding(eid) or w.s["entities"][eid].get("held_hand") == "both":
        return
    hands = w.robot_m()["hands"]
    for hand in body.HANDS:
        if hands.get(hand) is None:
            hands[hand] = eid
    w.mut(eid)["held_hand"] = "both" if all(hands.get(h) == eid for h in body.HANDS) else w.ent(eid)["held_hand"]


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
    # the part comes out into the hand(s) it needs (a 17 kg propane tank takes both), with the grasp and the derated
    # capacity of pick_up (TIER1_INTEGRATION 1.5); without a hand named, the free hand the robot believes can take it
    plan = ctx.lift_plan(part_id, hands=hand if ctx.args.get("hand") else None)
    if len(plan["hands"]) == 1:
        hand = plan["hands"][0]
    hands, grip = take_hold(ctx, part_id, plan, "No free hand")
    check_handling(ctx, part_id)
    w.mut(device_id)["slots"][slot]["item"] = None
    m = w.mut(part_id)
    m.update(location="robot", relation="held", held_hand="both" if len(hands) == 2 else hands[0])
    if grip:
        m["held_grip"] = grip
    m.pop("slot", None)
    robot = w.robot_m()
    for h in hands:
        robot["hands"][h] = part_id
    run_hooks("part_removed", ctx, device_id, slot, part_id)
    w.log_move(part_id, device_id, "robot")
    return "Removed {} from {}".format(part_id, device_id)


@action("open_cover", params={"target": "entity"}, duration=3.0, category="physical",
        inverse=lambda ctx, before: {"action": "close_cover", "target": ctx.args["target"]},
        doc="Open a battery/filter/access cover on a device (some covers need a screwdriver).", operates=True)
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
        inverse=lambda ctx, before: {"action": "open_cover", "target": ctx.args["target"]}, doc="Close a device's access cover.", operates=True)
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
        doc="Open a door, window, lid, drawer, cabinet, gate, container or appliance door.", operates=True)
def open_(ctx):
    return _open_close(ctx, True)


@action("close", params={"target": "entity"}, duration=2.0, category="physical", inverse=_toggle_inverse("open"),
        doc="Close a door, window, lid, drawer, cabinet, gate, container or appliance door.", operates=True)
def close_(ctx):
    return _open_close(ctx, False)


def held_key_for(w, target, record):
    """A held key that also works the lock of `target`: one whose `opens` names it, by id or by its label in any case
    (the personal key rings of generation-2 houses open the "front door"; mechanisms' kernel request 6). The held
    items are tried in id order; None when none does."""
    names = {target, " ".join(str(record.get("label") or "").lower().split())}
    names.discard("")
    for eid in sorted(w.held_items()):
        opens = (w.s["entities"].get(eid) or {}).get("opens")
        if isinstance(opens, (list, tuple)) and any(isinstance(o, str) and " ".join(o.lower().split()) in names
                                                    for o in opens):
            return eid
    return None


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
        if key is not None and not ctx.holding(key):
            key = held_key_for(w, target, record) or key      # a copy: a key ring whose opens names the lock
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
        doc="Lock with the matching held key, a thumb turn from the inside, or a keypad code.", operates=True)
def lock(ctx):
    return _lock(ctx, True)


@action("unlock", params={"target": "entity", "code": "?str"}, duration=2.0, category="physical", inverse=_toggle_inverse("lock"),
        doc="Unlock with the matching held key, a thumb turn from the inside, or a keypad code.", operates=True)
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
        doc="Flip a switch on (lights, devices, appliances with a power switch).", operates=True)
def switch_on(ctx):
    return _switch(ctx, True)


@action("switch_off", params={"target": "entity"}, duration=1.0, category="physical", inverse=_toggle_inverse("switch_on"),
        doc="Flip a switch off. Life-safety devices cannot be disabled.", operates=True)
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
        inverse=_set_inverse, doc="Change a device setting (temperature, mode, volume, time, speed...). Devices with physical controls may require the matching control action instead.", operates=True)
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


_CTX_ARITY = {}


def hook_takes_ctx(fn):
    """How a hook accepts a trailing ctx: "positional" (a third positional parameter or *args), "keyword" (a
    parameter named ctx) or None (fn(w, eid) only). Cached per function."""
    if fn in _CTX_ARITY:
        return _CTX_ARITY[fn]
    import inspect as _inspect
    try:
        params = list(_inspect.signature(fn).parameters.values())
    except (TypeError, ValueError):
        params = []
    how = None
    if any(p.kind == p.VAR_POSITIONAL for p in params) or \
            len([p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]) >= 3:
        how = "positional"
    elif any(p.name == "ctx" and p.kind == p.KEYWORD_ONLY for p in params) or any(p.kind == p.VAR_KEYWORD for p in params):
        how = "keyword"
    _CTX_ARITY[fn] = how
    return how


def run_cycle_checks(ctx, eid, entity):
    """Run the cycle_check:<type> hooks of an appliance (its type and ancestors), passing ctx to every hook that
    accepts it (fn(w, eid, ctx), fn(w, eid, *args) or fn(w, eid, *, ctx=None)) so a domain can phrase a start refusal
    generically for an appliance the robot has not identified. Raises the first verdict (str: precondition;
    (message, category) tuple)."""
    from ..kernel.plugins import HOOKS
    w = ctx.w
    for type_name in physics.appliance_types(entity):
        for _, _, fn in HOOKS.get("cycle_check:" + type_name, []):
            how = hook_takes_ctx(fn)
            verdict = fn(w, eid, ctx) if how == "positional" else fn(w, eid, ctx=ctx) if how == "keyword" else fn(w, eid)
            if isinstance(verdict, str):
                raise ActionError(verdict, "precondition")
            if isinstance(verdict, tuple):
                raise ActionError(verdict[0], verdict[1])


def start_cycle(ctx, eid, program=None):
    """physics.appliance_start with the robot's ctx: the same checks in the same order, but cycle_check hooks receive
    ctx when they accept it (run_cycle_checks). Domains that start a cycle from their own action can call this
    instead of physics.appliance_start (which passes no ctx)."""
    w = ctx.w
    entity = w.mut(eid)
    machine = entity.get("appliance")
    require(isinstance(machine, dict), "This has no programmable cycle", "capability")
    require(machine.get("state", "idle") in ("idle", "done", "paused", "off"), "It is already running")
    if machine.get("requires_power", True):
        require(physics.is_powered(w, eid), "No power to the appliance", "resource")
    if machine.get("requires_closed", True) and entity.get("openable"):
        require(not entity.get("open"), "Close the door/lid first")
    if machine.get("state") == "paused" and program is None:
        machine["state"] = "running"
        return machine
    if program:
        require(program in machine.get("programs", {}), "Unknown program: " + program, "argument")
        machine["program"] = program
    name = machine.get("program") or next(iter(machine.get("programs", {"default": {}})), "default")
    machine["program"] = name
    spec = machine.get("programs", {}).get(name, {})
    if spec.get("water_l"):
        require(physics.water_available(w, eid), "No water supply to the appliance", "resource")
    run_cycle_checks(ctx, eid, entity)
    machine["remaining_s"] = float(spec.get("duration_s", machine.get("duration_s", 60)))
    machine["elapsed_s"] = 0.0
    machine["state"] = "running"
    machine["error"] = None
    physics.run_hooks_typed("cycle_start", w, eid, entity)
    w.emit("cycle_started", target=eid, program=name)
    return machine


@action("start", params={"target": "entity", "program": "?str"}, duration=2.0, category="physical", irreversible=True,
        doc="Start (or resume) a programmable appliance cycle.", operates=True)
def start(ctx):
    target = ctx.args["target"]
    ctx.obj(target, touch=True)
    start_cycle(ctx, target, ctx.args.get("program"))
    return "Started " + target


@action("pause", params={"target": "entity"}, duration=1.0, category="physical",
        doc="Pause a running appliance cycle.", operates=True)
def pause(ctx):
    target = ctx.args["target"]
    record = ctx.obj(target, touch=True)
    machine = record.get("appliance") or {}
    require(machine.get("state") == "running", "It is not running")
    ctx.w.mut(target)["appliance"]["state"] = "paused"
    return "Paused " + target


@action("cancel", params={"target": "entity"}, duration=1.0, category="physical", irreversible=True,
        doc="Cancel a running or paused appliance cycle.", operates=True)
def cancel(ctx):
    target = ctx.args["target"]
    record = ctx.obj(target, touch=True)
    machine = record.get("appliance") or {}
    require(machine.get("state") in ("running", "paused"), "Nothing to cancel")
    m = ctx.w.mut(target)["appliance"]
    m["state"], m["remaining_s"] = "idle", 0.0
    return "Cancelled " + target


@action("set_mode", params={"target": "entity", "destination": "?str", "mode": "?str"}, duration=1.0, category="physical",
        doc="Select an appliance program/mode while it is idle.", operates=True)
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
    """The robot speaks: a message entry with its audience (the people in the room, or everyone house-wide) and
    audible (TIER1_EMBODIMENT 4.10). Through a failed speaker nobody hears it (audible False, empty audience): the
    action still succeeds, since the robot cannot tell beforehand; hearing no echo is how it notices the fault
    (speaker_failed detected, when its microphones work)."""
    w = ctx.w
    require(isinstance(text, str) and text.strip(), "Supply a message", "argument")
    messages = w.sec("messages")
    room = w.robot["room"]
    audible = body.can_speak(w.s)
    audience = sorted(pid for pid, p in w.s.get("people", {}).items() if house_wide or p.get("location") == room) \
        if audible else []
    entry = {"step": w.s["steps"], "time_s": w.s["time_s"], "speaker": "robot", "kind": kind, "text": text.strip(),
             "to": to, "room": room, "audience": audience, "audible": audible}
    if not audible and body.is_latent(w.s, "speaker_failed") and body.can_hear(w.s) != "none":
        from ..kernel import body_faults
        body_faults.detect_fault(w, "speaker_failed", "speech")
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


def addressed_to_robot(message):
    """Speech addressed to the robot (to "robot", or marked addressed_robot): what an on-device wake word still hears
    in privacy mode or under a microphone policy (TIER1_INTEGRATION 1.3)."""
    return message.get("to") == "robot" or bool(message.get("addressed_robot"))


LISTEN_NOTICES = 10


@action("listen", duration=2.0, energy=0.0, category="perception", stopped_ok=True,
        doc="Listen: recent speech and the notices the robot perceived (privacy mode: speech only when addressed to it).")
def listen(ctx):
    w = ctx.w
    hearing = ctx.require_hearing()         # "none" refuses; "addressed": the wake word only (1.3)
    heard = [m for m in w.s.get("messages", []) if m.get("speaker") != "robot" and (m.get("room") == w.robot["room"] or m.get("loud"))
             and (hearing == "full" or addressed_to_robot(m))][-5:]
    # the notices the robot perceived, as they are: state["notices"] stores only what it perceived, under the hearing
    # it had then (kernel/timeline.py robot_perceives: privacy mode keeps addressed speech, other sounds and alarms)
    notices = [copy.deepcopy(n) for n in (w.s.get("notices") or []) if isinstance(n, dict)][-LISTEN_NOTICES:]
    if notices:
        return json.dumps({"messages": heard, "notices": notices}, sort_keys=True)
    return json.dumps(heard if heard else "No new message", sort_keys=True)


ASK_DOC = ("Ask a person (aspect: which/owner/where/preference/amount/when/ok/keep/intended/why/how/status/who; "
           "about: handle/word). detail names a preference slot or what you mean; act the verb of an ok question (a "
           "permission: ok + act pick_up about a handle); options candidate handles or words (for which; for other "
           "aspects they narrow a word naming several things); aspect said checks a quoted claim (about the person "
           "quoted, detail the claim); aspect help + act step_aside asks them to move. about may also be a room, "
           "person or pet id, it (the last subject), request or house. channel voice, phone, text or written (default: "
           "voice in the same room, else phone when they have one; phone and text reach people away from home); "
           "language (default en). A person may ask back which one you mean. Legacy: topic (e.g. "
           "permission:<action>:<handle>).")
ASK_CANONICAL = ("about", "aspect", "detail", "act", "options", "channel", "language")


def legacy_answer(ctx, pid, topic):
    """The answer to a legacy topic, exactly as ask has always produced it: the domain answerer with the longest
    matching prefix, else a permission:<scope> request (granted by an adult with that permission), else the exact
    user_simulator answers / by_person keys (counted necessary or unnecessary), else "I have no additional information
    about that." (counted unnecessary). Canonical questions reach it through the dialogue routes."""
    w = ctx.w
    person = w.s.get("people", {})[pid]
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
            # a person's own answer (user_simulator.by_person) is new from each person asked; a household answer
            # (answers) is known once anyone gave it (food's adoption request 8)
            personal = topic in ((w.s.get("user_simulator") or {}).get("by_person") or {}).get(pid, {})
            necessary = (pid + ":" + topic) not in asked and (personal or topic not in facts)
            w.stat("necessary_questions" if necessary else "unnecessary_questions")
            answer = {"topic": topic, "value": copy.deepcopy(value), "by": pid}
            w.sec("facts")[topic] = copy.deepcopy(value)
        else:
            w.stat("unnecessary_questions")
            answer = {"topic": topic, "value": "I have no additional information about that.", "by": pid}
    return answer


@action("ask", params={"target": "person", "topic": "?str", "destination": "?str", "question": "?text",
                       "about": "?any", "aspect": "?str", "detail": "?str", "act": "?str", "options": "?list",
                       "channel": "?str:" + "|".join(dialogue.CHANNELS), "language": "?str"},
        duration=4.0, energy=0.0, category="communication", stopped_ok=True, doc=ASK_DOC)
def ask(ctx):
    w = ctx.w
    pid = ctx.args["target"]
    topic = ctx.args.get("topic") or ctx.args.get("destination")
    canonical = any(ctx.args.get(k) not in (None, "", []) for k in ASK_CANONICAL)
    if canonical:
        require(not topic, "Use either topic or about/aspect", "argument")
        return json.dumps(dialogue.answer(ctx, ctx.args), sort_keys=True)
    require(topic, "Name a topic, or ask with an aspect and what it is about", "argument")
    # the answer has to be heard: refused with the microphones off or failed; an answer is speech addressed to the
    # robot, so privacy mode still hears it (TIER1_INTEGRATION 1.3)
    ctx.require_hearing(addressed_ok=True)
    people = w.s.get("people", {})
    reachable = pid in people and (people[pid].get("location") == w.robot["room"] or
                                    (w.robot.get("network", True) and people[pid].get("reachable_by_phone")))
    require(reachable, "That person is not available here")
    person = people[pid]
    require(person.get("awake", True), "They are asleep", "social")
    answer = legacy_answer(ctx, pid, topic)
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
        doc="Quiet mode: noisy tools and loud announcements are refused; fast walking slows to normal.")
def set_quiet(ctx):
    on = ctx.args["target"] == "on"
    ctx.w.robot_m()["quiet"] = on
    if on and body.get(ctx.state, "speed_mode") == "fast":
        # the speed setting stays the user's; walks go at normal speed while quiet (body.effective_speed)
        ctx.note("Fast walking is too loud: the robot walks at normal speed while quiet")
    return "Quiet mode " + ctx.args["target"]


@action("set_speed", params={"target": "str:careful|normal|fast"}, duration=0.5, energy=0.0, category="control",
        doc="Motion profile: careful halves speed and is required around fragile items, wet floors and people. Fast "
            "is refused while quiet mode is on or in limp mode (battery critical, weak legs).")
def set_speed(ctx):
    problem = body.speed_problem(ctx.state, ctx.args["target"])
    if problem is not None:
        raise body.BodyRefusal(*problem)
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


RECALL_DOC = ("Recall memories: prefix, map (known rooms), seen:<handle> (last seen where/when, may be stale), snap: "
              "(snapshots). A prefix lists stored memories with when they were stored; map lists the rooms the robot "
              "knows with their exits and when it was last there; seen: lists every last-seen belief, seen:<handle> "
              "one, seen:room:<room> those of a room (stale unless in view now); snap: lists snapshot names.")
MIND_FORMS = ("map", "seen", "seen:", "snap", "snap:", "snapshots")


def _mind(ctx):
    """The robot's Mind (the engine keeps one per episode: Mind.from_config at reset, mind.update every step)."""
    mind = mind_mod.of(ctx.env)
    require(mind is not None, "The robot keeps no memory in this environment", "capability")
    return mind


def _stored(state, mind, entry):
    t = entry.get("time_s") if isinstance(entry, dict) else None
    if t == -1:
        t = None                        # the memory setup op's "known before the episode"
    return {"value": copy.deepcopy(entry.get("value") if isinstance(entry, dict) else entry),
            "stored": mind_mod._when(mind.start, state.get("time_s", 0.0), t), "stored_s": t}


def _round_s(value):
    return float(int(value + 0.5)) if value >= 0 else -float(int(-value + 0.5))


def _whole_seconds(value, depth=0):
    """Realistic perception: the times ("..._s" keys) in a memory message read whole seconds, as the robot's clock."""
    if depth > 6:
        return value
    if isinstance(value, dict):
        return {k: _round_s(v) if isinstance(v, float) and isinstance(k, str) and k.endswith("_s")
                else _whole_seconds(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_whole_seconds(v, depth + 1) for v in value]
    return value


@action("recall", params={"target": "?str"}, duration=1.0, energy=0.0, category="cognitive", stopped_ok=True,
        doc=RECALL_DOC)
def recall(ctx):
    prefix = ctx.args.get("target") or ""
    state = ctx.w.s
    if ctx.realistic:
        ctx.env.realism.memory_output()             # the robot's own memory: issued handles, seen now or not
    memory = state.get("memory", {})
    mind = _mind(ctx)
    if prefix in MIND_FORMS or prefix.startswith(("seen:", "snap:")):
        out = mind.recall(prefix)
        stored = {k: _stored(state, mind, v) for k, v in sorted(memory.items()) if k.startswith(prefix)
                  and prefix.startswith("seen")}
        if stored:
            out = dict(out or {})
            out["memories"] = stored
        out = out if out is not None else {}
    else:
        out = {k: _stored(state, mind, v) for k, v in sorted(memory.items()) if k.startswith(prefix)}
    if ctx.realistic:
        out = _whole_seconds(out)
    return json.dumps(out, sort_keys=True, default=str)


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
    if ctx.realistic:
        ctx.env.realism.memory_output()
    _mind(ctx).snapshot(name, ctx.env)
    return "Snapshot " + name


@action("diff", params={"target": "str"}, duration=2.0, energy=0.0, category="cognitive", stopped_ok=True,
        doc="Compare the visible scene with a named snapshot (one you took, or one the robot remembers).")
def diff(ctx):
    name = ctx.args["target"]
    if ctx.realistic:
        ctx.env.realism.memory_output()
    mind = _mind(ctx)
    require(name in mind.snapshots, "No such snapshot")
    return json.dumps(mind.diff(name, ctx.env), sort_keys=True)
