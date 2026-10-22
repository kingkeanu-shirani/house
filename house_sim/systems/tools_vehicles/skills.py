"""Reference skills (privileged routing; every state change goes through runner.do).

Skills are composed from core verbs, this domain's verbs and the core skills in house_sim.tasks.skills.
They are used by reference and wrong plans in task specs; evaluated agents do not get them.

Geometric camera (kernel v3.0): the core approach verb only goes to what the robot can see, so these skills walk to a
viewpoint first when a target (a thing, a door on the route) is out of sight, as the core approach skill does.
"""
import json

from ...kernel import geometry
from ...kernel.plugins import skill
from ...tasks import skills as base
from . import fasteners as F


def _err(message):
    from ...tasks.runner import PlanError
    return PlanError(message)


def _held(r):
    return [h for h in dict.fromkeys(r.state["robot"]["hands"].values()) if h]


TRIP_KINDS = ("trip",)


def _avoid_for(state, target):
    """["trip"] when the robot's room has trip-flagged floor items (panels, jacks, spare wheels...) and a path
    around them to the target exists; walking over a heavy low item trips the robot when navigation is loaded."""
    rid = state["robot"]["room"]
    hz = geometry.hazard_cells(state, rid)
    if not any(kind in TRIP_KINDS for items in hz.values() for kind, _ in items):
        return None
    room, cells = geometry.anchor(state, target)
    if room != rid or not cells:
        return None
    start = tuple(state["robot"].get("pos") or (0, 0))
    if min(geometry.chebyshev(start, c) for c in cells) <= geometry.REACH_CELLS:
        return None
    goals = geometry.near_cells(cells, state["rooms"][rid]) - set(cells)
    if geometry.find_path(state, rid, start, goals, avoid_kinds=TRIP_KINDS) is None:
        return None
    return list(TRIP_KINDS)


SKIP_FLOOR = ("floor", "wall", "door", "gate", "window", "light", "switch", "hatch", "hooks", "spill")


def _contact_cells(state, rid, exclude=(), rings=False):
    """Cells a careful robot steps around: loose small floor items (anything <= 0.2 m tall, or rolling, fragile,
    crushable, low or flagged items) and hazard cells (spills, wet floor, cords...). With the navigation domain loaded
    these trip, break or are kicked; without it avoiding them is merely polite. rings=True adds the one-cell keep-out
    ring around fragile/topple-prone floor items that navigation derives once the robot sees them (setup-time checks
    must anticipate it)."""
    cells = set()
    room = state["rooms"][rid]
    for eid in geometry.room_entities(state, rid):
        rec = state["entities"][eid]
        if eid in exclude or not rec.get("pos") or rec.get("blocks_path") or rec.get("ground_layer") or "connects" in rec                 or rec.get("mounted") or rec.get("category") in SKIP_FLOOR:
            continue
        height = float((rec.get("size_m") or [0.1, 0.1, 0.1])[2])
        if height <= 0.2 or rec.get("floor_hazard") or rec.get("rolls") or rec.get("fragile") or rec.get("crushable")                 or rec.get("low_obstacle") or rec.get("rv") or rec.get("cord_to") or rec.get("tines") == "up":
            own = geometry.footprint(rec)
            cells.update(own)
            if rings and (rec.get("fragile") or rec.get("topple_prone")) and not rec.get("broken"):
                cells.update(geometry.near_cells(own, room, max(1, int(rec.get("nav_margin", 1) or 1))))
    cells.update(geometry.hazard_cells(state, rid))
    return cells


RING_KINDS = ("fragile", "sleeping", "hot", "dock_zone", "occupied_ladder")


def _contact_without_rings(state, rid, exclude=()):
    """Contact cells minus the keep-out rings navigation draws around things (the cells beside a fragile box, a hot
    grill, a sleeping pet): nothing lies there, so a careful robot may edge through them slowly when there is no
    other way; every loose item, spill and other hazard stays out of bounds."""
    room = state["rooms"][rid]
    kept = [h for h in room.get("floor_hazards", []) or [] if not (h.get("nav_derived") and h.get("kind") in RING_KINDS)]
    view = dict(state, rooms=dict(state["rooms"], **{rid: dict(room, floor_hazards=kept)}))
    return _contact_cells(view, rid, exclude=exclude)


def _wide_near_people(r):
    """True when the robot carries a wide load (a flat-pack panel: more than 0.6 m across) and someone stands awake in
    its room: at normal speed a wide load brushes a person it passes (navigation_robot), so a careful robot slows down."""
    from ..core import held_width
    state = r.state
    if held_width(r.env.world) <= 0.6:
        return False
    rid = state["robot"]["room"]
    for person in state.get("people", {}).values():
        if person.get("location") == rid and person.get("awake", True) and person.get("posture") not in ("lying", "fallen", "sleeping"):
            return True
    return False


