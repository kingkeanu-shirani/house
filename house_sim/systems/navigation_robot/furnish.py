"""Furnisher: navigation features and robot gear in generated homes (modest counts).

* Stairs: tread depth, tread material, occasional winders and a handrail entity per flight (mounted along the wall
  of the lower landing, its footprint inside that room's grid).
* Thresholds: exterior door sills 2-3 cm, sliding-door tracks, bathroom saddles 1 cm.
* Reach aids: a kitchen step stool, a 6 ft step ladder in the garage/basement/utility closet
  (a folding step stool in apartments), sometimes a reacher.
* Tools: tape measure, carpenter pencil, flashlight and lens wipes in the kitchen junk drawer;
  a spirit level and laser meter where tools live (garage workbench or utility closet).
* Robot gear: the robot's onboard headlamp (worn), sometimes a spare battery on a wall charger.
* Household robots: a robot vacuum (or vacuum-mop) seated on its dock in about half of homes, and a
  robot mower in some houses with a backyard.

Generation 2 (kernel v3.1, docs/GENERATOR.md; nothing changes for generation 1, whose houses and draws are frozen):
* exterior porch steps (same-floor stairs with `exterior: true`, a handrail from four risers) get a rail as long as
  the flight plus the 0.3 m extension codes ask for, on the yard side;
* whether the home has a robot vacuum follows the household: smart-home tier none / basic / connected 0.2 / 0.45 / 0.7
  (an assumption giving about 35 % overall, the order of published US ownership surveys), +0.1 with a dog or cat
  (pet hair), instead of a flat half (the same draw, another threshold);
* a home with an elderly resident keeps a reacher (0.8) where they live day to day (kitchen first), placed last
  from its own stream so nothing else in the house moves.

Everything standing on the floor goes through the kernel Placer (v2.3): reach aids with Placer.floor_item (strict:
never in a doorway clearance or a door's leaf sweep; keep_access: never cutting a hazard-free route or the only access
to anything, and reachable themselves; tucked against a wall away from doorways and work fixtures, and kept out of
everyday rooms that would be left cramped - see TUCK_AWAY), docks with Placer.furniture (access "not worse"). A vacuum dock stands at a
wall outlet (its plug goes straight into it: no cord run across the floor) where its 1.5 m keep-clear zone cuts no
hazard-free route; the robot sitting on it is marked `docked`. Rooms that have no such spot are skipped (the next
plausible room is tried, else the house simply lacks the item).
"""
from ...generator.place import Placer
from ...kernel import geometry
from ...kernel.plugins import furnisher
from . import hazards as H

# where a household keeps each standing reach aid, in order of preference (the first room that has a spot wins)
STOOL_ROOMS = ("kitchen", "pantry", "laundry_room", "dining_room")
LADDER_ROOMS = ("garage", "basement", "shed", "utility_closet", "laundry_room", "mudroom")
APARTMENT_STOOL_ROOMS = ("utility_closet", "laundry_room", "pantry", "coat_closet", "kitchen", "hallway")
REACHER_ROOMS = ("garage", "basement", "utility_closet", "laundry_room", "kitchen", "pantry", "coat_closet", "hallway")
VACUUM_ROOMS = ("living_room", "hallway", "dining_room", "office", "entryway")


def _first(p, kinds):
    for kind in kinds:
        rooms = p.rooms(kind)
        if rooms:
            return rooms[0]
    return None


def _hidden_cells(config, state, rid, top):
    """Wall cells of mounts (0.5-2 m up, not outlets/lights/windows/drawer fronts) that a floor-standing piece `top`
    metres tall would hide (the layout audit's "hidden mount": a tool rack behind a parked ladder)."""
    cells = set()
    for eid in geometry.room_entities(state, rid):
        rec = config["entities"][eid]
        if not rec.get("mounted") or not rec.get("pos") or rec.get("ground_layer") or "connects" in rec or geometry.floor_standing(rec):
            continue
        if rec.get("category") in ("outlet", "light", "window", "drawer"):
            continue
        try:
            h = float(rec.get("mount_height_m", rec.get("z_m", 0.0)) or 0.0)
        except (TypeError, ValueError):
            continue
        if 0.5 <= h < 2.0 and top > h + 0.05:
            cells.update(geometry.footprint(rec))
    return cells


