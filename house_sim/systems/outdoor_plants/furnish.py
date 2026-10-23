"""Furnishers: yard structure (order 30), houseplants (72), outdoor items and garden supplies (74).

Robust to any dwelling: every placement tolerates missing rooms and Placer returning None.
Counts are modest (roughly 25-55 entities per house depending on the yard).
"""
import math

from ...generator.place import Placer
from ...kernel import geometry
from ...kernel.plugins import furnisher
from . import common as C
from . import lawn as L
from .species import SPECIES, profile, theta_for

HOUSEPLANTS = ["pothos", "snake_plant", "peace_lily", "fiddle_leaf_fig", "boston_fern", "spider_plant", "rubber_plant", "monstera", "zz_plant", "jade_plant",
               "african_violet", "phalaenopsis_orchid", "aloe_vera"]
FRONT_FLOWERS = ["petunia", "geranium", "marigold", "coneflower", "lavender", "hosta", "chrysanthemum"]
VEGETABLES = ["tomato", "pepper", "kale", "lettuce", "zucchini", "bush_bean"]
WEEDS = ["dandelion", "crabgrass", "plantain_weed", "chickweed"]


# ---------------------------------------------------------------------------
# floor placement (shared by furnishers and scenario helpers; config dicts, not World)
#
# Kernel v2.3 owns the access rules: `geometry.free_floor_cell(..., keep_access=True)` never cuts a hazard-free
# walking route or the only access to anything (a path-blocking record such as a large potted plant is fully
# checked), keeps door clearance (doorway cells and their neighbours, hinged leaves, overhead thresholds) and
# requires the item itself to be reachable; `Placer.floor_item` places through it. On top of that the domain
#   * never puts its floor items in door clearance (strict=True, also for low saucers that carry a pot),
#   * prefers cells not in front of floor-standing openable furniture (cabinet/closet doors swing there),
#   * honours hard `avoid` cells (e.g. a downed line's danger zone) and tries the record rotated 90 deg,
# then creates the entity with `Placer.floor_item(..., cell=...)`.

def sweep_fronts(state, rid, ignore=()):
    """Cells beside floor-standing openable furniture (a cabinet/closet door may swing over them)."""
    room = state["rooms"][rid]
    cells = set()
    for eid in geometry.room_entities(state, rid):
        e = state["entities"][eid]
        if eid in ignore or e.get("mounted") or not e.get("openable") or not geometry.blocks(e) or not e.get("pos"):
            continue
        foot = set(geometry.footprint(e))
        for (x, y) in foot:
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                c = (x + dx, y + dy)
                if c not in foot and geometry.in_grid(room, c):
                    cells.add(c)
    return cells


