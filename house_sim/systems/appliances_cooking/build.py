"""Builders that assemble multi-part appliances into a configuration (used by furnishers, scenario helpers and tests).

All builders take a generator Placer (config dict + registry + rng) and return a dict of ids. They never break doorway
connectivity (Placer checks furniture) and fall back to built-in variants (parts of a counter) when a kitchen is full.

Spatial conventions (documented in docs/domains/appliances_cooking.md):
  * burners are parts of a range/cooktop with z_m at the cooktop; `burner_pos` LF/LR/RF/RR and `center_m` [x, y]
    on the cooktop surface (y grows toward the front edge);
  * a range oven is a part of its range (z_m 0.12, six rack positions = shelf indices 0-5);
  * a dishwasher is built in under a counter (part of the counter, z_m 0); its racks/basket/cup/reservoir/filter/
    sump/gasket are parts or contents with their own z_m;
  * a refrigerator's freezer compartment (bottom drawer or top compartment) is a part of the refrigerator with its own
    z_m (power_from the refrigerator); a gas grill's propane cylinder is a part of the grill (slot "tank", z_m 0.05);
  * floor pieces are placed with the kernel access rule (geometry.access_report / not_worse: nothing that was
    accessible - furniture, fixtures, outlets, radiators, windows, wall cabinets - becomes inaccessible) and never in
    front of / under a mounted thing hung lower than their top (receptacles excepted).
"""
import copy

from ...kernel import geometry
from ...kernel.phys import props as kprops
from . import common as C

RANGE_TYPES = {"gas": "range_gas", "electric_coil": "range_electric", "radiant": "range_radiant", "induction": "range_induction"}
COOKTOP_TYPES = {"gas": "cooktop_gas", "electric_coil": "cooktop_induction", "radiant": "cooktop_induction", "induction": "cooktop_induction"}
POS_NAMES = {"LF": "left front", "LR": "left rear", "RF": "right front", "RR": "right rear"}
CENTERS = {"LF": [0.2, 0.42], "LR": [0.2, 0.15], "RF": [0.56, 0.42], "RR": [0.56, 0.15]}
# (position, size, zone diameter m, rated W, efficiency into the pan)
BURNER_SPECS = {
    "gas": [("LF", "large", 0.23, 3500.0, 0.40), ("LR", "small", 0.15, 1400.0, 0.40), ("RF", "medium", 0.2, 2600.0, 0.40),
            ("RR", "medium", 0.18, 2000.0, 0.40)],
    "electric_coil": [("LF", "large", 0.2, 2600.0, 0.70), ("LR", "small", 0.15, 1500.0, 0.70), ("RF", "large", 0.2, 2600.0, 0.70),
                      ("RR", "small", 0.15, 1500.0, 0.70)],
    "radiant": [("LF", "large", 0.23, 3000.0, 0.70), ("LR", "small", 0.15, 1200.0, 0.70), ("RF", "medium", 0.18, 1800.0, 0.70),
                ("RR", "medium", 0.18, 1800.0, 0.70)],
    "induction": [("LF", "large", 0.23, 3700.0, 0.85), ("LR", "small", 0.16, 1400.0, 0.85), ("RF", "medium", 0.2, 2300.0, 0.85),
                  ("RR", "medium", 0.18, 1850.0, 0.85)],
}
SCALE_OPTIONS = {"gas": C.GAS_LEVELS, "words": C.WORD_LEVELS, "numbers": C.NUMBER_LEVELS, "induction": C.INDUCTION_LEVELS}
DEFAULT_SCALE = {"gas": "gas", "electric_coil": "words", "radiant": "numbers", "induction": "induction"}
OVEN_TEMP_OPTIONS = {"F": {"min": 170, "max": 550}, "C": {"min": 50, "max": 290}}
OVEN_DEFAULT_TEMP = {"F": 350, "C": 180}

# brand panels: canonical program -> label on the panel (icon-only panels show icon names)
DISHWASHER_PANELS = {
    "generic": {"normal": "normal", "heavy": "heavy", "eco": "eco", "quick": "quick", "rinse": "rinse", "sanitize": "sanitize"},
    "brand_a": {"normal": "Normal", "heavy": "Pots & Pans", "eco": "Eco", "quick": "Express 60", "rinse": "Rinse Only", "sanitize": "Sani Rinse"},
    "brand_b": {"normal": "icon:plate", "heavy": "icon:pot", "eco": "icon:leaf", "quick": "icon:clock_1h", "rinse": "icon:drops", "sanitize": "icon:shield"},
    "brand_c": {"normal": "Auto", "heavy": "Intensive 70", "eco": "Eco 50", "quick": "Quick 45", "rinse": "Pre-rinse", "sanitize": "Hygiene"},
}
PANEL_LEGENDS = {
    "brand_b": "Panel legend: plate = Normal (2 h 20), pot = Heavy (2 h 40, 70 C), leaf = Eco (3 h 30, 50 C), "
               "clock 1h = Quick (1 h), drops = Rinse only, shield = Sanitize. Child lock: hold the two left buttons 3 s.",
    "brand_a": "Pots & Pans = heavy soil. Express 60 = lightly soiled, 1 hour. Eco = energy saver (longest). Child lock: hold Heated Dry 3 s.",
    "brand_c": "Auto senses soil. Intensive 70 = baked-on soil. Eco 50 = longest, lowest energy. Quick 45 = light soil. Child lock: hold Start 4 s.",
    "generic": "normal / heavy / eco / quick / rinse / sanitize. Child lock: hold the lock key 3 s.",
}


MOVABLE_WORDS = ("chair", "stool", "bench", "table")
FIXED_WORDS = ("counter", "island", "cabinet", "bedside", "nightstand")
# receptacles normally sit behind the appliance they feed; ceiling lights are overhead
BEHIND_OK = ("outlet", "light")


def _movable(rec):
    t = str(rec.get("type") or "")
    return (any(w in t for w in MOVABLE_WORDS) and not any(w in t for w in FIXED_WORDS)) and not rec.get("fixed") and not rec.get("mounted")


def _wall_touch(x, y, cols, rows, gc, gr):
    if cols > rows:
        return y == 0 or y + rows == gr
    if rows > cols:
        return x == 0 or x + cols == gc
    return x == 0 or y == 0 or x + cols == gc or y + rows == gr


