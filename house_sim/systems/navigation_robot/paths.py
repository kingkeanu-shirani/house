"""Paths and clearances (fidelity G on the 0.5 m occupancy grid).

* Line-of-sight room coverage: every robot motion (and look) marks the floor cells visible from
  where the robot stands; tall furniture (>= 1.2 m) and partitions occlude; darkness limits range
  to the robot's own light; with the cameras off (privacy mode, switched off, a room policy:
  body.imaging) it sees nothing. nav.visits[room].coverage. With the geometric camera the coverage
  is the camera's own (camera_log.coverage: what its frames surveyed, in line of sight and lit), and
  a patrol looks around at each viewpoint and reports what its frames caught.
* Resting spot: whether the robot stands in a doorway, its approach ring, a declared traffic lane,
  or a cell whose occupation disconnects the room's doorways (nav.robot_spot).
* Clear width: a route is clear for width W when a k x k block (k = ceil(W / 0.5)) can slide from
  the entry to the exit of every room on it, every connection is at least W wide and no door on
  it has its swing area obstructed. Watched routes and doors are re-evaluated every tick into
  nav.routes / nav.doors (set them up with the navigation_robot.watch_route / watch_doors helpers).
* Door swing (kernel geometry.door_swing, the shared door model): opening a hinged door nudges light
  items lying in its leaf sweep aside and stops at 45 degrees against heavy ones; only a careless (fast)
  swing breaks fragile items or hurts people. With the mechanisms domain loaded it owns the leaf's
  contact with loose items >= 15 cm and with people/pets; this hook then only nudges low items aside,
  records the 45 degree stop against furniture and mirrors mechanisms' part-way stops, so nothing is
  counted twice.
Verbs: go_to, step, patrol, wait_aside.
"""
import copy
import math
from collections import deque

from ...kernel import body, geometry
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, system
from . import common as C
from . import hazards as H

TALL_M = 1.2
SWING_SKIP = ("switch", "light", "floor", "window", "door", "gate", "hooks", "handrail", "elevator_panel", "wall")


# ----------------------------------------------------------------------
# line-of-sight coverage

def view_blockers(state, rid):
    cells = set()
    for eid in H.floor_entities(state, rid):
        rec = state["entities"][eid]
        size = rec.get("size_m") or [0.1, 0.1, 0.1]
        if rec.get("blocks_view") or (rec.get("blocks_path") and C.num(size[2]) >= TALL_M):
            cells.update(geometry.footprint(rec))
    return cells


def see_from(state, rid, vp):
    room = state["rooms"][rid]
    if not body.imaging(state):
        return set()
    cols, rows = geometry.grid_size(room)
    level = C.light_level(state, rid)
    own = C.robot_has_light(state)
    max_m = 8.0 if level >= 0.2 else (4.0 if own else 0.75)
    if body.clarity(state) < 0.4:
        max_m = min(max_m, 2.0)
    blockers = view_blockers(state, rid)
    seen = set()
    for y in range(rows):
        for x in range(cols):
            if C.CELL * math.hypot(x - vp[0], y - vp[1]) > max_m:
                continue
            if any(c in blockers for c in C.line_cells(vp, (x, y))):
                continue
            seen.add((x, y))
    return seen


def camera_coverage(state, rid):
    """Geometric camera: the share of a room's floor the camera's frames have surveyed (camera_log.coverage), 0.0
    before any."""
    return round(C.num(((state.get("camera_log") or {}).get("coverage") or {}).get(rid), 0.0), 3)


def coverage(state, rid):
    """How much of a room's floor the robot has seen: the camera log's share with the geometric camera, else this
    domain's line-of-sight record (nav.visits)."""
    if C.geometric(state):
        return camera_coverage(state, rid)
    return ((C.nav_read(state).get("visits") or {}).get(rid) or {}).get("coverage", 0.0)


