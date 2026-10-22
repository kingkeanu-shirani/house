"""Furnishers: climate equipment in generated houses (counts stay modest; Placer None is tolerated).

climate.orientation (order 21, before mechanisms' coverings at 25): every window gets ``facing_deg`` from the wall it
  sits on (row 0 north, last column east, last row south, column 0 west); outdoor rooms next to the house get
  ``shade_walls`` (the house wall along the edge holding the door cell, height = floors x 2.8 m + 0.6 m eaves).
climate.hvac (order 32): by region/dwelling - US/CA houses: gas furnace + AC (60 %) or heat pump (40 %) as
  path-blocking furniture (Placer.furniture) in the basement / utility closet / garage, a laundry room only while a
  washer + dryer spot stays free, a mudroom, else a hallway closet or another service-like room; outdoor condenser in
  the backyard, a thermostat in the ground-floor hallway (living room in apartments), supply registers in every
  conditioned room (floor / ceiling / wall per house), return grilles in hallways, a loaded filter in the slot and a
  spare wrapped filter nearby; UK/EU: gas boiler + radiators with TRVs; JP: a mini-split head per main room with its
  IR remote (in a wall holder). Initial room temperatures: conditioned rooms at the thermostat's active setpoint, garage/attic/basement/
  shed at their equilibrium with the outdoors.
climate.fans (order 46): ceiling fans in some living rooms and bedrooms (pull-chain or RF remote, light kit),
  an occasional portable fan (Placer.floor_item; electrical routes its cord when it plugs it in).
climate.air (order 56): dehumidifier in some basements, purifier/humidifier/hygrometer in some bedrooms, rarely a
  space heater in an office/bedroom (all off; floor units through Placer.floor_item).
climate.access (order 99): wide wall devices run along their wall inside the room; a register / grille / radiator /
  thermostat walled off by later furniture moves to a reachable wall cell.

Wall devices use wall_options / wall_cell (kernel door_clearance, door_swing, reachable_cells, floor_standing); loose
floor items use Placer.floor_item (kernel keep_access rule); equipment uses Placer.furniture. Nothing here keeps a
private copy of the door-swing or reachability rules.
"""
from ...generator.place import Placer
from ...kernel import geometry
from ...kernel.plugins import furnisher
from . import airclean
from . import common as C
from . import hvac

# where a furnace / air handler goes, in order of preference: basements, utility closets and garages before laundry
# rooms (only while a washer + dryer spot stays free) and mudrooms; then hallway closets and other service-like rooms
EQUIP_ROOMS = ("basement", "utility_closet", "garage", "laundry_room", "mudroom")
SERVICE_ROOMS = ("hallway", "entryway", "office", "kitchen", "dining_room")
REGISTER_COUNT = {"living_room": 2, "master_bedroom": 2, "kitchen": 1, "dining_room": 1, "bedroom": 1, "guest_room": 1, "nursery": 1,
                  "office": 1, "playroom": 1, "gym": 1, "craft_room": 1, "bathroom": 1, "half_bath": 0, "entryway": 1, "mudroom": 1,
                  "laundry_room": 1, "hallway": 1, "basement": 2}
NO_HVAC = {"coat_closet", "linen_closet", "pantry", "utility_closet", "garage", "attic", "shed", "elevator", "building_corridor",
           "lobby", "mail_room", "shared_laundry"}


def _wall_of_cell(room, cell, rng):
    cols, rows = geometry.grid_size(room)
    x, y = int(cell[0]), int(cell[1])
    options = []
    if y == 0:
        options.append(0.0)
    if x == cols - 1:
        options.append(90.0)
    if y == rows - 1:
        options.append(180.0)
    if x == 0:
        options.append(270.0)
    if not options:
        dists = [(y, 0.0), (cols - 1 - x, 90.0), (rows - 1 - y, 180.0), (x, 270.0)]
        return min(dists)[1]
    return options[0] if len(options) == 1 else rng.choice(options)


@furnisher("climate.orientation", order=21)
def furnish_orientation(config, rng, opts, registry):
    ents = config["entities"]
    rooms = config["rooms"]
    for wid in sorted(ents):
        rec = ents[wid]
        if rec.get("category") != "window" or rec.get("location") not in rooms or not rec.get("pos"):
            continue
        rec.setdefault("facing_deg", _wall_of_cell(rooms[rec["location"]], rec["pos"], rng))
    floors = int(((opts.get("plan") or {}).get("floors")) or (config.get("metadata") or {}).get("floors") or 1)
    for cid in sorted(config.get("connections", {})):
        conn = config["connections"][cid]
        a, b = conn["rooms"]
        for out_room, in_room in ((a, b), (b, a)):
            if not rooms[out_room].get("outdoor") or rooms[in_room].get("outdoor"):
                continue
            cell = conn.get("cells", {}).get(out_room)
            if not cell:
                continue
            room = rooms[out_room]
            cols, rows = geometry.grid_size(room)
            edge = "N" if cell[1] == 0 else ("S" if cell[1] == rows - 1 else ("W" if cell[0] == 0 else ("E" if cell[0] == cols - 1 else None)))
            if edge is None:
                continue
            walls = room.setdefault("shade_walls", [])
            if not any(w.get("edge") == edge for w in walls):
                w_m, d_m = room["size_m"][0], room["size_m"][1]
                walls.append({"edge": edge, "height_m": round(floors * 2.8 + 0.6, 2), "from_m": 0.0,
                              "to_m": float(w_m if edge in ("N", "S") else d_m), "label": "house wall"})


# ---------------------------------------------------------------------------------------------
# placement on top of the kernel v2.3 layout model (geometry.door_clearance / door_swing / reachable_cells /
# access_targets / access_report / floor_standing, Placer.furniture / floor_item / mount). No private door-swing or
# reachability copies: what the generator's repair pass and tools/layout_audit.py check is what climate places by.

WALL_DEVICE_KINDS = ("register", "return_grille", "radiator", "thermostat", "mini_split")


def config_state(config, robot_room=None):
    """A geometry view of a config (generation / setup time)."""
    return {"rooms": config["rooms"], "entities": config["entities"], "people": config.get("people", {}),
            "pets": config.get("pets", {}), "connections": config.get("connections", {}),
            "robot": {"room": robot_room or config.get("start_room"), "hands": {}, "pos": None}}


