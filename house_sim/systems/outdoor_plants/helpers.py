"""Scenario helpers (setup op {"op": "helper", "name": "outdoor_plants.<name>", "args": {...}}).

Every helper builds state on the generated house config with the Placer and returns a dict of
bindings usable in predicates and plans (ids, expected report strings, amounts). Helpers never
decide success; they only lay out the world and the hidden truth.

  houseplants      potted plants with chosen species/moisture/placement; needs_water / skip lists
  garden_bed       a bed with plants, weeds, volunteers, produce, buried lines and a planting plan
  lawn             (re)build a yard lawn with height, moisture, leaves, issues, slopes, buried lines
  weather_plan     current weather + forecast + matching scheduled weather events
  patio_set        chairs/table/cushions/umbrella/deck box/covers on a terrace or yard
  grill            a stand-in grill (appliances own the real type) with temperature and gas valve
  fire_pit         fire pit with ash, buried-ember temperature and debris
  firewood         loose logs (some rotten/infested) and a rack/pallet
  compost          a compost bin/tumbler/worm bin in a chosen state, optional nest
  surface          a paved surface state (leaves, snow, ice, material, young concrete)
  fence_defects    hidden fence-panel defects (+ expected report strings)
  roof_damage      roof faces with hidden damage (+ expected / unseen faces)
  pool_state       pool debris, toys floating/sunk, swimmers, season, cover
  yard_debris      branches/litter/stones/downed line/hanging limb in a yard
  cut_flowers      a vase and cut flower stems
  arrangement_status (function) aesthetic/stability summary for a vase of cut flowers
"""
import copy
import math

from ...kernel import geometry
from ...kernel.errors import SpecError
from ...kernel.plugins import scenario_helper
from . import common as C
from . import furnish as F
from . import lawn as L
from .species import SPECIES, profile, theta_for


def _room(ctx, value, outdoor=None):
    rooms = ctx.config["rooms"]
    if value in rooms:
        return value
    options = [r for r in sorted(rooms) if rooms[r]["type"] == value]
    if not options:
        raise SpecError("No room of type {} in this house".format(value))
    return options[0]


def _surface_in(ctx, rid, avoid=()):
    p = ctx.placer
    for kind in ("side_table", "coffee_table", "table", "desk", "dresser", "nightstand", "counter", "outdoor_table", "shelf_unit"):
        for eid in p.find(kind, rid):
            if eid not in avoid and ctx.config["entities"][eid].get("surface"):
                return eid
    return None


def _rwc_value(ctx, value):
    if isinstance(value, (int, float)):
        return float(value)
    ranges = {"dry": (0.02, 0.18), "moist": (0.75, 0.95), "wet": (1.02, 1.1), "medium": (0.4, 0.6)}
    lo, hi = ranges.get(value or "medium", (0.4, 0.6))
    return round(ctx.rng.uniform(lo, hi), 3)


# ---------------------------------------------------------------------------
# plants in pots

@scenario_helper("outdoor_plants.houseplants")
def houseplants(ctx, room="living_room", plants=None, clear_existing=True, window=True):
    rid = _room(ctx, room)
    p = ctx.placer
    cfg = ctx.config
    if clear_existing:
        for eid in sorted(cfg["entities"]):
            rec = cfg["entities"].get(eid)
            if rec and rec.get("type") in ("houseplant", "herb_plant") and C.room_of_cfg(cfg, eid) == rid:
                _remove_tree(cfg, _pot_root(cfg, eid))
    out = {"room": rid, "plants": [], "pots": [], "saucers": [], "needs_water": [], "skip": [], "artificial": []}
    windows = [e for e in sorted(cfg["entities"]) if cfg["entities"][e].get("category") == "window" and cfg["entities"][e].get("location") == rid]
    near = cfg["entities"][windows[0]]["pos"] if windows and window else None
    surfaces_used = []
    for i, spec in enumerate(plants or [{"species": "pothos"}]):
        spec = dict(spec)
        species = spec.get("species")
        if isinstance(species, list):
            species = ctx.rng.choice(species)
        rwc_v = _rwc_value(ctx, spec.get("rwc", "medium"))
        where = spec.get("on", "floor")
        extra = dict(spec.get("props", {}))
        if spec.get("flowers"):
            extra["flowers"] = dict(spec["flowers"])
        res = None
        if where == "floor":
            res = F.add_potted_plant(p, rid, species, ctx.rng, floor_room=rid, near=spec.get("near", near), size=spec.get("size", "medium"), rwc_value=rwc_v,
                                     pot_d=spec.get("pot_d"), saucer=spec.get("saucer", True), material=spec.get("material"), **extra)
        else:
            surface = where if where in cfg["entities"] else _surface_in(ctx, rid)
            if surface is None:
                surface = p.furniture(rid, "side_table", wall=True)
            res = F.add_potted_plant(p, rid, species, ctx.rng, on=surface, size=spec.get("size", "small"), rwc_value=rwc_v, pot_d=spec.get("pot_d"),
                                     saucer=spec.get("saucer", True), material=spec.get("material"), **extra)
            if res is None:
                extra_table = p.furniture(rid, "side_table", wall=False)
                if extra_table:
                    res = F.add_potted_plant(p, rid, species, ctx.rng, on=extra_table, size="small", rwc_value=rwc_v, pot_d=spec.get("pot_d"),
                                             saucer=spec.get("saucer", True), material=spec.get("material"), **extra)
            if res is None:
                # no surface and no space for a side table: a small pot on the floor (reachable, out of door swings)
                res = F.add_potted_plant(p, rid, species, ctx.rng, floor_room=rid, near=near, size="small", rwc_value=rwc_v, pot_d=spec.get("pot_d"),
                                         saucer=spec.get("saucer", True), material=spec.get("material"), **extra)
        if res is None:
            raise SpecError("houseplants: no room for plant {}".format(i))
        pot = cfg["entities"][res["pot"]]
        if spec.get("drainage") is False:
            pot["drainage_hole"] = False
        if spec.get("saucer_water_l") and res["saucer"]:
            cfg["entities"][res["saucer"]]["liquid_l"] = float(spec["saucer_water_l"])
        if spec.get("pebble_tray") and res["saucer"]:
            s = cfg["entities"][res["saucer"]]
            s.update(pebbles=True, pebble_top_l=0.3, label="pebble humidity tray", capacity_l=0.6)
        plant = cfg["entities"][res["plant"]]
        for key in ("pests", "diseased", "variegated", "root_bound", "leggy_stems", "role", "owner_wants", "nest", "health", "root_rot", "dormant", "flower_buds"):
            if key in spec:
                plant[key] = copy.deepcopy(spec[key])
        if spec.get("dead_leaves") is not None or spec.get("yellow_leaves") is not None:
            plant["leaves"]["dead"] = int(spec.get("dead_leaves", 0))
            plant["leaves"]["yellowing"] = int(spec.get("yellow_leaves", 0))
            if plant["leaves"]["dead"]:
                plant.setdefault("symptoms", []).append("dead brown leaves")
        if spec.get("lean_deg"):
            plant["lean_deg"] = float(spec["lean_deg"])
            plant.setdefault("symptoms", []).append("leaning toward the light")
        if spec.get("fallen_leaves"):
            pot["fallen_leaves"] = int(spec["fallen_leaves"])
        if spec.get("dust"):
            plant.setdefault("soil", {})["dust"] = float(spec["dust"])
        n = len(out["plants"]) + 1
        out["plants"].append(res["plant"])
        out["pots"].append(res["pot"])
        out["saucers"].append(res["saucer"])
        out["p{}".format(n)] = res["plant"]
        out["pot{}".format(n)] = res["pot"]
        out["saucer{}".format(n)] = res["saucer"]
        sp = plant["species"]
        if sp == "artificial" or plant.get("artificial"):
            out["artificial"].append(res["plant"])
            out["skip"].append(res["plant"])
            continue
        prof = profile(sp)
        if rwc_v < prof["water_at"]:
            out["needs_water"].append(res["plant"])
        else:
            out["skip"].append(res["plant"])
    return out


