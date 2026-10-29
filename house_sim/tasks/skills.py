"""Core reference skills (privileged routing; every state change goes through env.step).

Skills are used by reference and wrong plans in task specs. Agents being evaluated do
NOT get these; they compose primitives themselves (or use their own planners).

  go_room(room, avoid=None)       route through connections, opening doors and
                                  switching on a dark destination's doorway light
  approach(target, avoid=None)    go to the target's room and within arm's reach
  reveal(obj)                     open closed containers around obj / inspect under-behind
  fetch(obj, hand=None, grip=None, auto_grip=True)
  put(obj, dest, relation=None, spos=None, shelf=None, cell=None, orientation=None, grip=None, close=False)
  give(obj, person)              fetch, find the person, hand over
  ask(person, topic)              returns the answer value; the canonical form ask(person, about=..., aspect=...,
                                  detail=..., act=..., options=..., channel=..., language=...) too
  tell(person, kind=..., ...)     a typed report to a person: walks to them for an in-person report (or uses the
                                  channel given; person "household" logs it)
  open(target) / close(target) / switch(target, on=True) / set(target, setting, value)
  use(action, target, tool=None, approach_target=True, **params)
  drop_all(dest=None)             set everything held down (floor of current room by default)
  wait(seconds)
  survey(rooms=None, places=None, rows=1, strict=False)
                                  look around every room (nearest first), and at each candidate place (open, inspect)

Robustness (VERIFY_HARDENING 25, WP11; review H10 and F11, 14.5): a parameter that names a thing, room, person or pet
and arrives unbound (None, empty, not a string: "fetch {blocker}" on a seed whose setup placed no blocker) or names
nothing in the house stops the plan with PlanError, never a TypeError or KeyError (an exception that is no PlanError
or ActionError is a harness error charged to this file); such a PlanError carries category "argument", and a walk
with no way through category "route" (PlanError.category, for 16.3's skill-debt classification). put opens a closed destination: it unlocks it first (with
the key the robot holds, as reveal does for the containers around it), sets down what it holds when opening needs a
free hand and picks it up again, and with close=True closes and relocks what it opened. go_room descends winder
stairs carefully unless a hand on a usable rail (one the robot does not know to be loose) and lit treads guide it.

Survey (VERIFY_HARDENING 17): the canonical survey of the budget formula's explore allowance -- from where the robot
is, go_room to each room (nearest first by route, ties by room id) and look around in it -- and, with places, the
search baseline's walk: every candidate place that exists, nearest first, approached, opened when closed and
inspected, without stopping at the item. Returns {"rooms", "places", "skipped"}.

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
    robot does not trample through (its message says so only when trampling would have found a way: otherwise nothing
    reaches the goal at all, which is no caution, WP11); otherwise None."""
    candidates = [list(requested)] if requested else [list(SAFE_AVOID)] + RELAXED_AVOID
    for kinds in candidates:
        if geometry.find_path(state, room, start, goals, avoid_kinds=tuple(kinds), ignore_sources=ignore_sources) is not None:
            return kinds
    if requested:
        return list(requested)
    if not strict:
        return None
    if geometry.find_path(state, room, start, goals, avoid_kinds=(), ignore_sources=ignore_sources) is None:
        # nothing to trample: furniture, walls or a closed thing cut the way off (a setup or layout defect, not caution;
        # held-out seed 502 of T-9615ae9f7570b4-01 spawns the tape where no free cell reaches it)
        raise _plan_error("No route in {} at all: furniture, walls or closed things cut the way off".format(room),
                          "route")
    raise _plan_error("No route in {} that avoids stepping on sharp or fragile things, sleeping animals or people"
                      .format(room), "route")


def _plan_error(message, category=None):
    """PlanError(message); category (WP11), when given, is kept as the error's `category` attribute for the
    verifier's classification (VERIFY_HARDENING 16.3, 10.1): "argument" for a binding that names nothing (a spec
    failure), "route" for a walk with no way through (a navigation failure)."""
    from .runner import PlanError
    error = PlanError(message)
    if category:
        error.category = category
    return error