def can_reach(state, rid, cells, radius=1, reach=None):
    """True when the robot can stand within ``radius`` cells of ``cells`` on floor joined to the room's doorways
    (kernel reachable_cells: 4-connected, path-blocking footprints impassable). ``reach`` is an optional precomputed
    geometry.reachable_cells(state, rid) for loops. Rooms without doorways count as reachable."""
    room = state["rooms"][rid]
    if not geometry.doorway_cells(state, rid):
        return True
    if reach is None:
        reach = geometry.reachable_cells(state, rid)
    own = {tuple(c) for c in cells}
    return any(n in reach for n in geometry.near_cells(own, room, radius) if n not in own)


def inaccessible(state, rid, level="all"):
    """Entity ids of the room the robot cannot get to (kernel access_targets / access_report: radius 2 for mounts at
    0.85 m or higher, standing cells joined to the doorways)."""
    targets = geometry.access_targets(state, rid, level=level)
    rep = geometry.access_report(state, rid, set(geometry.blocked_cells(state, rid, include_agents=False)), targets)
    return {k for k in targets if isinstance(k, str) and k not in rep["ok"]}


def door_keepout(state, rid):
    """Cells even a flush or thin wall device does not use: doorway cells, the leaf of a hinged door swinging into the
    room (geometry.door_swing) and an overhead door's threshold run. The rest of geometry.door_clearance (the cells
    beside a doorway) may hold a floor register, grille or thermostat when the room offers nothing else."""
    cells = set(geometry.doorway_cells(state, rid))
    for cid in sorted(state.get("connections", {})):
        conn = state["connections"][cid]
        door = conn.get("door")
        if rid not in conn.get("cells", {}) or not door or door not in state["entities"]:
            continue
        if geometry.is_overhead_door(state["entities"][door], conn):
            cells.update(tuple(c) for c in geometry.overhead_door_cells(state, door).get(rid, []))
            continue
        srid, leaf = geometry.door_swing(state, door)
        if srid == rid:
            cells.update(leaf)
    return cells


def _perimeter(room):
    cols, rows = geometry.grid_size(room)
    return sorted({(x, 0) for x in range(cols)} | {(x, rows - 1) for x in range(cols)} | {(0, y) for y in range(rows)} |
                  {(cols - 1, y) for y in range(rows)})


def _wall_rot(room, cell, record):
    """A wide wall device on a side wall runs along it (rot 90)."""
    if record is None:
        return 0
    cols, rows = geometry.grid_size(room)
    x, y = int(cell[0]), int(cell[1])
    size = record.get("size_m") or [0.1, 0.1]
    wide = C.num(size[0], 0.1) > C.num(size[1], 0.1)
    return 90 if wide and x in (0, cols - 1) and y not in (0, rows - 1) else int(record.get("rot", 0) or 0)


def wall_options(p, rid, record=None, avoid_furniture=True, exclude=(), clearance="door"):
    """[(cell, rot)] perimeter cells for a wall / floor device (register, grille, radiator, thermostat, mini-split,
    boiler) whose footprint stays in the room, off other climate wall devices and floor-standing mounts (closet
    shelving, water meters), off path-blocking furniture (unless ``avoid_furniture`` is False: a floor register under
    furniture is then concealed by it; mounts above the furniture's top are fine) and with a doorway-joined standing
    cell in reach (radius 2 from 0.85 m up). ``clearance`` "door" keeps the whole doorway clearance free,
    "keepout" only doorways, door leaves and overhead thresholds (thin devices in a packed room)."""
    state = p.state()
    room = p.c["rooms"][rid]
    height = C.num((record or {}).get("mount_height_m"), 0.0)
    radius = 2 if height >= 0.85 else 1
    blocking = geometry.blocked_cells(state, rid, include_agents=False)
    used = set(exclude)
    for eid in geometry.room_entities(state, rid):
        e = p.c["entities"][eid]
        if not e.get("pos"):
            continue
        if e.get("climate_kind") in WALL_DEVICE_KINDS or (e.get("mounted") and geometry.floor_standing(e)):
            used.update(geometry.footprint(e))
    keep = geometry.door_clearance(state, rid) if clearance == "door" else door_keepout(state, rid)
    reach = geometry.reachable_cells(state, rid)

    def under_ok(c):
        if c not in blocking or not avoid_furniture:
            return True
        tops = [C.num((p.c["entities"][b].get("size_m") or [0, 0, 1.0])[2], 1.0) for b in blocking[c] if b in p.c["entities"]]
        return height >= 0.85 and max(tops or [9.0]) <= height - 0.1

    out = []
    for c in _perimeter(room):
        rot = _wall_rot(room, c, record)
        cells = geometry.footprint(dict(record, pos=list(c), rot=rot)) if record is not None else [c]
        if not all(geometry.in_grid(room, k) for k in cells) or any(k in keep or k in used or not under_ok(k) for k in cells):
            continue
        if not can_reach(state, rid, cells, radius, reach):
            continue
        out.append((c, rot))
    return out


def _span(rec):
    """(bottom, top) height of a record in metres (mounts from mount_height_m / z_m, floor items from 0)."""
    h0 = C.num(rec.get("mount_height_m", rec.get("z_m", 0.0)), 0.0) if rec.get("mounted") or rec.get("mount_height_m") is not None else 0.0
    size = rec.get("size_m") or [0.1, 0.1, 0.1]
    return h0, h0 + C.num(size[2] if len(size) > 2 else 0.1, 0.1)


def wall_clashes(p, rid, record, exclude_ids=()):
    """Footprint cells of things a climate wall device at ``record``'s height must not share: other wall mounts whose
    height span overlaps it (a wall-hung water heater, a breaker panel, a shelf - round-2 repro: seed 66 apartment, the
    boiler on the water heater's wall cell) and loose floor items standing in front of a device lower than their top
    (a floor register under a utility bucket: seed 2 apartment). Path-blocking furniture and floor-standing mounts are
    handled by wall_options itself."""
    if record is None:
        return set()
    h0, h1 = _span(dict(record, mounted=True))
    out = set()
    for eid in geometry.room_entities(p.state(), rid):
        e = p.c["entities"][eid]
        if eid in exclude_ids or not e.get("pos") or e.get("climate_kind") in WALL_DEVICE_KINDS or e.get("ground_layer"):
            continue
        if e.get("category") in ("floor", "wall", "window", "door", "light", "ceiling_light") or "connects" in e:
            continue
        if e.get("mounted"):
            if geometry.floor_standing(e):
                continue
            m0, m1 = _span(e)
            if m0 < h1 - 0.02 and h0 < m1 - 0.02 and min(C.num(v, 0.1) for v in (e.get("size_m") or [0.1, 0.1])[:2]) >= 0.05:
                out.update(geometry.footprint(e))
        elif geometry.floor_standing(e) and not geometry.blocks(e):
            if h0 < _span(e)[1] - 0.02:
                out.update(geometry.footprint(e))
    return out


