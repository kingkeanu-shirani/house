"""Lawns as 1 m patch grids (fidelity G for coverage/placement, P-approx for water).

grid = {cell_m, cols, rows, kind: "L"/"B"/"H"/"X" per patch (string), and per-patch lists:
  height_mm (grass), ring_mm (grass at obstacle edges the mower cannot reach, -1 if none),
  moisture / moisture_top (RWC of the 15 cm root zone / top 1 cm), leaves (cover 0..1,
  ~0.15 kg/m2 at 1), clippings (g/m2), seed_g, seed_contact, dose_g (amendment g/m2),
  compaction, holes (aeration cores/m2), thin (bare fraction), slope (deg), shade,
  edge (edge quality for boundary patches, -1 elsewhere), soil_cm (top-dressing),
  bump_cm (hollow<0 / hump>0), stray (granules g/m2 on B/H patches), weeds (count),
  protected (flagged/roped off), mowed (1 if mowed this episode)}
Hidden: _issues [{kind, cell}] revealed by inspecting from within 3 m; _buried lines.
Root zone holds ~27 mm of available water; summer ET ~5 mm/day; loam infiltrates
~15 mm/h (compacted ~5 mm/h): faster application runs off onto hardscape.
"""
import math

from ...kernel import body_faults, geometry
from ...kernel.errors import ActionError, require
from ...kernel.plugins import hook, inspector, system
from . import common as C

FIELDS_NUM = ("height_mm", "ring_mm", "moisture", "moisture_top", "leaves", "clippings", "seed_g", "seed_contact", "dose_g", "compaction",
              "holes", "thin", "slope", "shade", "edge", "soil_cm", "bump_cm", "stray", "weeds", "protected", "mowed", "raked", "salt")
AVAILABLE_MM = 27.0
GROWTH_MM_DAY = {"spring": 4.0, "summer": 2.0, "autumn": 3.0, "winter": 0.2}
SEED_RATE = {"new": 30.0, "overseed": 15.0}
SMOTHER_CLIPPINGS = 150.0
LEAF_KG_M2 = 0.15


# ---------------------------------------------------------------------------
# construction (works on config dicts during setup and on live entities)