def update_coverage(w, rid, vp):
    state = w.s
    if rid not in state["rooms"]:
        return
    if C.geometric(state):
        # the camera's frames are the record (camera_log, written as each step ends): the visit keeps its times and
        # the camera's coverage so far
        prev = (C.nav_read(state).get("visits") or {}).get(rid) or {}
        value = camera_coverage(state, rid)
        if prev.get("coverage") == value and prev.get("last_s") == C.now(state):
            return
        visit = dict(prev)
        visit.setdefault("first_s", C.now(state))
        visit["last_s"] = C.now(state)
        visit["coverage"] = value
        C.nav(w).setdefault("visits", {})[rid] = visit
        return
    room = state["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    n = cols * rows
    seen = see_from(state, rid, vp)
    old = (C.nav_read(state).get("seen") or {}).get(rid) or "0" * n
    if len(old) != n:
        old = "0" * n
    bits = list(old)
    for (x, y) in seen:
        bits[y * cols + x] = "1"
    new = "".join(bits)
    blocked = set(geometry.blocked_cells(state, rid, include_agents=False))
    floor_cells = [(x, y) for y in range(rows) for x in range(cols) if (x, y) not in blocked]
    covered = sum(1 for (x, y) in floor_cells if new[y * cols + x] == "1")
    coverage = round(covered / max(1, len(floor_cells)), 3)
    prev = (C.nav_read(state).get("visits") or {}).get(rid) or {}
    if new == old and prev.get("coverage") == coverage and prev.get("last_s") == C.now(state):
        return
    nav = C.nav(w)
    nav.setdefault("seen", {})[rid] = new
    visit = dict(prev)
    visit.setdefault("first_s", C.now(state))
    visit["last_s"] = C.now(state)
    visit["coverage"] = coverage
    nav.setdefault("visits", {})[rid] = visit


@after("look", order=60)
def _look_coverage(ctx, result):
    w = ctx.w
    update_coverage(w, w.robot["room"], C.cell(w.robot.get("pos") or [0, 0]))


# ----------------------------------------------------------------------
# where the robot stands

def blocks_route(state, rid, c):
    doors = []
    for cid in sorted(state.get("connections", {})):
        conn = state["connections"][cid]
        if rid in conn.get("cells", {}):
            doors.append(C.cell(conn["cells"][rid]))
    doors = sorted(set(doors))
    if len(doors) < 2 or c in doors:
        return c in doors and len(doors) >= 1
    for i in range(len(doors)):
        for j in range(i + 1, len(doors)):
            free = geometry.find_path(state, rid, doors[i], [doors[j]])
            if free is None:
                continue
            if geometry.find_path(state, rid, doors[i], [doors[j]], extra_blocked=[c]) is None:
                return True
    return False


def spot_status(state, rid, c):
    room = state["rooms"][rid]
    conn_cells = set()
    for cid in sorted(state.get("connections", {})):
        conn = state["connections"][cid]
        if rid in conn.get("cells", {}):
            conn_cells.add(C.cell(conn["cells"][rid]))
    ring = C.doorway_cells(state, rid, 1)
    traffic = {C.cell(x) for x in room.get("traffic_cells") or []}
    status = {"room": rid, "cell": [c[0], c[1]], "in_doorway": c in conn_cells, "near_doorway": c in ring,
              "in_traffic_lane": c in traffic, "blocks_route": blocks_route(state, rid, c)}
    status["in_the_way"] = bool(status["in_doorway"] or status["near_doorway"] or status["in_traffic_lane"] or status["blocks_route"])
    return status


def update_robot_spot(w):
    state = w.s
    rid = state["robot"]["room"]
    c = C.cell(state["robot"].get("pos") or [0, 0])
    status = spot_status(state, rid, c)
    old = C.nav_read(state).get("robot_spot") or {}
    if {k: v for k, v in old.items() if k != "since_s"} == status:
        return
    status["since_s"] = C.now(state)
    C.nav(w)["robot_spot"] = status


# ----------------------------------------------------------------------
# clear width

def obstacles(state, rid, clutter=True, rugs=False, ignore=()):
    """cell -> [owners]: furniture, plus loose floor items when clutter=True, plus unsafe rugs."""
    cells = {}
    for c, owners in geometry.blocked_cells(state, rid, ignore=ignore, include_agents=False).items():
        cells.setdefault(c, []).extend(owners)
    for eid in H.floor_entities(state, rid):
        if eid in ignore:
            continue
        rec = state["entities"][eid]
        if rec.get("blocks_path"):
            continue
        if rec.get("ground_layer"):
            unsafe = (rec.get("category") == "rug" or C.is_type(rec, "rug")) and (not rec.get("non_slip_pad", False) or rec.get("curled") or rec.get("curled_corner"))
            if not (rugs and unsafe):
                continue
        elif not clutter or rec.get("intentional_barrier"):
            continue
        for c in geometry.footprint(rec):
            cells.setdefault(c, []).append(eid)
    return cells


def wide_path(state, rid, start, goal, width_m, clutter=True, rugs=False, ignore=(), extra=()):
    """Can a block of width_m slide from start to goal? Returns (path anchors or None, k)."""
    room = state["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    k = max(1, int(math.ceil(width_m / C.CELL - 1e-9)))
    blocked = set(obstacles(state, rid, clutter=clutter, rugs=rugs, ignore=ignore)) | {C.cell(e) for e in extra}

    def ok(p):
        for i in range(k):
            for j in range(k):
                q = (p[0] + i, p[1] + j)
                if not (0 <= q[0] < cols and 0 <= q[1] < rows) or q in blocked:
                    return False
        return True

    def covering(c):
        return [(c[0] - i, c[1] - j) for i in range(k) for j in range(k) if ok((c[0] - i, c[1] - j))]

    starts = covering(C.cell(start))
    goals = set(covering(C.cell(goal)))
    if not starts or not goals:
        return None, k
    queue = deque([(s, [s]) for s in sorted(starts)])
    seen = set(starts)
    while queue:
        p, path = queue.popleft()
        if p in goals:
            return path, k
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            q = (p[0] + dx, p[1] + dy)
            if q not in seen and ok(q):
                seen.add(q)
                queue.append((q, path + [q]))
    return None, k


def band_blockers(state, rid, start, goal, width_m, clutter=True, rugs=False):
    """Items lying in the band of the plain route that keep it from being width_m wide."""
    path = geometry.find_path(state, rid, C.cell(start), [C.cell(goal)]) or []
    k = max(1, int(math.ceil(width_m / C.CELL - 1e-9)))
    obs = obstacles(state, rid, clutter=clutter, rugs=rugs)
    near = set()
    for c in [C.cell(start)] + [C.cell(p) for p in path]:
        for dx in range(-(k - 1), k):
            for dy in range(-(k - 1), k):
                near.add((c[0] + dx, c[1] + dy))
    owners = set()
    for c in near:
        for owner in obs.get(c, []):
            if owner in state["entities"] and not state["entities"][owner].get("blocks_path"):
                owners.add(owner)
    return sorted(owners)


def swing_cells(state, door_id):
    """(room, cells) a hinged door/gate leaf sweeps: the kernel's shared door model (geometry.door_swing: swing room by
    the one rule every domain uses, inward cell + hinge-side diagonal, a second inward cell for wide leaves)."""
    return geometry.door_swing(state, door_id)


def swing_blockers(state, door_id):
    rid, cells = swing_cells(state, door_id)
    if not rid:
        return None, [], []
    out = []
    cells = set(cells)
    for eid in H.floor_entities(state, rid):
        rec = state["entities"][eid]
        if rec.get("category") in SWING_SKIP or rec.get("ground_layer") or eid == door_id:
            continue
        if C.num((rec.get("size_m") or [0.1, 0.1, 0.1])[2], 0.1) < UNDERCUT_M:
            continue      # flat things (a sheet of paper, a cord) pass under the leaf
        if set(geometry.footprint(rec)) & cells:
            out.append(eid)
    agents = [aid for group, aid, a in C.agents_in_room(state, rid) if C.cell(a["pos"]) in cells]
    return rid, out, agents


UNDERCUT_M = 0.02          # gap under an interior door leaf (about 1-2 cm): anything flatter passes under it
TALL_SWEEP_M = 0.15        # with mechanisms loaded, items this tall meet its controlled-swing model
VULNERABLE_ROLES = ("infant", "toddler")


def _heavy(w, eid):
    from ..core import subtree_mass
    rec = w.ent(eid)
    return bool(rec.get("blocks_path") or rec.get("anchored") or rec.get("portable") is False or subtree_mass(w, eid) > 15.0)


def _set_angle(w, door_id, angle):
    rec = w.s["entities"][door_id]
    if "open_angle_deg" not in rec and angle >= 90.0:
        return
    if C.num(rec.get("open_angle_deg"), 90.0) != angle or "open_angle_deg" not in rec:
        w.mut(door_id)["open_angle_deg"] = angle


@after("open", order=70)
def _door_swing(ctx, result):
    """Opening a hinged door into what lies in its leaf sweep (kernel geometry.door_swing). The robot's open is a
    controlled swing: light things are nudged aside, heavy ones (furniture, > 15 kg) stop the leaf at about 45 degrees,
    and only a careless (fast) swing breaks fragile items or hurts people. With the mechanisms domain loaded it owns the
    leaf's contact with items >= 15 cm and with people and pets (its sweep runs first, order 50): this hook then only
    nudges low items aside, records the stop against furniture (which mechanisms leaves to the generator), and mirrors
    a leaf mechanisms stopped part-way into open_angle_deg (what route planners read)."""
    w = ctx.w
    door_id = ctx.args.get("target")
    state = w.s
    door = state["entities"].get(door_id or "") or {}
    if not door.get("connects") or not door.get("open"):
        return
    rid, items, agents = swing_blockers(state, door_id)
    mech = C.mechanisms_loaded()
    careless = C.speed_of(ctx) == "fast"
    _, cells = swing_cells(state, door_id)
    stopped = []
    for eid in items:
        rec = w.ent(eid)
        tall = C.num((rec.get("size_m") or [0, 0, 0.1])[2], 0.1) >= TALL_SWEEP_M
        if _heavy(w, eid):
            if not (mech and tall and not rec.get("blocks_path")):
                stopped.append(eid)
            continue
        if mech and (tall or rec.get("toppled")):
            continue     # mechanisms' sweep already slid it aside, toppled it or stopped against it
        if rec.get("fragile") and not rec.get("broken") and careless and not mech:
            C.break_entity(w, eid, "struck by a door flung open")
        target = None
        blocked = geometry.blocked_cells(state, rid)
        doorways = C.doorway_cells(state, rid, 0)
        for c in sorted(geometry.near_cells(set(geometry.footprint(rec)), state["rooms"][rid], 2), key=lambda q: (q[1], q[0])):
            if c in cells or c in blocked or c in doorways:
                continue
            target = c
            break
        if target:
            m = w.mut(eid)
            m["pos"] = [target[0], target[1]]
            m["displaced"] = True
            C.log(w, "door_pushed_item", door=door_id, item=eid)
        else:
            stopped.append(eid)
    for aid in ([] if mech else agents):
        if aid in state.get("people", {}):
            person = state["people"][aid]
            if careless or person.get("role") in VULNERABLE_ROLES:
                C.hurt_person(w, aid, "hit by a door flung open into them", fall=person.get("role") in VULNERABLE_ROLES)
                w.stat("collisions")
            else:
                # a controlled swing: the leaf touches them and stops; they are startled, not harmed
                w.person_m(aid)["mood"] = "annoyed"
                C.bump(w, "social.door_bumps")
                C.log(w, "door_bumped_person", door=door_id, person=aid)
                stopped.append(aid)
        elif aid in state.get("pets", {}):
            C.startle_pet(w, aid, "touched by an opening door")
    if stopped:
        _set_angle(w, door_id, 45.0)
        C.incident(w, "door_blocked_partly_open", door=door_id, by=stopped)
        return
    frac = C.num((door.get("mech") or {}).get("open_frac"), 1.0) if mech else 1.0
    _set_angle(w, door_id, 90.0 if frac >= 0.83 else round(90.0 * frac, 1))


def evaluate_route(state, spec):
    rooms = spec.get("rooms") or []
    width = C.num(spec.get("width_m"), 0.9)
    clutter = spec.get("clutter", True)
    rugs = spec.get("rugs", False)
    result = {"clear": True, "width_m": width, "problems": [], "blockers": []}
    if not rooms or any(r not in state["rooms"] for r in rooms):
        result.update(clear=False, problems=["unknown room"])
        return result
    points = []
    start = spec.get("from_cell")
    for i, rid in enumerate(rooms):
        entry = C.cell(start) if i == 0 and start else None
        exit_ = None
        if i > 0:
            cid = _conn(state, rooms[i - 1], rid)
            entry = C.cell(state["connections"][cid]["cells"][rid]) if cid else None
        if i < len(rooms) - 1:
            cid = _conn(state, rid, rooms[i + 1])
            if cid is None:
                result["clear"] = False
                result["problems"].append("no connection {}-{}".format(rid, rooms[i + 1]))
                return result
            conn = state["connections"][cid]
            exit_ = C.cell(conn["cells"][rid])
            if C.num(conn.get("width_m"), 0.9) + 1e-9 < width and not spec.get("ignore_doorway_width"):
                result["clear"] = False
                result["problems"].append("doorway {} is {:.2f} m wide".format(cid, C.num(conn.get("width_m"), 0.9)))
            door = conn.get("door")
            if door:
                _, blockers, agents = swing_blockers(state, door)
                if blockers:
                    result["clear"] = False
                    result["problems"].append("door {} swing obstructed".format(door))
                    result["blockers"].extend(blockers)
                if spec.get("doors_open") and not state["entities"].get(door, {}).get("open"):
                    result["clear"] = False
                    result["problems"].append("door {} closed".format(door))
        else:
            end = spec.get("to_cell")
            exit_ = C.cell(end) if end else None
        if entry is None:
            entry = exit_
        if exit_ is None:
            exit_ = entry
        if entry is None:
            continue
        points.append((rid, entry, exit_))
        extra = [state["robot"]["pos"]] if spec.get("robot_counts") and state["robot"].get("room") == rid and state["robot"].get("pos") else []
        path, k = wide_path(state, rid, entry, exit_, width, clutter=clutter, rugs=rugs, extra=extra)
        if path is None:
            result["clear"] = False
            result["problems"].append("{} narrower than {:.2f} m".format(rid, width))
            result["blockers"].extend(band_blockers(state, rid, entry, exit_, width, clutter=clutter, rugs=rugs))
        if i == len(rooms) - 1 and spec.get("turn_m"):
            free, _ = wide_path(state, rid, exit_, exit_, C.num(spec["turn_m"]), clutter=clutter, rugs=rugs)
            if free is None:
                result["clear"] = False
                result["problems"].append("no {:.1f} m turning space at the destination".format(C.num(spec["turn_m"])))
    result["blockers"] = sorted(set(result["blockers"]))
    return result


def _conn(state, a, b):
    for cid in sorted(state.get("connections", {})):
        if set(state["connections"][cid]["rooms"]) == {a, b}:
            return cid
    return None


def evaluate_door(state, door_id, width_m=0.8):
    rid, blockers, agents = swing_blockers(state, door_id)
    door = state["entities"].get(door_id, {})
    out = {"swing_clear": not blockers, "swing_blockers": blockers, "approach_clear": True, "approach_blockers": []}
    if not rid:
        out["swing_clear"] = True
        rid = door.get("location")
    conn = next((c for cid, c in sorted(state.get("connections", {}).items()) if c.get("door") == door_id), None)
    for side in sorted((conn or {}).get("cells", {})):
        if side not in state["rooms"] or state["rooms"][side].get("outdoor"):
            continue
        cell = C.cell(conn["cells"][side])
        room = state["rooms"][side]
        cols, rows = geometry.grid_size(room)
        target = (cols // 2, rows // 2)
        path, _ = wide_path(state, side, cell, target, width_m)
        if path is None:
            out["approach_clear"] = False
            out["approach_blockers"].extend(band_blockers(state, side, cell, target, width_m))
    out["approach_blockers"] = sorted(set(out["approach_blockers"]))
    out["clear"] = out["swing_clear"] and out["approach_clear"]
    return out


def _signature(state, rooms):
    sig = []
    for eid, rec in state["entities"].items():
        if rec.get("location") in rooms:
            sig.append((eid, rec.get("location"), tuple(rec.get("pos") or ()), rec.get("open"), rec.get("relation")))
    sig.sort(key=lambda t: t[0])
    for group in ("people", "pets"):
        for aid in sorted(state.get(group, {})):
            a = state[group][aid]
            if a.get("location") in rooms:
                sig.append((aid, a.get("location"), tuple(a.get("pos") or ())))
    import hashlib
    return hashlib.sha1(repr(sig).encode("utf-8")).hexdigest()[:20]


def evaluate_area(state, spec):
    rid = spec.get("room")
    if rid not in state["rooms"]:
        return {"clear": False, "blockers": []}
    obs = obstacles(state, rid, clutter=True)
    blockers = sorted({o for c in spec.get("cells", []) for o in obs.get(C.cell(c), [])})
    return {"clear": not blockers, "blockers": blockers}


SIGHTLINE_MIN_T = 0.5    # a seat sees the screen when at least half of the light from its middle gets through


def _sightline_blocked(state, room, seat, tv):
    """Geometric camera: whether furniture hides the screen from a seat -- the seated eye above the seat's middle
    (sight.PERSON_EYE sitting, 1.15 m) to the middle of the screen (sight.entity_box), tested exactly against every
    occluder box (sight.los, porous ones such as a ladder let part through); people and pets passing by are left out
    (the watch is about how the room is arranged)."""
    from ...kernel import sight
    sb, tb = sight.entity_box(state, seat), sight.entity_box(state, tv)
    if sb is None or tb is None or sb.rid != room or tb.rid != room:
        return True
    agents = [a for group in ("people", "pets") for a, rec in sorted(state.get(group, {}).items()) if rec.get("location") == room]
    eye = ((sb.x0 + sb.x1) / 2.0, (sb.y0 + sb.y1) / 2.0, sight.PERSON_EYE["sitting"])
    screen = ((tb.x0 + tb.x1) / 2.0, (tb.y0 + tb.y1) / 2.0, (tb.z0 + tb.z1) / 2.0)
    return sight.los(state, room, eye, screen, ignore=[seat, tv] + agents) < SIGHTLINE_MIN_T


def evaluate_sightlines(state, spec):
    """A watched TV sightline: which seats cannot see the screen. Room camera: a thing 1 m or taller (or blocks_view)
    on a cell of the line between the seat's and the TV's first cells; geometric camera: the exact line from the
    seated eye to the screen (_sightline_blocked)."""
    tv = spec.get("tv")
    if tv not in state["entities"]:
        return {"clear": False, "blocked_seats": list(spec.get("seats", []))}
    room, tv_cells = geometry.anchor(state, tv)
    blocked, distances = [], {}
    exact = C.geometric(state)
    for seat in spec.get("seats", []):
        if seat not in state["entities"]:
            blocked.append(seat)
            continue
        srid, seat_cells = geometry.anchor(state, seat)
        if srid != room or not seat_cells or not tv_cells:
            blocked.append(seat)
            continue
        a, b = seat_cells[0], tv_cells[0]
        distances[seat] = round(C.distance_m(a, b), 2)
        if exact:
            if _sightline_blocked(state, room, seat, tv):
                blocked.append(seat)
            continue
        for c in C.line_cells(a, b):
            if c in seat_cells or c in tv_cells:
                continue
            hit = False
            for eid in H.floor_entities(state, room):
                rec = state["entities"][eid]
                if eid in (seat, tv) or c not in geometry.footprint(rec):
                    continue
                if C.num((rec.get("size_m") or [0, 0, 0])[2]) >= 1.0 or rec.get("blocks_view"):
                    hit = True
                    break
            if hit:
                blocked.append(seat)
                break
    return {"clear": not blocked, "blocked_seats": sorted(blocked), "distances_m": distances}


def watch_idle(w):
    """Exact idle probe of navigation_robot.watch (kernel v3.1): no watched route, door, area or sightline (the
    system returns at once then)."""
    return not (w.s.get("household") or {}).get("nav_watch")


@system("navigation_robot.watch", order=190, max_dt=30.0, idle=watch_idle)
def watch_system(w, dt, tick):
    state = w.s
    watch = (state.get("household") or {}).get("nav_watch")
    if not watch or not (C.first_substep(tick) or C.every(tick, 20)):
        return
    rooms = set()
    for spec in watch.get("routes", {}).values():
        rooms.update(spec.get("rooms") or [])
    for spec in watch.get("areas", {}).values():
        rooms.add(spec.get("room"))
    for spec in watch.get("sightlines", {}).values():
        rooms.add(C.room_of(state, spec.get("tv")) if spec.get("tv") in state["entities"] else None)
    rooms.discard(None)
    for door in watch.get("doors", []):
        rec = state["entities"].get(door, {})
        rooms.update(rec.get("connects") or [])
    sig = "{}|{}|{}".format(_signature(state, rooms), list(state["robot"].get("pos") or []), state["robot"].get("room"))
    cache = C.nav_read(state).get("_watch_sig")
    if cache == sig:
        return
    nav = C.nav(w)
    nav["_watch_sig"] = sig
    routes = {}
    for name in sorted(watch.get("routes", {})):
        routes[name] = evaluate_route(state, watch["routes"][name])
    nav["routes"] = routes
    doors = {}
    for door in sorted(watch.get("doors", [])):
        if door in state["entities"]:
            doors[door] = evaluate_door(state, door, C.num(watch.get("door_path_m"), 0.8))
    nav["doors"] = doors
    if watch.get("areas"):
        nav["areas"] = {name: evaluate_area(state, watch["areas"][name]) for name in sorted(watch["areas"])}
    if watch.get("sightlines"):
        nav["sightlines"] = {name: evaluate_sightlines(state, watch["sightlines"][name]) for name in sorted(watch["sightlines"])}


# ----------------------------------------------------------------------
# movement verbs

def _resolve(ctx, target):
    """(room, entity/person/pet or None, cell or None) for a go_to target."""
    w = ctx.w
    state = w.s
    memory = state.get("memory", {})
    if target in state["rooms"]:
        return target, None, None
    for key in ("room_alias:" + target, "alias:" + target):
        if key in memory:
            value = memory[key].get("value") if isinstance(memory[key], dict) else memory[key]
            if isinstance(value, str) and value in state["rooms"]:
                return value, None, None
    if target in ("home", "home_pose"):
        pose = state["robot"].get("home_pose") or (state.get("household") or {}).get("robot_home")
        require(isinstance(pose, dict) and pose.get("room") in state["rooms"], "No home pose is configured", "precondition")
        return pose["room"], None, C.cell(pose.get("cell") or [0, 0])
    if target == "dock":
        dock = state["robot"].get("home") or state["robot"].get("dock")
        require(dock in state["entities"], "No charging dock is known", "precondition")
        target = dock
    if target in state["entities"]:
        known = ctx.visible(target) or target in (state.get("facts", {}).get("inspected") or {}) or state["robot"].get("map_known", True) and \
            state["entities"][target].get("location") in state["rooms"] and not state["entities"][target].get("portable")
        require(known, "The robot does not know where that is; search for it first", "perception")
        return C.room_of(state, target), target, None
    if target in state.get("people", {}) or target in state.get("pets", {}):
        group = "people" if target in state.get("people", {}) else "pets"
        agent = state[group][target]
        require(agent.get("location") == state["robot"]["room"], "They are not in sight; ask or search for them first", "perception")
        return agent["location"], target, None
    raise ActionError("Unknown destination: " + target, "argument")


def known_rooms(env):
    """The rooms on the robot's map and the exits it saw from them: its memory's map (env.mind.map, kernel v3.1: rooms
    it has been in and prior knowledge) together with the environment's known map of observed rooms."""
    tables = []
    mind = getattr(env, "mind", None)
    if isinstance(getattr(mind, "map", None), dict):
        tables.append(mind.map)
    tables.append(getattr(env, "_known_map", None) or {})
    known = set()
    for table in tables:
        for rid, entry in table.items():
            known.add(rid)
            known.update((entry or {}).get("exits") or [])
    return known


def route(ctx, start, goal):
    state = ctx.w.s
    from ...tasks.skills import room_route
    known = None
    if state["robot"].get("map_known", True) is False:
        known = known_rooms(ctx.env)
    # realistic perception: plan with the locks the robot knows about (an untried door counts as passable)
    path = room_route(C.perceived_locks(ctx.env, state), start, goal)
    if path is None:
        return None
    if known is not None and any(r not in known for r, _ in path):
        return None
    return path


def social_cells(state, rid, avoid=(), realistic=False):
    """Cells a socially careful walk keeps out of. realistic: the robot cannot tell who is asleep, so anyone lying down
    gets the sleeper's ring and everyone else counts as awake (pinch points), whatever their true state."""
    from . import traverse as T
    from . import hazards as H
    room = state["rooms"][rid]
    social = T.social_context(state, rid)
    cells = set(social["frame"])
    for _, line_cells, _ in social["lines"]:
        cells |= line_cells
    lying = {aid for aid, _ in H.lying_agents(state, rid)} if realistic else set()
    # pinch points: cells within arm's reach of two or more awake people (squeezing between them)
    near_count = {}
    for pid, p in sorted(state.get("people", {}).items()):
        if p.get("location") == rid and p.get("pos") and (pid not in lying if realistic else not C.asleep(p)):
            for c in geometry.near_cells([C.cell(p["pos"])], room, 1):
                near_count[c] = near_count.get(c, 0) + 1
    cells |= {c for c, n in near_count.items() if n >= 2}
    for group, aid, a in C.agents_in_room(state, rid):
        if (aid in lying) if realistic else C.asleep(a):
            cells |= set(geometry.near_cells([C.cell(a["pos"])], room, 1))
    return cells


def _walk_cells(ctx, cells, avoid, speed):
    """Walk an explicit in-room path one cell at a time (core walk_to, so every hook applies)."""
    w = ctx.w
    for c in cells:
        args = {"action": "walk_to", "cell": [int(c[0]), int(c[1])]}
        if avoid:
            args["avoid"] = list(avoid)
        if speed:
            args["speed"] = speed
        ctx.env.perform(args, ctx)
        here = C.cell(w.robot.get("pos") or [0, 0])
        if here != C.cell(c) or w.robot.get("fallen") or w.robot.get("stuck"):
            return False
    return True


def _social_walk(ctx, goal_cells, avoid, speed):
    w = ctx.w
    state = w.s
    rid = state["robot"]["room"]
    start = C.cell(state["robot"].get("pos") or [0, 0])
    extra = social_cells(state, rid, realistic=C.realistic(ctx)) - {C.cell(g) for g in goal_cells} - {start}
    # realistic perception: plan around the hazards the robot perceives, as core motion does (traverse.plan_path)
    from . import traverse as T
    path = T.plan_path(ctx, rid, start, goal_cells, avoid_kinds=tuple(avoid or ()), extra_blocked=sorted(extra))
    require(path is not None, "No socially acceptable path; wait for people to move or ask them", "precondition")
    return _walk_cells(ctx, path, avoid, speed)


def _near_known(ctx, entity, avoid, speed):
    """Geometric camera: go_to arrived in the room of a thing whose place the robot knows (_resolve: inspected, or a
    fixed thing on its map such as its own dock) but which its cameras cannot make out from where it stands (a dock
    plate across the room is below the detection resolution, camera.can_see), while a core approach needs the thing in
    view. It walks by its map to a free cell beside the thing first (core walk_to, adjacent, with the go_to's avoid and
    speed: every hazard applies), from where the approach can see it -- as a robot that knows its house does. A thing
    still not in view there is refused by the approach as before. The room camera sees the whole room: never needed."""
    room, cells = geometry.anchor(ctx.w.s, entity)
    if not cells or room != ctx.w.robot["room"]:
        return
    cell = sorted(cells)[0]
    args = {"action": "walk_to", "cell": [int(cell[0]), int(cell[1])], "adjacent": True}
    if avoid:
        args["avoid"] = list(avoid)
    if speed:
        args["speed"] = speed
    ctx.env.perform(args, ctx)


@action("go_to", params={"target": "target", "avoid": "?list", "speed": "?str:careful|normal|fast",
                         "restore_doors": {"type": "bool", "default": False}, "social": {"type": "bool", "default": False}},
        duration=lambda ctx: 0.5, energy=0.0, category="physical", timing="speed",
        doc="Walk to a room, an object (stopping within reach), a person in sight, 'home' (the configured park pose), 'dock', "
            "or a learned alias (memory room_alias:<name>) across rooms with the robot's map. Opens unlocked doors on the way; "
            "restore_doors closes the ones it opened. avoid lists hazard kinds; social=true also keeps out of people's personal "
            "space, conversations, TV sightlines and call camera frames. Each leg is a core walk_to/move, so all hazards apply.")
def go_to(ctx):
    w = ctx.w
    target = ctx.args["target"]
    avoid = ctx.args.get("avoid") or []
    speed = ctx.args.get("speed")
    room, entity, cell = _resolve(ctx, target)
    hops = route(ctx, w.robot["room"], room)
    require(hops is not None, "No known route to {}{}".format(room, " (explore the house first)" if w.robot.get("map_known", True) is False else ""), "precondition")
    for nxt, cid in hops:
        state = w.s
        conn = state["connections"][cid]
        here = state["robot"]["room"]
        door_id = conn.get("door")
        reopened = False
        if door_id and not state["entities"][door_id].get("open"):
            locked = state["entities"][door_id].get("locked")
            # realistic perception: refused up front only for a lock the robot knows about; an untried locked door is
            # found out at the door (it walks there, tries the handle and stops)
            require(not locked or not C.knows_field(ctx.env, state, door_id, "locked"),
                    "A locked door ({}) is on the route".format(door_id), "precondition")
            if ctx.args["social"]:
                _social_walk(ctx, [C.cell(conn["cells"][here])], avoid, speed)
            ctx.env.perform({"action": "approach", "target": door_id, **({"avoid": avoid} if avoid else {}), **({"speed": speed} if speed else {})}, ctx)
            if w.s["entities"][door_id].get("locked") and C.realistic(ctx):
                ctx.grant(door_id, "inspect", ["locked"])
                return "Stopped at {} in {}: it is locked".format(door_id, w.robot["room"])
            ctx.env.perform({"action": "open", "target": door_id}, ctx)
            reopened = True
        elif ctx.args["social"]:
            if not _social_walk(ctx, [C.cell(conn["cells"][here])], avoid, speed):
                return "Stopped in {} ({})".format(w.robot["room"], (C.nav_read(w.s).get("last_incident") or {}).get("kind", "blocked"))
        args = {"action": "move", "target": nxt}
        if avoid:
            args["avoid"] = list(avoid)
        if speed:
            args["speed"] = speed
        ctx.env.perform(args, ctx)
        if w.robot["room"] != nxt:
            return "Stopped in {} ({})".format(w.robot["room"], (C.nav_read(w.s).get("last_incident") or {}).get("kind", "blocked"))
        if reopened and ctx.args["restore_doors"]:
            ctx.env.perform({"action": "close", "target": door_id}, ctx)
    if entity is not None:
        if C.geometric(w.s) and entity in w.s["entities"] and w.robot["room"] == room and not ctx.args["social"] \
                and not ctx.visible(entity):
            _near_known(ctx, entity, avoid, speed)
        if ctx.args["social"]:
            room_rec = w.s["rooms"][room]
            _, cells = geometry.anchor(w.s, entity)
            goals = (geometry.near_cells(cells or [], room_rec) - set(cells or [])) if cells else set()
            if goals:
                _social_walk(ctx, sorted(goals), avoid, speed)
        else:
            args = {"action": "approach", "target": entity}
            if avoid:
                args["avoid"] = list(avoid)
            if speed:
                args["speed"] = speed
            ctx.env.perform(args, ctx)
    elif cell is not None:
        if ctx.args["social"]:
            _social_walk(ctx, [cell], avoid, speed)
        else:
            args = {"action": "walk_to", "cell": [cell[0], cell[1]]}
            if avoid:
                args["avoid"] = list(avoid)
            if speed:
                args["speed"] = speed
            ctx.env.perform(args, ctx)
    if not body.is_on(w.s):
        # the battery ran out on the last leg (its settlement powered the robot down where the walk was cut)
        return "Stopped in {} (battery_depleted)".format(w.robot["room"])
    return "Arrived at {}".format(target)


DIRS = {"n": (0, -1), "s": (0, 1), "e": (1, 0), "w": (-1, 0), "ne": (1, -1), "nw": (-1, -1), "se": (1, 1), "sw": (-1, 1)}


@action("step", params={"target": "?str:n|s|e|w|ne|nw|se|sw", "cell": "?cell", "over": {"type": "bool", "default": False},
                        "speed": "?str:careful|normal|fast"},
        duration=lambda ctx: ctx.data.get("seconds", 1.0), energy=0.002, category="physical", timing="speed",
        doc="Take one deliberate step to an adjacent cell (direction n/s/e/w/ne/nw/se/sw or cell). over=true lifts the foot high "
            "to step over a small object (<= 0.2 m: cord, toy, threshold strip) without touching it.")
def step(ctx):
    from . import traverse as T
    w = ctx.w
    state = w.s
    robot = state["robot"]
    # the body's mobility check (power, actuators, fallen, legs, standing on an aid, localization) with this verb's
    # wording for the cases it always refused
    ctx.require_mobile(legacy_message="Robot cannot move")
    require(not robot.get("stuck"), "The robot is stuck ({}); request help".format(robot.get("stuck")), "capability")
    require(body.fault(state, "software_stuck") is None, T.SOFTWARE_STUCK_TEXT, "capability")
    rid = robot["room"]
    room = state["rooms"][rid]
    start = C.cell(robot.get("pos") or [0, 0])
    if ctx.args.get("cell") is not None:
        c = C.cell(ctx.args["cell"])
    else:
        require(ctx.args.get("target") in DIRS, "Give a direction (n, s, e, w, ne, nw, se, sw) or a cell", "argument")
        d = DIRS[ctx.args["target"]]
        c = (start[0] + d[0], start[1] + d[1])
    require(geometry.chebyshev(start, c) == 1, "A step reaches only an adjacent cell", "argument")
    require(geometry.in_grid(room, c), "That cell is outside this room", "argument")
    T._pre_motion(ctx)
    blocked = geometry.blocked_cells(state, rid)
    require(c not in blocked, "Something is in the way there", "precondition")
    if c[0] != start[0] and c[1] != start[1]:
        require((c[0], start[1]) not in blocked and (start[0], c[1]) not in blocked, "Cannot cut the corner", "precondition")
    speed = T.pace(ctx)             # the effective pace: limp mode and weak legs step carefully
    # a deliberate step at the body profile's walking speed for that pace (timing="speed": careful speed slows it
    # once, through the pace, not again in the engine; NAV-M5)
    mps = body.speed_mps(state, speed)
    seconds = (1.6 if ctx.args["over"] else 1.0) * C.CELL / mps * 1.2
    # the motion record the body charges and the physics carry law and the camera read (TIER1_INTEGRATION 1.4); the
    # deliberate step's time beyond plain walking is handling time. An empty walk: not even this step is affordable.
    walk_s = geometry.path_length_m([start, c]) / mps
    walked = ctx.motion([c], start, speed=C.speed_of(ctx), extra_s=max(0.0, seconds - walk_s))
    if walked:
        w.robot_m()["pos"] = [c[0], c[1]]
    trip = T.Trip(ctx, "step", rid, start, walked, speed, stepping_over=ctx.args["over"])
    T.traverse(trip)
    T._restore_hidden(ctx)
    T.finish_motion(ctx, trip)
    if walked:
        ctx.data["seconds"] = seconds
    if T.battery_cut(ctx):
        # the battery runs out on this step: the engine's settlement powers the robot down (an ok step, 4.6)
        return body.BATTERY_CUT_TEXT.format(ctx.data["motion"]["meters"])
    return "Stepped to {}".format([c[0], c[1]])


ANOMALY_FIELDS = ("anomaly",)


def anomalies(state, rid):
    """Observable anomalies in a room: (entity, kind)."""
    out = []
    room = state["rooms"][rid]
    people_here = [p for p in state.get("people", {}).values() if p.get("location") == rid]
    for eid in sorted(state["entities"]):
        rec = state["entities"][eid]
        if C.room_of(state, eid) != rid:
            continue
        if rec.get("anomaly"):
            out.append((eid, rec["anomaly"]))
            continue
        cat = rec.get("category")
        if cat == "door" and rec.get("exterior") and rec.get("connects") and rid == rec.get("location"):
            if rec.get("open"):
                out.append((eid, "exterior_door_open"))
            elif rec.get("lockable") and not rec.get("locked") and not rec.get("expected_unlocked"):
                out.append((eid, "exterior_door_unlocked"))
        elif cat == "window" and rec.get("open") and not rec.get("expected_open"):
            out.append((eid, "window_open"))
        elif cat in ("stove", "cooktop", "burner", "oven", "range") and (rec.get("on") or (rec.get("appliance") or {}).get("state") == "running") and not people_here:
            out.append((eid, "appliance_left_on"))
        elif cat == "spill" or (rec.get("floor_hazard") in ("wet", "slip") and rec.get("location") == rid):
            out.append((eid, "water_on_floor"))
        elif cat == "smoke" or rec.get("smoke"):
            out.append((eid, "smoke"))
    for pid in sorted(state.get("people", {})):
        p = state["people"][pid]
        if p.get("location") == rid and p.get("role") in ("intruder", "stranger", "unknown"):
            out.append((pid, "unexpected_person"))
    if room.get("wet_cells") and not any(k == "water_on_floor" for _, k in out):
        fid = C.floor_id(state, rid)
        if fid:
            out.append((fid, "water_on_floor"))
    return out


def noticed(ctx, rid, eid, kind, seen_from=(), caught=None):
    """Would the sweep notice this anomaly? Geometric camera (both perception modes): its frames caught it at one of
    the sweep's viewpoints (`caught`, filled by _sweep_look), smoke is noticed in any light. Room camera, oracle mode:
    always. Room camera, realistic perception: people in the room are seen; things need light (or the robot's own
    light) and must not be missed by the small-object model from where the sweep ended; smoke is noticed in any light;
    a damp film with no puddle (the floor's wet cells) only shows its sheen within a couple of cells of a viewpoint the
    sweep stood on."""
    state = ctx.w.s
    if C.geometric(state):
        return kind == "smoke" or eid in (caught or ())
    if not C.realistic(ctx):
        return True
    if eid in state.get("people", {}):
        return state["people"][eid].get("location") == rid
    if kind == "smoke":
        return True
    if eid not in state["entities"]:
        return False
    if kind == "water_on_floor" and state["entities"][eid].get("category") == "floor":
        from . import hazards as H
        if C.light_level(state, rid) < 0.2 and not C.robot_has_light(state):
            return False
        wet = [C.cell(c) for c in state["rooms"][rid].get("wet_cells") or []]
        return any(geometry.chebyshev(v, c) <= H.FILM_SEEN_CELLS for v in seen_from for c in wet)
    return C.perceives(ctx.env, state, eid)


PRIVATE_ROOM_TYPES = ("bathroom", "half_bath", "bedroom", "master_bedroom", "guest_room", "nursery")


def occupied_private(state, rid):
    room = state["rooms"][rid]
    for pid in sorted(state.get("people", {})):
        p = state["people"][pid]
        if p.get("location") != rid:
            continue
        if p.get("activity") in C.PRIVATE_ACTIVITIES or C.asleep(p) or room.get("type") in ("bathroom", "half_bath"):
            return pid
        if room.get("type") in ("bedroom", "master_bedroom", "guest_room", "nursery") and p.get("privacy_requested"):
            return pid
    return None


def looks_occupied(state, rid):
    """Realistic perception: what a patrolling robot can tell about a private room before entering it -- a closed
    bathroom/bedroom door (it does not open those on a patrol), or someone it can see in there now. It cannot know from
    elsewhere who is inside, whether they are asleep or what they are doing."""
    room = state["rooms"][rid]
    here = state["robot"].get("room")
    if here == rid:
        return occupied_private(state, rid)
    if room.get("type") not in PRIVATE_ROOM_TYPES:
        return None
    for cid in sorted(state.get("connections", {})):
        conn = state["connections"][cid]
        if rid in conn.get("rooms", []) and here in conn.get("rooms", []):
            door = state["entities"].get(conn.get("door") or "")
            if door is not None and not door.get("open"):
                return "closed_door"
    return None


def exterior_doors(state, rid):
    """Closed lockable exterior doors of a room (a patrol tries each one: whether it is locked is not visible)."""
    out = []
    for eid in sorted(state["entities"]):
        rec = state["entities"][eid]
        if rec.get("category") == "door" and rec.get("exterior") and rec.get("connects") and rec.get("location") == rid \
                and not rec.get("open") and rec.get("lockable"):
            out.append(eid)
    return out


def _sweep_look(ctx, rid, caught):
    """Geometric camera: look all around from where the robot stands (core look around with the low row: frames on
    every side, the floor included) and add to `caught` the anomalies its frames caught -- a thing, person or pet in
    frame (camera.in_frame), a damp film whose wet cells a frame surveyed within FILM_SEEN_CELLS of here (its sheen
    shows only close by, at a low angle). No-op in room mode and with the cameras off."""
    w = ctx.w
    if not C.geometric(w.s) or not body.imaging(w.s):
        return
    try:
        ctx.env.perform({"action": "look", "target": "around", "rows": 2}, ctx)
    except ActionError:
        return
    from ...kernel import camera
    state = w.s
    here = C.cell(state["robot"].get("pos") or [0, 0])
    floor_seen = None
    for eid, kind in anomalies(state, rid):
        if eid in caught or kind == "smoke":
            continue
        if kind == "water_on_floor" and (state["entities"].get(eid) or {}).get("category") == "floor":
            if floor_seen is None:
                floor_seen = {c for r, c in camera.seen_floor(state, w) if r == rid}
            wet = [C.cell(c) for c in state["rooms"][rid].get("wet_cells") or []]
            if any(c in floor_seen and geometry.chebyshev(here, c) <= H.FILM_SEEN_CELLS for c in wet):
                caught.add(eid)
        elif C.camera_sees(ctx.env, state, eid):
            caught.add(eid)


def viewpoints(state, rid):
    """Up to three cells that together see most of the room (greedy cover)."""
    room = state["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    blocked = set(geometry.blocked_cells(state, rid))
    candidates = [(x, y) for y in range(rows) for x in range(cols) if (x, y) not in blocked]
    if not candidates:
        return []
    target = set(candidates)
    chosen, covered = [], set()
    ordered = sorted(candidates, key=lambda c: (abs(c[0] - cols // 2) + abs(c[1] - rows // 2), c[1], c[0]))
    pool = ordered[:: max(1, len(ordered) // 24)][:24]
    vis = {c: see_from(state, rid, c) & target for c in pool}
    for _ in range(3):
        best = max(pool, key=lambda c: (len(vis[c] - covered), -abs(c[0] - cols // 2) - abs(c[1] - rows // 2)))
        gain = vis[best] - covered
        if not gain:
            break
        chosen.append(best)
        covered |= gain
        if len(covered) >= 0.97 * len(target):
            break
    return chosen


@action("patrol", params={"rooms": "list", "report": {"type": "bool", "default": True}, "speed": "?str:careful|normal",
                          "skip_occupied": {"type": "bool", "default": True}, "dock_after": {"type": "bool", "default": False},
                          "restore_doors": {"type": "bool", "default": True}},
        duration=lambda ctx: 0.5, energy=0.0, category="physical", timing="speed",
        doc="Walk a list of rooms in order, sweeping each from viewpoints that cover the floor (with the geometric camera it "
            "looks all around at each), and (report=true) inspect and report every observable anomaly (open or unlocked "
            "exterior doors, open windows, appliances left on, water on the floor, smoke, unexpected people) as report "
            "<target>.anomaly. Occupied bathrooms/bedrooms are skipped and logged unless skip_occupied=false. dock_after "
            "returns to the charging dock. The result says where and why the patrol ended early (ended_early).")
def patrol(ctx):
    w = ctx.w
    rooms = ctx.args["rooms"]
    require(rooms and all(isinstance(r, str) and r in w.s["rooms"] for r in rooms), "List the rooms to patrol", "argument")
    speed = ctx.args.get("speed")
    real = C.realistic(ctx)
    findings, skipped = [], []
    ended = None
    for rid in rooms:
        if ctx.args["skip_occupied"]:
            pid = looks_occupied(w.s, rid) if real else occupied_private(w.s, rid)
            if pid:
                skipped.append(rid)
                C.log(w, "patrol_skipped", room=rid, reason="occupied")
                continue
        if w.robot["room"] != rid:
            args = {"action": "go_to", "target": rid, "restore_doors": ctx.args["restore_doors"]}
            if speed:
                args["speed"] = speed
            ctx.env.perform(args, ctx)
            if w.robot["room"] != rid:
                ended = _ended_early(w, rid)
                break
        seen_from = [C.cell(w.robot.get("pos") or [0, 0])]
        caught = set()
        _sweep_look(ctx, rid, caught)
        for vp in viewpoints(w.s, rid):
            args = {"action": "walk_to", "cell": [vp[0], vp[1]]}
            if speed:
                args["speed"] = speed
            try:
                ctx.env.perform(args, ctx)
            except ActionError:
                continue
            seen_from.append(C.cell(w.robot.get("pos") or [0, 0]))
            if w.robot.get("fallen"):
                break
            _sweep_look(ctx, rid, caught)
        if w.robot.get("fallen"):
            ended = _ended_early(w, rid)
            break
        found = anomalies(w.s, rid)
        if real:
            # a lock is not visible: the patrol tries every closed exterior door of the room, then reports the unlocked
            found = [f for f in found if f[1] != "exterior_door_unlocked"]
            for door in exterior_doors(w.s, rid):
                try:
                    ctx.env.perform({"action": "approach", "target": door}, ctx)
                    ctx.env.perform({"action": "inspect", "target": door}, ctx)
                except ActionError:
                    continue
                rec = w.s["entities"][door]
                if not rec.get("locked") and not rec.get("expected_unlocked"):
                    found.append((door, "exterior_door_unlocked"))
            # the sweep reports what it noticed in handle order (true-id order would hint at identities); peek, not
            # handle: handle() issues one, and an issued handle is one the agent may act on -- this list still holds
            # anomalies the sweep never noticed (K-LEAK)
            found.sort(key=lambda f: (ctx.env.realism.handles.peek(f[0]) if f[0] in w.s["entities"] else f[0], f[1]))
        for eid, kind in found:
            if not noticed(ctx, rid, eid, kind, seen_from, caught) and not (real and kind == "exterior_door_unlocked"):
                continue
            findings.append({"room": rid, "target": eid, "kind": kind})
            if ctx.args["report"] and eid in w.s["entities"]:
                try:
                    ctx.env.perform({"action": "approach", "target": eid}, ctx)
                except ActionError:
                    pass
                try:
                    ctx.env.perform({"action": "inspect", "target": eid}, ctx)
                    ctx.env.perform({"action": "report", "target": eid, "field": "anomaly", "value": kind}, ctx)
                except ActionError:
                    # e.g. PowerOff once the battery ran out on the sweep (TIER1_EMBODIMENT 4.6: the loop goes on
                    # without effect); C.log's own first argument is the event kind, so the anomaly has its own key
                    C.log(w, "patrol_unreported", target=eid, anomaly=kind)
    nav = C.nav(w)
    nav["patrol"] = {"time_s": C.now(w.s), "rooms": list(rooms), "skipped": skipped, "findings": copy.deepcopy(findings),
                     "ended_early": copy.deepcopy(ended)}
    if ctx.args["dock_after"] and not w.robot.get("fallen") and body.is_on(w.s):
        ctx.env.perform({"action": "go_to", "target": "dock"}, ctx)
        ctx.env.perform({"action": "dock"}, ctx)
    out = {"findings": findings, "skipped_occupied": skipped}
    if ended is not None:
        out["ended_early"] = ended
    return out


def _ended_early(w, rid):
    """Where and why a patrol stopped before covering `rid`: the robot's room and the reason -- powered down (the
    battery ran out), it fell, or the incident that stopped its walk (blocked when none was logged)."""
    state = w.s
    if not body.is_on(state):
        reason = "battery_depleted"
    elif state["robot"].get("fallen"):
        reason = "robot_fell"
    else:
        reason = (C.nav_read(state).get("last_incident") or {}).get("kind", "blocked")
    return {"room": state["robot"].get("room"), "before": rid, "reason": reason}


def unobtrusive_cell(state, rid, near=None):
    room = state["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    blocked = set(geometry.blocked_cells(state, rid))
    hazards = set(geometry.hazard_cells(state, rid))
    ring = C.doorway_cells(state, rid, 1)
    traffic = {C.cell(x) for x in room.get("traffic_cells") or []}
    start = C.cell(near or state["robot"].get("pos") or [0, 0])
    options = []
    for y in range(rows):
        for x in range(cols):
            c = (x, y)
            if c in blocked or c in hazards or c in ring or c in traffic:
                continue
            wall = x in (0, cols - 1) or y in (0, rows - 1) or any((x + dx, y + dy) in blocked for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))
            options.append((0 if wall else 1, geometry.chebyshev(c, start), y, x))
    for _, _, y, x in sorted(options):
        if not blocks_route(state, rid, (x, y)) and geometry.find_path(state, rid, start, [(x, y)]) is not None:
            return (x, y)
    return None


@action("wait_aside", params={"seconds": {"type": "int", "default": 60, "min": 1, "max": 86400}, "cell": "?cell",
                              "speed": "?str:careful|normal"},
        duration=lambda ctx: float(ctx.args["seconds"]), energy=0.0, category="control", timing="commanded",
        doc="Step to an unobtrusive spot (against a wall, out of doorways, traffic lanes and anything that would cut the room's "
            "doorways apart; or the given cell), tuck the arms and wait the given seconds while staying responsive.")
def wait_aside(ctx):
    w = ctx.w
    rid = w.robot["room"]
    if ctx.args.get("cell") is not None:
        cell = C.cell(ctx.args["cell"])
    else:
        cell = unobtrusive_cell(w.s, rid)
        require(cell is not None, "No out-of-the-way spot here; wait in another room", "precondition")
    if cell != C.cell(w.robot.get("pos") or [0, 0]):
        args = {"action": "walk_to", "cell": [cell[0], cell[1]]}
        if ctx.args.get("speed"):
            args["speed"] = ctx.args["speed"]
        ctx.env.perform(args, ctx)
        if not body.is_on(w.s):
            # the battery ran out on the way: it powered down where the walk was cut, not waiting aside
            update_robot_spot(w)
            return "Stopped at {} (battery_depleted)".format(list(w.robot.get("pos") or [0, 0]))
    update_robot_spot(w)
    nav = C.nav(w)
    nav["waits"] = (nav.get("waits") or [])[-20:] + [{"time_s": C.now(w.s), "room": rid, "cell": list(w.robot.get("pos") or [0, 0]),
                                                       "seconds": ctx.args["seconds"], "in_the_way": nav.get("robot_spot", {}).get("in_the_way")}]
    return "Waiting aside at {} for {} s".format(list(w.robot.get("pos") or [0, 0]), ctx.args["seconds"])
