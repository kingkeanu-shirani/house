"""Core reference skills (privileged routing; every state change goes through env.step).

Skills are used by reference and wrong plans in task specs. Agents being evaluated do
NOT get these; they compose primitives themselves (or use their own planners).

  go_room(room, avoid=None)       route through connections, opening doors and
                                  switching on a dark destination's doorway light
  approach(target, avoid=None)    go to the target's room and within arm's reach
  reveal(obj)                     open closed containers around obj / inspect under-behind
  fetch(obj, hand=None, grip=None, auto_grip=True)
  put(obj, dest, relation=None, spos=None, shelf=None, cell=None, orientation=None, grip=None)
  give(obj, person)               fetch, find the person, hand over
  ask(person, topic)              returns the answer value; the canonical form ask(person, about=..., aspect=...,
                                  detail=..., act=..., options=..., channel=..., language=...) too
  tell(person, kind=..., ...)     a typed report to a person: walks to them for an in-person report (or uses the
                                  channel given; person "household" logs it)
  open(target) / close(target) / switch(target, on=True) / set(target, setting, value)
  use(action, target, tool=None, approach_target=True, **params)
  drop_all(dest=None)             set everything held down (floor of current room by default)
  wait(seconds)

Geometric camera (kernel v3.0, TIER1_CAMERA 15; the room camera keeps the plans above unchanged): approach first
walks to a viewpoint (sight.best_viewpoints) when the target is not in sight; reveal looks under or behind a support
with the wrist camera (peek: kneeling beside it, or lifting a light cushion, rug or mat) instead of a room-wide
inspect, and fetch stands up again after picking up what reveal knelt for; fetch crouches for low items as before.

Robot body (kernel v3.0, TIER1_EMBODIMENT KB-3): a skill whose robot powered down (its battery ran out on the way)
stops with PlanError("robot powered down") instead of retrying actions the body would refuse.
"""
from collections import deque

from ..kernel import body, camera, geometry, sight
from ..kernel.plugins import skill
from ..kernel.predicates import root_room


SAFE_AVOID = ["wet", "slip", "trip", "sharp", "fragile", "sleeping", "person_down", "tail", "dock_zone", "floor_item", "hot", "spill", "cord"]
RELAXED_AVOID = [["wet", "slip", "trip", "sharp", "fragile", "sleeping", "person_down", "tail", "dock_zone", "spill", "cord"],
                 ["wet", "slip", "sharp", "fragile", "sleeping", "person_down", "tail"],
                 ["sharp", "fragile", "sleeping", "person_down"]]


def safe_avoid(state, room, start, goals, requested=None, ignore_sources=(), strict=False):
    """Hazard kinds to route around: the requested list, else the most cautious list that still has a clear route
    (privileged check), relaxing step by step down to never stepping on sharp or fragile things, sleeping animals or
    people on the floor. Hazards coming from ignore_sources (the approach target and what it rests on) never block.
    When even the least cautious list has no route: strict (core go_room/approach) raises PlanError, since a careful
    robot does not trample through; otherwise None."""
    candidates = [list(requested)] if requested else [list(SAFE_AVOID)] + RELAXED_AVOID
    for kinds in candidates:
        if geometry.find_path(state, room, start, goals, avoid_kinds=tuple(kinds), ignore_sources=ignore_sources) is not None:
            return kinds
    if requested:
        return list(requested)
    if not strict:
        return None
    raise _plan_error("No route in {} that avoids stepping on sharp or fragile things, sleeping animals or people".format(room))


def _plan_error(message):
    from .runner import PlanError
    return PlanError(message)


POWERED_DOWN = "robot powered down"


def _powered(r):
    """A powered-down robot refuses every action but wait (body.PowerOff), so a skill stops at once with
    PlanError("robot powered down") instead of retrying (TIER1_EMBODIMENT KB-3)."""
    if not body.is_on(r.state):
        raise _plan_error(POWERED_DOWN)


