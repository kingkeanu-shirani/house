"""Water on floors and flooding (fidelity G for puddle geometry and visibility, P-approx for volume/depth/evaporation,
S for policy).

Puddles (type floor_puddle, category "puddle"): liquid (water/coffee/juice/oil), clarity clear|opaque, liquid_l, depth_mm
(~2 mm film on a hard floor; carpet soaks it into a wet patch), area = liquid_l / depth -> size_m and footprint cells.
  A leak source (`_source` entity, `_leak_lph` litres/hour) keeps adding water while the source still leaks (`leaking`
  not False and `_leak_lph` > 0); open water evaporates ~0.15 mm/h indoors and the puddle dries out.
  Visibility (the specular-puddle problem): unspotted puddles are hidden from observations (entity and grid hazard).
  The robot spots one when it is within the visibility distance during any step:
    opaque liquid 5 m; clear water on carpet/rug 2.5 m (dark wet patch); clear water on a glossy hard floor 1.25 m (only
    a glint at grazing angles); x0.6 in dim light (< 0.6), nothing in the dark (< 0.2) without a flashlight; a
    flashlight held low makes glints visible to 2.5 m; crouching x1.5. `inspect <floor> where=closely` while crouched
    spots every puddle within 3 cells. Spotted puddles get floor_hazard "wet" (they show in the grid as slip hazards).
  Inspect a puddle: liquid look/smell, area, depth, and where it seems to come from (a leaking source within 1.5 m).
  Truth for reports: _hazard {"kind": "water_on_floor", "aliases": [...], "source": <source kind>} -> report fields
  hazard / source (also accepts the source entity id or label).
  Absorbing: `place` an absorbent item (towel, rag, paper towels, absorbent pad: `absorbent`/`absorb_l`) on a puddle soaks
  up to its capacity (towel 0.6 l, paper towels 0.25 l); the item becomes wet. A puddle under 20 ml is resolved (damp).
  People walking through an unmarked puddle on a hard floor slip (injury; also counted in stats.falls). People of 5+ route around puddles marked by
  a warning sign/barrier within 2 cells, people of 8+ around puddles they can see (spotted).
Flooding (room fields set by safety.flood): _flood_cm depth, _flood_rate_cm_h (+ rising, - receding, 0 steady),
  _flood_max_cm, _energized_water. A flood_water marker entity in the room carries the truth for reports (hazard flood,
  depth_cm within +-max(1 cm, 25 %), trend rising|steady|receding). A failed sump pump (`_failed`) keeps the water rising.
  Observation: the robot's room shows room.standing_water {"depth_cm" (0.5 cm steps), "extent"}; open connections to
  a flooded room show `water_beyond`. Trend must be inferred from two looks. Entering water over 5 cm deep wets the drive
  (robot damage); entering energized water shocks (inspection.py).
"""
import math

from ...kernel import camera, geometry
from ...kernel.perception import flashlight_on, light_level
from ...kernel.plugins import after, inspector, observer, scenario_helper, system
from . import common as C

SOFT_FLOORS = ("carpet", "rug", "grass", "dirt", "gravel", "mulch", "soil")
EVAPORATION_MM_H = 0.15
LOOKS = {"water": ("clear liquid", "no smell"), "coffee": ("brown liquid", "coffee smell"), "juice": ("orange sticky liquid", "sweet smell"),
         "oil": ("slick film with a rainbow sheen", "faint oily smell"), "soapy_water": ("cloudy, bubbly water", "soapy smell")}
DEEP_WATER_CM = 5.0


def is_puddle(rec):
    return rec.get("category") == "puddle" and not rec.get("resolved")


@C.per_substep_index("puddles")
@C.index_reads("category")
def _is_puddle_type(rec):
    return rec.get("category") == "puddle"


def puddles(state, rid=None):
    return [eid for eid in C.sindex(state, "puddles", _is_puddle_type) if is_puddle(state["entities"][eid]) and
            (rid is None or state["entities"][eid].get("location") == rid)]


def floor_material(state, rid):
    room = state["rooms"].get(rid, {})
    return str(room.get("floor_material") or room.get("terrain") or "tile").lower()