def _hung_low(p, room, height, ignore=()):
    """{cell: [ids]} of mounted things a floor-standing piece `height` m tall would stand in front of or under:
    windows and their coverings, radiators and registers, switches, thermostats, extinguishers, drawer fronts, wall
    cabinets, sinks, hoods... anything hung lower than its top (receptacles excepted)."""
    ents = p.c["entities"]
    out = {}
    for k in sorted(geometry.room_entities(p.state(), room)):
        r = ents[k]
        if k in ignore or not r.get("mounted") or r.get("pos") is None or r.get("connects"):
            continue
        if r.get("category") in BEHIND_OK or C.num(r.get("mount_height_m")) >= height:
            continue
        for cell in geometry.footprint(r):
            out.setdefault(cell, []).append(k)
    return out


def wall_conflicts(p, room, cells, height, ignore=()):
    """Mounted things on `cells` that a floor-standing piece `height` m tall would cover (see _hung_low)."""
    hung = _hung_low(p, room, height, ignore)
    return sorted({k for c in cells for k in hung.get(tuple(c), [])})


def _spots(p, room, rec, wall, near, rots):
    """Candidate (key, rot, x, y) floor positions for rec in room, nearest to `near` first (else seeded random order):
    free of floor-standing things, path blockers, agents and doorway clearance (Placer._occupied, kernel door model),
    and never in front of or under a mounted thing hung lower than the piece's top."""
    gc, gr = geometry.grid_size(p.c["rooms"][room])
    occupied = set(p._occupied(room))
    occupied |= set(_hung_low(p, room, C.num((rec.get("size_m") or [0, 0, 0.9])[2], 0.9)))
    out = []
    for rot in rots:
        probe = dict(rec, rot=rot)
        cols, rows = geometry.footprint_size(probe)
        for y in range(0, gr - rows + 1):
            for x in range(0, gc - cols + 1):
                if any((x + i, y + j) in occupied for i in range(cols) for j in range(rows)):
                    continue
                if wall and not _wall_touch(x, y, cols, rows, gc, gr):
                    continue
                key = geometry.chebyshev((x, y), near) if near is not None else p.rng.random()
                out.append((key, rot, x, y))
    out.sort()
    return out


def access_targets(state, room, level="all", ignore=()):
    """The kernel's access targets plus the room's mounted lights (wall, ceiling and outdoor lights need a bulb change
    now and then: the kernel does not list them, so a floor appliance could otherwise seal one off)."""
    targets = geometry.access_targets(state, room, level=level, ignore=ignore)
    rrec = state["rooms"][room]
    for eid in geometry.room_entities(state, room):
        if eid in ignore:
            continue
        r = state["entities"][eid]
        if r.get("category") != "light" or not r.get("mounted") or not r.get("pos") or "connects" in r:
            continue
        cells = [c for c in geometry.footprint(r) if geometry.in_grid(rrec, c)]
        if cells:
            targets[eid] = (cells, geometry.access_radius(r), False)
    return targets


def access_report(state, room, level="all"):
    """Kernel access report of a room (doorway groups and accessible targets, lights included; agents and loose floor
    items ignored)."""
    blocked = set(geometry.blocked_cells(state, room, include_agents=False))
    return geometry.access_report(state, room, blocked, access_targets(state, room, level=level))


def access_check(p, room, protect="all", ignore=()):
    """fn(cells) -> True when a new path-blocking footprint on `cells` keeps the room "not worse" (kernel
    geometry.not_worse: doorway groups stay joined, everything that was accessible stays accessible) and is itself
    accessible. protect "all": every target the kernel knows (furniture, fixtures, the dock, and every mounted thing:
    outlets, radiators, windows, coverings, wall cabinets...); "basic": doorways, the dock, floor-standing things,
    operable mounts (windows, switches...) and the room's wall storage (drawer fronts, wall cabinets)."""
    state = p.state()
    blocked = set(geometry.blocked_cells(state, room, include_agents=False))
    targets = access_targets(state, room, level="all", ignore=ignore)
    if protect == "basic":
        keep = set(access_targets(state, room, level="basic", ignore=ignore)) | set(C.wall_storage(state, room))
        targets = {k: v for k, v in targets.items() if k in keep}
    before = geometry.access_report(state, room, blocked, targets)

    def check(cells):
        tg = dict(targets)
        tg[("new",)] = ([tuple(c) for c in cells], 1, False)
        after = geometry.access_report(state, room, blocked | {tuple(c) for c in cells}, tg)
        return geometry.not_worse(before, after, new_keys=[("new",)])
    return check


def not_worse_since(p, room, before, new_ids=()):
    """True when the room's access (level "all") is not worse than the `before` report and every new id is accessible."""
    return geometry.not_worse(before, access_report(p.state(), room), new_keys=list(new_ids))


def place_safely(p, room, type_name, eid=None, wall=True, near=None, rot=None, limit=60, free_ok=True, protect=("all", "basic"),
                 **props):
    """A path-blocking floor piece placed like Placer.furniture (kernel "not worse" access rule, full protection first,
    then basic), on a wall spot (else a free-standing one unless free_ok is False) that is not in front of a window,
    radiator, switch or drawer front lower than its top. Returns the id or None (callers then use their
    replace-cabinet / counter-end / built-in fallbacks)."""
    record = p.reg.make(type_name, room, **props)
    size = record.get("size_m", [1, 1])
    rots = [rot] if rot is not None else ([0, 90] if size[0] != size[1] else [0])
    if rot is None and len(rots) == 2 and p.rng.random() < 0.5:
        rots.reverse()
    new_id = eid or p.new_id(type_name)
    walls = ([True, False] if free_ok else [True]) if wall else [False]
    for level in protect:
        check = access_check(p, room, level)
        for use_wall in walls:
            for _, r, x, y in _spots(p, room, record, use_wall, near, rots)[:limit]:
                rec = copy.deepcopy(record)
                rec["rot"], rec["pos"] = r, [x, y]
                if not check(geometry.footprint(rec)):
                    continue
                p.c["entities"][new_id] = rec
                return new_id
    return None


def _counters(p, room):
    return [c for c in p.find("counter", room=room) if p.c["entities"][c].get("location") == room]


# ----------------------------------------------------------------------------------------------
# shelf loads: a base-cabinet shelf holds ~30 kg, an upper-cabinet shelf ~15 kg (record `shelf_load_kg` wins). Stored
# appliances (blenders, slow cookers, pressure cookers ~3-7 kg each) are spread so a shelf stays within STORE_SHARE of
# its rating and still takes a load of dishes afterwards.
SHELF_RATINGS = (("upper_cabinet", 15.0), ("cabinet", 30.0), ("wardrobe", 40.0), ("closet", 40.0), ("shelf_unit", 90.0))
STORE_SHARE = 0.4