def _pot_root(cfg, plant_id):
    eid = plant_id
    while True:
        loc = cfg["entities"][eid].get("location")
        if loc in cfg["entities"] and cfg["entities"][loc].get("type") in ("plant_pot", "nursery_pot", "plant_saucer", "pebble_tray"):
            eid = loc
            continue
        return eid


def _remove_tree(cfg, root):
    doomed = [root]
    changed = True
    while changed:
        changed = False
        for eid, rec in list(cfg["entities"].items()):
            if rec.get("location") in doomed and eid not in doomed:
                doomed.append(eid)
                changed = True
    for eid in doomed:
        cfg["entities"].pop(eid, None)


def _room_of_cfg(cfg, eid):
    state = {"rooms": cfg["rooms"], "entities": cfg["entities"], "people": cfg.get("people", {}), "pets": cfg.get("pets", {}), "robot": {"room": cfg.get("start_room")}}
    from ...kernel.predicates import root_room
    return root_room(state, eid)


C.room_of_cfg = _room_of_cfg


# ---------------------------------------------------------------------------
# beds

@scenario_helper("outdoor_plants.garden_bed")
def garden_bed(ctx, room="backyard", bed_type="garden_bed", size=(3.0, 1.0), sun="full", rwc=0.5, compaction=0.3, plants=None, weeds=0, weed_species=None,
               buried=None, plan=None, flags=True, mulch_cm=0.0, label=None, near_house=False, sowings=None, props=None):
    rid = _room(ctx, room)
    p = ctx.placer
    cfg = ctx.config
    room_rec = cfg["rooms"][rid]
    near = None
    if near_house:
        edge = F.house_wall(cfg, rid)
        near = F._edge_anchor(room_rec, edge)
    bed = p.furniture(rid, bed_type, near=near, size_m=[float(size[0]), float(size[1]), 0.05 if bed_type == "garden_bed" else 0.3],
                      surface_size_m=[float(size[0]), float(size[1])], sun=sun, label=label or ("garden bed" if bed_type == "garden_bed" else "raised bed"))
    if bed is None:
        bed = p.furniture(rid, bed_type, wall=False, size_m=[float(size[0]), float(size[1]), 0.05], surface_size_m=[float(size[0]), float(size[1])], sun=sun)
    if bed is None:
        raise SpecError("garden_bed: no space in " + rid)
    rec = cfg["entities"][bed]
    kind = "garden_soil"
    rec["medium"] = {"kind": kind, "moisture": round(theta_for(_rwc_value(ctx, rwc), kind), 4), "moisture_top": round(theta_for(max(0.0, _rwc_value(ctx, rwc) - 0.1), kind), 4),
                     "compaction": float(compaction), "depth_cm": 30, "tilled_cm": 10, "texture": "loam", "organic": 0.04}
    for key, value in (props or {}).items():
        rec[key] = copy.deepcopy(value)
    out = {"bed": bed, "room": rid, "plants": [], "weeds": [], "crops": [], "wanted": [], "volunteers_keep": [], "volunteers_pull": [], "produce": [], "ripe": [], "unripe": []}
    sw, sd = float(size[0]), float(size[1])
    for i, spec in enumerate(plants or []):
        spec = dict(spec)
        species = spec["species"] if not isinstance(spec["species"], list) else ctx.rng.choice(spec["species"])
        role = spec.get("role")
        spos = spec.get("spos")
        extra = dict(spec.get("props", {}))
        if spec.get("flowers"):
            extra["flowers"] = dict(spec["flowers"])
        t = spec.get("type", "shrub" if profile(species).get("kind") == "shrub" and spec.get("size") == "large" else "garden_plant")
        eid = F.add_bed_plant(p, bed, species, ctx.rng, spos=spos, role=role, size=spec.get("size", "medium"), t=t, **extra)
        if eid is None:
            eid = F.add_bed_plant(p, bed, species, ctx.rng, spos=None, role=role, size="small", t=t, **extra)
        if eid is None:
            raise SpecError("garden_bed: no space for plant {}".format(species))
        prec = cfg["entities"][eid]
        for key in ("owner_wants", "seeding", "diseased", "nest", "pests", "emerging", "ambiguous", "bloomed", "health", "established_rhizome"):
            if key in spec:
                prec[key] = copy.deepcopy(spec[key])
        n = len(out["plants"]) + 1
        out["plants"].append(eid)
        out["plant{}".format(n)] = eid
        r = prec.get("role")
        if r == "weed":
            out["weeds"].append(eid)
        elif r == "volunteer":
            (out["volunteers_keep"] if prec.get("owner_wants") == "keep" else out["volunteers_pull"]).append(eid)
            if prec.get("owner_wants") == "keep":
                out["wanted"].append(eid)
        else:
            out["wanted"].append(eid)
            if r == "crop":
                out["crops"].append(eid)
        for j, prod in enumerate(spec.get("produce", [])):
            pr = dict(prod)
            ripe = float(pr.pop("ripeness", 1.0))
            crop = pr.pop("crop", species)
            props = {"label": pr.pop("label", crop.replace("_", " ")), "category": "produce", "produce": {"crop": crop}, "ripeness": ripe,
                     "food": {"kind": crop, "state": "raw", "quantity": 1, "unit": "count"}, "secret_fields": ["ripeness"],
                     "size_m": pr.pop("size_m", [0.07, 0.07, 0.07]), "mass_kg": pr.pop("mass_kg", 0.15), "volume_l": 0.3, "soft": pr.pop("soft", crop in ("tomato", "strawberry", "raspberry", "fig"))}
            props.update(pr)
            prid = p.make("generic_item", eid, **props)
            cfg["entities"][prid]["relation"] = "attached"
            out["produce"].append(prid)
            (out["ripe"] if ripe >= 0.85 or props.get("mature_green") else out["unripe"]).append(prid)
    for k in range(int(weeds)):
        sp = ctx.rng.choice(weed_species or ["dandelion", "crabgrass", "plantain_weed"])
        eid = F.add_bed_plant(p, bed, sp, ctx.rng, role="weed")
        if eid is None:
            continue
        out["weeds"].append(eid)
        out["plants"].append(eid)
    lines = []
    for line in buried or []:
        line = dict(line)
        line.setdefault("axis", "y")
        lines.append(line)
    rec["_buried"] = lines
    rec["utility_flags"] = [{"kind": l.get("kind"), "axis": l.get("axis"), "at_m": l.get("at_m")} for l in lines if l.get("flagged", flags)]
    if plan:
        rec["plan"] = [{"spos": list(s), "for": "plant"} for s in plan]
        out["plan"] = rec["plan"]
    if mulch_cm:
        cols, rows = int(round(sw / 0.25)), int(round(sd / 0.25))
        rec["mulch"] = {"cell_m": 0.25, "cols": cols, "rows": rows, "depth_cm": [float(mulch_cm)] * (cols * rows), "kind": "mulch"}
    for s in sowings or []:
        rec.setdefault("sowings", []).append(dict(s))
    out["n_weeds"] = len(out["weeds"])
    out["side_cell"] = _side_cell(ctx, rid, rec)
    return out