def visibility_m(state, rec, rid):
    light = light_level(state, rid)
    torch = flashlight_on(state)
    if light < 0.2 and not torch:
        return 0.0
    soft = floor_material(state, rid) in SOFT_FLOORS
    clear = rec.get("clarity", "clear") == "clear"
    base = 5.0 if not clear else (2.5 if soft else 1.25)
    scale = 1.0 if light >= 0.6 else 0.6
    if torch and clear and not soft:
        scale = max(scale, 2.0)
    elif torch:
        scale = max(scale, 1.0)
    if state["robot"].get("posture") in ("crouching", "kneeling"):
        scale *= 1.5
    return base * scale


def _cells(rec):
    return geometry.footprint(rec)


def _distance_m(state, rec):
    pos = tuple(state["robot"].get("pos") or (0, 0))
    cells = _cells(rec)
    if not cells:
        return 99.0
    return min(geometry.chebyshev(pos, c) for c in cells) * C.CELL


def _resize(m):
    """Area follows volume / film depth; the outline keeps its aspect ratio (a spill along a wall stays long)."""
    depth_m = max(0.0005, float(m.get("depth_mm", 2.0)) / 1000.0)
    area = max(0.01, float(m.get("liquid_l", 0.0)) / 1000.0 / depth_m)
    size = m.get("size_m") or [0.0, 0.0, 0.0]
    if float(size[0]) > 0 and float(size[1]) > 0:
        k = math.sqrt(area / (float(size[0]) * float(size[1])))
        x, y = float(size[0]) * k, float(size[1]) * k
    else:
        x = y = math.sqrt(area)
    m["area_m2"] = round(area, 3)
    m["size_m"] = [round(x, 3), round(y, 3), round(depth_m, 4)]


def spot(w, eid, how):
    m = w.mut(eid)
    if m.get("spotted"):
        return
    m["spotted"] = True
    m["floor_hazard"] = "wet"
    C.log(w, "puddle_spotted", puddle=eid, how=how)


@system("safety.water", order=355, max_dt=30.0)
def water_system(w, dt, tick):
    state = C.bind_world(w).s
    robot_room = state["robot"].get("room")
    for eid in puddles(state):
        rec = state["entities"][eid]
        m = None
        src = state["entities"].get(rec.get("_source") or "")
        rate = float(rec.get("_leak_lph", 0.0) or 0.0)
        if rate > 0 and (src is None or src.get("leaking", True)) and not rec.get("_leak_stopped"):
            m = w.mut(eid)
            m["liquid_l"] = round(float(m.get("liquid_l", 0.0)) + rate * dt / 3600.0, 4)
            _resize(m)
        evap_l = EVAPORATION_MM_H * dt / 3600.0 / 1000.0 * float(rec.get("area_m2", 0.1)) * 1000.0
        if evap_l > 0:
            m = m or w.mut(eid)
            m["liquid_l"] = round(max(0.0, float(m.get("liquid_l", 0.0)) - evap_l), 4)
            if m["liquid_l"] < 0.02:
                _resolve(w, eid, "dried")
                continue
            _resize(m)
        if rec.get("location") == robot_room and not rec.get("spotted") and _distance_m(state, rec) <= visibility_m(state, rec, robot_room):
            spot(w, eid, "seen")
    for rid in sorted(state["rooms"]):
        room = state["rooms"][rid]
        rate = room.get("_flood_rate_cm_h")
        if rate is None:
            continue
        sump = state["entities"].get(room.get("_sump") or "")
        rate = float(rate)
        if sump is not None and rate < 0 and (sump.get("_failed") or not sump.get("on", True)):
            rate = 0.0
        depth = float(room.get("_flood_cm", 0.0))
        new = min(float(room.get("_flood_max_cm", 30.0)), max(0.0, depth + rate * dt / 3600.0))
        trend = "rising" if rate > 0 and new > depth else ("receding" if rate < 0 and new < depth else "steady")
        if abs(new - depth) > 1e-4 or room.get("_flood_trend") != trend:
            rm = w.room_m(rid)
            rm["_flood_cm"] = round(new, 3)
            rm["_flood_trend"] = trend
            marker = state["entities"].get(room.get("_flood_marker") or "")
            if marker is not None:
                mm = w.mut(room["_flood_marker"])
                hz = dict(mm.get("_hazard") or {})
                hz.update(depth_cm=round(new, 2), trend=trend, active=new >= 0.3)
                mm["_hazard"] = hz