def room_route(state, start, goal, allow_hatch=False):
    """Cheapest room sequence [(room, connection id), ...]: one per hop, plus 20 for a locked door the robot holds no
    key for (thumb-turn locks open from the room side without a key) and 0.5 for detours outdoors when both ends are
    indoors; ties break by connection id. Ladder hatches only with allow_hatch."""
    import heapq
    if start == goal:
        return []
    rooms = state.get("rooms", {})
    indoor_trip = not (rooms.get(start) or {}).get("outdoor") and not (rooms.get(goal) or {}).get("outdoor")
    held = {v for v in ((state.get("robot") or {}).get("hands") or {}).values() if v}
    adjacency = {}
    for cid, conn in sorted(state.get("connections", {}).items()):
        if conn.get("needs_ladder") and not allow_hatch:
            continue
        a, b = conn["rooms"]
        adjacency.setdefault(a, []).append((b, cid))
        adjacency.setdefault(b, []).append((a, cid))

    def cost(here, nxt, cid):
        c = 1.0
        door = state.get("entities", {}).get(state["connections"][cid].get("door") or "")
        if door and door.get("locked"):
            key = door.get("key")
            thumb = door.get("lock_kind") == "thumb_turn" and here == (door.get("connects") or [None, None])[0]
            if not (key and key in held) and not thumb:
                c += 20.0
        if indoor_trip and (rooms.get(nxt) or {}).get("outdoor"):
            c += 0.5
        return c
    frontier, best = [(0.0, start, [])], {start: 0.0}
    while frontier:
        dist, room, path = heapq.heappop(frontier)
        if room == goal:
            return path
        if dist > best.get(room, float("inf")):
            continue
        for nxt, cid in adjacency.get(room, []):
            d = dist + cost(room, nxt, cid)
            if d < best.get(nxt, float("inf")) - 1e-9:
                best[nxt] = d
                heapq.heappush(frontier, (d, nxt, path + [(nxt, cid)]))
    return None


def _entity_room(state, eid):
    if eid in state["rooms"]:
        return eid
    entity = state["entities"].get(eid)
    if entity is not None and entity.get("connects") and state["robot"]["room"] in entity["connects"]:
        return state["robot"]["room"]
    return root_room(state, eid)


@skill("go_room")
def go_room(r, room, avoid=None, speed=None):
    state = r.state
    route = room_route(state, state["robot"]["room"], room)
    if route is None:
        raise _plan_error("No route to " + room)
    for nxt, cid in route:
        _powered(r)
        state = r.state
        conn = state["connections"][cid]
        here = state["robot"]["room"]
        door_id = conn.get("door")
        if door_id:
            door = state["entities"][door_id]
            if not door.get("open"):
                _approach_door(r, door_id)
                if door.get("locked"):
                    r.do("unlock", target=door_id)
                r.do("open", target=door_id)
        if not r.env.perception.lit(r.state, nxt):
            switch = next((eid for eid, e in sorted(r.state["entities"].items())
                           if e.get("near_connection") == cid and e.get("location") == nxt and e.get("category") == "switch"), None)
            if switch is None:
                light = next((eid for eid in r.env.world.held_items() if r.state["entities"][eid].get("category") == "flashlight"), None)
                if light is None:
                    raise _plan_error("Destination {} is dark and has no doorway switch; carry a flashlight".format(nxt))
                if not r.state["entities"][light].get("on"):
                    r.do("switch_on", target=light)
                switch = None
            if switch is not None:
                if geometry.chebyshev(r.state["robot"]["pos"], conn["cells"][here]) > geometry.REACH_CELLS:
                    _approach_door(r, door_id) if door_id else _approach_cell(r, conn["cells"][here])
                r.do("switch_on", target=switch)
        args = {"target": nxt}
        routed = safe_avoid(r.state, here, r.state["robot"]["pos"], [conn["cells"][here]], avoid, strict=True)
        if routed:
            args["avoid"] = routed
        if speed:
            args["speed"] = speed
        elif door_id and float(r.state["entities"][door_id].get("open_angle_deg", 90) or 90) < 75:
            args["speed"] = "careful"   # a door held partly open by something in its swing: pass slowly
        elif _unguided_winders(r, cid, here, nxt):
            args["speed"] = "careful"   # down winders without a hand on the rail or lit treads: every foot placed
        r.do("move", **args)
        _powered(r)                     # the battery ran out on the way: the walk stopped where it was cut
        if r.state["robot"]["room"] != nxt:
            raise _plan_error("Could not move from {} to {}: {}".format(here, nxt, (r.last or {}).get("message")))


