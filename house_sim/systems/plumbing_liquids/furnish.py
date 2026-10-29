"""Furnishers: plumbing fixtures (order 25), appliance water links (65) and small water items (75).

Every kitchen gets a sink on a base cabinet with hot/cold stops and a drain trap underneath (sometimes a disposal,
installed as a part of the sink base that hangs from the sink); every bathroom a vanity sink, a toilet with a supply
stop and a tub (or a shower stall); half baths a sink and a toilet; laundry rooms washer valves and often a utility
sink. The house gets a main shutoff (+ meter) in the basement, utility closet, garage, laundry room or kitchen - in
that order of preference - and a water heater with its inlet stop: a tank in a service room (never a kitchen, nor a
laundry closet/pantry under 6 m2 or one it would leave without a washer spot), else a wall-hung tankless unit. Yards get spigots (frost-free or standard with an interior shutoff), a hose with a nozzle on a hanger,
sometimes a rain barrel, sprinklers and a controller; driveways a storm drain. Placer returns None when a room is
full: every placement here tolerates that.

Layout (kernel v2.3): blocking fixtures go through `furniture_keeping_reach` (Placer.furniture, which keeps access
not worse, plus: everything the robot could reach in the room stays reachable, the fixture itself is reachable, and
it never stands under a wall mount hanging lower than its top - upper cabinets, counter drawers, wall shelves,
window sills, registers); wall controls through `mount_clear` (Placer.mount, then off the wall behind anything
floor-standing taller than the control); loose floor items through `place_floor_item` (Placer.floor_item on a cell
from geometry.free_floor_cell with keep_access and strict door clearance, never in front of a lower wall mount,
else the next suitable room). The late `reach` pass (order 91) repairs what later furnishers did to this domain's
things (controls boxed in or hidden behind appliances, floor drains under furniture, floor items that now cut a
route). Door clearance is the kernel's `geometry.door_clearance` (doorway cells + neighbours, leaves, overhead
thresholds).
"""
from ...generator.place import Placer
from ...kernel import geometry
from ...kernel.plugins import furnisher
from . import common as C

SERVICE_ROOMS = ("basement", "utility_closet", "garage", "laundry_room", "kitchen")
# wall-hung tankless heater (supply.py: hot water at the setpoint on demand); 0.5 L is its heat exchanger
TANKLESS = {"label": "tankless water heater", "tankless": True, "tank_l": 0.5, "recovery_kw": 44.0, "size_m": [0.4, 0.25, 0.7],
            "mass_kg": 20.0, "blocks_path": False}
NO_SHADOW = ("light", "switch", "outlet", "hooks")       # wall things a piece may stand in front of


def _view(config):
    return {"rooms": config["rooms"], "entities": config["entities"], "connections": config.get("connections", {}),
            "people": config.get("people", {}), "pets": config.get("pets", {}), "robot": {"room": None, "hands": {}, "pos": None}}


def _is(rec, type_name):
    return rec.get("type") == type_name or type_name in (rec.get("is_a") or [])


def _root(state, eid):
    """The entity in the chain eid -> parents that stands directly in a room."""
    ents, cur, seen = state["entities"], eid, set()
    while cur in ents and cur not in seen:
        seen.add(cur)
        if ents[cur].get("location") in state["rooms"]:
            return ents[cur]
        cur = ents[cur].get("location")
    return ents.get(eid) or {}


def door_sweep_cells(config, rid):
    """The kernel's door clearance of a room (geometry.door_clearance): doorway cells and their four neighbours,
    hinged leaves that swing into the room, overhead-door thresholds and the row inside them."""
    return geometry.door_clearance(_view(config), rid)


def reachable(p, eid):
    """True when the robot could walk from a doorway of the entity's room to within arm's reach of it (the kernel's
    reach rule: one cell, two for things mounted at or above counter height; people and pets ignored, they move)."""
    state = p.state()
    room, cells = geometry.anchor(state, eid)
    if room is None or not cells:
        return room is not None
    if not geometry.doorway_cells(state, room):
        return True
    radius = geometry.access_radius(_root(state, eid))
    goals = geometry.near_cells(cells, p.c["rooms"][room], radius) - set(cells)
    return bool(goals & geometry.reachable_cells(state, room))


def _top(rec):
    size = rec.get("size_m") or [0.5, 0.5, 0.5]
    return C.num(rec.get("mount_height_m"), 0.0) + C.num(size[2] if len(size) > 2 else 0.5, 0.5)


def _wall_mount(rec):
    return bool(rec.get("mounted")) and bool(rec.get("pos")) and "connects" not in rec and not geometry.floor_standing(rec) \
        and rec.get("category") not in NO_SHADOW


def air_cells(p, rid, top, exempt=()):
    """Floor cells under wall mounts that hang lower than `top` m and stick out at least 0.1 m from the wall (upper
    cabinets, counter drawers, wall shelves, window sills, registers, a fire blanket): a floor-standing piece that tall
    cannot stand there."""
    state = p.state()
    cells = set()
    for eid in geometry.room_entities(state, rid):
        rec = p.c["entities"][eid]
        if eid in exempt or not _wall_mount(rec):
            continue
        size = rec.get("size_m") or [0.1, 0.1, 0.1]
        if min(C.num(size[0]), C.num(size[1])) >= 0.1 and C.num(rec.get("mount_height_m"), 0.0) < top - 0.02:
            cells.update(geometry.footprint(rec))
    return cells


HIDES_NOT = ("outlet", "light", "window", "drawer")     # wall things the layout audit lets a piece stand in front of


def hidden_cells(p, rid, top, exempt=()):
    """Floor cells in front of wall mounts that a floor-standing piece `top` m tall would hide (the layout audit's
    "hidden mount", generator/repair.py: a mount 0.5-2 m up, not an outlet, light, window or drawer front, whose
    mounting height is below the piece's top): a shower stall in front of a window blind or an upper cabinet."""
    state = p.state()
    cells = set()
    for eid in geometry.room_entities(state, rid):
        rec = p.c["entities"][eid]
        if eid in exempt or not rec.get("pos") or not rec.get("mounted") or rec.get("ground_layer") or "connects" in rec:
            continue
        if geometry.floor_standing(rec) or rec.get("category") in HIDES_NOT:
            continue
        height = C.num(rec.get("mount_height_m", rec.get("z_m", 0.0)), 0.0)
        if 0.5 <= height < 2.0 and top > height + 0.05:
            cells.update(geometry.footprint(rec))
    return cells