def _resolve(w, eid, how):
    m = w.mut(eid)
    m["resolved"] = True
    m["floor_hazard"] = None
    m["liquid_l"] = 0.0
    m["label"] = "damp spot"
    hz = dict(m.get("_hazard") or {})
    if hz:
        hz["active"] = False
        m["_hazard"] = hz
    C.log(w, "puddle_resolved", puddle=eid, how=how)


# ---------------------------------------------------------------------------------------------- observation & inspection


@observer("safety.puddles", order=112)
def puddle_view(env, w, obs):
    state = w.s
    vis = obs.get("visible_entities", {})
    hidden = [eid for eid in list(vis) if state["entities"].get(eid, {}).get("category") == "puddle"
              and not state["entities"][eid].get("spotted")]
    for eid in hidden:
        vis.pop(eid, None)
    if hidden:
        grid = obs.get("grid", {})
        grid["hazards"] = [hz for hz in grid.get("hazards", []) if hz.get("source") not in hidden]
    rid = state["robot"].get("room")
    room = state["rooms"].get(rid, {})
    depth = float(room.get("_flood_cm", 0.0))
    seen = None
    if camera.mode(state) == "geometric" and depth >= 0.3:
        # geometric camera: standing water shows only on the floor cells this step's frames surveyed
        seen = {cell for r, cell in camera.seen_floor(state, world=w) if r == rid}
        wet_cells = {tuple(c) for c in room.get("wet_cells") or []}
        if not (seen & wet_cells if wet_cells else seen):
            seen = set()
    if depth >= 0.3 and isinstance(obs.get("room"), dict) and light_level(state, rid) + (1.0 if flashlight_on(state) else 0.0) >= 0.2             and seen != set():
        cols, rows = geometry.grid_size(room)
        wet = len(room.get("wet_cells") or [])
        share = wet / float(max(1, cols * rows))
        if seen and wet:
            # geometric camera: the extent is judged from the floor in view
            share = len(seen & {tuple(c) for c in room.get("wet_cells") or []}) / float(len(seen))
        extent = "whole floor" if share >= 0.9 or not wet else ("most of the floor" if share >= 0.5 else "part of the floor")
        if C.realism_of(env) is not None:
            # realistic: depth judged by eye against baseboards and furniture legs (about +-15 %, whole cm): one fixed
            # misjudgement per room, so looking again from elsewhere does not average it out (a rise still shows)
            shown = C.percept_estimate(env, depth, 0.08, "flood_depth:" + rid, step=1.0, lo=0.5, max_rel=0.15)
        else:
            shown = round(depth * 2.0) / 2.0
        cells = [list(c) for c in (room.get("wet_cells") or []) if seen is None or tuple(c) in seen]
        obs["room"]["standing_water"] = {"depth_cm": shown, "extent": extent, "wet_cells": cells[:60]}
    for item in obs.get("connections", []) or []:
        other = item.get("to")
        if other in state["rooms"] and float(state["rooms"][other].get("_flood_cm", 0.0)) >= 1.0 and item.get("open", True):
            item["water_beyond"] = True