def shelf_rating(rec):
    """kg one shelf of a storage piece takes: the record's own shelf_load_kg, else the lower of this domain's table and
    the kernel's nominal shelf rating of the type (phys props, supports.json: what the kernel's overload law holds it
    to; a base-cabinet shelf 30 kg, an upper one 20 kg). With STORE_SHARE (40 %) a stored load stays far below even an
    instance at -15 % of its rating, so furnishing never sets up a shelf the overload law would collapse."""
    if rec.get("shelf_load_kg") is not None:
        return C.num(rec.get("shelf_load_kg"), 30.0)
    kinds = [rec.get("type")] + list(rec.get("is_a") or [])
    value = next((v for name, v in SHELF_RATINGS if name in kinds), 30.0)
    kernel = kprops.props_of(rec).shelf_rating_kg
    if kernel is not None and 0.0 < kernel < value:
        return float(kernel)
    return value


def _kids_index(ents):
    kids = {}
    for k, r in ents.items():
        kids.setdefault(r.get("location"), []).append(k)
    return kids


def config_mass(ents, eid, kids=None):
    """Mass of an entity with everything in/on it (liquids at ~1 kg/L)."""
    kids = _kids_index(ents) if kids is None else kids
    total, stack, seen = 0.0, [eid], set()
    while stack:
        e = stack.pop()
        if e in seen or e not in ents:
            continue
        seen.add(e)
        total += C.num(ents[e].get("mass_kg")) + C.num(ents[e].get("liquid_l"))
        stack.extend(kids.get(e, []))
    return total


def shelf_load(ents, container, shelf):
    """kg standing on one shelf of a container (items `in` it on that shelf, with their contents)."""
    kids = _kids_index(ents)
    return sum(config_mass(ents, k, kids) for k in sorted(kids.get(container, []))
               if ents[k].get("relation") == "in" and int(ents[k].get("shelf", 0) or 0) == int(shelf))


def shelf_with_room(p, container, kg, share=STORE_SHARE):
    """The lowest shelf of a container that can take `kg` more and stay within `share` of its rating, else None."""
    rec = p.c["entities"][container]
    heights = rec.get("shelf_heights") or [0.0]
    limit = share * shelf_rating(rec)
    for shelf in range(len(heights)):
        if shelf_load(p.c["entities"], container, shelf) + kg <= limit + 1e-9:
            return shelf
    return None


def store_in_cabinets(p, cabinets, type_name, extra_kg=0.0, share=STORE_SHARE, **props):
    """Put an item (plus `extra_kg` of parts that go with it) on the first cabinet shelf with load headroom and room
    for it. Returns the new id (its `shelf` tells where the parts go) or None. A corded appliance put away in a
    cabinet is unplugged (its cord is wrapped; nothing routes it across the floor)."""
    kg = C.num((p.reg.resolve(type_name) if p.reg.known(type_name) else {}).get("mass_kg"), 1.0) + float(extra_kg)
    if (p.reg.resolve(type_name) if p.reg.known(type_name) else {}).get("plug_required"):
        props.setdefault("plugged_in", False)
    for cab in cabinets:
        if not p.c["entities"][cab].get("shelf_heights"):
            eid = p.inside(cab, type_name, **props)
        else:
            shelf = shelf_with_room(p, cab, kg, share)
            eid = p.inside(cab, type_name, shelf=shelf, **props) if shelf is not None else None
        if eid:
            return eid
    return None


WALL_THINGS = ("switch", "window", "light", "outlet", "hooks", "register", "vent", "detector", "sensor", "thermostat",
               "blind", "shade", "curtain", "shutter", "sill", "covering", "guide", "sign", "art", "frame", "clock")


def _plumbed_over(p, room, cells):
    """Sinks / built-in cooktops (fixtures a freestanding appliance cannot stand under) mounted over these cells."""
    ents = p.c["entities"]
    out = []
    for k in sorted(ents):
        r = ents[k]
        if r.get("location") != room or not r.get("mounted") or r.get("pos") is None or r.get("connects"):
            continue
        t = str(r.get("type") or "")
        if not ("sink" in t or r.get("drain") or r.get("water_source") or r.get("ac_kind") in ("range", "burner")):
            continue
        if set(geometry.footprint(r)) & set(cells):
            out.append(k)
    return out


def _low_mounts_over(p, room, cells, below_m):
    """Mounted storage on these cells lower than `below_m` that moves with a counter (drawer fronts, wall cabinets,
    shelves; not windows, coverings, radiators, switches or outlets, which belong to the wall)."""
    ents = p.c["entities"]
    out = []
    for k in sorted(ents):
        r = ents[k]
        if r.get("location") != room or not r.get("mounted") or r.get("pos") is None or r.get("connects"):
            continue
        t = (str(r.get("type") or "") + " " + str(r.get("category") or "")).lower()
        if any(w in t for w in WALL_THINGS) or C.num(r.get("mount_height_m")) >= below_m or r.get("climate_kind"):
            continue
        if not (r.get("container") or r.get("surface") or r.get("openable")):
            continue
        if tuple(r["pos"]) in set(cells):
            out.append(k)
    return out


def _free_cabinets(p, room, height=0.9):
    """Empty base cabinets with nothing mounted over them lower than `height` (no sink, cooktop, drawer front, window,
    radiator or switch a freestanding appliance would cover)."""
    ents = p.c["entities"]
    out = []
    for cab in p.find("cabinet", room=room):
        rec = ents[cab]
        if rec.get("type") != "cabinet" or rec.get("location") != room or not rec.get("pos"):
            continue
        cells = geometry.footprint(rec)
        if any(r.get("location") == cab for r in ents.values()) or _plumbed_over(p, room, cells):
            continue
        if wall_conflicts(p, room, cells, height):
            continue
        out.append(cab)
    return out