def _standing_tops(config, state, rid):
    """cell -> top height (m) of the floor-standing pieces in a room."""
    tops = {}
    for eid in geometry.room_entities(state, rid):
        rec = config["entities"][eid]
        if not rec.get("pos") or not geometry.floor_standing(rec):
            continue
        try:
            top = float(rec.get("mount_height_m", rec.get("z_m", 0.0)) or 0.0) + float((rec.get("size_m") or [0, 0, 0.5])[2])
        except (TypeError, ValueError, IndexError):
            continue
        for c in geometry.footprint(rec):
            tops[c] = max(tops.get(c, 0.0), top)
    return tops


# Reach aids are kept out of the way, the way households tuck a stool or ladder at the end of a counter run or in a
# storage corner: against a wall or a piece of furniture, never right beside a doorway (within 2 cells: the entrance is
# where everyone walks) or in front of a work fixture (sink, stove, fridge, dishwasher, washer...) when another spot
# exists, and an everyday room (kitchen,
# dining room, hallway...) keeps them only when it still has room to spare for loose things afterwards (a galley
# kitchen's stool lives in the pantry or the laundry room instead).
TUCK_AWAY = True
WORK_FRONT_CATEGORIES = ("sink", "stove", "range", "oven", "cooktop", "refrigerator", "fridge", "dishwasher", "washer",
                         "dryer", "washing_machine", "microwave", "water_heater", "furnace", "electrical_panel", "breaker_panel")
EVERYDAY_ROOMS = ("kitchen", "dining_room", "hallway", "entryway", "living_room", "office", "family_room")
SPARE_CELLS = 8            # loose-item spots an everyday room keeps after taking a reach aid
TALL_SPARE_CELLS = 4       # ... and any room after taking a tall one (a 6 ft ladder): with less, the generator's final
                           # repair pass ends up re-standing it in front of a panel, valve or rack (hidden mount)
AID_TYPES = ("kitchen_step_stool", "step_stool", "step_ladder", "reacher_grabber")


def _doorway_near(config, rid, radius=2):
    """Cells within `radius` (Chebyshev) of the room's doorway cells."""
    room = config["rooms"][rid]
    out = set()
    for conn in config.get("connections", {}).values():
        c = (conn.get("cells") or {}).get(rid)
        if c is None:
            continue
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                cell = (int(c[0]) + dx, int(c[1]) + dy)
                if geometry.in_grid(room, cell):
                    out.add(cell)
    return out


def _work_fronts(config, state, rid):
    """Floor cells next to work fixtures and appliances (where people stand to use them)."""
    room = config["rooms"][rid]
    out = set()
    for eid in geometry.room_entities(state, rid):
        rec = config["entities"][eid]
        words = " ".join(str(x) for x in [rec.get("category"), rec.get("type"), rec.get("appliance_kind")] + list(rec.get("is_a") or []))
        if rec.get("pos") and any(w in words for w in WORK_FRONT_CATEGORIES):
            cells = set(geometry.footprint(rec))
            out |= set(geometry.near_cells(cells, room, 1)) - cells
    return out


def _open_floor(config, state, rid):
    """Cells out in the open (no wall or blocking piece on any side): a stool or ladder is stood against something."""
    room = config["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    blocked = set(geometry.blocked_cells(state, rid, include_agents=False))
    out = set()
    for y in range(rows):
        for x in range(cols):
            sides = ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))
            if all(geometry.in_grid(room, s) and s not in blocked for s in sides):
                out.add((x, y))
    return out


def _spare_cells(p, rid, limit):
    """How many small loose things (a 10 cm item) the room could still take one after another, each placed by the
    kernel's keep_access rule (cuts no hazard-free route, blocks no access), up to `limit`. Placement probes go into a
    copy of the entity table; nothing is added to the house."""
    state = p.state()
    ents = dict(state["entities"])
    view = dict(state, entities=ents)
    n = 0
    while n < limit:
        probe_id = "_nav_spare_probe_{}".format(n)
        rec = {"size_m": [0.1, 0.1, 0.15], "portable": True, "location": rid, "relation": "on"}
        cell = geometry.free_floor_cell(view, rid, record=rec, keep_access=True, ignore=(probe_id,))
        if cell is None:
            break
        rec["pos"] = list(cell)
        ents[probe_id] = rec
        n += 1
    return n