def room_reach(p, rid, extra_blocked=()):
    """(entities in the room the robot can get within arm's reach of, doorway-reachable floor cells). Overhead
    wall mounts (>= 0.85 m) count from one extra cell away, like geometry.reach_problem."""
    state = p.state()
    room = p.c["rooms"][rid]
    blocked = set(geometry.blocked_cells(state, rid, include_agents=False)) | set(extra_blocked)
    reach = geometry.reachable_cells(state, rid, blocked)
    out = set()
    for eid in geometry.room_entities(state, rid):
        rec = p.c["entities"][eid]
        if not rec.get("pos") or "connects" in rec:
            continue
        cells = geometry.footprint(rec)
        if geometry.near_cells(cells, room, geometry.access_radius(rec)) & reach:
            out.add(eid)
    return out, reach


def furniture_keeping_reach(p, rid, type_name, wall=True, eid=None, required=False, **props):
    """Placer.furniture (access not worse) that also keeps everything the robot could reach in the room reachable
    (wall outlets, registers, closet shelving, a drawer, loose items), the new piece itself reachable, never stands
    the piece under a wall mount hanging lower than its top and never in front of a wall mount it would hide
    (hidden_cells: a shower stall before a window blind or an upper cabinet). The first try is the generator's own
    choice (same randomness); only when it fails are the other spots of the room tried (wall spots first unless
    wall=False). Returns None when no spot works (with required=True: the generator's plain choice then, so every
    bathroom still gets its toilet)."""
    if rid not in p.c["rooms"]:
        return None
    before, _ = room_reach(p, rid)
    new = p.furniture(rid, type_name, eid=eid, wall=wall, **props)
    if new is None:
        return None
    rec = p.c["entities"][new]
    air = air_cells(p, rid, _top(rec))
    # judged on each try, never used to narrow the spots below: the shuffle draws the same randomness as before, so a
    # house where no piece would hide a mount comes out byte-identical (generation-1 digests)
    hidden = hidden_cells(p, rid, _top(rec), exempt=(new,))

    def good():
        after, _ = room_reach(p, rid)
        cells = set(geometry.footprint(p.c["entities"][new]))
        return before <= after and new in after and not (cells & air) and not (cells & hidden)
    if good():
        return new
    rec = p.c["entities"].pop(new)
    size = rec.get("size_m") or [1, 1, 1]
    rots = [rec.get("rot", 0)]
    if rec.get("rot", 0) in (0, 90) and size[0] != size[1]:
        rots.append(90 - rec.get("rot", 0))
    gc, gr = geometry.grid_size(p.c["rooms"][rid])
    occupied = p._occupied(rid) | air
    options = []
    for rot in rots:
        rec["rot"] = rot
        cols, rows = geometry.footprint_size(rec)
        for y in range(0, gr - rows + 1):
            for x in range(0, gc - cols + 1):
                cells = [(x + i, y + j) for i in range(cols) for j in range(rows)]
                if any(c in occupied for c in cells):
                    continue
                if cols > rows:
                    touches = y == 0 or y + rows == gr
                elif rows > cols:
                    touches = x == 0 or x + cols == gc
                else:
                    touches = x == 0 or y == 0 or x + cols == gc or y + rows == gr
                options.append((0 if (touches or not wall) else 1, rot, x, y, cols, rows))
    p.rng.shuffle(options)
    options.sort(key=lambda o: o[0])
    for _, rot, x, y, cols, rows in options:
        if not p._keeps_connected(rid, (x, y), cols, rows):
            continue
        rec["rot"], rec["pos"] = rot, [x, y]
        p.c["entities"][new] = rec
        if good():
            return new
        del p.c["entities"][new]
    if required:
        return p.furniture(rid, type_name, eid=eid, wall=wall, **props)
    return None


# ----------------------------------------------------------------------------------------------------------------
# wall controls

def _wall_options(p, rid):
    cols, rows = geometry.grid_size(p.c["rooms"][rid])
    return sorted({(x, 0) for x in range(cols)} | {(x, rows - 1) for x in range(cols)} | {(0, y) for y in range(rows)}
                  | {(cols - 1, y) for y in range(rows)})


def hidden_behind(p, eid, exempt=(), loose=False):
    """What stops a hand reaching a wall control: floor-standing pieces (not loose items) standing in front of it and
    rising above it (a meter behind a dryer, a valve inside closet shelving or behind a water heater), and other wall
    mounts on the same wall stretch at overlapping heights (a boiler hung over the tankless heater, an outlet where the
    main valve is). The fixtures a valve feeds are exempt (stops under a utility sink are reached below the basin)."""
    rec = p.c["entities"].get(eid) or {}
    rid = rec.get("location")
    if rid not in p.c["rooms"] or not rec.get("pos"):
        return []
    height = C.num(rec.get("mount_height_m"), 0.0)
    top = _top(rec)
    cells = set(geometry.footprint(rec))
    skip = set(exempt) | set(rec.get("feeds") or []) | {eid}
    out = []
    for other in geometry.room_entities(p.state(), rid):
        orec = p.c["entities"][other]
        if other in skip or not orec.get("pos") or "connects" in orec or orec.get("ground_layer"):
            continue
        if not set(geometry.footprint(orec)) & cells:
            continue
        low = C.num(orec.get("mount_height_m"), 0.0)
        if geometry.floor_standing(orec):
            if orec.get("portable") and not geometry.blocks(orec) and not loose:
                continue        # a loose item can be moved aside (loose=True: it still covers the mount, e.g. a hose hanger)
            if _top(orec) > height + 0.02 and low <= height + 0.02:
                out.append(other)
        elif orec.get("mounted") and orec.get("category") != "light" and low < top - 0.01 and _top(orec) > height + 0.01:
            if rec.get("valve_kind") and orec.get("valve_kind") and not rec.get("main") and not orec.get("main"):
                continue        # the two valves of a washer box sit side by side and do not cover each other
            out.append(other)
    return out


def relocate_reachable(p, eid, exempt=(), extra=None):
    """Keep a wall-mounted control usable: when the robot cannot get within arm's reach of it (later furniture boxed it
    in) or a floor-standing piece taller than it now stands in front of it, move it to the nearest cell on the same
    room's walls that is reachable, clear of such pieces and (for low mounts) outside door clearance. Deterministic,
    no randomness. Returns True when it ends up usable."""
    rec = p.c["entities"].get(eid)
    if rec is None:
        return False
    rid = rec.get("location")
    if rid not in p.c["rooms"] or not rec.get("pos"):
        return True
    if not rec.get("mounted"):
        return reachable(p, eid)

    def usable():
        return reachable(p, eid) and not hidden_behind(p, eid, exempt) and (extra is None or extra())
    if usable():
        return True
    room = p.c["rooms"][rid]
    here = (int(rec["pos"][0]), int(rec["pos"][1]))
    state = p.state()
    clearance = geometry.door_clearance(state, rid) if C.num(rec.get("mount_height_m"), 0.0) < 0.5 else set()
    doors = set(geometry.doorway_cells(state, rid))
    options = [c for c in _wall_options(p, rid) if c not in doors and c not in clearance]
    options.sort(key=lambda c: (geometry.chebyshev(c, here), c[1], c[0]))
    for cell in options:
        rec["pos"] = [int(cell[0]), int(cell[1])]
        if not all(geometry.in_grid(room, c) for c in geometry.footprint(rec)):
            continue
        if usable():
            return True
    rec["pos"] = [here[0], here[1]]
    if extra is not None:
        extra()
    return reachable(p, eid)


