"""Hazard-aware versions of the core routing skills for people-domain reference plans.

A real robot plans around things on the floor (a robot vacuum in the hall, a cord, a wet patch, a loose rug)
when it can, especially while helping people. These wrappers mirror house_sim.tasks.skills (go_room,
approach, reveal, fetch, put, open/close/switch) but pass `avoid` hazard kinds for every hop or approach whose
path can be planned around them (checked on the privileged map first, so no step is refused), and fall back
to the kernel's step-by-step relaxed lists (tasks.skills.safe_avoid). Like the core skills (kernel v2.3), go_room and
approach never trample: when not even the least cautious list (sharp, fragile, sleeping animals, people on the floor)
leaves a route they raise PlanError; approaches ignore hazard rings sourced by their target and what it rests on
(geometry.target_sources), and a door held partly open is passed at careful pace. Every state change still goes
through runner.do.

Geometric camera (kernel v3.0, TIER1_CAMERA 15), as the core skills: a robot only acts on what it can see, so approach
first walks (hazard-aware, at people's pace) to a viewpoint that sees the target when it is out of sight from where
the robot stands (sight.best_viewpoints, privileged routing for reference plans); reveal looks under or behind a
support with the wrist camera (peek, kneeling beside it unless the support is light enough to lift) instead of a
room-wide inspect, and fetch stands up again after picking up what reveal knelt for. A person is a shape the robot
cannot name until the camera resolves them to the identify level, so approaching a person first walks up to them and
looks (identify). With no viewpoint at all, approach walks beside the thing (the wrist camera may reach it). The room
camera keeps the plans unchanged, apart from the hazard-aware walk beside a doorway to its light switch.
"""
import math

from ...kernel import camera, geometry, sight
from ...tasks import skills as base
from ...tasks.skills import locate, room_route

_OWN_AVOID = ("wet", "trip", "slip", "cord", "fragile", "sharp", "low_obstacle", "litter", "biohazard", "toy", "clutter")
# plus the kernel's v2.1 safe-routing kinds (floor_item = loose portable things on the floor, hot, spill, people down)
AVOID = _OWN_AVOID + tuple(k for k in getattr(base, "SAFE_AVOID", ()) if k not in _OWN_AVOID)


def _speed(state, rid, requested=None):
    """Careful pace whenever people are around (passing close to people at normal pace bumps them)."""
    if requested:
        return requested
    for p in state.get("people", {}).values():
        if p.get("location") == rid and not p.get("away"):
            return "careful"
    return None


def _feasible(state, rid, start, goals, ignore_sources=()):
    goals = [tuple(g) for g in goals]
    if not goals:
        return False
    return geometry.find_path(state, rid, tuple(start), goals, avoid_kinds=AVOID, ignore_sources=ignore_sources) is not None


def _avoid(state, rid, start, goals, ignore_sources=(), strict=True):
    """The most cautious hazard list that still leaves a route: the full people list, else the kernel's step-by-step
    relaxed lists (tasks.skills.safe_avoid). strict: PlanError when not even the least cautious list has a route (a
    careful robot does not trample sleeping animals or fragile things); else None."""
    goals = [tuple(g) for g in goals]
    if not goals:
        return None
    if _feasible(state, rid, start, goals, ignore_sources):
        return list(AVOID)
    return base.safe_avoid(state, rid, tuple(start), goals, ignore_sources=ignore_sources, strict=strict)


def _target_goals(state, target, room):
    """Cells within reach of the target (one more ring for overhead mounts, as the core approach action)."""
    anchor_room, cells = geometry.anchor(dict(state), target)
    if not cells or anchor_room != room:
        return None
    radius = geometry.REACH_CELLS + (1 if target in state["entities"] and geometry.mounted_overhead(state, target) else 0)
    goals = geometry.near_cells(cells, state["rooms"][room], radius) - set(cells)
    blocked = geometry.blocked_cells(state, room)
    return [g for g in sorted(goals) if g not in blocked]


def bed_partners(state, target):
    """Other people lying in the same bed as a person target (a couple in a double bed)."""
    p = state.get("people", {}).get(target) or {}
    bed = p.get("in_bed")
    if not bed:
        return ()
    return tuple(q for q, o in sorted(state.get("people", {}).items()) if q != target and o.get("in_bed") == bed
                 and o.get("location") == p.get("location"))