def pick_wall_spot(p, rid, rng, record, opts):
    """rng.choice over wall_options, re-picked among the spots that clash with nothing (wall_clashes) only when the
    first pick clashes, so houses whose pick was fine keep their layout."""
    spot = rng.choice(opts)
    if record is None:
        return spot
    clash = wall_clashes(p, rid, record)
    if not clash:
        return spot

    def bad(o):
        return any(k in clash for k in geometry.footprint(dict(record, pos=list(o[0]), rot=o[1])))
    if bad(spot):
        good = [o for o in opts if not bad(o)]
        if good:
            spot = rng.choice(good)
    return spot


def _clashes(p, rid, record, spot):
    """True when a wall device at ``spot`` (cell, rot) shares a cell with something in wall_clashes."""
    if record is None:
        return False
    clash = wall_clashes(p, rid, record)
    return bool(clash) and any(k in clash for k in geometry.footprint(dict(record, pos=list(spot[0]), rot=spot[1])))


def wall_cell(p, rid, rng, record=None, avoid_furniture=True, exclude=(), fallback=True):
    """A random wall cell from wall_options: outside the doorway clearance first; a device that does not stand on
    the floor (geometry.floor_standing) may then use the cells beside a doorway (never the doorway, a door leaf or an
    overhead threshold). With ``fallback`` a room with no reachable spot still returns a free perimeter cell (the late
    climate.access pass re-sites it); else None. Returns (cell, rot)."""
    passes = ["door"]
    if record is None or not geometry.floor_standing(dict(record, mounted=True)):
        passes.append("keepout")
    first = None
    for clearance in passes:
        opts = wall_options(p, rid, record, avoid_furniture=avoid_furniture, exclude=exclude, clearance=clearance)
        if not opts:
            continue
        if first is None:
            cell, rot = pick_wall_spot(p, rid, rng, record, opts)
            first = (list(cell), rot)
            if not _clashes(p, rid, record, (cell, rot)):
                return first
            continue
        # every spot of the stricter pass shares wall space with another mount (round-4 repro: seeds 18 apartment, 20, 27
        # - a boiler hung on the breaker panel's cell in a 1.5 m utility closet, NEC 110.26): a clean spot beside the
        # doorway (never in it or in a door leaf) wins over a clashing one
        clash = wall_clashes(p, rid, record)
        good = [o for o in opts if not any(k in clash for k in geometry.footprint(dict(record, pos=list(o[0]), rot=o[1])))]
        if good:
            cell, rot = rng.choice(good)
            return list(cell), rot
    if first is not None:
        return first
    if not fallback:
        return None, 0
    state = p.state()
    room = p.c["rooms"][rid]
    keep = geometry.door_clearance(state, rid)
    blocked = set(geometry.blocked_cells(state, rid, include_agents=False)) if avoid_furniture else set()
    perim = _perimeter(room)
    options = [c for c in perim if c not in keep and c not in blocked and c not in exclude] or \
        [c for c in perim if c not in door_keepout(state, rid)] or perim
    cell = rng.choice(options)
    rot = _wall_rot(room, cell, record)
    if record is not None and not all(geometry.in_grid(room, k) for k in geometry.footprint(dict(record, pos=list(cell), rot=rot))):
        # a wide device (1 m radiator) must not hang off the grid and be re-fitted late onto later furniture
        # (round-2 repro: seed 1 apartment kitchen radiator turned under the range by climate.access)
        fitting = []
        for c in options:
            first = _wall_rot(room, c, record)
            for r in (first, 0 if first == 90 else 90):
                if all(geometry.in_grid(room, k) and k not in blocked for k in geometry.footprint(dict(record, pos=list(c), rot=r))):
                    fitting.append((c, r))
        if fitting:
            cell, rot = fitting[0]
    return list(cell), rot


def _free_wall_cell(p, rid, rng, avoid_furniture=True, exclude=(), record=None):
    return wall_cell(p, rid, rng, record=record, avoid_furniture=avoid_furniture, exclude=exclude)[0]


def reachable_wall_spot(p, rid, rng, record=None, exclude=(), avoid_combustibles=False):
    """(cell, rot) of a reachable wall cell for a device (see wall_cell; radiators can ask for one away from
    combustibles); None when the room offers none."""
    passes = ["door"]
    if record is None or not geometry.floor_standing(dict(record, mounted=True)):
        passes.append("keepout")
    state = p.state()
    fabric = _combustible_cells(state, rid) if avoid_combustibles else set()
    for clearance in passes:
        opts = wall_options(p, rid, record, exclude=exclude, clearance=clearance)
        if avoid_combustibles:
            opts = [o for o in opts if not _near_combustible(state, rid, o[0], cells=fabric)]
        if opts:
            cell, rot = pick_wall_spot(p, rid, rng, record, opts)
            return list(cell), rot
    return None


def reachable_wall_cell(p, rid, rng, record=None, exclude=(), avoid_combustibles=False):
    spot = reachable_wall_spot(p, rid, rng, record=record, exclude=exclude, avoid_combustibles=avoid_combustibles)
    return spot[0] if spot else None


def conceal_if_under_furniture(config, eid):
    """A floor register whose cell lies inside a furniture footprint is hidden under/behind that furniture."""
    rec = config["entities"][eid]
    rid = rec.get("location")
    if rid not in config["rooms"] or not rec.get("pos") or rec.get("register_kind") == "ceiling":
        return None
    cell = tuple(rec["pos"])
    for fid in sorted(config["entities"]):
        f = config["entities"][fid]
        if fid == eid or f.get("location") != rid or not f.get("blocks_path") or not f.get("pos"):
            continue
        if cell in geometry.footprint(f):
            if f.get("openable"):
                return "openable"   # a vent inside a closed cabinet's footprint would be unobservable: re-site it
            rec["floor_cell"] = list(rec.pop("pos"))
            rec["location"], rec["relation"] = fid, ("under" if C.num((f.get("size_m") or [0, 0, 0.5])[2]) >= 0.3 else "behind")
            rec.pop("mounted", None)
            return fid
    return None