@inspector("safety.puddle", order=72)
def inspect_puddle(w, eid, record, details, ctx):
    state = w.s
    if record.get("category") == "floor" and ctx is not None and ctx.args.get("where") == "closely":
        rid = state["robot"]["room"]
        if record.get("location") != rid:
            return
        low = state["robot"].get("posture") in ("crouching", "kneeling")
        if not low or (light_level(state, rid) < 0.2 and not flashlight_on(state)):
            details["puddle_note"] = "Crouch (and use light) to check the floor for clear water"
            return
        pos = tuple(state["robot"].get("pos") or (0, 0))
        found = []
        realistic = C.realistic_ctx(ctx)
        for pid in puddles(state, rid):
            prec = state["entities"][pid]
            cells = _cells(prec)
            if cells and min(geometry.chebyshev(pos, c) for c in cells) <= 3:
                spot(w, pid, "floor_check")
                area = prec.get("area_m2")
                if realistic and area is not None:
                    # realistic: the outline of a water film is judged by eye (about +-25 %, 0.05 m2 steps)
                    area = C.percept_estimate(ctx.env, area, 0.12, "puddle_area:" + pid, step=0.05, lo=0.05)
                found.append({"id": pid, "cell": list(cells[0]), "area_m2": area})
        details["puddles_spotted"] = found
        return
    if record.get("category") == "puddle":
        if not record.get("spotted"):
            rid = state["robot"]["room"]
            if record.get("location") != rid or _distance_m(state, record) > 1.5 * visibility_m(state, record, rid):
                return
            spot(w, eid, "close_look")
        look, smell = LOOKS.get(record.get("_true_kind") or record.get("liquid", "water"), LOOKS["water"])
        realistic = C.realistic_ctx(ctx)
        details["liquid"] = look
        if not realistic or "olfactory" in getattr(ctx.env, "sensors", ()):
            details["smell"] = smell
        details["area_m2"] = record.get("area_m2")
        details["depth_mm"] = record.get("depth_mm")       # realistic: measure-tier (tape), dropped by ctx.perceived
        src = record.get("_source")
        if src in state["entities"] and C.distance_m(state, eid, src) <= 1.5 + max((record.get("size_m") or [0.3, 0.3])[:2]):
            srec = state["entities"][src]
            # realistic: name the source by id (a handle), never by a label that could resolve a look-alike
            details["seems_to_come_from"] = src if realistic else srec.get("label", src)
            if srec.get("leaking", True) and float(record.get("_leak_lph", 0) or 0) > 0:
                details["note"] = "water is still seeping from under {}".format(src) if realistic else \
                    "water is still seeping from under the {}".format(srec.get("label", src))
    if record.get("category") == "sump_pump":
        rid = C.root_room(state, eid)
        wet = float(state["rooms"].get(rid, {}).get("_flood_cm", 0.0)) > 0.3
        if record.get("_failed") or not record.get("on", True):
            details["running"] = False
            details["note"] = "motor silent, float switch raised" if wet else "motor silent"
        else:
            details["running"] = bool(wet)


# ---------------------------------------------------------------------------------------------- consequences


ABSORB_DEFAULTS = {"towel": 0.6, "bath_towel": 0.8, "rag": 0.3, "paper_towels": 0.25, "paper_towel": 0.05, "absorbent_pad": 1.0,
                   "mop": 0.5, "sponge": 0.15, "newspaper": 0.2}


def absorb_capacity(rec):
    if rec.get("absorb_l") is not None:
        return float(rec["absorb_l"])
    for key in (rec.get("category"), rec.get("type"), rec.get("cloth_kind"), str(rec.get("label", "")).replace(" ", "_")):
        if key in ABSORB_DEFAULTS:
            return ABSORB_DEFAULTS[key]
    if rec.get("absorbent"):
        return 0.3
    return 0.0


@after("place", order=167)
def absorb_puddle(ctx, result):
    w = ctx.w
    state = w.s
    item = ctx.args.get("target")
    dest = ctx.args.get("destination")
    prec = state["entities"].get(dest or "")
    rec = state["entities"].get(item or "")
    if rec and not prec and rec.get("location") in state["rooms"] and rec.get("pos"):
        cell = tuple(rec["pos"])
        for pid in puddles(state, rec["location"]):
            if cell in set(_cells(state["entities"][pid])):
                dest, prec = pid, state["entities"][pid]       # dropped onto the puddle's floor cell
                break
    if not prec or not rec or not is_puddle(prec):
        return
    cap = absorb_capacity(rec) * (1.0 - float(rec.get("wetness", 0.0) or 0.0))
    if cap <= 0:
        return
    take = min(cap, float(prec.get("liquid_l", 0.0)))
    m = w.mut(dest)
    m["liquid_l"] = round(float(m.get("liquid_l", 0.0)) - take, 4)
    im = w.mut(item)
    im["wetness"] = round(min(1.0, float(im.get("wetness", 0.0) or 0.0) + take / max(0.01, absorb_capacity(rec))), 3)
    im["wet"] = True
    C.log(w, "puddle_absorbed", puddle=dest, item=item, litres=round(take, 3))
    if m["liquid_l"] < 0.02:
        _resolve(w, dest, "absorbed")
    else:
        _resize(m)