def go_room(r, room, speed=None):
    state = r.state
    route = room_route(state, state["robot"]["room"], room)
    if route is None:
        raise base._plan_error("No route to " + room)
    for nxt, cid in route:
        state = r.state
        conn = state["connections"][cid]
        here = state["robot"]["room"]
        door_id = conn.get("door")
        if door_id:
            door = state["entities"][door_id]
            if not door.get("open"):
                approach(r, door_id)
                if door.get("locked"):
                    r.do("unlock", target=door_id)
                r.do("open", target=door_id)
        if not r.env.perception.lit(r.state, nxt):
            switch = next((eid for eid, e in sorted(r.state["entities"].items())
                           if e.get("near_connection") == cid and e.get("location") == nxt and e.get("category") == "switch"), None)
            if switch is None:
                light = next((eid for eid in r.env.world.held_items() if r.state["entities"][eid].get("category") == "flashlight"), None)
                if light is None:
                    raise base._plan_error("Destination {} is dark and has no doorway switch; carry a flashlight".format(nxt))
                if not r.state["entities"][light].get("on"):
                    r.do("switch_on", target=light)
            else:
                if geometry.chebyshev(r.state["robot"]["pos"], conn["cells"][here]) > geometry.REACH_CELLS:
                    if door_id:
                        approach(r, door_id)
                    else:
                        _beside_cell(r, conn["cells"][here], speed=speed)
                r.do("switch_on", target=switch)
        args = {"target": nxt}
        start = tuple(r.state["robot"].get("pos") or (0, 0))
        routed = _avoid(r.state, here, start, [conn["cells"].get(here, start)])
        if routed:
            args["avoid"] = routed
        pace = _speed(r.state, here, speed)
        if not pace and door_id and float(r.state["entities"][door_id].get("open_angle_deg", 90) or 90) < 75:
            pace = "careful"      # a door held partly open by something in its swing: pass slowly
        if pace:
            args["speed"] = pace
        r.do("move", **args)
        if r.state["robot"]["room"] != nxt:      # a lenient (wrong) plan whose move was refused stops cleanly
            raise base._plan_error("Could not move from {} to {}: {}".format(here, nxt, (r.last or {}).get("message")))


def _beside_cell(r, cell, speed=None):
    """Walk within reach of a cell (an open doorway's threshold, to reach the light switch beside it) the way people's
    routing walks: onto a free cell next to it by the most cautious hazard list that leaves a route, at people's pace.
    (A raw walk_to ... adjacent=true plans over floor items: on seed 41 of T-d1f9385dbb80fc-01 it ended on a rolling
    toy car and the robot fell.) Without any such route it keeps the lenient walk_to, as people.walk_to does."""
    state = r.state
    rid = state["robot"]["room"]
    start = tuple(state["robot"].get("pos") or (0, 0))
    target = (int(cell[0]), int(cell[1]))
    blocked = geometry.blocked_cells(state, rid)
    goals = sorted(c for c in geometry.near_cells([target], state["rooms"][rid]) if c != target and c not in blocked)
    kinds = _avoid(state, rid, start, goals, strict=False) if goals else None
    path = geometry.find_path(state, rid, start, goals, avoid_kinds=tuple(kinds or ())) if goals else None
    pace = _speed(state, rid, speed)
    if path:
        args = {"cell": list(path[-1])}
        if kinds:
            args["avoid"] = kinds
    elif start in goals:
        return
    else:
        args = {"cell": list(target), "adjacent": True}
    if pace:
        args["speed"] = pace
    r.do("walk_to", **args)


def _geometric(r):
    return camera.mode(r.state) == "geometric"


def _to_viewpoint(r, target, speed=None):
    """Geometric camera: walk to the nearest cell from which the head camera sees the target (sight.best_viewpoints),
    with people's hazard list and pace; nothing when it is already in sight or no cell is reachable. With no viewpoint
    at all (a thing at the back of a deep counter or shelf) it walks next to the target instead, as the core skill
    does (tasks/skills._to_viewpoint): the wrist camera may reach what the head cannot see."""
    state = r.state
    rid = state["robot"]["room"]
    start = tuple(state["robot"].get("pos") or (0, 0))
    goals = [tuple(c) for c in sight.best_viewpoints(state, rid, target, k=3)]
    if not goals:
        goals = [tuple(c) for c in _target_goals(state, target, rid) or ()]
    if not goals or start in goals:
        return
    # walk_to cannot ignore the target's own hazard ring (a cord, a floor item), so the viewpoint is planned without
    # ignoring it; a viewpoint only reachable through that ring is left to the approach action, which ignores it
    kinds = _avoid(state, rid, start, goals, strict=False)
    path = geometry.find_path(state, rid, start, goals, avoid_kinds=tuple(kinds or ()))
    if not path:
        return
    args = {"cell": list(path[-1])}
    if kinds:
        args["avoid"] = kinds
    pace = _speed(state, rid, speed)
    if pace:
        args["speed"] = pace
    r.do("walk_to", **args)