def tidy_doorways(config):
    """Deprecated (kernel v2.3 generation keeps loose floor items out of doorway clearance and off every route): move
    loose floor items 0.15 m or taller that stand in a doorway clearance (geometry.door_clearance) to the nearest cell
    that cuts nothing (geometry.free_floor_cell keep_access). Returns the moved ids."""
    moved = []
    ents = config["entities"]
    for rid in sorted(config["rooms"]):
        state = config_state(config)
        clear = geometry.door_clearance(state, rid)
        for eid in sorted(geometry.room_entities(state, rid)):
            rec = ents[eid]
            if not rec.get("pos") or not rec.get("portable") or geometry.blocks(rec) or rec.get("mounted") or \
                    rec.get("ground_layer") or "connects" in rec or C.num((rec.get("size_m") or [0, 0, 0.1])[2], 0.1) < 0.15:
                continue
            if not set(geometry.footprint(rec)) & clear:
                continue
            cell = geometry.free_floor_cell(config_state(config), rid, near=tuple(rec["pos"]), ignore=(eid,), record=rec,
                                            strict=True, keep_access=True)
            if cell is not None:
                rec["pos"] = cell
                moved.append(eid)
    return moved


# ---------------------------------------------------------------------------------------------
# equipment rooms: the furnace / air handler is path-blocking furniture placed through Placer.furniture

WASHER_PAIR_M = [1.2, 0.6, 0.9]   # side-by-side washer + dryer wall spot (textiles_laundry places them after us)


def _washer_spot_left(p, rid):
    """True when a laundry room still offers a wall spot for a washer and dryer side by side (or already has one)."""
    for eid in geometry.room_entities(p.state(), rid):
        rec = p.c["entities"][eid]
        if rec.get("category") == "appliance" and ("washing_machine" in (rec.get("is_a") or []) or "washer" in str(rec.get("type"))):
            return True
    probe = p.furniture(rid, "generic_item", wall=True, fallback=False, label="washer spot probe", size_m=list(WASHER_PAIR_M),
                        blocks_path=True, portable=False)
    if probe is None:
        return False
    del p.c["entities"][probe]
    return True


def place_equipment(p, rooms, type_name, rng=None, **props):
    """Floor-standing HVAC equipment (furnace / air handler, 0.55 x 0.75 m) as path-blocking furniture in the first room
    of ``rooms`` where Placer.furniture finds a spot (whole footprint in the room, off doorway clearance and door
    leaves, clear of furniture, appliances, loose items and floor-standing mounts such as closet shelving, every
    doorway, fixture and mount that was accessible still accessible, the unit itself serviceable). Wall spots in every
    room come before free-standing ones; a laundry room is used only while a washer + dryer spot remains.
    Returns (eid, rid) or (None, None)."""
    for wall_only in (True, False):
        for rid in rooms:
            if rid not in p.c["rooms"]:
                continue
            eid = p.furniture(rid, type_name, wall=True, fallback=not wall_only, **props)
            if eid is None:
                continue
            if p.c["rooms"][rid].get("type") == "laundry_room" and not _washer_spot_left(p, rid):
                del p.c["entities"][eid]
                continue
            if _pinches_passage(p, rid, eid):
                del p.c["entities"][eid]
                continue
            return eid, rid
    return None, None