def _standing_item(p, first, kinds, type_name, **props):
    """Place a standing floor item in `first` (the room the household would use) or the next room of `kinds` where the
    kernel finds a cell (Placer.floor_item: strict = outside every doorway clearance and leaf sweep; keep_access = cuts
    no hazard-free route and is itself reachable), never in front of a wall mount it would hide (a 1.8 m ladder parked
    over a tool rack), tucked away from doorways and work fixtures, and only where the room keeps room to spare (see
    TUCK_AWAY). Returns the id, or None when no room has a spot."""
    order = [first] if first else []
    for kind in kinds:
        for rid in p.rooms(kind):
            room = p.c["rooms"][rid]
            if rid not in order and not any(room.get(k) for k in ("shared", "private", "off_limits")):
                order.append(rid)
    try:
        top = float((p.reg.make(type_name, order[0] if order else "robot").get("size_m") or [0, 0, 0.5])[2])
    except (TypeError, ValueError, IndexError):
        top = 0.5
    for rid in order:
        state = p.state()
        everyday = TUCK_AWAY and p.c["rooms"][rid].get("type") in EVERYDAY_ROOMS
        if everyday and rid != first and any(r.get("location") == rid and r.get("type") in AID_TYPES for r in p.c["entities"].values()):
            continue      # one reach aid per everyday room; the others go to storage (or the house goes without)
        avoid = _hidden_cells(p.c, state, rid, top)
        tiers = [((), True)]
        if TUCK_AWAY:
            # best: against something, away from the doorways and work fixtures; then anywhere away from those; then
            # anywhere the kernel allows (each tier keeps every hazard-free route; only the last may settle for plain ones)
            busy = _doorway_near(p.c, rid) | _work_fronts(p.c, state, rid)
            tiers = [(busy | _open_floor(p.c, state, rid), False), (busy, False), ((), True)]
        eid = None
        for extra, plain in tiers:
            eid = p.floor_item(rid, type_name, strict=True, avoid=tuple(sorted(avoid | set(extra))), plain=plain, **props)
            if eid is not None:
                break
        if eid is None:
            continue
        need = SPARE_CELLS if everyday else TALL_SPARE_CELLS if TUCK_AWAY and top > 1.0 else 0
        if need and _spare_cells(p, rid, need) < need:
            del p.c["entities"][eid]      # a cramped room: the household keeps it elsewhere
            continue
        return eid
    return None


def _drawer(config, room, label):
    for eid in sorted(config["entities"]):
        rec = config["entities"][eid]
        if rec.get("location") == room and rec.get("label") == label:
            return eid
    return None


EXTERIOR_RAIL_EXTRA_M = 0.3     # a rail runs past the top and bottom risers (IRC R311.7.8: about one tread)


def _handrail(p, registry, config, lower, cid, conn):
    """The rail runs up the flight from the foot of the stairs: in the lower room it is mounted along the wall the
    stair opening is in (long side along the wall), shortened to the wall length available in that room. Exterior
    porch steps (generation 2) get a metal railing as long as their flight plus EXTERIOR_RAIL_EXTRA_M."""
    room = config["rooms"][lower]
    cols, rows = geometry.grid_size(room)
    x, y = (int(v) for v in conn["cells"][lower])
    rec = registry.make("handrail", lower, on_stairs=cid, label="stair handrail")
    length = float((rec.get("size_m") or [3.5])[0])
    if conn.get("exterior"):
        length = round(int(conn.get("steps", 3)) * float(conn.get("run_m", 0.28)) + EXTERIOR_RAIL_EXTRA_M, 2)
        rec["label"] = "porch step railing"
        rec["material"] = "metal"
        rec["size_m"] = [length] + list(rec.get("size_m") or [3.5, 0.06, 0.06])[1:]
    _, lateral = geometry.wall_inward(room, (x, y))
    along_x = lateral == (1, 0)
    span = cols if along_x else rows
    here = x if along_x else y
    cells = max(1, min(int(round(length / geometry.CELL)), span))
    # the rail starts at the stair opening and runs along that wall toward the side with more free wall: it stops short
    # of a taller floor-standing piece (a furnace, shelving, a water heater) rather than running behind it
    tops = _standing_tops(config, p.state(), lower)
    height = float(rec.get("mount_height_m", 0.9) or 0.9)

    def free_run(step):
        run = 0
        k = here
        while 0 <= k < span and run < cells:
            c = (k, y) if along_x else (x, k)
            if tops.get(c, 0.0) > height + 0.05:
                break
            run += 1
            k += step
        return run

    start = here if span - here >= cells else max(0, here - cells + 1)
    if span - start < cells:
        start = max(0, span - cells)
    hidden = [k for k in range(start, start + cells) if tops.get((k, y) if along_x else (x, k), 0.0) > height + 0.05]
    if hidden:
        up, down = free_run(1), free_run(-1)
        if up >= down:
            cells, start = max(1, up), here
        else:
            cells, start = down, here - down + 1
    size = list(rec.get("size_m") or [3.5, 0.06, 0.06])
    if cells * geometry.CELL < length - 1e-9:
        rec["rail_length_m"] = length
        size[0] = round(cells * geometry.CELL, 2)
    rec["size_m"] = size
    rec["rot"] = 0 if along_x else 90
    rec["pos"] = [start, y] if along_x else [x, start]
    rec["mounted"] = True
    rec["blocks_path"] = False
    eid = p.new_id("handrail")
    config["entities"][eid] = rec
    return eid