ROOM, THING, PERSON, PET = "room", "thing", "person", "pet"
ANYWHERE = (THING, PERSON, PET, ROOM)
_GROUPS = {ROOM: "rooms", THING: "entities", PERSON: "people", PET: "pets"}


def _named(r, skill_name, param, value, kinds):
    """The id a skill parameter names, checked before the skill reads it (VERIFY_HARDENING 14.5 and 25; review H10 and
    F11). A binding the setup left unset (None, an empty or a non-string value) or an id that names no thing, room,
    person or pet of `kinds` in the house now raises PlanError, as a refused step does: the plan cannot be carried out
    on this seed. It used to raise TypeError ("No such entity " + None in fetch, T-5ad61b6881821b-01 seed 502) or
    KeyError (put into an unknown room), which aborted the trial."""
    if not isinstance(value, str) or not value:
        raise _plan_error("{}: {} is unbound ({!r}): the binding names nothing".format(skill_name, param, value),
                          "argument")
    state = r.state
    if any(value in (state.get(_GROUPS[kind]) or {}) for kind in kinds):
        return value
    raise _plan_error("{}: no {} {!r} in the house".format(skill_name, " or ".join(kinds), value), "argument")


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
    _named(r, "go_room", "room", room, (ROOM,))
    state = r.state
    route = room_route(state, state["robot"]["room"], room)
    if route is None:
        raise _plan_error("No route to " + room, "route")
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
    carefully (phase 1, navigation_robot's kernel request; plumbing_liquids' and safety's skills already do). A rail
    the robot inspected and knows to be loose guides nothing: it only lets its hand slide along it (stairs.plan's
    light touch), so that flight is descended carefully too (WP11). Going up, and straight flights, are unchanged."""
    state = r.state
    conn = state["connections"][cid]
    if not int(conn.get("winders", 0) or 0) or body.stairs_direction(state, conn, here, nxt) != "down":
        return False
    hands = state["robot"].get("hands") or {}
    hand_free = any(not hands.get(h) and body.arm_health(state, h) > 0 for h in body.HANDS)
    rails = [(eid, rec) for eid, rec in sorted(state["entities"].items())
             if rec.get("on_stairs") == cid and rec.get("category") == "handrail"]
    checked = (state.get("nav") or {}).get("rails_checked") or {}
    rail = conn.get("handrail", True) is not False and \
        not any(rec.get("broken") or (rec.get("_loose") and eid in checked) for eid, rec in rails)
    carried_light = any((state["entities"].get(eid) or {}).get("category") == "flashlight" and
                        (state["entities"].get(eid) or {}).get("on") for eid in hands.values() if eid)
    lit = carried_light or state["robot"].get("headlamp_on") or \
        (r.env.perception.lit(state, here) and r.env.perception.lit(state, nxt))
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
    _named(r, "approach", "target", target, ANYWHERE)
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
    _named(r, "reveal", "obj", obj, (THING,))
    return _reveal(r, obj)[0]


def _reveal(r, obj, opened=None, free_hand=False):
    """reveal's work: (kneeling, opened). opened lists what it opened, outermost first, as (id, unlocked) pairs (put's
    close=True shuts them again); free_hand: put's rule for a robot with no free hand (_open_for_access)."""
    opened = [] if opened is None else opened
    state = r.state
    chain, location = [], state["entities"][obj].get("location")
    while location in state["entities"]:
        chain.append(location)
        location = state["entities"][location].get("location")
    for parent in reversed(chain):
        record = r.state["entities"][parent]
        if record.get("openable") and not record.get("open"):
            approach(r, parent)
            _open_for_access(r, parent, opened, free_hand)
    record = r.state["entities"][obj]
    if _geometric(r):
        return _peek_reveal(r, obj, record), opened
    if record.get("relation") in ("under", "behind") and not record.get("revealed"):
        approach(r, record["location"])
        r.do("inspect", target=record["location"], where=record["relation"])
    return False, opened


def _free_hands(r):
    """Empty hands, by the rule core open and close apply (core.hands_free_count)."""
    return sum(1 for held in (r.state["robot"].get("hands") or {}).values() if held is None)