def _probes(state, rid, record, near, bad, ignore=()):
    """Candidate anchor cells for record whose footprint fits the grid and avoids `bad`, used/blocked cells and
    door clearance, nearest to `near` first (cheap pre-filter; the kernel rule decides)."""
    room = state["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    taken = set(geometry.blocked_cells(state, rid, ignore=ignore)) | set(geometry.doorway_cells(state, rid)) | geometry.door_clearance(state, rid)
    for eid in geometry.room_entities(state, rid):
        if eid not in ignore and geometry.floor_standing(state["entities"][eid]):
            taken.update(geometry.footprint(state["entities"][eid]))
    out = []
    for y in range(rows):
        for x in range(cols):
            cells = geometry.footprint(dict(record, pos=[x, y]))
            if all(geometry.in_grid(room, c) and c not in taken and c not in bad for c in cells):
                out.append((x, y))
    out.sort(key=lambda c: (geometry.chebyshev(c, near), c[1], c[0]))
    return out


def hidden_mount_cells(state, rid, record, ignore=()):
    """Wall cells of mounts hung 0.5-2 m up (not outlets, lights, windows or drawer fronts) that the floor-standing
    `record` would hide: its top (base height `z_m`/`mount_height_m` plus its height) is above the mount (the layout
    audit's "hidden mount"; a mower's z_m is its handle height, so it hides wall racks below about 1.8 m)."""
    try:
        top = float(record.get("mount_height_m", record.get("z_m", 0.0)) or 0.0) + float((record.get("size_m") or [0, 0, 0])[2])
    except (TypeError, ValueError, IndexError):
        return set()
    cells = set()
    if top < 0.55:
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


def floor_spot(state, rid, record, near=None, avoid=(), ignore=(), tries=6, plain=True, prefer_not=()):
    """(cell, rot) for record on rid's floor by the kernel keep_access rule (see above), or (None, None).
    Hard `avoid` cells are never used; cells in front of openable furniture (and `prefer_not` cells) only when
    nothing else qualifies. plain=True falls back to keep_access="plain" (cut no plain walking route) like
    Placer.floor_item when the room's hazard-free routes are already cut by something else (scenario setup wants
    the item); furnishers pass plain=False and skip an item rather than make the robot step over it."""
    if rid not in state["rooms"]:
        return None, None
    hard = {tuple(c) for c in avoid}
    soft = sweep_fronts(state, rid, ignore) | {tuple(c) for c in prefer_not}
    rot0 = record.get("rot", 0) or 0
    rots = [rot0]
    size = geometry.footprint_size(record)
    if size[0] != size[1]:
        rots.append(90 if rot0 in (0, 180) else 0)
    for keep in ((True, "plain") if plain else (True,)):
        fallback = None
        for rot in rots:
            rec = dict(record, rot=rot)

            def hits(cell, zone):
                return bool(set(geometry.footprint(dict(rec, pos=cell))) & zone)
            first = geometry.free_floor_cell(state, rid, near=near, ignore=ignore, record=rec, strict=True, keep_access=keep)
            if first is None:
                continue
            if not hits(first, hard | soft):
                return first, rot
            if fallback is None and not hits(first, hard):
                fallback = (first, rot)
            tried, calls = {tuple(first)}, 0
            for probe in _probes(state, rid, rec, tuple(near) if near is not None else tuple(first), hard | soft, ignore):
                if calls >= tries:
                    break
                if probe in tried:
                    continue
                calls += 1
                cell = geometry.free_floor_cell(state, rid, near=probe, ignore=ignore, record=rec, strict=True, keep_access=keep)
                if cell is None:
                    break
                tried.add(tuple(cell))
                if not hits(cell, hard | soft):
                    return cell, rot
                if fallback is None and not hits(cell, hard):
                    fallback = (cell, rot)
        if fallback is not None:
            return fallback
    return None, None


def floor_place(p, rid, type_name, near=None, eid=None, avoid=(), plain=True, **props):
    """A floor item at `floor_spot` (kernel keep_access rule + domain preferences), created with
    Placer.floor_item. Path-blocking items pass blocks_path=True in props (decided before placing, never
    flipped afterwards). plain: see floor_spot (furnishers pass False). Returns the new id or None when no
    cell qualifies."""
    if rid not in p.c["rooms"]:
        return None
    record = p.reg.make(type_name, rid, **props)
    state = p.state()
    # a tall floor item (mower, wheelbarrow, snow pusher) never stands in front of a wall rack, holder or switch:
    # furnishers skip such cells outright, scenario helpers only prefer other cells
    hidden = hidden_mount_cells(state, rid, record)
    if plain:
        cell, rot = floor_spot(state, rid, record, near=near, avoid=avoid, plain=plain, prefer_not=hidden)
    else:
        cell, rot = floor_spot(state, rid, record, near=near, avoid=set(map(tuple, avoid)) | hidden, plain=plain)
    if cell is None:
        return None
    if rot != (record.get("rot", 0) or 0):
        props["rot"] = rot
    return p.floor_item(rid, type_name, eid=eid, cell=cell, **props)


# ---------------------------------------------------------------------------
# serviceable lights (round-3 report): a raised bed, deck box, pool or compost bin placed along a wall could seal off
# a wall or porch light a person re-lamps from a step stool. Since kernel v2.9 round 5 the kernel's access targets
# keep outdoor wall lights up to geometry.SERVICE_LIGHT_MAX_M (geometry.overhead_light), but still treat indoor lights
# mounted 2.0 m or higher (a garage or mudroom wall light) as ladder-serviced ceiling lights; this guard covers every
# light up to that height in any room. `guarded` keeps every such light that was accessible before the placement
# accessible after it (plain walking graph from the doorways, like the kernel's furniture rule): a piece that seals
# one is removed and retried near the corners farthest from the lights, and left out if every try seals one. A first
# attempt that seals nothing is kept as is, so layouts without the problem do not change.

SERVICE_LIGHT_MAX_M = geometry.SERVICE_LIGHT_MAX_M


def light_targets(state, rid):
    """{light_id: (cells, radius, False)} for mounted lights at most 2.5 m up (geometry.access_targets' shape)."""
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


def accessible_lights(state, rid, lights):
    if not lights:
        return set()
    targets = dict(geometry.access_targets(state, rid, level="doors"))
    targets.update(lights)
    blocked = set(geometry.blocked_cells(state, rid, include_agents=False))
    return {k for k in geometry.access_report(state, rid, blocked, targets)["ok"] if k in lights}


def _far_anchors(room, lights):
    cols, rows = geometry.grid_size(room)
    spots = {(0, 0), (cols - 1, 0), (0, rows - 1), (cols - 1, rows - 1), (cols // 2, 0), (cols // 2, rows - 1), (0, rows // 2), (cols - 1, rows // 2)}
    lit = [tuple(c) for cells, _, _ in lights.values() for c in cells]
    return [list(c) for c in sorted(spots, key=lambda c: (-min(geometry.chebyshev(c, l) for l in lit), c[1], c[0]))]


def guarded(p, rid, make, near=None, tries=4):
    """make(near) -> new id or None (a Placer.furniture/mount call). Keeps serviceable lights accessible (see above)."""
    state = p.state()
    lights = light_targets(state, rid)
    if not lights:
        return make(near)
    before = accessible_lights(state, rid, lights)
    eid = make(near)
    if eid is None or before <= accessible_lights(p.state(), rid, lights):
        return eid
    del p.c["entities"][eid]
    for anchor in _far_anchors(p.c["rooms"][rid], lights)[:tries]:
        eid = make(anchor)
        if eid is None:
            continue
        if before <= accessible_lights(p.state(), rid, lights):
            return eid
        del p.c["entities"][eid]
    return None


def furniture(p, rid, type_name, near=None, **props):
    """Placer.furniture with the serviceable-light guard."""
    return guarded(p, rid, lambda anchor: p.furniture(rid, type_name, near=anchor, **props), near=near)


def ground_cell(state, rid, rng=None, perimeter=True, avoid=()):
    """A walkable, reachable cell for a ground-layer fixture (a drain grate): outside door clearance, not under
    anything floor-standing; on the room's perimeter by default. None if the room has none."""
    room = state["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    blocked = set(geometry.blocked_cells(state, rid, include_agents=False))
    reach = geometry.reachable_cells(state, rid, blocked)
    no_go = geometry.door_clearance(state, rid) | {tuple(c) for c in avoid}
    for eid in geometry.room_entities(state, rid):
        if geometry.floor_standing(state["entities"][eid]):
            no_go.update(geometry.footprint(state["entities"][eid]))
    if perimeter:
        cells = sorted({(x, 0) for x in range(cols)} | {(x, rows - 1) for x in range(cols)} | {(0, y) for y in range(rows)} | {(cols - 1, y) for y in range(rows)})
    else:
        cells = [(x, y) for y in range(rows) for x in range(cols)]
    options = [c for c in cells if c in reach and c not in no_go]
    if not options:
        return None
    return list(rng.choice(options)) if rng is not None else list(options[0])


# ---------------------------------------------------------------------------
# record builders shared with scenario helpers (config dicts, not World)

def pot_props(diameter_cm, kind="potting_mix", rwc_value=0.5, material="plastic", drainage=True):
    d = float(diameter_cm)
    depth = round(d * 0.9, 1)
    cap = round(math.pi * (d / 200.0) ** 2 * depth / 100.0 * 1000.0 * 0.85, 2)
    soil_l = round(cap * (1 - 2.5 / depth), 3) if kind != "none" else 0.0
    theta = theta_for(rwc_value, kind) if kind != "none" else 0.0
    top = theta_for(max(0.0, rwc_value - 0.15), kind) if kind != "none" else 0.0
    from .species import medium_params
    bulk = medium_params(kind)["bulk_kg_l"]
    base = 0.08 * d / 15.0 * (3.0 if material in ("terracotta", "ceramic") else 1.0)
    return {"diameter_cm": d, "depth_cm": depth, "size_m": [d / 100.0, d / 100.0, depth / 100.0], "inner_size_m": [d / 100.0 - 0.01, d / 100.0 - 0.01, depth / 100.0 - 0.01],
            "capacity_l": cap, "volume_l": round(cap * 1.2, 2), "material": material, "drainage_hole": drainage, "base_mass_kg": round(base, 3),
            "mass_kg": round(base + soil_l * (bulk + theta), 3),
            "medium": {"kind": kind, "soil_l": soil_l, "moisture": round(theta, 4), "moisture_top": round(top, 4), "compaction": 0.3, "headspace_cm": 2.5 if kind != "none" else depth}}


def plant_props(species, rng, size="medium", **extra):
    p = profile(species)
    heights = {"small": 0.25, "medium": 0.45, "large": 1.2}
    h = heights.get(size, 0.45)
    w = {"small": 0.2, "medium": 0.35, "large": 0.6}.get(size, 0.35)
    leaves = {"healthy": int(rng.randint(14, 40)), "yellowing": 0, "dead": 0}
    props = {"species": species, "size_m": [w, w, h], "mass_kg": round(0.3 + h * 1.5, 2), "leaves": leaves, "label": label_for(species),
             "yaw_deg": float(rng.choice([0, 90, 180, 270])), "lean_deg": 0.0, "root_ball_cm": {"small": 8.0, "medium": 12.0, "large": 25.0}.get(size, 12.0)}
    props["_yaw_start"] = props["yaw_deg"]
    if p.get("kind") == "herb":
        props["foliage_g"] = float(rng.randint(40, 90))
    props.update(extra)
    return props


def label_for(species):
    p = profile(species)
    kind = p.get("kind")
    if kind in ("houseplant", "succulent", "artificial"):
        return "potted plant"
    if kind == "herb":
        return "herb plant"
    if kind == "weed":
        return "small plant"
    if kind == "vegetable":
        return "vegetable plant"
    if kind in ("shrub",):
        return "shrub"
    return "plant"


def add_potted_plant(p, location, species, rng, on=None, floor_room=None, near=None, size="medium", rwc_value=None, pot_d=None, saucer=True, material=None,
                     plain=True, **extra):
    """Place saucer + pot + plant. Returns dict(plant, pot, saucer) or None if nothing fits. A large floor plant is
    path-blocking (blocks_path=True is passed when placing). plain: see floor_spot (furnishers pass False)."""
    prof = profile(species)
    kind = prof.get("mix", "potting_mix") if species != "artificial" else "foam"
    if rwc_value is None:
        rwc_value = round(rng.uniform(0.2, 0.9), 2)
    d = pot_d or {"small": 12.0, "medium": 17.0, "large": 30.0}.get(size, 17.0)
    material = material or rng.choice(["plastic", "plastic", "terracotta", "ceramic"])
    pprops = pot_props(d, kind if kind != "foam" else "potting_mix", rwc_value, material, drainage=species != "artificial")
    if species == "artificial":
        pprops["medium"] = {"kind": "foam", "soil_l": round(pprops["capacity_l"] * 0.8, 3), "moisture": 0.0, "moisture_top": 0.0, "compaction": 0.5, "headspace_cm": 2.0}
    saucer_id = None
    holder = None
    if saucer:
        sd = d + 3.0
        sprops = {"surface_size_m": [sd / 100.0, sd / 100.0], "size_m": [sd / 100.0, sd / 100.0, 0.03], "capacity_l": round(math.pi * (sd / 200.0) ** 2 * 0.025 * 1000 * 0.8, 3)}
        if on:
            saucer_id = p.on(on, "plant_saucer", **sprops)
        elif floor_room:
            if size == "large":
                sprops["blocks_path"] = True      # a large floor plant is an obstacle: placed keeping access
            saucer_id = floor_place(p, floor_room, "plant_saucer", near=near, plain=plain, **sprops)
        if saucer_id is None:
            return None
        holder = saucer_id
        pot_id = p.on(saucer_id, "plant_pot", spos=[0.01, 0.01], **pprops)
        if pot_id is None:
            p.c["entities"].pop(saucer_id, None)
            return None
    else:
        if on:
            pot_id = p.on(on, "plant_pot", **pprops)
        elif floor_room:
            pot_id = floor_place(p, floor_room, "plant_pot", near=near, plain=plain, **pprops)
        else:
            pot_id = None
        if pot_id is None:
            return None
    t = "herb_plant" if prof.get("kind") == "herb" else "houseplant"
    props = plant_props(species, rng, size, **extra)
    if species == "artificial":
        props["artificial"] = True
        props["species"] = rng.choice(["pothos", "fiddle_leaf_fig", "boston_fern"])
        props["_looks_like"] = props["species"]
    plant_id = p.make(t, pot_id, **props)
    p.c["entities"][plant_id]["relation"] = "in"
    if species == "artificial":
        p.c["entities"][plant_id]["species"] = "artificial"
    return {"plant": plant_id, "pot": pot_id, "saucer": saucer_id}


def add_bed_plant(p, bed_id, species, rng, spos=None, role=None, size="medium", t="garden_plant", **extra):
    props = plant_props(species, rng, size, **extra)
    props["role"] = role or ("weed" if profile(species).get("weed") else ("crop" if profile(species).get("kind") in ("vegetable", "fruit", "herb") else "ornamental"))
    if props["role"] == "weed":
        props["size_m"] = [0.15, 0.15, 0.12]
        props["mass_kg"] = 0.05
        props["label"] = "small plant"
    eid = p.on(bed_id, t, spos=spos, **props)
    return eid


def make_lawn(config, registry, rid, rng, eid=None, height_mm=None, moisture=None):
    room = config["rooms"][rid]
    grid = L.build_grid(room, config["entities"], rid, rng, height_mm=height_mm if height_mm is not None else rng.choice([55.0, 70.0, 85.0, 95.0]),
                        moisture=moisture if moisture is not None else round(rng.uniform(0.45, 0.9), 2))
    for i in range(int(grid["cols"]) * int(grid["rows"])):
        grid["shade"][i] = 0.0
    record = registry.make("lawn", rid, size_m=[float(room["size_m"][0]), float(room["size_m"][1]), 0.05], grid=grid,
                           label="{} lawn".format(room.get("label", rid).lower()))
    record["pos"] = [0, 0]
    eid = eid or "lawn_" + rid
    config["entities"][eid] = record
    return eid


def rebuild_lawn_kinds(config, lid):
    record = config["entities"][lid]
    rid = record["location"]
    old = record.get("grid")
    new = L.build_grid(config["rooms"][rid], config["entities"], rid, None, height_mm=70.0, moisture=0.6)
    if old and old.get("cols") == new["cols"] and old.get("rows") == new["rows"]:
        old["kind"] = new["kind"]
        old["edge"] = new["edge"]
        old["ring_mm"] = [max(old["height_mm"][i], 0) if new["ring_mm"][i] >= 0 else -1.0 for i in range(len(new["ring_mm"]))]
    else:
        record["grid"] = new


def walkway_cells(config, rid):
    room = config["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    house = None
    street = None
    for cid, conn in sorted(config.get("connections", {}).items()):
        if rid not in conn["rooms"]:
            continue
        other = conn["rooms"][0] if conn["rooms"][1] == rid else conn["rooms"][1]
        orec = config["rooms"][other]
        cell = conn.get("cells", {}).get(rid)
        if cell is None:
            continue
        if not orec.get("outdoor") or orec.get("type") == "porch":
            house = tuple(cell)
        elif orec.get("type") in ("sidewalk",):
            street = tuple(cell)
    if house is None or street is None:
        return []
    state = {"rooms": config["rooms"], "entities": {}, "people": {}, "pets": {}, "robot": {"room": rid}}
    path = geometry.find_path(state, rid, house, [street])
    if path is None:
        return []
    return [list(house)] + [list(c) for c in path]


def house_wall(config, rid):
    room = config["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    for cid, conn in sorted(config.get("connections", {}).items()):
        if rid not in conn["rooms"]:
            continue
        other = conn["rooms"][0] if conn["rooms"][1] == rid else conn["rooms"][1]
        orec = config["rooms"][other]
        if (orec.get("outdoor") and orec.get("type") not in C.HOUSE_ATTACHED) or orec.get("type") in ("garage", "shed"):
            continue
        cell = conn.get("cells", {}).get(rid)
        if not cell:
            continue
        if cell[1] == 0:
            return "n"
        if cell[1] == rows - 1:
            return "s"
        if cell[0] == 0:
            return "w"
        if cell[0] == cols - 1:
            return "e"
    return "n"


FENCE_HEIGHT_M = 1.8


def fence_extent(room):
    """size_m of a yard fence record: the fence runs along every yard edge but the house wall, so its box is the yard's
    (pos [0, 0]). The geometric camera judges a close look at the box (camera.inspect_check: a 1 m stub in the corner
    was "too far to make out the details" from most of the yard -- the D3 preview of T-0caf47d0f789f5-01); the fence
    inspector still reports only the panels within FENCE_VIEW_M of the robot that its frames covered. A ground layer:
    it occludes nothing and takes no floor cells."""
    return [float(room["size_m"][0]), float(room["size_m"][1]), FENCE_HEIGHT_M]


def fence_panels(config, rid, house_edge):
    room = config["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    cells = []
    for edge in ("n", "e", "s", "w"):
        if edge == house_edge:
            continue
        cells.extend(C.edge_cells(room, edge))
    seen, ordered = set(), []
    for c in cells:
        key = tuple(c)
        if key not in seen:
            seen.add(key)
            ordered.append(c)
    panels = []
    for i in range(0, len(ordered), 5):
        chunk = ordered[i:i + 5]
        panels.append({"id": "panel_{}".format(len(panels) + 1), "cells": chunk, "appearance": "weathered boards"})
    return panels


# ---------------------------------------------------------------------------
# yard structure

@furnisher("outdoor_plants.yard", order=30)
def furnish_yard(config, rng, opts, registry):
    p = Placer(config, registry, rng)
    rooms = config["rooms"]
    floors = int((opts.get("plan") or {}).get("floors", 1) or 1)
    by_type = {}
    for rid in sorted(rooms):
        by_type.setdefault(rooms[rid]["type"], []).append(rid)
    month = 10
    # outdoor floors get surface fields for leaves, snow and ice
    for rid in sorted(rooms):
        room = rooms[rid]
        floor = config["entities"].get("floor_" + rid)
        if floor and room.get("outdoor"):
            floor.setdefault("snow_cm", 0.0)
            floor.setdefault("ice", 0.0)
            floor.setdefault("deicer", {})
            floor["cured_days"] = 3650
            if room["type"] in ("driveway", "sidewalk", "patio", "poolside"):
                floor.setdefault("soil", {})["leaves"] = round(rng.uniform(0.0, 0.3), 2)
    for rid in by_type.get("front_yard", []):
        cells = walkway_cells(config, rid)
        if cells:
            walk = registry.make("walkway", rid, cells=cells, area_m2=round(len(cells) * 0.25 * 2, 2), label="front walkway",
                                 soil={"leaves": round(rng.uniform(0.0, 0.4), 2)}, crack_weeds=rng.randint(0, 3))
            walk["pos"] = list(cells[0])
            config["entities"][p.new_id("walkway")] = walk
        edge = house_wall(config, rid)
        bed = furniture(p, rid, "garden_bed", near=_edge_anchor(rooms[rid], edge), label="front flower bed", size_m=[3.0, 1.0, 0.05], surface_size_m=[3.0, 1.0],
                          sun=rng.choice(["full", "full", "part"]))
        if bed:
            for i in range(rng.randint(2, 3)):
                add_bed_plant(p, bed, rng.choice(FRONT_FLOWERS), rng, flowers={"bud": rng.randint(0, 3), "open": rng.randint(1, 5), "spent": rng.randint(0, 4)})
            if rng.random() < 0.6:
                add_bed_plant(p, bed, rng.choice(WEEDS), rng)
        if rng.random() < 0.6:
            furniture(p, rid, "shrub", wall=False, species=rng.choice(["boxwood", "hydrangea", "lilac", "spirea", "juniper"]), label="shrub",
                        branches=_branches(rng, 6))
        if rng.random() < 0.5:
            furniture(p, rid, "tree", wall=False, species="maple", label="shade tree")
        _gutter(p, config, registry, rid, edge, 2.7 * floors, "front gutter")
        if rng.random() < 0.7:
            _roof_face(p, config, registry, rid, edge, "front", floors, "clear")
        make_lawn(config, registry, rid, rng)
    for rid in by_type.get("backyard", []):
        edge = house_wall(config, rid)
        fence = registry.make("fence", rid, panels=fence_panels(config, rid, edge), label="backyard fence", size_m=fence_extent(rooms[rid]))
        fence["pos"] = [0, 0]
        fence["ground_layer"] = True
        config["entities"][p.new_id("fence")] = fence
        if not by_type.get("garden") and rng.random() < 0.6:
            bed = furniture(p, rid, "raised_bed", wall=True, label="vegetable bed")
            if bed:
                for i in range(rng.randint(2, 3)):
                    add_bed_plant(p, bed, rng.choice(VEGETABLES), rng)
        if rng.random() < 0.7:
            furniture(p, rid, "tree", wall=False, species="maple", label="maple tree")
        if rng.random() < 0.35:
            furniture(p, rid, "hedge", wall=True, species="privet", label="privet hedge", branches=_branches(rng, 4), _target={"height_m": 1.5})
        _gutter(p, config, registry, rid, edge, 2.7 * floors, "back gutter")
        if rng.random() < 0.6:
            _roof_face(p, config, registry, rid, edge, "back", floors, "clear")
        if rng.random() < 0.3:
            config.setdefault("_op_grates", []).append([rid, "yard area drain", 0.5, 0.8])
        make_lawn(config, registry, rid, rng)
    for rid in by_type.get("garden", []):
        for i in range(2 if rooms[rid]["size_m"][0] >= 5 else 1):
            bed = furniture(p, rid, "raised_bed", wall=True, label="raised vegetable bed" if i == 0 else "herb bed")
            if bed:
                pool = VEGETABLES if i == 0 else ["basil", "rosemary", "thyme", "chives", "mint"]
                for _ in range(2):
                    add_bed_plant(p, bed, rng.choice(pool), rng)
                if rng.random() < 0.4:
                    add_bed_plant(p, bed, rng.choice(WEEDS), rng)
    for rid in by_type.get("driveway", []):
        # ground-level grates are walkable ground layers that the kernel's access rules do not protect from later
        # furniture (a parked car), so they are laid out by the final pass (outdoor_plants.finish)
        config.setdefault("_op_grates", []).append([rid, "driveway drain", 0.4, 0.6])
    for rid in by_type.get("sidewalk", []):
        furniture(p, rid, "mailbox", wall=True, label="curbside mailbox", fallback=False)
    for rid in by_type.get("poolside", []):
        _pool(p, config, registry, rid, rng)


def _drain_grate(p, config, registry, rid, rng, label, max_blockage, max_debris):
    blockage, debris = round(rng.uniform(0.0, max_blockage), 2), round(rng.uniform(0.0, max_debris), 2)
    cell = ground_cell(p.state(), rid, rng)
    if cell is None:
        return None
    g = registry.make("drain_grate", rid, label=label, blockage=blockage, debris_kg=debris)
    g["pos"] = cell
    gid = p.new_id("drain_grate")
    config["entities"][gid] = g
    return gid


def _edge_anchor(room, edge):
    cols, rows = geometry.grid_size(room)
    return {"n": [cols // 2, 0], "s": [cols // 2, rows - 1], "w": [0, rows // 2], "e": [cols - 1, rows // 2]}.get(edge, [0, 0])


def _branches(rng, n):
    classes = ["healthy"] * 6 + ["dead", "crossing", "diseased"]
    out = []
    for i in range(n):
        out.append({"id": "b{}".format(i + 1), "diam_cm": round(rng.uniform(0.6, 3.0), 1), "height_m": round(rng.uniform(0.4, 1.8), 2), "class": rng.choice(classes)})
    return out


def _edge_rot(edge):
    """Rotation of a wall-long record (size_m [length, depth]) running along a room edge."""
    return 90 if edge in ("w", "e") else 0


def _gutter(p, config, registry, rid, edge, height, label):
    room = config["rooms"][rid]
    cells = C.edge_cells(room, edge)
    if not cells:
        return None
    length = round(len(cells) * C.CELL, 2)
    gid = p.mount(rid, "gutter", cell=list(cells[0]), height=round(height, 2), label=label, cells=cells, length_m=length,
                  debris_kg=round(p.rng.uniform(0.2, 3.5), 2), size_m=[length, 0.13, 0.1], rot=_edge_rot(edge))
    clog, leaves = round(p.rng.uniform(0.0, 0.3), 2), round(p.rng.uniform(0.0, 0.4), 2)
    cell = downspout_cell(p.state(), rid, cells)
    if cell is not None:
        # the downspout comes down the wall to a floor-level outlet: a floor-standing mount (kernel floor_standing)
        p.mount(rid, "downspout", cell=cell, label=label.replace("gutter", "downspout"), gutter=gid, clog=clog, outlet_leaves_kg=leaves,
                size_m=[0.08, 0.08, round(height, 2)])
    return gid


def downspout_cell(state, rid, cells):
    """Where a downspout comes down along its gutter: an end cell if possible, otherwise the gutter cell nearest
    to the far end, outside door clearance (it is a tall floor-standing pipe), not on anything floor-standing, with
    reachable ground beside it (its outlet is cleared and flow-tested from the ground). None if none."""
    if not cells:
        return None
    room = state["rooms"][rid]
    blocked = set(geometry.blocked_cells(state, rid, include_agents=False))
    reach = geometry.reachable_cells(state, rid, blocked)
    taken = geometry.door_clearance(state, rid) | blocked
    for eid in geometry.room_entities(state, rid):
        if geometry.floor_standing(state["entities"][eid]):
            taken.update(geometry.footprint(state["entities"][eid]))
    first = tuple(cells[-1])
    order = [tuple(cells[-1]), tuple(cells[0])] + sorted((tuple(c) for c in cells), key=lambda c: (geometry.chebyshev(c, first), c[1], c[0]))
    for c in order:
        if c in taken or not geometry.in_grid(room, c):
            continue
        if (geometry.near_cells([c], room) - {c}) & reach:
            return [c[0], c[1]]
    return None


def _roof_face(p, config, registry, rid, edge, face, floors, view):
    room = config["rooms"][rid]
    cells = C.edge_cells(room, edge)
    if not cells:
        return None
    return p.mount(rid, "roof_face", cell=list(cells[0]), height=round(2.7 * floors + 0.5, 2), face=face, sightline=view, label="{} roof".format(face),
                   size_m=[round(len(cells) * C.CELL, 2), 0.1, 3.0], rot=_edge_rot(edge))


def _skimmer_cell(state, rid, pool_id):
    """A deck cell beside the pool's edge for the skimmer well (built into the pool wall): free, outside door
    clearance, with reachable deck next to it. None if the pool has no such edge cell."""
    room = state["rooms"][rid]
    pool = state["entities"][pool_id]
    own = set(geometry.footprint(pool))
    blocked = set(geometry.blocked_cells(state, rid, include_agents=False))
    reach = geometry.reachable_cells(state, rid, blocked)
    taken = geometry.door_clearance(state, rid) | blocked
    for eid in geometry.room_entities(state, rid):
        if geometry.floor_standing(state["entities"][eid]):
            taken.update(geometry.footprint(state["entities"][eid]))
    ring = sorted({(x + dx, y + dy) for (x, y) in own for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))} - own)
    for c in ring:
        if geometry.in_grid(room, c) and c not in taken and c in reach and (geometry.near_cells([c], room) - {c}) & reach:
            return list(c)
    return None


def _pool(p, config, registry, rid, rng):
    room = config["rooms"][rid]
    pid = furniture(p, rid, "pool", wall=False, size_m=[min(5.0, room["size_m"][0] - 2.5), min(3.0, room["size_m"][1] - 2.5), 1.5],
                      surface_size_m=[min(5.0, room["size_m"][0] - 2.5), min(3.0, room["size_m"][1] - 2.5)], fallback=False)
    if pid is None:
        return
    season = "closed" if rng.random() < 0.5 else "open"
    config["entities"][pid]["season"] = season
    config["entities"][pid]["debris_kg"] = round(rng.uniform(0.0, 0.6), 2)
    cell = _skimmer_cell(p.state(), rid, pid)
    sk = p.mount(rid, "skimmer", cell=cell, label="skimmer well") if cell else p.mount(rid, "skimmer", label="skimmer well", strict=True)
    if sk:
        basket = p.make("skimmer_basket", sk, label="skimmer basket", load_kg=round(rng.uniform(0.0, 0.8), 2), slot="basket")
        config["entities"][basket]["relation"] = "part"
        config["entities"][sk]["slots"] = {"basket": {"accepts": ["skimmer_basket"], "item": basket, "access": "none"}}
    guarded(p, rid, lambda anchor: p.mount(rid, "pool_pump", near=anchor, label="pool pump", on=season == "open"))
    st = guarded(p, rid, lambda anchor: p.mount(rid, "rescue_station", near=anchor, label="rescue equipment post"))
    if st:
        p.hang(st, "ring_buoy", label="ring buoy")


# ---------------------------------------------------------------------------
# houseplants

@furnisher("outdoor_plants.houseplants", order=72)
def furnish_houseplants(config, rng, opts, registry):
    p = Placer(config, registry, rng)
    rooms = config["rooms"]
    count = 0
    for rid in sorted(rooms):
        kind = rooms[rid]["type"]
        if kind == "living_room":
            windows = [e for e in sorted(config["entities"]) if config["entities"][e].get("category") == "window" and config["entities"][e].get("location") == rid]
            near = config["entities"][windows[0]]["pos"] if windows else None
            if rng.random() < 0.8:
                add_potted_plant(p, rid, rng.choice(["fiddle_leaf_fig", "monstera", "rubber_plant", "snake_plant"]), rng, floor_room=rid, near=near, size="large", plain=False)
                count += 1
            tables = p.find("side_table", rid) + p.find("coffee_table", rid)
            if tables and rng.random() < 0.8:
                if add_potted_plant(p, rid, rng.choice(HOUSEPLANTS), rng, on=tables[0], size="small"):
                    count += 1
        elif kind == "kitchen" and rng.random() < 0.5:
            counters = p.find("counter", rid)
            if counters and add_potted_plant(p, rid, "basil", rng, on=counters[0], size="small", pot_d=13.0):
                count += 1
        elif kind in ("office", "bedroom") and rng.random() < 0.35 and count < 4:
            surfaces = p.find("desk", rid) + p.find("nightstand", rid) + p.find("dresser", rid)
            if surfaces and add_potted_plant(p, rid, rng.choice(HOUSEPLANTS), rng, on=surfaces[0], size="small"):
                count += 1


# ---------------------------------------------------------------------------
# outdoor items, supplies and tools

@furnisher("outdoor_plants.outdoor_items", order=74)
def furnish_outdoor_items(config, rng, opts, registry):
    p = Placer(config, registry, rng)
    rooms = config["rooms"]
    by_type = {}
    for rid in sorted(rooms):
        by_type.setdefault(rooms[rid]["type"], []).append(rid)
    region = opts.get("region", "us")
    cold = region in ("ca", "us", "eu", "jp")
    terraces = by_type.get("patio", []) + by_type.get("deck", [])
    for rid in terraces:
        tables = p.find("outdoor_table", rid)
        chairs = p.find("outdoor_chair", rid)
        cushions_out = rng.random() < 0.5
        box = furniture(p, rid, "deck_box", wall=True, leaky=rng.random() < 0.25) if rng.random() < 0.5 else None
        for i, chair in enumerate(chairs[:4]):
            config["entities"][chair]["seat_kind"] = "chair"
            if cushions_out:
                cid = p.on(chair, "outdoor_cushion", label="seat cushion", fits="chair_seat", color=rng.choice(["navy", "beige", "green"]))
            elif box:
                p.inside(box, "outdoor_cushion", label="seat cushion", fits="chair_seat")
        if tables and rng.random() < 0.6:
            base = p.make("umbrella_base", tables[0], label="umbrella base", ballast_kg=float(rng.choice([0.0, 20.0, 20.0])))
            config["entities"][base]["relation"] = "under"
            config["entities"][base]["revealed"] = True
            p.make("patio_umbrella", base, label="patio umbrella", open=False, strapped=True)
        if rng.random() < 0.4:
            floor_place(p, rid, "furniture_cover", label="table set cover", fits_kind=["table"], plain=False)
    yards = by_type.get("backyard", []) + by_type.get("garden", [])
    if yards and rng.random() < 0.6:
        rid = yards[-1]
        b = furniture(p, rid, "compost_bin", wall=True, label="compost bin")
        if b:
            comp = dict(config["entities"][b]["compost"])
            comp.update(greens_kg=round(rng.uniform(10, 40), 1), browns_kg=round(rng.uniform(15, 50), 1), moisture=round(rng.uniform(0.35, 0.7), 2),
                        o2=round(rng.uniform(0.2, 0.9), 2), temp_c=round(rng.uniform(15, 55), 1), maturity=round(rng.uniform(0.1, 0.7), 2),
                        last_turned_s=-float(rng.randint(1, 12)) * 86400.0)
            config["entities"][b]["compost"] = comp
    if by_type.get("backyard") and rng.random() < 0.3:
        rid = by_type["backyard"][0]
        pit = furniture(p, rid, "fire_pit", wall=False, label="fire pit", ash_kg=round(rng.uniform(0.5, 4.0), 1))
        if pit:
            floor_place(p, rid, "metal_ash_can", near=config["entities"][pit]["pos"], label="metal ash can", plain=False)
            if rng.random() < 0.6:
                rack = furniture(p, rid, "firewood_rack", wall=True, label="firewood rack")
                if rack:
                    for _ in range(rng.randint(3, 5)):
                        p.inside(rack, "firewood_log", label="split firewood log")
    store = (by_type.get("shed") or by_type.get("garage") or [None])[0]
    if store:
        rack = p.mount(store, "garden_tool_rack", height=1.4, label="garden tool rack", strict=True)
        if rack:
            for t in ("leaf_rake", "shovel", "garden_fork"):
                p.hang(rack, t, home=rack)
            if rng.random() < 0.5:
                p.hang(rack, "bow_rake", home=rack)
        shelves = p.find("shelf_unit", store)
        shelf = shelves[0] if shelves else None
        if shelf:
            for t, props in (("bypass_pruners", {}), ("trowel", {}), ("garden_gloves", {}), ("twine_roll", {}),
                             ("liquid_fertilizer", {"shelf": 3}), ("potting_mix_bag", {"shelf": 0}), ("leaf_bag", {"open": False, "shelf": 0})):
                kwargs = dict(props)
                sh = kwargs.pop("shelf", None)
                p.inside(shelf, t, shelf=sh if sh is not None else rng.randrange(1, 3), home=shelf, **kwargs)
            if cold:
                p.inside(shelf, "ice_melt_bag", shelf=0, home=shelf)
        if by_type.get("front_yard") or by_type.get("backyard"):
            _yard_equipment(p, config, by_type, rng, cold, rack, shelf)
    elif terraces or by_type.get("balcony"):
        rid = (terraces or by_type.get("balcony"))[0]
        if rng.random() < 0.5:
            add_potted_plant(p, rid, rng.choice(["geranium", "petunia", "rosemary", "lemon_tree"]), rng, floor_room=rid, size="medium", saucer=True,
                             material="terracotta", plain=False)
    for rid in by_type.get("balcony", []):
        if rng.random() < 0.6:
            add_potted_plant(p, rid, rng.choice(["geranium", "petunia", "basil", "lavender"]), rng, floor_room=rid, size="medium", material="terracotta", plain=False)
    for rid in by_type.get("porch", []):
        if rng.random() < 0.4:
            floor_place(p, rid, "pumpkin", label="pumpkin", carved=False, plain=False)


def _yard_equipment(p, config, by_type, rng, cold, rack, shelf):
    """Lawn and snow equipment for a house with a yard, stored the way people store it: the snow pusher hangs on
    the tool rack, the folded tarp lies on the bottom shelf, and the mower stands on the floor of the shed or
    garage with the most free floor. A wheelbarrow only goes indoors when that store keeps at least half of its
    floor free -- in a garage full of car, laundry and workbench it is parked outside against the house wall
    instead. Floor items go through floor_place (Placer.floor_item, kernel keep_access) without the plain
    fallback: an item that would make the robot step over it is left out (a tiny shed with a bicycle has no
    mower)."""
    stores = by_type.get("shed", []) + by_type.get("garage", [])

    def roomiest(extra):
        return sorted(stores, key=lambda r: (-round(p.free_fraction(r, extra=extra), 3), stores.index(r)))

    def footprint_cells(type_name):
        cols, rows = geometry.footprint_size(p.reg.make(type_name, stores[0]))
        return cols * rows

    for rid in roomiest(footprint_cells("lawn_mower")):
        if floor_place(p, rid, "lawn_mower", label="battery lawn mower", plain=False):
            break
    if rng.random() < 0.4:
        cells, placed = footprint_cells("wheelbarrow"), None
        for rid in roomiest(cells):
            if p.free_fraction(rid, extra=cells) >= STORE_MIN_FREE:
                placed = floor_place(p, rid, "wheelbarrow", plain=False)
                if placed:
                    break
        if placed is None:
            for rid in by_type.get("backyard", []) + by_type.get("garden", []):
                near = _edge_anchor(config["rooms"][rid], house_wall(config, rid))
                if floor_place(p, rid, "wheelbarrow", near=near, plain=False):
                    break
    if cold and rng.random() < 0.6:
        if not (rack and p.hang(rack, "snow_pusher", home=rack)):
            for rid in roomiest(2):
                if floor_place(p, rid, "snow_pusher", plain=False):
                    break
    if not (shelf and p.inside(shelf, "tarp", shelf=0, home=shelf)):
        for rid in roomiest(1):
            if floor_place(p, rid, "tarp", plain=False):
                break


STORE_MIN_FREE = 0.5    # share of a shed/garage floor that stays free when a wheelbarrow is parked inside


# ---------------------------------------------------------------------------
# final pass after every other furnisher (order 98, before the robot dock and residents are placed)

@furnisher("outdoor_plants.finish", order=98)
def furnish_finish(config, rng, opts, registry):
    """Lay out the drain grates the yard pass scheduled (flush ground-layer fixtures: the kernel's access rules do
    not keep later furniture such as a parked car off them, so they go down last, on a reachable perimeter cell
    outside door clearance and not under anything floor-standing), and refresh lawn patch kinds for the blockers
    other furnishers added in the yards."""
    p = Placer(config, registry, rng)
    for rid, label, max_blockage, max_debris in config.pop("_op_grates", []):
        if rid in config["rooms"]:
            _drain_grate(p, config, registry, rid, rng, label, max_blockage, max_debris)
    ents = config["entities"]
    for lid in [e for e in sorted(ents) if ents[e].get("type") == "lawn"]:
        rebuild_lawn_kinds(config, lid)
    # (phase 1, adoption request outdoor_plants-5: the generation-1 record shims -- roof faces' old `view` word, no
    # wheelbarrow `chocked` -- are gone; generation-1 houses carry the tier-1 fields like every other house, with
    # the main session's gen-1 digest re-record)
