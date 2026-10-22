"""Furnishers: tools, garage/workshop gear, cars and bikes in generated houses.

* tools_vehicles.vehicles (order 32, before small furniture fills the garage/driveway): a car in the garage if there is
  one (else on the driveway of a house) through Placer.furniture (kernel v2.3: access not worse) plus a light guard
  (lights are not kernel access targets, so a car must not seal the outdoor/wall light a person re-lamps from a step
  stool), with trunk, footwells, mats, rear seat and a roadside kit (children of the car); the fob hangs on the
  entryway key hook. Bikes (blocks_path: nobody walks through a bicycle) are parked along a wall of the
  garage/shed/basement through Placer.floor_item with the kernel keep_access rule (never cutting a route, sealing
  anything or standing in a door clearance; left out when nothing qualifies), the floor pump stands beside them.
* tools_vehicles.tools (order 74): a toolbox on the garage/basement workbench (or in the utility closet), a pegboard with
  tool outlines on the wall at the workbench (outside door clearance, not over another wall fixture), a cordless drill
  with battery and bits, hardware organizer, automotive supplies on a high garage shelf, painting/patching supplies in
  the basement, a screwdriver and adhesive hooks in the kitchen junk drawer.
Counts stay modest (roughly 15-45 entities per house) and every placement tolerates a full room (Placer returns None).

Generation 2 (opts["gen"] >= 2, docs/GENERATOR.md; generation-1 houses are unchanged): the vehicles follow the
household. Residents who drive are marked (`can_drive`: adults with full vision and cognition, 90 %; the elderly by
age, 85 % under 80, 50 % in their 80s, 20 % older) and a household with nobody who drives has no car; the car carries
a child seat for every child up to 7 (at most two, infants and toddlers rear-facing); a car parked outdoors on a
frosty morning (0 C or below at the start) has frosted glass and, after snow, snow on the roof. Bikes are the
riders': a kids' bike for 75 % of children aged 4-12, a bike for 60 % of teens, 35 % of adults (at most two) and 10 %
of the elderly, parked as in generation 1.
"""

import hashlib
import random

from ...generator.place import Placer
from ...kernel import geometry
from ...kernel.plugins import furnisher


def _foreign(registry, type_name, props):
    if registry.known(type_name):
        return type_name, {k: v for k, v in props.items() if k not in ("label", "size_m", "mass_kg", "category", "material", "volume_l")}
    return "generic_item", dict(props)


def _first(p, type_name, room):
    found = p.find(type_name, room)
    return found[0] if found else None


SERVICE_LIGHT_MAX_M = 2.5   # wall, porch and ceiling lights a person re-lamps from a step stool (outdoor lights 2.2 m)


def _light_targets(state, rid):
    """Mounted lights a person services from the floor (bulb changes from a step stool), which the kernel's access
    targets leave out: {eid: (cells, radius, False)} in geometry.access_targets' shape."""
    room = state["rooms"][rid]
    out = {}
    for eid in sorted(geometry.room_entities(state, rid)):
        rec = state["entities"][eid]
        if not rec.get("mounted") or rec.get("category") != "light" or not rec.get("pos"):
            continue
        try:
            height = float(rec.get("mount_height_m", 2.4) or 0.0)
        except (TypeError, ValueError):
            continue
        if height > SERVICE_LIGHT_MAX_M:
            continue
        cells = [c for c in geometry.footprint(rec) if geometry.in_grid(room, c)]
        if cells:
            out[eid] = (cells, geometry.access_radius(rec), False)
    return out


class _LightGuard:
    """Placer.furniture / Placer.floor_item keep every kernel access target accessible, but lights are not targets: a
    car parked along the driveway's house wall would seal the outdoor light above it. The guard records which
    serviceable lights are accessible now (plain walking graph, as the kernel's furniture rule) and says whether a
    placement kept all of them accessible."""

    def __init__(self, p, rid):
        self.p, self.rid = p, rid
        state = p.state()
        self.lights = _light_targets(state, rid)
        self.targets = dict(geometry.access_targets(state, rid, level="doors"))
        self.targets.update(self.lights)
        self.before = self._ok(state) if self.lights else set()

    def _ok(self, state):
        blocked = set(geometry.blocked_cells(state, self.rid, include_agents=False))
        return {k for k in geometry.access_report(state, self.rid, blocked, self.targets)["ok"] if k in self.lights}

    def kept(self):
        return not self.before or self.before <= self._ok(self.p.state())

    def cells(self):
        return [c for eid in sorted(self.before) for c in self.lights[eid][0]]


