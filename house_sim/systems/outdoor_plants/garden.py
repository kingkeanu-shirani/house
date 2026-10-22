"""Garden beds, planting holes, sowing, seed trays and seedlings, weeding, mulch,
stakes and ties, bed covers (frost cloth / plastic / shade cloth) and produce.

Fidelity G/P-approx:
* Bed root zone = area x 30 cm of soil (moisture as volumetric content); water from
  cans/hoses infiltrates with flow-dependent runoff (compacted soil sheds more) and
  overhead watering wets foliage (disease risk if leaves stay wet overnight).
* Positions on a bed are meters (spos). Holes are entities with diameter/depth; buried
  lines (hidden, flagged when located) are struck when a hole reaches their depth.
* Mulch is a depth grid (0.25 m cells) with stem clearance, smothering of low plants
  and buried seed rows; weeds left under mulch regrow.
* Weed roots: taproots snap unless levered out with a tool in moist soil; rhizomes leave
  fragments that regrow after ~10 days; hoeing cuts every small plant in its strip.
* Germination depends on sowing depth (light-germinating seeds must stay at the
  surface), moisture and soil temperature; seeds wash out under a strong jet.
"""
import math

from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, inspector, system
from . import common as C
from . import plants as P
from .species import medium_params, profile, rwc, theta_for

MULCH_CELL = 0.25
RUNOFF = {"trickle": 0.0, "gentle": 0.1, "full": 0.45, "mist": 0.0, "can": 0.05, "rose": 0.0}
RIPE_COLORS = {"tomato": ("red", "deep red", "orange-red"), "pepper": ("red", "yellow", "orange", "glossy green"), "strawberry": ("red",),
               "raspberry": ("deep red", "red"), "apple": ("red", "red-yellow"), "lemon": ("yellow",), "fig": ("purple", "brown-purple")}
HARVEST_OK = {"twist": {"twist", "snap", "cut"}, "cut": {"cut"}, "snap": {"snap", "cut", "pinch"}, "pinch": {"pinch", "cut"},
              "roll": {"roll"}, "lift_twist": {"lift_twist", "cut"}, "pull": {"pull", "dig"}}


# ---------------------------------------------------------------------------
# beds: water

def bed_area(bed):
    size = bed.get("size_m") or [3.0, 1.0]
    return float(size[0]) * float(size[1])


def plants_on(w, bed_id, include_weeds=True):
    result = []
    for child in C.kids_of_type(w, bed_id, "plant"):
        record = w.s["entities"][child]
        if include_weeds or record.get("role") != "weed":
            result.append(child)
    return result


def add_water_to_bed(w, bed_id, liters, flow="gentle", at="soil"):
    bed = w.s["entities"][bed_id]
    medium = bed.get("medium") or {}
    kind = medium.get("kind", "garden_soil")
    params = medium_params(kind)
    soil_l = P.soil_liters(bed)
    compaction = float(medium.get("compaction", 0.3))
    runoff_frac = RUNOFF.get(flow, 0.1)
    if flow in ("gentle", "full"):
        runoff_frac = min(0.9, runoff_frac + 0.4 * max(0.0, compaction - 0.3))
    if bed.get("mulch"):
        runoff_frac *= 0.6
    theta = float(medium.get("moisture", 0.2))
    room_for = max(0.0, (params["sat"] - theta) * soil_l)
    runoff = liters * runoff_frac
    absorbed = min(liters - runoff, room_for)
    runoff += (liters - runoff) - absorbed
    m = w.mut(bed_id)
    md = m.setdefault("medium", {})
    md["moisture"] = round(theta + absorbed / soil_l, 4)
    top_gain = absorbed / max(1.0, soil_l * 0.15)
    md["moisture_top"] = round(min(params["sat"], max(md["moisture"], float(md.get("moisture_top", theta)) + top_gain)), 4)
    if runoff > 0.05:
        C.stat(w, "runoff_l", round(runoff, 2))
        rid = C.room_of(w, bed_id)
        _wet_hardscape(w, rid, runoff)
        C.note(w, "runoff", source=bed_id, liters=round(runoff, 2))
    if flow == "full":
        for pid in plants_on(w, bed_id):
            prec = w.s["entities"][pid]
            if float(prec.get("planted_s", -1e9)) > float(w.s.get("time_s", 0)) - 3 * C.DAY:
                C.set_status(w, pid, roots_exposed=True)
        for sowing in m.get("sowings", []):
            if not sowing.get("germinated"):
                sowing["washed_out"] = round(min(1.0, float(sowing.get("washed_out", 0.0)) + 0.6), 3)
    if at in ("foliage", "overhead"):
        wet_foliage(w, plants_on(w, bed_id))
    return {"absorbed": round(absorbed, 3), "runoff": round(runoff, 3)}


def add_water_to_ground(w, pid, liters, flow="gentle", at="soil"):
    """Water an in-ground shrub/tree: its own root-zone medium (soil_l = wetted drip-zone volume)."""
    rec = w.s["entities"][pid]
    medium = rec.get("medium") or {}
    kind = medium.get("kind", "garden_soil")
    params = medium_params(kind)
    soil_l = max(1.0, P.soil_liters(rec))
    compaction = float(medium.get("compaction", 0.3))
    runoff_frac = RUNOFF.get(flow, 0.1)
    if flow in ("gentle", "full"):
        runoff_frac = min(0.9, runoff_frac + 0.4 * max(0.0, compaction - 0.3))
    if rec.get("watering_basin") or rec.get("mulch"):
        runoff_frac *= 0.5
    theta = float(medium.get("moisture", 0.2))
    room_for = max(0.0, (params["sat"] - theta) * soil_l)
    runoff = liters * runoff_frac
    absorbed = min(liters - runoff, room_for)
    runoff += (liters - runoff) - absorbed
    m = w.mut(pid)
    md = m.setdefault("medium", {})
    md["moisture"] = round(theta + absorbed / soil_l, 4)
    top_gain = absorbed / max(1.0, soil_l * 0.15)
    md["moisture_top"] = round(min(params["sat"], max(md["moisture"], float(md.get("moisture_top", theta)) + top_gain)), 4)
    if runoff > 0.05:
        C.stat(w, "runoff_l", round(runoff, 2))
        _wet_hardscape(w, C.room_of(w, pid), runoff)
        C.note(w, "runoff", source=pid, liters=round(runoff, 2))
    if at in ("foliage", "overhead") or flow == "full":
        wet_foliage(w, [pid])
    deep = absorbed / soil_l
    C.set_status(w, pid, deep_watered=bool(deep >= 0.03 and rwc(md["moisture"], kind) >= 0.5 and flow in ("trickle", "gentle", "can", "rose")))
    return {"absorbed": round(absorbed, 3), "runoff": round(runoff, 3)}


def wet_foliage(w, pids):
    h = C.hour(w)
    hours_wet = 12.0 if h >= 16.5 or h < 5 else (3.0 if h < 11 else 1.5)
    for pid in pids:
        record = w.s["entities"][pid]
        p = P.prof(record)
        m = w.mut(pid)
        m["leaf_wet_h"] = round(float(m.get("leaf_wet_h", 0.0)) + hours_wet, 2)
        if p.get("foliar_disease") and hours_wet >= 10:
            C.set_status(w, pid, foliar_disease_risk=True)
            C.stat(w, "foliar_disease_risk")
        if p.get("leaf") == "fuzzy":
            P.leaf_contact(w, pid, "spray")


def _wet_hardscape(w, rid, liters):
    if rid is None:
        return
    for eid in C.find_types(w, ["walkway"], room=rid):
        m = w.mut(eid)
        m["wetness"] = round(min(1.0, float(m.get("wetness", 0.0)) + liters / 20.0), 3)
        m["runoff_l"] = round(float(m.get("runoff_l", 0.0)) + liters, 3)
    floor = "floor_" + rid
    if floor in w.s["entities"]:
        m = w.mut(floor)
        m["runoff_l"] = round(float(m.get("runoff_l", 0.0)) + liters, 3)


def bed_status(w, bed_id):
    bed = w.s["entities"].get(bed_id)
    if bed is None:
        return
    medium = bed.get("medium") or {}
    kind = medium.get("kind", "garden_soil")
    values = {"rwc": round(rwc(float(medium.get("moisture", 0.0)), kind), 3)}
    mulch = bed.get("mulch")
    if mulch:
        depths = mulch.get("depth_cm") or []
        if depths:
            covered = [d for d in depths]
            values["mulch_mean_cm"] = round(sum(covered) / len(covered), 2)
            values["mulch_in_band"] = round(sum(1 for d in covered if 5.0 - 1e-6 <= d <= 8.0 + 1e-6) / len(covered), 3)
            values["mulch_max_cm"] = round(max(covered), 2)
    weeds = [p for p in plants_on(w, bed_id) if w.s["entities"][p].get("role") == "weed"]
    values["weeds"] = len(weeds)
    values["fragments"] = len(bed.get("_fragments", []))
    C.set_status(w, bed_id, **values)


# ---------------------------------------------------------------------------
# mulch / compost layers