def _safe_walk(r, goals, exclude=()):
    """Walk to one of `goals` (cells in the robot's room) without crossing contact cells, using walk_to waypoints, when
    the default planner path would cross them and a clear detour exists. Returns "moved" (walked the detour), "clear"
    (the default path is fine, or there is none) or "crossing" (no detour: the default path crosses loose items)."""
    state = r.state
    rid = state["robot"]["room"]
    room = state["rooms"][rid]
    start = tuple(state["robot"].get("pos") or (0, 0))
    goals = {tuple(g) for g in goals if geometry.in_grid(room, tuple(g))}
    if not goals or start in goals:
        return "clear"
    contact = _contact_cells(state, rid, exclude=exclude) - {start}
    default = geometry.find_path(state, rid, start, goals)
    if default is None or not (set(default) & contact):
        return "clear"
    detour = geometry.find_path(state, rid, start, goals - contact, extra_blocked=sorted(contact))
    if not detour:
        detour = _detour_via_doorway(state, rid, start, goals, contact)
    speed = None
    if not detour:
        # nothing fully clear: edge slowly through keep-out rings (the cells beside a fragile box, a hot grill, a
        # sleeping pet), but never onto any loose item, spill or hazard itself
        contact = _contact_without_rings(state, rid, exclude=exclude) - {start}
        if default is not None and not (set(default) & contact):
            return "crossing"
        detour = geometry.find_path(state, rid, start, goals - contact, extra_blocked=sorted(contact))
        if not detour:
            detour = _detour_via_doorway(state, rid, start, goals, contact)
        if not detour:
            # boxed in by clutter: step slowly over the fewest and least delicate things (a paperback before a
            # carton, never a cake box)
            detour = _least_contact_path(state, rid, start, goals, exclude)
            if not detour:
                return "crossing"
            contact = contact - set(detour)
        speed = "careful"
    if speed is None and _wide_near_people(r):
        speed = "careful"
    cur, i = start, 0
    while i < len(detour):
        j = len(detour) - 1
        while j > i:
            seg = geometry.find_path(r.state, rid, cur, [detour[j]])
            if seg is not None and not (set(seg) & contact):
                break
            j -= 1
        args = {"cell": [int(detour[j][0]), int(detour[j][1])]}
        if speed:
            args["speed"] = speed
        res = r.do("walk_to", **args)
        if not res["ok"] or tuple(r.state["robot"].get("pos") or ()) != tuple(detour[j]):
            return "moved"
        cur, i = detour[j], j + 1
    return "moved"


def _step_cost(rec):
    """Cost of stepping over a loose item for a careful robot (paperback 10, a heavy or trip-flagged box 60, anything
    fragile, crushable, rolling or holding liquid 1000)."""
    if rec.get("fragile") or rec.get("crushable") or rec.get("rolls") or float(rec.get("liquid_l", 0.0) or 0.0) > 0:
        return 1000.0
    if rec.get("floor_hazard") in ("trip",) or float(rec.get("mass_kg", 0.0) or 0.0) >= 3.0:
        return 60.0
    return 10.0


def _least_contact_path(state, rid, start, goals, exclude=()):
    """Dijkstra over the room grid (8-neighbour, no corner cutting past blocked cells) where loose items cost
    `_step_cost` and hazard cells 200: the path a careful person picks when every way is cluttered. None if none."""
    import heapq
    room = state["rooms"][rid]
    blocked = set(geometry.blocked_cells(state, rid))
    blocked.discard(tuple(start))
    cost = {}
    for eid in geometry.room_entities(state, rid):
        rec = state["entities"][eid]
        if eid in exclude or not rec.get("pos") or rec.get("blocks_path") or rec.get("ground_layer") or "connects" in rec or rec.get("mounted"):
            continue
        for c in geometry.footprint(rec):
            cost[c] = max(cost.get(c, 0.0), _step_cost(rec))
    for c in geometry.hazard_cells(state, rid):
        cost.setdefault(c, 200.0)
    goals = {tuple(g) for g in goals if tuple(g) not in blocked and geometry.in_grid(room, tuple(g))}
    if not goals:
        return None
    start = tuple(start)
    best = {start: 0.0}
    came = {start: None}
    heap = [(0.0, start)]
    while heap:
        d, cur = heapq.heappop(heap)
        if d > best.get(cur, 1e18):
            continue
        if cur in goals:
            path = []
            while cur != start:
                path.append(cur)
                cur = came[cur]
            return list(reversed(path))
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            nxt = (cur[0] + dx, cur[1] + dy)
            if nxt in blocked or not geometry.in_grid(room, nxt):
                continue
            if dx and dy and ((cur[0] + dx, cur[1]) in blocked or (cur[0], cur[1] + dy) in blocked
                              or cost.get((cur[0] + dx, cur[1]), 0) > 0 or cost.get((cur[0], cur[1] + dy), 0) > 0):
                continue
            nd = d + (1.4 if dx and dy else 1.0) + cost.get(nxt, 0.0)
            if nd < best.get(nxt, 1e18):
                best[nxt] = nd
                came[nxt] = cur
                heapq.heappush(heap, (nd, nxt))
    return None