def _open_for_access(r, eid, opened, free_hand=False):
    """Open a closed thing the robot stands at, to reach what is in it: unlock it first when it is locked (with the key
    the robot holds, a thumb turn on this side...; the env refuses otherwise), as reveal always did. free_hand (put):
    when opening needs a free hand and none is free (a two-handed load), set what the robot holds down beside it,
    open, and pick each thing up again with the hand and grip it had. Appends (eid, unlocked) to opened."""
    record = r.state["entities"][eid]
    unlocked = bool(record.get("locked"))
    if unlocked:
        r.do("unlock", target=eid)
    set_down = []
    if free_hand and _free_hands(r) == 0 and not record.get("hands_free_operable") and not record.get("push_operable"):
        for item in list(dict.fromkeys(v for v in r.state["robot"]["hands"].values() if v)):
            rec = r.state["entities"][item]
            set_down.append((item, rec.get("held_hand"), rec.get("held_grip")))
            r.do("place", target=item)
    r.do("open", target=eid)
    opened.append((eid, unlocked))
    for item, hand, grip in set_down:
        fetch(r, item, hand=hand if hand in ("left", "right", "both") else None, grip=grip, auto_grip=grip is None)
    if set_down:
        approach(r, eid)


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
    _named(r, "fetch", "obj", obj, (THING,))
    state = r.state
    if obj in [v for v in state["robot"]["hands"].values() if v]:
        return obj
    entity = state["entities"][obj]
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
def put(r, obj, dest, relation=None, spos=None, shelf=None, cell=None, orientation=None, grip=None, avoid=None,
        close=False):
    """Fetch obj and set it at dest: on or in a thing (relation, spos, shelf, orientation), on a room's floor (cell) or
    in a person's hands (give). A closed destination is opened first, the closed containers around it too (reveal):
    unlocked when locked, and with what the robot holds set down and picked up again when opening needs a free hand
    (_open_for_access; WP11). close=True closes, and relocks, what put opened once the thing is placed, leaving them as
    found (a battery or must-pass variant that puts a thing away in a cupboard); by default they stay open, as
    before."""
    _named(r, "put", "obj", obj, (THING,))
    _named(r, "put", "dest", dest, (THING, ROOM, PERSON))
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
    lowered, opened = _reveal(r, dest, free_hand=True)
    approach(r, dest, avoid=avoid)
    record = r.state["entities"][dest]
    wants_in = relation == "in" or (relation is None and record.get("container"))
    if wants_in and record.get("openable") and not record.get("open"):
        _open_for_access(r, dest, opened, free_hand=True)
    crouched = _crouch_if_low(r, dest)
    args["destination"] = dest
    for key, value in (("relation", relation), ("spos", spos), ("shelf", shelf), ("orientation", orientation)):
        if value is not None:
            args[key] = value
    r.do("place", **args)
    if crouched or lowered:
        _stand(r)
    if close:
        _shut(r, opened)
    return dest


def _shut(r, opened):
    """put's close=True: close what it opened, innermost first, and lock again what it unlocked."""
    for eid, unlocked in reversed(opened):
        if eid not in r.state["entities"]:
            continue
        approach(r, eid)
        if r.state["entities"][eid].get("open"):
            r.do("close", target=eid)
        if unlocked and not r.state["entities"][eid].get("locked"):
            r.do("lock", target=eid)


@skill("give")
def give(r, obj, person):
    _named(r, "give", "obj", obj, (THING,))
    _named(r, "give", "person", person, (PERSON,))
    fetch(r, obj)
    approach(r, person)
    r.do("hand_over", target=obj, destination=person)


@skill("ask")
def ask(r, person, topic=None, **question):
    """Ask a person: the legacy topic, or the canonical question (about, aspect, detail, act, options, channel,
    language; kernel/dialogue.py). Walks to the person's room unless they can be reached by phone (or a phone / text
    channel is named) or are away from home (location None: only a phone or text reaches them); a written question
    walks right up to them. Returns the answer value (a permission's grant)."""
    _named(r, "ask", "person", person, (PERSON,))
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
        if person != "household" and channel in (None, "in_person"):
            _named(r, "tell", "person", person, (PERSON,))   # walked to: a resident (a remote contact needs a channel)
        elif not isinstance(person, str):
            raise _plan_error("tell: person is not an id ({!r})".format(person), "argument")
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
    _named(r, "open", "target", target, (THING,))
    approach(r, target)
    if not r.state["entities"][target].get("open"):
        r.do("open", target=target)