def _outlets(config, rid):
    """Wall outlets in a room (cells), sorted by id."""
    out = []
    for eid in sorted(config["entities"]):
        rec = config["entities"][eid]
        if rec.get("location") == rid and rec.get("category") == "outlet" and rec.get("pos") and not rec.get("broken"):
            out.append((eid, (int(rec["pos"][0]), int(rec["pos"][1]))))
    return out


def _zone_cuts_nothing(p, rid, dock):
    """The dock's keep-clear zone (1.5 m in front, 0.5 m beside it) is a no-go area for a careful robot: it must not
    cut a hazard-free route or the only hazard-free access to anything in the room."""
    state = p.state()
    zone = set(H.dock_zone(state, dock))
    if not zone:
        return True
    blocked = set(geometry.blocked_cells(state, rid, include_agents=False))
    # fixed hazards only (loose things can be moved, and the final repair pass re-places those that cut a route)
    fixed = {c for c, items in geometry.placement_hazards(state, rid).items()
             if any(s != dock and not _movable(state, s) for _, s in items)}
    targets = geometry.access_targets(state, rid, level="all", ignore=(dock,))
    before = geometry.access_report(state, rid, blocked | fixed, targets)
    after = geometry.access_report(state, rid, blocked | fixed | zone, targets)
    return geometry.not_worse(before, after)


def _movable(state, source):
    rec = state["entities"].get(source)
    return bool(rec and rec.get("portable") and not rec.get("blocks_path") and not rec.get("docked") and not rec.get("mounted"))


def _free_receptacle(config, outlet):
    """First receptacle of an outlet no device is plugged into (None when all are taken)."""
    used = {int(rec.get("_socket") or 0) for rec in config["entities"].values()
            if rec.get("plugged_in") and rec.get("outlet") == outlet}
    for i in range(1, int(config["entities"][outlet].get("receptacles") or 0) + 1):
        if i not in used:
            return i
    return None


def _dock_at_outlet(p, rid, dock_type):
    """A dock standing against the wall right beside an outlet with a free receptacle (placed by Placer.furniture, so
    access is not made worse) whose clearance zone cuts nothing - the outlet itself stays reachable from outside the
    zone. The dock is plugged straight into it (a 0.5 m cord along the skirting: no cord across the floor). Returns the
    dock id or None."""
    room = p.c["rooms"][rid]
    for oid, cell in _outlets(p.c, rid):
        socket = _free_receptacle(p.c, oid)
        if socket is None:
            continue
        _, lateral = geometry.wall_inward(room, cell)
        beside = [(cell[0] + lateral[0] * k, cell[1] + lateral[1] * k) for k in (1, -1)]
        for near in [b for b in beside if geometry.in_grid(room, b)]:
            dock = p.furniture(rid, dock_type, wall=True, near=near, fallback=False)
            if dock is None:
                continue
            rec = p.c["entities"][dock]
            foot = geometry.footprint(rec)
            if cell not in foot and min(geometry.chebyshev(cell, f) for f in foot) == 1 and _zone_cuts_nothing(p, rid, dock):
                plug = min(foot, key=lambda f: (geometry.chebyshev(cell, f), f[1], f[0]))
                rec.update(plugged_in=True, outlet=oid, _socket=socket, plug_seated=1.0, cord_route="along_wall",
                           cord_cells=[[rid, [plug[0], plug[1]]], [rid, [cell[0], cell[1]]]], cord_length_used_m=0.8, cord_hazard=[])
                return dock
            del p.c["entities"][dock]
    return None