def _detour_via_doorway(state, rid, start, goals, contact):
    """No clear path from here (the robot stands among staged loads): a clear path back to one of the room's doorways
    and from there to the goal, the way a careful person steps back out and comes round. None if there is none."""
    blocked = sorted(contact)
    doors = sorted({tuple(c["cells"][rid]) for c in state.get("connections", {}).values() if rid in c.get("cells", {})} - contact)
    best = None
    for door in doors:
        if door == start:
            continue
        out = geometry.find_path(state, rid, start, [door], extra_blocked=blocked)
        if out is None:
            continue
        back = geometry.find_path(state, rid, door, goals - contact, extra_blocked=blocked)
        if back is None:
            continue
        path = list(out) + [c for c in back if c != door]
        if best is None or len(path) < len(best):
            best = path
    return best


def see_first(r, target):
    """Geometric camera: a target out of the robot's sight is walked into view first (the core approach skill's
    viewpoint walk); a no-op in room mode or once it is in sight."""
    if base._geometric(r) and target in r.state["entities"] and not base._in_sight(r, target):
        base._to_viewpoint(r, target)


def go_room(r, room):
    """base.go_room, hop by hop, first walking to each doorway around loose floor items."""
    route = base.room_route(r.state, r.state["robot"]["room"], room)
    if route is None:
        raise _err("No route to " + room)
    for nxt, cid in route:
        here = r.state["robot"]["room"]
        conn = r.state["connections"][cid]
        cell = conn.get("cells", {}).get(here)
        how = "clear"
        if cell is not None:
            how = _safe_walk(r, geometry.near_cells([tuple(cell)], r.state["rooms"][here]))
        if _door_with_full_hands(r, conn.get("door"), nxt):
            continue
        door = conn.get("door")
        drec = r.state["entities"].get(door) if door else None
        if drec is not None and not drec.get("open") and any(v is None for v in r.state["robot"]["hands"].values()):
            see_first(r, door)
            r.do("approach", target=door)
            if drec.get("locked"):
                r.do("unlock", target=door)
            r.do("open", target=door)
        # kernel v2.3: base.go_room itself passes a door held partly open (open_angle_deg < 75) at careful speed
        if how == "crossing" or _wide_near_people(r):
            base.go_room(r, nxt, speed="careful")
        else:
            base.go_room(r, nxt)


def _door_with_full_hands(r, door, nxt):
    """A closed door with both hands full. Carrying two things: shuttle them (self-closing garage/fire doors swing shut
    ~4 s after opening, too soon to set one down, open, and pick it up again): leave one by the door, carry the other
    through, set it down, come back for the first. One two-handed item: set it down, open, pick it up again.
    Returns True when the robot already went through to `nxt`."""
    state = r.state
    if not door or door not in state["entities"] or state["entities"][door].get("open"):
        return False
    if any(v is None for v in state["robot"]["hands"].values()):
        return False
    held = _held(r)
    if not held:
        return False
    here = state["robot"]["room"]
    see_first(r, door)
    r.do("approach", target=door)
    if len(held) == 1:
        set_down(r, held[0])
        if r.state["entities"][door].get("locked"):
            r.do("unlock", target=door)
        r.do("open", target=door)
        fetch(r, held[0])
        return False
    spill_prone = [h for h in held if state["entities"][h].get("openable") and state["entities"][h].get("open")]
    leave = next((h for h in held if h not in spill_prone), held[0])
    carry = next(h for h in held if h != leave)
    set_down(r, leave)
    base.go_room(r, nxt)
    set_down(r, carry)
    base.go_room(r, here)
    fetch(r, leave)
    base.go_room(r, nxt)
    fetch(r, carry)
    return True


def approach(r, target):
    """base.approach, stepping around loose floor items and trip/hazard cells when a clear detour exists."""
    state = r.state
    room = base.locate(state, target)
    if room is None:
        raise _err("Cannot locate " + target)
    if room != state["robot"]["room"]:
        go_room(r, room)
    if target in r.state["entities"] and geometry.reach_problem(r.state, target) is None:
        return
    see_first(r, target)
    if target in r.state["entities"] and geometry.reach_problem(r.state, target) is None:
        return
    rid, cells = geometry.anchor(r.state, target)
    how = "clear"
    if rid == r.state["robot"]["room"] and cells:
        goals = geometry.near_cells(cells, r.state["rooms"][rid]) - set(cells)
        how = _safe_walk(r, goals, exclude=(target,))
        if how == "moved" and target in r.state["entities"] and geometry.reach_problem(r.state, target) is None:
            return
    args = {"target": target}
    if how == "crossing" or _wide_near_people(r):
        # no clear detour: step over/around loose items slowly (stumble, not fall); a wide load near people: slowly
        args["speed"] = "careful"
    avoid = _avoid_for(r.state, target)
    if avoid:
        args["avoid"] = avoid
    r.do("approach", **args)


def _open_path_to(r, eid):
    """Open the closed containers holding eid (approaching each around trip hazards). Kernel v2.1: a closed container
    hides and blocks only what is `in` it, so things standing `on` it or `hung` from it are reached without opening."""
    state = r.state
    chain, child, location = [], eid, state["entities"][eid].get("location")
    while location in state["entities"]:
        chain.append((location, state["entities"][child].get("relation")))
        child, location = location, state["entities"][location].get("location")
    for parent, relation in reversed(chain):
        record = r.state["entities"][parent]
        if relation in ("on", "hung"):
            continue
        if record.get("openable") and not record.get("open"):
            approach(r, parent)
            if record.get("locked"):
                r.do("unlock", target=parent)
            swing_clear(r, parent)
            r.do("open", target=parent)