@skill("close")
def close_skill(r, target):
    _named(r, "close", "target", target, (THING,))
    approach(r, target)
    if r.state["entities"][target].get("open"):
        r.do("close", target=target)


@skill("switch")
def switch_skill(r, target, on=True):
    _named(r, "switch", "target", target, (THING,))
    approach(r, target)
    if bool(r.state["entities"][target].get("on")) != bool(on):
        r.do("switch_on" if on else "switch_off", target=target)


@skill("set")
def set_skill(r, target, setting, value):
    _named(r, "set", "target", target, (THING,))
    approach(r, target)
    r.do("set", target=target, setting=setting, value=value)


@skill("use")
def use(r, action, target, tool=None, approach_target=True, crouch=False, **params):
    """Any action: fetch the tool first, walk to the target (reveal it when it is hidden) unless approach_target is
    false, crouch for a low target when asked. A target the action takes that is not a thing (a speed, a cell) goes
    with approach_target=False."""
    if not isinstance(action, str) or not action:
        raise _plan_error("use: action is unbound ({!r})".format(action), "argument")
    if tool:
        fetch(r, tool)
        params["tool"] = tool
    if approach_target and target:
        _named(r, "use", "target", target, ANYWHERE)
        if target in r.state["entities"]:
            reveal(r, target)
        approach(r, target)
    if crouch and isinstance(target, str):
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
    try:
        remaining = int(seconds)
    except (TypeError, ValueError):
        raise _plan_error("wait: seconds is unbound or not a number ({!r})".format(seconds), "argument")
    while remaining > 0:
        chunk = min(remaining, 3600)
        r.do("wait", seconds=chunk)
        remaining -= chunk


@skill("goto")
def goto(r, target, avoid=None):
    _named(r, "goto", "target", target, ANYWHERE)
    if target in r.state["rooms"]:
        return go_room(r, target, avoid=avoid)
    return approach(r, target, avoid=avoid)


# ----------------------------------------------------------------------
# survey (VERIFY_HARDENING 17: the explore allowance of the budget formula, the search baseline)

def _hops(state, start, goal):
    """Rooms walked from start to goal on the route go_room takes (0 when there; None without a route)."""
    route = room_route(state, start, goal)
    return None if route is None else len(route)


def _nearest(state, here, pos, candidates):
    """The next stop of a survey tour: fewest room hops from here, then (in this room) the fewest cells from pos, then
    the id. candidates: [(stop, room)]. Returns (stop, room) or None when no candidate has a route."""
    best = None
    for stop, room in candidates:
        hops = _hops(state, here, room)
        if hops is None:
            continue
        cells = 0
        if hops == 0 and stop != room:
            anchor_room, footprint = geometry.anchor(state, stop)
            if footprint and anchor_room == here:
                cells = min(geometry.chebyshev(pos, c) for c in footprint)
        key = (hops, cells, stop)
        if best is None or key < best[0]:
            best = (key, (stop, room))
    return None if best is None else best[1]


def _halted(r):
    """Why the robot can do no more (powered down, fallen), else None."""
    if not body.is_on(r.state):
        return POWERED_DOWN
    if r.state["robot"].get("fallen"):
        return "robot fell"
    return None