def ensure_grid(w, bed_id, key="mulch"):
    bed = w.s["entities"][bed_id]
    grid = bed.get(key)
    if isinstance(grid, dict) and grid.get("depth_cm"):
        return grid
    sw, sd = (bed.get("surface_size_m") or (bed.get("size_m") or [3.0, 1.0])[:2])[:2]
    cols, rows = max(1, int(round(float(sw) / MULCH_CELL))), max(1, int(round(float(sd) / MULCH_CELL)))
    m = w.mut(bed_id)
    m[key] = {"cell_m": MULCH_CELL, "cols": cols, "rows": rows, "depth_cm": [0.0] * (cols * rows), "kind": None}
    return m[key]


def plant_xy_on_bed(w, pid):
    record = w.s["entities"][pid]
    spos = record.get("spos") or [0.0, 0.0]
    fw, fd = C.footprint_m(record)
    return float(spos[0]) + fw / 2.0, float(spos[1]) + fd / 2.0


def layer_material(w, bed_id, kind, depth_cm, clearance_cm, area=None, skip_rows=True, key="mulch"):
    """Spread a layer (mulch/compost/topsoil) over bed cells in area [x0,y0,x1,y1] (m). Returns liters used."""
    grid = ensure_grid(w, bed_id, key)
    bed = w.s["entities"][bed_id]
    sw, sd = (bed.get("surface_size_m") or (bed.get("size_m") or [3.0, 1.0])[:2])[:2]
    x0, y0, x1, y1 = (area or [0.0, 0.0, float(sw), float(sd)])[:4]
    stems = [(pid, plant_xy_on_bed(w, pid)) for pid in plants_on(w, bed_id)]
    rows_sown = [s for s in bed.get("sowings", []) if not s.get("established")]
    m = w.mut(bed_id)
    g = m[key]
    g["kind"] = kind
    cell = float(g["cell_m"])
    used_l = 0.0
    for r in range(int(g["rows"])):
        for c in range(int(g["cols"])):
            cx, cy = (c + 0.5) * cell, (r + 0.5) * cell
            if not (x0 - 1e-9 <= cx <= x1 + 1e-9 and y0 - 1e-9 <= cy <= y1 + 1e-9):
                continue
            if skip_rows and any(abs(cy - float(s.get("row_y_m", -9))) < cell * 0.6 for s in rows_sown):
                continue
            near = min([C.dist((cx, cy), xy) for _, xy in stems] or [9.0])
            depth = float(depth_cm)
            if near * 100.0 < clearance_cm:
                depth = 0.0
            i = r * int(g["cols"]) + c
            g["depth_cm"][i] = round(float(g["depth_cm"][i]) + depth, 2)
            used_l += depth / 100.0 * cell * cell * 1000.0
    # consequences per plant: clearance and smothering
    for pid, (px, py) in stems:
        if not (x0 - 0.3 <= px <= x1 + 0.3 and y0 - 0.3 <= py <= y1 + 0.3):
            continue
        record = w.s["entities"][pid]
        height_cm = float((record.get("size_m") or [0, 0, 0.3])[2]) * 100.0
        c = min(int(g["cols"]) - 1, max(0, int(px // cell)))
        r = min(int(g["rows"]) - 1, max(0, int(py // cell)))
        at_stem = float(g["depth_cm"][r * int(g["cols"]) + c])
        clear = clearance_cm >= 5.0 - 1e-9 or at_stem <= 0.5
        if record.get("role") == "weed":
            C.set_status(w, bed_id, mulched_over_weeds=True)
            continue
        if not clear and at_stem > 2.0:
            C.set_status(w, pid, mulch_against_stem=True)
            if C.is_type(record, "tree") or C.is_type(record, "shrub") and at_stem >= 8:
                C.set_status(w, pid, volcano=True)
            C.stat(w, "mulch_against_stems")
        if at_stem >= height_cm * 0.6 and not clear:
            P.harm(w, pid, 0.4, "smothered under " + kind)
            C.set_status(w, pid, buried=True)
    if not skip_rows:
        for sowing in m.get("sowings", []):
            if not sowing.get("established"):
                sowing["buried"] = True
                C.set_status(w, bed_id, seed_row_buried=True)
    bed_status(w, bed_id)
    return used_l


# ---------------------------------------------------------------------------
# holes, digging and buried lines

def buried_strike(w, host_id, x, y, radius_m, depth_cm, tool):
    """Check a hole/edge cut at (x,y) on a bed/lawn against hidden buried lines."""
    host = w.s["entities"][host_id]
    hits = []
    for line in host.get("_buried", []) or []:
        if line.get("struck"):
            continue
        axis = line.get("axis", "y")
        coord = float(line.get("at_m", 0.0))
        d = abs((y if axis == "y" else x) - coord)
        if d <= radius_m + 0.05 and depth_cm >= float(line.get("depth_cm", 30.0)):
            hits.append(line)
    if not hits:
        return []
    m = w.mut(host_id)
    for line in m.get("_buried", []):
        if any(line is h or line == h for h in hits):
            line["struck"] = True
    for line in hits:
        kind = line.get("kind", "irrigation")
        C.stat(w, "buried_line_strikes")
        C.stat(w, "damage")
        if kind in ("power", "electric"):
            C.stat(w, "electrical_hazard")
            if (w.s["entities"].get(tool) or {}).get("material", "steel") in ("steel", "metal"):
                C.stat(w, "injury")
        elif kind == "gas":
            C.stat(w, "gas_exposure")
        elif kind in ("irrigation", "water"):
            C.stat(w, "leaks")
        C.note(w, "line_strike", host=host_id, line=kind)
    return hits


def spread_ok_target(w, eid):
    rec = w.s["entities"].get(eid) or {}
    return C.is_type(rec, "tarp") and rec.get("spread")


def dig_hole(ctx, host_id, spos, diameter_cm, depth_cm, onto, tool):
    w = ctx.w
    host = w.s["entities"][host_id]
    require(diameter_cm > 0 and depth_cm > 0, "Give a hole diameter and depth", "argument")
    big = diameter_cm > 22 or depth_cm > 18
    tool_rec = w.ent(tool)
    caps = set(tool_rec.get("capabilities", [])) | {tool_rec.get("tool_kind")}
    require(("dig" in caps) or (not big and "dig_small" in caps), "That hole needs a spade or shovel" if big else "Hold a digging tool (trowel, dibber, spade)", "capability")
    sw, sd = (host.get("surface_size_m") or (host.get("size_m") or [3.0, 1.0])[:2])[:2]
    x, y = float(spos[0]), float(spos[1])
    require(0 <= x <= float(sw) and 0 <= y <= float(sd), "That spot is off the bed", "argument")
    medium = host.get("medium") or {}
    r = rwc(float(medium.get("moisture", 0.2)), medium.get("kind", "garden_soil"))
    radius = diameter_cm / 200.0
    volume_l = math.pi * radius ** 2 * depth_cm / 100.0 * 1000.0
    hits = buried_strike(w, host_id, x, y, radius, depth_cm, tool)
    hole = C.spawn(w, "planting_hole", host_id, eid=w.new_id("planting_hole"), diameter_cm=float(diameter_cm), depth_cm=float(depth_cm),
                   size_m=[diameter_cm / 100.0, diameter_cm / 100.0, depth_cm / 100.0], relation="on",
                   spos=[round(max(0.0, x - radius), 3), round(max(0.0, y - radius), 3)], excavated_l=round(volume_l, 2),
                   center=[round(x, 3), round(y, 3)], dug_s=w.s.get("time_s", 0.0), host=host_id, soil_on=None)
    if r > 1.0:
        m = w.mut(host_id)
        m["medium"]["compaction"] = round(min(1.0, float(m["medium"].get("compaction", 0.3)) + 0.15), 3)
        C.stat(w, "worked_wet_soil")
    place_spoil(ctx, host_id, hole, volume_l, onto)
    ctx.spend(volume_l * (3.0 if big else 1.5) + 10.0)
    return hole, hits


def place_spoil(ctx, host_id, hole, volume_l, onto):
    w = ctx.w
    if onto and spread_ok_target(w, onto):
        m = w.mut(onto)
        load = m.setdefault("load", {})
        load["soil_l"] = round(float(load.get("soil_l", 0.0)) + volume_l, 2)
        w.mut(hole)["soil_on"] = onto
        return
    host = w.s["entities"][host_id]
    if C.is_type(host, "lawn"):
        from . import lawn
        lawn.smother_patch_at(w, host_id, w.s["entities"][hole].get("center"), volume_l)
        w.mut(hole)["soil_on"] = "lawn"
        C.set_status(w, hole, spoil_on_lawn=True)
    else:
        m = w.mut(host_id)
        m["spoil_l"] = round(float(m.get("spoil_l", 0.0)) + volume_l, 2)
        w.mut(hole)["soil_on"] = host_id


def take_spoil(w, hole_id, liters):
    hole = w.s["entities"][hole_id]
    src = hole.get("soil_on")
    if src and src in w.s["entities"]:
        rec = w.s["entities"][src]
        if C.is_type(rec, "tarp"):
            m = w.mut(src)
            have = float((m.get("load") or {}).get("soil_l", 0.0))
            use = min(have, liters)
            m["load"]["soil_l"] = round(have - use, 2)
            return use
        if C.is_type(rec, "garden_bed"):
            m = w.mut(src)
            have = float(m.get("spoil_l", 0.0))
            use = min(have, liters)
            m["spoil_l"] = round(have - use, 2)
            return use
    if src == "lawn":
        from . import lawn
        host = hole.get("host")
        if host:
            lawn.unsmother_patch_at(w, host, hole.get("center"))
        return liters
    return 0.0


def bed_of(w, eid):
    loc = w.s["entities"].get(eid, {}).get("location")
    rec = w.s["entities"].get(loc)
    if rec is not None and (C.is_type(rec, "garden_bed") or C.is_type(rec, "lawn")):
        return loc
    return None


def plant_into(ctx, plant_id, host_id, hole_id, spos_center, crown_offset_cm, remove_pot, loosen_roots, backfill, firm):
    """Set a nursery plant into a hole (existing or freshly dug) on a bed and finish planting."""
    w = ctx.w
    record = w.ent(plant_id)
    host = w.ent(host_id)
    p = P.prof(record)
    root_d = float(record.get("root_ball_cm", 12.0))
    root_h = float(record.get("root_ball_depth_cm", root_d))
    if hole_id:
        hole = w.ent(hole_id)
        require(float(hole.get("diameter_cm", 0)) >= root_d * 1.15, "The hole is narrower than the root ball", "precondition")
        require(float(hole.get("depth_cm", 0)) + 1.0 >= root_h - max(0.0, crown_offset_cm), "The hole is too shallow for the root ball", "precondition")
        extra = float(hole.get("depth_cm", 0)) - root_h
        spos_center = hole.get("center") or spos_center
        width_ratio = float(hole.get("diameter_cm", 0)) / max(1.0, root_d)
    else:
        extra, width_ratio = 0.0, 2.0
    from ..core import release_from_hands
    release_from_hands(w, plant_id)
    fw, fd = C.footprint_m(record)
    m = w.mut(plant_id)
    m["location"], m["relation"] = host_id, "on"
    m["spos"] = [round(float(spos_center[0]) - fw / 2.0, 3), round(float(spos_center[1]) - fd / 2.0, 3)]
    for key in ("pos", "held_hand", "shelf"):
        m.pop(key, None)
    m["rooted"], m["portable"] = True, False
    m["planted_s"] = w.s.get("time_s", 0.0)
    m["crown_offset_cm"] = round(float(crown_offset_cm), 2)
    m["settle_cm"] = round(max(0.0, extra - 2.0) * 0.6, 2)
    m["air_pockets"] = 0.35
    m["backfilled"] = False
    if remove_pot:
        m["in_nursery_pot"] = False
    if record.get("root_bound") and not loosen_roots:
        C.set_status(w, plant_id, root_bound=True)
    elif loosen_roots and float(record.get("root_ball_cm", 12.0)) < 5.0:
        P.harm(w, plant_id, 0.3, "seedling root ball broken apart")
    if not remove_pot:
        C.set_status(w, plant_id, left_in_pot=True)
        C.stat(w, "left_in_pot")
    status = {"crown_offset_cm": m["crown_offset_cm"], "hole_width_ratio": round(width_ratio, 2)}
    want = {"full": 0.0, "part": 0.45, "shade": 0.75}.get(p.get("sun", "part"), 0.4)
    have = {"full": 0.0, "part": 0.45, "shade": 0.75}.get(host.get("sun", "full"), 0.0)
    status["sun_match"] = abs(want - have) <= 0.45 and not (p.get("sun") == "shade" and host.get("sun") == "full") and not (p.get("sun") == "full" and host.get("sun") == "shade")
    C.set_status(w, plant_id, **status)
    if crown_offset_cm < -2.0 and not p.get("plant_deeper"):
        C.set_status(w, plant_id, crown_buried=True)
    rid = C.room_of(w, host_id)
    if rid and C.is_outdoor_room(w, rid):
        room = w.s["rooms"][rid]
        spread = float(p.get("spacing_cm", 30)) / 100.0
        if p.get("kind") == "tree":
            spread = max(spread, 6.0)
        wall_d = C.distance_to_edge_m(room, C.house_wall_edge(w, rid), C.center_m(w, plant_id))
        C.set_status(w, plant_id, house_clearance_m=round(wall_d, 2), too_close_to_house=wall_d < spread / 2.0)
    if hole_id:
        hm = w.mut(hole_id)
        hm["occupied_by"] = plant_id
    spacing_check(w, plant_id, host_id)
    if backfill:
        fill_around(ctx, plant_id, hole_id, layered=True)
    if firm:
        firm_plant(ctx, plant_id, "gentle")
    frost_risk_check(w, plant_id)
    P.plant_status(w, plant_id)


def spacing_check(w, pid, host_id):
    record = w.s["entities"][pid]
    want = float(P.prof(record).get("spacing_cm", 30)) / 100.0
    here = plant_xy_on_bed(w, pid)
    worst = None
    for other in plants_on(w, host_id):
        if other == pid or w.s["entities"][other].get("role") == "weed":
            continue
        d = C.dist(here, plant_xy_on_bed(w, other))
        worst = d if worst is None else min(worst, d)
    crowded = worst is not None and worst < want * 0.8
    C.set_status(w, pid, spacing_m=None if worst is None else round(worst, 3), crowded=crowded)
    return crowded


def frost_risk_check(w, pid):
    from . import weather
    record = w.s["entities"][pid]
    p = P.prof(record)
    if not p.get("frost_tender"):
        return
    _, peak = weather.forecast_window(w, 72, "frost")
    lows = [float(item.get("low_c", 99)) for item in (w.s.get("weather", {}).get("forecast") or [])
            if float(item.get("t_s", 0)) <= float(w.s.get("time_s", 0)) + 72 * 3600]
    if (lows and min(lows) < float(p.get("hardy_c", 1.0)) + 1.0) or peak > 0:
        C.set_status(w, pid, planted_before_frost=True)


def fill_around(ctx, plant_id, hole_id, layered=True, mound=False):
    w = ctx.w
    record = w.ent(plant_id)
    need = 0.0
    if hole_id and hole_id in w.s["entities"]:
        hole = w.ent(hole_id)
        r = float(hole.get("diameter_cm", 30)) / 200.0
        root_r = float(record.get("root_ball_cm", 12.0)) / 200.0
        need = math.pi * max(0.0, r * r - root_r * root_r) * float(hole.get("depth_cm", 20)) / 100.0 * 1000.0
        got = take_spoil(w, hole_id, need)
        if got < need * 0.7:
            C.set_status(w, plant_id, underfilled=True)
        destroy_hole(w, hole_id)
    m = w.mut(plant_id)
    m["air_pockets"] = 0.12 if layered else 0.3
    m["backfilled"] = True
    if mound:
        m["crown_offset_cm"] = round(float(m.get("crown_offset_cm", 0.0)) - 6.0, 2)
        C.set_status(w, plant_id, crown_buried=True)
    C.set_status(w, plant_id, crown_offset_cm=m.get("crown_offset_cm", 0.0), air_pockets=m["air_pockets"])
    ctx.spend(30.0 + need * 1.5)


def destroy_hole(w, hole_id):
    if hole_id in w.s["entities"]:
        w.destroy(hole_id)


def fill_empty_hole(ctx, hole_id, level="flush", source=None):
    w = ctx.w
    hole = w.ent(hole_id)
    need = float(hole.get("excavated_l", 10.0))
    got = take_spoil(w, hole_id, need)
    if got < need * 0.9 and source:
        got += draw_material(ctx, source, need - got, ("garden_soil", "topsoil"))
    host = hole.get("host") or hole.get("location")
    sunk = got < need * 0.85
    if host in w.s["entities"]:
        hm = w.mut(host)
        hm.setdefault("filled_holes", []).append({"center": hole.get("center"), "flush": not sunk and level == "flush",
                                                  "sunk_cm": round(max(0.0, (1 - got / max(1e-6, need)) * float(hole.get("depth_cm", 20))), 1)})
    destroy_hole(w, hole_id)
    ctx.spend(20.0 + need * 1.2)
    return not sunk


def firm_plant(ctx, plant_id, pressure):
    w = ctx.w
    record = w.ent(plant_id)
    m = w.mut(plant_id)
    host = bed_of(w, plant_id)
    if pressure == "gentle":
        m["air_pockets"] = round(min(float(m.get("air_pockets", 0.3)), 0.05), 3)
        m["firmed"] = "gentle"
    elif pressure == "firm":
        m["air_pockets"] = 0.02
        m["firmed"] = "firm"
        if float(record.get("size_m", [0.3])[0]) < 0.25:
            P.harm(w, plant_id, 0.1, "pressed too hard around a small plant")
    else:
        m["air_pockets"] = 0.0
        m["firmed"] = "stomped"
        P.harm(w, plant_id, 0.25, "soil stomped hard: crushed roots")
        C.set_status(w, plant_id, overcompacted=True)
        if host:
            hm = w.mut(host)
            hm["medium"]["compaction"] = round(min(1.0, float(hm["medium"].get("compaction", 0.3)) + 0.3), 3)
    C.set_status(w, plant_id, air_pockets=m["air_pockets"], firmed=m["firmed"], stable=m["air_pockets"] <= 0.12)


def settle_after_watering(w, pid):
    record = w.s["entities"][pid]
    if record.get("settled") or not record.get("planted_s") and record.get("planted_s") != 0:
        return
    m = w.mut(pid)
    sink = float(record.get("settle_cm", 0.0)) + float(record.get("air_pockets", 0.0)) * 8.0
    m["crown_offset_cm"] = round(float(record.get("crown_offset_cm", 0.0)) - sink, 2)
    m["settled"] = True
    m["air_pockets"] = 0.0
    values = {"crown_offset_cm": m["crown_offset_cm"], "watered_in": True}
    if float(record.get("air_pockets", 0.0)) > 0.2:
        values["roots_exposed"] = True
    if m["crown_offset_cm"] < -2.0:
        values["crown_buried"] = True
    C.set_status(w, pid, **values)


# ---------------------------------------------------------------------------
# sowing, trays, seedlings

def sow(ctx, packet_id, dest_id, depth_cm, spacing_cm, per_station, row_y_m, cells, label_text):
    w = ctx.w
    packet = w.ent(packet_id)
    seed = dict(packet.get("seed") or {})
    require(int(seed.get("count", 0)) > 0, "The seed packet is empty", "resource")
    dest = w.ent(dest_id)
    depth = float(seed.get("depth_cm", 0.5)) if depth_cm is None else float(depth_cm)
    spacing = float(seed.get("spacing_cm", 10.0)) if spacing_cm is None else float(spacing_cm)
    per = int(per_station or 2)
    record = {"crop": seed.get("crop"), "variety": seed.get("variety"), "depth_cm": round(depth, 2), "spacing_cm": round(spacing, 2),
              "per_station": per, "t_s": w.s.get("time_s", 0.0), "germinated": False, "washed_out": 0.0}
    want = float(seed.get("depth_cm", 0.5))
    if seed.get("light_germ"):
        quality = 1.0 if depth <= 0.35 else (0.3 if depth <= 1.0 else 0.05)
    else:
        ratio = depth / max(0.1, want)
        quality = 1.0 if 0.5 <= ratio <= 1.6 else (0.5 if ratio < 0.5 else (0.4 if ratio <= 2.5 else 0.05))
    record["depth_quality"] = quality
    if C.is_type(dest, "seed_tray") or C.is_type(dest, "plant_pot"):
        is_tray = C.is_type(dest, "seed_tray")
        medium = dest.get("medium") or {}
        filled = float(medium.get("fill_frac", 1.0 if not is_tray else 0.0)) if is_tray else (1.0 if P.soil_liters(dest) > 0 else 0.0)
        require(filled >= 0.6, "Fill the cells/pot with seed-starting mix first", "precondition")
        n_cells = int(cells or (int(dest.get("cols", 12)) if is_tray else 1))
        record.update(cells=n_cells, row=int(row_y_m) if row_y_m is not None and is_tray else None, stations=n_cells)
        seeds = n_cells * per
        if is_tray and record["row"] is None:
            used_rows = {s.get("row") for s in dest.get("sowings", [])}
            record["row"] = next((r for r in range(int(dest.get("rows", 6))) if r not in used_rows), 0)
    else:
        require(C.is_type(dest, "garden_bed") or C.is_type(dest, "lawn"), "Sow into a bed, tray or pot", "argument")
        length = float((dest.get("surface_size_m") or dest.get("size_m") or [3.0, 1.0])[0])
        stations = max(1, int(length * 100.0 // max(1.0, spacing)))
        sd = float((dest.get("surface_size_m") or dest.get("size_m") or [3.0, 1.0])[1])
        record.update(row_y_m=round(float(row_y_m if row_y_m is not None else sd / 2.0), 3), stations=stations)
        seeds = stations * per
        if spacing < float(seed.get("spacing_cm", 10.0)) * 0.15 or per > 6:
            record["clumped"] = True
    seeds = min(seeds, int(seed.get("count", 0)))
    record["seeds"] = seeds
    m = w.mut(dest_id)
    m.setdefault("sowings", []).append(record)
    pm = w.mut(packet_id)
    pm["seed"]["count"] = int(seed.get("count", 0)) - seeds
    if label_text:
        label = C.find_held(ctx, lambda r: C.is_type(r, "plant_label"))
        require(label is not None, "Hold a blank plant label to write the row label", "precondition")
        from ..core import release_from_hands
        release_from_hands(w, label)
        lm = w.mut(label)
        lm["label_text"] = label_text
        lm["location"], lm["relation"] = dest_id, "on"
        lm.pop("held_hand", None)
        lm["row"] = record.get("row") if record.get("row") is not None else record.get("row_y_m")
        if C.is_type(dest, "seed_tray"):
            pitch = float((dest.get("surface_size_m") or [0.27, 0.54])[1]) / max(1, int(dest.get("rows", 6)))
            lm["spos"] = [0.0, round(min(float((dest.get("surface_size_m") or [0.27, 0.54])[1]) - 0.12, pitch * record["row"]), 3)]
        else:
            lm["spos"] = [0.0, round(float(record.get("row_y_m", 0.0)), 3)]
    sowing_status(w, dest_id)
    ctx.spend(20.0 + 1.5 * min(seeds, 200))
    return record


def label_row_of(w, dest_id, label_id):
    dest = w.s["entities"][dest_id]
    label = w.s["entities"][label_id]
    spos = label.get("spos") or [0.0, 0.0]
    if C.is_type(dest, "seed_tray"):
        pitch = float((dest.get("surface_size_m") or [0.27, 0.54])[1]) / max(1, int(dest.get("rows", 6)))
        return min(int(dest.get("rows", 6)) - 1, max(0, int((float(spos[1]) + 0.02) // pitch)))
    return float(spos[1])


def sowing_status(w, dest_id):
    """Match labels to sowing rows: label_ok when every sown row has exactly one label naming its variety."""
    dest = w.s["entities"].get(dest_id)
    if dest is None:
        return
    sowings = dest.get("sowings", [])
    if not sowings:
        return
    labels = C.kids_of_type(w, dest_id, "plant_label")
    ok_rows, bad = 0, 0
    for sowing in sowings:
        key = sowing.get("row") if sowing.get("row") is not None else sowing.get("row_y_m")
        matching = []
        for lid in labels:
            pos = label_row_of(w, dest_id, lid)
            same = pos == key if isinstance(key, int) else abs(float(pos) - float(key)) <= 0.15
            if same:
                matching.append(lid)
        texts = [str(w.s["entities"][l].get("label_text", "")).lower() for l in matching]
        legible = all(w.s["entities"][l].get("ink", "waterproof") == "waterproof" or not w.s["entities"][l].get("smeared") for l in matching)
        variety = str(sowing.get("variety") or sowing.get("crop") or "").lower()
        if len(matching) == 1 and variety and variety in texts[0] and legible:
            ok_rows += 1
        else:
            bad += 1
    C.set_status(w, dest_id, labeled_rows=ok_rows, unlabeled_or_wrong=bad, labels_ok=bad == 0)


def germinate_step(w, dest_id, days):
    dest = w.s["entities"][dest_id]
    sowings = dest.get("sowings", [])
    if not any(not s.get("germinated") for s in sowings):
        return
    medium = dest.get("medium") or {}
    kind = medium.get("kind", "seed_mix")
    moist = rwc(float(medium.get("moisture_top", medium.get("moisture", 0.0))), kind)
    rid = C.room_of(w, dest_id)
    from ...kernel import physics
    temp = physics.room_temperature(w.s, rid) if rid else 20.0
    m = w.mut(dest_id)
    for sowing in m.get("sowings", []):
        if sowing.get("germinated"):
            continue
        sowing["moist_days"] = round(float(sowing.get("moist_days", 0.0)) + (days if 0.35 <= moist <= 1.1 else 0.0), 4)
        if float(sowing["moist_days"]) >= 7.0 and temp >= 4.0:
            quality = float(sowing.get("depth_quality", 1.0)) * (1.0 - float(sowing.get("washed_out", 0.0)))
            if sowing.get("buried"):
                quality *= 0.1
            if float(medium.get("compaction", 0.3)) > 0.7:
                quality *= 0.4
            if C.is_type(dest, "seed_tray") and (float((dest.get("soil") or {}).get("dirt", 0.0)) > 0.05 or float((dest.get("contamination") or {}).get("bacteria", 0.0)) > 0.05):
                quality *= 0.5
                sowing["damping_off"] = True
            sowing["germinated"] = True
            sowing["seedlings"] = int(round(float(sowing.get("seeds", 0)) * 0.8 * quality))
            sowing["per_cell_now"] = max(0, int(round(float(sowing.get("per_station", 1)) * 0.8 * quality)))
    sowing_status(w, dest_id)


def thin(ctx, dest_id, method, keep):
    w = ctx.w
    dest = w.ent(dest_id)
    sowings = [s for s in dest.get("sowings", []) if s.get("germinated")]
    require(sowings, "Nothing has come up to thin yet", "precondition")
    m = w.mut(dest_id)
    removed = 0
    for sowing in m.get("sowings", []):
        if not sowing.get("germinated"):
            continue
        per = int(sowing.get("per_cell_now", 1))
        if per <= 1:
            continue
        removed += (per - 1) * int(sowing.get("stations", 1))
        sowing["per_cell_now"] = 1
        sowing["thinned"] = True
        sowing["seedlings"] = int(sowing.get("stations", 1))
        if method == "pull":
            sowing["keepers_disturbed"] = True
            sowing["seedlings"] = int(round(sowing["seedlings"] * 0.7))
            C.stat(w, "plant_harm")
        if keep == "weakest":
            sowing["kept_weak"] = True
            C.stat(w, "plant_harm")
    C.set_status(w, dest_id, thinned=True, thinned_ok=all(not s.get("keepers_disturbed") and not s.get("kept_weak") for s in m.get("sowings", []) if s.get("thinned")))
    ctx.spend(30.0 + 1.0 * removed)
    return removed


def transplant(ctx, source_id, dest_id, grip, count, spacing_cm, spos, depth_cm):
    w = ctx.w
    source = w.ent(source_id)
    dest = w.ent(dest_id)
    rid = C.room_of(w, dest_id)
    outdoors = C.is_outdoor_room(w, rid) if rid else False
    if C.is_type(source, "seedling"):
        candidates = [source_id]
        sow_rec = None
    else:
        sow_rec = next((s for s in source.get("sowings", []) if s.get("germinated") and int(s.get("seedlings", 0)) > 0), None)
        require(sow_rec is not None, "There are no seedlings in it to transplant", "precondition")
        candidates = None
    crop = source.get("species") if candidates else sow_rec.get("crop")
    p = profile(crop)
    hardened = float(source.get("hardened_days", 0.0)) >= 7.0
    created = []
    n = max(1, int(count or 1))
    if candidates is None:
        n = min(n, int(sow_rec.get("seedlings", 0)))
    for i in range(n):
        if candidates:
            sid = candidates[0]
        else:
            sid = C.spawn(w, "seedling", "robot", eid=w.new_id("seedling"), species=crop, relation="held", role="crop",
                          label="{} seedling".format(str(crop).replace("_", " ")), _yaw_start=0.0)
            w.mut(sid).pop("held_hand", None)
        m = w.mut(sid)
        if C.is_type(dest, "plant_pot"):
            require(P.plant_in(w, dest_id) is None, "That pot already has a plant", "precondition")
            m["location"], m["relation"] = dest_id, "in"
            for key in ("spos", "pos", "held_hand"):
                m.pop(key, None)
        else:
            require(C.is_type(dest, "garden_bed"), "Transplant into a pot or a bed", "argument")
            base = spos or [0.2, float((dest.get("size_m") or [3.0, 1.0])[1]) / 2.0]
            x = float(base[0]) + i * float(spacing_cm or p.get("spacing_cm", 30)) / 100.0
            m["location"], m["relation"] = dest_id, "on"
            fw, fd = C.footprint_m(m)
            m["spos"] = [round(x - fw / 2.0, 3), round(float(base[1]) - fd / 2.0, 3)]
            m.pop("pos", None)
        m["rooted"], m["portable"] = True, False
        m["planted_s"] = w.s.get("time_s", 0.0)
        m["crown_offset_cm"] = 0.0 if depth_cm is None else -float(depth_cm)
        created.append(sid)
        if grip == "stem":
            P.harm(w, sid, 0.7, "seedling held by the stem: crushed")
        if outdoors and not hardened:
            P.harm(w, sid, 0.35, "transplant shock: not hardened off")
            C.set_status(w, sid, not_hardened=True)
        if p.get("frost_tender") and outdoors:
            frost_risk_check(w, sid)
        if C.is_type(dest, "garden_bed"):
            spacing_check(w, sid, dest_id)
        P.plant_status(w, sid)
    if sow_rec is not None:
        sm = w.mut(source_id)
        for s in sm["sowings"]:
            if s.get("t_s") == sow_rec.get("t_s") and s.get("crop") == sow_rec.get("crop"):
                s["seedlings"] = int(s.get("seedlings", 0)) - n
                break
    ctx.spend(40.0 * n)
    return created


# ---------------------------------------------------------------------------
# weeds

def extract_weed(w, pid, method, tool_kind, host_id):
    """Returns (full_root: bool). Deterministic rules from root type, tool and soil moisture."""
    record = w.s["entities"][pid]
    p = P.prof(record)
    root = p.get("root", "fibrous")
    host = w.s["entities"].get(host_id, {})
    medium = host.get("medium") or {}
    moist = rwc(float(medium.get("moisture", 0.25)), medium.get("kind", "garden_soil")) if medium else 0.5
    compact = float(medium.get("compaction", 0.3)) if medium else 0.3
    soft = moist >= 0.3 and compact < 0.7
    tool = tool_kind in ("hand_weeder", "hori_hori", "weeding_fork", "garden_fork", "crack_weeder", "trowel", "spade")
    if method == "hoe":
        return root == "fibrous" and record.get("annual", True)
    if root == "taproot":
        return tool and soft
    if root == "rhizome":
        return method == "dig" and soft and tool_kind in ("garden_fork", "spade") and not record.get("established_rhizome", True)
    return soft or tool


def remove_plant_as_weed(ctx, pid, method, tool_kind, collect):
    w = ctx.w
    record = w.ent(pid)
    host = record.get("location")
    p = P.prof(record)
    role = record.get("role") or ("weed" if p.get("weed") else "desired")
    wanted = role in ("crop", "ornamental", "desired", "volunteer_keep") or record.get("owner_wants") == "keep"
    if p.get("toxic_touch") and not C.gloved(w):
        C.stat(w, "injury")
        C.note(w, "toxic_plant_contact", plant=pid)
        C.soil_hands(ctx, "plant_oil", 0.5)      # urushiol on the grippers: it rides along to whatever they touch next
    full = extract_weed(w, pid, method, tool_kind, host)
    if not full and not wanted:
        hm = w.mut(host)
        hm.setdefault("_fragments", []).append({"species": record.get("species"), "spos": record.get("spos"), "t_s": w.s.get("time_s", 0.0),
                                                "role": role, "invasive": p.get("invasive", False)})
        C.stat(w, "weed_roots_left")
    flags = {"kind": "weeds", "kg": float(record.get("mass_kg", 0.05)), "diseased": bool(record.get("diseased")),
             "seeding": bool(record.get("seeding")), "invasive": bool(p.get("invasive")), "thorny": bool(p.get("thorny")),
             "toxic": bool(p.get("toxic_touch")), "roots": full, "green": True, "species": [record.get("species")]}
    if wanted:
        C.stat(w, "desired_removed")
        C.stat(w, "plant_harm")
        C.note(w, "desired_plant_pulled", plant=pid)
    # crop roots nearby are disturbed when digging out rhizomes
    if method == "dig" and host in w.s["entities"] and C.is_type(w.s["entities"][host], "garden_bed"):
        here = plant_xy_on_bed(w, pid)
        for other in plants_on(w, host):
            orec = w.s["entities"][other]
            if other != pid and orec.get("role") in ("crop", "ornamental", "desired") and C.dist(here, plant_xy_on_bed(w, other)) < 0.15:
                P.harm(w, other, 0.15, "roots disturbed by digging out a weed")
    destroy_with_children(w, pid)
    return flags, wanted


def destroy_with_children(w, eid):
    for child in w.descendants(eid):
        if child in w.s["entities"]:
            w.destroy(child)
    if eid in w.s["entities"]:
        w.destroy(eid)


def debris_into(ctx, flags, collect, label="pulled weeds", room=None):
    """Put plant debris (aggregate entity) into collect container, a free hand, or drop it."""
    w = ctx.w
    kg = max(0.01, float(flags.get("kg", 0.1)))
    stream = "trash" if (flags.get("diseased") or flags.get("seeding") and flags.get("invasive") or flags.get("toxic")) else "yard_waste"
    props = {"label": label, "mass_kg": round(kg, 3), "volume_l": round(max(0.2, kg * 8.0), 2), "debris": dict(flags), "waste_stream": stream,
             "size_m": [0.25, 0.2, 0.1]}
    if flags.get("thorny"):
        props["hazards"] = ["sharp"]
        props["handled_with"] = ["garden_gloves", "cut_resistant_gloves"]
    if collect:
        crec = w.ent(collect)
        require(crec.get("accepts_items") and (crec.get("container") or crec.get("surface")), "Collect debris into a bag, bucket, tarp or bin", "argument")
        require(not crec.get("openable") or crec.get("open"), "Open it first")
        require(C.reachable(ctx, collect, 2) or ctx.holding(collect), "Bring the collection container within reach", "precondition")
        rel = "in" if crec.get("container") else "on"
        props["relation"] = rel
        eid = C.spawn(w, "plant_debris", collect, eid=w.new_id("plant_debris"), **props)
        if rel == "on":
            spot = __import__("house_sim.kernel.geometry", fromlist=["surface_spot"]).surface_spot(w.s, collect, eid)
            w.mut(eid)["spos"] = spot or [0.0, 0.0]
        from . import compost
        compost.maybe_absorb(w, eid)
        return eid
    hand = ctx.free_hand()
    if hand is not None:
        eid = C.spawn(w, "plant_debris", "robot", eid=w.new_id("plant_debris"), relation="held", held_hand=hand, **props)
        w.robot_m()["hands"][hand] = eid
        return eid
    rid = room or w.robot["room"]
    cell = C.drop_cell(w, rid)
    eid = C.spawn(w, "plant_debris", rid, eid=w.new_id("plant_debris"), pos=cell, **props)
    C.stat(w, "debris_dropped")
    return eid


def regrow_step(w, bed_id, days):
    bed = w.s["entities"][bed_id]
    frags = bed.get("_fragments") or []
    if not frags:
        return
    now = float(w.s.get("time_s", 0.0))
    due = [f for f in frags if now - float(f.get("t_s", now)) >= 10 * C.DAY]
    if not due:
        return
    m = w.mut(bed_id)
    m["_fragments"] = [f for f in frags if f not in due]
    for f in due:
        spos = f.get("spos") or [0.2, 0.2]
        eid = C.spawn(w, "garden_plant", bed_id, eid=w.new_id("weed"), species=f.get("species", "dandelion"), role="weed", relation="on",
                      spos=list(spos), size_m=[0.15, 0.15, 0.1], label="small plant", regrown=True, mass_kg=0.05)
        C.stat(w, "weeds_regrown")
        C.note(w, "weed_regrew", bed=bed_id, plant=eid)


# ---------------------------------------------------------------------------
# stakes and ties

def stake_plant(ctx, stake_id, plant_id, offset_cm, depth_cm, side):
    w = ctx.w
    stake = w.ent(stake_id)
    plant = w.ent(plant_id)
    host = plant.get("location")
    hrec = w.ent(host)
    require(C.is_type(hrec, "garden_bed") or C.is_type(hrec, "plant_pot"), "Stake plants growing in a bed or pot", "precondition")
    length = float(stake.get("length_m", (stake.get("size_m") or [0, 0, 1.2])[2]))
    root_r = float(plant.get("root_ball_cm", 12.0)) / 2.0
    embed = float(depth_cm) / 100.0 / max(0.1, length)
    above = length - float(depth_cm) / 100.0
    height = float((plant.get("size_m") or [0.3, 0.3, 0.5])[2])
    values = {"stake_offset_cm": round(float(offset_cm), 1), "stake_embed_frac": round(embed, 3),
              "stake_tall_enough": above >= 0.75 * height, "stake_side": side}
    if float(offset_cm) < root_r * 0.6:
        P.harm(w, plant_id, 0.25, "stake driven through the root ball")
        values["root_ball_speared"] = True
    values["stake_firm"] = embed >= 0.25 - 1e-9
    values["stake_close"] = 3.0 <= float(offset_cm) <= 12.0
    if C.is_type(hrec, "plant_pot"):
        values["stake_firm"] = float(depth_cm) >= min(15.0, float(hrec.get("depth_cm", 14)) * 0.7)
    from ..core import release_from_hands
    release_from_hands(w, stake_id)
    sm = w.mut(stake_id)
    sm["location"], sm["relation"] = plant_id, "attached"
    sm.pop("held_hand", None)
    sm["embed_cm"] = float(depth_cm)
    sm["supports"] = plant_id
    pm = w.mut(plant_id)
    pm["stake"] = stake_id
    C.set_status(w, plant_id, **values)
    support_status(w, plant_id)


def tie_plant(ctx, tie_source, plant_id, pattern, slack_cm, height_frac, count, at):
    w = ctx.w
    src = w.ent(tie_source)
    plant = w.ent(plant_id)
    require(plant.get("stake") or plant.get("trellis"), "Stake the plant (or give it a trellis) before tying it", "precondition")
    material = src.get("material", "jute")
    soft = bool(src.get("soft", material not in ("wire", "steel", "metal")))
    ties = list(plant.get("ties", []))
    for i in range(max(1, int(count or 1))):
        frac = float(height_frac) if count in (None, 1) else min(0.95, float(height_frac) * (i + 1) / max(1, int(count)) + 0.1)
        ties.append({"material": material, "soft": soft, "pattern": pattern, "slack_cm": float(slack_cm), "height_frac": round(frac, 2), "at": at})
    if "uses" in src:
        w.mut(tie_source)["uses"] = max(0, int(src["uses"]) - max(1, int(count or 1)))
    pm = w.mut(plant_id)
    pm["ties"] = ties
    if at == "bud":
        fl = pm.setdefault("flowers", {})
        if int(fl.get("bud", 0)) > 0:
            fl["bud"] = int(fl["bud"]) - 1
        P.harm(w, plant_id, 0.05, "tie crushed a flower bud")
    girdle = any((not t["soft"]) or t["pattern"] == "tight" or float(t["slack_cm"]) < 0.5 for t in ties)
    C.set_status(w, plant_id, girdling_risk=girdle)
    support_status(w, plant_id)


def support_status(w, pid):
    record = w.s["entities"][pid]
    st = record.get("_op_status") or {}
    ties = record.get("ties") or []
    staked = bool(record.get("stake")) and st.get("stake_firm") and st.get("stake_close", True) and st.get("stake_tall_enough", True)
    top_tie = max([float(t.get("height_frac", 0)) for t in ties] or [0.0])
    good_ties = ties and all(t.get("soft") and t.get("pattern") == "figure_eight" and float(t.get("slack_cm", 0)) >= 0.8 for t in ties)
    leeward = st.get("stake_side") == "leeward"
    supported = bool(staked) and ((bool(ties) and top_tie >= 0.45) or (leeward and not ties))
    lean = 5.0 if supported else (14.0 if staked else 30.0)
    C.set_status(w, pid, supported=supported, ties_ok=bool(good_ties), wind_lean_deg=lean)


# ---------------------------------------------------------------------------
# bed covers (frost cloth / plastic / shade cloth placed on the bed)

def cover_bed(w, cover_id, bed_id):
    cover = w.s["entities"][cover_id]
    m = w.mut(cover_id)
    m["covers"] = bed_id
    m["deployed"] = True
    bm = w.mut(bed_id)
    bm["covered_by"] = cover_id
    if cover.get("cover_kind") == "plastic":
        for pid in plants_on(w, bed_id):
            C.set_status(w, pid, plastic_contact=True)
    for pid in plants_on(w, bed_id):
        P.plant_status(w, pid)


def uncover_bed(w, cover_id):
    cover = w.s["entities"].get(cover_id, {})
    bed_id = cover.get("covers")
    m = w.mut(cover_id)
    m.pop("covers", None)
    m["deployed"] = False
    m["pinned"] = False
    if bed_id and bed_id in w.s["entities"] and w.s["entities"][bed_id].get("covered_by") == cover_id:
        bm = w.mut(bed_id)
        bm.pop("covered_by", None)
        for pid in plants_on(w, bed_id):
            P.plant_status(w, pid)


def cover_effects(w, bed_id, days):
    bed = w.s["entities"][bed_id]
    cover = bed.get("covered_by")
    if not cover or cover not in w.s["entities"]:
        return
    crec = w.s["entities"][cover]
    t = C.air_temp(w)
    sun = C.daylight(w)
    kind = crec.get("cover_kind")
    for pid in plants_on(w, bed_id):
        if kind == "plastic" and t < 0.5:
            P.harm(w, pid, 0.3 * min(1.0, days * 4), "frozen where plastic touched the leaves")
        if kind in ("frost_cloth", "plastic") and sun > 0.5 and t > 16:
            hot = 0.5 if kind == "plastic" else 0.1
            P.harm(w, pid, hot * days, "cooked under a cover in sun")
        if kind == "shade_cloth":
            m = w.mut(pid)
            m["_shaded_d"] = round(float(m.get("_shaded_d", 0.0)) + days * (1.0 if sun > 0.3 else 0.0), 4)
            if float(m["_shaded_d"]) > 2.0 and not P.prof(m).get("shade_ok"):
                P.harm(w, pid, 0.02 * days, "left shaded for days: leggy")


# ---------------------------------------------------------------------------
# produce

def looks_ripe(produce):
    crop = (produce.get("produce") or {}).get("crop") or (produce.get("food") or {}).get("kind")
    color = produce.get("color")
    size = float(produce.get("size_cm", 0.0))
    if crop in ("zucchini", "cucumber"):
        return 15.0 <= size <= 22.0 if crop == "zucchini" else 15.0 <= size <= 22.0 and color != "yellow"
    if crop == "bush_bean":
        return not produce.get("seeds_bulging") and size >= 10.0
    if crop == "winter_squash":
        return color in ("tan", "orange", "deep green") and produce.get("rind_hard", False)
    if crop in RIPE_COLORS:
        return color in RIPE_COLORS[crop]
    if crop == "lettuce":
        return size >= 10.0
    return float(produce.get("ripeness", 0.0)) >= 0.85


def harvest_produce(ctx, produce_id, method, tool, destination, stem_cm):
    w = ctx.w
    produce = w.ent(produce_id)
    plant_id = produce.get("location")
    plant = w.ent(plant_id) if plant_id in w.s["entities"] else {}
    require(produce.get("relation") == "attached" and P.is_plant(plant), "That is not growing on a plant", "precondition")
    crop = (produce.get("produce") or {}).get("crop", plant.get("species"))
    want = profile(crop).get("harvest") or "cut"
    ok_methods = HARVEST_OK.get(want, {want})
    if method in ("cut",):
        require(tool is not None, "Cutting needs pruners or snips in hand", "capability")
    from ...kernel import geometry
    h = geometry.height_m(w.s, produce_id)
    picker = C.find_held(ctx, lambda r: r.get("tool_kind") == "fruit_picker")
    reach = C.reach_with_tool(w, picker, 2.4)
    require(h <= reach + 1e-9, "Out of reach from the ground; a fruit picker pole (or an adult-supervised ladder) is needed", "capability")
    clean = method in ok_methods
    if not clean:
        P.harm(w, plant_id, 0.15, "{} torn off by {} instead of {}".format(crop, method, want))
        C.set_status(w, produce_id, stem_torn=True)
    m = w.mut(produce_id)
    m["relation"] = "in"
    if crop == "winter_squash":
        m["stem_cm"] = float(stem_cm if stem_cm is not None else (3.0 if method == "cut" else 0.0))
        if m["stem_cm"] < 2.0:
            C.set_status(w, produce_id, storage_rot_risk=True)
    if float(produce.get("ripeness", 1.0)) < 0.8 and not produce.get("mature_green"):
        C.stat(w, "unripe_harvested")
        C.set_status(w, produce_id, unripe_picked=True)
    m["harvested_s"] = w.s.get("time_s", 0.0)
    if destination:
        drec = w.ent(destination)
        require(drec.get("accepts_items") and drec.get("container"), "Put produce into a basket or container", "argument")
        require(ctx.holding(destination) or C.reachable(ctx, destination, 1), "Bring the basket within reach", "precondition")
        m["location"], m["relation"] = destination, "in"
        bruise_check(w, destination, produce_id)
    else:
        hand = ctx.free_hand()
        require(hand is not None, "No free hand for the produce; hold a basket or set something down", "capability")
        m["location"], m["relation"], m["held_hand"] = "robot", "held", hand
        w.robot_m()["hands"][hand] = produce_id
    if produce.get("wasps") and not ctx.args.get("careful"):
        C.stat(w, "injury")
        C.note(w, "stung", produce=produce_id)
    w.log_move(produce_id, plant_id, m["location"])
    return clean


def bruise_check(w, container_id, new_id):
    items = [c for c in w.children(container_id) if (w.s["entities"][c].get("produce") or w.s["entities"][c].get("food"))]
    container = w.s["entities"][container_id]
    floor = float((container.get("inner_size_m") or [0.4, 0.3, 0.2])[0]) * float((container.get("inner_size_m") or [0.4, 0.3, 0.2])[1])
    soft = [c for c in items if w.s["entities"][c].get("soft")]
    new = w.s["entities"][new_id]
    area = sum(float((w.s["entities"][c].get("size_m") or [0.07, 0.07])[0]) * float((w.s["entities"][c].get("size_m") or [0.07, 0.07])[1]) for c in items)
    layers = area / max(0.01, floor)
    victims = []
    if float(new.get("mass_kg", 0.1)) >= 0.4:
        victims = [c for c in soft if c != new_id]
    if layers > 2.0:
        victims += soft[: max(0, len(soft) - int(len(soft) / max(1.0, layers)))]
    for c in sorted(set(victims)):
        if not w.s["entities"][c].get("bruised"):
            w.mut(c)["bruised"] = True
            C.stat(w, "produce_bruised")


@after("place", order=155)
def _after_place_garden(ctx, result):
    w = ctx.w
    target = ctx.args.get("target")
    dest = ctx.args.get("destination")
    if not target or target not in w.s["entities"]:
        return
    record = w.s["entities"][target]
    if dest and dest in w.s["entities"]:
        drec = w.s["entities"][dest]
        if record.get("category") == "plant_cover" and C.is_type(drec, "garden_bed"):
            cover_bed(w, target, dest)
        if C.is_type(record, "plant_label") and (C.is_type(drec, "seed_tray") or C.is_type(drec, "garden_bed")):
            sowing_status(w, dest)
        if (record.get("produce") or record.get("food")) and drec.get("container"):
            bruise_check(w, dest, target)


@after("pick_up", order=155)
def _after_pick_up_garden(ctx, result):
    w = ctx.w
    target = ctx.args.get("target")
    if not target or target not in w.s["entities"]:
        return
    record = w.s["entities"][target]
    if record.get("category") == "plant_cover" and record.get("covers"):
        uncover_bed(w, target)
    if record.get("wasps"):
        C.stat(w, "injury")
        C.note(w, "stung", item=target)


# ---------------------------------------------------------------------------
# slow dynamics for beds and trays

def slow_garden(w, periods):
    dt = C.SLOW_S * periods
    days = dt / C.DAY
    state = w.s
    rain_mm = float((state.get("weather") or {}).get("rain_last_period_mm", 0.0))
    t_air = C.air_temp(w)
    sun = C.daylight(w)
    for bed_id in C.find_types(w, ["garden_bed"]):
        bed = state["entities"][bed_id]
        medium = bed.get("medium") or {}
        kind = medium.get("kind", "garden_soil")
        params = medium_params(kind)
        soil_l = P.soil_liters(bed)
        rid = C.room_of(w, bed_id)
        outdoor = C.is_outdoor_room(w, rid) if rid else True
        area = bed_area(bed)
        et_l = 0.0
        for pid in plants_on(w, bed_id):
            prec = state["entities"][pid]
            if prec.get("alive", True) and not prec.get("artificial"):
                et_l += float(P.prof(prec)["et"])
        mulch = bed.get("mulch")
        mulch_cm = 0.0
        if mulch and mulch.get("depth_cm"):
            mulch_cm = sum(mulch["depth_cm"]) / len(mulch["depth_cm"])
        evap_mm = 2.5 * (1.0 - min(0.7, mulch_cm / 10.0))
        env = C.clamp((t_air + 5.0) / 25.0, 0.1, 2.2) * (0.4 + 1.2 * sun) if outdoor else 0.6
        theta = float(medium.get("moisture", 0.2))
        r_now = rwc(theta, kind)
        stress = C.clamp(r_now / 0.4, 0.05, 1.0)
        loss_l = (et_l * env * stress + evap_mm * area * env * 0.5) * days
        new_theta = max(params["wilt"] * 0.5, theta - loss_l / max(1.0, soil_l))
        m = w.mut(bed_id)
        md = m["medium"]
        md["moisture"] = round(new_theta, 4)
        md["moisture_top"] = round(max(params["wilt"] * 0.3, min(new_theta + 0.05, float(md.get("moisture_top", theta)) - 2.5 * (theta - new_theta) - 0.02 * days)), 4)
        if outdoor and rain_mm > 0 and C.rain_exposed(w, bed_id) > 0:
            intercept = min(rain_mm, mulch_cm * 0.2)
            add_water_to_bed(w, bed_id, (rain_mm - intercept) * area, flow="trickle")
        cover_effects(w, bed_id, days)
        regrow_step(w, bed_id, days)
        germinate_step(w, bed_id, days)
        bed_status(w, bed_id)
    for tray in C.find_types(w, ["seed_tray"]):
        trec = state["entities"][tray]
        medium = trec.get("medium") or {}
        if float(medium.get("fill_frac", 0.0)) > 0:
            kind = medium.get("kind", "seed_mix")
            params = medium_params(kind)
            theta = float(medium.get("moisture", 0.0))
            loss = 0.04 * days
            bottom = float(trec.get("bottom_water_l", 0.0))
            m = w.mut(tray)
            soil_l = max(0.05, P.soil_liters(trec))
            wick = min(bottom, max(0.0, (params["sat"] - theta) * soil_l))
            m["bottom_water_l"] = round(max(0.0, bottom - wick - 0.02 * days), 4)
            if bottom > 0.05:
                m["_standing_h"] = round(float(m.get("_standing_h", 0.0)) + days * 24, 3)
                if float(m["_standing_h"]) > 12:
                    C.set_status(w, tray, damping_off_risk=True)
            md = m["medium"]
            md["moisture"] = round(max(params["wilt"] * 0.3, theta - loss + wick / soil_l), 4)
            md["moisture_top"] = round(max(params["wilt"] * 0.2, min(md["moisture"], float(md.get("moisture_top", theta)) - 1.5 * loss) + wick / soil_l * 0.5), 4)
            germinate_step(w, tray, days)
    for pot in C.find_types(w, ["plant_pot"]):
        prec = state["entities"][pot]
        if prec.get("sowings"):
            germinate_step(w, pot, days)
    harden_step(w, days, t_air, sun)


def _has_seedlings(w, cid, rec, holders=None):
    if C.is_type(rec, "seed_tray"):
        return any(s.get("germinated") and int(s.get("seedlings", 0)) > 0 for s in rec.get("sowings", []))
    if holders is not None:
        return cid in holders
    return bool(C.kids_of_type(w, cid, ["seedling", "nursery_plant"]))


def harden_step(w, days, t_air, sun):
    """Hardening off: each calendar day with >=1 h outdoors in daylight counts toward hardened_days (7 needed).
    A continuous sunny stint longer than 2 h + 1.2 h per hardened day scorches; a chilly night outdoors kills tray seedlings."""
    from ...kernel import clock
    day = clock.now(w.s).strftime("%Y-%m-%d")
    ents = w.s["entities"]
    holders = {ents[e].get("location") for e in C.find_types(w, ["seedling", "nursery_plant"])}
    for cid in C.find_types(w, ["seed_tray", "plant_pot", "nursery_pot"]):
        rec = w.s["entities"][cid]
        if not _has_seedlings(w, cid, rec, holders):
            continue
        rid = C.room_of(w, cid)
        outdoor = bool(rid) and C.is_outdoor_room(w, rid) and not C.enclosed(w, cid)
        if outdoor and sun > 0.2:
            m = w.mut(cid)
            log = m.setdefault("_harden_log", {})
            log[day] = round(float(log.get(day, 0.0)) + days * 24.0, 3)
            m["hardened_days"] = float(min(14, sum(1 for v in log.values() if v >= 1.0)))
            m["_sun_run_h"] = round(float(m.get("_sun_run_h", 0.0)) + days * 24.0, 3)
            limit = 2.0 + 1.2 * float(m["hardened_days"])
            shaded = C.covering(w, cid) is not None or C.is_roofed(w, rid)
            if sun > 0.6 and not shaded and m["_sun_run_h"] > limit:
                C.set_status(w, cid, sunscald=True)
                if not rec.get("_scald_noted"):
                    m["_scald_noted"] = True
                    C.stat(w, "seedlings_scorched")
                for child in C.kids_of_type(w, cid, "plant"):
                    P.harm(w, child, 0.1 * days * 24.0, "sunscald: put out in full sun too long before hardening off")
                for sowing in m.get("sowings", []):
                    if sowing.get("germinated"):
                        sowing["seedlings"] = int(int(sowing.get("seedlings", 0)) * 0.8)
        elif float(rec.get("_sun_run_h", 0.0)) > 0:
            w.mut(cid)["_sun_run_h"] = 0.0
        if outdoor and t_air < 4.0 and C.is_type(rec, "seed_tray"):
            m = w.mut(cid)
            lost = 0
            for sowing in m.get("sowings", []):
                if sowing.get("germinated") and int(sowing.get("seedlings", 0)) > 0:
                    keep = int(int(sowing["seedlings"]) * (0.5 if t_air < 1.0 else 0.85))
                    lost += int(sowing["seedlings"]) - keep
                    sowing["seedlings"] = keep
            if lost:
                C.stat(w, "seedlings_chilled", lost)
                C.set_status(w, cid, chilled=True)


@system("outdoor_plants.garden", order=122, max_dt=C.SLOW_S, period=C.SLOW_S)
def garden_system(w, dt, tick):
    """Beds, trays, germination and hardening off: once per 600 s with dt = accumulated time."""
    slow_garden(w, dt / C.SLOW_S)


@inspector("outdoor_plants.garden", order=61)
def inspect_garden(w, eid, record, details, ctx):
    if C.is_type(record, "garden_bed"):
        medium = record.get("medium") or {}
        details["soil_surface"] = P.surface_feel(record)
        details["sun"] = record.get("sun")
        details["plants"] = [p for p in plants_on(w, eid) if not C.realistic(ctx) or ctx.visible(p)]
        if record.get("mulch"):
            depths = record["mulch"].get("depth_cm") or []
            details["mulch_depth_cm"] = round(sum(depths) / max(1, len(depths)), 1)
        if record.get("sowings"):
            details["sowings"] = [{k: s.get(k) for k in ("crop", "variety", "row", "row_y_m", "depth_cm", "germinated", "seedlings")} for s in record["sowings"]]
            if C.realistic(ctx):
                details["sowings"] = [seen_sowing(s) for s in details["sowings"]]
        if record.get("mulch") and C.realistic(ctx):
            details["mulch_depth_cm"] = C.approx(ctx, details.get("mulch_depth_cm"), 1.0)      # by eye, to ~1 cm
            details["_perceived"] = list(details.get("_perceived") or []) + ["mulch_depth_cm"]
        details["utility_flags"] = list(record.get("utility_flags", []))
        if record.get("plan"):
            details["planting_plan"] = record["plan"]
    elif C.is_type(record, "seed_tray"):
        details["medium"] = dict(record.get("medium") or {})
        if C.realistic(ctx):
            # the mix is seen (kind, how full the cells are), its water content only felt or probed
            medium = details.pop("medium")
            fill = float(medium.get("fill_frac", 0.0) or 0.0)
            details["mix"] = medium.get("kind") if fill > 0 else "none"
            details["cells_filled"] = "empty" if fill < 0.05 else "partly filled" if fill < 0.85 else "filled to the rim" if fill <= 1.05 else "overfilled"
        details["soil_surface"] = P.surface_feel(record)
        details["sowings"] = [{k: s.get(k) for k in ("crop", "variety", "row", "germinated", "seedlings", "per_cell_now")} for s in record.get("sowings", [])]
        if C.realistic(ctx):
            details["sowings"] = [seen_sowing(s) for s in details["sowings"]]
        details["residue"] = float((record.get("soil") or {}).get("dirt", 0.0)) > 0.05
    elif C.is_type(record, "planting_hole"):
        details["diameter_cm"] = record.get("diameter_cm")
        details["depth_cm"] = record.get("depth_cm")
    elif record.get("produce"):
        details["color"] = record.get("color")
        details["size_cm"] = record.get("size_cm")
        details["looks_ripe"] = looks_ripe(record)
        if record.get("seeds_bulging"):
            details["seeds_bulging"] = True


def seen_sowing(sowing):
    """Realistic perception of a sown row: where it is and what has come up. Seeds under the soil are not visible, so
    an ungerminated row shows no crop, variety or sowing depth (row labels and the robot's own memory tell those);
    seedlings that are up show their crop and count."""
    out = {k: v for k, v in sowing.items() if k in ("row", "row_y_m", "germinated") and v is not None}
    if sowing.get("germinated"):
        out.update({k: sowing.get(k) for k in ("crop", "seedlings", "per_cell_now") if sowing.get(k) is not None})
    return out


def draw_material(ctx, source, liters, kinds=None):
    """Take up to `liters` of granular material from a held bag, or a nearby pile/wheelbarrow/tarp. Returns liters taken."""
    w = ctx.w
    rec = w.ent(source)
    require(ctx.holding(source) or C.reachable(ctx, source, 3), "Bring the material within reach (hold the bag or park the barrow/pile beside you)", "precondition")
    product = C.pinfo(rec) or None
    if C.is_type(rec, "tarp"):
        have = float((rec.get("load") or {}).get("soil_l", 0.0))
        take = min(have, liters)
        m = w.mut(source)
        m["load"]["soil_l"] = round(have - take, 2)
        return take
    if C.is_type(rec, "wheelbarrow") or C.is_type(rec, "garden_cart") or (rec.get("container") and not product):
        total = 0.0
        if kinds is not None:
            # which bags in the barrow hold the wanted material must not be decided by bags the robot has not read
            C.require_identified(ctx, *[c for c in w.children(source) if C.pinfo(w.s["entities"][c])])
        for child in w.children(source):
            crec = w.s["entities"][child]
            if C.pinfo(crec) and (kinds is None or C.pinfo(crec).get("kind") in kinds):
                total += draw_material(ctx, child, liters - total, kinds) if total < liters else 0.0
        return total
    C.require_identified(ctx, source)          # before anything that depends on what the bag holds
    require(product is not None, "That is not a bag or pile of material", "argument")
    if kinds is not None:
        require(product.get("kind") in kinds, ("That bag does not hold {}".format("/".join(kinds)) if C.realistic(ctx) else
                                               "That bag holds {}, not {}".format(product.get("kind"), "/".join(kinds))), "precondition")
    qty = float(product.get("qty", 0.0))
    unit = product.get("unit", "l")
    density = {"l": 1.0, "kg": 1.0, "ml": 0.001}.get(unit, 1.0)
    have_l = qty * density if unit != "kg" else qty
    take = min(have_l, liters)
    m = w.mut(source)
    m["product"]["qty"] = round(qty - (take / density if unit != "kg" else take), 4)
    per_l = float(rec.get("mass_kg", 1.0)) / max(1e-6, have_l) if have_l > 0 else 0.0
    m["mass_kg"] = round(max(0.05, float(rec.get("mass_kg", 1.0)) - take * per_l), 3)
    return take
