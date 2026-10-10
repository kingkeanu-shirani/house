"""Scenario helpers (setup op {"op": "helper", "name": "cleaning.<name>", "args": {...}}).

They edit the generated house config before the episode starts and return bindings.
  cleaning.soil        put soil/contamination on an entity (whole, per face, or under items)
  cleaning.floor       put soil on floor cells (cells, a zone, under a piece of furniture, random spots)
  cleaning.spill       create a liquid spill on a floor cell or a surface
  cleaning.waste       spawn waste/look-alike items (preset mixes or explicit list) and bind them by true stream
  cleaning.rules       set the household's local waste rules (preset/random) and the posted guide text
  cleaning.bucket      a mop bucket with water + cleaner (dose factor, dirtiness)
  cleaning.basin       fill a sink basin with (soapy) water
  cleaning.tools       spawn cleaning tools/products on a surface or the floor; binds {type: id}
  cleaning.liner       install a liner in a waste container (or leave it empty)
  cleaning.room_mess   realistic mixed mess for a room (dust, crumbs, under-furniture dust, fingerprints)
"""
import copy

from ...kernel import geometry
from ...kernel.errors import SpecError
from ...kernel.plugins import scenario_helper
from ...kernel.world import World
from . import model as M
from . import state as S


class _View:
    """Read-only World-like view over a config (for layout and stream logic at setup time)."""

    def __init__(self, ctx):
        state = ctx.placer.state()
        state["household"] = ctx.config.get("household", {})
        self.s = state
        self.w = World(state, ctx.reg)


def _aggregate(record):
    agg = {}
    for key in ("soil_faces", "_shadows", "_cells"):
        for part in (record.get(key) or {}).values():
            for kind, level in part.items():
                agg[kind] = max(agg.get(kind, 0.0), float(level))
    record["soil"] = {k: round(v, 4) for k, v in sorted(agg.items()) if v > 0.0005}
    record["_soil_sync"] = dict(record["soil"])
    if isinstance(record.get("_cells"), dict):
        counts = {}
        for part in record["_cells"].values():
            for kind, level in part.items():
                if level >= M.CLEAN:
                    counts[kind] = counts.get(kind, 0) + 1
        record["_cell_counts"] = dict(sorted(counts.items()))


def _room(ctx, value):
    if value in ctx.config["rooms"]:
        return value
    rooms = ctx.placer.rooms(value)
    if not rooms:
        raise SpecError("No room of type " + str(value))
    return ctx.rng.choice(rooms)


def _entity(ctx, eid):
    if eid not in ctx.config["entities"]:
        raise SpecError("Unknown entity " + str(eid))
    return ctx.config["entities"][eid]


@scenario_helper("cleaning.soil")
def soil_helper(ctx, target, soil=None, faces=None, face=None, contamination=None, cont_faces=None, wetness=None, under_items=False, props=None):
    rec = _entity(ctx, target)
    declared = list(rec.get("faces") or [])
    if faces or (declared and soil) or under_items:
        sf = rec.setdefault("soil_faces", {})
        for f in declared:
            sf.setdefault(f, {})
        base = rec.get("soil") if isinstance(rec.get("soil"), dict) else {}
        if base and not any(sf.values()):
            sf.setdefault(declared[0] if declared else "main", {}).update(base)
        if soil:
            sf.setdefault(face or (declared[0] if declared else "main"), {}).update({k: float(v) for k, v in soil.items()})
        for name, levels in (faces or {}).items():
            sf.setdefault(name, {}).update({k: float(v) for k, v in levels.items()})
        if under_items:
            primary = face or (declared[0] if declared else "main")
            free = sf.get(primary, {})
            shadows = rec.setdefault("_shadows", {})
            for eid, other in ctx.config["entities"].items():
                if other.get("location") == target and other.get("relation", "on") == "on":
                    shadows[eid] = dict(free)
        _aggregate(rec)
    elif soil:
        current = rec.get("soil") if isinstance(rec.get("soil"), dict) else {}
        current.update({k: float(v) for k, v in soil.items()})
        rec["soil"] = current
    if contamination:
        cont = rec.get("contamination") if isinstance(rec.get("contamination"), dict) else {}
        cont.update(contamination)
        rec["contamination"] = cont
    if wetness is not None:
        rec["wetness"] = float(wetness)
    if props:
        rec.update(copy.deepcopy(props))
    return {"soiled": target}


def _cells_for(ctx, view, rid, zone, count=None, near=None, reachable=True):
    layout = S.floor_layout(view, rid)
    reach = reachable_cells(ctx, rid) if reachable else None
    edges = S.perimeter_cells(view, rid)
    corners = S.corner_cells(view, rid)
    doors = {S.ck(c["cells"][rid]) for c in ctx.config.get("connections", {}).values() if rid in c.get("cells", {})}
    keys = []
    for key, (status, src) in sorted(layout.items(), key=lambda kv: S.pk(kv[0])[::-1]):
        if zone in ("all", "floor"):
            ok = status in ("open", "under")
        elif zone == "open":
            ok = status == "open" and key not in doors
        elif zone == "edges":
            ok = status in ("open", "under") and key in edges
        elif zone == "corners":
            ok = status in ("open", "under") and key in corners
        elif zone == "under":
            ok = status == "under"
        elif zone.startswith("under:"):
            name = zone[6:]
            ok = status == "under" and (src == name or ctx.config["entities"].get(src, {}).get("type") == name or
                                       name in ctx.config["entities"].get(src, {}).get("is_a", []))
        elif zone.startswith("rug:"):
            ok = status == "rug"
        else:
            raise SpecError("cleaning.floor: unknown zone " + zone)
        if ok and reach is not None:
            cell = S.pk(key)
            ok = cell in reach if status == "open" else any(geometry.chebyshev(cell, r) <= 1 for r in reach)
        if ok:
            keys.append(key)
    if near is not None and keys:
        keys.sort(key=lambda k: (geometry.chebyshev(S.pk(k), tuple(near)), S.pk(k)[1], S.pk(k)[0]))
        keys = keys[: count or len(keys)]
    elif count is not None and keys:
        keys = sorted(ctx.rng.sample(keys, min(int(count), len(keys))), key=lambda k: S.pk(k)[::-1])
    return keys


@scenario_helper("cleaning.floor")
def floor_helper(ctx, room, soil, cells=None, zone="open", count=None, near=None, jitter=0.0, furniture=None):
    rid = _room(ctx, room)
    floor_id = "floor_" + rid if "floor_" + rid in ctx.config["entities"] else None
    if floor_id is None:
        floor_id = next((e for e, r in sorted(ctx.config["entities"].items()) if r.get("location") == rid and r.get("category") == "floor"), None)
    if floor_id is None:
        raise SpecError("Room {} has no floor entity".format(rid))
    view = _View(ctx)
    if furniture:
        zone = "under:" + furniture
    if cells:
        keys = [S.ck(c) for c in cells]
    else:
        keys = _cells_for(ctx, view, rid, zone, count, near)
    if not keys:
        raise SpecError("cleaning.floor: no cells for zone {} in {}".format(zone, rid))
    rec = ctx.config["entities"][floor_id]
    cell_map = rec.setdefault("_cells", {})
    for key in keys:
        part = cell_map.setdefault(key, {})
        for kind, level in soil.items():
            value = float(level) * (1.0 + (ctx.rng.uniform(-jitter, jitter) if jitter else 0.0))
            part[kind] = round(min(1.0, max(part.get(kind, 0.0), value)), 4)
    _aggregate(rec)
    return {"floor": floor_id, "room": rid, "cells": [list(S.pk(k)) for k in keys], "cell": _out_of_the_way_cell(ctx, rid, keys)}