@skill("survey")
def survey(r, rooms=None, places=None, rows=1, strict=False):
    """The canonical survey (VERIFY_HARDENING 17, the budget's explore allowance; S3 runs it on each seed from the
    start pose): go_room to each room of `rooms` (default: every indoor room of the house) and look around in it
    (look around: six head frames in the geometric camera, rows=2 adds the low row; one look in the room camera). The
    tour is canonical, whatever the order given: nearest room first by go_room's route from where the robot is, ties
    by room id; the room it stands in first.

    places: the search baseline (17, critique H19): every candidate place of a `place` answer that exists in the house
    (absent ones are skipped silently), visited nearest first (room hops, then cells, then id) -- go_room, look
    around on entering a room, approach the place, open it when it is closed (a locked one is skipped), and inspect
    it (inside, for a container) -- without stopping at the item. A place may be a room (looked around in).

    strict=False (the baselines): a room or place the robot cannot reach, open or look at is skipped and recorded,
    and the tour goes on from where the robot is; strict=True raises the first PlanError. Either way it stops when
    the robot powered down or fell. Returns {"rooms": rooms looked around in, in order, "places": places inspected, in
    order, "skipped": {id: why}}."""
    state = r.state
    if rooms is None:
        rooms = [] if places is not None else [rid for rid, rec in sorted(state["rooms"].items())
                                               if not rec.get("outdoor")]
    if isinstance(rooms, str) or not isinstance(rooms, (list, tuple)):
        raise _plan_error("survey: rooms must list room ids ({!r})".format(rooms), "argument")
    if places is not None and (isinstance(places, str) or not isinstance(places, (list, tuple))):
        raise _plan_error("survey: places must list ids ({!r})".format(places), "argument")
    for rid in rooms:
        _named(r, "survey", "rooms", rid, (ROOM,))
    try:
        rows = int(rows)
    except (TypeError, ValueError):
        raise _plan_error("survey: rows is not a number ({!r})".format(rows), "argument")
    out = {"rooms": [], "places": [], "skipped": {}}
    pending = []
    for rid in rooms:
        if (rid, rid) not in pending:
            pending.append((rid, rid))
    for pid in places or ():
        if not isinstance(pid, str) or not pid:
            raise _plan_error("survey: places lists an unbound id ({!r})".format(pid), "argument")
        room = pid if pid in state["rooms"] else locate(state, pid) if pid in state["entities"] else None
        if room is not None and (pid, room) not in pending:
            pending.append((pid, room))

    from .runner import PlanError

    def attempt(stop, work):
        try:
            work()
        except PlanError as exc:
            if strict:
                raise
            out["skipped"].setdefault(stop, str(exc)[:200])
            return False
        return True

    def look_here():
        room = r.state["robot"]["room"]
        args = {"target": "around"}
        if rows != 1:
            args["rows"] = rows
        r.do("look", **args)
        out["rooms"].append(room)

    while pending:
        why = _halted(r)
        if why:
            if strict:
                raise _plan_error("survey: " + why)
            for stop, _ in pending:
                out["skipped"].setdefault(stop, why)
            break
        state = r.state
        nxt = _nearest(state, state["robot"]["room"], tuple(state["robot"].get("pos") or (0, 0)), pending)
        if nxt is None:
            if strict:
                raise _plan_error("survey: no route to " + ", ".join(stop for stop, _ in pending), "route")
            for stop, _ in pending:
                out["skipped"].setdefault(stop, "no route")
            break
        pending.remove(nxt)
        stop, room = nxt
        if not attempt(stop, lambda: go_room(r, room)):
            continue
        if room not in out["rooms"]:
            attempt(room, look_here)
        if stop != room:
            attempt(stop, lambda: _search_place(r, stop, out))
    return out


def _search_place(r, place, out):
    """One stop of the search baseline: open the closed containers around the place (reveal), approach it, open it
    when it is closed (a locked place is reported, not forced), inspect it -- inside, for a container -- and record
    it. A robot reveal left kneeling stands up again."""
    lowered = _reveal(r, place)[0]
    approach(r, place)
    record = r.state["entities"][place]
    if record.get("openable") and not record.get("open"):
        if record.get("locked"):
            raise _plan_error("survey: {} is locked".format(place))
        r.do("open", target=place)
    record = r.state["entities"][place]
    if record.get("container") or (record.get("openable") and record.get("accepts_items")):
        r.do("inspect", target=place, where="inside")
    else:
        r.do("inspect", target=place)
    if lowered:
        _stand(r)
    out["places"].append(place)