def mount_clear(p, rid, type_name, exempt=(), **props):
    """Placer.mount, then off any wall stretch a floor-standing piece taller than the control already covers (Placer
    only keeps low mounts off furniture): valves, the meter and controllers stay where a hand can get to them."""
    if rid not in p.c["rooms"]:
        return None
    eid = p.mount(rid, type_name, **props)
    if eid is not None and props.get("cell") is None and (hidden_behind(p, eid, exempt) or not reachable(p, eid)):
        relocate_reachable(p, eid, exempt)
    return eid


def lay_pipe(p, eid, exempt=()):
    """An exposed supply run follows a wall: lay the pipe along the wall of its cell (geometry.along_wall: rot 90 on
    the west/east walls) and, where that wall is shorter than the run (a 1.5 m utility closet), cut the run to the
    wall's length (mass in proportion), so its whole footprint stays on the grid. It stays on its cell when it is
    usable there (reachable, not hidden behind anything taller), else the nearest wall cell where it is. Placer.mount
    keeps the type's orientation, which ran a 2 m pipe straight into the room or off a small closet's grid.
    Deterministic, no randomness. Returns True when it ends up reachable."""
    rec = p.c["entities"].get(eid)
    if rec is None or not rec.get("pos") or rec.get("location") not in p.c["rooms"]:
        return False
    rid = rec["location"]
    room = p.c["rooms"][rid]
    here = (int(rec["pos"][0]), int(rec["pos"][1]))
    size = [C.num(v) for v in (rec.get("size_m") or [2.0, 0.05, 0.05])]
    full = max(size[0], size[1])
    thin = min(size[0], size[1])
    per_m = C.num(rec.get("mass_kg"), 0.0) / full if full > 0 else 0.0
    walls = (room.get("size_m") or [4.0, 4.0])[:2]

    def lay(cell):
        rec["pos"] = [int(cell[0]), int(cell[1])]
        inward, _ = geometry.wall_inward(room, cell)
        wall_m = C.num(walls[1] if inward[0] != 0 else walls[0], full)
        length = round(min(full, wall_m), 3)
        rec["size_m"] = [length, thin] + size[2:]
        if per_m > 0:
            rec["mass_kg"] = round(per_m * length, 3)
        rec.pop("rot", None)
        geometry.along_wall(room, rec)
        return all(geometry.in_grid(room, c) for c in geometry.footprint(rec))

    options = sorted(set(_wall_options(p, rid)) - {here}, key=lambda c: (geometry.chebyshev(c, here), c[1], c[0]))
    for cell in [here] + options:
        if lay(cell) and reachable(p, eid) and not hidden_behind(p, eid, exempt):
            return True
    lay(here)
    return reachable(p, eid)


# ----------------------------------------------------------------------------------------------------------------
# loose floor items

def _shadow_view(p, rid, record, avoid=()):
    """State view in which wall mounts lower than the item (registers, valves, the meter, a low shelf) take their
    floor cell, so free_floor_cell does not stand a bucket or a plunger in front of them; `avoid` cells (e.g. in front
    of a cabinet the plan opens) are taken by flat markers that must stay accessible."""
    top = _top(record)
    changed = {}
    for eid in geometry.room_entities(p.state(), rid):
        rec = p.c["entities"][eid]
        if _wall_mount(rec) and C.num(rec.get("mount_height_m"), 0.0) < top:
            changed[eid] = dict(rec, mounted=False, blocks_path=False, portable=False)
    for i, cell in enumerate(sorted(avoid)):
        changed["_keep_clear_{}".format(i)] = {"location": rid, "pos": [int(cell[0]), int(cell[1])], "mounted": True, "mount_height_m": 0.0,
                                               "size_m": [geometry.CELL, geometry.CELL, 0.06], "blocks_path": False, "portable": False}
    state = p.state()
    if changed:
        state["entities"] = dict(state["entities"])
        state["entities"].update(changed)
    return state


def floor_cell(p, rid, record, near=None, ignore=(), avoid=()):
    """Cell for a loose floor item at generation/setup time: geometry.free_floor_cell with keep_access (no
    hazard-free route or only access is cut, the item itself is reachable without stepping on anything) and strict
    door clearance, not in front of a lower wall mount nor on `avoid` cells. None when the room has no such cell."""
    if rid not in p.c["rooms"]:
        return None
    rec = dict(record, location=rid)
    rec.pop("pos", None)
    room = p.c["rooms"][rid]
    avoid = [c for c in avoid if geometry.in_grid(room, c)]
    return geometry.free_floor_cell(_shadow_view(p, rid, rec, avoid), rid, near=near, ignore=ignore, record=rec, strict=True, keep_access=True)


def room_clean(p, rid, ignore=()):
    """True when every doorway pair and every target the robot can walk to in the room is also reachable without
    stepping on any floor hazard (loose items, cords, dock zones...): the condition under which the final layout repair
    leaves a room's loose items alone. Generation puts this domain's loose items only into rooms that pass it (an
    already cluttered shed sends the watering can to the patio instead of being the item repair cannot re-place)."""
    state = p.state()
    if ignore:
        state["entities"] = {e: r for e, r in state["entities"].items() if e not in ignore}
    static_blocked = set(geometry.blocked_cells(state, rid, include_agents=False))
    targets = geometry.access_targets(state, rid, level="all")
    static = geometry.access_report(state, rid, static_blocked, targets)
    hazards = {c for c, items in geometry.placement_hazards(state, rid).items() if any(s not in ignore for _, s in items)}
    return geometry.not_worse(static, geometry.access_report(state, rid, static_blocked | hazards, targets))


def place_floor_item(p, rooms, type_name, near=None, clean=False, **props):
    """A loose floor item in the first of `rooms` with a floor_cell (near applies to the first room); placed through
    Placer.floor_item on that cell. clean=True (generation) skips rooms that fail room_clean. None when no room
    qualifies."""
    for i, rid in enumerate(rooms):
        if rid not in p.c["rooms"] or (clean and not room_clean(p, rid)):
            continue
        cell = floor_cell(p, rid, p.reg.make(type_name, rid, **props), near=near if i == 0 else None)
        if cell is not None:
            return p.floor_item(rid, type_name, cell=cell, **props)
    return None