def _side_cell(ctx, rid, rec):
    """A reachable free cell beside an entity's footprint (outside doorway clearance when possible)."""
    state = ctx.state()
    room_rec = ctx.config["rooms"][rid]
    blocked = set(geometry.blocked_cells(state, rid))
    reach = geometry.reachable_cells(state, rid, set(geometry.blocked_cells(state, rid, include_agents=False)))
    cells = geometry.footprint(rec)
    ring = sorted(c for c in geometry.near_cells(cells, room_rec, radius=1) if c not in cells and c not in blocked and c in reach)
    clear = geometry.door_clearance(state, rid)
    side = [c for c in ring if c not in clear] or ring
    return list(side[len(side) // 2]) if side else None


# ---------------------------------------------------------------------------
# lawn

@scenario_helper("outdoor_plants.lawn")
def lawn(ctx, room="backyard", height_mm=None, moisture=None, leaves=None, leaf_cells=None, clippings=None, issues=None, slopes=None, buried=None, flags=None,
         thin=None, compaction=None, seed=None, target=None, rebuild=True, weeds=None, shade=None, hidden=None):
    rid = _room(ctx, room)
    cfg = ctx.config
    lid = next((e for e in sorted(cfg["entities"]) if cfg["entities"][e].get("type") == "lawn" and cfg["entities"][e].get("location") == rid), None)
    if lid is None or rebuild:
        if lid:
            cfg["entities"].pop(lid, None)
        lid = F.make_lawn(cfg, ctx.reg, rid, ctx.rng, eid="lawn_" + rid, height_mm=height_mm, moisture=moisture)
    rec = cfg["entities"][lid]
    g = rec["grid"]
    n = int(g["cols"]) * int(g["rows"])
    lawn_idx = [i for i in range(n) if g["kind"][i] == "L"]
    out = {"lawn": lid, "room": rid, "cols": g["cols"], "rows": g["rows"], "lawn_cells": len(lawn_idx)}
    if height_mm is not None:
        for i in lawn_idx:
            g["height_mm"][i] = float(height_mm) + round(ctx.rng.uniform(-5.0, 5.0), 1)
            if g["ring_mm"][i] >= 0:
                g["ring_mm"][i] = g["height_mm"][i]
    if leaves is not None:
        for i in range(n):
            if g["kind"][i] in ("L",):
                g["leaves"][i] = round(min(1.0, max(0.0, float(leaves) + ctx.rng.uniform(-0.15, 0.15))), 3)
    if clippings is not None:
        for i in lawn_idx:
            if ctx.rng.random() < 0.4:
                g["clippings"][i] = float(clippings)
    for key, value in (("thin", thin), ("compaction", compaction)):
        if value is not None:
            for i in lawn_idx:
                g[key][i] = float(value)
    if shade:
        for cell in shade:
            g["shade"][L.idx(g, cell[0], cell[1])] = 0.7
    for cell in slopes or []:
        g["slope"][L.idx(g, cell[0], cell[1])] = float(cell[2]) if len(cell) > 2 else 22.0
    issue_list = []
    for issue in (issues if isinstance(issues, (list, tuple)) else []):
        if isinstance(issue, dict):
            issue_list.append(dict(issue))
    if isinstance(issues, int) and not isinstance(issues, bool) and lawn_idx:
        kinds = ["mushroom", "mole_hill", "grub_damage", "bare_patch", "weeds", "standing_water", "hole"]
        for k in range(issues):
            i = ctx.rng.choice(lawn_idx)
            issue_list.append({"kind": ctx.rng.choice(kinds), "cell": [i % int(g["cols"]), i // int(g["cols"])]})
    rec["_issues"] = issue_list
    out["expected_issues"] = sorted(set("{}@{},{}".format(it["kind"], it["cell"][0], it["cell"][1]) for it in issue_list))
    for it in issue_list:
        i = L.idx(g, it["cell"][0], it["cell"][1])
        if it["kind"] == "bare_patch":
            g["thin"][i] = 0.8
        if it["kind"] == "weeds":
            g["weeds"][i] = 4.0
        if it["kind"] == "standing_water":
            g["moisture"][i] = 1.5
    rec["_buried"] = [dict(b) for b in (buried or [])]
    rec["utility_flags"] = [{"patch": list(b["patch"]), "kind": b.get("kind")} for b in (buried or []) if b.get("flagged")]
    if seed:
        if not lawn_idx and not seed.get("cells"):
            raise SpecError("lawn: no lawn patches in " + rid)
        cells = seed.get("cells") or [[i % int(g["cols"]), i // int(g["cols"])] for i in lawn_idx[: int(seed.get("count", 6))]]
        for c in cells:
            i = L.idx(g, c[0], c[1])
            if "thin" in seed:
                g["thin"][i] = float(seed["thin"])
        out["seed_cells"] = cells
    if weeds:
        for c in weeds:
            g["weeds"][L.idx(g, c[0], c[1])] = 3.0
    if target:
        t = dict(target)
        for key in ("seed_cells", "dose_cells", "aerate_cells", "leaf_cells", "soil_cells"):
            v = t.get(key)
            if v == "all":
                t[key] = list(lawn_idx)
            elif isinstance(v, list) and v and isinstance(v[0], (list, tuple)):
                t[key] = [L.idx(g, int(c[0]), int(c[1])) for c in v]
        rec["_target"] = t
        out["target"] = t
    for h in hidden or []:
        h = dict(h)
        hid = ctx.placer.make(h.pop("type", "generic_item"), lid, **h.get("props", {}))
        cfg["entities"][hid]["relation"] = "under"
        cfg["entities"][hid]["patch"] = list(h.get("patch", [0, 0]))
        out.setdefault("hidden", []).append(hid)
    out["leaf_patches"] = [[i % int(g["cols"]), i // int(g["cols"])] for i in lawn_idx if g["leaves"][i] > 0.1]
    out["full_area"] = [0, 0, int(g["cols"]) - 1, int(g["rows"]) - 1]
    return out


# ---------------------------------------------------------------------------
# weather

@scenario_helper("outdoor_plants.weather_plan")
def weather_plan(ctx, now=None, events=None, forecast=None, rules=None):
    cfg = ctx.config
    wx = cfg.setdefault("weather", {})
    wx.update(copy.deepcopy(now or {}))
    if isinstance(wx.get("process"), dict):
        # a scripted weather (and forecast) on a generation-2 house: the default weather process yields to it for
        # the whole episode (TIER1_SWEEP 13.2, weather.override_until_s)
        from .weather import SCRIPTED_S
        wx["override_until_s"] = SCRIPTED_S
    if "wind_mps" in (now or {}) and "gust_mps" not in (now or {}):
        wx["gust_mps"] = round(float(wx["wind_mps"]) * 1.4, 2)
    if wx.get("condition") in ("rain", "storm") and "rain_mm_h" not in (now or {}):
        wx["rain_mm_h"] = 4.0
    fc = list(wx.get("forecast", []))
    out = {}
    for i, ev in enumerate(events or []):
        ev = dict(ev)
        at = float(ev.pop("in_h", 1.0)) * 3600.0
        hours = float(ev.pop("hours", 2.0))
        after = ev.pop("after", None)
        fc_item = {"t_s": at, "hours": hours, "condition": ev.get("condition", "rain"), "rain_mm": round(float(ev.get("rain_mm_h", 0.0)) * hours, 1),
                   "wind_mps": ev.get("wind_mps", wx.get("wind_mps", 2.0)), "gust_mps": ev.get("gust_mps", ev.get("wind_mps", 2.0) * 1.5 if isinstance(ev.get("wind_mps"), (int, float)) else 3.0),
                   "low_c": ev.get("temperature_c", wx.get("temperature_c", 15.0)), "high_c": ev.get("temperature_c", wx.get("temperature_c", 15.0))}
        if ev.get("hidden_forecast"):
            ev.pop("hidden_forecast")
        else:
            fc.append(fc_item)
        cfg.setdefault("events", []).append({"at_s": at, "effect": "weather", "args": ev})
        restore = after or {"condition": "cloudy", "rain_mm_h": 0.0, "snow_cm_h": 0.0, "wind_mps": wx.get("wind_mps", 2.0), "gust_mps": wx.get("gust_mps", 3.0)}
        cfg["events"].append({"at_s": at + hours * 3600.0, "effect": "weather", "args": restore})
        out["event{}_s".format(i + 1)] = at
        out["event{}_end_s".format(i + 1)] = at + hours * 3600.0
    for item in forecast or []:
        item = dict(item)
        item["t_s"] = float(item.pop("in_h", 0.0)) * 3600.0
        fc.append(item)
    wx["forecast"] = sorted(fc, key=lambda it: float(it.get("t_s", 0)))
    if rules:
        cfg.setdefault("household", {}).update(copy.deepcopy(rules))
    return out


# ---------------------------------------------------------------------------
# patio, grill, fire pit, firewood

@scenario_helper("outdoor_plants.patio_set")
def patio_set(ctx, room=None, chairs=2, cushions="out", wet_chairs=False, damp_cushions=0, umbrella=None, deck_box=None, covers=None, table=True, cushion_fits=None):
    cfg = ctx.config
    rooms = cfg["rooms"]
    if room is None:
        options = [r for r in sorted(rooms) if rooms[r]["type"] in ("patio", "deck")] or [r for r in sorted(rooms) if rooms[r]["type"] == "backyard"]
        if not options:
            raise SpecError("patio_set: no patio/deck/backyard")
        rid = options[0]
    else:
        rid = _room(ctx, room)
    p = ctx.placer
    out = {"room": rid, "chairs": [], "cushions": [], "covers": []}
    tables = p.find("outdoor_table", rid)
    tbl = tables[0] if tables else (p.furniture(rid, "outdoor_table", wall=False) if table else None)
    out["table"] = tbl
    have = p.find("outdoor_chair", rid)
    for i in range(int(chairs)):
        ch = have[i] if i < len(have) else p.furniture(rid, "outdoor_chair", wall=False, near=cfg["entities"][tbl]["pos"] if tbl else None)
        if ch is None:
            continue
        cfg["entities"][ch]["seat_kind"] = "chair"
        if wet_chairs:
            cfg["entities"][ch]["wetness"] = 0.8
        out["chairs"].append(ch)
    for eid in sorted(cfg["entities"]):
        rec = cfg["entities"][eid]
        if rec.get("type") == "outdoor_cushion" and _room_of_cfg(cfg, eid) == rid:
            cfg["entities"].pop(eid)
    box = None
    if deck_box is not None:
        box = p.furniture(rid, "deck_box", wall=True, **(deck_box or {}))
        out["deck_box"] = box
    for i, ch in enumerate(out["chairs"]):
        fits = (cushion_fits or {}).get(str(i), "chair_seat")
        damp = i < int(damp_cushions)
        props = {"label": "seat cushion", "fits": fits, "wetness": 0.6 if damp else 0.0}
        if cushions == "out":
            cid = p.on(ch, "outdoor_cushion", **props)
        elif cushions == "box" and box:
            cid = p.inside(box, "outdoor_cushion", **props)
        else:
            cid = None
        if cid:
            out["cushions"].append(cid)
            out["cushion{}".format(len(out["cushions"]))] = cid
    if umbrella is not None and tbl:
        u = dict(umbrella)
        base = p.make("umbrella_base", tbl, label="umbrella base", ballast_kg=float(u.get("ballast_kg", 20.0)))
        cfg["entities"][base]["relation"] = "under"
        cfg["entities"][base]["revealed"] = True
        uid = p.make("patio_umbrella", base, label="patio umbrella", open=bool(u.get("open", False)), strapped=bool(u.get("strapped", not u.get("open", False))),
                     settings={"tilt_deg": int(u.get("tilt_deg", 0)), "lock": "engaged"})
        out["umbrella"] = uid
        out["umbrella_base"] = base
    for cv in covers or []:
        cv = dict(cv)
        on = cv.pop("on", None)
        target = {"table": tbl, "chair": out["chairs"][0] if out["chairs"] else None}.get(on, on)
        type_name = cv.pop("type", "furniture_cover")
        if target:
            cid = p.on(target, type_name, **cv)
            if cid:
                crec = cfg["entities"][cid]
                crec["covers"] = target
                crec["deployed"] = True
                crec["fit"] = True
                cfg["entities"][target]["covered_by"] = cid
        else:
            cid = F.floor_place(p, rid, type_name, near=cfg["entities"][tbl]["pos"] if tbl and cfg["entities"][tbl].get("pos") else None, **cv)
        if cid:
            out["covers"].append(cid)
    return out


@scenario_helper("outdoor_plants.grill")
def grill(ctx, room=None, temperature_c=21.0, gas_open=False, lit=False, kind="gas", hung_tools=0, covered=False, cover_water_l=0.0, nest=None, propane_tank=False, near_house=False):
    """Stand-in grill built from core generic_item (appliances_cooking owns the real grill type).

    Without `room` it goes on the first patio/deck with space, else the backyard (a furnished deck may
    already hold the real grill and a table set). The cover lies on the floor beside it (or on it when
    covered=True); an optional propane cylinder stands next to it."""
    cfg = ctx.config
    rooms = cfg["rooms"]
    if room:
        candidates = [_room(ctx, room)]
    else:
        candidates = [r for r in sorted(rooms) if rooms[r]["type"] in ("patio", "deck")] + [r for r in sorted(rooms) if rooms[r]["type"] == "backyard"]
        if not candidates:
            raise SpecError("grill: no patio/deck/backyard in this house (add house.require)")
    p = ctx.placer
    props = dict(label="{} grill".format(kind), category="grill", portable=False, blocks_path=True, accepts_items=True, surface=True,
                 container=False, size_m=[1.3, 0.6, 1.15], surface_size_m=[1.3, 0.6], mass_kg=45.0, material="stainless_steel", temperature_c=float(temperature_c),
                 thermal_tau_s=1900.0, burners_on=bool(lit), cover_class="grill", hang_slots=3, hang_height_m=0.9,
                 settings={"tank_valve": "open" if gas_open else "closed"}, setting_options={"tank_valve": ["open", "closed"]})
    gid, rid = None, None
    for rid in candidates:
        gid = p.furniture(rid, "generic_item", **copy.deepcopy(props)) or p.furniture(rid, "generic_item", wall=False, **copy.deepcopy(props))
        if gid is not None:
            break
    if gid is None:
        raise SpecError("grill: no space")
    out = {"grill": gid, "room": rid}
    for i in range(int(hung_tools)):
        t = p.hang(gid, "generic_item", label="grill spatula", category="utensil", mass_kg=0.2, size_m=[0.08, 0.02, 0.4])
        out.setdefault("tools", []).append(t)
    if covered:
        cover = p.make("grill_cover", gid, label="grill cover")
        cfg["entities"][cover].update(relation="on", spos=[0.0, 0.0], covers=gid, deployed=True, fit=True, fastened=True, pooled_water_l=float(cover_water_l), nest=nest)
        cfg["entities"][gid]["covered_by"] = cover
    else:
        cover = F.floor_place(p, rid, "grill_cover", label="grill cover", near=cfg["entities"][gid]["pos"])
        if cover is None:
            raise SpecError("grill: no space for the grill cover")
    out["cover"] = cover
    if propane_tank:
        tank = F.floor_place(p, rid, "generic_item", near=cfg["entities"][gid]["pos"], label="propane cylinder (20 lb)", category="propane_tank", mass_kg=17.0, two_handed=True, handles=["collar"],
                             size_m=[0.31, 0.31, 0.46], material="steel", orientation="upright", valve="closed", tags=["propane_tank"], z_m=0.4)
        if tank is None:
            raise SpecError("grill: no space for the propane tank")
        out["tank"] = tank
    return out


@scenario_helper("outdoor_plants.fire_pit")
def fire_pit(ctx, room="backyard", hours_since=48.0, ember_c=None, surface_c=None, ash_kg=3.0, debris=(), can=True, plastic_bin=True, tools=()):
    rid = _room(ctx, room)
    p = ctx.placer
    cfg = ctx.config
    pits = p.find("fire_pit", rid)
    pit = pits[0] if pits else p.furniture(rid, "fire_pit", wall=False)
    if pit is None:
        raise SpecError("fire_pit: no space")
    ambient = float(cfg.get("weather", {}).get("temperature_c", 15.0))
    if ember_c is None:
        ember_c = ambient + (600.0 - ambient) * math.exp(-float(hours_since) * 3600.0 / (14 * 3600.0))
    if surface_c is None:
        surface_c = ambient + (float(ember_c) - ambient) * 0.12
    rec = cfg["entities"][pit]
    rec.update(ash_kg=float(ash_kg), _ember_c=round(float(ember_c), 1), temperature_c=round(float(surface_c), 1), conducts_heat=False)
    out = {"pit": pit, "room": rid, "ember_c": round(float(ember_c), 1), "hot": float(ember_c) > 60.0}
    for d in debris:
        did = p.make("generic_item", pit, **dict(d))
        cfg["entities"][did]["relation"] = "in"
        out.setdefault("debris", []).append(did)
    if can:
        out["can"] = F.floor_place(p, rid, "metal_ash_can", near=rec["pos"])
        if out["can"] is None:
            raise SpecError("fire_pit: no space for the metal ash can")
    if plastic_bin:
        out["bin"] = F.floor_place(p, rid, "generic_item", label="plastic trash bin", category="trash_bin", material="plastic", accepts_items=True, container=True,
                                  capacity_l=120.0, waste_stream="trash", accepts_any_waste=True, size_m=[0.6, 0.7, 1.0], portable=False, near=rec["pos"])
    for tool in tools:
        tool = dict(tool)
        var = tool.pop("var")
        out[var] = F.floor_place(p, rid, tool.pop("type"), near=rec["pos"], **tool.get("props", {}))
        if out[var] is None:
            raise SpecError("fire_pit: no space for tool " + var)
    return out


@scenario_helper("outdoor_plants.firewood")
def firewood(ctx, room="backyard", logs=8, rotten=1, infested=0, rack=True, rack_near_house=False):
    rid = _room(ctx, room)
    p = ctx.placer
    cfg = ctx.config
    out = {"room": rid, "logs": [], "sound": [], "bad": []}
    for i in range(int(logs)):
        lid = F.floor_place(p, rid, "firewood_log", label="split firewood log")
        if lid is None:
            continue
        rec = cfg["entities"][lid]
        if i < int(rotten):
            rec["rotten"] = True
            out["bad"].append(lid)
        elif i < int(rotten) + int(infested):
            rec["infested"] = True
            out["bad"].append(lid)
        else:
            out["sound"].append(lid)
        out["logs"].append(lid)
    if rack:
        near = F._edge_anchor(cfg["rooms"][rid], F.house_wall(cfg, rid)) if rack_near_house else None
        racks = p.find("firewood_rack", rid)
        out["rack"] = racks[0] if racks else (p.furniture(rid, "firewood_rack", wall=not rack_near_house, near=near) or p.furniture(rid, "firewood_rack", wall=False))
    if not out["logs"]:
        raise SpecError("firewood: no space for logs in " + rid)
    return out


# ---------------------------------------------------------------------------
# compost, surfaces

@scenario_helper("outdoor_plants.compost")
def compost(ctx, room="backyard", kind="hot", state=None, nest=None, label=None, days_since_turned=6.0, open_=False):
    rid = _room(ctx, room)
    p = ctx.placer
    cfg = ctx.config
    type_name = {"tumbler": "compost_tumbler", "vermi": "worm_bin"}.get(kind, "compost_bin")
    bins = [b for b in p.find(type_name, rid)]
    bid = bins[0] if bins else p.furniture(rid, type_name, wall=True, label=label or type_name.replace("_", " "))
    if bid is None:
        raise SpecError("compost: no space")
    rec = cfg["entities"][bid]
    comp = dict(rec.get("compost") or {})
    comp.update({"kind": kind, "last_turned_s": -float(days_since_turned) * 86400.0})
    comp.update(copy.deepcopy(state or {}))
    rec["compost"] = comp
    rec["nest"] = nest
    rec["open"] = bool(open_)
    return {"bin": bid, "room": rid}


@scenario_helper("outdoor_plants.surface")
def surface(ctx, room="front_yard", which="walkway", leaves=None, wet=None, snow_cm=None, density=None, ice=None, material=None, cured_days=None, crack_weeds=None):
    rid = _room(ctx, room)
    cfg = ctx.config
    sid = None
    if which == "floor":
        sid = "floor_" + rid
    else:
        # which: a walkway, the steps (a generation-2 house's porch steps are a `steps` surface) or any of them
        kinds = {"steps": ("steps",), "any": ("walkway", "steps")}.get(which, ("walkway",))
        sid = next((e for e in sorted(cfg["entities"]) if cfg["entities"][e].get("type") in kinds and cfg["entities"][e].get("location") == rid), None)
        if sid is None:
            cells = F.walkway_cells(cfg, rid) or _fallback_path(ctx, rid)
            rec = ctx.reg.make("walkway" if which != "steps" else "steps", rid, cells=cells, area_m2=round(len(cells) * 0.5, 2), label="front walkway" if which != "steps" else "front steps")
            rec["pos"] = list(cells[0])
            sid = ctx.placer.new_id(which if which in ("walkway", "steps") else "walkway")
            cfg["entities"][sid] = rec
    rec = cfg["entities"][sid]
    if leaves is not None:
        rec.setdefault("soil", {})["leaves"] = float(leaves)
    if wet is not None:
        rec["wetness"] = float(wet)
    if snow_cm is not None:
        rec["snow_cm"] = float(snow_cm)
    if density is not None:
        rec["snow_density"] = float(density)
    if ice is not None:
        rec["ice"] = float(ice)
    if material is not None:
        rec["material"] = material
    if cured_days is not None:
        rec["cured_days"] = float(cured_days)
    if crack_weeds is not None:
        rec["crack_weeds"] = int(crack_weeds)
    rec.setdefault("deicer", {})
    return {"surface": sid, "room": rid, "area_m2": rec.get("area_m2") or (cfg["rooms"][rid]["size_m"][0] * cfg["rooms"][rid]["size_m"][1])}


# ---------------------------------------------------------------------------
# inspection targets

@scenario_helper("outdoor_plants.fence_defects")
def fence_defects(ctx, room="backyard", count=3, kinds=None, weathered=2, gap_cm=18.0, growth=None):
    rid = _room(ctx, room)
    cfg = ctx.config
    fence = next((e for e in sorted(cfg["entities"]) if cfg["entities"][e].get("type") == "fence" and cfg["entities"][e].get("location") == rid), None)
    if fence is None:
        edge = F.house_wall(cfg, rid)
        rec = ctx.reg.make("fence", rid, panels=F.fence_panels(cfg, rid, edge), label="backyard fence")
        rec["pos"] = [0, 0]
        rec["ground_layer"] = True
        fence = ctx.placer.new_id("fence")
        cfg["entities"][fence] = rec
    rec = cfg["entities"][fence]
    panels = [pn["id"] for pn in rec["panels"]]
    choices = list(kinds or ["broken_board", "missing_board", "leaning_post", "gap_under", "loose_rail", "protruding_nail"])
    picked = ctx.rng.sample(panels, min(int(count), len(panels)))
    defects = {}
    expected = []
    dog = max([{"small": 10.0, "medium": 15.0, "large": 22.0}.get(pt.get("size", "medium"), 15.0) for pt in cfg.get("pets", {}).values() if pt.get("species") == "dog"] or [0.0])
    for pid in sorted(picked):
        kind = ctx.rng.choice(choices)
        gap = float(gap_cm) if kind in ("gap_under", "missing_board", "broken_board") else 0.0
        defects[pid] = [{"kind": kind, "gap_cm": gap}]
        expected.append("{}:{}".format(pid, kind))
        if dog and gap >= dog * 0.8:
            expected.append("{}:{}:urgent".format(pid, kind))
    rec["_fence_defects"] = defects
    for pn in rec["panels"]:
        if pn["id"] not in picked and weathered > 0:
            pn["appearance"] = "faded, weathered but sound boards"
            weathered -= 1
    out = {"fence": fence, "room": rid, "expected_defects": sorted(expected), "defect_panels": sorted(picked), "panels": panels,
           "growth": [], "wanted_growth": [], "unwanted_growth": []}
    plist = rec["panels"]
    for n, gspec in enumerate(growth or []):
        gspec = dict(gspec)
        panel = plist[int(gspec.get("panel", n)) % len(plist)]
        gid = "g{}".format(n + 1)
        item = {"id": gid, "kind": gspec.get("kind", "bindweed"), "wanted": bool(gspec.get("wanted", False)),
                "stem_cm": float(gspec.get("stem_cm", 0.5)), "cells": [list(c) for c in (panel.get("cells") or [])], "panel": panel["id"], "cut": False}
        rec.setdefault("growth", []).append(item)
        out["growth"].append(gid)
        (out["wanted_growth"] if item["wanted"] else out["unwanted_growth"]).append(gid)
    return out


@scenario_helper("outdoor_plants.roof_damage")
def roof_damage(ctx, faces=None):
    cfg = ctx.config
    out = {"faces": [], "expected": [], "unseen": []}
    for spec in faces or [{"face": "front", "room": "front_yard", "sightline": "clear", "damage": ["missing_shingles"]}]:
        rid = _room(ctx, spec.get("room", "front_yard"))
        edge = F.house_wall(cfg, rid)
        cells = C.edge_cells(cfg["rooms"][rid], edge)
        rec = ctx.reg.make("roof_face", rid, face=spec["face"], sightline=spec.get("sightline", spec.get("view", "clear")), label="{} roof".format(spec["face"]), mount_height_m=5.5,
                           size_m=[0.5, 0.1, 3.0], width_m=round(len(cells) * 0.5, 1), _roof_damage=[{"kind": d, "severity": "major"} for d in spec.get("damage", [])], moss=bool(spec.get("moss")))
        rec["pos"] = list(cells[len(cells) // 2]) if cells else [0, 0]
        rec["mounted"] = True
        eid = ctx.placer.new_id("roof_face")
        cfg["entities"][eid] = rec
        out["faces"].append(eid)
        if spec.get("sightline", spec.get("view", "clear")) == "blocked":
            out["unseen"].append(spec["face"])
        else:
            out["expected"] += ["{}:{}".format(spec["face"], d) for d in spec.get("damage", [])]
    out["expected"] = sorted(out["expected"])
    return out


@scenario_helper("outdoor_plants.storm_damage")
def storm_damage(ctx, items=None):
    cfg = ctx.config
    out = {"damaged": [], "expected": []}
    for spec in items or []:
        spec = dict(spec)
        eid = spec.get("id")
        if eid is None:
            rid = _room(ctx, spec.get("room", "backyard"))
            eid = F.floor_place(ctx.placer, rid, spec.get("type", "generic_item"), **spec.get("props", {}))
            if eid is None:
                raise SpecError("storm_damage: no space in " + rid)
        elif eid not in cfg["entities"]:
            raise SpecError("storm_damage: unknown entity " + str(eid))
        cfg["entities"][eid]["storm_damage"] = spec.get("damage", "damaged")
        cfg["entities"][eid].setdefault("secret_fields", [])
        out["damaged"].append(eid)
        out["expected"].append("{}:{}".format(eid, spec.get("damage", "damaged")))
    out["expected"] = sorted(out["expected"])
    return out


def _fallback_path(ctx, rid):
    """Walkway cells when the room has no house-to-street path: a straight run of reachable ground cells
    from the first doorway into the room (never under blocking furniture)."""
    cfg = ctx.config
    state = ctx.state()
    room = cfg["rooms"][rid]
    blocked = set(geometry.blocked_cells(state, rid, include_agents=False))
    reach = geometry.reachable_cells(state, rid, blocked)
    doors = [tuple(c["cells"][rid]) for _, c in sorted(cfg.get("connections", {}).items()) if rid in c.get("cells", {})]
    cols, rows = geometry.grid_size(room)
    start = doors[0] if doors else next(iter(sorted(reach)), (0, 0))
    goal = (cols // 2, rows // 2)
    far = sorted(reach, key=lambda c: (-geometry.chebyshev(c, start), geometry.chebyshev(c, goal), c[1], c[0]))
    path = geometry.find_path(state, rid, start, far[:1]) if far else None
    cells = [list(start)] + [list(c) for c in (path or [])]
    if len(cells) < 2:
        cells = [list(c) for c in sorted(reach)[:4]] or [[0, 0]]
    return cells[:24]


def _power_line_cells(ctx, rid):
    """Two perimeter cells for a downed line whose hard no-go ring (cells within one cell) stays clear of every
    doorway's clearance and leaves access not worse (kernel geometry.access_report / not_worse: doorways stay
    connected, everything accessible stays accessible). Prefers places far from the doorways. None if the room has
    no such place."""
    cfg = ctx.config
    state = ctx.state()
    room = cfg["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    clear = geometry.door_clearance(state, rid)
    doors = geometry.doorway_cells(state, rid)
    blocked = set(geometry.blocked_cells(state, rid, include_agents=False))
    targets = geometry.access_targets(state, rid, level="all")
    before = geometry.access_report(state, rid, blocked, targets)
    perimeter = sorted({(x, 0) for x in range(cols)} | {(x, rows - 1) for x in range(cols)} | {(0, y) for y in range(rows)} | {(cols - 1, y) for y in range(rows)})
    options = []
    for c in perimeter:
        along = (1, 0) if c[1] in (0, rows - 1) else (0, 1)
        d = (c[0] + along[0], c[1] + along[1])
        if not geometry.in_grid(room, d) or c in blocked or d in blocked:
            continue
        ring = geometry.near_cells([c, d], room, 1)
        if ring & clear:
            continue
        dist = min([geometry.chebyshev(c, k) for k in doors] or [99])
        options.append((-dist, c[1], c[0], [list(c), list(d)], ring))
    options.sort(key=lambda o: o[:3])
    for _, _, _, cells, ring in options[:40]:
        if geometry.not_worse(before, geometry.access_report(state, rid, blocked | ring, targets)):
            return cells
    return None


# ---------------------------------------------------------------------------
# pool and debris

@scenario_helper("outdoor_plants.pool_state")
def pool_state(ctx, debris_kg=1.0, toys=None, swimmers=None, season="open", wet_coping=False, basket_load=0.5, frog=False, pump_on=True):
    cfg = ctx.config
    rid = _room(ctx, "poolside")
    pid = next((e for e in sorted(cfg["entities"]) if cfg["entities"][e].get("type") == "pool"), None)
    if pid is None:
        F._pool(ctx.placer, cfg, ctx.reg, rid, ctx.rng)
        pid = next((e for e in sorted(cfg["entities"]) if cfg["entities"][e].get("type") == "pool"), None)
    if pid is None:
        raise SpecError("pool_state: no pool")
    rec = cfg["entities"][pid]
    rec["debris_kg"] = float(debris_kg)
    rec["season"] = season
    out = {"pool": pid, "room": rid, "toys": [], "floating": [], "sunk": []}
    size = rec.get("surface_size_m") or rec["size_m"][:2]
    for i, t in enumerate(toys or []):
        t = dict(t)
        where = t.pop("where", "floating")
        type_name = t.pop("type", "pool_toy")
        spos = t.pop("spos", [round(ctx.rng.uniform(0.2, float(size[0]) - 1.6), 2), round(ctx.rng.uniform(0.2, float(size[1]) - 0.9), 2)])
        if where == "deck":
            tid = F.floor_place(ctx.placer, rid, type_name, **t)
        elif where == "sunk":
            tid = ctx.placer.make(type_name, pid, **t)
            cfg["entities"][tid]["relation"] = "in"
            out["sunk"].append(tid)
        else:
            tid = ctx.placer.on(pid, type_name, spos=spos, **t) or ctx.placer.on(pid, type_name, **t)
            out["floating"].append(tid)
        if tid:
            out["toys"].append(tid)
    for s in swimmers or []:
        person = cfg.setdefault("people", {}).setdefault(s, {"role": "child", "authority": 0, "consent": True, "awake": True})
        person.update(location=rid, in_pool=True, activity="swimming")
        person["pos"] = ctx.placer.agent_cell(rid, near=rec.get("pos"))
        if person["pos"] is None:
            raise SpecError("pool_state: no free cell for swimmer {} in {}".format(s, rid))
    if wet_coping:
        cells = geometry.footprint(rec)
        band = sorted({(x + dx, y + dy) for (x, y) in cells for dx in (-1, 0, 1) for dy in (-1, 0, 1)} - set(cells))
        cfg["rooms"][rid]["wet_cells"] = [list(c) for c in band]
    for eid in sorted(cfg["entities"]):
        r = cfg["entities"][eid]
        if r.get("type") == "skimmer_basket":
            r["load_kg"] = float(basket_load)
            if frog:
                fid = ctx.placer.make("generic_item", eid, label="frog", animal="frog", alive=True, mass_kg=0.03, size_m=[0.06, 0.05, 0.03])
                cfg["entities"][fid]["relation"] = "in"
                out["frog"] = fid
            out["basket"] = eid
        if r.get("type") == "pool_pump":
            r["on"] = bool(pump_on)
            out["pump"] = eid
        if r.get("type") == "skimmer":
            out["skimmer"] = eid
    return out


@scenario_helper("outdoor_plants.yard_debris")
def yard_debris(ctx, room="backyard", branches=0, big_limbs=0, litter=0, stones=0, power_line=False, line_touching=0, hanging=False, sharps=0, valuables=()):
    """Debris to collect plus optional hazards (downed live line, broken limb hanging in a tree).

    The hazards are laid out first: the line on the perimeter far from the doorways (its hard no-go ring never
    cuts the yard off), the hanging limb in an existing or new tree. Collectable debris then avoids the line's
    danger radius and the 2 m zone under the limb when the yard has room (else only the hard ring), so a
    careful reference plan can collect it without a safety violation."""
    rid = _room(ctx, room)
    p = ctx.placer
    cfg = ctx.config
    room_rec = cfg["rooms"][rid]
    cols, rows = geometry.grid_size(room_rec)
    out = {"room": rid, "branches": [], "limbs": [], "litter": [], "stones": [], "sharps": [], "valuables": [], "touching": []}
    line = None
    soft, hard = set(), set()
    if power_line:
        line_cells = _power_line_cells(ctx, rid)
        if line_cells is None:
            raise SpecError("yard_debris: no place for a downed line in {} that keeps the yard passable".format(rid))
        line = ctx.reg.make("power_line", rid, downed=True, live=True, label="downed power line", cells=line_cells)
        line["pos"] = list(line_cells[0])
        lid = p.new_id("power_line")
        cfg["entities"][lid] = line
        out["line"] = lid
        radius = float(line.get("danger_radius_m", 8.0))
        hard |= geometry.near_cells([tuple(c) for c in line_cells], room_rec, 1)
        for x in range(cols):
            for y in range(rows):
                if min(math.hypot((x - c[0]) * C.CELL, (y - c[1]) * C.CELL) for c in line_cells) < radius:
                    soft.add((x, y))
    if hanging:
        trees = p.find("tree", rid)
        tree = trees[0] if trees else p.furniture(rid, "tree", wall=False, species="maple")
        if tree:
            h = p.make("branch", tree, label="broken branch hanging in the tree", hanging=True, length_m=2.5, diameter_cm=8.0, mass_kg=12.0, z_m=4.0)
            cfg["entities"][h]["relation"] = "attached"
            out["hanger"] = h
            trec = cfg["entities"][tree]
            fc, fr = geometry.footprint_size(trec)
            hx, hy = (int(trec["pos"][0]) + fc / 2.0) * C.CELL, (int(trec["pos"][1]) + fr / 2.0) * C.CELL
            zone = {(x, y) for x in range(cols) for y in range(rows) if math.hypot(x * C.CELL + 0.25 - hx, y * C.CELL + 0.25 - hy) < 2.0}
            soft |= zone
            hard |= zone

    def place(type_name, **props):
        return F.floor_place(p, rid, type_name, avoid=soft, **props) or F.floor_place(p, rid, type_name, avoid=hard, **props)

    for i in range(int(branches)):
        length = round(ctx.rng.uniform(0.6, 2.2), 2)
        diam = round(ctx.rng.uniform(1.0, 3.5), 1)
        b = place("branch", length_m=length, diameter_cm=diam, mass_kg=round(0.6 * length * (diam / 2.0) ** 2, 2), size_m=[length, 0.08, 0.08])
        if b:
            out["branches"].append(b)
    for i in range(int(big_limbs)):
        b = place("branch", label="large fallen limb", length_m=4.0, diameter_cm=15.0, mass_kg=45.0, size_m=[4.0, 0.3, 0.3])
        if b:
            out["limbs"].append(b)
    for i in range(int(litter)):
        kind = ctx.rng.choice([("food wrapper", "plastic", "trash"), ("aluminum can", "aluminum", "recycling"), ("plastic bottle", "plastic", "recycling"), ("flyer", "paper", "recycling")])
        l = place("litter_item", label=kind[0], material=kind[1], waste_stream=kind[2], pile_contaminant=True)
        if l:
            out["litter"].append(l)
    for i in range(int(stones)):
        s = place("stone", pile_contaminant=True)
        if s:
            out["stones"].append(s)
    for i in range(int(sharps)):
        s = place("litter_item", label="broken glass", material="glass", hazards=["sharp"], handled_with=["cut_resistant_gloves", "garden_gloves"], waste_stream="trash")
        if s:
            out["sharps"].append(s)
    for v in valuables:
        v = dict(v)
        vid = place(v.pop("type", "generic_item"), **v)
        if vid:
            out["valuables"].append(vid)
    if line is not None:
        for i in range(int(line_touching)):
            b = F.floor_place(p, rid, "branch", near=line["pos"], label="fallen branch", length_m=1.5, diameter_cm=3.0, size_m=[1.5, 0.08, 0.08])
            if b:
                out["touching"].append(b)
        line["touching"] = list(out["touching"])
    wanted = int(branches) + int(big_limbs) + int(litter) + int(stones) + int(sharps) + len(valuables)
    got = sum(len(out[k]) for k in ("branches", "limbs", "litter", "stones", "sharps", "valuables"))
    if wanted and not got:
        raise SpecError("yard_debris: none of the {} items fit in {}".format(wanted, rid))
    return out


@scenario_helper("outdoor_plants.cut_flowers")
def cut_flowers(ctx, room="dining_room", count=6, colors=None, vase_height_m=0.25, on=None):
    rid = _room(ctx, room) if room in ctx.config["rooms"] or any(r["type"] == room for r in ctx.config["rooms"].values()) else _room(ctx, "kitchen")
    p = ctx.placer
    cfg = ctx.config
    surface = on or _surface_in(ctx, rid)
    if surface is None:
        surface = p.furniture(rid, "table", wall=False) or p.furniture(rid, "side_table", wall=True)
    if surface is None or surface not in cfg["entities"]:
        raise SpecError("cut_flowers: no table or counter for the vase in " + rid)
    vase = p.on(surface, "generic_item", label="glass vase", category="vase", material="glass", fragile=True, accepts_items=True, container=True, accepts_in=True,
                capacity_l=1.5, inner_size_m=[0.1, 0.1, float(vase_height_m)], size_m=[0.12, 0.12, float(vase_height_m)], mass_kg=0.8, liquid_container=True, liquid="water",
                liquid_l=0.8, vase_height_m=float(vase_height_m))
    if vase is None:
        raise SpecError("cut_flowers: no room for the vase on " + surface)
    out = {"vase": vase, "surface": surface, "stems": []}
    palette = colors or ["red", "white", "yellow", "red", "white", "yellow", "pink", "purple"]
    for i in range(int(count)):
        sid = p.on(surface, "cut_flower", color=palette[i % len(palette)], stem_cm=round(ctx.rng.uniform(55, 70), 1), leaves_low=3)
        if sid:
            out["stems"].append(sid)
    return out


def arrangement_status(w, vase_id):
    if not vase_id or vase_id not in w.s["entities"]:
        return
    vase = w.s["entities"][vase_id]
    if vase.get("category") != "vase":
        return
    stems = [c for c in w.children(vase_id) if C.is_type(w.s["entities"][c], "cut_flower")]
    if not stems:
        return
    vh = float(vase.get("vase_height_m", 0.25)) * 100.0
    heights = [float(w.s["entities"][s].get("stem_cm", 60.0)) for s in stems]
    tallest = max(heights)
    colors = [w.s["entities"][s].get("color") for s in stems]
    counts = {}
    for c in colors:
        counts[c] = counts.get(c, 0) + 1
    balance = 1.0 - (max(counts.values()) - min(counts.values())) / max(1.0, len(stems))
    graded = len(set(round(h / 5.0) for h in heights)) >= min(3, len(stems))
    leaves_under = sum(int(w.s["entities"][s].get("leaves_low", 0)) for s in stems)
    proportion_ok = 1.4 * vh <= tallest <= 2.1 * vh
    top_heavy = tallest > 2.6 * vh and len(stems) >= 5
    values = {"stems": len(stems), "tallest_cm": tallest, "proportion_ok": proportion_ok, "graded": graded, "color_balance": round(balance, 3),
              "leaves_below_waterline": leaves_under, "all_angled": all(w.s["entities"][s].get("stem_cut") == "angled" for s in stems), "top_heavy": top_heavy}
    if top_heavy and not vase.get("broken"):
        m = w.mut(vase_id)
        m["broken"] = True
        C.stat(w, "breakages")
        C.stat(w, "damage")
        values["tipped"] = True
    C.set_status(w, vase_id, **values)


from ...kernel.plugins import after as _after


@_after("place", order=159)
def _after_place_vase(ctx, result):
    dest = ctx.args.get("destination")
    if dest and dest in ctx.w.s["entities"] and ctx.w.s["entities"][dest].get("category") == "vase":
        arrangement_status(ctx.w, dest)