def _unguided_winders(r, cid, here, nxt):
    """go_room's pace down a flight with winder treads (navigation_robot's stairs rule, review H10): at a walking pace
    a hand on the rail and lit treads keep the robot on the walk line; without a free working hand, a usable rail or
    light in both rooms (or a light it carries), a heel lands on a narrow inner end. The reference then descends
    carefully (phase 1, navigation_robot's kernel request; plumbing_liquids' and safety's skills already do)."""
    state = r.state
    conn = state["connections"][cid]
    if not int(conn.get("winders", 0) or 0) or body.stairs_direction(state, conn, here, nxt) != "down":
        return False
    hands = state["robot"].get("hands") or {}
    hand_free = any(not hands.get(h) and body.arm_health(state, h) > 0 for h in body.HANDS)
    rails = [rec for rec in state["entities"].values()
             if rec.get("on_stairs") == cid and rec.get("category") == "handrail"]
    rail = conn.get("handrail", True) is not False and not any(rec.get("broken") for rec in rails)
    carried_light = any((state["entities"].get(eid) or {}).get("category") == "flashlight" and
                        (state["entities"].get(eid) or {}).get("on") for eid in hands.values() if eid)
    lit = carried_light or state["robot"].get("headlamp_on") or         (r.env.perception.lit(state, here) and r.env.perception.lit(state, nxt))
    return not (hand_free and rail and lit)


def _approach_cell(r, cell):
    """Walk within reach of a cell (a doorless doorway's threshold, to reach the light switch beside it): onto a free
    cell next to it by the most cautious hazard list that leaves a route (safe_avoid). A raw walk_to ... adjacent=true
    plans over floor items: on seed 41 of T-d1f9385dbb80fc-01 it ended on a rolling toy car and the robot fell (phase
    1, the people domain's kernel request; people's routing._beside_cell). Without such a route the lenient walk stays."""
    state = r.state
    rid = state["robot"]["room"]
    start = tuple(state["robot"].get("pos") or (0, 0))
    target = (int(cell[0]), int(cell[1]))
    blocked = geometry.blocked_cells(state, rid)
    goals = sorted(c for c in geometry.near_cells([target], state["rooms"][rid]) if c != target and c not in blocked)
    if start in goals:
        return None
    kinds = safe_avoid(state, rid, start, goals) if goals else None
    path = geometry.find_path(state, rid, start, goals, avoid_kinds=tuple(kinds or ())) if goals else None
    if path:
        args = {"cell": list(path[-1])}
        if kinds:
            args["avoid"] = kinds
        return r.do("walk_to", **args)
    return r.do("walk_to", cell=list(target), adjacent=True)


def locate(state, target):
    if target in state.get("people", {}):
        return state["people"][target].get("location")
    if target in state.get("pets", {}):
        return state["pets"][target].get("location")
    return _entity_room(state, target)


def _geometric(r):
    return camera.mode(r.state) == "geometric"


def _approach_door(r, door_id):
    """go_room's approach of a door on the route (the raw verb, as before). With the geometric camera a door out of
    sight from here (a fridge or a wall run between) is walked into view first, as the approach skill does
    (_to_viewpoint), since core approach refuses a target it cannot see (adoption-round request of cleaning and food;
    safety's own go_room did the same). Room camera: the raw verb alone."""
    if _geometric(r) and door_id in r.state["entities"] and not _in_sight(r, door_id):
        _to_viewpoint(r, door_id)
    r.do("approach", target=door_id)


def _in_sight(r, target):
    return r.env.perception.visible(r.state, target)


def _to_viewpoint(r, target, avoid=None):
    """Geometric camera: when the target is out of sight from here, walk to the nearest viewpoint that sees it
    (sight.best_viewpoints), else next to it (privileged routing; the wrist may reach what the head cannot see)."""
    state = r.state
    rid = state["robot"]["room"]
    start = tuple(state["robot"].get("pos") or (0, 0))
    goals = [tuple(c) for c in sight.best_viewpoints(state, rid, target, k=3)]
    if not goals:
        anchor_room, cells = geometry.anchor(state, target)
        if anchor_room != rid or not cells:
            return
        goals = sorted(geometry.near_cells(cells, state["rooms"][rid], geometry.REACH_CELLS) - set(cells))
    kinds = safe_avoid(state, rid, start, goals, avoid, ignore_sources=geometry.target_sources(state, target))
    path = geometry.find_path(state, rid, start, goals, avoid_kinds=tuple(kinds or ()),
                              ignore_sources=geometry.target_sources(state, target))
    if not path:
        return
    args = {"cell": list(path[-1])}
    if kinds:
        args["avoid"] = kinds
    r.do("walk_to", **args)