def _remove_tree(p, eid):
    ents = p.c["entities"]
    doomed, frontier = [eid], [eid]
    while frontier:
        cur = frontier.pop()
        kids = [e for e in sorted(ents) if ents[e].get("location") == cur]
        doomed.extend(kids)
        frontier.extend(kids)
    for e in doomed:
        ents.pop(e, None)


REACH_TYPES = ("main_shutoff_valve", "shutoff_valve", "water_meter", "irrigation_controller", "water_pipe", "water_heater")
FLOOR_TYPES = ("utility_bucket", "watering_can", "flange_plunger", "cup_plunger")
DRAIN_TYPES = ("floor_drain",)
# where a floor item may live when its own room has no floor_cell
ITEM_HOMES = {"utility_bucket": ("laundry_room", "utility_closet", "garage", "basement", "mudroom"),
              "watering_can": ("garage", "shed", "porch", "patio", "backyard", "mudroom")}


def _vanity_of(p, rid):
    for eid in p.find("cabinet", rid):
        rec = p.c["entities"][eid]
        if rec.get("location") == rid and rec.get("sink_base") and rec.get("container", True):
            return eid
    return None


def _rehome(p, eid):
    """A floor item with no floor_cell left in its room: plungers go under the bathroom vanity (where people keep
    them), buckets and watering cans to another utility/outdoor-storage room; otherwise the item is left out."""
    ents = p.c["entities"]
    rec = ents[eid]
    rid = rec["location"]
    kind = rec.get("type")
    if kind in ("flange_plunger", "cup_plunger"):
        cab = _vanity_of(p, rid)
        if cab is not None:
            parent = ents[cab]
            used = sum(C.num(e.get("volume_l")) for e in ents.values() if e.get("location") == cab and e.get("relation") == "in")
            cap = C.num(parent.get("capacity_l"), 0.0)
            if geometry.fits_inside(rec.get("size_m") or [0.15, 0.15, 0.5], parent.get("inner_size_m")) and \
                    (cap <= 0 or used + C.num(rec.get("volume_l")) <= cap + 1e-9):
                rec.update(location=cab, relation="in")
                rec.pop("pos", None)
                if parent.get("shelf_heights"):
                    rec["shelf"] = 0
                return True
    for kind_name in ITEM_HOMES.get(kind, ()):
        for other in p.rooms(kind_name):
            if other == rid or not room_clean(p, other, ignore=(eid,)):
                continue
            cell = floor_cell(p, other, rec, ignore=(eid,))
            if cell is not None:
                rec["location"], rec["pos"] = other, cell
                return True
    _remove_tree(p, eid)
    return False


def _drain_usable(p, eid):
    """A floor drain is usable when no furniture, appliance or floor-standing fixture covers it (a loose bucket can be
    moved aside) and the robot can get next to it."""
    rec = p.c["entities"][eid]
    rid = rec["location"]
    state = p.state()
    covered = set()
    for other in geometry.room_entities(state, rid):
        orec = p.c["entities"][other]
        if other == eid or not orec.get("pos") or (orec.get("portable") and not geometry.blocks(orec)):
            continue
        if geometry.floor_standing(orec) or geometry.blocks(orec):
            covered.update(geometry.footprint(orec))
    if any(c in covered for c in geometry.footprint(rec)):
        return False
    return reachable(p, eid)


def _relocate_drain(p, eid):
    rec = p.c["entities"][eid]
    if _drain_usable(p, eid):
        return True
    cell = geometry.free_floor_cell(p.state(), rec["location"], near=rec.get("pos"), ignore=(eid,), record=rec, clearance=False)
    if cell is not None:
        rec["pos"] = cell
    return _drain_usable(p, eid)


def _keep_floor_item(p, eid):
    """A floor item placed earlier: it stays (or moves to the nearest floor_cell of its room) while the room is clean
    without it; when later furniture made its cell cut a route with no other cell left, or later loose items cluttered
    the room (room_clean fails), it is _rehome'd."""
    rec = p.c["entities"][eid]
    rid, here = rec["location"], list(rec["pos"])
    if room_clean(p, rid, ignore=(eid,)):
        cell = floor_cell(p, rid, rec, near=tuple(here), ignore=(eid,))
        if cell is not None:
            rec["pos"] = cell
            return True
    return _rehome(p, eid)


@furnisher("plumbing_liquids.reach", order=91)
def keep_controls_reachable(config, rng, opts, registry):
    """Runs after every furniture/small-item furnisher: wall-mounted shutoffs, meters, controllers and exposed pipes
    the robot can no longer reach, or that a later appliance/shelving now stands in front of, move along the walls
    (a spigot takes its hose hanger along); floor drains later furniture covered move to a free cell; this domain's
    floor items whose cell now cuts a route move (or are stored elsewhere when the room has no clear cell)."""
    p = Placer(config, registry, rng)
    ents = config["entities"]
    for eid in sorted(ents):
        rec = ents.get(eid)
        if rec is None or rec.get("location") not in config["rooms"] or rec.get("type") not in REACH_TYPES + FLOOR_TYPES + DRAIN_TYPES + ("outdoor_spigot",):
            continue
        if rec.get("type") == "water_pipe" and rec.get("mounted"):
            lay_pipe(p, eid)
        elif rec.get("type") in REACH_TYPES and rec.get("mounted"):
            relocate_reachable(p, eid)
        elif rec.get("type") == "outdoor_spigot" and rec.get("mounted") and rec.get("pos"):
            # yard furniture placed later (planters, downspouts, sheds, grills) can stand in front of the spigot: the
            # spigot and the hose hanger mounted with it move along the wall together
            old = list(rec["pos"])
            hangers = [h for h in sorted(ents) if ents[h].get("location") == rec["location"] and ents[h].get("hose_storage")
                       and ents[h].get("mounted") and ents[h].get("pos") == old]
            def hangers_clear(spigot=eid, hangers=tuple(hangers)):
                # the hanger moves with the spigot; anything standing in front of it (a wheelbarrow, a planter, even
                # a loose one) would hide the hose storage, so that spot is not good enough either
                ok = True
                for hid in hangers:
                    ents[hid]["pos"] = list(ents[spigot]["pos"])
                    ok = ok and not hidden_behind(p, hid, exempt=(spigot,) + hangers, loose=True)
                return ok
            relocate_reachable(p, eid, exempt=hangers, extra=hangers_clear if hangers else None)
            if rec["pos"] != old:
                for hid in hangers:
                    ents[hid]["pos"] = list(rec["pos"])
        elif rec.get("type") in DRAIN_TYPES:
            _relocate_drain(p, eid)
        elif rec.get("type") in FLOOR_TYPES and not rec.get("mounted") and rec.get("pos"):
            _keep_floor_item(p, eid)