def swing_clear(r, eid, tries=6):
    """A careful robot does not swing a head-height cabinet door into someone standing at the counter below it: it
    waits (30 s at a time, up to 3 min) for them to step away before opening."""
    for _ in range(tries):
        state = r.state
        rec = state["entities"].get(eid) or {}
        if rec.get("connects") or geometry.height_m(state, eid) < 1.2:
            return
        room, cells = geometry.anchor(state, eid)
        if not cells:
            return
        near = {(x + dx, y + dy) for x, y in cells for dx, dy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1))}
        if not any(p.get("location") == room and p.get("pos") and tuple(p["pos"]) in near for p in state.get("people", {}).values()):
            return
        r.do("wait", seconds=30)


def fetch(r, eid, **kw):
    if eid in _held(r):
        return eid
    if eid in r.state["entities"] and r.state["entities"][eid].get("location") not in r.state.get("people", {}):
        _open_path_to(r, eid)
        rec = r.state["entities"][eid]
        if not (rec.get("relation") in ("under", "behind") and not rec.get("revealed")):
            approach(r, eid)
    return base.fetch(r, eid, **kw)


def put(r, obj, dest, **kw):
    fetch(r, obj, **({"grip": kw["grip"]} if kw.get("grip") else {}))
    if dest in r.state["entities"]:
        _open_path_to(r, dest)
        approach(r, dest)
    return base.put(r, obj, dest, **kw)


def _free_floor(state, room, eid):
    """(blocked, used) cell sets for laying eid on this room's floor."""
    blocked = set(geometry.blocked_cells(state, room, ignore=(eid,)))
    used = set()
    for other in geometry.room_entities(state, room):
        orec = state["entities"][other]
        if other != eid and not orec.get("ground_layer") and "connects" not in orec:
            used.update(geometry.footprint(orec))
    for conn in state.get("connections", {}).values():
        if room in conn.get("cells", {}):
            used.add(tuple(conn["cells"][room]))
    used.update(geometry.hazard_cells(state, room))
    return blocked, used


def far_drop_cell(r, eid):
    """Nearest free floor cell in this room that the robot can walk next to (for crowded work areas)."""
    state = r.state
    room = state["robot"]["room"]
    rpos = tuple(state["robot"].get("pos") or (0, 0))
    rec = dict(state["entities"][eid])
    blocked, used = _free_floor(state, room, eid)
    cols, rows = geometry.grid_size(state["rooms"][room])
    cands = sorted(((geometry.chebyshev((x, y), rpos), y, x) for y in range(rows) for x in range(cols)
                    if (x, y) not in blocked and (x, y) not in used))
    for _, y, x in cands[:60]:
        rec["pos"] = [x, y]
        cells = geometry.footprint(rec)
        if not all(geometry.in_grid(state["rooms"][room], c) and c not in blocked and c not in used for c in cells):
            continue
        goals = geometry.near_cells(cells, state["rooms"][room]) - set(cells)
        for kinds in (TRIP_KINDS, ()):
            if geometry.find_path(state, room, rpos, goals, avoid_kinds=kinds) is not None:
                return [x, y], list(kinds)
    return None, None


def drop_cell(r, eid):
    """A floor cell within arm's reach where the held item's footprint is free (None if there is none)."""
    state = r.state
    room = state["robot"]["room"]
    rpos = tuple(state["robot"].get("pos") or (0, 0))
    rec = dict(state["entities"][eid])
    blocked = set(geometry.blocked_cells(state, room, ignore=(eid,)))
    used = set()
    for other in geometry.room_entities(state, room):
        orec = state["entities"][other]
        if other != eid and not orec.get("ground_layer") and "connects" not in orec:
            used.update(geometry.footprint(orec))
    for conn in state.get("connections", {}).values():
        if room in conn.get("cells", {}):
            used.add(tuple(conn["cells"][room]))
    used.update(geometry.hazard_cells(state, room))
    way_out = _way_out(state, room, rpos)
    for cell in sorted(geometry.near_cells([rpos], state["rooms"][room]), key=lambda c: (geometry.chebyshev(c, rpos), c[1], c[0])):
        if cell == rpos:
            continue
        rec["pos"] = list(cell)
        cells = geometry.footprint(rec)
        if all(geometry.in_grid(state["rooms"][room], c) and c not in blocked and c not in used for c in cells):
            if way_out and not _way_out(state, room, rpos, extra=cells):
                continue   # never box yourself in: the tool set down must not close the last clear way to a doorway
            return [int(cell[0]), int(cell[1])]
    return None


def _way_out(state, room, start, extra=()):
    """True if the robot can walk from start to one of the room's doorways without crossing loose items (plus `extra`)."""
    contact = (_contact_cells(state, room) | set(tuple(c) for c in extra)) - {tuple(start)}
    doors = sorted({tuple(c["cells"][room]) for c in state.get("connections", {}).values() if room in c.get("cells", {})} - contact)
    if not doors:
        return False
    return geometry.find_path(state, room, tuple(start), doors, extra_blocked=sorted(contact)) is not None