def _pinches_passage(p, rid, eid):
    """True when equipment standing in a pass-through room (two or more doorways) leaves less than two free cells
    (1 m) across the room's narrow axis beside it: no 0.9 m wheelchair route past it (round-4 repro: generate_house(4,
    dwelling='apartment') and (97, 'apartment'), a heat-pump air handler in a 1.5 m hallway left a 0.5 m pinch)."""
    state = p.state()
    if len(geometry.doorway_cells(state, rid)) < 2:
        return False
    room = p.c["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    cells = geometry.footprint(p.c["entities"][eid])
    if not cells:
        return False
    xs = [c[0] for c in cells]
    ys = [c[1] for c in cells]
    if cols <= rows:
        return cols - (max(xs) - min(xs) + 1) < 2
    return rows - (max(ys) - min(ys) + 1) < 2


def _setpoint_c(settings):
    units = settings.get("units", "C")
    return C.to_c(settings.get("heat_setpoint"), units), C.to_c(settings.get("cool_setpoint"), units)


def system_choice(region, dwelling, rng):
    if region == "jp":
        return "mini_split"
    if region in ("uk", "eu"):
        return "boiler" if rng.random() < 0.85 else ("heat_pump" if dwelling != "apartment" else "boiler")
    if dwelling == "apartment":
        return "heat_pump" if rng.random() < 0.6 else "furnace_ac"
    return "furnace_ac" if rng.random() < (0.6 if region == "us" else 0.7) else "heat_pump"


def initial_mode(t_out, cooling):
    if not cooling or t_out < 16.0:
        return "heat"
    return "cool" if t_out > 22.0 else "auto"


# Generation 2 (TIER1_SWEEP 13.3, docs/GENERATOR.md): households set the thermostat for the season, not for the
# hour's weather. It is on "heat" in winter and in the months whose mean daily temperature (the region's 1991-2020
# normals, generator/climate_normals.json) is below 16 C, on "cool" (where the system cools) in the months above 20 C,
# and "off" in between. Assumptions, order of magnitude: heating degree days use a 15.5 C (UK) to 18.3 C (US, 65 F)
# base, so a house needs heat on some days of every month whose mean is below about 16 C (Philadelphia October-April,
# London October-May, Toronto October-May); cooling degree days use 18.3 C and air conditioning runs in the months
# whose mean exceeds about 20 C (Philadelphia and Tokyo June-September, Toronto July-August, never in London).
HEAT_SEASON_BELOW_C = 16.0
COOL_SEASON_ABOVE_C = 20.0


def month_mean_c(config, region):
    """Mean daily temperature (C) of the start month in the region's normals, or None without them."""
    from ...generator import climate as gclimate
    try:
        row = gclimate.normals()["regions"][region]
        month = int(str((config.get("clock") or {}).get("start") or "")[5:7]) - 1
        return (float(row["high_c"][month]) + float(row["low_c"][month])) / 2.0
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def season_mode(season, mean_c, cooling):
    """The thermostat mode a household keeps in a season (generation 2): heat, cool or off."""
    if season == "winter" or (mean_c is not None and mean_c < HEAT_SEASON_BELOW_C):
        return "heat"
    if cooling and mean_c is not None and mean_c > COOL_SEASON_ABOVE_C:
        return "cool"
    return "off"


@furnisher("climate.hvac", order=32)
def furnish_hvac(config, rng, opts, registry):
    if any(e.get("climate_kind") in ("hvac_system", "mini_split") for e in config["entities"].values()):
        return
    p = Placer(config, registry, rng)
    meta = config.get("metadata") or {}
    region = opts.get("region") or meta.get("region") or "us"
    dwelling = (opts.get("plan") or {}).get("dwelling") or meta.get("dwelling_type", "house")
    units = "F" if meta.get("units") == "imperial" else "C"
    kind = system_choice(region, dwelling, rng)
    mode_rule = None
    if int(opts.get("gen") or 1) >= 2:
        mean_c = month_mean_c(config, region)
        mode_rule = lambda cooling: season_mode(opts.get("season"), mean_c, cooling)      # noqa: E731
    build_hvac(config, p, rng, kind, units, None, tstat_type="smart_thermostat" if rng.random() < 0.35 else "thermostat",
               condenser=dwelling != "apartment", merv=rng.choice([8, 8, 11]), filter_dirt=round(rng.uniform(0.15, 0.85), 2),
               spare=rng.random() < 0.7, mode_rule=mode_rule)


def build_hvac(config, p, rng, kind, units, mode=None, heat_c=21.0, cool_c=24.5, tstat_type="thermostat", therm_room=None,
               register_kind=None, condenser=True, merv=8, filter_dirt=0.3, spare=True, nominal=None, set_temps=True, setback_c=0.0,
               night_c=1.5, mode_rule=None):
    """Build a complete HVAC installation; returns {"thermostat", "hvac", "thermo_room", "registers", "returns", "radiators", "filter"}.
    mode: the thermostat mode; else mode_rule(cooling) (generation 2: the season's mode), else initial_mode from the
    outdoor temperature now."""
    rooms = config["rooms"]
    t_out = C.num((config.get("weather") or {}).get("temperature_c"), 15.0)
    indoor = [r for r in sorted(rooms) if not rooms[r].get("outdoor") and not rooms[r].get("shared")]
    conditioned = [r for r in indoor if rooms[r]["type"] not in NO_HVAC]
    out = {"thermostat": None, "hvac": None, "thermo_room": None, "registers": [], "returns": [], "radiators": [], "filter": None,
           "units": units, "spare": None, "condenser": None, "mini_splits": []}
    if not conditioned:
        return out
    halls = [r for r in indoor if rooms[r]["type"] == "hallway"]
    living = next((r for r in conditioned if rooms[r]["type"] == "living_room"), conditioned[0])
    ground = C.num(rooms[living].get("floor"))
    if therm_room is None:
        therm_room = next((r for r in halls if C.num(rooms[r].get("floor")) == ground), None) or \
            next((r for r in conditioned if rooms[r]["type"] == "living_room"), conditioned[0])
    out["thermo_room"] = therm_room
    if kind == "mini_split":
        out["mini_splits"] = _mini_splits(config, p, rng, conditioned, units, t_out, mode or (mode_rule(True) if mode_rule else None))
        if set_temps:
            _initial_temps(config, None, t_out)
        return out
    equip_rooms = [r for t in EQUIP_ROOMS for r in indoor if rooms[r]["type"] == t]
    equip_room = equip_rooms[0] if equip_rooms else therm_room
    type_name = {"furnace_ac": "furnace", "heat_pump": "heat_pump_air_handler", "boiler": "boiler"}[kind]
    if kind == "boiler":
        # wall-hung at 1 m: never where closet shelving or a water heater stands (wall_options), serviceable
        sid = wall_mount(p, equip_room, rng, type_name, 1.0)
        config.setdefault("utilities", {}).setdefault("heating_pressure_bar", 1.5)
    else:
        sid = _place_air_handler(config, p, type_name, equip_rooms, indoor, therm_room)
        equip_room = config["entities"][sid]["location"]
    out["hvac"] = sid
    rec = config["entities"][sid]
    if nominal and isinstance((rec.get("slots") or {}).get("filter"), dict):
        rec["slots"]["filter"]["fit"]["nominal"] = nominal
    cooling = C.num(rec.get("cooling_capacity_w")) > 0
    mode = mode or (mode_rule(cooling) if mode_rule else initial_mode(t_out, cooling))
    settings, options = hvac.thermostat_settings(units, mode, heat_c, cool_c, heat_pump=kind == "heat_pump", cooling=cooling,
                                                 setback_c=setback_c, night_c=night_c)
    tid = wall_mount(p, therm_room, rng, tstat_type, 1.5, controls_hvac=sid, settings=settings, setting_options=options)
    out["thermostat"] = tid
    sync_period(config, tid)
    if kind == "boiler":
        for rid in conditioned:
            if rooms[rid]["type"] in ("hallway", "entryway") and rng.random() < 0.5:
                continue
            cell, rot = _radiator_cell(p, rid, rng)
            out["radiators"].append(p.mount(rid, "radiator", height=0.15, cell=cell, rot=rot, system=sid,
                                            label="{} radiator".format(rooms[rid]["label"].lower())))
    else:
        rkind = register_kind or rng.choice(["supply_register", "supply_register", "supply_register", "ceiling_register", "wall_register"])
        names = {"supply_register": "floor register", "ceiling_register": "ceiling vent", "wall_register": "wall vent"}
        heights = {"supply_register": 0.0, "ceiling_register": 2.4, "wall_register": 0.3}
        for rid in conditioned:
            for i in range(REGISTER_COUNT.get(rooms[rid]["type"], 1)):
                # about one register in eight ends up under / behind furniture (concealed by it)
                reg = wall_mount(p, rid, rng, rkind, heights[rkind], avoid_furniture=rng.random() < 0.88, system=sid,
                                 label="{} {}".format(rooms[rid]["label"].lower(), names[rkind]))
                cell = tuple(config["entities"][reg]["pos"])
                if conceal_if_under_furniture(config, reg) == "openable":
                    rrec = config["entities"][reg]
                    rrec["pos"], rrec["rot"] = wall_cell(p, rid, rng, record=rrec, exclude=(cell,))
                out["registers"].append(reg)
        for rid in halls or [therm_room]:
            out["returns"].append(wall_mount(p, rid, rng, "return_grille", 0.3, system=sid))
        nominal = (rec["slots"]["filter"].get("fit") or {}).get("nominal", "16x25x1")
        fid = p.make("hvac_filter", sid, **_filter_props(nominal, merv, dirt=filter_dirt, wrapped=False))
        config["entities"][fid]["relation"] = "part"
        config["entities"][fid]["slot"] = "filter"
        rec["slots"]["filter"]["item"] = fid
        out["filter"] = fid
        if spare:
            out["spare"] = p.floor_item(equip_room, "hvac_filter", **_filter_props(nominal, merv))
        if condenser:
            yard = next((r for r in sorted(rooms) if rooms[r]["type"] in ("backyard", "side_yard")), None)
            if yard:
                cid = p.furniture(yard, "outdoor_condenser", serves=sid)
                if cid:
                    rec["condenser"] = cid
                    out["condenser"] = cid
    if set_temps:
        _initial_temps(config, tid, t_out)
    return out


def wall_mount(p, rid, rng, type_name, height, avoid_furniture=True, exclude=(), **props):
    """Mount a climate wall / floor device on a cell from wall_cell (rot 90 for wide devices on side walls)."""
    probe = p.reg.make(type_name, rid, **props)
    probe["mount_height_m"] = height
    cell, rot = wall_cell(p, rid, rng, record=probe, avoid_furniture=avoid_furniture, exclude=exclude)
    return p.mount(rid, type_name, height=height, cell=cell, rot=rot, **props)


def _place_air_handler(config, p, type_name, equip_rooms, indoor, therm_room):
    """Furnace / air handler as path-blocking furniture (place_equipment): equipment rooms in EQUIP_ROOMS order, then
    hallway closets and other service-like rooms, then any indoor room. A 1.5 m utility closet that already holds
    shelving and a water heater has no room for a 0.55 x 0.75 m air handler, so it goes to the next room instead of
    overlapping them; a small laundry room keeps its washer spot."""
    rooms = config["rooms"]
    fallback = sorted((r for r in indoor if rooms[r]["type"] in SERVICE_ROOMS), key=lambda r: (SERVICE_ROOMS.index(rooms[r]["type"]), r))
    order = []
    for r in equip_rooms + fallback + [therm_room] + indoor:
        if r not in order:
            order.append(r)
    sid, _ = place_equipment(p, order, type_name)
    if sid is not None:
        return sid
    # no room can take it as furniture (never seen in generated houses): a floor-standing mount, which Placer.mount
    # keeps off furniture, shelving and doorway clearance without cutting access
    return p.mount(order[0], type_name, height=0.0)


def _combustible_cells(state, rid):
    cells = set()
    for oid in sorted(state["entities"]):
        rec = state["entities"][oid]
        if rec.get("category") in ("floor", "wall", "window", "light", "switch") or not C.is_combustible(rec):
            continue
        if C.room_of(state, oid) != rid:
            continue
        _, found = geometry.anchor(state, oid)
        cells.update(tuple(c) for c in found or [])
    return cells


def _near_combustible(state, rid, cell, radius=2, cells=None):
    cells = _combustible_cells(state, rid) if cells is None else cells
    return any(geometry.chebyshev(tuple(cell), c) <= radius for c in cells)


def _radiator_cell(p, rid, rng):
    """(cell, rot) for a radiator: a reachable wall cell (wall_options), preferring one under a window and away from
    curtains, beds and other fabrics (hot radiators near textiles are flagged by the textiles domain)."""
    state = p.state()
    probe = p.reg.make("radiator", rid)
    probe["mount_height_m"] = 0.15
    wins = {tuple(e["pos"]) for e in p.c["entities"].values() if e.get("category") == "window" and e.get("location") == rid and e.get("pos")}
    fabric = _combustible_cells(state, rid)
    for clearance in ("door", "keepout"):
        opts = wall_options(p, rid, probe, clearance=clearance)
        if not opts:
            continue
        calm = [o for o in opts if not _near_combustible(state, rid, o[0], cells=fabric)]
        under = [o for o in calm if o[0] in wins]
        cell, rot = under[0] if under else rng.choice(calm or opts)
        return list(cell), rot
    return wall_cell(p, rid, rng, record=probe)


def sync_period(config, tid):
    """Apply the schedule period active at the clock start (so the first tick does not jump)."""
    import datetime
    rec = config["entities"][tid]
    s = rec.get("settings") or {}
    if not rec.get("schedule_enabled", True) or s.get("hold", "none") != "none":
        return
    start = datetime.datetime.fromisoformat((config.get("clock") or {}).get("start", "2026-10-09T07:00:00"))
    key = hvac.current_period(s, start)
    if key and isinstance(s.get(key), dict):
        rec["active_period"] = key
        s["heat_setpoint"] = s[key].get("heat", s.get("heat_setpoint"))
        s["cool_setpoint"] = s[key].get("cool", s.get("cool_setpoint"))


def _filter_props(nominal, merv, dirt=0.0, wrapped=True):
    return {"filter_spec": {"nominal": nominal, "merv": merv, "arrow": "toward_blower"}, "size_m": airclean.actual_size_m(nominal),
            "label": "{} air filter (MERV {})".format(nominal, merv), "shrink_wrapped": wrapped, "openable": wrapped, "dirtiness": dirt,
            "label_text": "{} MERV {} - install with the AIRFLOW arrow pointing toward the furnace".format(nominal, merv)}


def remote_in_holder(p, rid, **props):
    """A remote control in a small wall holder at 1.2 m: the holder is a real wall mount (later furniture neither overlaps
    nor hides it; layout audit: remotes used to be unmounted items on a wall cell, under beds and on the robot dock) and the
    remote a portable item in it. Returns the remote's id, or None when the room has no wall spot."""
    holder = p.mount(rid, "generic_item", height=1.2, label="remote holder", category="remote_holder", size_m=[0.07, 0.04, 0.2],
                     mass_kg=0.05, material="plastic", portable=False, mounted=True, blocks_path=False, container=True,
                     accepts_items=True, capacity_l=0.3, inner_size_m=[0.06, 0.03, 0.18])
    if not holder:
        return None
    remote = p.make("generic_item", holder, category="remote", battery_powered=True, battery_level=1.0, portable=True, **props)
    p.c["entities"][remote]["relation"] = "in"
    return remote


def _mini_splits(config, p, rng, conditioned, units, t_out, mode=None):
    rooms = config["rooms"]
    made = []
    for rid in conditioned:
        if rooms[rid]["type"] not in ("living_room", "master_bedroom", "bedroom", "office", "dining_room", "kitchen"):
            continue
        if rooms[rid]["type"] == "kitchen" and any(rooms[r]["type"] == "living_room" for r in conditioned):
            continue
        m_ = mode or ("heat" if t_out < 18 else ("cool" if t_out > 24 else "off"))
        sp = 21.0 if m_ == "heat" else 26.0
        uid = wall_mount(p, rid, rng, "mini_split", 2.1,
                         settings={"mode": m_, "setpoint": round(C.from_c(sp, units)) if units == "F" else sp, "fan": "auto", "units": units},
                         setting_options={"mode": ["off", "cool", "heat", "dry", "fan_only", "auto"],
                                          "setpoint": {"min": 61, "max": 86, "step": 1} if units == "F" else {"min": 16, "max": 30, "step": 0.5},
                                          "fan": ["auto", "low", "medium", "high"], "units": ["C", "F"]},
                         label="{} air conditioner".format(rooms[rid]["label"].lower()))
        config["entities"][uid]["remote"] = remote_in_holder(p, rid, label="AC remote", remote_kind="ir", controls_device=uid,
                                                             size_m=[0.05, 0.02, 0.17], mass_kg=0.09)
        made.append(uid)
    return made


def _initial_temps(config, tid, t_out):
    rooms = config["rooms"]
    target = 21.0
    if tid:
        rec = config["entities"][tid]
        s = rec.get("settings") or {}
        heat_c, cool_c = _setpoint_c(s)
        mode = s.get("mode")
        if mode in ("heat", "emergency_heat"):
            target = max(heat_c, min(t_out + 2.0, 24.0))
        elif mode == "cool":
            target = min(cool_c, max(t_out, heat_c))
        elif mode == "auto":
            target = C.clamp(t_out + 2.0, heat_c, cool_c)
        else:
            target = C.clamp(t_out + 2.0, 16.0, 26.0)
    for rid in sorted(rooms):
        room = rooms[rid]
        if room.get("outdoor"):
            continue
        rtype = room.get("type")
        if rtype == "garage":
            t = t_out + 0.35 * (target - t_out)
        elif rtype == "attic":
            t = t_out + 3.0
        elif rtype == "shed":
            t = t_out + 1.0
        elif rtype == "basement":
            t = 0.6 * 15.0 + 0.4 * target
        else:
            t = target
        room["temperature_c"] = round(t, 2)


# ---------------------------------------------------------------------------------------------
# fans and air devices

def _surface_in(p, rid, kinds):
    for e in sorted(p.c["entities"]):
        rec = p.c["entities"][e]
        if rec.get("location") == rid and rec.get("type") in kinds:
            return e
    return None


@furnisher("climate.fans", order=46)
def furnish_fans(config, rng, opts, registry):
    p = Placer(config, registry, rng)
    rooms = config["rooms"]
    region = opts.get("region") or (config.get("metadata") or {}).get("region") or "us"
    ceiling_p = {"living_room": 0.5, "master_bedroom": 0.55, "bedroom": 0.35, "office": 0.25, "guest_room": 0.3}
    if region not in ("us", "ca"):
        ceiling_p = {k: v * 0.4 for k, v in ceiling_p.items()}
    for rid in sorted(rooms):
        room = rooms[rid]
        if room.get("outdoor") or room.get("shared"):
            continue
        if rng.random() >= ceiling_p.get(room["type"], 0.0):
            continue
        cols, rows = geometry.grid_size(room)
        ceiling = C.num(room.get("ceiling_m"), 2.5) or 2.5
        remote = rng.random() < 0.45
        fid = p.mount(rid, "remote_ceiling_fan" if remote else "ceiling_fan", height=round(max(2.1, ceiling - 0.35), 2),
                      cell=[max(0, cols // 2 - 1), max(0, rows // 2 - 1)], has_light=rng.random() < 0.7,
                      label="{} ceiling fan".format(room["label"].lower()))
        if not remote:
            from .fans import add_chains
            add_chains(config, registry, fid)
        if remote:
            surf = _surface_in(p, rid, ("nightstand", "side_table", "coffee_table", "desk", "dresser"))
            rid_remote = p.on(surf, "generic_item", label="ceiling fan remote", category="remote", remote_kind="rf", controls_device=fid,
                              battery_powered=True, battery_level=1.0, size_m=[0.05, 0.02, 0.15], mass_kg=0.08) if surf else None
            if rid_remote is None:
                rid_remote = remote_in_holder(p, rid, label="ceiling fan remote", remote_kind="rf", controls_device=fid,
                                              size_m=[0.05, 0.02, 0.15], mass_kg=0.08)
            config["entities"][fid]["remote"] = rid_remote
    if rng.random() < 0.25:
        options = [r for r in sorted(rooms) if rooms[r]["type"] in ("bedroom", "office", "master_bedroom", "living_room")]
        if options:
            rid = rng.choice(options)
            # a loose floor item placed by the kernel rule (keep_access: off doorway clearance, cuts no route); the
            # cord is routed by electrical when it plugs the fan in. Generation 2: the fan stands by a wall outlet of
            # the room (where people put one: a short cord, so a later robot-vacuum dock or desk lamp cord does not
            # combine with a cord run across the room into a barrier of trip hazards)
            near = _outlet_cell(config, rid) if int(opts.get("gen") or 1) >= 2 else None
            p.floor_item(rid, rng.choice(["box_fan", "pedestal_fan", "tower_fan"]), near=near,
                         settings={"speed": "low", "oscillate": False, "aim_deg": float(rng.choice([0, 90, 180, 270]))})


def _outlet_cell(config, rid):
    """The cell of the room's first wall outlet (sorted ids), or None."""
    for eid in sorted(config["entities"]):
        rec = config["entities"][eid]
        if rec.get("location") == rid and rec.get("category") == "outlet" and rec.get("pos"):
            return tuple(rec["pos"])
    return None


ACCESS_KINDS = ("register", "return_grille", "radiator", "thermostat")


@furnisher("climate.access", order=99)
def furnish_access(config, rng, opts, registry):
    """Late pass: furniture placed after the HVAC furnisher (washers, dryers, kitchen appliances, wardrobes) can wall
    off a register, return grille, radiator or thermostat. Such a device is moved to a reachable free wall cell of
    its room (radiators away from combustibles when possible), so vent checks, TRV changes and thermostat tasks can
    reach every device the house has."""
    fit_wall_items(config)
    resite_inaccessible(config, Placer(config, registry, rng), rng)


WALL_KINDS = ("register", "return_grille", "radiator", "thermostat", "mini_split")


def fit_wall_items(config):
    """A wide wall device (1 m radiator, 0.6 m return grille, mini-split head) on a side wall runs along that wall
    (rot 90) and its footprint stays inside the room (anchor shifted back from the far corner). Returns moved ids."""
    moved = []
    for eid in sorted(config["entities"]):
        rec = config["entities"][eid]
        rid = rec.get("location")
        if rec.get("climate_kind") not in WALL_KINDS or rid not in config["rooms"] or not rec.get("pos"):
            continue
        room = config["rooms"][rid]
        if all(geometry.in_grid(room, c) for c in geometry.footprint(rec)):
            continue
        cols, rows = geometry.grid_size(room)
        x, y = int(rec["pos"][0]), int(rec["pos"][1])
        side = x in (0, cols - 1) and y not in (0, rows - 1)
        # cells later furnishers filled (appliances, furniture, floor-standing mounts, other wall devices): the re-fitted
        # run must not land on them (round-2 repro: seed 1 apartment, a kitchen radiator turned under the range)
        taken = set()
        for oid in sorted(config["entities"]):
            o = config["entities"][oid]
            if oid == eid or o.get("location") != rid or not o.get("pos"):
                continue
            if geometry.floor_standing(o) or o.get("climate_kind") in WALL_DEVICE_KINDS:
                taken.update(geometry.footprint(o))
        candidates = []
        for rot in ((90, 0) if side else (0, 90)):
            fw, fd = geometry.footprint_size(dict(rec, rot=rot))
            if fw > cols or fd > rows:
                continue
            base = [max(0, min(x, cols - fw)), max(0, min(y, rows - fd))]
            candidates.append((rot, base))
            # the same wall, shifted back along it
            along = [[base[0] - k, base[1]] if rot == 0 else [base[0], base[1] - k] for k in range(1, 4)]
            candidates.extend((rot, pos) for pos in along if pos[0] >= 0 and pos[1] >= 0)
        if not candidates:
            continue
        chosen = next((c for c in candidates if not any(k in taken or not geometry.in_grid(room, k)
                                                        for k in geometry.footprint(dict(rec, rot=c[0], pos=c[1])))), candidates[0])
        rec["rot"], rec["pos"] = chosen[0], list(chosen[1])
        moved.append(eid)
    return moved


def resite_inaccessible(config, p, rng):
    """Move every register / grille / radiator / thermostat the robot cannot get to (kernel access_report) to a
    reachable wall cell of its room (radiators away from combustibles when possible). Returns the moved ids."""
    moved = []
    state = p.state()
    by_room = {}
    for eid in sorted(config["entities"]):
        rec = config["entities"][eid]
        if rec.get("climate_kind") in ACCESS_KINDS and rec.get("location") in config["rooms"] and rec.get("pos"):
            by_room.setdefault(rec["location"], []).append(eid)
    for rid in sorted(by_room):
        bad = inaccessible(state, rid)
        if not bad:
            continue
        for eid in by_room[rid]:
            if eid not in bad:
                continue
            rec = config["entities"][eid]
            spot = None
            if rec.get("climate_kind") == "radiator":
                spot = reachable_wall_spot(p, rid, rng, record=rec, exclude=geometry.footprint(rec), avoid_combustibles=True)
            spot = spot or reachable_wall_spot(p, rid, rng, record=rec, exclude=geometry.footprint(rec))
            if spot is not None:
                rec["pos"], rec["rot"] = spot
                moved.append(eid)
    return moved


@furnisher("climate.air", order=56)
def furnish_air(config, rng, opts, registry):
    p = Placer(config, registry, rng)
    rooms = config["rooms"]
    basements = [r for r in sorted(rooms) if rooms[r]["type"] == "basement"]
    for rid in basements:
        if rng.random() < 0.6:
            did = p.floor_item(rid, "dehumidifier", settings={"target_rh": 50, "fan": "high", "drain": "bucket"})
            if did:
                bid = p.make("dehumidifier_bucket", did, liquid_l=round(rng.uniform(0.0, 4.0), 2))
                config["entities"][bid]["relation"] = "part"
                config["entities"][bid]["slot"] = "bucket"
                config["entities"][did]["slots"]["bucket"]["item"] = bid
                rooms[rid]["humidity"] = round(rng.uniform(0.5, 0.62), 3)
    bedrooms = [r for r in sorted(rooms) if rooms[r]["type"] in ("master_bedroom", "bedroom", "nursery")]
    if bedrooms and rng.random() < 0.3:
        rid = bedrooms[0]
        p.floor_item(rid, "air_purifier")
        pid = next((e for e in sorted(config["entities"]) if config["entities"][e].get("climate_kind") == "air_purifier"
                    and config["entities"][e].get("location") == rid), None)
        if pid:
            f = p.make("purifier_filter", pid, shrink_wrapped=False, openable=False, dirtiness=round(rng.uniform(0.1, 0.7), 2))
            config["entities"][f]["relation"] = "part"
            config["entities"][f]["slot"] = "cartridge"
            config["entities"][pid]["slots"]["cartridge"]["item"] = f
    if bedrooms and rng.random() < 0.15:
        rid = rng.choice(bedrooms)
        surf = _surface_in(p, rid, ("dresser", "nightstand"))
        hid = p.on(surf, rng.choice(["ultrasonic_humidifier", "evaporative_humidifier"])) if surf else None
        if hid:
            tank = p.make("humidifier_tank", hid, liquid_l=round(rng.uniform(0.0, 1.5), 2), water_age_h=round(rng.uniform(0, 96), 1))
            config["entities"][tank]["relation"] = "part"
            config["entities"][tank]["slot"] = "tank"
            config["entities"][hid]["slots"]["tank"]["item"] = tank
    if bedrooms and rng.random() < 0.2:
        rid = rng.choice(bedrooms)
        surf = _surface_in(p, rid, ("dresser", "nightstand", "desk"))
        if surf:
            p.on(surf, "hygrometer")
    if rng.random() < 0.08:
        options = [r for r in sorted(rooms) if rooms[r]["type"] in ("office", "bedroom")]
        if options:
            p.floor_item(rng.choice(options), rng.choice(["ceramic_heater", "oil_filled_heater"]))