# ----------------------------------------------------------------------------------------------------------------
# fixtures

def _floor_cabinet(p, rid, label_words):
    best = None
    for eid in p.find("cabinet", rid):
        rec = p.c["entities"][eid]
        if rec.get("location") != rid or rec.get("mounted") or rec.get("mount_height_m") or rec.get("sink"):
            continue
        if not reachable(p, eid):
            continue
        label = (rec.get("label") or "").lower()
        if any(word in label for word in label_words):
            return eid
        if best is None and rec.get("type") == "cabinet" and "sink" not in label and "filing" not in label and "sideboard" not in label:
            best = eid
    return best


def _stops(p, cab, fixture, rng, hot=True):
    out = []
    for line in (("hot", "cold") if hot else ("cold",)):
        vid = p.inside(cab, "shutoff_valve", label="{} water stop under the {}".format(line, p.c["entities"][fixture].get("label", "sink")),
                       line=line, feeds=[fixture], serves_label=p.c["entities"][fixture].get("label"), shelf=0)
        if vid and rng.random() < 0.08:
            p.c["entities"][vid]["_seized"] = True
        if vid:
            out.append(vid)
    return out


def _sink_on(p, rid, cab, type_name, rng, **props):
    """The basin is mounted at counter height over its base cabinet (same floor cells): items in the basin stay
    visible while the cabinet doors (and the stops, trap and disposal behind them) are closed."""
    cabrec = p.c["entities"][cab]
    height = float(cabrec.get("surface_height_m") or (cabrec.get("size_m") or [0, 0, 0.9])[2])
    sink = p.mount(rid, type_name, height=height, cell=list(cabrec.get("pos") or [0, 0]), cabinet=cab, rot=cabrec.get("rot", 0), **props)
    if sink is None:
        return None
    cabrec["sink"] = sink
    cabrec["sink_base"] = True
    cabrec["base_material"] = "particleboard"
    cabrec["label"] = "sink base cabinet" if type_name == "kitchen_sink" else cabrec.get("label", "vanity cabinet")
    _stops(p, cab, sink, rng)
    p.inside(cab, "drain_trap", label="drain trap under the {}".format(p.c["entities"][sink].get("label")), shelf=0)
    return sink


def add_disposal(p, cab, sink):
    """A garbage disposal hangs from the sink's drain flange inside the sink base: an installed part of the cabinet
    (slot `disposal`, reached with the doors open, hidden while they are closed) whose weight the sink carries, not
    the cabinet shelf; it sits ~0.45 m below the counter."""
    ents = p.c["entities"]
    cabrec = ents[cab]
    disp = p.make("garbage_disposal", cab, hangs_from=sink, part_type="garbage_disposal")
    top = C.num(cabrec.get("surface_height_m") or (cabrec.get("size_m") or [0, 0, 0.9])[2], 0.9)
    ents[disp].update(relation="part", slot="disposal", z_m=round(max(0.1, top - 0.6), 2))
    slots = dict(cabrec.get("slots") or {})
    slots["disposal"] = {"accepts": ["garbage_disposal"], "item": disp, "access": "open", "tool": "screwdriver"}
    cabrec["slots"] = slots
    ents[sink]["disposal"] = disp
    return disp


def _bath_faucet(p, sink, rng):
    rec = p.c["entities"][sink]
    if rng.random() < 0.45:
        rec["faucet"] = dict(rec["faucet"], kind="single_lever")
        rec.pop("_hot_handle", None)
        rec["settings"] = {"flow": 0.0, "mix": 0.0}
        rec["setting_options"] = {"flow": {"min": 0.0, "max": 1.0}, "mix": {"min": -1.0, "max": 1.0}}
        rec["control_kinds"] = {"flow": "lever", "mix": "lever"}
        rec["mech"] = {"lever": {"kind": "mixer", "max_lpm": rec["faucet"].get("max_lpm", 4.5)}}
    elif rng.random() < 0.15:
        rec["_hot_handle"] = "right"   # reversed supply lines: the handle marked hot carries cold water
    if rng.random() < 0.12:
        rec["_worn"] = {"drip_lpm": round(rng.uniform(0.006, 0.02), 4)}


def unsound_spot(p, eid):
    """True when a floor-standing piece stands under a wall mount hanging lower than its top or in front of a wall mount
    it hides (air_cells, hidden_cells): what furniture_keeping_reach's required fallback may do in a cramped room."""
    rec = p.c["entities"][eid]
    rid = rec.get("location")
    if rid not in p.c["rooms"]:
        return False
    top = _top(rec)
    return bool(set(geometry.footprint(rec)) & (air_cells(p, rid, top) | hidden_cells(p, rid, top, exempt=(eid,))))


def _ensure_bathing(p, cramped):
    """A home keeps a tub or a shower: when every bathroom that skipped one was cramped and none has one, the first
    cramped bathroom gets the generator's plain stall after all (furniture_keeping_reach's required fallback)."""
    if not cramped:
        return
    ents = p.c["entities"]
    if any(rec.get("type") in ("bathtub", "shower_stall") or _is(rec, "bathtub") or _is(rec, "shower_stall")
           for rec in ents.values()):
        return
    furniture_keeping_reach(p, cramped[0], "shower_stall", wall=True, required=True)