def set_down(r, eid):
    """Put a held item down within reach: a free floor cell, else a reachable surface or open container."""
    cell = drop_cell(r, eid)
    if cell is not None:
        return r.do("place", target=eid, cell=cell)
    state = r.state
    room = state["robot"]["room"]
    for other in sorted(state["entities"]):
        rec = state["entities"][other]
        if other == eid or not rec.get("accepts_items") or not (rec.get("surface") or (rec.get("container") and (rec.get("open") or not rec.get("openable")))):
            continue
        if w_root(state, other) != room or geometry.reach_problem(state, other) is not None or rec.get("waste_stream") or rec.get("vehicle_kind"):
            continue
        if not _sees(r, other):
            continue  # a platter inside a closed cabinet is within arm's reach but not a place to set anything
        if _in_vehicle(state, other):
            continue  # never park things inside a car (footwell, seat, trunk) while working around it
        if rec.get("surface") and geometry.surface_spot(state, other, eid) is None:
            continue
        if rec.get("container") and not geometry.fits_inside(state["entities"][eid].get("size_m") or [0.1, 0.1, 0.1], rec.get("inner_size_m")):
            continue
        return r.do("place", target=eid, destination=other)
    cell, avoid = far_drop_cell(r, eid)
    if cell is not None:
        args = {"cell": cell, "adjacent": True}
        if avoid:
            args["avoid"] = avoid
        if _wide_near_people(r):
            args["speed"] = "careful"
        r.do("walk_to", **args)
        return r.do("place", target=eid, cell=cell)
    return r.do("place", target=eid)


def _sees(r, eid):
    perception = getattr(r.env, "perception", None)
    return perception is None or perception.visible(r.state, eid)


def _in_vehicle(state, eid):
    seen, cur = set(), eid
    while cur in state["entities"] and cur not in seen:
        seen.add(cur)
        rec = state["entities"][cur]
        if rec.get("vehicle_kind"):
            return True
        cur = rec.get("location")
    return False


def w_root(state, eid):
    from ...kernel.predicates import root_room
    return root_room(state, eid)


def free_hands(r, n=1, keep=()):
    """Set held items (except keep) down on the floor here until n hands are free."""
    for eid in _held(r):
        if sum(1 for v in r.state["robot"]["hands"].values() if v is None) >= n:
            return
        if eid in keep:
            continue
        set_down(r, eid)


def ensure_holding(r, eid, keep=()):
    if eid in _held(r):
        return eid
    rec = r.state["entities"][eid]
    need = 2 if (rec.get("two_handed") or max((rec.get("size_m") or [0.1])[:2]) > 0.7) else 1
    free_hands(r, need, keep=keep)
    return fetch(r, eid)


def _container_of(r, eid):
    loc = r.state["entities"][eid].get("location")
    rec = r.state["entities"].get(loc)
    return loc if rec and rec.get("openable") else None


@skill("tools_vehicles.go_to")
def go_to(r, target):
    """Walk up to a target the way this domain's skills do: around loose floor items, through partly open doors
    slowly, opening only the containers it is in."""
    if target in r.state["entities"]:
        _open_path_to(r, target)
    approach(r, target)
    return target


@skill("tools_vehicles.get_tool")
def get_tool(r, tool, keep=()):
    box = _container_of(r, tool)
    ensure_holding(r, tool, keep=keep)
    free = sum(1 for v in r.state["robot"]["hands"].values() if v is None)
    if box and free and r.state["entities"][box].get("open") and r.state["entities"][box].get("category") == "toolbox":
        approach(r, box)
        r.do("close", target=box)
    return tool


@skill("tools_vehicles.return_tools")
def return_tools(r, tools, home=None):
    for tool in tools:
        if tool not in r.state["entities"]:
            continue
        dest = home or r.state["entities"][tool].get("home")
        if not dest or r.state["entities"][tool].get("location") == dest:
            continue
        if tool in _held(r) and dest in r.state["entities"]:
            # carry everything held to the tool's home first; free a hand there only when its lid/drawer must be opened
            # (setting the other tool down back at the work site would mean a second trip)
            _open_path_to(r, dest)
            approach(r, dest)
            drec = r.state["entities"][dest]
            if drec.get("openable") and not drec.get("open") and all(v is not None for v in r.state["robot"]["hands"].values()):
                free_hands(r, 1, keep=(tool,))
        else:
            free_hands(r, 1, keep=(tool,))
        put(r, tool, dest)
    for dest in sorted({home} if home else {r.state["entities"][t].get("home") for t in tools if t in r.state["entities"]}):
        if dest and r.state["entities"].get(dest, {}).get("openable") and r.state["entities"][dest].get("open"):
            approach(r, dest)
            r.do("close", target=dest)