def guarded_furniture(p, rid, type_name, wall=False, anchors=None, accept=None, **props):
    """Placer.furniture (kernel v2.3: access not worse) that also keeps serviceable lights accessible. anchors: [(near,
    rot)] tried in order (default: one free try); a spot whose rotation differs from its anchor's (Placer turns a piece
    that does not fit) or that fails accept(record) (e.g. a car out of line with the garage door) is discarded. If the
    accepted spot seals a light, the spots farthest from the lights are retried (deterministic) before keeping it."""
    guard = _LightGuard(p, rid)
    tries = list(anchors) if anchors else [(props.pop("near", None), props.pop("rot", None))]
    props.pop("near", None)
    props.pop("rot", None)
    sealed = None

    def attempt(near, rot, eid=None):
        cid = p.furniture(rid, type_name, eid=eid, wall=wall, near=near, rot=rot, fallback=near is None, **props)
        if cid is None:
            return None
        rec = p.c["entities"][cid]
        if (rot is not None and int(rec.get("rot", 0) or 0) != rot) or (accept is not None and not accept(rec)):
            del p.c["entities"][cid]
            return None
        return cid

    for near, rot in tries:
        cid = attempt(near, rot)
        if cid is None:
            continue
        if guard.kept():
            return cid
        sealed = (cid, p.c["entities"].pop(cid))
        break
    if sealed is None:
        return None
    first_id, first = sealed
    record = p.reg.make(type_name, rid, **props)
    gc, gr = geometry.grid_size(p.c["rooms"][rid])
    lights = guard.cells()
    rot = int(first.get("rot", 0) or 0)
    cols, rows = geometry.footprint_size(dict(record, rot=rot))
    options = []
    for y in range(gr - rows + 1):
        for x in range(gc - cols + 1):
            d = min(geometry.chebyshev((x + i, y + j), c) for i in range(cols) for j in range(rows) for c in lights)
            options.append((-d, y, x))
    for _, y, x in sorted(options)[:16]:
        cid = attempt([x, y], rot, eid=first_id)
        if cid is None:
            continue
        if guard.kept():
            return cid
        del p.c["entities"][cid]
    p.c["entities"][first_id] = first      # nothing keeps every light serviceable: the spot the placer chose
    return first_id