@furnisher("plumbing_liquids.fixtures", order=25)
def furnish_fixtures(config, rng, opts, registry):
    p = Placer(config, registry, rng)
    rooms = config["rooms"]
    cramped = []
    for rid in p.rooms("kitchen"):
        cab = _floor_cabinet(p, rid, ("sink",)) or furniture_keeping_reach(p, rid, "cabinet", required=True, label="sink base cabinet")
        if cab is None:
            continue
        sink = _sink_on(p, rid, cab, "kitchen_sink", rng)
        if sink is None:
            continue
        if rng.random() < 0.4:
            add_disposal(p, cab, sink)
        if rng.random() < 0.1:
            p.c["entities"][sink]["_worn"] = {"drip_lpm": round(rng.uniform(0.005, 0.015), 4)}
        p.inside(cab, "cup_plunger", shelf=0)
    for rid in p.rooms("bathroom") + p.rooms("half_bath"):
        cab = _floor_cabinet(p, rid, ("vanity",)) or furniture_keeping_reach(p, rid, "cabinet", required=True, label="vanity cabinet",
                                                                             size_m=[0.8, 0.5, 0.85])
        if cab is not None:
            sink = _sink_on(p, rid, cab, "bathroom_sink", rng)
            if sink:
                _bath_faucet(p, sink, rng)
                if rng.random() < 0.1:
                    p.c["entities"][sink]["_nonpotable"] = True   # old galvanized lines: residents do not drink from it
        toilet = furniture_keeping_reach(p, rid, "toilet", wall=True, required=True)
        if toilet:
            if rng.random() < 0.1:
                p.c["entities"][toilet]["_supply_seized"] = True
            # beside the toilet, never in door clearance or a route; under the vanity when the floor has no such spot
            if place_floor_item(p, [rid], "flange_plunger", near=p.c["entities"][toilet]["pos"], clean=True) is None and cab is not None:
                p.inside(cab, "flange_plunger", shelf=0)
        if rooms[rid]["type"] == "bathroom":
            first, second = ("bathtub", "shower_stall") if rng.random() < 0.6 else ("shower_stall", "bathtub")
            if furniture_keeping_reach(p, rid, first, wall=True) is None and furniture_keeping_reach(p, rid, second, wall=True) is None:
                stall = furniture_keeping_reach(p, rid, "shower_stall", wall=True, required=True)
                if stall is not None and len(p.rooms("bathroom")) > 1 and unsound_spot(p, stall):
                    # no spot for a tub or a stall that does not stand it under a wall cabinet or in front of a window
                    # blind: this cramped bathroom goes without (the house has another bathroom; _ensure_bathing)
                    _remove_tree(p, stall)
                    cramped.append(rid)
    _ensure_bathing(p, cramped)
    for rid in p.rooms("laundry_room") + p.rooms("shared_laundry"):
        box = None
        for line in ("hot", "cold"):
            # one washer box holds both valves: the cold one shares the hot one's wall cell (kernel v2.5 keeps a
            # valve's front cell clear, so two separate cells would take wall space the washer needs)
            vid = mount_clear(p, rid, "shutoff_valve", height=1.1, label="washer {} valve".format(line), line=line, valve_kind="quarter_turn_stop",
                              turns_to_close=0.25, serves_label="washing machine", feeds=[], **({"cell": box} if box else {}))
            if vid and box is None:
                box = list(p.c["entities"][vid]["pos"])
        tub = rooms[rid]["type"] == "laundry_room" and rng.random() < 0.5
        if tub:
            # the laundry tub goes in after the washer and dryer (plumbing_liquids.links): a tub placed first could
            # take the wall run the machines need
            p.c["rooms"][rid]["_plumbing_tub"] = True
        if rng.random() < 0.5:
            p.floor_item(rid, "floor_drain")
    _service(p, rng, opts)
    _outdoor(p, rng, opts)


def _washer_spots(p, rid, first=False):
    """Wall spots (top-left cells) where a 0.6 m x 0.6 m machine (2 x 2 cells) still fits with access kept."""
    gc, gr = geometry.grid_size(p.c["rooms"][rid])
    occupied = p._occupied(rid)
    check = p._connectivity(rid, "all")
    spots = []
    for y in range(0, gr - 1):
        for x in range(0, gc - 1):
            cells = [(x, y), (x + 1, y), (x, y + 1), (x + 1, y + 1)]
            if (x in (0, gc - 2) or y in (0, gr - 2)) and not any(c in occupied for c in cells) and check((x, y), 2, 2):
                spots.append((x, y))
                if first:
                    return spots
    return spots


def _room_for_pair(p, rid, spare=1):
    """True if a washer AND a dryer (two 2 x 2 wall spots) still fit with access kept, plus `spare` more spots for
    what later furnishers put on the walls and floor (registers, vents, shelving): a laundry room must keep space
    for both machines, since the washer box valves sit behind the washer and a dryer stacked over them would hide
    them (textiles then leaves the room without a dryer). Spots are taken greedily by probe footprints."""
    probes = []
    try:
        while len(probes) < 2 + spare:
            spots = _washer_spots(p, rid, first=True)
            if not spots:
                return False
            pid = "_plumbing_machine_probe_{}".format(len(probes))
            p.c["entities"][pid] = {"label": "machine spot", "category": "appliance", "location": rid, "pos": list(spots[0]),
                                    "size_m": [1.0, 1.0, 0.85], "blocks_path": True, "portable": False}
            probes.append(pid)
        return True
    finally:
        for pid in probes:
            p.c["entities"].pop(pid, None)


def _service(p, rng, opts):
    room = None
    for kind in SERVICE_ROOMS:
        found = p.rooms(kind)
        if found:
            room = found[0]
            break
    if room is None:
        return
    apartment = (opts.get("plan") or {}).get("dwelling") == "apartment"
    gate = rng.random() < 0.6
    props = {} if gate else {"valve_kind": "ball", "turns_to_close": 0.25, "mech": {}}
    main = mount_clear(p, room, "main_shutoff_valve", height=0.6 if not apartment else 0.3, eid="main_water_valve",
                       label="main water shutoff" if not apartment else "unit water shutoff", **props)
    if main and rng.random() < 0.06:
        p.c["entities"][main]["_seized"] = True
    if not apartment:
        mount_clear(p, room, "water_meter", height=0.3, eid="water_meter_1")
    heater = None
    heater_room = None
    # a 190 L tank stands in a service room; never in the kitchen, nor in a laundry closet or pantry under 6 m2
    # (those homes get a wall-hung tankless unit in the service room instead)
    candidates = [r for r in [room] + [r for kind in ("basement", "utility_closet", "garage", "laundry_room", "pantry") for r in p.rooms(kind)]
                  if p.c["rooms"][r].get("type") != "kitchen"]
    for cand in dict.fromkeys(candidates):
        size = p.c["rooms"][cand].get("size_m") or [0, 0]
        if p.c["rooms"][cand].get("type") in ("laundry_room", "pantry") and C.num(size[0]) * C.num(size[1]) < 6.0:
            continue
        heater = furniture_keeping_reach(p, cand, "water_heater", wall=True, eid="water_heater_1")
        if heater and cand in p.rooms("laundry_room") and not _room_for_pair(p, cand, spare=2):
            _remove_tree(p, heater)      # the tank would leave a small laundry room no spot for the washer and dryer
            heater = None
        if heater:
            heater_room = cand
            break
    if heater is None:
        # no floor spot keeps the room usable: a wall-hung tankless unit in the service room
        heater = mount_clear(p, room, "water_heater", height=1.2, eid="water_heater_1", **TANKLESS)
        heater_room = room if heater else None
    if heater_room:
        if heater:
            mount_clear(p, heater_room, "shutoff_valve", height=1.6, eid="water_heater_inlet_valve", label="water heater cold inlet valve",
                        line="cold", feeds_hot=True, valve_kind="gate", turns_to_close=5.0, serves_label="water heater")
            if rng.random() < 0.5:
                p.c["entities"][heater]["fuel"] = "electric"
                p.c["entities"][heater]["recovery_kw"] = 24.0 if p.c["entities"][heater].get("tankless") else 4.5
                p.c["entities"][heater]["plug_required"] = False      # hardwired
    if room in p.rooms("basement") or room in p.rooms("utility_closet"):
        pipe = mount_clear(p, room, "water_pipe", height=2.1, label="cold water supply pipe", condensation_prone=rng.random() < 0.4)
        if pipe is not None:
            lay_pipe(p, pipe)
    for rid in p.rooms("basement") + p.rooms("garage"):
        if rng.random() < 0.6:
            p.floor_item(rid, "floor_drain")