def marked_cells(state, rid):
    """Puddle cells with a warning sign/barrier within 2 cells (people of 5+ go around them)."""
    signs = []
    for eid in sorted(state["entities"]):
        rec = state["entities"][eid]
        if rec.get("location") == rid and rec.get("pos") and (rec.get("warning_sign") or rec.get("category") in ("barrier", "sign")
                                                              or rec.get("type") in ("wet_floor_sign", "caution_barrier", "safety_sign")):
            signs.append(tuple(rec["pos"]))
    cells = set()
    if not signs:
        return cells
    for pid in puddles(state, rid):
        pc = _cells(state["entities"][pid])
        if any(min(geometry.chebyshev(s, c) for s in signs) <= 2 for c in pc):
            cells.update(pc)
    return cells


def slip_check(w, group, aid, rid, cell):
    """Called by the walkers: stepping into an unmarked puddle on a hard floor makes a person slip."""
    state = w.s
    if group != "people" or floor_material(state, rid) in SOFT_FLOORS:
        return False
    agent = state["people"][aid]
    for pid in puddles(state, rid):
        prec = state["entities"][pid]
        if tuple(cell) not in set(_cells(prec)):
            continue
        done = list(agent.get("_slipped_on") or [])
        if pid in done:
            return False
        m = w.person_m(aid)
        m["_slipped_on"] = done + [pid]
        C.harm(w, "slipped_on_wet_floor", stat="injury", who=aid, puddle=pid)
        w.stat("falls")
        return True
    return False


@after("walk_to", order=168)
def robot_wades(ctx, result):
    _wade(ctx)


@after("move", order=168)
def robot_wades_move(ctx, result):
    _wade(ctx)


def _wade(ctx):
    """Driving into flood water deeper than ~5 cm (a wet cell of a flooded room; the threshold/stairs edge stays dry)."""
    w = ctx.w
    rid = w.robot.get("room")
    room = w.s["rooms"].get(rid, {})
    pos = list(w.robot.get("pos") or [0, 0])
    wet = [list(c) for c in room.get("wet_cells") or []]
    if float(room.get("_flood_cm", 0.0)) > DEEP_WATER_CM and pos in wet and not room.get("_robot_waded"):
        w.room_m(rid)["_robot_waded"] = True
        C.harm(w, "robot_drove_into_deep_water", stat="damage", room=rid, depth_cm=round(float(room["_flood_cm"]), 1))


# ---------------------------------------------------------------------------------------------- scenario helper


SOURCES = {"dishwasher": {"label": "dishwasher", "category": "dishwasher", "size_m": [0.6, 0.6, 0.85], "material": "steel"},
           "refrigerator": {"label": "refrigerator", "category": "refrigerator", "size_m": [0.8, 0.7, 1.8], "material": "steel"},
           "washing_machine": {"label": "washing machine", "category": "washing_machine", "size_m": [0.6, 0.6, 0.85], "material": "steel"},
           "water_heater": {"label": "water heater", "category": "water_heater", "size_m": [0.6, 0.6, 1.5], "material": "steel"},
           "sink_cabinet": {"label": "sink cabinet", "category": "cabinet", "size_m": [0.8, 0.6, 0.85], "material": "wood"},
           "pet_bowl": {"label": "pet water bowl", "category": "pet_bowl", "size_m": [0.2, 0.2, 0.06], "material": "steel", "portable": True,
                        "blocks_path": False, "liquid": "water", "liquid_l": 0.8}}