@skill("approach")
def approach(r, target, avoid=None, speed=None):
    state = r.state
    room = locate(state, target)
    if room is None:
        raise _plan_error("Cannot locate " + target)
    if room != state["robot"]["room"]:
        go_room(r, room, avoid=avoid, speed=speed)
    if target in r.state["entities"] and geometry.reach_problem(r.state, target) is None:
        return
    if _geometric(r) and target in r.state["entities"] and not _in_sight(r, target):
        _to_viewpoint(r, target, avoid)
        if geometry.reach_problem(r.state, target) is None:
            return
    args = {"target": target}
    state = r.state
    anchor_room, cells = geometry.anchor(state, target)
    if cells and anchor_room == state["robot"]["room"]:
        radius = geometry.REACH_CELLS + (1 if target in state["entities"] and geometry.mounted_overhead(state, target) else 0)
        goals = geometry.near_cells(cells, state["rooms"][anchor_room], radius) - set(cells)
        routed = safe_avoid(state, anchor_room, state["robot"]["pos"], goals, avoid,
                            ignore_sources=geometry.target_sources(state, target), strict=True)
        if routed:
            args["avoid"] = routed
    elif avoid:
        args["avoid"] = avoid
    if speed:
        args["speed"] = speed
    r.do("approach", **args)
    _powered(r)


@skill("reveal")
def reveal(r, obj):
    """Open the closed containers around obj; for a thing under or behind a support, look there (room camera: inspect
    the support where=under/behind; geometric camera: approach the support and peek with the wrist camera, kneeling
    for under unless the support is light enough to lift). Returns True when it left the robot kneeling."""
    state = r.state
    chain, location = [], state["entities"][obj].get("location")
    while location in state["entities"]:
        chain.append(location)
        location = state["entities"][location].get("location")
    for parent in reversed(chain):
        record = r.state["entities"][parent]
        if record.get("openable") and not record.get("open"):
            approach(r, parent)
            if record.get("locked"):
                r.do("unlock", target=parent)
            r.do("open", target=parent)
    record = r.state["entities"][obj]
    if _geometric(r):
        return _peek_reveal(r, obj, record)
    if record.get("relation") in ("under", "behind") and not record.get("revealed"):
        approach(r, record["location"])
        r.do("inspect", target=record["location"], where=record["relation"])
    return False


def _peek_reveal(r, obj, record):
    """reveal in geometric camera mode (TIER1_CAMERA 15): a thing under or behind a support that is out of sight is
    looked for with the wrist camera from beside the support (kneeling to look under it, unless the support is a
    light one to lift). Returns True when the robot was made to kneel."""
    where, support = record.get("relation"), record.get("location")
    if where not in ("under", "behind") or support not in r.state["entities"]:
        return False
    approach(r, support)
    if _in_sight(r, obj):
        return False
    lowered = False
    if where == "under" and not camera.peek_access(r.state, support, "under")[0] and \
            r.state["robot"].get("posture") == "standing":
        r.do("kneel")
        lowered = True
    r.do("peek", target=support, where=where)
    return lowered


def _crouch_if_low(r, eid):
    if eid in r.state["entities"] and geometry.height_m(r.state, eid) < geometry.LOW_REACH_M and r.state["robot"].get("posture") == "standing":
        r.do("crouch")
        return True
    return False


def _stand(r):
    if r.state["robot"].get("posture") != "standing":
        r.do("stand")


@skill("fetch")
def fetch(r, obj, hand=None, grip=None, auto_grip=True, avoid=None):
    state = r.state
    if obj in [v for v in state["robot"]["hands"].values() if v]:
        return obj
    entity = state["entities"].get(obj)
    if entity is None:
        raise _plan_error("No such entity " + obj)
    if entity.get("location") in state.get("people", {}):
        raise _plan_error("{} is held by a person; use receive".format(obj))
    lowered = reveal(r, obj)
    approach(r, obj, avoid=avoid)
    crouched = _crouch_if_low(r, obj)
    args = {"target": obj}
    if hand:
        args["hand"] = hand
    if grip is None and auto_grip and entity.get("safe_grips"):
        grip = entity["safe_grips"][0]
    if grip:
        args["grip"] = grip
    r.do("pick_up", **args)
    if crouched or lowered:
        _stand(r)
    return obj