def _out_of_the_way_cell(ctx, rid, keys):
    """The `cell` binding (where specs drop a lost earring or a sock): the first chosen cell that is neither a cut cell
    of the room's walkable floor (a one-cell passage the robot must cross, kicking what lies there) nor in the doorway
    clearance; the first chosen cell if every one is."""
    state = ctx.state()
    room = ctx.config["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    blocked = set(geometry.blocked_cells(state, rid))
    hazards = set(geometry.hazard_cells(state, rid))
    cut = geometry.articulation_cells({(x, y) for y in range(rows) for x in range(cols)} - blocked - hazards)
    clear = geometry.door_clearance(state, rid)
    cells = [S.pk(k) for k in keys]
    best = sorted(range(len(cells)), key=lambda i: (cells[i] in cut, cells[i] in clear, i))[0]
    return list(cells[best])


@scenario_helper("cleaning.spill")
def spill_helper(ctx, liquid="water", liters=0.3, room=None, surface=None, cell=None, near=None, eid=None, props=None):
    from .floors import SPILL_PROPS
    record = {"liquid": liquid, "liquid_l": float(liters), "label": "{} spill".format(liquid.replace("_", " "))}
    record.update(copy.deepcopy(SPILL_PROPS.get(liquid, {})))
    record.update(copy.deepcopy(props or {}))
    if surface:
        new = ctx.placer.make("spill", surface, eid=eid, **record)
        rec = ctx.config["entities"][new]
        rec["relation"] = "on"
        rec.pop("pos", None)
    else:
        rid = _room(ctx, room)
        new = ctx.placer.make("spill", rid, eid=eid, **record)
        rec = ctx.config["entities"][new]
        rec.pop("pos", None)
        floor = ctx.config["entities"].get("floor_" + rid, {})
        if floor and M.material_key(floor) in ("carpet", "wool_carpet"):
            rec["on_carpet"] = True
    thickness = 3.0 if rec.get("on_carpet") else (2.5 if liquid in ("cooking_oil", "motor_oil", "paint", "vomit") else 1.5)
    area = max(0.01, float(liters) / thickness)
    side = round(min(3.0, area ** 0.5), 3)
    rec["area_m2"] = round(area, 4)
    rec["size_m"] = [max(0.05, side), max(0.05, side), 0.002]
    if not surface:
        if cell is None:
            # a puddle is not left against a live outlet or a plugged-in device unless the spec says where: picking a
            # tool up out of it would then be reaching into water next to power (plumbing's electrical hazard)
            cell = open_floor_spot(ctx.config, ctx.state(), rid, rec, near=near, item=new, avoid=power_cells(ctx.state(), rid))
        if cell is None:
            raise SpecError("cleaning.spill: no reachable floor in {}".format(rid))
        rec["pos"] = [int(cell[0]), int(cell[1])]
    return {"spill": new, "spill_cells": [list(c) for c in geometry.footprint(rec)]}


def _standing_cells(state, rid, ignore=()):
    """Floor cells taken by floor-standing things (geometry.floor_standing: furniture, appliances, loose items,
    floor-level mounts; not rugs, high or thin wall mounts, doors)."""
    used = set()
    for eid in geometry.room_entities(state, rid):
        rec = state["entities"][eid]
        if eid not in ignore and rec.get("pos") and geometry.floor_standing(rec):
            used.update(geometry.footprint(rec))
    return used


def _flat_cells(state, rid, ignore=()):
    flat = set()
    for eid in geometry.room_entities(state, rid):
        other = state["entities"][eid]
        if eid not in ignore and other.get("pos") and (other.get("category") == "rug" or (other.get("ground_layer") and other.get("portable"))):
            flat.update(geometry.footprint(other))
    return flat


def power_cells(state, rid):
    """Floor cells within one cell of a wall outlet, a power strip or a plugged-in device in the room."""
    room = state["rooms"].get(rid) or {}
    out = set()
    for eid in geometry.room_entities(state, rid):
        rec = state["entities"][eid]
        if not rec.get("pos"):
            continue
        if rec.get("category") in ("outlet", "power_strip") or (rec.get("plug_required") and rec.get("plugged_in")):
            for c in geometry.footprint(rec) or [tuple(rec["pos"])]:
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        n = (c[0] + dx, c[1] + dy)
                        if geometry.in_grid(room, n):
                            out.add(n)
    return out


def open_floor_spot(config, state, rid, record, near=None, item=None, avoid=()):
    """Top-left cell for a flat thing lying on the floor (spill, puddle): first where it cuts no hazard-free route
    and nobody must step in it to reach anything (geometry.free_floor_cell keep_access, then route_safe_cell in a room
    others' hazards already cut), off rugs; else its whole footprint on reachable floor, off doorways and clear of
    standing things (doorway clearance avoided when the room allows); else the corner that keeps most of it on
    reachable floor (a big puddle then runs under furniture). Nearest to `near`; None if no reachable floor.
    avoid: cells kept out of the footprint whenever any placement allows it (dropped only as a last resort)."""
    room = config["rooms"][rid]
    reach = geometry.reachable_cells(state, rid, set(geometry.blocked_cells(state, rid)))
    if not reach:
        return None
    ignore = (item,) if item else ()
    flat = _flat_cells(state, rid, ignore)
    near_t = tuple(near) if near is not None else None
    soft = {tuple(c) for c in avoid}
    for no_go in ((soft, set()) if soft else (set(),)):
        for cell in (geometry.free_floor_cell(state, rid, near=near_t, ignore=ignore, record=record, strict=False, keep_access=True,
                                              avoid=tuple(no_go)),
                     route_safe_cell(state, rid, record, near=near_t, ignore=ignore, strict=False, avoid=no_go)):
            if cell is not None and not set(geometry.footprint(dict(record, pos=list(cell)))) & flat:
                return list(cell)
        if not soft:
            break
    used = _standing_cells(state, rid, ignore)
    doors = set(geometry.doorway_cells(state, rid))
    clear = geometry.door_clearance(state, rid)
    sets = (clear | used | flat, doors | used | flat, doors | used, doors)
    for avoid in ([a | soft for a in sets] if soft else []) + list(sets):
        options = []
        for pos in sorted(reach, key=lambda c: (c[1], c[0])):
            cells = geometry.footprint(dict(record, pos=list(pos)))
            if all(geometry.in_grid(room, c) and c in reach and c not in avoid for c in cells):
                options.append(pos)
        if options:
            if near is not None:
                options.sort(key=lambda c: (geometry.chebyshev(c, near_t), c[1], c[0]))
            return list(options[0])

    def on_floor(pos):
        cells = geometry.footprint(dict(record, pos=list(pos)))
        if not all(geometry.in_grid(room, c) for c in cells):
            return -1
        return sum(1 for c in cells if c in reach and c not in doors)
    single = sorted((c for c in reach if c not in doors),
                    key=lambda c: (-on_floor(c), geometry.chebyshev(c, near_t) if near is not None else 0, c[1], c[0]))
    return list(single[0]) if single else None


RUG_SIZES = [[2.0, 1.4], [1.8, 1.2], [1.5, 1.0], [1.5, 0.6], [1.2, 0.8], [1.0, 0.7], [1.2, 0.5], [1.0, 0.5], [0.9, 0.6], [0.8, 0.5], [0.6, 0.4],
             [0.5, 0.5]]


@scenario_helper("cleaning.rug")
def rug_helper(ctx, room, type="rug", size_m=None, min_size_m=None, near=None, eid=None, var="rug", props=None):
    """A rug/runner/mat lying flat on open, reachable floor (whole footprint, off doorways, not under furniture):
    the requested size if it fits this room, else the largest standard smaller size (both orientations) that does.
    Binds `<var>`, `<var>_pos` (its corner cell) and `<var>_cells`."""
    rid = _room(ctx, room)
    extra = copy.deepcopy(props or {})
    new = ctx.placer.make(type, rid, eid=eid, **extra)
    rec = ctx.config["entities"][new]
    rec.pop("pos", None)
    want = [float(v) for v in (size_m or rec.get("size_m") or [1.0, 0.7])[:2]]
    least = [float(v) for v in (min_size_m or [0.5, 0.5])[:2]]
    height = float((rec.get("size_m") or [0, 0, 0.01])[2]) if len(rec.get("size_m") or []) > 2 else 0.01
    sizes = [want, [want[1], want[0]]]
    for size in RUG_SIZES:
        if size[0] * size[1] < want[0] * want[1] - 1e-9 and min(size) >= min(least) - 1e-9 and max(size) >= max(least) - 1e-9:
            sizes += [list(size), [size[1], size[0]]]
    state = ctx.state()
    reach = reachable_cells(ctx, rid)
    used = _standing_cells(state, rid, ignore=(new,))
    doors = set(geometry.doorway_cells(state, rid))
    flat = _flat_cells(state, rid, ignore=(new,))
    room_rec = ctx.config["rooms"][rid]
    for size in sizes:
        trial = dict(rec, size_m=[size[0], size[1], height], rot=0)
        options = []
        for pos in sorted(reach, key=lambda c: (c[1], c[0])):
            cells = geometry.footprint(dict(trial, pos=list(pos)))
            if all(geometry.in_grid(room_rec, c) and c in reach and c not in doors and c not in used and c not in flat for c in cells):
                options.append(pos)
        if not options:
            continue
        if near is not None:
            options.sort(key=lambda c: (geometry.chebyshev(c, tuple(near)), c[1], c[0]))
            pos = options[0]
        else:
            pos = ctx.rng.choice(options)
        if size[0] * size[1] < want[0] * want[1] - 1e-9 and rec.get("mass_kg"):
            rec["mass_kg"] = round(float(rec["mass_kg"]) * size[0] * size[1] / (want[0] * want[1]), 3)
        rec.update(size_m=[size[0], size[1], height], rot=0, pos=[int(pos[0]), int(pos[1])])
        cells = [list(c) for c in geometry.footprint(rec)]
        return {var: new, var + "_pos": list(rec["pos"]), var + "_cells": cells}
    del ctx.config["entities"][new]
    raise SpecError("cleaning.rug: no open reachable floor in {} for a {} of at least {}".format(rid, type, least))


WASTE_PRESETS = {
    "kitchen_mix": ["plastic_bottle", "soda_can", "glass_jar", "yogurt_cup", "food_scraps", "coffee_grounds", "chicken_bones",
                    "pizza_box", "plastic_bag", "paper_napkin", "aluminum_foil", "food_carton"],
    "recycling_mix": ["plastic_bottle", "milk_jug", "soda_can", "glass_bottle", "newspaper", "cardboard_box", "pizza_box", "plastic_bag",
                      "styrofoam_block", "black_plastic_tray", "takeout_container", "thermal_receipt"],
    "compost_mix": ["food_scraps", "coffee_grounds", "eggshells", "fruit_peel", "chicken_bones", "cheese_rind", "tea_bag", "pla_cutlery",
                    "paper_napkin", "meat_scraps"],
    "party": ["soda_can", "plastic_bottle", "paper_plate", "paper_cup", "paper_napkin", "food_scraps", "glass_bottle", "wooden_skewer",
              "corn_cob", "bottle_cap", "chip_bag"],
    "bathroom_empties": ["detergent_bottle", "toothpaste_tube", "toilet_paper_core", "aerosol_can", "used_tissue"],
    "packaging": ["cardboard_box", "bubble_wrap", "styrofoam_block", "plastic_bag", "paper_scrap", "thermal_receipt"],
    "litter": ["food_wrapper", "soda_can", "used_tissue", "chip_bag", "cigarette_butt", "paper_cup"],
}
LOOKALIKES = {
    "earring": {"type": "generic_item", "props": {"label": "earring", "value": 150.0, "size_m": [0.015, 0.01, 0.01], "mass_kg": 0.004,
                                                   "material": "gold", "_disposition": "keep", "owner": "resident_1", "category": "jewelry"}},
    "note": {"type": "paper_scrap", "props": {"label": "crumpled note", "text": "Dentist Tue 3pm - call 555-0142", "_disposition": "keep",
                                              "_ambiguous": True, "_reason": "it has a phone number on it"}},
    "half_coffee": {"type": "paper_cup", "props": {"label": "coffee cup (half full)", "liquid_l": 0.15, "liquid": "coffee", "_disposition": "keep",
                                                  "_ambiguous": True, "_reason": "I'm still drinking that"}},
    "return_box": {"type": "cardboard_box", "props": {"label": "shipping box", "_disposition": "keep", "_ambiguous": True,
                                                       "_reason": "I need it to return the shoes", "label_text": "RETURN - keep box"}},
    "drawing": {"type": "paper_scrap", "props": {"label": "child's drawing", "text": "MY DOG by Riley", "_disposition": "keep",
                                                 "sentimental": True, "_reason": "it's Riley's drawing"}},
    "sauce_packet": {"type": "waste_item", "props": {"label": "unopened sauce packet", "waste_kind": "food", "_disposition": "keep",
                                                     "_reason": "it's unopened food", "size_m": [0.06, 0.04, 0.005]}},
    "glass_jar_keep": {"type": "glass_jar", "props": {"label": "clean glass jar", "residue": 0.0, "_ambiguous": True}},
    "gift_bag": {"type": "gift_bag", "props": {"_ambiguous": True}},
}


def _spawn_item(ctx, spec, where, idx):
    if isinstance(spec, str):
        if spec in LOOKALIKES:
            spec = LOOKALIKES[spec]
        else:
            spec = {"type": spec}
    type_name = spec.get("type", "waste_item")
    props = copy.deepcopy(spec.get("props", {}))
    p = ctx.placer
    kind, target = where
    if kind == "on":
        new = p.on(target, type_name, **props)
    elif kind == "in":
        new = p.inside(target, type_name, **props)
    elif kind == "under":
        new = p.make(type_name, target, **props)
        ctx.config["entities"][new]["relation"] = "under"
    else:
        new = place_reachable(ctx, target, type_name, near=spec.get("near"), **props)
    return new


def _surface_floor(ctx, surface):
    """(room, cell next to the surface the robot can reach) for items that do not fit on a full surface."""
    view = _View(ctx)
    rid = S.room_of(view.w, surface)
    if rid is None or rid not in ctx.config["rooms"]:
        return None, None
    state = dict(ctx.state(), robot={"room": rid, "hands": {}, "pos": None})
    _, cells = geometry.anchor(state, surface)
    return rid, (list(cells[0]) if cells else None)


SURFACE_CATEGORIES = ("counter", "table", "desk", "island", "workbench", "dresser", "nightstand", "side_table", "shelf")


def _reachable_surfaces(ctx, rid):
    """Furniture tops in a room the robot can reach (counters and tables first), for items no floor cell takes."""
    out = []
    for eid, rec in sorted(ctx.config["entities"].items()):
        if rec.get("location") != rid or not rec.get("pos") or not (rec.get("surface") or rec.get("category") in SURFACE_CATEGORIES):
            continue
        if rec.get("category") not in SURFACE_CATEGORIES or not accessible(ctx, eid):
            continue
        out.append((SURFACE_CATEGORIES.index(rec["category"]), eid))
    return [eid for _, eid in sorted(out)]


@scenario_helper("cleaning.waste")
def waste_helper(ctx, items=None, preset=None, on=None, into=None, floor=None, under=None, count=None, spread=None, lookalikes=None):
    specs = list(items or [])
    if preset:
        names = list(WASTE_PRESETS[preset])
        if count:
            names = ctx.rng.sample(names, min(int(count), len(names)))
        specs = names + specs
    specs += list(lookalikes or [])
    placements = []
    if spread:
        for target in spread:
            placements.append(("on", target) if target in ctx.config["entities"] else ("floor", _room(ctx, target)))
    if on:
        placements.append(("on", on))
    if into:
        placements.append(("in", into))
    if floor:
        placements.append(("floor", _room(ctx, floor)))
    if under:
        placements.append(("under", under))
    if not placements:
        raise SpecError("cleaning.waste needs on/into/floor/under/spread")
    created = []
    for i, spec in enumerate(specs):
        where = placements[i % len(placements)]
        new = _spawn_item(ctx, spec, where, i)
        if new is None:
            fallback = next((pl for pl in placements if pl[0] == "floor"), None)
            if fallback:
                new = _spawn_item(ctx, spec, fallback, i)
        if new is None and where[0] == "on":
            # a full counter/table: the rest lands on the floor beside it (still reachable)
            rid, cell = _surface_floor(ctx, where[1])
            if rid is not None:
                spec = dict(LOOKALIKES.get(spec, {"type": spec})) if isinstance(spec, str) else dict(spec)
                spec["near"] = cell
                new = _spawn_item(ctx, spec, ("floor", rid), i)
        if new is None:
            # no floor cell takes it (a fragile glass jar's keep-out ring would seal a narrow kitchen aisle for every
            # careful approach): it is left on a reachable counter or table of the room instead
            floor_rooms = [pl[1] for pl in placements if pl[0] == "floor"] + ([where[1]] if where[0] == "floor" else [])
            for rid in dict.fromkeys(floor_rooms):
                for surface in _reachable_surfaces(ctx, rid):
                    new = _spawn_item(ctx, spec, ("on", surface), i)
                    if new is not None:
                        break
                if new is not None:
                    break
        if new is None:
            raise SpecError("cleaning.waste: no room for {}".format(spec))
        created.append(new)
    return bind_streams(ctx, created)


def bind_streams(ctx, created):
    """Group items by the stream they should END in when handled correctly (caps fixed, rinsed)."""
    from .waste import acceptable
    view = _View(ctx)
    out = {"waste_items": list(created), "trash": [], "recycling": [], "compost": [], "keep": [], "hazardous": [], "dropoff": [],
           "yard_waste": [], "shred": [], "special": [], "fix_caps": [], "rinse": [], "remove_sticker": []}
    fixable = {"cap_on": "fix_caps", "cap_loose": "fix_caps", "not_rinsed": "rinse", "sticker": "remove_sticker"}
    for eid in created:
        primary, ok, issues = acceptable(view.w, eid)
        if primary == "keep":
            out["keep"].append(eid)
            continue
        if issues and all(i in fixable for i in issues):
            best = primary
            for issue in issues:
                out[fixable[issue]].append(eid)
        else:
            best = ok[0] if ok else primary
        if best == "dropoff":
            best = "trash"
        out.setdefault(best, []).append(eid)
    out["discard"] = [e for e in created if e not in out["keep"]]
    for key in list(out):
        out["n_" + key] = len(out[key])
    return out


RULE_PRESETS = {
    "strict_city": {"program": "single_stream", "accepted_resins": [1, 2], "glass": False, "cartons": False, "bagged_recycling": False,
                    "caps": "off", "compost": "backyard", "shredded_paper": "no", "colors": {"trash": "blue", "recycling": "green", "compost": "brown", "yard_waste": "brown"}},
    "broad_city": {"program": "single_stream", "accepted_resins": [1, 2, 3, 4, 5, 7], "glass": True, "cartons": True, "bagged_recycling": False,
                   "caps": "on", "compost": "municipal", "compostable_plastics": True, "shredded_paper": "bagged"},
    "dual_stream": {"program": "dual_stream", "accepted_resins": [1, 2, 5], "glass": True, "cartons": True, "bagged_recycling": False,
                    "caps": "on", "compost": "none", "shredded_paper": "bagged"},
    "suburb": {"program": "single_stream", "accepted_resins": [1, 2, 5], "glass": True, "cartons": True, "bagged_recycling": False,
               "caps": "off", "compost": "municipal", "shredded_paper": "bagged", "film_dropoff": True},
}


def random_rules(rng):
    rules = {"program": rng.choice(["single_stream", "single_stream", "dual_stream"]),
             "accepted_resins": rng.choice([[1, 2], [1, 2, 5], [1, 2, 3, 4, 5, 7], [1, 2, 4, 5]]),
             "glass": rng.random() < 0.75, "cartons": rng.random() < 0.7, "film": False, "film_dropoff": rng.random() < 0.3,
             "bagged_recycling": rng.random() < 0.1, "caps": rng.choice(["on", "on", "off"]),
             "shredded_paper": rng.choice(["bagged", "bagged", "loose", "no"]),
             "compost": rng.choice(["none", "backyard", "municipal", "municipal"]), "compostable_plastics": rng.random() < 0.3,
             "max_residue": 0.15, "aerosols": rng.random() < 0.6,
             "collection_day": rng.choice(["monday", "tuesday", "wednesday", "thursday", "friday"])}
    colors = {"trash": "black", "recycling": "blue", "compost": "green", "yard_waste": "brown"}
    if rng.random() < 0.25:
        colors = {"trash": "blue", "recycling": "yellow", "compost": "brown", "yard_waste": "green"}
    rules["colors"] = colors
    return rules


def guide_text(rules):
    resins = ", ".join("#{}".format(c) for c in rules.get("accepted_resins", []))
    parts = ["LOCAL RECYCLING ({}):".format("single-stream" if rules.get("program") == "single_stream" else "dual-stream: paper separate from containers"),
             "Plastic bottles/tubs/jugs {} only - empty and rinsed, caps {}.".format(resins, "ON" if rules.get("caps") == "on" else "OFF (caps to trash)"),
             "Glass bottles & jars: {}.".format("yes" if rules.get("glass") else "NO (drop-off only)"),
             "Cartons: {}.".format("yes" if rules.get("cartons") else "no"),
             "Metal cans{}.".format(", empty aerosols" if rules.get("aerosols") else " (no aerosols)"),
             "Paper and flattened cardboard; greasy pizza boxes are NOT recyclable.",
             "Never: plastic bags/film{}, styrofoam, black plastic, thermal receipts.".format(" (store drop-off bag)" if rules.get("film_dropoff") else ""),
             "Recyclables go in LOOSE{}.".format("" if not rules.get("bagged_recycling") else " or in clear blue bags"),
             "Shredded paper: {}.".format({"bagged": "in a closed paper bag", "loose": "loose", "no": "not accepted (compost or trash)"}.get(rules.get("shredded_paper"), "bagged")),
             "Food scraps: {}.".format({"none": "trash", "backyard": "backyard bin - fruit/veg, coffee, eggshells only; NO meat, bones, dairy, oil",
                                        "municipal": "green organics cart - all food incl. meat & bones, soiled paper" + (", certified compostable plastics" if rules.get("compostable_plastics") else "")}[rules.get("compost", "none")]),
             "Batteries, bulbs, paint, electronics: hazardous drop-off, never in the bins.",
             "Bin colours here: " + ", ".join("{} = {}".format(v, k.replace("_", " ")) for k, v in sorted((rules.get("colors") or {}).items())),
             "Collection: {}.".format(str(rules.get("collection_day", "tuesday")).title())]
    return " ".join(parts)


@scenario_helper("cleaning.rules")
def rules_helper(ctx, preset=None, overrides=None, guide=True):
    from .waste import DEFAULT_RULES
    if preset == "random" or preset is None:
        rules = random_rules(ctx.rng)
    else:
        rules = dict(DEFAULT_RULES)
        rules.update(copy.deepcopy(RULE_PRESETS[preset]))
    if overrides:
        rules.update(copy.deepcopy(overrides))
    full = dict(DEFAULT_RULES)
    full.update(rules)
    ctx.config.setdefault("household", {})["waste_rules"] = full
    guide_id = None
    text = guide_text(full)
    for eid, rec in sorted(ctx.config["entities"].items()):
        if rec.get("type") == "recycling_guide":
            rec["text"] = text
            guide_id = eid
    if guide and guide_id is None:
        kitchens = ctx.placer.rooms("kitchen")
        if kitchens:
            guide_id = ctx.placer.mount(kitchens[0], "recycling_guide", height=1.3, text=text)
    if guide_id and guide_id in ctx.config["entities"]:
        rec = ctx.config["entities"][guide_id]
        rid = rec.get("location")
        if rid in ctx.config["rooms"]:
            reach = reachable_cells(ctx, rid)
            pos = tuple(rec.get("pos") or ())
            hidden = pos and set(geometry.footprint(rec)) & taller_than(ctx.state(), rid, float(rec.get("mount_height_m", 1.3) or 1.3))
            if reach and (hidden or not (pos and any(geometry.chebyshev(pos, r) <= 1 for r in reach))):
                rec["pos"] = sign_cell(ctx.config, ctx.state(), rid, height=float(rec.get("mount_height_m", 1.3) or 1.3), record=rec) \
                    or rec.get("pos")
    for eid, rec in ctx.config["entities"].items():
        stream = rec.get("waste_stream") if S.is_waste_container(rec) else None
        if stream and (full.get("colors") or {}).get(stream):
            rec["color"] = full["colors"][stream]
    return {"rules": full, "caps": full["caps"], "compost": full["compost"], "guide": guide_id}


@scenario_helper("cleaning.bucket")
def bucket_helper(ctx, room=None, near=None, liters=6.0, product="floor_cleaner", dose=1.0, soil_g=0.0, cell=None, eid=None, type_name="mop_bucket"):
    rid = _room(ctx, room) if room else ctx.config.get("robot", {}).get("room") or ctx.config["start_room"]
    anchor = None
    if near and near in ctx.config["entities"]:
        _, cells = geometry.anchor(dict(ctx.state(), robot={"room": rid, "hands": {}, "pos": None}), near)
        anchor = cells[0] if cells else None
    new = place_reachable(ctx, rid, type_name, near=list(cell) if cell is not None else list(anchor or [0, 0]), eid=eid)
    if new is None:
        raise SpecError("cleaning.bucket: no floor space")
    rec = ctx.config["entities"][new]
    rec["liquid"] = "cleaning_solution" if product and product != "water" else "water"
    rec["liquid_l"] = float(liters)
    rec["_cl_ref_l"] = float(liters)
    rec["cl_chem"] = {}
    if product and product not in ("water",):
        info = M.PRODUCTS[product]
        # concentrates at their label dose; ready-to-use products poured in neat (dose 1.0 = label strength)
        ml = (float(info.get("dose_ml_per_l", 10.0)) * liters if info.get("concentrate") else liters * 1000.0) * float(dose)
        rec["cl_chem"][product] = round(ml, 2)
    rec["cl_soil_g"] = float(soil_g)
    rec["temperature_c"] = 35.0
    return {"bucket": new}


@scenario_helper("cleaning.basin")
def basin_helper(ctx, sink, liters=8.0, product="dish_soap", dose=1.0, temp_c=45.0, soil_g=0.0):
    rec = _entity(ctx, sink)
    rec["liquid_container"] = True
    rec["liquid"] = "soapy_water" if product else "water"
    rec["liquid_l"] = float(liters)
    rec["_cl_ref_l"] = float(liters)
    rec["liquid_temp_c"] = float(temp_c)
    rec["cl_chem"] = {}
    if product:
        info = M.PRODUCTS[product]
        rec["cl_chem"][product] = round(float(info.get("dose_ml_per_l", 2.0)) * liters * float(dose), 2)
    rec["cl_soil_g"] = float(soil_g)
    rec.setdefault("capacity_l", 20.0)
    rec["container"] = True
    rec["accepts_items"] = True
    return {"basin": sink}


# ----- placement (kernel v2.3: geometry.free_floor_cell / door_clearance / reachable_cells / access reports) -------
def _perimeter(room):
    cols, rows = geometry.grid_size(room)
    return {(x, 0) for x in range(cols)} | {(x, rows - 1) for x in range(cols)} | {(0, y) for y in range(rows)} | \
        {(cols - 1, y) for y in range(rows)}


def taller_than(state, rid, height, ignore=()):
    """Cells under floor-standing things whose top is above `height` m: a wall mount hung lower than that on one of
    these cells would be hidden behind them (the layout audit's "hidden mount")."""
    cells = set()
    for eid in geometry.room_entities(state, rid):
        rec = state["entities"][eid]
        if eid in ignore or not rec.get("pos") or not geometry.floor_standing(rec):
            continue
        try:
            top = float(rec.get("mount_height_m", rec.get("z_m", 0.0)) or 0.0) + float((rec.get("size_m") or [0, 0, 0])[2])
        except (TypeError, ValueError, IndexError):
            continue
        if top > height + 0.05:
            cells.update(geometry.footprint(rec))
    return cells


def mounts_hidden_by(state, rid, top, ignore=()):
    """Cells of wall mounts (0.5-2 m up, not outlets, lights, windows or drawer fronts) that a floor-standing thing
    `top` m tall would hide: tall floor items (vacuums, mops) keep off them."""
    cells = set()
    if top is None or top < 0.5:
        return cells
    for eid in geometry.room_entities(state, rid):
        rec = state["entities"][eid]
        if eid in ignore or not rec.get("pos") or not rec.get("mounted") or rec.get("ground_layer") or "connects" in rec:
            continue
        if geometry.floor_standing(rec) or rec.get("category") in ("outlet", "light", "window", "drawer"):
            continue
        try:
            h = float(rec.get("mount_height_m", rec.get("z_m", 0.0)) or 0.0)
        except (TypeError, ValueError):
            continue
        if 0.5 <= h < 2.0 and top > h + 0.05:
            cells.update(geometry.footprint(rec))
    return cells


def sign_cell(config, state, rid, near=None, height=1.3, record=None):
    """A wall cell for a posted sign or guide (hung above counter height): on the room's perimeter, never a doorway,
    not behind anything floor-standing taller than the sign's height (a refrigerator would hide it) and, when the room
    allows, outside the doorway clearance (an open leaf would hide it); readable from a cell the robot can reach (one
    cell away). Nearest to `near`. record: the sign (its whole footprint is checked)."""
    room = config["rooms"][rid]
    reach = geometry.reachable_cells(state, rid)
    doors = set(geometry.doorway_cells(state, rid))
    clear = geometry.door_clearance(state, rid)
    behind = taller_than(state, rid, height)

    def cells_at(c):
        return geometry.footprint(dict(record, pos=list(c))) if record else [c]
    wall = [c for c in _perimeter(room) if c not in doors and any(geometry.chebyshev(c, r) <= 1 for r in reach)
            and all(geometry.in_grid(room, x) for x in cells_at(c))]
    wall = [c for c in wall if not set(cells_at(c)) & behind] or wall
    options = [c for c in wall if c not in clear] or wall
    if not options:
        ordered = sorted(reach, key=lambda c: (c[1], c[0]))
        return list(ordered[0]) if ordered else None
    options.sort(key=lambda c: ((geometry.chebyshev(c, tuple(near)) if near else 0), c[1], c[0]))
    return list(options[0])


def reachable_cells(ctx, rid):
    """Free floor cells joined to the room's doorways (4-connected, as find_path moves; people and pets in the way)."""
    state = ctx.state()
    return geometry.reachable_cells(state, rid, set(geometry.blocked_cells(state, rid)))


def _tall(record):
    size = record.get("size_m") or [0.1, 0.1, 0.1]
    try:
        return float(size[2]) >= 0.15 if len(size) > 2 else False
    except (TypeError, ValueError):
        return True


def _loose_cells(state, rid, ignore):
    """Cells whose only occupants are loose, non-blocking portable floor items (another small thing may share them)."""
    out, other = set(), set()
    for eid in geometry.room_entities(state, rid):
        rec = state["entities"][eid]
        if eid in ignore or not rec.get("pos") or not geometry.floor_standing(rec):
            continue
        loose = rec.get("portable") and not geometry.blocks(rec) and not rec.get("mounted") and not rec.get("floor_hazard")
        (out if loose else other).update(geometry.footprint(rec))
    return out - other


def _sole_access(report, targets, comp, room):
    """(door components, cells that are the only doorway-joined access cell of some accessible target)."""
    mains = {comp.get(cells[0]) for k, (cells, _, _) in targets.items() if k[0:1] == ("door",)} - {None}
    sole = set()
    for key in report["ok"]:
        cells, radius, standable = targets[key]
        if standable:
            continue
        own = set(cells)
        access = [n for n in geometry.near_cells(cells, room, radius) if n not in own and comp.get(n) in mains]
        if len(access) == 1:
            sole.add(access[0])
    return mains, sole


def _own_hazards(state, rid, eid, pos):
    """Hazard cells the entity would add standing at `pos` (geometry.placement_hazards: its own floor_item cells and
    any keep-out ring another domain derives around it - a fragile jar's, a hot pan's)."""
    rec = state["entities"][eid]
    old = rec.get("pos")
    rec["pos"] = [int(pos[0]), int(pos[1])]
    try:
        return {c for c, items in geometry.placement_hazards(state, rid).items() if any(src == eid for _, src in items)}
    finally:
        if old is None:
            rec.pop("pos", None)
        else:
            rec["pos"] = old


def _ring_cuts(state, rid, eid):
    """True if an entity's derived keep-out ring (cells beyond its footprint) cuts a hazard-free route or the only
    hazard-free access to something that its footprint alone would not."""
    rec = state["entities"][eid]
    foot = set(geometry.footprint(rec))
    own = _own_hazards(state, rid, eid, rec["pos"])
    if not own - foot:
        return False
    static = set(geometry.blocked_cells(state, rid, ignore=(eid,)))
    others = {c for c, items in geometry.placement_hazards(state, rid).items() if any(src != eid for _, src in items)}
    targets = geometry.access_targets(state, rid, level="all", ignore=(eid,))
    before = geometry.access_report(state, rid, static | others | foot, targets)
    after = geometry.access_report(state, rid, static | others | foot | own, targets)
    return not geometry.not_worse(before, after)


def _seals_floor(state, rid, eid):
    """True if a path-blocking entity walls off floor beyond its own footprint (a dead corner behind a bin)."""
    rec = state["entities"][eid]
    foot = set(geometry.footprint(rec))
    before = geometry.reachable_cells(state, rid, set(geometry.blocked_cells(state, rid, ignore=(eid,), include_agents=False)))
    after = geometry.reachable_cells(state, rid, set(geometry.blocked_cells(state, rid, include_agents=False)))
    return bool(before - foot - after)


def route_safe_cell(state, rid, record, near=None, ignore=(), strict=None, share=False, safe_reach=False, eid=None,
                    keep_floor=False, avoid=()):
    """Placement search that keeps a room's access no worse, used where geometry.free_floor_cell(keep_access=True) has
    no answer: (1) rooms other things' hazards (cords, loose items, wet floor) already cut - keep_access wants the new
    thing reachable without stepping on anything, which nothing deep in such a room is; (2) things whose own derived
    keep-out ring (navigation's margin around fragile or hot floor items) would cut a careful route even though their
    cell does not (eid: the record's id in state; its ring is evaluated at each candidate).
    Returns the cell nearest `near` (against a wall or a fixed obstacle first among equals) whose footprint (and
    ring) loses no hazard-free route or only access given the
    existing hazards (a path-blocking record also keeps every plain route and is itself accessible over plain floor),
    is reachable, free, off doorways and outside the doorway clearance (always for tall or blocking records:
    geometry.door_clearance includes hinged leaves and overhead thresholds). safe_reach: the new thing itself must be
    reachable without stepping on anything (the kernel keep_access rule). share: a small, non-blocking thing may stand
    on a cell only loose items occupy (cuts nothing new). keep_floor: a path-blocking thing must not wall off any floor
    beyond its own footprint. avoid: hard no-go cells (e.g. next to live outlets for a puddle). None if nothing fits."""
    room = state["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    blocking = geometry.blocks(record)
    no_go = {tuple(c) for c in avoid}
    if strict is None:
        strict = blocking or _tall(record)
    ignore = tuple(ignore) + ((eid,) if eid and eid not in ignore else ())
    static = set(geometry.blocked_cells(state, rid, ignore=ignore, include_agents=False))
    agents = set(geometry.blocked_cells(state, rid, ignore=ignore)) - static
    used = set(geometry.doorway_cells(state, rid))
    for other in geometry.room_entities(state, rid):
        if other not in ignore and geometry.floor_standing(state["entities"][other]):
            used.update(geometry.footprint(state["entities"][other]))
    hazards = {c for c, items in geometry.placement_hazards(state, rid).items() if any(src not in ignore for _, src in items)}
    shareable = _loose_cells(state, rid, ignore) if share and not blocking else set()
    reach = geometry.reachable_cells(state, rid, static)
    keep_floor = keep_floor and blocking
    targets = geometry.access_targets(state, rid, level="all", ignore=ignore)
    every = {(x, y) for y in range(rows) for x in range(cols)}
    checks = []
    for base in ([static | agents | hazards, static | agents] if blocking else [static | agents | hazards]):
        before = geometry.access_report(state, rid, base, targets)
        walk = every - base
        comp = geometry.components(walk)
        mains, sole = _sole_access(before, targets, comp, room)
        checks.append((base, before, geometry.articulation_cells(walk), sole, comp, mains))
    ringy = None
    clear = geometry.door_clearance(state, rid)
    for avoid in ([clear] if strict else [clear, set()]):
        options = []
        for y in range(rows):
            for x in range(cols):
                cells = geometry.footprint(dict(record, pos=[x, y]))
                if any(not geometry.in_grid(room, c) or c in static or c in agents or c in avoid or c in no_go for c in cells):
                    continue
                if any((c in used or c in hazards) and c not in shareable for c in cells):
                    continue
                if not any(n in reach for n in geometry.near_cells(cells, room) if n not in cells):
                    continue
                options.append(((x, y), cells))
        def contact(cells):
            """Footprint sides against a wall or a fixed obstacle: bins and tools stand against something."""
            own = set(cells)
            return sum(1 for c in cells for n in ((c[0] + 1, c[1]), (c[0] - 1, c[1]), (c[0], c[1] + 1), (c[0], c[1] - 1))
                       if n not in own and (not geometry.in_grid(room, n) or n in static))
        options.sort(key=lambda o: (geometry.chebyshev(o[0], tuple(near)) if near is not None else 0, -contact(o[1]), o[0][1], o[0][0]))
        for pos, cells in options:
            cellset = set(cells)
            ring = set()
            if eid and eid in state["entities"] and ringy is not False:
                ring = _own_hazards(state, rid, eid, pos) - cellset
                if ringy is None:
                    ringy = bool(ring)
            if keep_floor and reach - cellset - geometry.reachable_cells(state, rid, static | cellset):
                continue            # it would wall off a dead corner of floor behind it
            ok = True
            for i, (base, before, cut, sole, comp, mains) in enumerate(checks):
                around = [n for n in geometry.near_cells(cells, room) if n not in cellset]
                if (i == 1 or safe_reach) and not any(comp.get(n) in mains for n in around):
                    ok = False      # must itself be accessible (over plain floor; without hazards when safe_reach)
                    break
                taken = cellset | (ring if i == 0 else set())
                if taken <= base or (len(taken) == 1 and not (taken & cut) and not (taken & sole)):
                    continue        # already impassable, or neither a cut cell nor anything's only access
                if not geometry.not_worse(before, geometry.access_report(state, rid, base | taken, targets)):
                    ok = False
                    break
            if ok:
                return list(pos)
    return None


def place_floor(p, rid, type_name, near=None, eid=None, strict=None, share=False, avoid=(), **props):
    """A floor item placed by the kernel rule first (Placer.floor_item: keep_access - it cuts no hazard-free route or
    the only access to anything and is reachable without stepping on anything - and strict for tall or path-blocking
    things: never in doorway clearance), unless the keep-out ring another domain derives around it (fragile, hot)
    would cut a careful route; a path-blocking thing (blocks_path=True in props, never flipped later) is moved to a
    spot that walls off no dead corner of floor when the kernel's pick does and such a spot exists. Then
    route_safe_cell: the same rule with the ring included, then (a room others' hazards already cut) a cell that keeps
    access no worse, then (share) a cell other loose items occupy. avoid: cells it must not take (wall mounts a tall
    item would hide). None if nothing fits."""
    avoid = {tuple(c) for c in avoid}
    new = p.floor_item(rid, type_name, eid=eid, near=near, strict=strict, avoid=tuple(sorted(avoid)), **props)
    if new is not None:
        state = p.state()
        if _ring_cuts(state, rid, new):
            del p.c["entities"][new]
        elif geometry.blocks(p.c["entities"][new]) and _seals_floor(state, rid, new):
            # the kernel's pick walls off a dead corner behind it: a spot that does not, if there is one
            rec = p.c["entities"][new]
            kept = rec.pop("pos")
            cell = route_safe_cell(p.state(), rid, rec, near=near, strict=strict, eid=new, safe_reach=True, keep_floor=True, avoid=avoid)
            rec["pos"] = cell or kept
            return new
        else:
            return new
    new = p.make(type_name, rid, eid=eid, **props)
    rec = p.c["entities"][new]
    rec.pop("pos", None)
    tries = [{"safe_reach": True, "keep_floor": True}, {"safe_reach": True}, {"keep_floor": True}, {}]
    if share and not geometry.blocks(rec):
        tries.append({"share": True})
    for extra in tries:
        cell = route_safe_cell(p.state(), rid, rec, near=near, strict=strict, eid=new, avoid=avoid, **extra)
        if cell is not None:
            rec["pos"] = cell
            return new
    del p.c["entities"][new]
    return None


def accessible(ctx, eid):
    """True if the robot can stand within arm's reach of the entity's footprint (or its holder's)."""
    view = _View(ctx)
    rid = S.room_of(view.w, eid)
    if rid is None:
        return False
    holder = eid
    while ctx.config["entities"].get(holder, {}).get("location") not in ctx.config["rooms"]:
        holder = ctx.config["entities"][holder]["location"]
        if holder not in ctx.config["entities"]:
            return False
    rec = ctx.config["entities"][holder]
    cells = geometry.footprint(rec)
    if not cells:
        return False
    reach = reachable_cells(ctx, rid)
    radius = geometry.access_radius(rec)
    return any(geometry.chebyshev(a, b) <= radius for a in cells for b in reach)


def place_reachable(ctx, rid, type_name, near=None, eid=None, **props):
    """A setup-time floor item (tools, buckets, vacuums, waste on the floor) by `place_floor`: the kernel keep_access
    rule, then a cell that keeps access no worse in a room others' hazards already cut, then (small things) a cell
    other loose items share. None only when the room has no such floor at all."""
    top = _top(ctx.placer.reg.make(type_name, rid, **props))
    return place_floor(ctx.placer, rid, type_name, near=near, eid=eid, share=True,
                       avoid=mounts_hidden_by(ctx.state(), rid, top), **props)


def _top(record):
    try:
        return float((record.get("size_m") or [0, 0, 0])[2])
    except (TypeError, ValueError, IndexError):
        return 0.0


@scenario_helper("cleaning.reachable")
def reachable_helper(ctx, type, room=None, pick="first", where=None, var=None):
    """Bind an entity of a type that the robot can actually get to (generated rooms can box furniture in)."""
    rid = _room(ctx, room) if room else None
    found = [e for e in ctx.placer.find(type, room=rid, where=where) if accessible(ctx, e)]
    if not found:
        raise SpecError("cleaning.reachable: no accessible {} in {}".format(type, rid))
    chosen = found[0] if pick == "first" else ctx.rng.choice(found)
    return {"result": chosen, (var or type): chosen}


@scenario_helper("cleaning.fixture")
def fixture_helper(ctx, room, type="generic_item", props=None, eid=None, var="fixture", wall=True, tries=12, other_rooms=True,
                   reuse=None):
    """A fixed fixture (sink, toilet, tub...) the robot can actually reach. With reuse (match fields, e.g.
    {"category": "toilet"} or {"sink_zone": "kitchen"}) the room's own accessible fixture of that kind comes first - a
    kitchen already has its sink, so no second one is plumbed in beside it (the scenario's fields are added where it
    lacks them; a closed lid is raised). Otherwise a new one against a wall (Placer.furniture keeps every route and
    access), then free-standing, then (other_rooms) in the other rooms of the same type, then their own fixtures.
    Binds `<var>` and `<var>_room`."""
    first = _room(ctx, room)
    rooms = [first]
    if other_rooms:
        kind = ctx.config["rooms"][first].get("type")
        rooms += [r for r in ctx.placer.rooms(kind) if r != first]

    def reuse_in(rid):
        for found in ctx.placer.find(room=rid, where=reuse):
            if not accessible(ctx, found):
                continue
            rec = ctx.config["entities"][found]
            for key, value in sorted((props or {}).items()):
                if key not in rec or (key == "faces" and not rec.get("faces")):
                    rec[key] = copy.deepcopy(value)
            if rec.get("openable") and not rec.get("open"):
                rec["open"] = True
            return {var: found, "result": found, var + "_room": rid}
        return None
    if reuse:
        found = reuse_in(first)
        if found:
            return found
    for rid in rooms:
        for walled in ((True, False) if wall else (False,)):
            for attempt in range(int(tries)):
                new = ctx.placer.furniture(rid, type, eid=eid, wall=walled, **copy.deepcopy(props or {}))
                if new is None:
                    break
                if accessible(ctx, new):
                    return {var: new, "result": new, var + "_room": rid}
                del ctx.config["entities"][new]
    if reuse:
        for rid in rooms[1:]:
            found = reuse_in(rid)
            if found:
                return found
    raise SpecError("cleaning.fixture: no accessible spot for {} in {}".format(type, ", ".join(rooms)))


@scenario_helper("cleaning.tools")
def tools_helper(ctx, tools, on=None, room=None, near=None, near_entity=None, props=None, cell=None):
    out = {}
    p = ctx.placer
    rid = _room(ctx, room) if room else None
    if near_entity and near_entity in ctx.config["entities"]:
        view = _View(ctx)
        rid = rid or S.room_of(view.w, near_entity)
        state = dict(ctx.state(), robot={"room": rid, "hands": {}, "pos": None})
        _, cells = geometry.anchor(state, near_entity)
        if cells:
            room = ctx.config["rooms"][rid]
            reach = reachable_cells(ctx, rid)
            ring = sorted((geometry.near_cells(cells, room) - set(cells)) & reach, key=lambda c: (c[1], c[0]))
            near = list(ring[0]) if ring else list(cells[0])
    for item in tools:
        name = item if isinstance(item, str) else item["type"]
        extra = copy.deepcopy((props or {}).get(name, {}))
        if not isinstance(item, str):
            extra.update(copy.deepcopy(item.get("props", {})))
        new = None
        if on:
            new = p.on(on, name, **extra)
        if new is None and cell is not None:
            new = p.make(name, rid or ctx.config["start_room"], **extra)
            ctx.config["entities"][new]["pos"] = [int(cell[0]), int(cell[1])]
        if new is None:
            target_room = rid or (ctx.state()["entities"].get(on, {}) and S.room_of(_View(ctx).w, on)) or ctx.config["start_room"]
            new = place_reachable(ctx, target_room, name, near=near, **extra)
        if new is None:
            raise SpecError("cleaning.tools: no room for " + name)
        key = item.get("as") if isinstance(item, dict) and item.get("as") else name
        out[key] = new
    return out


@scenario_helper("cleaning.liner")
def liner_helper(ctx, container, liner_type="trash_liner_13gal", installed=True, opened=True):
    rec = _entity(ctx, container)
    slot = (rec.get("slots") or {}).get("liner")
    if slot is None:
        raise SpecError("{} has no liner slot".format(container))
    old = slot.get("item")
    if old:
        ctx.config["entities"].pop(old, None)
        slot["item"] = None
    if not installed:
        return {"liner": None}
    new = ctx.placer.make(liner_type, container)
    lrec = ctx.config["entities"][new]
    lrec.update(relation="part", slot="liner", opened=bool(opened), folded=not opened, seated=bool(opened), rim_folded=bool(opened))
    slot["item"] = new
    return {"liner": new}


@scenario_helper("cleaning.room_mess")
def room_mess_helper(ctx, room, level=0.5, under=True, surfaces=True, floor_kinds=("crumbs", "dust", "hair"), seed_spots=4):
    rid = _room(ctx, room)
    out = {"room": rid}
    view = _View(ctx)
    floor_id = "floor_" + rid
    if floor_id in ctx.config["entities"]:
        rec = ctx.config["entities"][floor_id]
        cell_map = rec.setdefault("_cells", {})
        open_keys = _cells_for(ctx, view, rid, "open", count=seed_spots)
        for key in open_keys:
            part = cell_map.setdefault(key, {})
            for kind in floor_kinds:
                part[kind] = round(min(1.0, max(part.get(kind, 0.0), level * ctx.rng.uniform(0.6, 1.0))), 4)
        if under:
            for key in _cells_for(ctx, view, rid, "under"):
                part = cell_map.setdefault(key, {})
                part["dust"] = round(min(1.0, max(part.get("dust", 0.0), level)), 4)
                part["hair"] = round(min(1.0, max(part.get("hair", 0.0), level * 0.5)), 4)
        _aggregate(rec)
        out["floor"] = floor_id
    dusty = []
    if surfaces:
        for eid, rec in sorted(ctx.config["entities"].items()):
            if rec.get("location") == rid and rec.get("surface") and rec.get("accepts_items") and rec.get("category") not in ("floor",):
                soil = rec.get("soil") if isinstance(rec.get("soil"), dict) else {}
                soil["dust"] = round(max(soil.get("dust", 0.0), level * 0.8), 4)
                rec["soil"] = soil
                dusty.append(eid)
    out["dusty"] = dusty
    return out


@scenario_helper("cleaning.vacuum")
def vacuum_helper(ctx, room=None, on=None, near=None, type_name="stick_vacuum", filter_type="vacuum_filter_foam", attachment=None,
                  bin_load=None, bin_items=None, mode=None, suction=None, eid=None, props=None):
    """A ready-to-use vacuum with its bin/bag and filter fitted (optionally a loaded bin with items inside)."""
    rid = _room(ctx, room) if room else ctx.config["start_room"]
    extra = copy.deepcopy(props or {})
    # a cell the robot can actually get to (generated rooms can box a free cell in behind furniture)
    new = place_reachable(ctx, rid, type_name, near=near, eid=eid, **extra)
    if new is None:
        raise SpecError("cleaning.vacuum: no floor space in " + rid)
    rec = ctx.config["entities"][new]
    out = {"vacuum": new}
    slots = rec.get("slots") or {}
    if "bin" in slots:
        part = ctx.placer.make("vacuum_bin", new)
        prec = ctx.config["entities"][part]
        prec.update(relation="part", slot="bin")
        slots["bin"]["item"] = part
        out["bin"] = part
        if bin_load:
            prec["cl_load"] = {k: float(v) for k, v in bin_load.items()}
            prec["cl_load_g"] = round(sum(prec["cl_load"].values()), 2)
            prec["fill_level"] = round(min(1.0, prec["cl_load_g"] / float(prec.get("capacity_g", 250.0))), 3)
        for i, spec in enumerate(bin_items or []):
            spec = LOOKALIKES.get(spec, spec) if isinstance(spec, str) else spec
            item = ctx.placer.make(spec.get("type", "generic_item"), part, **copy.deepcopy(spec.get("props", {})))
            ctx.config["entities"][item]["relation"] = "in"
            ctx.config["entities"][item]["ingested"] = True
            out["bin_item_{}".format(i + 1)] = item
    if "bag" in slots:
        part = ctx.placer.make("vacuum_bag", new, model=rec.get("bag_model", "C"))
        ctx.config["entities"][part].update(relation="part", slot="bag")
        slots["bag"]["item"] = part
        out["bag"] = part
    if "filter" in slots:
        part = ctx.placer.make(filter_type, new)
        ctx.config["entities"][part].update(relation="part", slot="filter")
        slots["filter"]["item"] = part
        out["filter"] = part
    if attachment:
        part = ctx.placer.make(attachment, new)
        ctx.config["entities"][part].update(relation="part", slot="tool")
        slots["tool"]["item"] = part
        out["attachment"] = part
    settings = rec.setdefault("settings", {})
    if mode:
        settings["mode"] = mode
    if suction:
        settings["suction"] = suction
    return out