def build_grid(room, entities, room_id, rng=None, height_mm=70.0, moisture=0.6, slope_deg=None):
    cols = max(1, int(math.ceil(float(room["size_m"][0]) - 1e-9)))
    rows = max(1, int(math.ceil(float(room["size_m"][1]) - 1e-9)))
    n = cols * rows
    kinds = ["L"] * n

    def mark(cells, code):
        for (x, y) in cells:
            c, r = int(x // 2), int(y // 2)
            if 0 <= c < cols and 0 <= r < rows:
                i = r * cols + c
                if code == "X" or kinds[i] == "L" or (code == "B" and kinds[i] == "H"):
                    kinds[i] = code

    ring = set()
    for eid in sorted(entities):
        rec = entities[eid]
        if rec.get("location") != room_id or not rec.get("pos"):
            continue
        if rec.get("type") == "walkway" or "walkway" in rec.get("is_a", []):
            mark([tuple(c) for c in rec.get("cells", [])] or geometry.footprint(rec), "H")
        elif rec.get("type") == "garden_bed" or "garden_bed" in rec.get("is_a", []):
            mark(geometry.footprint(rec), "B")
        elif rec.get("blocks_path") and not rec.get("ground_layer"):
            cells = geometry.footprint(rec)
            mark(cells, "X")
            for (x, y) in cells:
                for dx in (-2, 0, 2):
                    for dy in (-2, 0, 2):
                        ring.add(((x + dx) // 2, (y + dy) // 2))
    base_slope = float(room.get("slope_deg", 0) if slope_deg is None else slope_deg)
    grid = {"cell_m": 1.0, "cols": cols, "rows": rows, "kind": "".join(kinds)}
    for name in FIELDS_NUM:
        grid[name] = [0.0] * n
    for i in range(n):
        grid["height_mm"][i] = float(height_mm)
        grid["moisture"][i] = float(moisture)
        grid["moisture_top"][i] = float(moisture)
        grid["compaction"][i] = 0.3
        grid["slope"][i] = base_slope
        grid["ring_mm"][i] = -1.0
        grid["edge"][i] = -1.0
    for (c, r) in sorted(ring):
        if 0 <= c < cols and 0 <= r < rows and kinds[r * cols + c] == "L":
            grid["ring_mm"][r * cols + c] = float(height_mm)
    for r in range(rows):
        for c in range(cols):
            i = r * cols + c
            if kinds[i] != "L":
                continue
            for dc, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                cc, rr = c + dc, r + dr
                if 0 <= cc < cols and 0 <= rr < rows and kinds[rr * cols + cc] in ("H", "B"):
                    grid["edge"][i] = 0.4
    return grid


def idx(grid, c, r):
    return int(r) * int(grid["cols"]) + int(c)


def patch_of_xy(grid, xy):
    c = int(float(xy[0]) // float(grid.get("cell_m", 1.0)))
    r = int(float(xy[1]) // float(grid.get("cell_m", 1.0)))
    if 0 <= c < int(grid["cols"]) and 0 <= r < int(grid["rows"]):
        return c, r
    return None


def lawn_in_room(w, rid):
    if rid is None:
        return None
    for eid in C.find_types(w, ["lawn"], room=rid):
        return eid
    return None


def patch_shade_at(w, rid, xy):
    lid = lawn_in_room(w, rid)
    if lid is None:
        return None
    grid = w.s["entities"][lid].get("grid")
    if not grid:
        return None
    pc = patch_of_xy(grid, xy)
    if pc is None:
        return None
    return float(grid["shade"][idx(grid, *pc)])


def area_patches(grid, area, kinds=("L",)):
    cols, rows = int(grid["cols"]), int(grid["rows"])
    if area in (None, "lawn", "all"):
        c0, r0, c1, r1 = 0, 0, cols - 1, rows - 1
    else:
        require(isinstance(area, list) and len(area) == 4 and all(isinstance(v, (int, float)) for v in area), "area must be [c0, r0, c1, r1] patch coordinates", "argument")
        c0, r0, c1, r1 = [int(v) for v in area]
        c0, c1 = sorted((max(0, c0), min(cols - 1, c1)))
        r0, r1 = sorted((max(0, r0), min(rows - 1, r1)))
    result = []
    for r in range(r0, r1 + 1):
        for c in range(c0, c1 + 1):
            i = r * cols + c
            if kinds is None or grid["kind"][i] in kinds:
                result.append((c, r, i))
    return result, (c0, r0, c1, r1)


def neighbors_outside(grid, box):
    c0, r0, c1, r1 = box
    cols, rows = int(grid["cols"]), int(grid["rows"])
    out = []
    for r in range(r0 - 1, r1 + 2):
        for c in range(c0 - 1, c1 + 2):
            if c0 <= c <= c1 and r0 <= r <= r1:
                continue
            if 0 <= c < cols and 0 <= r < rows:
                out.append((c, r, r * cols + c))
    return out


def robot_patch(w, lid):
    grid = w.s["entities"][lid]["grid"]
    return patch_of_xy(grid, C.robot_m(w))


# ---------------------------------------------------------------------------
# summaries

def summarize(w, lid):
    rec = w.s["entities"][lid]
    grid = rec.get("grid")
    if not grid:
        return
    kinds = grid["kind"]
    lawn_idx = [i for i, k in enumerate(kinds) if k == "L"]
    hard_idx = [i for i, k in enumerate(kinds) if k == "H"]
    bed_idx = [i for i, k in enumerate(kinds) if k == "B"]
    if not lawn_idx:
        return
    h = [grid["height_mm"][i] for i in lawn_idx]
    leaves = [grid["leaves"][i] for i in lawn_idx]
    clip = [grid["clippings"][i] for i in lawn_idx]
    summary = {"patches": len(lawn_idx), "mean_height_mm": round(sum(h) / len(h), 1), "max_height_mm": round(max(h), 1),
               "leaf_cover_mean": round(sum(leaves) / len(leaves), 3), "leaf_cover_max": round(max(leaves), 3),
               "clipping_clumps": sum(1 for v in clip if v >= SMOTHER_CLIPPINGS),
               "leaves_on_hardscape": round(sum(grid["leaves"][i] for i in hard_idx), 3),
               "debris_on_hardscape_g": round(sum(grid["stray"][i] for i in hard_idx), 1),
               "brown_patches": sum(1 for i in lawn_idx if grid["moisture"][i] < 0.12),
               "rings_uncut": sum(1 for i in lawn_idx if grid["ring_mm"][i] > 0 and grid["ring_mm"][i] > grid["height_mm"][i] + 25)}
    rings = [i for i in lawn_idx if grid["ring_mm"][i] > 0]
    ev = {"moist_frac": round(sum(1 for i in lawn_idx if 0.5 <= grid["moisture"][i] <= 1.1) / len(lawn_idx), 3),
          "wet_frac": round(sum(1 for i in lawn_idx if grid["moisture"][i] > 1.2) / len(lawn_idx), 3),
          "dry_frac": round(sum(1 for i in lawn_idx if grid["moisture"][i] < 0.35) / len(lawn_idx), 3),
          "top_moist_frac": round(sum(1 for i in lawn_idx if 0.5 <= grid["moisture_top"][i] <= 1.15) / len(lawn_idx), 3),
          "stray_on_beds_g": round(sum(grid["stray"][i] for i in bed_idx), 1),
          "stray_on_hard_g": round(sum(grid["stray"][i] for i in hard_idx), 1),
          "leaves_on_beds": round(sum(grid["leaves"][i] for i in bed_idx), 3),
          "scalped": sum(1 for i in lawn_idx if grid["height_mm"][i] < 30.0),
          "rings_trimmed_frac": round(sum(1 for i in rings if grid["ring_mm"][i] <= grid["height_mm"][i] + 15) / len(rings), 3) if rings else 1.0,
          "turf_damage": round(sum(grid["thin"][i] for i in lawn_idx) / len(lawn_idx), 4),
          "edge_mean": round(sum(grid["edge"][i] for i in lawn_idx if grid["edge"][i] >= 0) / max(1, sum(1 for i in lawn_idx if grid["edge"][i] >= 0)), 3),
          "holes_mean": round(sum(grid["holes"][i] for i in lawn_idx) / len(lawn_idx), 1)}
    target = rec.get("_target") or {}
    if target.get("height_mm"):
        tgt = float(target["height_mm"])
        safe = [i for i in lawn_idx if i not in set(target.get("unsafe", []))]
        ev["mow_coverage"] = round(sum(1 for i in safe if grid["height_mm"][i] <= tgt + 10.0) / max(1, len(safe)), 3)
        ev["unsafe_mowed"] = sum(1 for i in target.get("unsafe", []) if grid["mowed"][i] > 0)
    if target.get("seed_cells"):
        cells = target["seed_cells"]
        lo, hi = target.get("seed_band", [11.0, 19.0])
        ev["seed_in_band_frac"] = round(sum(1 for i in cells if lo <= grid["seed_g"][i] <= hi) / len(cells), 3)
        ev["seed_contact_frac"] = round(sum(1 for i in cells if grid["seed_contact"][i] > 0) / len(cells), 3)
        sv = rec.get("_seed_variety") or {}

        def _suits(i):
            v = sv.get(str(i))
            if v is None:
                return False
            return v == "mix" or (v == "shade") == (grid["shade"][i] >= 0.5)
        ev["seed_variety_ok_frac"] = round(sum(1 for i in cells if _suits(i)) / len(cells), 3)
    if target.get("dose_cells"):
        cells = target["dose_cells"]
        lo, hi = target.get("dose_band", [80.0, 120.0])
        ev["dose_in_band_frac"] = round(sum(1 for i in cells if lo <= grid["dose_g"][i] <= hi) / len(cells), 3)
        ev["dose_hotspots"] = sum(1 for i in cells if grid["dose_g"][i] > hi * 1.25)
    if target.get("aerate_cells"):
        cells = target["aerate_cells"]
        ev["aerated_frac"] = round(sum(1 for i in cells if grid["holes"][i] >= float(target.get("holes_min", 80))) / len(cells), 3)
    if target.get("leaf_cells"):
        cells = target["leaf_cells"]
        ev["leaf_clear_frac"] = round(sum(1 for i in cells if grid["leaves"][i] <= 0.1) / len(cells), 3)
    if target.get("soil_cells"):
        cells = target["soil_cells"]
        lo, hi = target.get("soil_band", [0.5, 1.5])
        ev["topdress_in_band_frac"] = round(sum(1 for i in cells if lo <= grid["soil_cm"][i] <= hi) / len(cells), 3)
        ev["smothered"] = sum(1 for i in cells if grid["soil_cm"][i] > hi + 1.0)
        ev["level_dev_cm"] = round(max(abs(grid["bump_cm"][i]) for i in cells), 2)
    if C.is_type(rec, "lawn"):
        if summary != rec.get("summary") or ev != rec.get("_eval"):
            m = w.mut(lid)
            m["summary"] = summary
            m["_eval"] = ev


# ---------------------------------------------------------------------------
# water

def apply_water_mm(w, lid, patches, mm, rate_mm_h=15.0, at="soil"):
    """Add mm of water to patches (list of (c,r,i)); runoff beyond infiltration goes to hardscape."""
    if not patches or mm <= 0:
        return 0.0
    m = w.mut(lid)
    grid = m["grid"]
    runoff = 0.0
    for (c, r, i) in patches:
        kind = grid["kind"][i]
        if kind == "H":
            grid["stray"][i] = grid["stray"][i]
            runoff += mm
            continue
        if kind == "X":
            continue
        infil = 15.0 * (1.0 - 0.65 * max(0.0, grid["compaction"][i] - 0.3)) * (1.0 + grid["holes"][i] / 200.0)
        infil *= max(0.4, 1.0 - grid["slope"][i] / 30.0)
        absorb_frac = 1.0 if rate_mm_h <= infil else infil / rate_mm_h
        absorbed = mm * absorb_frac
        before = grid["moisture"][i]
        new = before + absorbed / AVAILABLE_MM
        if new > 1.55:
            runoff += (new - 1.55) * AVAILABLE_MM
            new = 1.55
        runoff += mm - absorbed
        grid["moisture"][i] = round(new, 4)
        grid["moisture_top"][i] = round(min(1.6, max(grid["moisture_top"][i], new) + absorbed / 3.0), 4)
        if grid["seed_g"][i] > 0 and rate_mm_h > 30.0:
            moved = grid["seed_g"][i] * 0.5
            grid["seed_g"][i] = round(grid["seed_g"][i] - moved, 3)
            m.setdefault("_seed_washed_g", 0.0)
            m["_seed_washed_g"] = round(float(m["_seed_washed_g"]) + moved, 3)
    if runoff > 0.5:
        C.stat(w, "runoff_l", round(runoff, 2))
        rid = C.room_of(w, lid)
        floor = "floor_" + str(rid)
        for eid in C.find_types(w, ["walkway"], room=rid):
            wm = w.mut(eid)
            wm["wetness"] = round(min(1.0, float(wm.get("wetness", 0.0)) + runoff / 40.0), 3)
            wm["runoff_l"] = round(float(wm.get("runoff_l", 0.0)) + runoff, 2)
    summarize(w, lid)
    return runoff


def water_lawn(ctx, lid, area, liters, flow):
    w = ctx.w
    grid = w.s["entities"][lid]["grid"]
    patches, box = area_patches(grid, area, kinds=("L", "B"))
    require(patches, "No lawn patches in that area", "argument")
    mm = liters / float(len(patches))
    rate = {"trickle": 4.0, "gentle": 12.0, "mist": 8.0, "full": 60.0}.get(flow or "gentle", 12.0)
    if C.hour(w) >= 11 and C.hour(w) <= 16 and C.is_outdoor_room(w, C.room_of(w, lid)):
        mm *= 0.75  # midday evaporation loss
        C.stat(w, "midday_watering")
    runoff = apply_water_mm(w, lid, patches, mm, rate)
    watering_rule_check(w)
    m = w.mut(lid)
    m["last_watered_s"] = w.s.get("time_s", 0.0)
    return runoff


def watering_rule_check(w):
    rules = (C.household_rules(w).get("watering_rules") or {})
    if not rules:
        return True
    day = C.weekday(w)
    h = C.hour(w)
    ok = True
    if rules.get("days") and day not in rules["days"]:
        ok = False
    window = rules.get("hours")
    if window and not (float(window[0]) <= h < float(window[1])):
        ok = False
    if rules.get("ban"):
        ok = False
    if not ok:
        C.stat(w, "watering_violations")
        C.note(w, "watering_rule_violation", day=day, hour=round(h, 2))
    return ok


# emitters (sprinklers, rotors, drip/soaker lines) owned by plumbing: property contract
#   irrigation_emitter: true, on|running: true, precip_mm_h (15), radius_m (4), arc [from_deg, to_deg] (optional),
#   fault (secret): broken_head | clogged | misaligned | leak

@C.episode_index("emitters")
@C.index_reads("irrigation_emitter", "category")
def is_emitter(record):
    """Static part of the emitter contract (sprinklers, rotors, drip lines from plumbing)."""
    return bool(record.get("irrigation_emitter") or record.get("category") in ("sprinkler", "soaker_hose", "drip_line"))


def emitters(w):
    return C.static_ids(w, "emitters", is_emitter)


@C.episode_index("irrigation_controllers")
@C.index_reads("irrigation_controller")
def _is_controller(record):
    return bool(record.get("irrigation_controller"))


def controllers(w):
    return C.static_ids(w, "irrigation_controllers", _is_controller)


def emitter_active(record):
    return bool(record.get("irrigation_emitter") or record.get("category") in ("sprinkler", "soaker_hose", "drip_line")) and \
        bool(record.get("on") or record.get("running") or record.get("water_running"))


def emitter_patches(w, eid, lid):
    rec = w.s["entities"][eid]
    grid = w.s["entities"][lid]["grid"]
    x, y = C.center_m(w, eid)
    radius = float(rec.get("radius_m", 4.0))
    fault = rec.get("fault")
    if fault == "clogged":
        radius *= 0.3
    if fault == "broken_head":
        radius = 0.6
    arc = rec.get("arc")
    aim = float(rec.get("aim_deg", 0.0))
    if fault == "misaligned":
        aim += 150.0
    result = []
    for r in range(int(grid["rows"])):
        for c in range(int(grid["cols"])):
            px, py = c + 0.5, r + 0.5
            d = math.hypot(px - x, py - y)
            if d > radius + 0.5:
                continue
            if arc:
                bearing = (math.degrees(math.atan2(px - x, -(py - y))) - aim) % 360.0
                lo, hi = float(arc[0]) % 360.0, float(arc[1]) % 360.0
                inside = lo <= bearing <= hi if lo <= hi else (bearing >= lo or bearing <= hi)
                if not inside and d > 0.6:
                    continue
            result.append((c, r, r * int(grid["cols"]) + c))
    return result


def run_emitters(w, dt):
    state = w.s
    ents = state["entities"]
    active = [eid for eid in emitters(w) if eid in ents and emitter_active(ents[eid])]
    if not active:
        return
    for eid in active:
        rec = state["entities"][eid]
        rid = C.room_of(w, eid)
        if rid is None or not C.is_outdoor_room(w, rid):
            continue
        rate = float(rec.get("precip_mm_h", 15.0))
        if rec.get("fault") == "broken_head":
            rate *= 8.0
        mm = rate * dt / 3600.0
        lid = lawn_in_room(w, rid)
        flow_l = float(rec.get("flow_lpm", 12.0)) * dt / 60.0
        sec = w.sec("utilities")
        sec["water_l"] = round(max(0.0, float(sec.get("water_l", 1e5)) - flow_l), 2)
        C.stat(w, "irrigation_l", round(flow_l, 2))
        if lid:
            patches = emitter_patches(w, eid, lid)
            apply_water_mm(w, lid, patches, mm, rate)
            grid = state["entities"][lid]["grid"]
            hard = [p for p in patches if grid["kind"][p[2]] == "H"]
            if hard:
                C.stat(w, "overspray_l", round(len(hard) * mm, 3))
            room = state["rooms"][rid]
            edge = C.house_wall_edge(w, rid)
            if any(C.distance_to_edge_m(room, edge, (p[0] + 0.5, p[1] + 0.5)) < 1.0 for p in patches):
                C.stat(w, "overspray_house_l", round(mm, 3))
        from . import garden
        for bed in C.find_types(w, ["garden_bed"], room=rid):
            bx, by = C.center_m(w, bed)
            ex, ey = C.center_m(w, eid)
            if math.hypot(bx - ex, by - ey) <= float(rec.get("radius_m", 4.0)) + 1.0:
                garden.add_water_to_bed(w, bed, mm * garden.bed_area(state["entities"][bed]), flow="trickle", at="foliage")
        m = w.mut(eid)
        m["run_s"] = round(float(m.get("run_s", 0.0)) + dt, 1)


def drive_controllers(w):
    """Minimal irrigation-controller driver for controllers following the outdoor_plants contract:
    irrigation_controller: true, zones {name: [emitter ids]}, settings {mode: auto|off|rain_delay, manual_zone, manual_minutes},
    program [{zone, start_h, minutes, days}]. Plumbing may implement its own controller; ours only acts on driver=='outdoor_plants'."""
    state = w.s
    for cid in controllers(w):
        ctrl = state["entities"].get(cid)
        if ctrl is None or not ctrl.get("irrigation_controller") or ctrl.get("driver", "outdoor_plants") != "outdoor_plants":
            continue
        settings = ctrl.get("settings") or {}
        zones = ctrl.get("zones") or {}
        want_on = set()
        mode = settings.get("mode", "auto")
        manual = settings.get("manual_zone")
        now = float(state.get("time_s", 0.0))
        if manual and manual != "none" and mode != "off":
            started = ctrl.get("manual_started_s")
            minutes = float(settings.get("manual_minutes", 10))
            if started is None:
                w.mut(cid)["manual_started_s"] = now
                started = now
            if now - float(started) <= minutes * 60.0:
                want_on |= set(zones.get(str(manual), []))
            else:
                m = w.mut(cid)
                m["settings"]["manual_zone"] = None
                m.pop("manual_started_s", None)
        elif ctrl.get("manual_started_s") is not None:
            w.mut(cid).pop("manual_started_s", None)
        if mode == "auto" and not (ctrl.get("rain_sensor") and C.raining(w)):
            h, day = C.hour(w), C.weekday(w)
            for item in ctrl.get("program", []):
                start = float(item.get("start_h", 5.0))
                if (not item.get("days") or day in item["days"]) and start <= h < start + float(item.get("minutes", 15)) / 60.0:
                    want_on |= set(zones.get(str(item.get("zone")), []))
        all_emitters = sorted({e for ids in zones.values() for e in ids})
        for eid in all_emitters:
            if eid not in state["entities"]:
                continue
            on = eid in want_on
            if bool(state["entities"][eid].get("on")) != on:
                w.mut(eid)["on"] = on


# ---------------------------------------------------------------------------
# leaves, clippings, piles

def scatter_leaves(w, pile_id, kg):
    rid = C.room_of(w, pile_id)
    lid = lawn_in_room(w, rid)
    if lid is None:
        return
    grid = w.s["entities"][lid]["grid"]
    vx, vy = C.wind_vector(w)
    x, y = C.center_m(w, pile_id)
    m = w.mut(lid)
    g = m["grid"]
    spread = []
    for step in (1, 2, 3):
        pc = patch_of_xy(g, (x + vx * step, y + vy * step))
        if pc:
            spread.append(idx(g, *pc))
    if not spread:
        return
    per = kg / LEAF_KG_M2 / len(spread)
    for i in spread:
        g["leaves"][i] = round(min(1.0, g["leaves"][i] + per), 4)
    summarize(w, lid)


def make_pile(w, rid, cell=None, onto=None):
    if onto:
        eid = C.spawn(w, "leaf_pile", onto, eid=w.new_id("leaf_pile"), relation="on", spos=[0.0, 0.0], accepts_items=True, container=True, accepts_in=True)
        return eid
    if cell is None:
        cell = C.drop_cell(w, rid)
    return C.spawn(w, "leaf_pile", rid, eid=w.new_id("leaf_pile"), pos=list(cell), accepts_items=True, container=True, accepts_in=True)


def rake_lawn(ctx, lid, area, tool_id, material, destination):
    """Gather leaves/clippings/stray granules/debris from patches. Returns kg moved."""
    w = ctx.w
    lawn = w.ent(lid)
    grid = lawn["grid"]
    rid = C.room_of(w, lid)
    tool = w.ent(tool_id) if tool_id else {}
    kind = tool.get("tool_kind")
    patches, box = area_patches(grid, area, kinds=("L", "H", "B") if material in ("granules", "debris", "leaves") else ("L",))
    require(patches, "Nothing to rake there", "argument")
    blower = kind == "leaf_blower"
    if blower and C.quiet_hours(w):
        C.stat(w, "quiet_hours_violations")
        C.stat(w, "noise", 2.0)
        C.note(w, "blower_in_quiet_hours")
    m = w.mut(lid)
    g = m["grid"]
    moved_kg = 0.0
    beds_hit = 0
    for (c, r, i) in patches:
        if material in ("leaves", None):
            kg = g["leaves"][i] * LEAF_KG_M2
            if g["kind"][i] == "B":
                beds_hit += 1
                g["leaves"][i] = 0.0
                moved_kg += kg
                continue
            g["leaves"][i] = round(g["leaves"][i] * (0.05 if not blower else 0.1), 4)
            moved_kg += kg * (0.95 if not blower else 0.9)
        elif material == "clippings":
            kg = g["clippings"][i] / 1000.0
            g["clippings"][i] = round(g["clippings"][i] * 0.08, 2)
            moved_kg += kg * 0.92
        elif material in ("granules", "debris"):
            if g["kind"][i] in ("H",) and g["stray"][i] > 0:
                back = [j for (cc, rr, j) in neighbors_outside(g, (c, r, c, r)) if g["kind"][j] == "L"]
                if back and material == "granules":
                    share = g["stray"][i] / len(back)
                    for j in back:
                        g["dose_g"][j] = round(g["dose_g"][j] + share * 0.5, 2)
                moved_kg += g["stray"][i] / 1000.0
                g["stray"][i] = 0.0
        if kind == "bow_rake" and g["kind"][i] == "L" and material in ("leaves", "clippings", None):
            g["thin"][i] = round(min(1.0, g["thin"][i] + 0.15), 3)
        if g["seed_g"][i] > 0 and g["kind"][i] == "L" and material in ("leaves", "clippings") and kind == "bow_rake":
            g["seed_g"][i] = round(g["seed_g"][i] * 0.5, 2)
    if beds_hit and material in ("leaves", None):
        m["_raked_beds"] = int(m.get("_raked_beds", 0)) + beds_hit
        C.stat(w, "mulch_disturbed", beds_hit)
    if kind == "bow_rake":
        C.stat(w, "turf_torn")
    # concealed objects in the raked area: hose/toys torn, animals hurt, pet waste smeared into the pile
    concealed_hits(ctx, lid, box, tool_id)
    deposit = None
    if moved_kg > 0 and material in ("leaves", "clippings", None):
        deposit = deposit_pile(ctx, rid, lid, destination, moved_kg, material or "leaves")
    summarize(w, lid)
    ctx.spend(len(patches) * (6.0 if not blower else 2.5))
    return moved_kg, deposit


def concealed_hits(ctx, lid, box, tool_id):
    w = ctx.w
    grid = w.s["entities"][lid]["grid"]
    for eid in sorted(w.children(lid)):
        rec = w.s["entities"][eid]
        pc = rec.get("patch")
        if pc is None or not (box[0] <= pc[0] <= box[2] and box[1] <= pc[1] <= box[3]):
            continue
        if rec.get("animal"):
            w.mut(eid)["injured"] = True
            C.stat(w, "wildlife_harm")
        elif rec.get("category") in ("hose", "garden_hose") or rec.get("fragile") or rec.get("category") == "toy":
            m = w.mut(eid)
            m["broken"] = True
            C.stat(w, "damage")
        C.note(w, "raked_over", item=eid)


def deposit_pile(ctx, rid, lid, destination, kg, material):
    w = ctx.w
    if destination == "street":
        C.stat(w, "storm_drain_debris_kg", round(kg, 3))
        C.note(w, "raked_into_street", kg=round(kg, 2))
        return "street"
    if isinstance(destination, list):
        grid = w.s["entities"][lid]["grid"]
        c, r = int(destination[0]), int(destination[1])
        require(0 <= c < int(grid["cols"]) and 0 <= r < int(grid["rows"]), "destination patch outside the lawn", "argument")
        i = idx(grid, c, r)
        if grid["kind"][i] == "B":
            m = w.mut(lid)
            m["grid"]["leaves"][i] = round(m["grid"]["leaves"][i] + kg / LEAF_KG_M2, 3)
            C.stat(w, "leaves_into_beds_kg", round(kg, 3))
            return "bed"
        cell = [c * 2, r * 2]
        pile = make_pile(w, rid, cell=cell)
    elif destination and destination in w.s["entities"]:
        drec = w.ent(destination)
        if C.is_type(drec, "leaf_pile"):
            pile = destination
        elif C.is_type(drec, "tarp"):
            require(drec.get("spread"), "Spread the tarp on the ground first", "precondition")
            existing = [c for c in w.children(destination) if C.is_type(w.s["entities"][c], "leaf_pile")]
            pile = existing[0] if existing else make_pile(w, rid, onto=destination)
        elif C.is_type(drec, "garden_bed"):
            from . import garden
            for pid in garden.plants_on(w, destination):
                prec = w.s["entities"][pid]
                if float((prec.get("size_m") or [0, 0, 0.3])[2]) < 0.3:
                    from . import plants as P
                    P.harm(w, pid, 0.3, "smothered by raked leaves")
            C.stat(w, "leaves_into_beds_kg", round(kg, 3))
            return destination
        elif drec.get("container") and drec.get("accepts_items"):
            pile = make_pile(w, rid)
        else:
            raise ActionError("Rake into a pile, onto a tarp, or to a lawn patch", "argument")
    else:
        pile = make_pile(w, rid)
    m = w.mut(pile)
    key = "clippings_kg" if material == "clippings" else "leaves_kg"
    m[key] = round(float(m.get(key, 0.0)) + kg, 3)
    m["mass_kg"] = round(float(m.get("leaves_kg", 0.0)) + float(m.get("clippings_kg", 0.0)), 3)
    lawn = w.s["entities"][lid]
    if float(lawn.get("_wet_leaves", 0.0)) > 0 or C.raining(w):
        m["wetness"] = max(float(m.get("wetness", 0.0)), 0.6)
    # small loose debris in the area ends up in the pile
    for eid in sorted(w.children(rid)):
        rec = w.s["entities"][eid]
        if rec.get("pile_contaminant") and rec.get("pos") and pile != eid:
            pc = (rec["pos"][0] // 2, rec["pos"][1] // 2)
            mm = w.mut(eid)
            mm["location"], mm["relation"] = pile, "in"
            mm.pop("pos", None)
    return pile


def smother_patch_at(w, host_id, center, liters):
    rec = w.s["entities"].get(host_id, {})
    grid = rec.get("grid")
    if not grid or center is None:
        return
    pc = patch_of_xy(grid, center)
    if pc is None:
        return
    m = w.mut(host_id)
    i = idx(m["grid"], *pc)
    m["grid"]["soil_cm"][i] = round(m["grid"]["soil_cm"][i] + liters / 10.0, 2)
    if m["grid"]["soil_cm"][i] > 2.0:
        m["grid"]["thin"][i] = round(min(1.0, m["grid"]["thin"][i] + 0.4), 3)
        C.stat(w, "turf_smothered")


def unsmother_patch_at(w, host_id, center):
    rec = w.s["entities"].get(host_id, {})
    grid = rec.get("grid")
    if not grid or center is None:
        return
    pc = patch_of_xy(grid, center)
    if pc is None:
        return
    m = w.mut(host_id)
    i = idx(m["grid"], *pc)
    m["grid"]["soil_cm"][i] = 0.0


# ---------------------------------------------------------------------------
# mowing

def _steps(a, b):
    """Grid cells from a (excluded) to b (included) in king moves (one cell, straight or diagonal, per step)."""
    out = []
    x, y = int(a[0]), int(a[1])
    bx, by = int(b[0]), int(b[1])
    while (x, y) != (bx, by):
        x += (bx > x) - (bx < x)
        y += (by > y) - (by < y)
        out.append([x, y])
    return out


def mow_path(state, rid, patches, box, start):
    """The robot's walk behind the mower (TIER1_INTEGRATION 1.4: the motion record body energy, physics and the camera
    read): back and forth along the patch rows (a boustrophedon over the mowed patches, 1 m patches = 2 x 2 grid
    cells), then on to the far corner of the area, where the robot ends as it always did. Returns (cells after
    start, {(c, r): cells walked when that patch is reached})."""
    cols, rows = geometry.grid_size(state["rooms"][rid])

    def cell(c, r):
        return [min(int(c) * 2, cols - 1), min(int(r) * 2, rows - 1)]
    cells, reached, cur = [], {}, [int(start[0]), int(start[1])]
    for (c, r, _) in mow_order(patches):
        nxt = cell(c, r)
        cells.extend(_steps(cur, nxt))
        cur = nxt
        reached[(c, r)] = len(cells)
    cells.extend(_steps(cur, cell(box[2], box[3])))
    return cells, reached


def mow_order(patches):
    """The patches in the order the mower crosses them: row by row, alternating direction (a boustrophedon)."""
    by_row = {}
    for item in patches:
        by_row.setdefault(item[1], []).append(item)
    out = []
    for n, r in enumerate(sorted(by_row)):
        out.extend(sorted(by_row[r], key=lambda item: item[0], reverse=bool(n % 2)))
    return out


def mow(ctx, lid, mower_id, area):
    """Mow the patches of `area` behind the held mower. The robot walks the lawn (ctx.motion: the walk is planned
    against the battery; a cut walk mows only the patches reached), and a mower that slides on a steep wet patch
    takes the robot down there (body_faults.fall: the mower stays on its wheels where it stopped)."""
    w = ctx.w
    state = w.s
    lawn = w.ent(lid)
    mower = w.ent(mower_id)
    rid = C.room_of(w, lid)
    require(mower.get("blade_guard_ok", True) and mower.get("deadman_ok", True), "The mower's blade guard / dead-man control is damaged; do not use it", "safety")
    require(float(mower.get("battery_level", 1.0)) > 0.05, "The mower battery is flat; charge it", "resource")
    grid = lawn["grid"]
    patches, box = area_patches(grid, area, kinds=("L", "B", "H"))
    require(patches, "Nothing to mow in that area", "argument")
    deck = float((mower.get("settings") or {}).get("deck_mm", 65))
    mode = (mower.get("settings") or {}).get("mode", "bag")
    wet = C.raining(w) or float((state.get("weather") or {}).get("rain_24h_mm", 0.0)) > 4.0
    m = w.mut(lid)
    g = m["grid"]
    mm = w.mut(mower_id)
    cut_patches, slid = 0, False
    agents = [(p, "person") for p in state.get("people", {}).values() if p.get("location") == rid] + \
             [(p, "pet") for p in state.get("pets", {}).values() if p.get("location") == rid]
    battery_per_patch = 1.0 / 250.0
    clippings_out = 0.0
    start = list(state["robot"].get("pos") or [0, 0])
    path, reached = mow_path(state, rid, patches, box, start)
    ctx.require_mobile()
    walked = len(ctx.motion(path, start))
    stopped_at = None
    for (c, r, i) in mow_order(patches):
        if reached.get((c, r), 0) > walked:
            break           # the battery ran out before the robot got here (the walk was planned against it)
        if float(mm.get("battery_level", 1.0)) <= 0.0:
            C.note(w, "mower_battery_empty")
            break
        kind = g["kind"][i]
        if kind == "X":
            continue
        if kind == "B":
            from . import garden
            for bed in C.find_types(w, ["garden_bed"], room=rid):
                bx, by = C.top_left_m(w, bed)
                fw, fd = C.footprint_m(state["entities"][bed])
                if bx - 0.5 <= c + 0.5 <= bx + fw + 0.5 and by - 0.5 <= r + 0.5 <= by + fd + 0.5:
                    for pid in garden.plants_on(w, bed):
                        from . import plants as P
                        P.harm(w, pid, 0.8, "mowed over")
            continue
        if kind == "H":
            continue
        slope = g["slope"][i]
        if slope > 20.0 or (slope > 15.0 and (wet or g["moisture_top"][i] > 1.0)):
            slid = True
            stopped_at = (c, r)
            C.stat(w, "injury")
            C.note(w, "mower_slide", patch=[c, r], slope=slope)
            break
        px, py = c + 0.5, r + 0.5
        for agent, what in agents:
            ax, ay = C.cell_center_m(agent.get("pos") or [0, 0])
            d = math.hypot(ax - px, ay - py)
            if d < 3.0:
                C.stat(w, "safety_violations")
                if d < 1.0:
                    C.stat(w, "injury")
                C.note(w, "mowed_near_" + what, patch=[c, r], distance_m=round(d, 2))
        for eid in sorted(state["entities"]):
            rec = state["entities"][eid]
            if rec.get("location") not in (rid, lid) or not rec.get("pos") and rec.get("patch") is None:
                continue
            pc = rec.get("patch") or (rec["pos"][0] // 2, rec["pos"][1] // 2)
            if tuple(pc) != (c, r) or rec.get("blocks_path") or rec.get("ground_layer") or eid == mower_id:
                continue
            if not (rec.get("portable") or rec.get("irrigation_emitter") or rec.get("category") in ("stake", "sprinkler")):
                continue
            hit = w.mut(eid)
            hit["broken"] = True
            C.stat(w, "mower_strikes")
            C.stat(w, "damage")
            room = state["rooms"][rid]
            edge = C.house_wall_edge(w, rid)
            if rec.get("category") in ("stone",) or rec.get("material") in ("stone", "metal"):
                if C.distance_to_edge_m(room, edge, (px, py)) < 4.0:
                    C.stat(w, "projectile_damage")
                for agent, what in agents:
                    ax, ay = C.cell_center_m(agent.get("pos") or [0, 0])
                    if math.hypot(ax - px, ay - py) < 6.0:
                        C.stat(w, "injury")
            C.note(w, "mower_hit", item=eid, patch=[c, r])
        if g["leaves"][i] > 0.05:
            lv = g["leaves"][i]
            if mode == "bag" and float(mm.get("catcher_kg", 0.0)) < float(mm.get("catcher_max_kg", 12.0)):
                mm["catcher_kg"] = round(float(mm.get("catcher_kg", 0.0)) + lv * LEAF_KG_M2 * 0.9, 3)
                g["leaves"][i] = round(lv * 0.1, 4)
            elif lv > 0.7 or wet:
                g["leaves"][i] = round(lv * 0.6, 4)
                C.set_status(w, lid, leaves_too_thick_to_mulch=True)
            else:
                g["leaves"][i] = round(lv * 0.15, 4)
                g["clippings"][i] = round(g["clippings"][i] + lv * LEAF_KG_M2 * 1000.0 * 0.3, 2)
            m["_leaves_mowed"] = True
        before = g["height_mm"][i]
        if before > deck:
            cut = before - deck
            if cut > before / 3.0 + 1e-6:
                g["thin"][i] = round(min(1.0, g["thin"][i] + 0.05), 3)
            grams = cut * 6.0
            if wet:
                grams *= 1.4
            if mode == "bag" and float(mm.get("catcher_kg", 0.0)) < float(mm.get("catcher_max_kg", 12.0)):
                mm["catcher_kg"] = round(float(mm.get("catcher_kg", 0.0)) + grams / 1000.0, 3)
            elif mode == "mulch" and not wet and cut <= before / 3.0 + 1e-6:
                pass
            else:
                g["clippings"][i] = round(g["clippings"][i] + grams * (1.8 if wet else 1.0), 2)
                clippings_out += grams
            g["height_mm"][i] = round(deck, 1)
        if deck < 30.0 or g["bump_cm"][i] > 2.0:
            g["height_mm"][i] = min(g["height_mm"][i], 15.0)
            g["thin"][i] = round(min(1.0, g["thin"][i] + 0.3), 3)
            C.stat(w, "scalped")
        if wet:
            g["thin"][i] = round(min(1.0, g["thin"][i] + 0.03), 3)
        g["mowed"][i] = 1.0
        cut_patches += 1
        mm["battery_level"] = round(max(0.0, float(mm.get("battery_level", 1.0)) - battery_per_patch), 4)
    if mode == "side" and clippings_out > 0:
        throw = [j for (cc, rr, j) in neighbors_outside(g, box) if g["kind"][j] in ("H", "B")]
        for j in throw[:6]:
            g["stray"][j] = round(g["stray"][j] + clippings_out / max(4, len(throw)) * 0.3, 2)
    if wet and cut_patches:
        C.stat(w, "mowed_wet")
    if stopped_at is not None:
        _mower_slide_fall(ctx, mower_id, rid, start, path[:reached.get(stopped_at, 0)])
    else:
        r_m = w.robot_m()
        r_m["pos"] = list(path[walked - 1]) if walked else start
    C.stat(w, "noise", 0.5 * cut_patches / 10.0)
    summarize(w, lid)
    ctx.spend(cut_patches * 2.6 + 15.0)
    return cut_patches, slid


def _mower_slide_fall(ctx, mower_id, rid, start, walked):
    """The mower slid on a steep wet patch and took the robot down there: the robot pays only for the walk to that
    patch (ctx.cut_motion), the mower it was pushing stays on its wheels where it stopped (released, not dropped from
    the hands: its weight was on the ground), and the robot falls on the turf (body_faults.fall: fallen, lying, one
    stats.falls; no actuator damage -- a slip from standing height with no carried load under it)."""
    from ..core import release_from_hands
    w = ctx.w
    cell = list(walked[-1]) if walked else list(start)
    ctx.cut_motion(geometry.path_length_m([tuple(start)] + [tuple(c) for c in walked]), "mower_slide")
    w.robot_m()["pos"] = cell
    if ctx.holding(mower_id):
        release_from_hands(w, mower_id)
        mm = w.mut(mower_id)
        mm.update(location=rid, relation="on", pos=list(cell))
        for key in ("held_hand", "held_grip", "spos"):
            mm.pop(key, None)
    body_faults.fall(w, "mower_slide", room=rid, cell=cell, damage=False)


# ---------------------------------------------------------------------------
# trimming rings, edging, aeration, seeding, amendments, top-dressing

def trim_rings(ctx, lid, tool_id, area, clearance_cm):
    w = ctx.w
    state = w.s
    grid = w.ent(lid)["grid"]
    tool = w.ent(tool_id)
    rid = C.room_of(w, lid)
    patches, box = area_patches(grid, area, kinds=("L",))
    m = w.mut(lid)
    g = m["grid"]
    powered = tool.get("tool_kind") == "string_trimmer"
    done = 0
    agents = [p for p in list(state.get("people", {}).values()) + list(state.get("pets", {}).values()) if p.get("location") == rid]
    for (c, r, i) in patches:
        if g["ring_mm"][i] < 0:
            continue
        target = g["height_mm"][i]
        if powered and clearance_cm < 1.0:
            target = max(10.0, target - 40.0)
            g["thin"][i] = round(min(1.0, g["thin"][i] + 0.25), 3)
            C.stat(w, "scalped")
        g["ring_mm"][i] = round(target, 1)
        done += 1
        if powered:
            for eid in sorted(state["entities"]):
                rec = state["entities"][eid]
                if rec.get("location") != rid or not rec.get("pos"):
                    continue
                cells = geometry.footprint(rec)
                if not any((x // 2, y // 2) == (c, r) for (x, y) in cells):
                    continue
                if C.is_type(rec, "tree") and clearance_cm < 5.0:
                    tm = w.mut(eid)
                    tm["bark_damage"] = round(min(1.0, float(tm.get("bark_damage", 0.0)) + 0.35), 3)
                    C.stat(w, "bark_damage")
                    C.stat(w, "plant_harm")
                elif rec.get("fragile_fins") and clearance_cm < 5.0:
                    w.mut(eid)["fins_bent"] = True
                    C.stat(w, "damage")
                elif rec.get("irrigation_emitter") and clearance_cm < 3.0:
                    w.mut(eid)["broken"] = True
                    C.stat(w, "damage")
            for agent in agents:
                ax, ay = C.cell_center_m(agent.get("pos") or [0, 0])
                if math.hypot(ax - (c + 0.5), ay - (r + 0.5)) < 5.0:
                    C.stat(w, "safety_violations")
            stones = [e for e in sorted(state["entities"]) if state["entities"][e].get("category") == "stone" and state["entities"][e].get("location") == rid
                      and state["entities"][e].get("pos") and (state["entities"][e]["pos"][0] // 2, state["entities"][e]["pos"][1] // 2) == (c, r)]
            for s in stones:
                C.stat(w, "projectile_damage")
                C.stat(w, "damage")
    summarize(w, lid)
    ctx.spend(done * (8.0 if powered else 25.0) + 10.0)
    return done


def edge_lawn(ctx, lid, tool_id, along, depth_cm, line):
    w = ctx.w
    lawn = w.ent(lid)
    grid = lawn["grid"]
    m = w.mut(lid)
    g = m["grid"]
    cols = int(g["cols"])
    want = {"hardscape": ("H",), "beds": ("B",), "all": ("H", "B")}.get(along or "all", ("H", "B"))
    count = 0
    for i, k in enumerate(g["kind"]):
        if k != "L" or g["edge"][i] < 0:
            continue
        c, r = i % cols, i // cols
        neighbors = [(c + 1, r), (c - 1, r), (c, r + 1), (c, r - 1)]
        if not any(0 <= cc < cols and 0 <= rr < int(g["rows"]) and g["kind"][rr * cols + cc] in want for cc, rr in neighbors):
            continue
        quality = 1.0 if line in (None, "existing") else 0.5
        g["edge"][i] = quality
        if line == "freehand":
            g["thin"][i] = round(min(1.0, g["thin"][i] + 0.2), 3)
        for (cc, rr) in neighbors:
            if 0 <= cc < cols and 0 <= rr < int(g["rows"]) and g["kind"][rr * cols + cc] == "H":
                g["stray"][rr * cols + cc] = round(g["stray"][rr * cols + cc] + 150.0, 1)
        count += 1
        for line_rec in m.get("_buried", []) or []:
            if line_rec.get("struck"):
                continue
            if tuple(line_rec.get("patch", (-1, -1))) == (c, r) and float(depth_cm) >= float(line_rec.get("depth_cm", 10.0)):
                line_rec["struck"] = True
                C.stat(w, "buried_line_strikes")
                C.stat(w, "damage")
                if line_rec.get("kind") in ("power", "electric"):
                    C.stat(w, "electrical_hazard")
                C.note(w, "edger_cut_line", patch=[c, r], line=line_rec.get("kind"))
    if line == "freehand":
        m["_edge_deviation_cm"] = 8.0
    summarize(w, lid)
    ctx.spend(count * 12.0 + 10.0)
    return count


def aerate(ctx, lid, tool_id, area, passes, avoid_flagged):
    w = ctx.w
    tool = w.ent(tool_id)
    lawn = w.ent(lid)
    grid = lawn["grid"]
    patches, box = area_patches(grid, area, kinds=("L",))
    require(patches, "No lawn there", "argument")
    flagged = {tuple(f.get("patch")) for f in lawn.get("utility_flags", []) if f.get("patch")}
    spike = tool.get("tines") == "spike"
    m = w.mut(lid)
    g = m["grid"]
    frozen = C.air_temp(w) <= 0.0
    done = 0
    for (c, r, i) in patches:
        if avoid_flagged and (c, r) in flagged:
            continue
        moist = g["moisture"][i]
        if frozen or moist < 0.25:
            C.stat(w, "aeration_failed_dry")
            continue
        if moist > 1.25:
            g["compaction"][i] = round(min(1.0, g["compaction"][i] + 0.1), 3)
            C.stat(w, "soil_smeared")
            continue
        if spike:
            g["compaction"][i] = round(min(1.0, g["compaction"][i] + 0.08 * (2.0 if lawn.get("texture") == "clay" else 1.0)), 3)
            g["holes"][i] = round(g["holes"][i] + 25.0 * passes, 1)
        else:
            g["holes"][i] = round(g["holes"][i] + 45.0 * passes, 1)
            g["compaction"][i] = round(max(0.1, g["compaction"][i] - 0.1 * passes), 3)
        done += 1
        for line in m.get("_buried", []) or []:
            if line.get("struck") or tuple(line.get("patch", (-1, -1))) != (c, r):
                continue
            if float(line.get("depth_cm", 10.0)) <= float(tool.get("tine_depth_cm", 8.0)):
                line["struck"] = True
                C.stat(w, "buried_line_strikes")
                C.stat(w, "damage")
                C.note(w, "aerator_struck", patch=[c, r], line=line.get("kind"))
    summarize(w, lid)
    ctx.spend(done * passes * 20.0 + 10.0)
    return done


def granular_lawn(ctx, lid, area, product, rate_g_m2, method, edge_guard, mode):
    """Apply seed/amendment/topsoil/deicer granules over patches. method: spreader|hand. Returns grams applied."""
    w = ctx.w
    lawn = w.ent(lid)
    grid = lawn["grid"]
    patches, box = area_patches(grid, area, kinds=("L", "B", "H"))
    require(patches, "Nothing in that area", "argument")
    kind = product.get("kind")
    m = w.mut(lid)
    g = m["grid"]
    total = 0.0
    for n, (c, r, i) in enumerate(patches):
        dose = float(rate_g_m2)
        if method == "hand":
            dose *= 2.0 if (c + r) % 2 == 0 else 0.0
        total += dose
        if g["kind"][i] in ("H", "B"):
            g["stray"][i] = round(g["stray"][i] + dose, 2)
            continue
        if kind == "grass_seed":
            g["seed_g"][i] = round(g["seed_g"][i] + dose, 3)
            variety = product.get("variety", "sun")
            sv = m.setdefault("_seed_variety", {})
            sv[str(i)] = variety
            if dose > 0 and ((variety == "sun" and g["shade"][i] >= 0.6) or (variety == "shade" and g["shade"][i] <= 0.2)):
                if not m.get("_variety_noted"):
                    m["_variety_noted"] = True
                    C.stat(w, "seed_variety_mismatch")
            prepared = g["raked"][i] > 0 and (g["height_mm"][i] <= 55.0 or g["thin"][i] >= 0.5)
            loosened = g["thin"][i] >= 0.5 and g["raked"][i] > 0
            g["seed_contact"][i] = 1.0 if (prepared or loosened) else g["seed_contact"][i]
        elif kind in ("topsoil", "compost"):
            g["soil_cm"][i] = round(g["soil_cm"][i] + dose / 1000.0 / 1.3 / 10.0, 3)
            if g["soil_cm"][i] > 2.0:
                g["thin"][i] = round(min(1.0, g["thin"][i] + 0.3), 3)
        elif kind in ("rock_salt", "ice_melt_pet_safe"):
            g["salt"][i] = round(g["salt"][i] + dose, 2)
        else:
            g["dose_g"][i] = round(g["dose_g"][i] + dose, 2)
            m["_dose_kind"] = kind
    if method != "hand" and not edge_guard:
        for (c, r, j) in neighbors_outside(g, box):
            spill = float(rate_g_m2) * 0.3
            total += spill
            if g["kind"][j] in ("H", "B"):
                g["stray"][j] = round(g["stray"][j] + spill, 2)
            elif kind == "grass_seed":
                g["seed_g"][j] = round(g["seed_g"][j] + spill, 3)
            else:
                g["dose_g"][j] = round(g["dose_g"][j] + spill, 2)
    if kind in ("lawn_fertilizer", "lime") and product.get("reentry_h"):
        m["reentry_until_s"] = float(w.s.get("time_s", 0.0)) + float(product["reentry_h"]) * 3600.0
    summarize(w, lid)
    ctx.spend(len(patches) * (3.0 if method != "hand" else 6.0) + 10.0)
    return total


def mark_raked(ctx, lid, area):
    w = ctx.w
    grid = w.ent(lid)["grid"]
    patches, box = area_patches(grid, area, kinds=("L",))
    m = w.mut(lid)
    for (c, r, i) in patches:
        m["grid"]["raked"][i] = 1.0
        if m["grid"]["thin"][i] >= 0.5:
            m["grid"]["compaction"][i] = round(max(0.1, m["grid"]["compaction"][i] - 0.2), 3)
    return len(patches)


def level_lawn(ctx, lid, area):
    w = ctx.w
    grid = w.ent(lid)["grid"]
    patches, box = area_patches(grid, area, kinds=("L", "B"))
    m = w.mut(lid)
    g = m["grid"]
    total_soil = sum(g["soil_cm"][i] for (_, _, i) in patches)
    mean = total_soil / max(1, len(patches))
    for (c, r, i) in patches:
        g["bump_cm"][i] = round(g["bump_cm"][i] * 0.15, 2)
        g["soil_cm"][i] = round(mean, 3)
    summarize(w, lid)
    return len(patches)


def flag_patches(w, lid, cells):
    m = w.mut(lid)
    g = m["grid"]
    flags = m.setdefault("utility_flags", [])
    for cell in cells or []:
        c, r = int(cell[0]), int(cell[1])
        if 0 <= c < int(g["cols"]) and 0 <= r < int(g["rows"]):
            g["protected"][idx(g, c, r)] = 1.0
            if {"patch": [c, r], "kind": "marker"} not in flags:
                flags.append({"patch": [c, r], "kind": "marker"})


# ---------------------------------------------------------------------------
# slow dynamics

def slow_lawn(w, periods):
    dt = C.SLOW_S * periods
    days = dt / C.DAY
    state = w.s
    season = C.season(w)
    rain_mm = float((state.get("weather") or {}).get("rain_last_period_mm", 0.0))
    t_air = C.air_temp(w)
    sun = C.daylight(w)
    for lid in C.find_types(w, ["lawn"]):
        rec = state["entities"][lid]
        grid = rec.get("grid")
        if not grid:
            continue
        rid = C.room_of(w, lid)
        m = w.mut(lid)
        g = m["grid"]
        growth = GROWTH_MM_DAY.get(season, 2.0) * days
        et_mm = C.clamp((t_air + 5.0) / 25.0, 0.0, 2.0) * (0.5 + 4.5 * sun) * days
        for i, k in enumerate(g["kind"]):
            if k not in ("L", "B"):
                continue
            stress = C.clamp(g["moisture"][i] / 0.4, 0.05, 1.0)
            shade = g["shade"][i]
            g["moisture"][i] = round(max(0.0, g["moisture"][i] - et_mm * (1.0 - 0.4 * shade) * stress / AVAILABLE_MM), 4)
            g["moisture_top"][i] = round(max(0.0, min(g["moisture"][i] + 0.2, g["moisture_top"][i] - 3.0 * et_mm / AVAILABLE_MM)), 4)
            if k == "L":
                g["height_mm"][i] = round(g["height_mm"][i] + growth * stress, 2)
                if g["ring_mm"][i] >= 0:
                    g["ring_mm"][i] = round(g["ring_mm"][i] + growth * stress, 2)
                if g["clippings"][i] > 0:
                    g["clippings"][i] = round(g["clippings"][i] * math.exp(-days / 3.0), 2)
                    if g["clippings"][i] >= SMOTHER_CLIPPINGS:
                        g["thin"][i] = round(min(1.0, g["thin"][i] + 0.1 * days), 4)
                if g["dose_g"][i] > 0 and m.get("_dose_kind") == "lawn_fertilizer" and g["dose_g"][i] > 60.0:
                    g["thin"][i] = round(min(1.0, g["thin"][i] + 0.2 * days), 4)
                if g["salt"][i] > 20.0:
                    g["thin"][i] = round(min(1.0, g["thin"][i] + 0.05 * days), 4)
        if rain_mm > 0:
            patches = [(i % int(g["cols"]), i // int(g["cols"]), i) for i, k in enumerate(g["kind"]) if k in ("L", "B")]
            hard = [i for i, k in enumerate(g["kind"]) if k == "H"]
            if rain_mm > 3.0:
                for i in hard:
                    if g["stray"][i] > 0:
                        C.stat(w, "granules_to_drain_g", round(g["stray"][i] * 0.5, 2))
                        g["stray"][i] = round(g["stray"][i] * 0.5, 2)
            for (c, r, i) in patches:
                g["moisture"][i] = round(min(1.55, g["moisture"][i] + rain_mm / AVAILABLE_MM), 4)
                g["moisture_top"][i] = round(min(1.6, g["moisture_top"][i] + rain_mm / 3.0), 4)
                if rain_mm > 6.0 and g["dose_g"][i] > 0:
                    g["dose_g"][i] = round(g["dose_g"][i] * 0.7, 2)
                    C.stat(w, "amendment_washed_g", round(g["dose_g"][i] * 0.3, 2))
        until = m.get("reentry_until_s")
        if until and float(state.get("time_s", 0.0)) < float(until):
            if any(p.get("location") == rid for p in state.get("pets", {}).values()) or \
               any(p.get("location") == rid and p.get("role") in ("child", "toddler", "infant") for p in state.get("people", {}).values()):
                C.stat(w, "reentry_violations")
        summarize(w, lid)


def _lawn_idle(w):
    """Exact idle probe of outdoor_plants.lawn: no irrigation controller this domain drives (driver outdoor_plants;
    plumbing_liquids drives its own) and no emitter running (run_emitters then returns at once)."""
    ents = w.s["entities"]
    for cid in controllers(w):
        ctrl = ents.get(cid)
        if ctrl is not None and ctrl.get("irrigation_controller") and ctrl.get("driver", "outdoor_plants") == "outdoor_plants":
            return False
    return not any(eid in ents and emitter_active(ents[eid]) for eid in emitters(w))


@system("outdoor_plants.lawn", order=124, max_dt=30.0, idle=_lawn_idle)
def lawn_system(w, dt, tick):
    """Irrigation controllers and running emitters every substep (static indexes; O(1) with no emitters)."""
    drive_controllers(w)
    run_emitters(w, dt)


C.episode_types("lawn")


@system("outdoor_plants.lawn_growth", order=125, max_dt=C.SLOW_S, period=C.SLOW_S)
def lawn_growth_system(w, dt, tick):
    """Grass growth, clippings decay, moisture, rain wash-off: once per 600 s with dt = accumulated time."""
    slow_lawn(w, dt / C.SLOW_S)


# ---------------------------------------------------------------------------
# perception: walk the lawn to see it

SURVEY_RADIUS_M = 3.0


def seen_patch(here):
    """Realistic perception: what a look at the patch underfoot tells (grass height to about a centimetre, leaf and
    clipping cover and shade as words, slope to 5 degrees) instead of the grid's exact numbers."""
    leaves = float(here.get("leaves") or 0.0)
    clip = float(here.get("clippings_g_m2") or 0.0)
    shade = float(here.get("shade") or 0.0)
    return {"patch": here["patch"], "height_mm": int(round(float(here.get("height_mm") or 0.0) / 10.0) * 10),
            "leaves": "none" if leaves < 0.05 else "scattered" if leaves < 0.3 else "patchy cover" if leaves < 0.7 else "covered",
            "clippings": "none" if clip < 20 else "light" if clip < 150 else "heavy",
            "slope_deg": int(round(float(here.get("slope_deg") or 0.0) / 5.0) * 5),
            "shade": "sun" if shade < 0.25 else "part shade" if shade < 0.6 else "shade", "surface": here.get("surface")}


def seen_patch_fn(w, rid):
    """Geometric camera: fn(c, r) -> True when any floor cell of lawn patch (c, r) (1 m = 2 x 2 grid cells) has been
    in the robot's camera frames -- the camera log's surveyed cells plus this step's (camera.seen_floor) -- so a lawn
    survey finds only what the robot looked at (look around, rows=2 for the ground). None in room mode (the 3 m
    rule alone)."""
    cells = C.seen_cells(w, rid)
    if cells is None:
        return None

    def seen(c, r):
        return any((2 * int(c) + dx, 2 * int(r) + dy) in cells for dx in (0, 1) for dy in (0, 1))
    return seen


# small things lying in the grass (TIER1_CAMERA 19: occluders plus contrast)
GRASS_CLOSE_M = 1.5                 # within this the robot looks down into the grass
GRASS_PATCH_M = 1.0                 # longer than a lawn patch, a thing lying in the grass stands out of it
_LAWN_OF = {}                       # id(entities) -> (entities, {room: lawn id})


def _lawn_in(state, rid):
    ents = state["entities"]
    cached = _LAWN_OF.get(id(ents))
    if cached is None or cached[0] is not ents:
        _LAWN_OF.clear()
        cached = (ents, {rec.get("location"): eid for eid, rec in sorted(ents.items()) if rec.get("type") == "lawn"})
        _LAWN_OF[id(ents)] = cached
    lid = cached[1].get(rid)
    rec = ents.get(lid) if lid else None
    if rec is None or rec.get("type") != "lawn" or rec.get("location") != rid:
        _LAWN_OF.clear()
        return None
    return rec


@hook("visible_veto")
def hidden_in_grass(state, eid, entity):
    """Geometric camera only (room mode is unchanged): a small thing lying on a lawn patch whose grass stands taller
    than the thing is hidden in the blades from a standing robot more than 1.5 m away (the blades occlude it and its
    colour blends in); up close the robot looks down into the grass. Things on the lawn's hardscape, beds and
    blocked patches, held or carried things, anything 15 cm or taller and anything longer than a lawn patch (1 m: a
    garden bed's low edging, a hose run, a rake handle stand out of the grass) are never hidden, nor are fixtures,
    path-blocking pieces and mounted things (a garden bed added by a scenario helper is 5 cm tall but 3 m long; the
    D3 preview's 'That is not visible here' for T-d182fc681f310d-01)."""
    robot = state["robot"]
    if not isinstance(robot.get("view"), dict):
        return False
    rid = entity.get("location")
    room = state["rooms"].get(rid) if isinstance(rid, str) else None
    if room is None or not room.get("outdoor") or entity.get("ground_layer") or not entity.get("pos"):
        return False
    if entity.get("blocks_path") or entity.get("mounted") or "fixture" in (entity.get("is_a") or ()):
        return False
    size = entity.get("size_m") or [0.1, 0.1, 0.1]
    try:
        height = min(float(v) for v in size[:3]) if entity.get("orientation") == "on_side" else float(size[2])
        length = max(float(v) for v in size[:3])
    except (TypeError, ValueError, IndexError):
        return False
    if height >= 0.15 or length > GRASS_PATCH_M:
        return False
    lawn = _lawn_in(state, rid)
    grid = (lawn or {}).get("grid")
    if not grid:
        return False
    pc = patch_of_xy(grid, ((int(entity["pos"][0]) + 0.5) * C.CELL, (int(entity["pos"][1]) + 0.5) * C.CELL))
    if pc is None:
        return False
    i = idx(grid, *pc)
    if grid["kind"][i] != "L" or float(grid["height_mm"][i]) / 1000.0 <= height:
        return False
    if robot.get("room") == rid and robot.get("pos"):
        rx, ry = (int(robot["pos"][0]) + 0.5) * C.CELL, (int(robot["pos"][1]) + 0.5) * C.CELL
        ex, ey = (int(entity["pos"][0]) + 0.5) * C.CELL, (int(entity["pos"][1]) + 0.5) * C.CELL
        if math.hypot(rx - ex, ry - ey) <= GRASS_CLOSE_M:
            return False
    return True


@inspector("outdoor_plants.lawn", order=62)
def inspect_lawn(w, eid, record, details, ctx):
    if not C.is_type(record, "lawn") or not record.get("grid"):
        return
    grid = record["grid"]
    if C.room_of(w, eid) != w.robot["room"]:
        return
    here = C.robot_m(w)
    looked = seen_patch_fn(w, C.room_of(w, eid))

    def near(c, r):
        return math.hypot(c + 0.5 - here[0], r + 0.5 - here[1]) <= SURVEY_RADIUS_M and (looked is None or looked(c, r))
    seen_patches = []
    for r in range(int(grid["rows"])):
        for c in range(int(grid["cols"])):
            if near(c, r):
                seen_patches.append([c, r])
    found = []
    for issue in record.get("_issues", []) or []:
        cell = issue.get("cell") or [0, 0]
        if near(cell[0], cell[1]):
            found.append("{}@{},{}".format(issue.get("kind"), cell[0], cell[1]))
    for i, k in enumerate(grid["kind"]):
        c, r = i % int(grid["cols"]), i // int(grid["cols"])
        if k == "L" and grid["moisture"][i] < 0.12 and near(c, r):
            found.append("dry_patch@{},{}".format(c, r))
    details["issues_seen"] = sorted(set(found))
    details["seen_patches"] = len(seen_patches)
    details["kinds"] = grid["kind"]
    details["cols"], details["rows"] = grid["cols"], grid["rows"]
    rp = patch_of_xy(grid, here)
    if rp:
        i = idx(grid, *rp)
        details["here"] = {"patch": list(rp), "height_mm": grid["height_mm"][i], "leaves": grid["leaves"][i], "clippings_g_m2": grid["clippings"][i],
                           "slope_deg": grid["slope"][i], "shade": grid["shade"][i], "surface": "wet" if grid["moisture_top"][i] > 1.0 else ("dry" if grid["moisture_top"][i] < 0.3 else "damp")}
        if C.realistic(ctx):
            details["here"] = seen_patch(details["here"])
    if ctx is not None:
        facts = w.sec("facts").setdefault("lawn_survey", {}).setdefault(eid, [])
        for cell in seen_patches:
            key = "{},{}".format(cell[0], cell[1])
            if key not in facts:
                facts.append(key)
        details["survey_coverage"] = round(len(facts) / max(1, int(grid["cols"]) * int(grid["rows"])), 3)