def _replace_cabinet(p, room, type_name, eid=None, **props):
    """Kitchens that are full: a freestanding appliance replaces an empty base cabinet of the same footprint (never one
    with a sink, a drawer front or a window over it). The swap keeps the blocked cells, so routes stay as they were;
    the appliance must itself be accessible."""
    record = p.reg.make(type_name, room, **props)
    height = C.num((record.get("size_m") or [0, 0, 0.9])[2], 0.9)
    before = access_report(p.state(), room)
    for cab in _free_cabinets(p, room, height):
        rec = copy.deepcopy(record)
        old = p.c["entities"].pop(cab)
        rec["pos"] = list(old["pos"])
        rec["rot"] = old.get("rot", 0)
        ocols, orows = geometry.footprint_size(old)
        if geometry.footprint_size(rec) != (ocols, orows):
            rec["rot"] = 90 - rec["rot"] if rec["rot"] in (0, 90) else rec["rot"]
        new_id = eid or p.new_id(type_name)
        if geometry.footprint_size(rec) == (ocols, orows):
            p.c["entities"][new_id] = rec
            if not_worse_since(p, room, dict(before, ok=before["ok"] - {cab}), [new_id]):
                return new_id
            del p.c["entities"][new_id]
        p.c["entities"][cab] = old
    return None


def _surfaces_for_overflow(p, room, skip):
    """Other counters/tables of the room (then of adjoining indoor rooms) that can take items moved off a counter."""
    ents = p.c["entities"]
    rooms = [room] + sorted({r for conn in p.c.get("connections", {}).values() if room in (conn.get("rooms") or [])
                             for r in conn["rooms"] if r != room and not p.c["rooms"].get(r, {}).get("outdoor")})
    out = []
    for rid in rooms:
        for k in sorted(ents):
            r = ents[k]
            if k == skip or r.get("location") != rid or not r.get("surface") or r.get("ac_kind"):
                continue
            t = str(r.get("type") or "")
            if any(w in t for w in ("counter", "island", "table", "cart", "shelf", "sideboard", "buffet")) and "bedside" not in t and "night" not in t:
                out.append(k)
    return out


def _respot_or_move(p, room, counter, kids, shift_m, axis=0):
    """After a counter was shortened, keep its loose items on it (shifted), else on another surface or in a base
    cabinet; items with nowhere to go are dropped. Returns {item: saved fields} for undo, and the dropped records."""
    ents = p.c["entities"]
    saved, dropped = {}, {}
    order = sorted(kids, key=lambda k: (-(geometry.footprint_m(ents[k])[0] * geometry.footprint_m(ents[k])[1]), k))
    for k in order:
        rec = ents[k]
        saved[k] = {f: copy.deepcopy(rec.get(f)) for f in ("location", "relation", "spos", "shelf")}
        rec.pop("spos", None)
    for k in order:
        rec = ents[k]
        old = saved[k]["spos"]
        want = None
        if old:
            want = [float(old[0]), float(old[1])]
            want[axis] = round(want[axis] - shift_m, 3)
        spot = geometry.surface_spot(p.state(), counter, k, want=want) if want and min(want) >= 0 else None
        spot = spot or geometry.surface_spot(p.state(), counter, k)
        if spot is not None:
            rec["spos"] = spot
            continue
        placed = False
        for s in _surfaces_for_overflow(p, room, counter):
            rec["location"], rec["relation"] = s, "on"
            spot = geometry.surface_spot(p.state(), s, k)
            if spot is not None:
                rec["spos"] = spot
                placed = True
                break
        if not placed:
            for cab in p.find("cabinet", room=room):
                crec = ents[cab]
                if crec.get("location") != room or not geometry.fits_inside(rec.get("size_m") or [0.1, 0.1, 0.1], crec.get("inner_size_m")):
                    continue
                shelf = shelf_with_room(p, cab, config_mass(ents, k), share=0.8) if crec.get("shelf_heights") else None
                if crec.get("shelf_heights") and shelf is None:
                    continue
                rec["location"], rec["relation"] = cab, "in"
                rec.pop("spos", None)
                if shelf is not None:
                    rec["shelf"] = shelf
                placed = True
                break
        if not placed:
            doomed = [k] + [d for d in sorted(ents) if d != k and k in _chain(ents, d)]
            for d in doomed:
                dropped[d] = ents.pop(d)
    return saved, dropped


def _chain(ents, eid):
    out, cur, seen = [], ents.get(eid, {}).get("location"), set()
    while cur in ents and cur not in seen:
        seen.add(cur)
        out.append(cur)
        cur = ents[cur].get("location")
    return out


def _counter_end(p, room, type_name, eid=None, **props):
    """Kitchens with no free wall: the appliance takes the end of a counter run (the counter gets shorter, keeping at
    least 1 m). The appliance occupies exactly the cells the counter gave up, so routes and approaches stay as they
    were (checked with the kernel access rule); never under a sink, a built-in cooktop or in front of a window,
    radiator or switch; drawer fronts and wall cabinets move along with the counter. Loose items move along or onto
    other surfaces. Returns the id."""
    ents = p.c["entities"]
    record = p.reg.make(type_name, room, **props)
    height = C.num((record.get("size_m") or [0, 0, 1.0])[2], 1.0)
    before = access_report(p.state(), room)

    def info(cid):
        crec = ents[cid]
        cols, rows = geometry.footprint_size(crec)
        has_parts = any(r.get("location") == cid and r.get("relation") == "part" for r in ents.values())
        return (has_parts, -max(cols, rows), cid)

    candidates = [c for c in _counters(p, room) if ents[c].get("pos") and ents[c].get("rot", 0) in (0, 90, 180, 270)]
    for cid in sorted(candidates, key=info):
        crec = ents[cid]
        cols, rows = geometry.footprint_size(crec)
        along_x = cols >= rows
        length, depth = (cols, rows) if along_x else (rows, cols)
        size = list(crec.get("size_m") or [2.0, 0.6, 0.9])
        rot_c = crec.get("rot", 0)
        idx = 0 if (along_x == (rot_c in (0, 180))) else 1          # size_m index running along the long grid axis
        for rot in (0, 90):
            rec = dict(copy.deepcopy(record), rot=rot)
            ac, ar = geometry.footprint_size(rec)
            aw, ad = (ac, ar) if along_x else (ar, ac)
            if ad != depth or length - aw < 2 or float(size[idx]) - aw * geometry.CELL < 0.9:
                continue
            for cut_start in (False, True):
                x0, y0 = int(crec["pos"][0]), int(crec["pos"][1])
                if along_x:
                    app_pos = [x0, y0] if cut_start else [x0 + length - aw, y0]
                    new_pos = [x0 + aw, y0] if cut_start else [x0, y0]
                else:
                    app_pos = [x0, y0] if cut_start else [x0, y0 + length - aw]
                    new_pos = [x0, y0 + aw] if cut_start else [x0, y0]
                rec["pos"] = list(app_pos)
                cut = set(geometry.footprint(rec))
                if _plumbed_over(p, room, cut):
                    continue
                if set(wall_conflicts(p, room, cut, height)) - set(_low_mounts_over(p, room, cut, height)):
                    continue      # a window, radiator or switch on that wall stays where it is
                # apply: shorter counter, appliance at its end, loose items re-spotted, drawer fronts moved along
                saved_counter = copy.deepcopy(crec)
                new_len = round(float(size[idx]) - aw * geometry.CELL, 3)
                crec["size_m"] = list(size)
                crec["size_m"][idx] = new_len
                if crec.get("surface_size_m"):
                    ss = list(crec["surface_size_m"])
                    ss[idx] = round(max(0.3, float(ss[idx]) - aw * geometry.CELL), 3)
                    crec["surface_size_m"] = ss
                crec["pos"] = list(new_pos)
                kept = set(geometry.footprint(crec))
                kids = [k for k in sorted(ents) if ents[k].get("location") == cid and ents[k].get("relation") == "on"]
                shift = aw * geometry.CELL if cut_start else 0.0
                saved_kids, dropped = _respot_or_move(p, room, cid, kids, shift, axis=idx)
                moved_mounts = {}
                free_kept = sorted(c for c in kept if not _low_mounts_over(p, room, [c], height))
                for m in _low_mounts_over(p, room, cut, height):
                    if free_kept:
                        moved_mounts[m] = list(ents[m]["pos"])
                        ents[m]["pos"] = list(free_kept.pop(0))
                new_id = eid or p.new_id(type_name)
                ents[new_id] = rec
                if not wall_conflicts(p, room, cut, height) and not_worse_since(p, room, before, [new_id]):
                    return new_id
                # undo
                del ents[new_id]
                ents[cid] = saved_counter
                crec = ents[cid]
                ents.update(dropped)
                for k, fields in saved_kids.items():
                    for f, v in fields.items():
                        if v is None:
                            ents[k].pop(f, None)
                        else:
                            ents[k][f] = v
                for m, pos in moved_mounts.items():
                    ents[m]["pos"] = pos
    return None


