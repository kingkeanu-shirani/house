"""Room occupancy grids, positions, paths, heights and reach (fidelity G).

Rooms are rectangles of size_m [width, depth] split into CELL x CELL meter cells.
An entity located directly in a room has pos [col, row] (its top-left cell) and a
footprint from size_m [w, d, h] (swapped when rot is 90/270). Furniture-like
entities with blocks_path=True are obstacles; small floor items are not obstacles
but may be floor hazards (floor_hazard: "trip", "slip", "sharp", "fragile", ...).
People and pets occupy their pos cell and are dynamic obstacles.
"""
import heapq
import math

CELL = 0.5
REACH_CELLS = 1          # Chebyshev distance from the robot cell to a footprint cell
LOW_REACH_M = 0.35       # grasping below this height requires crouching or kneeling
DEFAULT_REACH_M = 1.85
NEIGHBORS = [(1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
             (1, 1, 1.4142), (1, -1, 1.4142), (-1, 1, 1.4142), (-1, -1, 1.4142)]


def grid_size(room):
    w, d = (room.get("size_m") or [4.0, 4.0])[:2]
    return max(1, int(math.ceil(w / CELL - 1e-9))), max(1, int(math.ceil(d / CELL - 1e-9)))


def footprint_size(entity):
    size = entity.get("size_m") or [0.1, 0.1, 0.1]
    w, d = size[0], size[1]
    if entity.get("rot", 0) in (90, 270):
        w, d = d, w
    return max(1, int(math.ceil(w / CELL - 1e-9))), max(1, int(math.ceil(d / CELL - 1e-9)))


def footprint(entity):
    pos = entity.get("pos")
    if not pos:
        return []
    cols, rows = footprint_size(entity)
    x0, y0 = int(pos[0]), int(pos[1])
    return [(x, y) for x in range(x0, x0 + cols) for y in range(y0, y0 + rows)]


def in_grid(room, cell):
    cols, rows = grid_size(room)
    return 0 <= cell[0] < cols and 0 <= cell[1] < rows


def blocks(entity):
    """Path obstacle: blocks_path, or a floor-standing mount taller than 0.3 m (closet shelving, a furnace, a
    built-in closet) whatever its blocks_path says -- nothing walks through those. The robot dock is standable."""
    if entity.get("blocks_path"):
        return True
    if entity.get("mounted") and not entity.get("ground_layer") and "connects" not in entity:
        try:
            height = float(entity.get("mount_height_m", entity.get("z_m", 0.0)) or 0.0)
            tall = float((entity.get("size_m") or [0, 0, 0])[2])
        except (TypeError, ValueError, IndexError):
            return False
        if height <= 0.05 and tall > 0.3 and entity.get("category") not in FLOOR_MOUNT_SKIP and not is_standable(entity):
            return True
    return False


def front_clear(entity):
    """Operable thin wall fixtures low enough to be worked from the floor (valves, meters, panels, cleanouts,
    access hatches): nothing should be stood in front of them, though they do not block walking."""
    if not entity.get("mounted") or entity.get("ground_layer"):
        return False
    try:
        height = float(entity.get("mount_height_m", entity.get("z_m", 0.0)) or 0.0)
    except (TypeError, ValueError):
        return False
    if height > 0.9:
        return False      # higher valves (a washer box at 1.1 m) are worked over the appliance below them
    words = " ".join(str(x) for x in [entity.get("category"), entity.get("type")] + list(entity.get("is_a") or []))
    return any(w in words for w in FRONT_CLEAR_WORDS)


def floor_vent(entity):
    """Flush floor registers/vents/grates: walkable, but nothing floor-standing may cover them."""
    if not entity.get("mounted") and not entity.get("ground_layer"):
        return False
    try:
        height = float(entity.get("mount_height_m", entity.get("z_m", 0.0)) or 0.0)
        tall = float((entity.get("size_m") or [0, 0, 0])[2])
    except (TypeError, ValueError, IndexError):
        return False
    words = " ".join(str(x) for x in [entity.get("category"), entity.get("type")])
    return height <= 0.05 and tall <= 0.05 and any(w in words for w in ("register", "vent", "grate", "grille", "drain"))


FRONT_CLEAR_WORDS = ("valve", "meter", "breaker", "panel", "cleanout", "access_hatch", "shutoff", "fuse_box")


def access_fixture(entity):
    """Ground-layer things that must stay reachable and uncovered: drain grates, floor drains, hatches."""
    return bool(entity.get("ground_layer") and (entity.get("openable") or entity.get("access_target")))


def room_entities(state, rid):
    return [eid for eid, e in state["entities"].items() if e.get("location") == rid]


def blocked_cells(state, rid, ignore=(), include_agents=True):
    cells = {}
    for eid in room_entities(state, rid):
        if eid in ignore:
            continue
        entity = state["entities"][eid]
        if blocks(entity):
            for cell in footprint(entity):
                cells.setdefault(cell, []).append(eid)
    if include_agents:
        for cell, aid in agent_cells(state, rid, ignore):
            cells.setdefault(cell, []).append(aid)
    return cells


def agent_cells(state, rid, ignore=()):
    """[(cell, id)] of the people and pets standing in a room (dynamic obstacles)."""
    out = []
    for group in ("people", "pets"):
        for aid, agent in state.get(group, {}).items():
            if aid in ignore or agent.get("location") != rid or not agent.get("pos"):
                continue
            out.append((tuple(agent["pos"]), aid))
    return out


def hazard_cells(state, rid):
    """cell -> list of (kind, source). Sources: floor items and room wet_cells."""
    hazards = {}
    from .plugins import HOOKS
    veto = HOOKS.get("visible_veto", [])
    for eid in room_entities(state, rid):
        entity = state["entities"][eid]
        if blocks(entity) or entity.get("mounted") or "connects" in entity:
            continue
        kind = entity.get("floor_hazard")
        if entity.get("ground_layer") and not kind:
            continue   # rugs and floor surfaces are walkable unless explicitly hazardous (e.g. a puddle)
        if veto and any(fn(state, eid, entity) for _, _, fn in veto):
            continue   # hazards the robot has not perceived must not steer planning
        if not kind and entity.get("portable"):
            kind = "floor_item"   # loose object on the floor: stepping on it can trip, break or crush
        if kind:
            for cell in footprint(entity) or []:
                hazards.setdefault(cell, []).append((kind, eid))
    room = state["rooms"][rid]
    for cell in room.get("wet_cells", []):
        hazards.setdefault(tuple(cell), []).append(("wet", rid))
    for item in room.get("floor_hazards", []):
        hazards.setdefault(tuple(item["cell"]), []).append((item["kind"], item.get("source", rid)))
    for group in ("people", "pets"):
        for aid, agent in state.get(group, {}).items():
            if agent.get("inside"):
                continue   # in an appliance, a crate or a cupboard: not lying on the floor (textiles_laundry request 3)
            if agent.get("location") == rid and agent.get("pos") and agent.get("posture") in ("sleeping", "lying", "fallen"):
                hazards.setdefault(tuple(agent["pos"]), []).append(("sleeping" if agent.get("posture") == "sleeping" else "person_down", aid))
    return hazards


def chebyshev(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def near_cells(cells, room, radius=REACH_CELLS):
    result = set()
    for (x, y) in cells:
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                cell = (x + dx, y + dy)
                if in_grid(room, cell):
                    result.add(cell)
    return result


_SOURCES_ARITY = {}


def _hook_takes_sources(fn):
    """path_blocked hooks may take a 4th parameter: the hazard sources to ignore (the approach target and what it
    rests on); hooks that derive rings from agents/objects must drop rings from those sources."""
    if fn not in _SOURCES_ARITY:
        import inspect
        try:
            params = [p for p in inspect.signature(fn).parameters.values() if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
            _SOURCES_ARITY[fn] = len(params) >= 4
        except (TypeError, ValueError):
            _SOURCES_ARITY[fn] = False
    return _SOURCES_ARITY[fn]


def target_sources(state, target):
    """The target and everything it rests on/in up to the room (hazard sources an approach to it must not avoid:
    a sleeping pet's own ring, a hot stove under the pot, a vase's own fragile ring). For a person in bed: the bed
    and the others lying in it."""
    for group in ("people", "pets"):
        agent = state.get(group, {}).get(target)
        if agent is not None:
            out = [target]
            bed = agent.get("in_bed") or agent.get("bed")
            if isinstance(bed, str):
                out.append(bed)
                for other_group in ("people", "pets"):
                    for oid, other in sorted(state.get(other_group, {}).items()):
                        if oid != target and (other.get("in_bed") == bed or other.get("bed") == bed):
                            out.append(oid)
            return tuple(out)
    out, current = [], target
    while current and current not in out and current not in state.get("rooms", {}):
        out.append(current)
        record = state["entities"].get(current)
        current = record.get("location") if record else None
    return tuple(out)


# Avoid-kind aliases: "lying" is the hazard kind realistic observations show for anyone lying down (asleep or awake,
# fallen or not), so avoiding it avoids the agent markers hazard_cells writes ("sleeping", "person_down") too.
AVOID_ALIASES = {"lying": ("sleeping", "person_down")}


def expand_avoid(avoid_kinds):
    """avoid_kinds as a tuple with AVOID_ALIASES added after the kinds that imply them (order kept, no duplicates).
    find_path applies it (and passes the expanded kinds to path_blocked hooks); domains that match hazard_cells kinds
    against an avoid list themselves should use it too."""
    out = []
    for kind in avoid_kinds or ():
        for k in (kind,) + tuple(AVOID_ALIASES.get(kind, ()) if isinstance(kind, str) else ()):
            if k not in out:
                out.append(k)
    return tuple(out)


def find_path(state, rid, start, goals, avoid_kinds=(), ignore=(), extra_blocked=(), ignore_sources=(), hazard_filter=None):
    """A* over the room grid. Returns a list of cells from start (exclusive) to a goal.

    Obstacles and other agents are impassable. Hazard cells whose kind is listed in
    avoid_kinds (with AVOID_ALIASES: "lying" also means "sleeping" and "person_down") are
    impassable too, unless every entry on the cell comes from a source in
    ignore_sources; other hazards are walked over (and their effects are applied by the
    navigation system). Diagonal moves may not cut corners.

    hazard_filter (realistic perception, e.g. Realism.route_hazards): fn(state, rid, avoid_kinds, ignore_sources) ->
    (kinds for path_blocked hooks, cells to keep out of). It replaces the kind matching against hazard_cells, so the
    plan uses only the hazards the robot perceives (no route around a sleeper it cannot tell is asleep).
    """
    room = state["rooms"][rid]
    start = tuple(start)
    goals = {tuple(g) for g in goals if in_grid(room, tuple(g))}
    if not goals:
        return None
    avoid_cells = None
    if hazard_filter is not None:
        hook_kinds, avoid_cells = hazard_filter(state, rid, tuple(avoid_kinds or ()), tuple(ignore_sources or ()))
        avoid_kinds = hook_kinds = expand_avoid(hook_kinds)
    else:
        avoid_kinds = hook_kinds = expand_avoid(avoid_kinds)
    blocked = set(blocked_cells(state, rid, ignore=ignore)) | {tuple(c) for c in extra_blocked}
    from .plugins import HOOKS
    for _, _, fn in HOOKS.get("path_blocked", []):
        cells = fn(state, rid, hook_kinds, ignore_sources) if _hook_takes_sources(fn) else fn(state, rid, hook_kinds)
        blocked |= {tuple(c) for c in (cells or ())}
    if avoid_cells is not None:
        blocked |= {tuple(c) for c in avoid_cells}
    elif avoid_kinds:
        for cell, items in hazard_cells(state, rid).items():
            if any(kind in avoid_kinds and src not in ignore_sources for kind, src in items):
                blocked.add(cell)
    blocked.discard(start)
    goals = {g for g in goals if g not in blocked or g == start}
    if not goals:
        return None
    if start in goals:
        return []
    def h(cell):
        return min(chebyshev(cell, g) for g in goals)
    frontier = [(h(start), 0.0, start)]
    came, cost = {start: None}, {start: 0.0}
    while frontier:
        _, g, current = heapq.heappop(frontier)
        if current in goals:
            path = []
            while current != start:
                path.append(current)
                current = came[current]
            return list(reversed(path))
        if g > cost.get(current, float("inf")):
            continue
        for dx, dy, step in NEIGHBORS:
            nxt = (current[0] + dx, current[1] + dy)
            if not in_grid(room, nxt) or nxt in blocked:
                continue
            if dx and dy and ((current[0] + dx, current[1]) in blocked or (current[0], current[1] + dy) in blocked):
                continue
            new = g + step
            if new < cost.get(nxt, float("inf")):
                cost[nxt], came[nxt] = new, current
                heapq.heappush(frontier, (new + h(nxt), new, nxt))
    return None


def path_length_m(path):
    total, prev = 0.0, None
    for cell in path:
        if prev is not None:
            total += CELL * (1.4142 if prev[0] != cell[0] and prev[1] != cell[1] else 1.0)
        prev = cell
    return total


def anchor(state, eid):
    """(room, footprint cells) where an entity can be reached from."""
    entities = state["entities"]
    entity = entities.get(eid)
    robot_room = state["robot"]["room"]
    if entity is not None and entity.get("connects") and robot_room in entity["connects"]:
        # Doors, gates and windows between rooms are reachable from either side.
        for conn in state.get("connections", {}).values():
            if conn.get("door") == eid and robot_room in conn.get("cells", {}):
                return robot_room, [tuple(conn["cells"][robot_room])]
        return robot_room, footprint(entity) or None
    current, seen = eid, set()
    while current not in seen:
        seen.add(current)
        if current == "robot":
            robot = state["robot"]
            return robot["room"], [tuple(robot.get("pos", (0, 0)))]
        for group in ("people", "pets"):
            if current in state.get(group, {}):
                agent = state[group][current]
                return agent.get("location"), [tuple(agent.get("pos") or (0, 0))]
        if current in state["rooms"]:
            return current, None
        entity = entities.get(current)
        if entity is None:
            return None, None
        location = entity.get("location")
        if location in state["rooms"]:
            cells = footprint(entity)
            return location, cells or None
        current = location
    return None, None


def height_m(state, eid, _depth=0):
    """Height of an entity's base above its floor, in meters."""
    entities = state["entities"]
    entity = entities.get(eid)
    if entity is None or _depth > 30:
        return 0.0
    if "z_m" in entity:
        return float(entity["z_m"])
    location = entity.get("location")
    if location in state["rooms"]:
        return float(entity.get("mount_height_m", 0.0))
    if location == "robot":
        return 1.0
    if location in state.get("people", {}) or location in state.get("pets", {}):
        return 0.9 if location in state.get("people", {}) else 0.2
    parent = entities.get(location)
    if parent is None:
        return 0.0
    base = height_m(state, location, _depth + 1)
    relation = entity.get("relation", "on")
    if relation in ("under", "behind"):
        return base
    if relation == "hung":
        return base + float(parent.get("hang_height_m", parent.get("size_m", [0, 0, 1.5])[2]))
    if relation == "in":
        shelf = entity.get("shelf")
        heights = parent.get("shelf_heights")
        if shelf is not None and heights and 0 <= int(shelf) < len(heights):
            return base + float(heights[int(shelf)])
        return base + float(parent.get("inner_floor_m", 0.05))
    surface = parent.get("surface_height_m")
    if surface is None:
        surface = (parent.get("size_m") or [0, 0, 0.1])[2]
    return base + float(surface)


def mounted_overhead(state, eid):
    """True for wall-mounted things above counter height (or items inside/on them)."""
    entities = state["entities"]
    current, depth = eid, 0
    while current in entities and depth < 6:
        record = entities[current]
        if record.get("location") in state["rooms"]:
            return bool(record.get("mounted")) and float(record.get("mount_height_m", 0.0)) >= 0.85
        current = record.get("location")
        depth += 1
    return False


def robot_reach_m(state):
    robot = state["robot"]
    reach = float(robot.get("reach_m", DEFAULT_REACH_M))
    extension = 0.0
    for held in robot.get("hands", {}).values():
        if held and held in state["entities"]:
            extension = max(extension, float(state["entities"][held].get("reach_extension_m", 0.0) or 0.0))
    reach += extension
    standing_on = robot.get("standing_on")
    if standing_on and standing_on in state["entities"]:
        reach += float(state["entities"][standing_on].get("standing_height_m", 0.0))
    return reach


def reach_problem(state, eid, grasp=False):
    """None if the robot can physically reach eid now; otherwise a reason string."""
    robot = state["robot"]
    if eid in [v for v in robot.get("hands", {}).values() if v]:
        return None
    room, cells = anchor(state, eid)
    if room is None:
        return "That is not reachable"
    if room != robot.get("room"):
        # A switch or control mounted beside a doorway can be reached from the threshold.
        cid = state["entities"].get(eid, {}).get("near_connection")
        conn = state.get("connections", {}).get(cid) if cid else None
        if conn and robot.get("room") in conn.get("cells", {}) and room in conn["rooms"]:
            if chebyshev(tuple(robot.get("pos") or (0, 0)), tuple(conn["cells"][robot["room"]])) <= REACH_CELLS:
                door = state["entities"].get(conn.get("door") or "")
                if door is None or door.get("open"):
                    return None
        return "Go to the room containing it first"
    if cells:
        rpos = tuple(robot.get("pos", (0, 0)))
        radius = REACH_CELLS
        if mounted_overhead(state, eid):
            radius += 1  # reaching over a 0.6 m counter to wall cabinets and shelves
        if min(chebyshev(rpos, c) for c in cells) > radius:
            return "Approach it first; it is out of arm's reach"
    if eid in state["entities"]:
        h = height_m(state, eid)
        if h > robot_reach_m(state) + 1e-9:
            return "Too high to reach safely; use a step stool, ladder or reacher"
        if grasp and h < LOW_REACH_M and robot.get("posture", "standing") == "standing":
            return "Crouch or kneel to grasp something this low"
    return None


CROSS = ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1))
FLOOR_MOUNT_SKIP = ("switch", "window", "light", "outlet", "hooks", "wall", "ceiling", "floor")
ESCAPE_ROOM_TYPES = {"front_yard", "driveway", "sidewalk", "porch", "building_corridor", "lobby", "elevator", "mail_room"}
HINGED_CATEGORIES = ("door", "gate")
NON_SWING_HANDLES = ("sliding", "opener", "pocket")
NON_SWING_KINDS = ("sliding_door", "pocket_door", "bypass_door", "closet_bypass", "closet_pocket", "overhead_door")


def floor_standing(entity):
    """True if an entity located in a room takes its floor cells: furniture, appliances, loose floor items and
    floor-standing mounts (mounted below 0.5 m and at least 0.25 m deep, or sitting on the floor). Wall/ceiling mounts
    above that, thin low wall mounts, switches, windows, lights, outlets, hooks, ground layers (rugs, floor surfaces)
    and doors do not."""
    if entity.get("ground_layer") or "connects" in entity:
        return False
    if entity.get("mounted"):
        if entity.get("category") in FLOOR_MOUNT_SKIP:
            return False
        try:
            height = float(entity.get("mount_height_m", entity.get("z_m", 0.0)) or 0.0)
            size = entity.get("size_m") or [0.5, 0.5, 0.5]
            depth = min(float(size[0]), float(size[1]))
            tall = float(size[2]) if len(size) > 2 else 0.5
        except (TypeError, ValueError, IndexError):
            return True
        if height <= 0.05:
            return tall > 0.05      # floor-level: a closet or furnace stands there; a flush floor register does not
        # thin wall mounts off the floor (radiators, registers, photo eyes) leave the floor in front of them free
        return height < 0.5 and depth >= 0.25
    return True


def doorway_cells(state, rid):
    """Doorway cells of a room in connection-id order."""
    return [tuple(c["cells"][rid]) for _, c in sorted(state.get("connections", {}).items()) if rid in c.get("cells", {})]


def wall_inward(room, cell):
    """(inward, lateral) unit steps for a perimeter cell: inward points into the room, lateral along the wall."""
    cols, rows = grid_size(room)
    x, y = int(cell[0]), int(cell[1])
    if x == 0:
        return (1, 0), (0, 1)
    if x == cols - 1:
        return (-1, 0), (0, 1)
    if y == 0:
        return (0, 1), (1, 0)
    return (0, -1), (1, 0)


def along_wall(room, record):
    """Lay a wall-mounted record whose footprint is longer one way (a 1.0 m window is 2 cells by 1) along the wall its
    pos cell is on: rot 90 on the west/east walls, 0 on the north/south walls (wall_inward decides a corner), and move
    its start back along the wall so the whole footprint stays on the grid. A square footprint keeps its rot; only its
    pos may move. Returns the record (changed in place)."""
    if not record.get("pos"):
        return record
    cols, rows = grid_size(room)
    x, y = int(record["pos"][0]), int(record["pos"][1])
    w, d = footprint_size(dict(record, rot=0))
    if w != d:
        inward, _ = wall_inward(room, (x, y))
        side_wall = inward[0] != 0          # west/east wall: the long side must run along y
        record["rot"] = (90 if w > d else 0) if side_wall else (0 if w > d else 90)
    fc, fr = footprint_size(record)
    record["pos"] = [max(0, min(x, cols - fc)), max(0, min(y, rows - fr))]
    return record


def _door_conn(state, door_id):
    for cid in sorted(state.get("connections", {})):
        if state["connections"][cid].get("door") == door_id:
            return cid, state["connections"][cid]
    return None, None


def swing_room(state, door_id):
    """Room a hinged door/gate swings into (the single shared rule): explicit ``mech.swing_into`` or ``swing_into``;
    else the indoor side of an exterior door; else the dwelling side of a door onto a shared corridor/lobby or other
    escape-route room; else the second room of the pair."""
    rec = state["entities"].get(door_id) or {}
    explicit = (rec.get("mech") or {}).get("swing_into") or rec.get("swing_into")
    if explicit:
        return explicit
    rooms = list(rec.get("connects") or [])
    if len(rooms) != 2:
        _, conn = _door_conn(state, door_id)
        rooms = list(conn["rooms"]) if conn else []
    if len(rooms) != 2:
        return None
    a, b = rooms
    ra, rb = state["rooms"].get(a) or {}, state["rooms"].get(b) or {}
    if ra.get("outdoor") and not rb.get("outdoor"):
        return b
    if rb.get("outdoor") and not ra.get("outdoor"):
        return a

    def private(room):
        if room.get("outdoor"):
            return False
        if "escape_zone" in room:
            return not room["escape_zone"]
        return room.get("type") not in ESCAPE_ROOM_TYPES and not room.get("shared")
    if private(ra) and not private(rb):
        return a
    if private(rb) and not private(ra):
        return b
    return b


def is_overhead_door(entity, conn=None):
    return (conn or {}).get("kind") == "garage" or entity.get("category") == "garage_door" or \
        entity.get("type") == "garage_door" or "garage_door" in (entity.get("is_a") or [])


def door_swing(state, door_id):
    """(room, cells) a hinged door leaf sweeps when it opens: the inward cell, the hinge-side diagonal and, for
    leaves wider than 0.95 m, the second inward cell (hinge "far" mirrors the diagonal). Sliding, pocket and
    overhead doors and plain doorways return (None, [])."""
    door = state["entities"].get(door_id) or {}
    if not door.get("connects") or door.get("category") not in HINGED_CATEGORIES or door.get("handle_kind") in NON_SWING_HANDLES:
        return None, []
    if str((door.get("mech") or {}).get("kind") or "") in NON_SWING_KINDS:
        return None, []
    _, conn = _door_conn(state, door_id)
    if conn is None or is_overhead_door(door, conn):
        return None, []
    rid = swing_room(state, door_id)
    if not rid or rid not in conn.get("cells", {}) or rid not in state["rooms"]:
        return None, []
    room = state["rooms"][rid]
    x, y = (int(v) for v in conn["cells"][rid])
    inward, lateral = wall_inward(room, (x, y))
    if door.get("hinge") == "far":
        lateral = (-lateral[0], -lateral[1])
    cells = [(x + inward[0], y + inward[1]), (x + inward[0] + lateral[0], y + inward[1] + lateral[1])]
    words = " ".join(str(v) for v in [door.get("type"), door.get("label"), (door.get("mech") or {}).get("kind")] + list(door.get("is_a") or []))
    leaves = (door.get("mech") or {}).get("leaves", 1)
    try:
        n_leaves = len(leaves) if isinstance(leaves, (dict, list)) else int(leaves or 1)
    except (TypeError, ValueError):
        n_leaves = 1
    double = n_leaves >= 2 or "double" in words or "french" in words
    if double:
        cells.append((x + inward[0] - lateral[0], y + inward[1] - lateral[1]))
    try:
        wide = float((door.get("size_m") or [0.85])[0]) > 0.95
    except (TypeError, ValueError):
        wide = False
    if wide and not double:
        cells.append((x + 2 * inward[0], y + 2 * inward[1]))
    return rid, [c for c in cells if in_grid(room, c)]


def overhead_door_cells(state, door_id):
    """{room: [cells]} an overhead (garage) door's threshold spans on each side: the doorway cell is the centre of a
    run of round(width / CELL) cells along the wall."""
    door = state["entities"].get(door_id) or {}
    _, conn = _door_conn(state, door_id)
    if conn is None:
        return {}
    try:
        width = float((door.get("size_m") or [2.5])[0])
    except (TypeError, ValueError):
        width = 2.5
    span = max(1, int(round(width / CELL)))
    out = {}
    for rid, cell in sorted(conn.get("cells", {}).items()):
        if rid not in state["rooms"]:
            continue
        room = state["rooms"][rid]
        cols, rows = grid_size(room)
        x, y = int(cell[0]), int(cell[1])
        if y in (0, rows - 1) and not (x in (0, cols - 1) and rows > cols):
            cells = [(x + dx, y) for dx in range(-(span // 2), span - span // 2)]
        else:
            cells = [(x, y + dy) for dy in range(-(span // 2), span - span // 2)]
        out[rid] = [c for c in cells if in_grid(room, c)]
    return out


def door_clearance(state, rid):
    """Cells kept clear around a room's doorways: each doorway cell and its four neighbours, the leaf sweep of a
    hinged door that swings into this room (see door_swing), and an overhead door's threshold run plus the row
    inside it."""
    room = state["rooms"][rid]
    cells = set()
    for cid, conn in sorted(state.get("connections", {}).items()):
        cell = conn.get("cells", {}).get(rid)
        if cell is None:
            continue
        x, y = int(cell[0]), int(cell[1])
        for dx, dy in CROSS:
            cells.add((x + dx, y + dy))
        door_id = conn.get("door")
        door = state["entities"].get(door_id) if door_id else None
        if not door:
            continue
        if is_overhead_door(door, conn):
            inward, _ = wall_inward(room, (x, y))
            for c in overhead_door_cells(state, door_id).get(rid, []):
                cells.add(c)
                cells.add((c[0] + inward[0], c[1] + inward[1]))
        else:
            srid, leaf = door_swing(state, door_id)
            if srid == rid:
                cells.update(leaf)
    return {c for c in cells if in_grid(room, c)}


def reachable_cells(state, rid, blocked=None):
    """Cells reachable (4-connected) from the room's doorways, avoiding blocking footprints."""
    room = state["rooms"][rid]
    if blocked is None:
        blocked = set(blocked_cells(state, rid, include_agents=False))
    starts = [tuple(c["cells"][rid]) for _, c in sorted(state.get("connections", {}).items()) if rid in c.get("cells", {})]
    reach, frontier = set(), []
    for start in starts:
        if start not in blocked and start not in reach:
            reach.add(start)
            frontier.append(start)
    while frontier:
        x, y = frontier.pop()
        for nxt in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if nxt not in reach and nxt not in blocked and in_grid(room, nxt):
                reach.add(nxt)
                frontier.append(nxt)
    return reach


# ----- connectivity and access ------------------------------------------------
def placement_hazards(state, rid):
    """hazard_cells plus hazards that systems derive only at run time (``@hook("derived_hazards")``:
    fn(state, rid) -> iterable of (cell, kind, source)), e.g. a vacuum dock's keep-clear zone or a cord run.
    Used when placing things at generation/setup time, before those systems have run."""
    hazards = {cell: list(items) for cell, items in hazard_cells(state, rid).items()}
    from .plugins import HOOKS
    for _, _, fn in HOOKS.get("derived_hazards", []):
        for cell, kind, source in fn(state, rid) or ():
            hazards.setdefault(tuple(cell), []).append((kind, source))
    return hazards


def components(cells):
    """{cell: component id} for 4-connected components of a cell set (deterministic ids)."""
    comp, n = {}, 0
    for start in sorted(cells):
        if start in comp:
            continue
        n += 1
        comp[start] = n
        stack = [start]
        while stack:
            x, y = stack.pop()
            for nxt in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if nxt in cells and nxt not in comp:
                    comp[nxt] = n
                    stack.append(nxt)
    return comp


def _nbrs4(cell):
    x, y = cell
    return ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))


def articulation_cells(cells):
    """Cut cells of the 4-connected grid graph: removing one disconnects some other cells (iterative Tarjan)."""
    cells = set(cells)
    disc, low, cut, counter = {}, {}, set(), 0
    for root in sorted(cells):
        if root in disc:
            continue
        disc[root] = low[root] = counter
        counter += 1
        root_children = 0
        stack = [(root, None, iter(_nbrs4(root)))]
        while stack:
            node, parent, it = stack[-1]
            advanced = False
            for nxt in it:
                if nxt not in cells or nxt == parent:
                    continue
                if nxt in disc:
                    low[node] = min(low[node], disc[nxt])
                    continue
                disc[nxt] = low[nxt] = counter
                counter += 1
                if node == root:
                    root_children += 1
                stack.append((nxt, node, iter(_nbrs4(nxt))))
                advanced = True
                break
            if advanced:
                continue
            stack.pop()
            if parent is not None:
                low[parent] = min(low[parent], low[node])
                if parent != root and low[node] >= disc[parent]:
                    cut.add(parent)
        if root_children > 1:
            cut.add(root)
    return cut


def access_radius(entity):
    """Cells from which the robot can work on it: 2 for things on the wall at or above counter height (reached over a
    counter, a bed edge or a sill), else 1."""
    try:
        height = float(entity.get("mount_height_m", 0.0) or 0.0)
    except (TypeError, ValueError):
        height = 0.0
    return 2 if (entity.get("mounted") or "mount_height_m" in entity) and height >= 0.85 else 1


def is_standable(entity):
    """Things the robot must stand on (its charging dock)."""
    return entity.get("type") == "robot_dock" or "robot_dock" in (entity.get("is_a") or [])


BASIC_MOUNTS = ("window", "switch", "thermostat", "valve", "breaker_panel", "panel", "control", "drawer")


def access_targets(state, rid, level="all", ignore=()):
    """{key: (cells, radius, standable)} the robot must be able to get to in a room. Doorways (standable) and the
    robot dock (standable) always; floor-standing furniture/appliances/fixtures, loose floor items and operable
    mounts (BASIC_MOUNTS: windows, switches, thermostats, valves, panels) for level "basic"; every mounted entity
    except lights (outlets, wall cabinets, hooks, coverings...) for level "all"."""
    room = state["rooms"][rid]
    targets = {}
    for cid, conn in sorted(state.get("connections", {}).items()):
        if rid in conn.get("cells", {}):
            targets[("door", cid)] = ([tuple(conn["cells"][rid])], 0, True)
    for eid in room_entities(state, rid):
        if eid in ignore:
            continue
        entity = state["entities"][eid]
        if (entity.get("ground_layer") and not access_fixture(entity)) or "connects" in entity or not entity.get("pos"):
            continue
        cells = [c for c in footprint(entity) if in_grid(room, c)]
        if access_fixture(entity):
            if level in ("basic", "all") and cells:
                targets[eid] = (cells, 1, False)
            continue
        if not cells:
            continue
        if is_standable(entity):
            targets[eid] = (cells, 0, True)
        elif floor_standing(entity):
            if level in ("basic", "all"):
                targets[eid] = (cells, access_radius(entity), False)
        elif (entity.get("mounted") or "mount_height_m" in entity) and                 (level == "all" or (level == "basic" and entity.get("category") in BASIC_MOUNTS)):
            if entity.get("category") == "light" and overhead_light(entity, room):
                continue      # ceiling lights are reached with a ladder from any cell below them
            targets[eid] = (cells, access_radius(entity), False)
    return targets


SERVICE_LIGHT_MAX_M = 2.5    # wall lights a person re-lamps from a step stool beside their wall


def overhead_light(entity, room=None):
    """Lights serviced with a ladder from any cell below them, which are therefore not access targets: indoor lights
    mounted 2.0 m or higher (ceiling lights). Outdoor wall lights -- flagged ``outdoor``, of the outdoor_light family,
    or mounted in an outdoor room (which has no ceiling) -- up to SERVICE_LIGHT_MAX_M are serviced from the ground
    beside their wall and stay targets, so nothing may seal them off."""
    height = _num_or(entity.get("mount_height_m", 0.0) or 0.0, 0.0)
    if height < 2.0 or entity.get("outdoor"):
        return False
    outdoor = bool((room or {}).get("outdoor")) or entity.get("type") == "outdoor_light" or \
        "outdoor_light" in (entity.get("is_a") or [])
    return not (outdoor and height <= SERVICE_LIGHT_MAX_M + 1e-9)


def access_report(state, rid, blocked, targets):
    """Which doorways share a walkable component and which targets are accessible, given impassable cells.
    Returns {"groups": frozenset of frozensets of door keys, "ok": set of accessible target keys}."""
    room = state["rooms"][rid]
    cols, rows = grid_size(room)
    walk = {(x, y) for y in range(rows) for x in range(cols) if (x, y) not in blocked}
    comp = components(walk)
    doors = {k: comp.get(cells[0]) for k, (cells, _, _) in targets.items() if k[0:1] == ("door",)}
    main = {c for c in doors.values() if c is not None}
    if not main and comp:
        sizes = {}
        for c in comp.values():
            sizes[c] = sizes.get(c, 0) + 1
        main = {max(sorted(sizes), key=lambda c: sizes[c])}
    groups = {}
    for key, c in doors.items():
        if c is not None:
            groups.setdefault(c, set()).add(key)
    ok = set()
    for key, (cells, radius, standable) in targets.items():
        if standable:
            if any(comp.get(c) in main for c in cells):
                ok.add(key)
            continue
        own = set(cells)
        if any(comp.get(n) in main for n in near_cells(cells, room, radius) if n not in own):
            ok.add(key)
    return {"groups": frozenset(frozenset(g) for g in groups.values()), "ok": ok}


def not_worse(before, after, new_keys=()):
    """True if no doorway pair that was connected got separated, every target accessible before still is, and every
    new target is accessible."""
    for group in before["groups"]:
        if len(group) > 1 and not any(group <= g for g in after["groups"]):
            return False
    if not before["ok"] <= after["ok"]:
        return False
    return all(k in after["ok"] for k in new_keys)


def free_floor_cell(state, rid, near=None, ignore=(), record=None, clearance=True, reachable=True, strict=None,
                    keep_access=False, extra_used=(), avoid=(), prefer_not=(), ring=None):
    """A free cell for an item or agent: not blocked, not used by anything floor-standing, not a hazard, outside the
    doorway clearance (door_clearance), reachable from a doorway, and (with record) fitting the record's whole
    footprint. Closest to `near` if given, else first in scan order. Returns None when nothing fits.

    strict: never fall back to cells inside the doorway clearance (default: True for a record 0.15 m or taller or
    one that blocks paths, False otherwise).
    keep_access (generation/setup placement): the item (or agent, record=None) must not cut the room's hazard-free
    walking routes: doorways that were connected stay connected and the robot dock, furniture, fixtures and other
    floor items that were accessible stay accessible without stepping on anything. A path-blocking record is always
    fully checked this way when keep_access is set. keep_access="plain" applies the same rule to the plain walking
    graph only (hazards passable): the fallback for a room whose hazard-free routes are already cut.
    extra_used: cells to treat as taken (stand-ins); avoid: hard no-go cells; prefer_not: cells used only if nothing
    else fits. ring: keep-out ring the placed thing will itself create (1 for fragile or hot items and for sleeping or
    lying people; default derived from the record). The people and pets already in the room (minus `ignore`) count as
    obstacles in the access checks, as they do for core routing, so the new item or agent never walls part of the room
    off together with them; an agent placement (record None) must land on floor joined to the doorways by 4-connected
    moves. Path-blocking records prefer cells that wall off no free floor."""
    room = state["rooms"][rid]
    cols, rows = grid_size(room)
    if strict is None:
        strict = record is not None and (blocks(record) or _tall(record))
    blocked = set(blocked_cells(state, rid, ignore=ignore))
    used = {tuple(c) for c in extra_used}
    for eid in room_entities(state, rid):
        other = state["entities"][eid]
        if eid not in ignore and (floor_standing(other) or front_clear(other) or access_fixture(other)):
            used.update(footprint(other))
    used.update(doorway_cells(state, rid))
    robot = state.get("robot") or {}
    if robot.get("room") == rid and robot.get("pos"):
        used.add(tuple(robot["pos"]))
    if ring is None:
        ring = 1 if record is not None and (record.get("fragile") or (_num_or(record.get("temperature_c"), 21.0) >= 50.0)) else 0
    hazards = placement_hazards(state, rid) if keep_access else hazard_cells(state, rid)
    hazard_set = {c for c, items in hazards.items() if any(src not in ignore for _, src in items)}
    used |= hazard_set
    static_blocked = set(blocked_cells(state, rid, ignore=ignore, include_agents=False))
    reach = reachable_cells(state, rid, static_blocked) if reachable else None
    checkers = []
    if keep_access:
        # the people and pets already standing in the room are walls to core routing (find_path, the reference skills'
        # strict safe_avoid): the new thing must not cut a route together with them
        agents = {cell for cell, _ in agent_cells(state, rid, ignore)}
        if keep_access != "plain":
            checkers.append(_AccessChecker(state, rid, static_blocked | agents | hazard_set, ignore, ring=ring, standable=record is None))
        if keep_access == "plain" or record is None or blocks(record):
            # people, pets and path-blocking things are physical obstacles: they must not cut plain routes either
            checkers.append(_AccessChecker(state, rid, static_blocked | agents, ignore, standable=record is None))

    hard = {tuple(c) for c in avoid}
    soft = {tuple(c) for c in prefer_not}

    def options_for(no_go):
        found = []
        for y in range(rows):
            for x in range(cols):
                cells = footprint(dict(record, pos=[x, y])) if record is not None else [(x, y)]
                if any(not in_grid(room, c) or c in blocked or c in used or c in no_go or c in hard for c in cells):
                    continue
                if reach is not None and reach and not any(n in reach for n in near_cells(cells, room)):
                    continue
                found.append(((x, y), cells))
        found.sort(key=lambda o: (any(c in soft for c in o[1]),) + ((chebyshev(o[0], near), o[0][1], o[0][0]) if near is not None else (o[0][1], o[0][0])))
        return found

    blocking = record is not None and blocks(record)
    passes = [door_clearance(state, rid)] if clearance else [set()]
    if clearance and not strict:
        passes.append(set())
    fallback = None
    for no_go in passes:
        for cell, cells in options_for(no_go):
            full = blocking or ring > 0
            if all(ch.allows(cells, full=full) for ch in checkers):
                if blocking and keep_access and _walls_off_floor(state, rid, static_blocked, cells):
                    fallback = fallback or list(cell)
                    continue
                return list(cell)
        if fallback is not None:
            return fallback
    return None


def _num_or(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _walls_off_floor(state, rid, blocked, cells):
    """True if blocking `cells` leaves some previously doorway-reachable free cell (outside the footprint)
    unreachable: a dead corner behind the new piece."""
    before = reachable_cells(state, rid, set(blocked))
    after = reachable_cells(state, rid, set(blocked) | set(cells))
    return bool(before - set(cells) - after)


def _tall(record):
    try:
        return float((record.get("size_m") or [0, 0, 0.1])[2]) >= 0.15
    except (TypeError, ValueError, IndexError):
        return True


class _AccessChecker:
    """Fast 'does occupying these cells cut any route?' test: cells that are neither cut cells of the hazard-free
    walk graph nor the only access cell of a target pass at once; others get a full before/after comparison."""
    FULL_CHECKS = 30

    def __init__(self, state, rid, impassable, ignore, ring=0, standable=False):
        self.state, self.rid, self.impassable = state, rid, set(impassable)
        self.ring, self.standable = ring, standable
        self.targets = access_targets(state, rid, level="all", ignore=ignore)
        self.before = access_report(state, rid, self.impassable, self.targets)
        room = state["rooms"][rid]
        cols, rows = grid_size(room)
        walk = {(x, y) for y in range(rows) for x in range(cols) if (x, y) not in self.impassable}
        self.cut = articulation_cells(walk)
        comp = components(walk)
        door_comps = {comp.get(cells[0]) for k, (cells, _, _) in self.targets.items() if k[0:1] == ("door",)} - {None}
        self.comp, self.main, self.room = comp, door_comps, room
        self.sole = set()
        for key in self.before["ok"]:
            cells, radius, standable = self.targets[key]
            if standable:
                continue
            own = set(cells)
            access = [n for n in near_cells(cells, room, radius) if n not in own and comp.get(n) in door_comps]
            if len(access) == 1:
                self.sole.add(access[0])
        self.full_left = self.FULL_CHECKS

    def allows(self, cells, full=False):
        """True if occupying `cells` keeps every route and access, and the new thing itself can be reached from
        the walkable area joined to the doorways without stepping on anything."""
        cells = set(cells)
        if self.standable and self.main and not all(self.comp.get(c) in self.main for c in cells):
            return False      # an agent must be able to walk out (no diagonal-only pockets)
        if self.main and not any(self.comp.get(n) in self.main for n in near_cells(cells, self.room) if n not in cells):
            return False
        if not full and not self.ring and len(cells) == 1 and not (cells & self.cut) and not (cells & self.sole):
            return True
        if self.full_left <= 0:
            return False
        self.full_left -= 1
        after = access_report(self.state, self.rid, self.impassable | set(cells), self.targets)
        if not not_worse(self.before, after):
            return False
        if self.ring:
            # the keep-out ring the new thing creates must not split doorways or seal the robot dock; standing
            # next to it to reach furniture is fine (routes may pass beside a fragile item, not over it)
            ringed = access_report(self.state, self.rid, self.impassable | set(cells) | near_cells(cells, self.room, self.ring), self.targets)
            essential = {k for k in self.before["ok"] if k[0:1] == ("door",) or self.targets[k][2]}
            reduced_before = {"groups": self.before["groups"], "ok": essential}
            if not not_worse(reduced_before, ringed):
                return False
        return True


def surface_area_m2(entity):
    size = entity.get("surface_size_m") or (entity.get("size_m") or [0.5, 0.5])[:2]
    return float(size[0]) * float(size[1])


def surface_items(state, eid):
    return [k for k, e in state["entities"].items() if e.get("location") == eid and e.get("relation", "on") == "on"]


def footprint_m(entity):
    size = entity.get("size_m") or [0.1, 0.1, 0.1]
    w, d = float(size[0]), float(size[1])
    if entity.get("rot", 0) in (90, 270):
        w, d = d, w
    return w, d


def rect_overlap(a_pos, a_size, b_pos, b_size, margin=0.0):
    eps = 1e-9
    return not (a_pos[0] + a_size[0] <= b_pos[0] + margin + eps or b_pos[0] + b_size[0] <= a_pos[0] + margin + eps or
                a_pos[1] + a_size[1] <= b_pos[1] + margin + eps or b_pos[1] + b_size[1] <= a_pos[1] + margin + eps)


def surface_spot(state, surface_id, item_id, want=None, step=0.05):
    """A position [x, y] (meters from the surface's corner) where item fits without overlap.

    want: requested position; returned only if it fits. Without want, the first
    free spot in row-major order is used. Returns None if nothing fits.
    """
    surface = state["entities"][surface_id]
    sw, sd = (surface.get("surface_size_m") or (surface.get("size_m") or [0.5, 0.5])[:2])[:2]
    iw, idp = footprint_m(state["entities"][item_id])
    if iw > sw + 1e-9 or idp > sd + 1e-9:
        return None
    others = []
    for other in surface_items(state, surface_id):
        if other == item_id:
            continue
        record = state["entities"][other]
        if record.get("spos") is None:
            continue
        others.append((record["spos"], footprint_m(record)))
    def fits(pos):
        if pos[0] < -1e-9 or pos[1] < -1e-9 or pos[0] + iw > sw + 1e-9 or pos[1] + idp > sd + 1e-9:
            return False
        return not any(rect_overlap(pos, (iw, idp), opos, osize) for opos, osize in others)
    if want is not None:
        want = [round(float(want[0]), 3), round(float(want[1]), 3)]
        return want if fits(want) else None
    y = 0.0
    while y + idp <= sd + 1e-9:
        x = 0.0
        while x + iw <= sw + 1e-9:
            if fits((x, y)):
                return [round(x, 3), round(y, 3)]
            x += step
        y += step
    return None


def fits_inside(item_size, inner_size):
    """Axis-aligned fit allowing any rotation of the item (sorted dimensions)."""
    if not inner_size:
        return True
    a = sorted(float(v) for v in item_size[:3])
    b = sorted(float(v) for v in inner_size[:3])
    return all(x <= y + 1e-9 for x, y in zip(a, b))