@skill("tools_vehicles.tighten")
def tighten(r, target, tool=None, fastener=None, torque_nm=None, hold_aligned=False, loosen=False, turns=None, remove=False, into=None, screw=None):
    if tool:
        get_tool(r, tool)
    rec = r.state["entities"][target]
    truth = rec.get("_fasteners") or {}
    ids = [fastener] if fastener in truth else [f for f in truth if truth[f].get("group") == fastener] if fastener else list(truth)
    access = {truth[f].get("access") for f in ids if f in truth}
    approach(r, target)
    opened = None
    speed = r.state["robot"].get("speed_mode", "normal")
    if "inside" in access and speed != "careful":
        r.do("set_speed", target="careful")
    if "inside" in access and rec.get("openable") and not rec.get("open"):
        r.do("open", target=target)
        opened = target
    if "cover" in access and "cover_open" in rec and not rec.get("cover_open"):
        r.do("open_cover", target=target)
    crouched = False
    if "underneath" in access and r.state["robot"].get("posture") == "standing":
        r.do("crouch")
        crouched = True
    args = {"target": target}
    for key, value in (("tool", tool), ("fastener", fastener), ("torque_nm", torque_nm), ("turns", turns), ("into", into), ("screw", screw)):
        if value is not None:
            args[key] = value
    if hold_aligned:
        args["hold_aligned"] = True
    if remove:
        args["remove"] = True
    result = r.do("loosen" if loosen else "tighten", **args)
    if crouched:
        r.do("stand")
    if opened:
        r.do("close", target=opened)
    if r.state["robot"].get("speed_mode") != speed:
        r.do("set_speed", target=speed)
    return result


@skill("tools_vehicles.fix_wobble")
def fix_wobble(r, table, cause, wrench=None, pad=None, leg=None):
    approach(r, table)
    r.do("inspect", target=table, where="closely")
    if cause == "loose_bolts":
        tighten(r, table, tool=wrench, fastener="leg_bolts")
    else:
        ensure_holding(r, pad)
        approach(r, table)
        r.do("install", target=pad, destination=table, slot="pad_" + leg)
    r.do("inspect", target=table, where="closely")


@skill("tools_vehicles.assemble_kit")
def assemble_kit(r, assembly, tool, box=None, skip_cams=False, flip=None, order=None, anchor_tool=None, wall=None, ask=None, stop_before=None,
                 cam_torque=None):
    """Build a flat-pack kit: for each manual step fetch the matching panel, attach it (finished face as wanted unless flipped),
    then tighten that slot's cams with the screwdriver (unless skip_cams). anchor_tool (a drill) + ask (a person) do the anti-tip step."""
    a = r.state["entities"][assembly]["assembly"]
    steps = order or a["manual"]
    for slot in steps:
        if stop_before and slot == stop_before:
            return
        if slot == "anti_tip":
            if not anchor_tool:
                continue
            if ask:
                base.ask(r, ask, "permission:drill:" + (wall or ""))
            free_hands(r, 2)
            get_tool(r, anchor_tool)
            approach(r, assembly)
            r.do("drill", target=assembly, tool=anchor_tool, purpose="anti_tip")
            set_down(r, anchor_tool)
            r.do("assemble", target=assembly, slot="anti_tip")
            continue
        info = r.state["entities"][assembly]["assembly"]["slots"][slot]
        if info.get("item"):
            continue
        part = None
        for eid in sorted(r.state["entities"]):
            rec = r.state["entities"][eid]
            if rec.get("part_type") == info["part_type"] and rec.get("location") not in (assembly,) and rec.get("slot") is None:
                if box is None or rec.get("location") in (box, "robot") or r.state["entities"].get(rec.get("location"), {}).get("location") == r.state["robot"]["room"] or True:
                    part = eid
                    break
        if part is None:
            raise _err("No part for " + slot)
        free_hands(r, 2)
        ensure_holding(r, part)
        approach(r, assembly)
        want = info.get("want", "finished_out")
        orientation = ("finished_in" if want == "finished_out" else "finished_out") if flip == slot else want
        r.do("assemble", target=assembly, part=part, slot=slot, orientation=orientation)
        if info.get("cams") and not skip_cams:
            get_tool(r, tool)
            approach(r, assembly)
            args = {"target": assembly, "fastener": slot, "tool": tool}
            if cam_torque is not None:
                args["torque_nm"] = cam_torque
            r.do("tighten", **args)
            set_down(r, tool)


@skill("tools_vehicles.hang")
def hang(r, item, hook, wall, height_m, offset_m, level=None, cure_s=3600, settle_s=1800, hammer=None):
    if hammer:
        free_hands(r, 2)
        get_tool(r, hammer)
        ensure_holding(r, hook, keep=(hammer,))
        approach(r, wall)
        r.do("hammer", target=wall, tool=hammer, mode="drive", hardware=hook, height_m=height_m, offset_m=offset_m)
        set_down(r, hammer)
    else:
        free_hands(r, 1)
        ensure_holding(r, hook)
        approach(r, wall)
        r.do("hang_decoration", target=hook, destination=wall, height_m=height_m, offset_m=offset_m)
    if cure_s:
        base.wait_skill(r, cure_s)
    free_hands(r, 2)
    ensure_holding(r, item)
    args = {"target": item, "destination": hook}
    if level:
        ensure_holding(r, level, keep=(item,))
        args["level_tool"] = level
    approach(r, hook)
    r.do("hang_decoration", **args)
    if level:
        set_down(r, level)
    if settle_s:
        base.wait_skill(r, settle_s)