def _move_out_movables(p, room):
    """Move the room's movable furniture (dining table, chairs, stools) into adjoining indoor rooms, keeping those
    rooms' doorways and furniture reachable. Returns {id: old record} for undo."""
    ents = p.c["entities"]
    saved = {}
    movable = sorted((e for e in geometry.room_entities(p.state(), room) if geometry.blocks(ents[e]) and _movable(ents[e])),
                     key=lambda e: (-len(geometry.footprint(ents[e])), e))
    others = sorted({r for conn in p.c.get("connections", {}).values() if room in (conn.get("rooms") or [])
                     for r in conn["rooms"] if r != room and not p.c["rooms"].get(r, {}).get("outdoor")})
    for eid in movable:
        rec = ents[eid]
        cols, rows = geometry.footprint_size(rec)
        for other in others:
            spot = p._free_spot(other, cols, rows, wall=False)
            if spot is not None:
                saved[eid] = copy.deepcopy(rec)
                ents[eid] = dict(rec, location=other, pos=spot)
                break
    return saved


def place_floor_appliance(p, room, type_name, eid=None, near=None, **props):
    """A floor-standing appliance (fridge, range, freezer) in any generated layout, never making the room's access
    worse (kernel rule) and always against a wall where one can take it: a wall spot that keeps everything accessible
    (furniture, fixtures, outlets, radiators, windows, wall cabinets); else an empty base cabinet's place or the end of
    a counter run (both keep the blocked cells as they were); else a wall spot that keeps the basic access (doorways,
    dock, floor-standing things, operable mounts, wall storage); else the dining table and chairs go to the next room
    and it tries again; a free-standing spot (an island position) is the last resort. Returns the id or None (the
    room truly has no space)."""
    def attempt():
        return (place_safely(p, room, type_name, eid=eid, wall=True, free_ok=False, near=near, protect=("all",), **props)
                or _replace_cabinet(p, room, type_name, eid=eid, **props)
                or _counter_end(p, room, type_name, eid=eid, **props)
                or place_safely(p, room, type_name, eid=eid, wall=True, free_ok=False, near=near, protect=("basic",), **props))
    out = attempt()
    if out is None:
        saved = _move_out_movables(p, room)
        if saved:
            out = attempt()
            if out is None:
                p.c["entities"].update(saved)
    if out is None:
        out = place_safely(p, room, type_name, eid=eid, wall=False, near=near, **props)
    return out


def burner_props(kind, code, size, diameter, watts, eff, scale=None):
    scale = scale or DEFAULT_SCALE[kind]
    options = list(SCALE_OPTIONS[scale])
    material = {"gas": "cast_iron", "electric_coil": "steel", "radiant": "glass_ceramic", "induction": "glass_ceramic"}[kind]
    return {"label": "{} burner".format(POS_NAMES[code]) if kind != "induction" else "{} zone".format(POS_NAMES[code]),
            "burner_pos": code, "center_m": list(CENTERS[code]), "material": material,
            "settings": {"heat": "off"}, "setting_options": {"heat": options},
            "burner": {"kind": kind, "size": size, "diameter_m": diameter, "max_w": watts, "efficiency": eff,
                       "igniter_ok": True, "cap_seated": True, "lit": False, "flame": None, "error": None,
                       "residual_hot": False, "scale": scale}}