def _seat_on_dock(config, robot_rec, dock_id):
    """Put a household robot on its dock: same cell(s), oriented to fit the dock's footprint inside the room, marked
    docked (it sits on the charging plate; it is not a separate obstacle on the floor)."""
    drec = config["entities"][dock_id]
    room = config["rooms"][drec["location"]]
    dock_cells = set(geometry.footprint(drec))
    best = None
    for rot in (0, 90):
        for dx in (0, -1):
            for dy in (0, -1):
                pos = [drec["pos"][0] + dx, drec["pos"][1] + dy]
                cells = geometry.footprint(dict(robot_rec, rot=rot, pos=pos))
                if not all(geometry.in_grid(room, c) for c in cells):
                    continue
                key = (len(set(cells) - dock_cells), abs(dx) + abs(dy), rot)
                if best is None or key < best[0]:
                    best = (key, rot, pos)
    rot, pos = (best[1], best[2]) if best else (0, list(drec["pos"]))
    robot_rec["rot"] = rot
    robot_rec["pos"] = pos
    robot_rec["relation"] = "on"
    robot_rec["docked"] = True
    robot_rec["rv"]["dock"] = dock_id
    robot_rec["rv"]["docked"] = True
    robot_rec["rv"]["state"] = "docked"


VACUUM_BY_TIER = {"none": 0.2, "basic": 0.45, "connected": 0.7}
VACUUM_PET_BONUS = 0.1
ELDER_REACHER_P = 0.8
ELDER_REACHER_ROOMS = ("kitchen", "living_room", "hallway", "pantry", "laundry_room", "utility_closet")


def _profile(config):
    return ((config.get("household") or {}).get("profile") or {})


def _has_role(config, role):
    members = _profile(config).get("members") or {}
    return any((m or {}).get("role") == role for m in members.values()) or \
        any(p.get("role") == role for p in (config.get("people") or {}).values())


def vacuum_share(config):
    """Generation 2: the chance a household has a robot vacuum, from its smart-home tier (VACUUM_BY_TIER) plus
    VACUUM_PET_BONUS when a dog or cat lives there."""
    share = VACUUM_BY_TIER.get(_profile(config).get("smart_home"), 0.35)
    if any(pet.get("species") in ("dog", "cat") for pet in (config.get("pets") or {}).values()):
        share += VACUUM_PET_BONUS
    return min(1.0, share)


def _gen2_stream(rng):
    """A generation-2 random stream derived from the furnisher's own stream without drawing from it (its state seeds a
    string seed: deterministic in every process), so generation-2 extras never shift the generation-1 draws."""
    import random
    return random.Random("navigation_robot:gen2:" + ",".join(str(v) for v in rng.getstate()[1][:8]))


def _gen2_extras(p, config, gen_rng):
    """Generation-2 content placed after everything else (Placer draws from the house stream: placing it last moves
    nothing that was placed before)."""
    if _has_role(config, "elderly") and not any(r.get("type") == "reacher_grabber" for r in config["entities"].values()):
        if gen_rng.random() < ELDER_REACHER_P:
            _standing_item(p, _first(p, ["kitchen"]), ELDER_REACHER_ROOMS, "reacher_grabber")