@skill("put")
def put(r, obj, dest, relation=None, spos=None, shelf=None, cell=None, orientation=None, grip=None, avoid=None):
    fetch(r, obj, grip=grip, avoid=avoid)
    state = r.state
    if dest in state.get("people", {}):
        return give(r, obj, dest)
    args = {"target": obj}
    if dest in state["rooms"]:
        if state["robot"]["room"] != dest:
            go_room(r, dest, avoid=avoid)
        if cell is not None:
            args["cell"] = cell
        args["destination"] = dest
        r.do("place", **args)
        return dest
    record = state["entities"][dest]
    reveal(r, dest)
    approach(r, dest, avoid=avoid)
    record = r.state["entities"][dest]
    wants_in = relation == "in" or (relation is None and record.get("container"))
    if wants_in and record.get("openable") and not record.get("open"):
        r.do("open", target=dest)
    crouched = _crouch_if_low(r, dest)
    args["destination"] = dest
    for key, value in (("relation", relation), ("spos", spos), ("shelf", shelf), ("orientation", orientation)):
        if value is not None:
            args[key] = value
    r.do("place", **args)
    if crouched:
        _stand(r)
    return dest


@skill("give")
def give(r, obj, person):
    fetch(r, obj)
    approach(r, person)
    r.do("hand_over", target=obj, destination=person)


@skill("ask")
def ask(r, person, topic=None, **question):
    """Ask a person: the legacy topic, or the canonical question (about, aspect, detail, act, options, channel,
    language; kernel/dialogue.py). Walks to the person's room unless they can be reached by phone (or a phone / text
    channel is named) or are away from home (location None: only a phone or text reaches them); a written question
    walks right up to them. Returns the answer value (a permission's grant)."""
    state = r.state
    person_rec = state["people"][person]
    room = person_rec.get("location")
    channel = question.get("channel")
    remote = channel in ("phone", "text") or (channel is None and person_rec.get("reachable_by_phone"))
    if room is not None and room != state["robot"]["room"] and not remote:
        go_room(r, room)
    if channel == "written":
        approach(r, person)
    args = {k: v for k, v in question.items() if v is not None}
    if topic is not None:
        args["topic"] = topic
    r.do("ask", target=person, **args)
    answer = r.env.world.s.get("facts", {}).get("last_answer", {})
    return answer.get("value", answer.get("granted"))


@skill("tell")
def tell(r, person=None, channel=None, **report):
    """A typed report (kernel/reports.py): kind, subject, issue / state / quantity, value, unit, of, scope, ... to
    `person` over `channel` (in person by default: the robot walks to their room first; "household" logs it for the
    household). Returns the report message."""
    state = r.state
    args = {k: v for k, v in report.items() if v is not None}
    if person:
        args["to"] = person
        if person != "household" and channel in (None, "in_person"):
            room = state["people"][person].get("location")
            if room is not None and room != state["robot"]["room"]:
                go_room(r, room)
    if channel:
        args["channel"] = channel
    return r.do("report", **args)


@skill("open")
def open_skill(r, target):
    approach(r, target)
    if not r.state["entities"][target].get("open"):
        r.do("open", target=target)


@skill("close")
def close_skill(r, target):
    approach(r, target)
    if r.state["entities"][target].get("open"):
        r.do("close", target=target)


@skill("switch")
def switch_skill(r, target, on=True):
    approach(r, target)
    if bool(r.state["entities"][target].get("on")) != bool(on):
        r.do("switch_on" if on else "switch_off", target=target)


@skill("set")
def set_skill(r, target, setting, value):
    approach(r, target)
    r.do("set", target=target, setting=setting, value=value)


@skill("use")
def use(r, action, target, tool=None, approach_target=True, crouch=False, **params):
    if tool:
        fetch(r, tool)
        params["tool"] = tool
    if approach_target and target:
        if target in r.state["entities"]:
            reveal(r, target)
        approach(r, target)
    if crouch:
        _crouch_if_low(r, target)
    args = dict(params)
    if target:
        args["target"] = target
    result = r.do(action, **args)
    if crouch:
        _stand(r)
    return result


@skill("drop_all")
def drop_all(r, dest=None):
    for eid in list(dict.fromkeys(v for v in r.state["robot"]["hands"].values() if v)):
        if dest:
            put(r, eid, dest)
        else:
            r.do("place", target=eid)


@skill("wait")
def wait_skill(r, seconds):
    remaining = int(seconds)
    while remaining > 0:
        chunk = min(remaining, 3600)
        r.do("wait", seconds=chunk)
        remaining -= chunk


@skill("goto")
def goto(r, target, avoid=None):
    if target in r.state["rooms"]:
        return go_room(r, target, avoid=avoid)
    return approach(r, target, avoid=avoid)