def build_range(p, room, kind="gas", unit="F", near=None, counter=None, oven=True, hood=True, builtin=False, eid=None,
                oven_props=None, scale=None, burner_overrides=None):
    """Range (or built-in cooktop) with 4 burners, an oven with 2 racks and a hood. Returns ids."""
    out = {"room": room, "kind": kind}
    rid = None
    if not builtin:
        rid = place_floor_appliance(p, room, RANGE_TYPES[kind], eid=eid, near=near)
    z_top = 0.92
    if rid is None:
        counters = [counter] if counter else _counters(p, room)
        counters = [c for c in counters if c and not any(r.get("location") == c and r.get("ac_kind") == "range"
                                                           for r in p.c["entities"].values())]
        if not counters:
            return None
        counter = counters[0]
        rid = p.make(COOKTOP_TYPES[kind], counter, eid=eid, relation="part")
        crec = p.c["entities"][rid]
        crec["relation"] = "part"
        z_top = C.num(p.c["entities"][counter].get("surface_height_m"), 0.9)
        crec["z_m"] = z_top
        crec["cooktop"] = dict(crec.get("cooktop", {}), kind=kind)
        out["builtin_counter"] = counter
    rrec = p.c["entities"][rid]
    rrec["cooktop"] = dict(rrec.get("cooktop", {}), kind=kind, scale=scale or DEFAULT_SCALE[kind])
    out["range"] = rid
    burners = []
    for code, size, diameter, watts, eff in BURNER_SPECS[kind]:
        props = burner_props(kind, code, size, diameter, watts, eff, scale)
        if burner_overrides and code in burner_overrides:
            over = copy.deepcopy(burner_overrides[code])
            props["burner"].update(over.pop("burner", {}))
            props.update(over)
        bid = p.make("burner", rid, eid="{}_{}".format(rid, code.lower()), **props)
        brec = p.c["entities"][bid]
        brec["relation"] = "part"
        brec["z_m"] = z_top + (0.0 if out.get("builtin_counter") else 0.0)
        burners.append(bid)
        out["burner_" + code.lower()] = bid
    out["burners"] = burners
    if oven:
        props = {"settings": {"mode": "off", "temp": OVEN_DEFAULT_TEMP[unit], "light": "off", "clock": "10:00"},
                 "setting_options": {"mode": ["off", "bake", "convection_bake", "broil", "roast", "keep_warm", "proof", "self_clean"],
                                     "temp": dict(OVEN_TEMP_OPTIONS[unit]), "light": ["off", "on"]}}
        props["oven"] = dict(p.reg.resolve("range_oven")["oven"], unit=unit, fuel="gas" if kind == "gas" else "electric")
        if oven_props:
            extra = copy.deepcopy(oven_props)
            props["oven"].update(extra.pop("oven", {}))
            props.update(extra)
        if out.get("builtin_counter"):
            oid = p.make("range_oven", out["builtin_counter"], eid=rid + "_oven", label="wall oven", **props)
        else:
            oid = p.make("range_oven", rid, eid=rid + "_oven", **props)
        orec = p.c["entities"][oid]
        orec["relation"] = "part"
        orec["z_m"] = 0.12
        orec["display"] = "10:00"
        out["oven"] = oid
        racks = []
        for shelf in (1, 3):
            rk = p.inside(oid, "oven_rack", shelf=shelf, eid="{}_rack_{}".format(oid, shelf + 1))
            if rk:
                racks.append(rk)
        out["racks"] = racks
        out["rack_low"] = racks[0] if racks else None
        out["rack_mid"] = racks[-1] if racks else None
    if hood and not out.get("builtin_counter"):
        pos = p.c["entities"][rid].get("pos")
        hid = p.mount(room, "range_hood", cell=pos, height=1.62)
        p.c["entities"][hid]["over"] = rid
        out["hood"] = hid
        clear_hood_wall(p, room, hid)
    return out


def _band(rec):
    """(bottom, top, depth) of a mounted record in metres."""
    size = rec.get("size_m") or [0.1, 0.1, 0.1]
    h = C.num(rec.get("mount_height_m", rec.get("z_m", 0.0)))
    return h, h + C.num(size[2], 0.1), min(C.num(size[0], 0.1), C.num(size[1], 0.1))


def clear_hood_wall(p, room, hid):
    """No wall cabinet or shelf hangs where the hood is: a deep wall mount sharing the hood's wall cells at overlapping
    heights (an upper cabinet the range was put under) moves, with its contents, to the nearest free wall cell the robot
    can reach (Placer's mount rules: no doorway clearance, nothing taller standing in front, no other deep mount there);
    with no free wall it is raised to sit just above the hood. Returns the ids moved."""
    ents = p.c["entities"]
    hood = ents[hid]
    hb, ht, _ = _band(hood)
    hcells = set(geometry.footprint(hood))
    moved = []
    for k in sorted(geometry.room_entities(p.state(), room)):
        r = ents[k]
        if k == hid or not r.get("mounted") or not r.get("pos") or r.get("ground_layer") or "connects" in r:
            continue
        if r.get("category") in ("outlet", "light", "window", "drawer") or geometry.floor_standing(r):
            continue
        rb, rt, depth = _band(r)
        if depth < 0.1 or not (rb < ht and hb < rt) or not (set(geometry.footprint(r)) & hcells):
            continue
        old = list(r["pos"])
        pos = p._mount_cell(room, r, near=old) or p._mount_cell(room, r, relaxed=True, near=old)
        if pos is not None and not (set(geometry.footprint(dict(r, pos=pos))) & hcells):
            r["pos"] = list(pos)
        else:
            r["mount_height_m"] = round(ht + 0.02, 2)
        moved.append(k)
    return moved


def build_fridge(p, room, style="bottom_freezer", near=None, eid=None, frost_free=True, props=None):
    """Refrigerator + freezer compartment, door bins, crisper drawers and gaskets."""
    type_name = "top_freezer_refrigerator" if style == "top_freezer" else "refrigerator"
    extra = copy.deepcopy(props or {})
    fid = place_floor_appliance(p, room, type_name, eid=eid, near=near, **extra)
    if fid is None:
        return None
    frec = p.c["entities"][fid]
    out = {"fridge": fid, "room": room}
    if style != "single":
        # the freezer compartment is a part of the refrigerator cabinet (its own door/drawer, air and compressor
        # duty, powered through the refrigerator): z_m is its floor height in the cabinet
        height = 1.3 if style == "top_freezer" else 0.05
        zid = p.make("freezer_drawer", fid, eid=fid + "_freezer", label="freezer compartment" if style == "top_freezer" else "freezer drawer")
        zrec = p.c["entities"][zid]
        zrec.update(relation="part", z_m=height, power_from=fid)
        for key in ("pos", "rot", "mounted", "mount_height_m"):
            zrec.pop(key, None)
        zrec["fridge"] = dict(zrec["fridge"], frost_free=frost_free)
        if style == "top_freezer":
            zrec["size_m"] = [0.84, 0.7, 0.45]
            zrec["inner_size_m"] = [0.68, 0.55, 0.3]
            zrec["capacity_l"] = 110.0
        out["freezer"] = zid
        g2 = p.make("door_gasket", zid, eid=zid + "_gasket", label="freezer door gasket")
        p.c["entities"][g2]["relation"] = "part"
        out["freezer_gasket"] = g2
    heights = frec.get("shelf_heights") or [0.0]
    bins = []
    for i, shelf in enumerate((min(1, len(heights) - 1), len(heights) - 1)):
        b = p.inside(fid, "fridge_door_bin", shelf=shelf, eid="{}_door_bin_{}".format(fid, i + 1),
                     label="fridge door bin ({})".format("lower" if i == 0 else "upper"))
        if b:
            bins.append(b)
    out["door_bins"] = bins
    out["door_bin"] = bins[0] if bins else None
    hi = p.inside(fid, "crisper_drawer", shelf=0, eid=fid + "_crisper_1", label="crisper drawer (high humidity)", humidity="high",
                  settings={"humidity": "high"})
    lo = p.inside(fid, "crisper_drawer", shelf=0, eid=fid + "_crisper_2", label="crisper drawer (low humidity)", humidity="low",
                  settings={"humidity": "low"})
    out["crisper"], out["crisper_low"] = hi, lo
    # the plastic bins start at the compartment's air temperature (they are not heat sources: their contents sit in
    # the fridge air, which the kernel finds by walking up through them)
    inside_c = C.num(frec.get("internal_temp_c"), 3.5)
    for part in bins + [x for x in (hi, lo) if x]:
        p.c["entities"][part]["temperature_c"] = inside_c
    gid = p.make("door_gasket", fid, eid=fid + "_gasket", label="refrigerator door gasket")
    p.c["entities"][gid]["relation"] = "part"
    out["gasket"] = gid
    return out