@skill("tools_vehicles.inflate_wheel")
def inflate_wheel(r, bike, part, pump, to_bar=None, strokes=None, seconds=None, head=None, presta=False, recap=True):
    get_tool(r, pump)
    approach(r, bike)
    rec = r.state["entities"][bike]
    if head and (r.state["entities"][pump].get("settings") or {}).get("head") != head:
        r.do("set", target=pump, setting="head", value=head)
    fs = rec.get("_fasteners") or {}
    if part + "_cap" in fs and float(fs[part + "_cap"]["torque_nm"]) > 0.01:
        r.do("loosen", target=bike, fastener=part + "_cap")
    if presta and part + "_valve_nut" in fs and float(fs[part + "_valve_nut"]["torque_nm"]) > 0.01:
        r.do("loosen", target=bike, fastener=part + "_valve_nut")
    args = {"target": bike, "tool": pump, "part": part}
    for key, value in (("to_bar", to_bar), ("strokes", strokes), ("seconds", seconds)):
        if value is not None:
            args[key] = value
    r.do("inflate", **args)
    if recap:
        fs = r.state["entities"][bike].get("_fasteners") or {}
        if presta and part + "_valve_nut" in fs:
            r.do("tighten", target=bike, fastener=part + "_valve_nut")
        if part + "_cap" in fs:
            r.do("tighten", target=bike, fastener=part + "_cap")


@skill("tools_vehicles.unlock_car")
def unlock_car(r, car, fob):
    get_tool(r, fob)
    approach(r, car)
    if r.state["entities"][car].get("locked"):
        r.do("unlock", target=car)
    if not r.state["entities"][car].get("open"):
        r.do("open", target=car)


@skill("tools_vehicles.lock_car")
def lock_car(r, car, fob):
    approach(r, car)
    if r.state["entities"][car].get("open"):
        r.do("close", target=car)
    if not r.state["entities"][car].get("locked"):
        if fob not in _held(r):
            ensure_holding(r, fob)
            approach(r, car)
        if r.state["entities"][car].get("open"):
            r.do("close", target=car)      # fetching the fob from inside the cabin opened a door again
        r.do("lock", target=car)


@skill("tools_vehicles.install_mat")
def install_mat(r, mat, footwell, car=None, loose=False):
    keep = tuple(h for h in _held(r) if r.state["entities"][h].get("type") == "car_key_fob")
    free_hands(r, 1, keep=keep)
    ensure_holding(r, mat, keep=keep)
    approach(r, footwell)
    if loose:
        r.do("place", target=mat, destination=footwell)
    else:
        r.do("install", target=mat, destination=footwell, slot="mat")


@skill("tools_vehicles.replace_undersink_filter")
def replace_undersink_filter(r, housing, cabinet, faucet, old, new, trash, shut_valve=True, lock=True, flush_s=420, date=None):
    approach(r, cabinet)
    if not r.state["entities"][cabinet].get("open"):
        r.do("open", target=cabinet)
    if shut_valve:
        r.do("set", target=housing, setting="supply_valve", value="closed")
        r.do("switch_on", target=faucet)
        r.do("switch_off", target=faucet)
    free_hands(r, 2)
    r.do("crouch")
    r.do("loosen", target=housing, fastener="water_filter")
    r.do("uninstall", target=housing, slot="water_filter")
    r.do("stand")
    put(r, old, trash)
    ensure_holding(r, new)
    approach(r, housing)
    r.do("crouch")
    r.do("install", target=new, destination=housing, slot="water_filter")
    if lock:
        r.do("tighten", target=housing, fastener="water_filter")
    r.do("stand")
    r.do("set", target=housing, setting="supply_valve", value="open")
    if flush_s:
        r.do("switch_on", target=faucet)
        base.wait_skill(r, flush_s)
        r.do("switch_off", target=faucet)
    r.do("set", target=housing, setting="filter_indicator", value="ok")
    approach(r, cabinet)
    r.do("close", target=cabinet)
    if date:
        r.do("remember", target="water_filter_changed", value=date)


@skill("tools_vehicles.lubricate_hinge")
def lubricate_hinge(r, door, lube, rag=None, amount="dab"):
    free_hands(r, 2)
    ensure_holding(r, lube)
    if r.state["entities"][lube].get("openable") and not r.state["entities"][lube].get("open"):
        r.do("open", target=lube)
    if rag:
        ensure_holding(r, rag, keep=(lube,))
    approach(r, door)
    r.do("lubricate", target=door, tool=lube, point="pin", amount=amount)
    if rag:
        set_down(r, rag)
    for _ in range(2):
        rec = r.state["entities"][door]
        r.do("close" if rec.get("open") else "open", target=door)
    if not r.state["entities"][door].get("open"):
        r.do("open", target=door)
    if r.state["entities"][lube].get("openable") and r.state["entities"][lube].get("open"):
        r.do("close", target=lube)


def _parse(message):
    try:
        return json.loads(message)
    except (TypeError, ValueError):
        return {}