def approach(r, target, speed=None):
    state = r.state
    room = locate(state, target)
    if room is None:
        raise base._plan_error("Cannot locate " + target)
    if room != state["robot"]["room"]:
        go_room(r, room, speed=speed)
    state = r.state
    if target in state["entities"] and geometry.reach_problem(state, target) is None:
        return
    if target in (state.get("people") or {}) and _geometric(r):
        # a person the camera cannot yet tell is a shape: the approach action refuses their name ("You cannot tell who
        # that is from here"), so the robot walks up to them first and looks (safety-7, people part)
        identify(r, target, speed=speed)
        state = r.state
        person = state["people"][target]
        here = tuple(state["robot"].get("pos") or (0, 0))
        if person.get("pos") and camera.agent_identified(r.env, target) and \
                geometry.chebyshev(here, tuple(person["pos"])) <= geometry.REACH_CELLS:
            return
    if _geometric(r) and target in state["entities"] and not r.env.perception.visible(state, target):
        _to_viewpoint(r, target, speed)
        state = r.state
        if geometry.reach_problem(state, target) is None:
            return
    args = {"target": target}
    rid = state["robot"]["room"]
    goals = _target_goals(state, target, rid)
    start = state["robot"].get("pos") or (0, 0)
    sources = geometry.target_sources(state, target)
    from ...tasks.runner import PlanError
    try:
        routed = _avoid(state, rid, start, goals or [], ignore_sources=sources)
    except PlanError:
        partners = bed_partners(state, target)
        if not partners:
            raise
        # someone asleep in the same bed: their ring covers every cell beside the target, so the careful robot may come
        # that close to them (the sleep model then judges its noise and light); it still steps on nothing else
        routed = _avoid(state, rid, start, goals or [], ignore_sources=sources + partners)
        routed = [k for k in routed if k != "sleeping"] if routed else routed
    if routed:
        args["avoid"] = routed
    pace = _speed(state, state["robot"]["room"], speed)
    if pace:
        args["speed"] = pace
    r.do("approach", **args)
    if target in (r.state.get("people") or {}):
        identify(r, target, speed=speed)


# ----- telling who someone is (geometric camera) -------------------------------------------------------------------

WHO_RADIUS = 2          # cells around a person the robot tries for a clear look at them


def _bearing(state, cell, pid):
    """Grid heading (0 = +x, 90 = +y) from a cell's centre to a person's eye point."""
    ex, ey, _ = sight.person_eye(state, pid)
    cx, cy = (cell[0] + 0.5) * geometry.CELL, (cell[1] + 0.5) * geometry.CELL
    return round(math.degrees(math.atan2(ey - cy, ex - cx)) % 360.0, 1)


def _would_identify(state, pid, cell):
    """Scratch copy: would the head camera, standing on cell with the body turned toward the person, resolve them to the
    identify level (64 px, light 0.2, not backlit: camera.agent_identified's rule)?"""
    from ...kernel.world import clone
    trial = clone(state)
    robot = trial["robot"]
    robot["pos"] = [int(cell[0]), int(cell[1])]
    robot["facing_deg"] = _bearing(state, cell, pid)
    robot["posture"] = "standing"
    robot["view"] = {"pan_deg": 0.0, "step": -1}        # no frames of this step: the implicit frame at this pose
    found = camera.acuity(trial, pid)
    return found is not None and found["level"] == "identify"