def _overhead_door_wall(state, rid):
    """(doorway cell, inward step, threshold cells) of the room's overhead (garage) door, or None. The wall follows the
    kernel's overhead_door_cells rule (a corner cell belongs to the wall its threshold run lies along)."""
    room = state["rooms"][rid]
    gc, gr = geometry.grid_size(room)
    for _, conn in sorted(state.get("connections", {}).items()):
        cell = conn.get("cells", {}).get(rid)
        door = state["entities"].get(conn.get("door")) if conn.get("door") else None
        if cell is None or not (conn.get("kind") == "garage" or (door and geometry.is_overhead_door(door, conn))):
            continue
        x, y = int(cell[0]), int(cell[1])
        if y in (0, gr - 1) and not (x in (0, gc - 1) and gr > gc):
            inward = (0, 1) if y == 0 else (0, -1)
        else:
            inward = (1, 0) if x == 0 else (-1, 0)
        span = geometry.overhead_door_cells(state, conn["door"]).get(rid) if door else None
        if not span:       # an opening without a door entity: its width, centred on the doorway cell, along the wall
            n = max(1, int(round(float(conn.get("width_m", 2.4) or 2.4) / geometry.CELL)))
            run = range(-(n // 2), n - n // 2)
            span = [(x + d, y) if inward[1] else (x, y + d) for d in run]
            span = [c for c in span if geometry.in_grid(room, c)] or [(x, y)]
        return (x, y), inward, span
    return None


def car_pose(state, rid, record):
    """How a car stands where it was driven in: (anchors [(near, rot)], accept(record)). In a garage with an overhead
    door it points nose-in, its long axis perpendicular to the door wall and its width inside the door opening (one
    cell of slack each side), the anchors centred on the opening just past the threshold clearance and at the back
    wall; on a driveway (or any outdoor spot, even one the garage door opens onto) it lies along the long side."""
    room = state["rooms"][rid]
    gc, gr = geometry.grid_size(room)
    size = record.get("size_m") or [1.85, 4.7, 1.45]
    long_y = float(size[1]) >= float(size[0])          # rot 0 keeps the long axis along y
    door = None if room.get("outdoor") else _overhead_door_wall(state, rid)   # a driveway may hold the garage door too
    if door is None:
        rot = 0 if (gr >= gc) == long_y else 90
        return [(None, rot)], (lambda rec: int(rec.get("rot", 0) or 0) == rot)
    (x, y), inward, span = door
    along_y = inward[1] != 0
    rot = 0 if along_y == long_y else 90
    cols, rows = geometry.footprint_size(dict(record, rot=rot))
    if along_y:
        lo, hi = min(c[0] for c in span) - 1, max(c[0] for c in span) + 1
        centre = (lo + hi + 1) // 2 - cols // 2
        lateral = sorted(range(max(0, lo), min(gc - cols, hi - cols + 1) + 1), key=lambda v: (abs(v - centre), v))
        depths = [2, gr - rows] if inward == (0, 1) else [gr - 2 - rows, 0]
        anchors = [([v, d], rot) for d in depths for v in lateral]
    else:
        lo, hi = min(c[1] for c in span) - 1, max(c[1] for c in span) + 1
        centre = (lo + hi + 1) // 2 - rows // 2
        lateral = sorted(range(max(0, lo), min(gr - rows, hi - rows + 1) + 1), key=lambda v: (abs(v - centre), v))
        depths = [2, gc - cols] if inward == (1, 0) else [gc - 2 - cols, 0]
        anchors = [([d, v], rot) for d in depths for v in lateral]

    def accept(rec):
        if int(rec.get("rot", 0) or 0) != rot:
            return False
        px, py = rec["pos"]
        return lo <= (px if along_y else py) and (px + cols - 1 if along_y else py + rows - 1) <= hi
    unique = []
    for anchor in anchors:
        if anchor not in unique:
            unique.append(anchor)
    return unique or [(None, rot)], accept


def _wall_spots(room, record):
    """(x, y, rot) anchors where a long floor item stands with its long side along a wall (how bikes are parked)."""
    gc, gr = geometry.grid_size(room)
    out = []
    for rot in (0, 90):
        cols, rows = geometry.footprint_size(dict(record, rot=rot))
        if cols == rows and rot == 90:
            continue
        for y in range(gr - rows + 1):
            for x in range(gc - cols + 1):
                if (cols >= rows and (y == 0 or y + rows == gr)) or (rows > cols and (x == 0 or x + cols == gc)):
                    out.append((x, y, rot))
    return out


HANDRAIL_RUN_M = 3.5     # a stair handrail runs this far along the wall from the foot of the stairs


def stair_rail_cells(config, rid):
    """Wall cells a stair handrail will take in rid, the lower room of a flight (navigation_robot mounts it later, at
    order 66, along the wall the stair opening is in, running toward the side with more wall). Nobody parks a bike
    against the stair wall: the rail would end up hidden behind it (the layout audit's "hidden mount")."""
    rooms = config["rooms"]
    room = rooms[rid]
    cols, rows = geometry.grid_size(room)
    out = set()
    for cid in sorted(config.get("connections", {})):
        conn = config["connections"][cid]
        if conn.get("kind") != "stairs" or conn.get("handrail") is False or rid not in conn.get("cells", {}):
            continue
        a, b = conn["rooms"]
        lower = a if rooms[a].get("floor", 0) <= rooms[b].get("floor", 0) else b
        if lower != rid:
            continue
        x, y = (int(v) for v in conn["cells"][rid])
        _, lateral = geometry.wall_inward(room, (x, y))
        along_x = lateral == (1, 0)
        span = cols if along_x else rows
        here = x if along_x else y
        n = max(1, min(int(round(HANDRAIL_RUN_M / geometry.CELL)), span))
        start = here if span - here >= n else max(0, here - n + 1)
        if span - start < n:
            start = max(0, span - n)
        for i in range(start, start + n):
            out.add((i, y) if along_x else (x, i))
    return out


def park_floor_item(p, rid, type_name, rng, tries=24, **props):
    """A long path-blocking floor item (a bicycle) parked along a wall through Placer.floor_item: the kernel's
    keep_access rule (no hazard-free or plain route cut, nothing that was accessible sealed, never in a doorway
    clearance) decides each candidate cell, and lights must stay serviceable too. Prefers wall spots (long side
    against the wall, either orientation), then any cell the rule allows; None when nothing qualifies (no plain-route
    fallback: the bike is left out rather than boxing anything in)."""
    room = p.c["rooms"][rid]
    guard = _LightGuard(p, rid)
    base = p.reg.make(type_name, rid, **props)
    rail = sorted(stair_rail_cells(p.c, rid))
    spots = [s for s in _wall_spots(room, base)
             if not set(geometry.footprint(dict(base, rot=s[2], pos=[s[0], s[1]]))) & set(rail)]
    rng.shuffle(spots)
    walls = set(spots)
    seen = set()
    fallback = None
    for x, y, rot in spots[:tries]:
        cell = geometry.free_floor_cell(p.state(), rid, near=(x, y), record=dict(base, rot=rot), keep_access=True, avoid=rail)
        if cell is None or (cell[0], cell[1], rot) in seen:
            continue
        seen.add((cell[0], cell[1], rot))
        if (cell[0], cell[1], rot) not in walls:
            fallback = fallback or (cell, rot)
            continue
        eid = p.floor_item(rid, type_name, cell=cell, rot=rot, **props)
        if guard.kept():
            return eid
        del p.c["entities"][eid]
    if fallback is None:
        for rot in (0, 90):
            cell = geometry.free_floor_cell(p.state(), rid, record=dict(base, rot=rot), keep_access=True, avoid=rail)
            if cell is not None:
                fallback = (cell, rot)
                break
    if fallback is not None:
        eid = p.floor_item(rid, type_name, cell=fallback[0], rot=fallback[1], **props)
        if guard.kept():
            return eid
        del p.c["entities"][eid]
    return None


def place_car(config, rng, registry, room, p, label="sedan"):
    anchors, accept = car_pose(p.state(), room, registry.make("car", room))
    cid = guarded_furniture(p, room, "car", anchors=anchors, accept=accept, label=label, plate="TV-{:04d}".format(rng.randint(0, 9999)),
                            color=rng.choice(["silver", "white", "blue", "black", "red"]))
    if cid is None:
        return None
    rec = config["entities"][cid]
    rec["tires"] = {w: {"kind": "tire", "pressure_bar": round(rng.uniform(2.1, 2.45), 2), "range_bar": [2.3, 2.5], "burst_bar": 9.0, "volume_l": 30.0,
                        "valve": "schrader", "label_text": "Door placard: 35 psi (240 kPa) cold"} for w in ("fl", "fr", "rl", "rr")}
    rec["glass_ice_mm"] = {"windshield": 0.0, "rear": 0.0, "sides": 0.0}
    rec["home_room"] = room
    rec["slots"] = {"wiper_driver": {"accepts": ["wiper_22in"], "item": None, "access": "none"},
                    "wiper_passenger": {"accepts": ["wiper_18in"], "item": None, "access": "none"}}
    trunk = p.make("car_trunk", cid, label="trunk")
    config["entities"][trunk]["relation"] = "part"
    for pos in ("driver", "passenger", "rear_left", "rear_right"):
        fw = p.make("car_footwell", cid, label="{} footwell".format(pos.replace("_", " ")), position=pos)
        config["entities"][fw]["relation"] = "part"
        if pos in ("driver", "passenger"):
            mat = p.make("car_floor_mat", fw, label="{} floor mat".format(pos), position=pos)
            config["entities"][mat].update(relation="part", slot="mat")
            config["entities"][fw]["slots"]["mat"]["item"] = mat
    seat = p.make("generic_item", cid, label="rear seat", category="car_rear_seat", portable=False, accepts_items=True, surface=True,
                  container=False, surface_size_m=[1.3, 0.5], size_m=[1.3, 0.5, 0.45], z_m=0.45, mass_kg=0.0, cabin=True)
    config["entities"][seat]["relation"] = "part"
    kit = p.inside(trunk, "car_emergency_kit", kit_checklist=["jumper cables", "flashlight", "reflective triangle", "first-aid kit",
                                                             "emergency blanket", "water", "gloves"], home=trunk, cargo_pocket=True)
    if kit:
        p.inside(kit, "jumper_cables", kit_item="jumper cables")
        p.inside(kit, "reflective_triangle")
        p.inside(kit, "emergency_blanket")
        p.inside(kit, "kit_water_bottle", expiration=rng.choice(["2026-03-01", "2027-06-01", "2028-01-15"]))
        p.inside(kit, "flashlight", label="kit flashlight", kit_item="flashlight", secret_fields=["battery_level"],
                 battery_level=rng.choice([1.0, 0.8, 0.0]))
    p.inside(trunk, "ice_scraper")
    hook_rooms = p.rooms("entryway") or p.rooms("mudroom")
    fob = None
    for hr in hook_rooms:
        hook = _first(p, "key_hook", hr)
        if hook:
            fob = p.hang(hook, "car_key_fob", opens=[cid])
            if fob:
                break
    rec["key"] = fob
    rec["locked"] = fob is not None
    return cid


YOUNG_SEAT_AGE = 7        # US child-passenger guidance: a car seat or booster to about age 8 (up to 7 here)


def _members(config):
    return sorted((pid, rec) for pid, rec in (config.get("people") or {}).items() if isinstance(rec, dict))


def _age(rec, default=40):
    try:
        return float(rec.get("age")) if rec.get("age") is not None else float(default)
    except (TypeError, ValueError):
        return float(default)


def mark_drivers(config, rng):
    """Generation 2: who in the household drives (`can_drive`, the field drive_to reads): adults 18 and over with full
    vision and cognition (90 %), the elderly by age (85 % under 80, 50 % in their 80s, 20 % older) unless their vision
    or cognition is impaired. A `can_drive` the setup already gave is kept. Returns the drivers' ids."""
    drivers = []
    for pid, rec in _members(config):
        if rec.get("can_drive") is not None:
            if rec.get("can_drive"):
                drivers.append(pid)
            continue
        role, age = rec.get("role"), _age(rec)
        abilities = rec.get("abilities") if isinstance(rec.get("abilities"), dict) else {}
        fit = abilities.get("vision", "full") in ("full", "corrected") and abilities.get("cognition", "full") == "full"
        if role == "adult" and age >= 18:
            share = 0.9
        elif role == "elderly":
            share = 0.85 if age < 80 else (0.5 if age < 90 else 0.2)
        else:
            share = 0.0
        drives = fit and share > 0 and rng.random() < share
        rec["can_drive"] = bool(drives)
        if drives:
            drivers.append(pid)
    return drivers


def bike_riders(config, rng):
    """Generation 2: the bikes a household keeps, one per rider: [(type, owner)] -- a kids' bike for 75 % of children
    aged 4-12, a bike for 60 % of teens, 35 % of adults (at most two adult bikes) and 10 % of the elderly."""
    out, adults = [], 0
    for pid, rec in _members(config):
        role, age = rec.get("role"), _age(rec)
        if role == "child" and age >= 4 and rng.random() < 0.75:
            out.append(("kids_bicycle", pid))
        elif role == "teen" and rng.random() < 0.6:
            out.append(("bicycle", pid))
        elif role == "adult" and adults < 2 and rng.random() < 0.35:
            out.append(("bicycle", pid))
            adults += 1
        elif role == "elderly" and rng.random() < 0.1:
            out.append(("bicycle", pid))
    return out


def gen2_car(config, rng, p, car):
    """Generation 2: what the household keeps in and on its car -- child seats for the young children (at most two on
    the rear seat; infants and toddlers rear-facing, older children forward-facing or on a booster), and frost (and
    after snow, roof snow) on a car parked outdoors on a frosty start."""
    rec = config["entities"][car]
    young = [(pid, r) for pid, r in _members(config) if r.get("role") in ("infant", "toddler", "child") and _age(r, 5) <= YOUNG_SEAT_AGE]
    for pid, r in young[:2]:
        age = _age(r, 5)
        label = "rear-facing infant seat" if age < 1 else ("rear-facing child seat" if age < 4 else "booster seat")
        sid = p.make("child_car_seat", car, label=label, owner=pid,
                     _install={"latch_attached": True, "movement_in_mm": round(rng.uniform(8.0, 20.0), 1), "harness_snug": True,
                               "recline_ok": True})
        config["entities"][sid]["relation"] = "part"
    room = config["rooms"].get(rec.get("location")) or {}
    weather = config.get("weather") or {}
    try:
        temp = float(weather.get("temperature_c", 10.0))
    except (TypeError, ValueError):
        temp = 10.0
    if room.get("outdoor") and temp <= 0.0:
        snowing = weather.get("condition") in ("snow", "sleet") or float(weather.get("snow_cm_h", 0.0) or 0.0) > 0
        ice = round(rng.uniform(1.0, 3.0) if snowing else rng.uniform(0.3, 1.5), 1)
        rec["glass_ice_mm"] = {"windshield": ice, "rear": ice, "sides": round(ice * 0.6, 1)}
        rec["clear_view"] = False
        if snowing:
            rec["roof_snow_cm"] = round(rng.uniform(2.0, 8.0), 1)


@furnisher("tools_vehicles.vehicles", order=32)
def furnish_vehicles(config, rng, opts, registry):
    p = Placer(config, registry, rng)
    gen2 = int(opts.get("gen") or 1) >= 2
    # generation 2's household draws come from their own stream (seeded from this furnisher's), so a household that
    # has a driver gets its car exactly where generation 1 would put it
    extra = random.Random(hashlib.sha256(repr(rng.getstate()).encode("ascii")).hexdigest()) if gen2 else None
    drivers = mark_drivers(config, extra) if gen2 else None
    if (opts.get("plan") or {}).get("dwelling") == "apartment":
        return
    garages = p.rooms("garage")
    drives = p.rooms("driveway")
    car = None
    if drivers is None or drivers:
        if garages:
            car = place_car(config, rng, registry, garages[0], p)
        if car is None and drives and rng.random() < 0.85:
            car = place_car(config, rng, registry, drives[0], p)
    if car is not None and gen2:
        gen2_car(config, extra, p, car)
    bike_rooms = garages + p.rooms("shed") + p.rooms("basement")
    if bike_rooms and gen2:
        bikes = []
        for type_name, owner in bike_riders(config, extra):
            bid = _bike(config, p, bike_rooms, type_name, rng)
            if bid:
                config["entities"][bid]["owner"] = owner
                bikes.append(bid)
        if bikes:
            first = config["entities"][bikes[0]]
            p.floor_item(first["location"], "floor_pump", near=first["pos"], settings={"head": rng.choice(["presta", "schrader"])})
    elif bike_rooms:
        adults = sum(1 for v in config.get("people", {}).values() if v.get("role") == "adult")
        kids = sum(1 for v in config.get("people", {}).values() if v.get("role") == "child")
        bikes = []
        for i in range(min(2, max(1, adults - 1)) if rng.random() < 0.7 else 0):
            bikes.append(_bike(config, p, bike_rooms, "bicycle", rng))
        if kids:
            bikes.append(_bike(config, p, bike_rooms, "kids_bicycle", rng))
        bikes = [b for b in bikes if b]
        if bikes:
            first = config["entities"][bikes[0]]
            p.floor_item(first["location"], "floor_pump", near=first["pos"], settings={"head": rng.choice(["presta", "schrader"])})


def _bike(config, p, rooms, type_name, rng):
    """A bike parked along a wall (blocks_path: nobody walks through a bicycle), never boxing anything in: in the
    garage, else the shed or basement when the garage has no such spot (None if none has)."""
    bid = None
    for room in rooms:
        bid = park_floor_item(p, room, type_name, rng, blocks_path=True)
        if bid is not None:
            break
    if bid is None:
        return None
    kids = type_name == "kids_bicycle"
    valve = "schrader" if kids or rng.random() < 0.6 else "presta"
    rng_bar = [2.1, 3.1] if kids else ([5.5, 7.0] if valve == "presta" else [3.5, 4.8])
    rec = config["entities"][bid]
    rec["wheels"] = {w: {"kind": "tube", "pressure_bar": round(rng.uniform(0.5, rng_bar[1]), 2), "range_bar": rng_bar, "burst_bar": rng_bar[1] * 1.6,
                         "volume_l": 1.3 if kids else (0.6 if valve == "presta" else 1.6), "valve": valve,
                         "label_text": "Inflate to {:.0f}-{:.0f} PSI".format(rng_bar[0] / 0.0689476, rng_bar[1] / 0.0689476)}
                     for w in ("front", "rear")}
    from .fasteners import attach_fasteners
    specs = []
    for w in ("front", "rear"):
        specs.append({"id": w + "_cap", "kind": "valve_cap", "drive": "hand", "size": "1", "role": "cap", "group": w + "_valve", "substrate": "plastic",
                      "torque_nm": 0.2, "snug_nm": [0.1, 0.5], "strip_nm": 2.0, "spot": w + " valve cap", "hand_ok": True})
        if valve == "presta":
            specs.append({"id": w + "_valve_nut", "kind": "valve_nut", "drive": "hand", "size": "1", "role": "valve", "group": w + "_valve",
                          "substrate": "metal", "torque_nm": 0.2, "snug_nm": [0.1, 0.5], "strip_nm": 3.0, "spot": w + " Presta lock nut", "hand_ok": True})
    attach_fasteners(rec, specs)
    return bid


PEG_HEIGHT_M = 1.0       # bottom edge of the tool pegboard (a 0.6 m board: 1.0-1.6 m, above a 0.9 m workbench)


def _pegboard_spot(p, rid, bench, rng):
    """(cell, rot) for the tool pegboard, the way shops hang one: on a wall above or beside the workbench, its whole
    width on the wall (along the top/bottom wall, or turned 90 degrees on a side wall), outside every doorway clearance
    (geometry.door_clearance: a door leaf swinging into the board knocks the tools off), not over another wall
    fixture in its height band (switches, outlets, thermostats, windows, wall cabinets) nor behind furniture taller
    than its bottom edge, and within reach (two cells for a board above counter height). None if no wall qualifies."""
    state = p.state()
    room = p.c["rooms"][rid]
    gc, gr = geometry.grid_size(room)
    record = p.reg.make("pegboard", rid)
    lo = PEG_HEIGHT_M
    hi = lo + float((record.get("size_m") or [1.2, 0.03, 0.6])[2])
    keep_off = set(geometry.door_clearance(state, rid)) | set(geometry.doorway_cells(state, rid))
    for eid in geometry.room_entities(state, rid):
        rec = p.c["entities"][eid]
        if not rec.get("pos") or rec.get("ground_layer") or "connects" in rec:
            continue
        try:
            tall = float((rec.get("size_m") or [0, 0, 0.1])[2])
            # the layout audit's base: the mount height of mounted things, the floor for everything else (a mower's
            # z_m is its handle height, not where it stands)
            base = float(rec.get("mount_height_m", 0.0) or 0.0) if rec.get("mounted") else 0.0
        except (TypeError, ValueError, IndexError):
            tall, base = 2.0, 0.0
        if base < hi and base + tall > lo:      # anything in front reaching the board's height band hides or blocks it
            keep_off.update(geometry.footprint(rec))
    reach = geometry.reachable_cells(state, rid)
    bench_cells = geometry.footprint(p.c["entities"][bench]) if bench and p.c["entities"][bench].get("location") == rid else []
    options = []
    for rot in (0, 90):
        rec = dict(record, rot=rot)
        cols, rows = geometry.footprint_size(rec)
        for y in range(gr - rows + 1):
            for x in range(gc - cols + 1):
                if (rot == 0 and y not in (0, gr - 1)) or (rot == 90 and x not in (0, gc - 1)):
                    continue
                cells = geometry.footprint(dict(rec, pos=[x, y]))
                if any(c in keep_off for c in cells) or not (geometry.near_cells(cells, room, 2) & reach):
                    continue
                d = min(geometry.chebyshev(c, b) for c in cells for b in bench_cells) if bench_cells else 0
                options.append((d, rng.random(), [x, y], rot))
    if not options:
        return None
    best = min(options)
    return best[2], best[3]


PEG_OUTLINES = [("hammer", ["claw_hammer"], [0.05, 0.05], [0.34, 0.14]), ("wrench", ["adjustable_wrench"], [0.45, 0.05], [0.22, 0.08]),
                ("pliers", ["pliers"], [0.75, 0.05], [0.22, 0.07]), ("screwdriver", ["screwdriver"], [0.05, 0.3], [0.26, 0.05]),
                ("flathead", ["screwdriver"], [0.4, 0.3], [0.28, 0.05]), ("tape", ["tape_measure"], [0.8, 0.3], [0.1, 0.1])]


@furnisher("tools_vehicles.tools", order=74)
def furnish_tools(config, rng, opts, registry):
    p = Placer(config, registry, rng)
    shop = None
    for kind in ("garage", "basement"):
        for rid in p.rooms(kind):
            if _first(p, "workbench", rid):
                shop = rid
                break
        if shop:
            break
    if shop:
        bench = _first(p, "workbench", shop)
        box = p.on(bench, "toolbox") or p.floor_item(shop, "toolbox")
        tools = ["screwdriver_phillips_2", "screwdriver_flat_large", "screwdriver_flat_small", "claw_hammer", "adjustable_wrench", "pliers_slip_joint",
                 "utility_knife", "allen_key_4mm", "allen_key_5mm"]
        if box:
            for t in tools:
                p.inside(box, t, home=box)
        tm_t, tm_p = _foreign(registry, "tape_measure", {"label": "tape measure", "tool_kind": "tape_measure", "category": "measuring_tool",
                                                         "size_m": [0.08, 0.04, 0.08], "mass_kg": 0.35})
        if box:
            p.inside(box, tm_t, home=box, **tm_p)
        spot = _pegboard_spot(p, shop, bench, rng)
        board = p.mount(shop, "pegboard", height=PEG_HEIGHT_M, cell=spot[0], rot=spot[1]) if spot else None
        if board:
            outlines = {}
            for oid, kinds, spos, size in PEG_OUTLINES:
                outlines[oid] = {"tool_kind": kinds, "spos": spos, "size": size}
            config["entities"][board]["outlines"] = outlines
            for oid, kinds, spos, size in PEG_OUTLINES[:2]:
                t = {"hammer": "rubber_mallet", "wrench": "combination_wrench_13mm"}.get(oid)
                if t and rng.random() < 0.6:
                    p.on(board, t, spos=[spos[0] + 0.01, spos[1] + 0.01], home=board)
        shelves = p.find("shelf_unit", shop)
        if shelves:
            sh = shelves[0]
            drill = p.inside(sh, "cordless_drill", shelf=1)
            if drill:
                bt, bp = _foreign(registry, "tool_battery_pack", {"label": "18 V tool battery", "category": "battery", "part_type": "tool_pack_brand_a_18v",
                                                                  "size_m": [0.11, 0.075, 0.07], "mass_kg": 0.6, "charge": 0.8, "secret_fields": []})
                bp["charge"] = round(rng.uniform(0.1, 1.0), 2)
                pack = p.make(bt, drill, **bp)
                config["entities"][pack].update(relation="part", slot="battery")
                config["entities"][drill]["slots"]["battery"]["item"] = pack
                bit = p.make("driver_bit_ph2", drill)
                config["entities"][bit].update(relation="part", slot="bit")
                config["entities"][drill]["slots"]["bit"]["item"] = bit
            organizer = p.inside(sh, "hardware_organizer", shelf=1)
            if organizer:
                for label, kind in (("#8 x 1-1/4 in wood screws", "wood_screw_8x1.25in"), ("#6 x 1-1/4 in drywall screws", "drywall_screw_6x1.25in"),
                                    ("M4 x 16 machine screws", "machine_screw_m4x16"), ("1-1/2 in finishing nails", "finishing_nail_1.5in")):
                    comp = p.make("organizer_compartment", organizer, label="compartment: " + label, holds_kind=kind, label_text=label)
                    config["entities"][comp]["relation"] = "in"
            top = len(config["entities"][sh].get("shelf_heights", [0, 0, 0, 0])) - 1
            for t in ("motor_oil_jug", "washer_fluid_jug", "antifreeze_jug"):
                if rng.random() < 0.7:
                    p.inside(sh, t, shelf=top)
            if rng.random() < 0.5:
                p.inside(sh, "silicone_spray", shelf=top - 1 if top > 0 else 0)
            p.inside(sh, "light_machine_oil", shelf=max(0, top - 1))
            if len(shelves) > 1 and rng.random() < 0.6:
                p.inside(shelves[1], "paint_can", shelf=0)
                p.inside(shelves[1], "spackle_tub", shelf=1)
                p.inside(shelves[1], "putty_knife", shelf=1)
                p.inside(shelves[1], "caulk_gun", shelf=1)
    else:
        box = None
        for closet in p.rooms("utility_closet") + p.rooms("coat_closet"):
            for shelf in p.find("closet_shelving", closet):
                box = box or p.inside(shelf, "toolbox", shelf=0)
            box = box or p.floor_item(closet, "toolbox")
        for kitchen in p.rooms("kitchen"):
            for cab in p.find("cabinet", kitchen):
                box = box or p.inside(cab, "toolbox", shelf=0)
        for hall in p.rooms("hallway") + p.rooms("living_room"):
            box = box or p.floor_item(hall, "toolbox")
        if box:
            for t in ("screwdriver_phillips_2", "screwdriver_flat_large", "claw_hammer", "adjustable_wrench", "pliers_needle_nose", "utility_knife"):
                p.inside(box, t, home=box)
    for kitchen in p.rooms("kitchen"):
        drawers = [d for d in p.find("drawer", kitchen) if config["entities"][d].get("label") == "junk drawer"]
        if drawers:
            p.inside(drawers[0], "screwdriver_phillips_2", home=drawers[0])
            if rng.random() < 0.6:
                p.inside(drawers[0], "adhesive_hook_small")
                p.inside(drawers[0], "adhesive_hook_medium")
            if rng.random() < 0.5:
                p.inside(drawers[0], "felt_pad_25mm")