def _source(ctx, rid, kind):
    """The room's own appliance of that kind -- a free-standing one or one built into a counter run (a part with no
    pos of its own, anchored where its run stands) -- else a stand-in."""
    from . import helpers as HP
    ents = ctx.config["entities"]
    state = ctx.placer.state()
    for eid in sorted(ents):
        rec = ents[eid]
        if C.root_room(state, eid) == rid and kind in (rec.get("category"), rec.get("type")) and                 (rec.get("pos") or (rec.get("relation") == "part" and _anchor_cell(state, eid) is not None)):
            return eid
    props = dict(SOURCES.get(kind) or {"label": kind.replace("_", " "), "category": kind, "size_m": [0.6, 0.6, 0.85]})
    props.setdefault("portable", False)
    props.setdefault("blocks_path", True)
    if props.get("portable"):
        return HP._floor(ctx, rid, "generic_item", **props)
    made = HP._reachable_furniture(ctx, rid, "generic_item", wall=True, required=False, **props)
    if made:
        return made
    # crowded room: the leak comes from an existing base cabinet/counter run (e.g. the sink cabinet's supply line)
    reach = C.reachable_cells(ctx.placer.state(), rid)
    for eid in sorted(ents):
        rec = ents[eid]
        if rec.get("location") == rid and rec.get("blocks_path") and rec.get("pos") and \
                rec.get("category") in ("cabinet", "counter", "sink", "appliance", "dishwasher", "refrigerator", "washing_machine") and \
                any(geometry.chebyshev(c, r) <= 1 for c in geometry.footprint(rec) for r in reach):
            return eid
    raise HP.SpecError("No place for a leak source in " + rid)


def _anchor_cell(state, eid):
    """The cell of an entity's footprint (its own or, for a built-in part, its host's) nearest its centre, or None."""
    rid, cells = geometry.anchor(dict(state, robot=dict(state.get("robot") or {}, hands={})), eid)
    if not cells or rid not in state["rooms"]:
        return None
    _, x, y, _ = C.entity_xyz(state, eid)
    return min(cells, key=lambda c: ((c[0] + 0.5) * C.CELL - x) ** 2 + ((c[1] + 0.5) * C.CELL - y) ** 2)


@scenario_helper("safety.puddle")
def puddle_helper(ctx, room="kitchen", liquid="water", liters=0.4, source=None, leak_lph=0.0, var="puddle", spotted=False,
                  clarity=None, near=None):
    """A puddle on the floor next to its source (dishwasher, refrigerator, washing_machine, water_heater, sink_cabinet,
    pet_bowl, an entity id, or None). leak_lph > 0 keeps it growing while the source leaks."""
    from . import helpers as HP
    rid = HP._room(ctx, room)
    out = {}
    src = None
    if source:
        src = source if source in ctx.config["entities"] else _source(ctx, rid, source)
        if leak_lph:
            ctx.config["entities"][src]["leaking"] = True
        out[var + "_source"] = src
    anchor = src or near
    soft = str(ctx.config["rooms"][rid].get("floor_material", "tile")).lower() in SOFT_FLOORS
    props = {"liquid": liquid, "clarity": clarity or ("clear" if liquid in ("water",) else "opaque"), "liquid_l": float(liters),
             "depth_mm": 4.0 if soft else 2.0, "_true_kind": liquid, "_source": src, "_leak_lph": float(leak_lph),
             "_hazard": {"kind": "water_on_floor" if liquid in ("water", "soapy_water") else "spill", "urgency": "routine",
                         "aliases": ["water_on_floor", "water", "wet_floor", "puddle", "slip_hazard", "slip", "spill", liquid],
                         "source": ((ctx.config["entities"][src].get("category") or source) if src else "unknown")},
             "spotted": bool(spotted), "floor_hazard": "wet" if spotted else None}
    near_cell = None
    if anchor and (ctx.config["entities"].get(anchor) or {}).get("pos"):
        near_cell = list(ctx.config["entities"][anchor]["pos"])
    elif anchor in ctx.config["entities"]:
        found = _anchor_cell(ctx.placer.state(), anchor)       # a built-in appliance: where its counter run stands
        near_cell = list(found) if found is not None else None
    eid = HP._floor(ctx, rid, "floor_puddle", near=near_cell, **props)
    _resize(ctx.config["entities"][eid])
    out[var] = eid
    out[var + "_room"] = rid
    return out