def _service_room(p):
    for kind in SERVICE_ROOMS:
        found = p.rooms(kind)
        if found:
            return found[0]
    return None


def _outdoor(p, rng, opts):
    yards = p.rooms("backyard") + p.rooms("front_yard")
    inside = _service_room(p)
    for i, rid in enumerate(yards):
        if i > 0 and rng.random() < 0.4:
            continue
        frost_free = rng.random() < 0.6
        spigot = mount_clear(p, rid, "outdoor_spigot", height=0.45, frost_free=frost_free,
                             label="{} spigot".format(p.c["rooms"][rid].get("label", rid).lower()))
        if spigot is None:
            continue
        if not frost_free and inside:
            valve = mount_clear(p, inside, "shutoff_valve", height=1.8,
                                label="interior shutoff for the {} spigot".format(p.c["rooms"][rid].get("label", "outdoor").lower()),
                                line="cold", feeds=[spigot], serves_label="outdoor spigot")
            if valve:
                p.c["entities"][spigot]["interior_valve"] = valve
        if rid in p.rooms("backyard"):
            hanger = p.mount(rid, "hose_hanger", height=1.0, cell=p.c["entities"][spigot]["pos"])
            hose = p.hang(hanger, "garden_hose") if hanger else None
            if hose:
                rec = p.c["entities"][hose]
                if rng.random() < 0.6:
                    rec["attached_to"] = spigot
                if rng.random() < 0.7:
                    nozzle = p.make("hose_nozzle", hose)
                    p.c["entities"][nozzle]["relation"] = "attached"
                    rec["nozzle"] = nozzle
            if rng.random() < 0.25:
                barrel = furniture_keeping_reach(p, rid, "rain_barrel", wall=True)
                if barrel:
                    p.c["entities"][barrel]["liquid_l"] = round(rng.uniform(30, 180), 1)
                    p.c["entities"][barrel]["_mix"] = {"rainwater": p.c["entities"][barrel]["liquid_l"]}
    for rid in p.rooms("driveway") or p.rooms("sidewalk") or p.rooms("front_yard"):
        p.floor_item(rid, "storm_drain")
        break
    garage = (p.rooms("garage") or [None])[0]
    if garage and yards and rng.random() < 0.4:
        ctrl = mount_clear(p, garage, "irrigation_controller", height=1.4, eid="sprinkler_controller")
        if ctrl:
            zones, program = {}, []
            for n, rid in enumerate(yards[:3], 1):
                heads = [h for h in (p.floor_item(rid, "sprinkler_head") for _ in range(2)) if h]
                if heads:
                    zone = "zone_{}".format(n)
                    zones[zone] = heads
                    program.append({"zone": zone, "start_h": 5.0 + 0.5 * (n - 1), "minutes": 20, "days": ["Monday", "Wednesday", "Friday"]})
            p.c["entities"][ctrl]["zones"] = zones
            p.c["entities"][ctrl]["program"] = program


# ----------------------------------------------------------------------------------------------------------------
# appliance water links

def _washer(rec):
    return _is(rec, "washing_machine") or rec.get("appliance_kind") == "washer" or rec.get("category") == "washer"


def _dishwasher(rec):
    return _is(rec, "dishwasher") or rec.get("ac_kind") == "dishwasher"


def _fridge(rec):
    return _is(rec, "refrigerator") or rec.get("ac_kind") == "fridge"


@furnisher("plumbing_liquids.links", order=65)
def link_appliances(config, rng, opts, registry):
    """After appliances exist: washer valves feed the washer (two fill hoses behind it); each dishwasher gets one hot
    supply stop under the kitchen sink and one supply line behind it; a refrigerator often has an ice-maker line
    valve. Only the appliances themselves are matched (type/is_a), never their racks, baskets, filters, sumps, door
    bins or the detergent pods stored nearby, and an appliance already fed by a stop gets no second one."""
    p = Placer(config, registry, rng)
    ents = config["entities"]
    for rid in p.rooms("laundry_room") + p.rooms("shared_laundry"):
        washers = [e for e in p.find(None, rid) if _washer(ents[e])]
        if not washers:
            continue
        for vid in p.find("shutoff_valve", rid):
            if (ents[vid].get("serves_label") or "") == "washing machine":
                ents[vid]["feeds"] = [washers[0]]
        if any(ents[h].get("location") == washers[0] for h in p.find("appliance_supply_hose", rid)):
            continue
        for line in ("hot", "cold"):
            hose = p.make("appliance_supply_hose", washers[0], label="washer {} fill hose".format(line), line=line,
                          hose_kind=rng.choice(["rubber", "rubber", "braided_steel"]), slack_m=round(rng.uniform(0.25, 0.6), 2),
                          label_text="MFD {}".format(rng.randint(2009, 2024)))
            ents[hose]["relation"] = "behind"
            if rng.random() < 0.12:
                ents[hose]["_defect"] = rng.choice(["bulge", "crack", "kink"])
    for rid in p.rooms("kitchen"):
        cab = next((e for e in p.find("cabinet", rid) if ents[e].get("sink_base")), None)
        if cab is None:
            continue
        fed = {f for v in p.find("shutoff_valve", rid) for f in (ents[v].get("feeds") or [])}
        for e in p.find(None, rid):
            rec = ents[e]
            if e in fed:
                continue
            if _dishwasher(rec):
                p.inside(cab, "shutoff_valve", label="dishwasher supply stop", line="hot", feeds=[e], serves_label="dishwasher", shelf=0)
                hose = p.make("appliance_supply_hose", e, label="dishwasher supply line", line="hot", hose_kind="braided_steel",
                              slack_m=round(rng.uniform(0.2, 0.5), 2), label_text="MFD {}".format(rng.randint(2012, 2024)))
                ents[hose]["relation"] = "behind"
                fed.add(e)
            elif _fridge(rec) and rng.random() < 0.6:
                p.inside(cab, "shutoff_valve", label="refrigerator ice-maker line valve", line="cold", feeds=[e], serves_label="refrigerator", shelf=0)
                fed.add(e)
    for rid in p.rooms("laundry_room"):
        if p.c["rooms"][rid].pop("_plumbing_tub", None):
            _laundry_tub(p, rid)