@skill("tools_vehicles.inspect_seals")
def inspect_seals(r, targets, closely=True, to=None):
    """Inspect each door/window seal (paper-pull test when closely) and report the failing segments read from the result."""
    for t in targets:
        approach(r, t)
        rec = r.state["entities"][t]
        reopened = False
        if rec.get("open"):
            r.do("close", target=t)
            reopened = True
        result = r.do("inspect", target=t, where="closely") if closely else r.do("inspect", target=t)
        details = _parse(result.get("message"))
        failing = []
        test = details.get("seal_test")
        if isinstance(test, dict):
            failing = sorted(k for k, v in test.items() if not str(v).startswith("firm drag"))
        else:
            failing = sorted(k for k in (details.get("seal_visual") or {}) if k not in ("threshold", "glass"))
        args = {"target": t, "field": "seal_failures", "value": failing}
        if to:
            args["to"] = to
        r.do("report", **args)
        if reopened:
            r.do("open", target=t)


@skill("tools_vehicles.load_trunk")
def load_trunk(r, car, trunk, fob, items, cabin=None, cabin_items=(), close=True):
    unlock_car(r, car, fob)
    approach(r, trunk)
    if not r.state["entities"][trunk].get("open"):
        r.do("open", target=trunk)
    for item in items:
        free_hands(r, 2)
        put(r, item, trunk)
    for item in cabin_items:
        free_hands(r, 2)
        put(r, item, cabin)
    if close:
        approach(r, trunk)
        r.do("close", target=trunk)
    lock_car(r, car, fob)


@skill("tools_vehicles.fix_toilet")
def fix_toilet(r, toilet, fault, new_flapper=None):
    approach(r, toilet)
    if not r.state["entities"][toilet].get("cover_open"):
        r.do("open_cover", target=toilet)
    if fault == "chain":
        r.do("adjust_flapper", target=toilet, part="chain", value_mm=10)
    elif fault == "float":
        r.do("adjust_flapper", target=toilet, part="float", value_mm=-25)
    elif fault == "flapper":
        r.do("set", target=toilet, setting="supply_valve", value="closed")
        free_hands(r, 2)
        r.do("uninstall", target=toilet, slot="flapper")
        set_down(r, r.state["robot"]["hands"]["right"] or r.state["robot"]["hands"]["left"])
        ensure_holding(r, new_flapper)
        approach(r, toilet)
        r.do("install", target=new_flapper, destination=toilet, slot="flapper")
        r.do("set", target=toilet, setting="supply_valve", value="open")
    r.do("close_cover", target=toilet)


@skill("tools_vehicles.unclog_drain")
def unclog_drain(r, sink, snake):
    free_hands(r, 1)
    get_tool(r, snake)
    approach(r, sink)
    if (r.state["entities"][sink].get("settings") or {}).get("stopper") == "installed":
        r.do("set", target=sink, setting="stopper", value="removed")
    r.do("snake_drain", target=sink, tool=snake)


@skill("tools_vehicles.reset_disposal")
def reset_disposal(r, disposal, hex_key, wait_s=200):
    base.reveal(r, disposal)
    if r.state["entities"][disposal].get("on"):
        approach(r, disposal)
        r.do("switch_off", target=disposal)
    get_tool(r, hex_key)
    base.reveal(r, disposal)
    approach(r, disposal)
    r.do("crouch")
    r.do("reset_disposal", target=disposal, tool=hex_key, step="free")
    rec = r.state["entities"][disposal]
    obj = (rec.get("_jam") or {}).get("object")
    r.do("stand")
    if obj:
        free_hands(r, 1, keep=(hex_key,))
        r.do("pick_up", target=obj)
        set_down(r, obj)
    if rec.get("thermal_tripped"):
        base.wait_skill(r, wait_s)
        r.do("crouch")
        r.do("reset_disposal", target=disposal, step="reset")
        r.do("stand")
    r.do("switch_on", target=disposal)
    base.wait_skill(r, 30)
    r.do("switch_off", target=disposal)
    cab = r.state["entities"][disposal].get("location")
    if cab in r.state["entities"] and r.state["entities"][cab].get("openable") and r.state["entities"][cab].get("open"):
        if not any(v is None for v in r.state["robot"]["hands"].values()):
            set_down(r, hex_key)
        r.do("close", target=cab)


@skill("tools_vehicles.clear_printer")
def clear_printer(r, printer, direction="feed", dispose=None):
    approach(r, printer)
    free_hands(r, 1)
    if not r.state["entities"][printer].get("cover_open"):
        r.do("open_cover", target=printer)
    r.do("clear_jam", target=printer, direction=direction)
    r.do("close_cover", target=printer)
    jam = next((h for h in _held(r) if r.state["entities"][h].get("part_type") == "jammed_paper"), None)
    if jam and dispose:
        put(r, jam, dispose)
    elif jam:
        set_down(r, jam)


@skill("tools_vehicles.bleed_radiator")
def bleed_radiator(r, radiator, key, towel=None):
    free_hands(r, 2)
    if towel:
        ensure_holding(r, towel)
    ensure_holding(r, key, keep=(towel,) if towel else ())
    approach(r, radiator)
    r.do("bleed_radiator", target=radiator, tool=key, close_on="water")