@furnisher("navigation_robot.features", order=66)
def furnish(config, rng, opts, registry):
    gen2 = int(opts.get("gen") or 1) >= 2
    gen_rng = _gen2_stream(rng) if gen2 else None
    p = Placer(config, registry, rng)
    rooms = config["rooms"]
    conns = config.get("connections", {})
    for cid in sorted(conns):
        conn = conns[cid]
        a, b = conn["rooms"]
        if conn.get("kind") == "stairs":
            lower = a if rooms[a].get("floor", 0) <= rooms[b].get("floor", 0) else b
            conn.setdefault("run_m", round(rng.choice([0.25, 0.26, 0.27, 0.28]), 2))
            conn.setdefault("rise_m", 0.18)
            conn.setdefault("tread_material", rng.choice(["carpet", "hardwood", "hardwood"]))
            conn.setdefault("handrail", True)
            if min(rooms[a].get("floor", 0), rooms[b].get("floor", 0)) >= 0 and rng.random() < 0.15:
                conn.setdefault("winders", 3)
            if conn.get("handrail") and lower in conn.get("cells", {}):
                _handrail(p, registry, config, lower, cid, conn)
            continue
        door = config["entities"].get(conn.get("door") or "", {})
        if door.get("type") == "sliding_door":
            conn.setdefault("track", True)
            conn.setdefault("threshold_cm", 1.5)
        elif door.get("exterior") and door.get("category") == "door" and door.get("type") != "garage_door":
            conn.setdefault("threshold_cm", rng.choice([2.0, 2.5, 3.0]))
        elif {rooms[a].get("type"), rooms[b].get("type")} & {"bathroom", "half_bath"}:
            conn.setdefault("threshold_cm", 1.0)
    kitchen = _first(p, ["kitchen"])
    house = (opts.get("plan") or {}).get("dwelling", "house") != "apartment"
    if kitchen:
        if rng.random() < 0.7:
            _standing_item(p, kitchen, STOOL_ROOMS, "kitchen_step_stool")
        junk = _drawer(config, kitchen, "junk drawer")
        if junk:
            for type_name in ("tape_measure", "carpenter_pencil", "flashlight", "robot_lens_wipe"):
                if type_name == "flashlight" and rng.random() < 0.2:
                    continue
                p.inside(junk, type_name)
    tool_room = _first(p, ["garage", "basement", "utility_closet", "laundry_room"])
    if tool_room:
        bench = next((e for e in p.find("workbench", tool_room)), None) or next((e for e in p.find("shelf_unit", tool_room)), None)
        if house and rng.random() < 0.85:
            _standing_item(p, tool_room, LADDER_ROOMS, "step_ladder")
        elif not house:
            _standing_item(p, tool_room, APARTMENT_STOOL_ROOMS, "step_stool")
        if bench:
            if config["entities"][bench].get("surface"):
                p.on(bench, "spirit_level")
                if rng.random() < 0.5:
                    p.on(bench, "laser_distance_meter")
            else:
                p.inside(bench, "spirit_level", shelf=1)
                if rng.random() < 0.5:
                    p.inside(bench, "laser_distance_meter", shelf=1)
        if rng.random() < 0.4:
            _standing_item(p, tool_room, REACHER_ROOMS, "reacher_grabber")
    lamp = registry.make("robot_headlamp", "robot")
    lamp["relation"] = "worn"
    config["entities"][p.new_id("robot_headlamp")] = lamp
    living = _first(p, ["living_room"])
    if living and rng.random() < 0.3:
        station = p.mount(living, "battery_charging_station", height=0.7, strict=True)
        if station:
            p.on(station, "robot_battery_pack", charge_pct=100.0)
    if rng.random() < (vacuum_share(config) if gen2 else 0.5):
        prefer = VACUUM_ROOMS if rng.random() < 0.7 else ("hallway",) + VACUUM_ROOMS
        model = "robot_vacuum_mop" if rng.random() < 0.4 else "robot_vacuum"
        bin_fill = round(rng.uniform(0.05, 0.5), 2)
        brush = round(rng.uniform(0.0, 0.4), 2)
        tank = round(rng.uniform(0.0, 0.3), 2)
        dock = None
        for kind in prefer:
            for rid in p.rooms(kind):
                if any(rooms[rid].get(k) for k in ("shared", "private", "off_limits", "outdoor")):
                    continue
                dock = _dock_at_outlet(p, rid, "robot_vacuum_dock")
                if dock:
                    break
            if dock:
                break
        if dock:
            rv = registry.make(model, config["entities"][dock]["location"])
            _seat_on_dock(config, rv, dock)
            rv["rv"]["bin_fill"] = bin_fill
            rv["rv"]["brush_hair"] = brush
            # no standing schedule in generated homes: a run would quietly clean floors during unrelated tasks;
            # specs add schedules with the navigation_robot.robot_vacuum helper
            rv["rv"]["schedule"] = []
            if rv["rv"].get("mop"):
                rv["rv"]["mop_tank_l"] = tank
                rv["rv"]["tank_liquid"] = "water"
            config["entities"][p.new_id(model)] = rv
    back = _first(p, ["backyard"])
    if back and house and rng.random() < 0.15:
        # beside an outdoor outlet when the yard has one (plugged straight in), else anywhere along the fence/wall (the
        # electrical hookup feeds it through its low-voltage transformer)
        dock = _dock_at_outlet(p, back, "robot_mower_dock") or p.furniture(back, "robot_mower_dock", wall=True)
        if dock and not _zone_cuts_nothing(p, back, dock):
            del config["entities"][dock]
            dock = None
        if dock:
            mower = registry.make("robot_mower", back)
            _seat_on_dock(config, mower, dock)
            config["entities"][p.new_id("robot_mower")] = mower
    if gen2:
        _gen2_extras(p, config, gen_rng)