def _laundry_tub(p, rid):
    """A utility sink (laundry tub) where a spot is left once the washer and dryer stand; its hot and cold stops
    sit on the wall under its basin (0.45 m), not on wall space of their own."""
    sink = furniture_keeping_reach(p, rid, "utility_sink", wall=True)
    if not sink:
        return None
    for line in ("hot", "cold"):
        mount_clear(p, rid, "shutoff_valve", height=0.45, cell=list(p.c["entities"][sink]["pos"]),
                    label="{} stop for the utility sink".format(line), line=line, feeds=[sink])
    return sink


# ----------------------------------------------------------------------------------------------------------------
# small items

def _freezer(p):
    for eid in sorted(p.c["entities"]):
        rec = p.c["entities"][eid]
        if rec.get("internal_temp_c") is not None and C.num(rec.get("internal_temp_c")) <= -10 and rec.get("container"):
            return eid
    return None


def _store_inside(p, rooms, type_name, **props):
    """Put an item on the bottom shelf of reachable shelving (shelf unit, closet shelving) in the first of `rooms`
    that has room for it, else under a sink base. None when nothing fits."""
    ents = p.c["entities"]
    for kinds in (("shelf_unit", "closet_shelving"), ("cabinet",)):
        for rid in rooms:
            for cont in p.find(None, rid):
                rec = ents[cont]
                if rec.get("location") != rid or not rec.get("container") or not any(_is(rec, k) for k in kinds):
                    continue
                if kinds == ("cabinet",) and not rec.get("sink_base"):
                    continue
                if not reachable(p, cont):
                    continue
                eid = p.inside(cont, type_name, shelf=0, **props)
                if eid is not None:
                    return eid
    return None


WINTERIZED_SHARE = 0.7       # assumption: most households with a frost-prone yard put the hose away for the winter


@furnisher("plumbing_liquids.household", order=93)
def furnish_household(config, rng, opts, registry):
    """Generation 2 only (TIER1_SWEEP 14, the household profile and season): the water heater burns what the house
    is supplied with (a home without gas service has an electric unit), and in winter most households have put the
    garden water away (hoses disconnected, drained and coiled, standard spigots shut inside and drained, sprinklers
    switched off; WINTERIZED_SHARE of households, the rest left it as it was, which winter tasks are made of).
    Generation 1 is untouched (the furnisher returns at once); its own draws come from this furnisher's stream."""
    if int(opts.get("gen") or 1) < 2:
        return
    ents = config["entities"]
    utilities = config.get("utilities") or {}
    heater = ents.get("water_heater_1")
    if heater is not None and not utilities.get("gas", True) and heater.get("fuel", "gas") == "gas":
        heater["fuel"] = "electric"
        heater["recovery_kw"] = 24.0 if heater.get("tankless") else 4.5
        heater["plug_required"] = False      # hardwired
    if opts.get("season") != "winter" or rng.random() >= WINTERIZED_SHARE:
        return
    for eid in sorted(ents):
        rec = ents[eid]
        if rec.get("type") == "garden_hose" and rec.get("attached_to"):
            rec.update(attached_to=None, pressurized=False, liquid_l=0.0, coiled=True)
        elif rec.get("type") == "outdoor_spigot":
            settings = rec.setdefault("settings", {})
            settings["valve"] = 0.0
            valve = ents.get(rec.get("interior_valve") or "")
            if valve is not None and not rec.get("frost_free"):
                valve.setdefault("settings", {})["position"] = 0.0
                valve["valve_open"] = False
                rec["drained"] = True
        elif rec.get("irrigation_controller") and rec.get("driver") == "plumbing_liquids":
            rec.setdefault("settings", {})["mode"] = "off"


@furnisher("plumbing_liquids.items", order=75)
def furnish_items(config, rng, opts, registry):
    p = Placer(config, registry, rng)
    ents = config["entities"]
    residents = sorted(pid for pid, person in config.get("people", {}).items() if person.get("role") != "infant")
    for rid in p.rooms("kitchen"):
        counters = p.find("counter", rid)
        if counters:
            p.on(counters[0], "sink_stopper")
        uppers = [e for e in p.find("upper_cabinet", rid)]
        for pid in residents[:3]:
            if uppers:
                # whose bottle it is shows only on its name sticker (read), never in what the camera calls it
                name = str(config["people"][pid].get("label", pid)).split(" (")[0].upper()
                p.inside(uppers[0], "water_bottle", label="water bottle", name_label=name, owner=pid)
        if rng.random() < 0.4:
            fridge = next((e for e in sorted(ents) if ents[e].get("internal_temp_c") is not None and 0 < C.num(ents[e].get("internal_temp_c")) < 8
                           and ents[e].get("container")), None)
            target = fridge or (counters[0] if counters else None)
            if target:
                pitcher = p.inside(target, "filter_pitcher") if fridge else p.on(target, "filter_pitcher")
                if pitcher:
                    cart = p.make("pitcher_filter_cartridge", pitcher, used_l=round(rng.uniform(10, 140), 1))
                    ents[cart]["relation"] = "part"
                    ents[cart]["slot"] = "cartridge"
                    ents[pitcher]["slots"]["cartridge"]["item"] = cart
                    if not fridge:
                        ents[pitcher]["temperature_c"] = 21.0
    freezer = _freezer(p)
    if freezer:
        for i in range(2):
            tray = p.inside(freezer, "ice_cube_tray")
            if tray and i == 0:
                ents[tray].update(ice_g=380.0, ice_cubes=14, frozen_frac=1.0, cell_fill=0.0, temperature_c=-18.0)
        if rng.random() < 0.5:
            p.inside(freezer, "freezer_ice_maker")
    utility_rooms = p.rooms("laundry_room") + p.rooms("utility_closet") + p.rooms("garage") + p.rooms("basement")
    if utility_rooms:
        if place_floor_item(p, utility_rooms, "utility_bucket", clean=True) is None:
            # no clear floor spot: on the bottom shelf of the utility shelving, else under the kitchen sink
            _store_inside(p, utility_rooms + p.rooms("kitchen"), "utility_bucket")
    stores = p.rooms("garage") + p.rooms("shed") + p.rooms("porch") + p.rooms("patio") + p.rooms("backyard")
    outdoor_store = (stores or [None])[0]
    if outdoor_store and (p.rooms("backyard") or p.rooms("front_yard") or p.rooms("garden")):
        place_floor_item(p, stores, "watering_can", clean=True)
        if rng.random() < 0.5:
            shelves = p.find("shelf_unit", outdoor_store)
            if shelves:
                p.inside(shelves[0], "hose_nozzle", label="spare hose nozzle", shelf=1)
    for rid in p.rooms("guest_room"):
        stands = p.find("nightstand", rid)
        if stands and rng.random() < 0.5:
            p.on(stands[0], "water_carafe")