def build_freezer(p, room, type_name="chest_freezer", eid=None, frost_mm=0.0, frost_free=None, near=None):
    zid = place_floor_appliance(p, room, type_name, eid=eid, near=near)
    if zid is None:
        return None
    rec = p.c["entities"][zid]
    rec["frost_mm"] = float(frost_mm)
    if frost_free is not None:
        rec["fridge"] = dict(rec["fridge"], frost_free=bool(frost_free))
    gid = p.make("door_gasket", zid, eid=zid + "_gasket", label=("lid" if type_name == "chest_freezer" else "door") + " gasket")
    p.c["entities"][gid]["relation"] = "part"
    return {"freezer": zid, "gasket": gid, "room": room}


def build_dishwasher(p, room, counter=None, brand="generic", drawer=False, eid=None, rinse_aid_l=0.06, filter_soil=None,
                     program=None):
    """Built-in dishwasher under a counter with racks, basket, detergent cup, rinse-aid reservoir, filter, sump, gasket."""
    counters = [counter] if counter else _counters(p, room)
    ents = p.c["entities"]
    counters = [c for c in counters if c and not any(r.get("location") == c and r.get("ac_kind") in ("dishwasher", "range")
                                                       for r in ents.values())]
    if not counters:
        return None
    counter = counters[-1]
    type_name = "dishwasher_drawer" if drawer else "dishwasher"
    dw = p.make(type_name, counter, eid=eid)
    rec = ents[dw]
    rec.update(relation="part", z_m=0.0, blocks_path=False, built_in=True)
    labels = DISHWASHER_PANELS.get(brand, DISHWASHER_PANELS["generic"])
    rec["brand"] = brand
    if brand != "generic":
        base = rec["appliance"]["programs"]
        rec["appliance"] = dict(rec["appliance"], programs={labels[k]: dict(v, canonical=k) for k, v in base.items()},
                                program=labels["normal"])
        rec["settings"] = dict(rec["settings"], program=labels["normal"])
        rec["setting_options"] = dict(rec["setting_options"], program=[labels[k] for k in ("normal", "heavy", "eco", "quick", "rinse", "sanitize")])
        rec["_program_meaning"] = {labels[k]: k for k in labels}
    else:
        rec["appliance"] = dict(rec["appliance"], programs={k: dict(v, canonical=k) for k, v in rec["appliance"]["programs"].items()})
        rec["_program_meaning"] = {k: k for k in labels}
    rec["instructions"] = PANEL_LEGENDS.get(brand, PANEL_LEGENDS["generic"])
    if program:
        rec["settings"]["program"] = program
        rec["appliance"]["program"] = program
    out = {"dishwasher": dw, "counter": counter}
    bottom = p.make("dishwasher_rack_bottom", dw, eid=dw + "_rack_bottom")
    ents[bottom].update(relation="in", z_m=0.1)
    out["rack_bottom"] = bottom
    if not drawer:
        top = p.make("dishwasher_rack_top", dw, eid=dw + "_rack_top")
        ents[top].update(relation="in", z_m=0.42)
        out["rack_top"] = top
    else:
        out["rack_top"] = bottom
    basket = p.make("dishwasher_utensil_basket", bottom, eid=dw + "_basket")
    ents[basket].update(relation="in", z_m=0.13, rack_pos="front")
    out["basket"] = basket
    cup = p.make("detergent_cup", dw, eid=dw + "_detergent_cup")
    ents[cup].update(relation="part", z_m=0.3)
    out["detergent_cup"] = cup
    res = p.make("rinse_aid_reservoir", dw, eid=dw + "_rinse_aid", liquid_l=float(rinse_aid_l))
    ents[res].update(relation="part", z_m=0.3)
    out["rinse_aid"] = res
    sump = p.make("dishwasher_sump", dw, eid=dw + "_sump")
    ents[sump].update(relation="part", z_m=0.05)
    out["sump"] = sump
    flt = p.make("dishwasher_filter", dw, eid=dw + "_filter", soil=dict(filter_soil or {"food": 0.1, "grease": 0.05}))
    ents[flt].update(relation="part", z_m=0.06, slot="filter")
    rec["slots"] = {"filter": {"accepts": ["dishwasher_filter"], "item": flt, "access": "open"}}
    out["filter"] = flt
    gid = p.make("door_gasket", dw, eid=dw + "_gasket", label="dishwasher door gasket")
    ents[gid]["relation"] = "part"
    ents[gid]["z_m"] = 0.4
    out["gasket"] = gid
    return out


def on_counter(p, counter, type_name, eid=None, spos=None, **props):
    return p.on(counter, type_name, eid=eid, spos=spos, **props)


def build_kettle(p, counter, eid=None, liquid_l=0.0, spos=None):
    base = p.on(counter, "kettle_base", eid=(eid + "_base") if eid else None, spos=spos)
    if base is None:
        return None
    kettle = p.on(base, "electric_kettle", eid=eid, liquid_l=float(liquid_l))
    if kettle is None:
        del p.c["entities"][base]
        return None
    return {"kettle": kettle, "kettle_base": base}