def identify(r, pid, speed=None, near=True):
    """Geometric camera: a person the frames do not resolve to the identify level (seen from behind, against a window,
    half hidden by a door leaf) is a shape the robot cannot address by name (the kernel's "You cannot tell who that is
    from here"), so the robot walks up to them the way a careful helper would: onto a free cell within WHO_RADIUS of
    them from which, turned toward them, it would tell who they are (checked on a scratch copy of the state: a
    privileged viewpoint search, as sight.best_viewpoints is for things), then turns to face them. No-op in room
    mode, for someone in another room, or when they are already identified; when no cell gives a clear look it only
    turns toward them (an agent would then address the shape it sees). near=False (asking a question): someone already
    told apart from across the room is left where they are."""
    state = r.state
    p = (state.get("people") or {}).get(pid) or {}
    rid = state["robot"]["room"]
    if not _geometric(r) or p.get("location") != rid or not p.get("pos"):
        return
    start = tuple(state["robot"].get("pos") or (0, 0))
    here = tuple(int(v) for v in p["pos"])
    if camera.agent_identified(r.env, pid) and (not near or geometry.chebyshev(start, here) <= geometry.REACH_CELLS):
        return
    blocked = geometry.blocked_cells(state, rid)
    taken = {tuple(a["pos"]) for group in ("people", "pets") for a in (state.get(group) or {}).values()
             if a.get("location") == rid and a.get("pos")}
    doors = set(geometry.doorway_cells(state, rid))
    candidates = [c for c in geometry.near_cells([here], state["rooms"][rid], WHO_RADIUS)
                  if c not in blocked and c not in taken and c not in doors]
    # within arm's reach of them first (hand-overs, assists), then the shortest walk
    candidates.sort(key=lambda c: (geometry.chebyshev(c, here) > geometry.REACH_CELLS, geometry.chebyshev(c, start),
                                   geometry.chebyshev(c, here), c[1], c[0]))
    goal = None
    for cell in candidates[:16]:
        if cell != start and not _feasible(state, rid, start, [cell]) and \
                geometry.find_path(state, rid, start, [cell]) is None:
            continue
        if _would_identify(state, pid, cell):
            goal = cell
            break
    if goal is None and camera.agent_identified(r.env, pid):
        return          # already told apart from here; the approach action brings the robot within reach
    if goal is not None and goal != start:
        walk_to(r, goal, speed=speed)
    r.do("face", deg=_bearing(r.state, tuple(r.state["robot"]["pos"]), pid))


def walk_to(r, cell, speed=None):
    state = r.state
    rid = state["robot"]["room"]
    args = {"cell": [int(cell[0]), int(cell[1])]}
    routed = _avoid(state, rid, state["robot"].get("pos") or (0, 0), [tuple(cell)], strict=False)
    if routed:
        args["avoid"] = routed
    pace = _speed(state, rid, speed)
    if pace:
        args["speed"] = pace
    r.do("walk_to", **args)


def reveal(r, obj):
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
    """reveal with the geometric camera (core skills' _peek_reveal, people's routing): a thing under or behind a
    support that is out of sight is looked for with the wrist camera from beside the support, kneeling to look under
    it unless the support is light enough to lift. Returns True when the robot was made to kneel."""
    where, support = record.get("relation"), record.get("location")
    if where not in ("under", "behind") or support not in r.state["entities"]:
        return False
    approach(r, support)
    if r.env.perception.visible(r.state, obj):
        return False
    lowered = False
    if where == "under" and not camera.peek_access(r.state, support, "under")[0] and \
            r.state["robot"].get("posture") == "standing":
        r.do("kneel")
        lowered = True
    r.do("peek", target=support, where=where)
    return lowered


def fetch(r, obj, hand=None, grip=None):
    state = r.state
    if obj in [v for v in state["robot"]["hands"].values() if v]:
        return obj
    entity = state["entities"].get(obj)
    if entity is None:
        raise base._plan_error("No such entity " + obj)
    lowered = reveal(r, obj)
    approach(r, obj)
    crouched = base._crouch_if_low(r, obj)
    args = {"target": obj}
    if hand:
        args["hand"] = hand
    if grip is None and entity.get("safe_grips"):
        grip = entity["safe_grips"][0]
    if grip:
        args["grip"] = grip
    r.do("pick_up", **args)
    if crouched or lowered:
        base._stand(r)
    return obj


def put(r, obj, dest, relation=None, spos=None, shelf=None, orientation=None):
    fetch(r, obj)
    state = r.state
    args = {"target": obj}
    if dest in state["rooms"]:
        if state["robot"]["room"] != dest:
            go_room(r, dest)
        args["destination"] = dest
        r.do("place", **args)
        return dest
    reveal(r, dest)
    approach(r, dest)
    record = r.state["entities"][dest]
    wants_in = relation == "in" or (relation is None and record.get("container"))
    if wants_in and record.get("openable") and not record.get("open"):
        r.do("open", target=dest)
    crouched = base._crouch_if_low(r, dest)
    args["destination"] = dest
    for key, value in (("relation", relation), ("spos", spos), ("shelf", shelf), ("orientation", orientation)):
        if value is not None:
            args[key] = value
    r.do("place", **args)
    if crouched:
        base._stand(r)
    return dest


def open_(r, target):
    approach(r, target)
    if not r.state["entities"][target].get("open"):
        r.do("open", target=target)


def close_(r, target):
    approach(r, target)
    if r.state["entities"][target].get("open"):
        r.do("close", target=target)


def switch(r, target, on=True):
    approach(r, target)
    if bool(r.state["entities"][target].get("on")) != bool(on):
        r.do("switch_on" if on else "switch_off", target=target)