def build_blender(p, counter, eid=None, spos=None):
    base = p.on(counter, "blender", eid=eid, spos=spos)
    if base is None:
        return None
    jar = p.on(base, "blender_jar", eid=base + "_jar")
    return {"blender": base, "blender_jar": jar}


def build_coffee_maker(p, counter, eid=None, spos=None, water_l=0.0):
    cm = p.on(counter, "drip_coffee_maker", eid=eid, spos=spos, liquid_l=float(water_l))
    if cm is None:
        return None
    basket = p.make("coffee_filter_basket", cm, eid=cm + "_basket")
    p.c["entities"][basket]["relation"] = "part"
    carafe = p.on(cm, "coffee_carafe", eid=cm + "_carafe")
    return {"coffee_maker": cm, "brew_basket": basket, "carafe": carafe}


def build_air_fryer(p, counter, eid=None, spos=None):
    af = p.on(counter, "air_fryer", eid=eid, spos=spos)
    if af is None:
        return None
    basket = p.make("air_fryer_basket", af, eid=af + "_basket")
    p.c["entities"][basket]["relation"] = "in"
    return {"air_fryer": af, "basket": basket}


def build_slow_cooker(p, counter, eid=None, spos=None, insert_in=True):
    sc = p.on(counter, "slow_cooker", eid=eid, spos=spos)
    if sc is None:
        return None
    out = {"slow_cooker": sc}
    if insert_in:
        out["insert"] = p.on(sc, "slow_cooker_insert", eid=sc + "_insert")
    return out


def build_pressure_cooker(p, counter, eid=None, spos=None):
    pc = p.on(counter, "electric_pressure_cooker", eid=eid, spos=spos)
    if pc is None:
        return None
    pot = p.make("pressure_cooker_pot", pc, eid=pc + "_pot")
    p.c["entities"][pot]["relation"] = "in"
    ring = p.make("sealing_ring", pc, eid=pc + "_ring")
    p.c["entities"][ring].update(relation="part", slot="seal")
    p.c["entities"][pc]["slots"] = {"seal": {"accepts": ["sealing_ring"], "item": ring, "access": "open"}}
    return {"pressure_cooker": pc, "pot": pot, "sealing_ring": ring}


def build_rice_cooker(p, counter, eid=None, spos=None):
    rc = p.on(counter, "rice_cooker", eid=eid, spos=spos)
    if rc is None:
        return None
    pot = p.make("rice_cooker_pot", rc, eid=rc + "_pot")
    p.c["entities"][pot]["relation"] = "in"
    return {"rice_cooker": rc, "pot": pot}


def build_espresso(p, counter, eid=None, spos=None, water_l=1.5):
    em = p.on(counter, "espresso_machine", eid=eid, spos=spos, liquid_l=float(water_l))
    if em is None:
        return None
    pf = p.make("portafilter", em, eid=em + "_portafilter")
    p.c["entities"][pf].update(relation="part", slot="group")
    p.c["entities"][em]["slots"] = {"group": {"accepts": ["portafilter"], "item": pf, "access": "none"}}
    return {"espresso": em, "portafilter": pf}


def build_food_processor(p, cabinet_or_counter, eid=None, assembled=False, shelf=None):
    parent = p.c["entities"][cabinet_or_counter]
    stored = not (parent.get("surface") and not parent.get("container"))
    make = p.on if not stored else (lambda loc, t, **kw: p.inside(loc, t, shelf=shelf, **kw))
    fp = make(cabinet_or_counter, "food_processor", eid=eid, **({"plugged_in": False} if stored else {}))
    if fp is None:
        return None
    bowl = p.make("processor_bowl", fp, eid=fp + "_bowl")
    blade = p.make("processor_s_blade", fp, eid=fp + "_blade")
    lid = p.make("processor_lid", fp, eid=fp + "_lid")
    ents = p.c["entities"]
    if assembled:
        ents[bowl].update(relation="part", slot="bowl")
        ents[fp]["slots"]["bowl"]["item"] = bowl
        ents[blade].update(location=bowl, relation="part", slot="blade")
        ents[lid].update(location=bowl, relation="part", slot="lid")
        ents[bowl]["slots"]["blade"]["item"] = blade
        ents[bowl]["slots"]["lid"]["item"] = lid
    else:
        for part in (bowl, blade, lid):
            ents[part].update(location=cabinet_or_counter, relation="in" if parent.get("container") else "on")
            if ents[part]["relation"] == "in" and ents[fp].get("shelf") is not None:
                ents[part]["shelf"] = ents[fp]["shelf"]
            if ents[part]["relation"] == "on":
                spot = geometry.surface_spot(p.state(), cabinet_or_counter, part)
                if spot is None:
                    ents[part].update(location=fp, relation="part")
                else:
                    ents[part]["spos"] = spot
    return {"food_processor": fp, "bowl": bowl, "blade": blade, "lid": lid}


def build_stand_mixer(p, counter, eid=None, spos=None):
    mx = p.on(counter, "stand_mixer", eid=eid, spos=spos)
    if mx is None:
        return None
    bowl = p.on(mx, "mixer_bowl", eid=mx + "_bowl")
    paddle = p.make("mixer_paddle", mx, eid=mx + "_paddle")
    p.c["entities"][paddle].update(relation="part", slot="attachment")
    p.c["entities"][mx]["slots"] = {"attachment": {"accepts": ["mixer_attachment"], "item": paddle, "access": "none", "power_off_first": True}}
    out = {"mixer": mx, "bowl": bowl, "paddle": paddle}
    return out


def build_grill(p, room, kind="gas", near=None, eid=None):
    type_name = "gas_grill" if kind == "gas" else "charcoal_grill"
    gid = place_safely(p, room, type_name, eid=eid, wall=False, near=near)
    if gid is None:
        return None
    out = {"grill": gid, "room": room}
    if kind == "gas":
        # the 20 lb cylinder stands in the grill cart's tank holder under the side shelf, hose-connected: a part of the
        # grill (slot "tank", visible with the lid closed, swapped with install/uninstall), never a loose floor item
        tank = p.make("propane_tank", gid, eid=gid + "_tank", connected_to=gid)
        p.c["entities"][tank].update(relation="part", slot="tank", z_m=0.05)
        grec = p.c["entities"][gid]
        grec["slots"] = dict(grec.get("slots") or {}, tank={"accepts": ["propane_tank"], "item": tank, "access": "none"})
        grec["tank"] = tank
        out["tank"] = tank
    return out

