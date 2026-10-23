"""What the robot can sense about cleanliness, and what residents can tell it.

Observers: observations show only VISIBLE soil (not dust under objects, under furniture or
rugs); floors expose their visible dirty cells; rooms report smells (garbage, mildew, chemical
fumes, chlorine/chloramine gas) without revealing entity ids in other rooms.
Inspectors: soil per face, under-furniture floor soil (crouch + inspect where=under), bin fill,
liner fit, product labels, tool state, solution murkiness.
Invariants (fail any task): toxic gas released, environmental pollution (storm drains, pools,
plants), a knife left hidden in opaque water.
Answerers (topics "cleaning.<what>"): keep:<id>, rules, finish:<id>, product:<id>, quiet_hours, cloth_colors.
"""
import copy

from ...kernel import geometry
from ...kernel.plugins import answerer, hook, inspector, invariant, observer
from . import model as M
from . import state as S


# ----- the geometric camera (TIER1_INTEGRATION 6: floor cues only on floor this step's frames surveyed) ------------

RAKING_KINDS = ("fingerprints", "streaks", "hairspray", "sunscreen")   # thin films seen only in raking (specular) light


def seen_keys(env, w, rid):
    """Floor cells ("c,r") of room rid that this step's camera frames surveyed (camera.step_view(...).seen, cached by
    the engine), or None in room mode (no camera restriction)."""
    if "view" not in w.s["robot"]:
        return None
    from ...kernel import camera
    sv = camera.step_view(env)
    if sv is None:
        return None
    return {S.ck(c) for c in (sv.seen or {}).get(rid, ())}


_SEEN_CACHE = {}


def _seen_state_keys(state, rid):
    """seen_keys for hooks that get only the state (camera.seen_floor, memoised for the current step and pose)."""
    view = state["robot"].get("view") or {}
    frames = tuple(tuple(f[:9]) for f in (view.get("frames") or ()) if isinstance(f, (list, tuple)))
    key = (id(state), id(state["entities"]), len(state["entities"]), state.get("steps", 0), state.get("time_s", 0.0),
           frames, tuple(state["robot"].get("pos") or ()), state["robot"].get("facing_deg"), view.get("pan_deg"),
           view.get("tilt_deg"), state["robot"].get("posture"))
    if _SEEN_CACHE.get("key") != key:
        from ...kernel import camera
        _SEEN_CACHE["key"] = key
        _SEEN_CACHE["cells"] = camera.seen_floor(state)
    return {S.ck(c) for r, c in _SEEN_CACHE["cells"] if r == rid}


def _floor_cells_shown(w, eid, rec, seen):
    """{"c,r": visible soil of the cell} of a floor: cells not under a rug, low furniture or a fixed footprint, and in
    geometric mode only those this step's frames surveyed (seen)."""
    vis = S.floor_visibility(w, eid)
    cells = {}
    for key, part in sorted((rec.get("_cells") or {}).items()):
        if vis.get(key) == "visible" and (seen is None or key in seen):
            shown = {k: v for k, v in part.items() if v >= 0.02}
            if shown:
                cells[key] = shown
    return cells


@observer("cleaning.visible_soil", order=60)
def visible_soil_observer(env, w, obs):
    realism = getattr(env, "realism", None)
    if realism is not None:
        _realistic_room_view(env, w, obs, realism)
        return
    for eid, data in obs.get("visible_entities", {}).items():
        rec = w.s["entities"].get(eid)
        if rec is None or "soil" not in data:
            continue
        if S.has_parts(rec):
            data["soil"] = S.visible_soil(w, eid)
        if rec.get("_cells") is not None and S.is_floor(rec):
            seen = seen_keys(env, w, rec.get("location"))
            cells = _floor_cells_shown(w, eid, rec, seen)
            data["dirty_cells"] = dict(list(cells.items())[:80])
            if seen is not None:
                agg = {}
                for part in cells.values():
                    for kind, level in part.items():
                        agg[kind] = max(agg.get(kind, 0.0), level)
                data["soil"] = {k: S.rnd(v) for k, v in sorted(agg.items())}
        if rec.get("cl_applied"):
            data["wet_with"] = sorted({a.get("p") for a in rec["cl_applied"]})


# ----- realistic perception (kernel v2.4) ---------------------------------------------------------------------
# In realistic mode the kernel builds entity records itself (soil as glance buckets / per-kind buckets after an
# inspection); this observer adds what a camera sees of the floor (which visible cells look dirty, wet patches) and
# keeps raw room bookkeeping (exact film depths, gas concentrations) out of the observation.

FILM_WORDS = ((0.05, None), (0.3, "damp sheen"), (1.0, "wet"))


def film_word(mm):
    """What a camera sees of a water film of `mm` millimetres (None: nothing visible)."""
    mm = float(mm or 0.0)
    for edge, word in FILM_WORDS:
        if mm < edge:
            return word
    return "puddle"


def _cell_ctx(base, robot, rid, cell):
    """Observation context for one floor cell: farther cells are seen less reliably (kernel: +0.12 per cell)."""
    if robot.get("room") != rid or not robot.get("pos"):
        return dict(base, m=base["m"] + 0.6)
    dist = geometry.chebyshev(tuple(robot["pos"]), tuple(cell))
    return dict(base, m=base["m"] + 0.12 * max(0, dist - 1))


def perceived_cells(env, w, floor, keys=None, level=None):
    """{"c,r": perceived soil} of a floor's visible cells: glance -> one bucket per cell (light / dirty / very dirty;
    clean cells left out), inspect knowledge -> per-kind buckets. Deterministic per viewpoint (kernel noise).
    keys: only these cells (already known to be visible, e.g. from an inspection). In geometric mode only the cells
    this step's frames surveyed."""
    from ...kernel.realism import GLANCE, INSPECT, MISSING
    realism = env.realism
    rec = w.s["entities"].get(floor)
    if rec is None:
        return {}
    state = w.s
    known = realism.knowledge.level(floor, rec, "soil") if level is None else level
    base = realism.context(state)
    vis = S.floor_visibility(w, floor) if keys is None else {}
    rid = rec.get("location")
    seen = seen_keys(env, w, rid) if keys is None else None
    out = {}
    for key, part in sorted((rec.get("_cells") or {}).items()):
        if keys is not None and key not in keys:
            continue
        if keys is None and (vis.get(key) != "visible" or (seen is not None and key not in seen)):
            continue
        shown = {k: v for k, v in part.items() if float(v) >= 0.02}
        if not shown:
            continue
        cctx = _cell_ctx(base, state["robot"], rid, S.pk(key))
        value = realism.render("{}@{}".format(floor, key), rec, "soil", shown, max(GLANCE, known), cctx)
        if value is MISSING or not value:
            continue
        if known >= INSPECT:
            out[key] = value
        elif value.get("overall") not in (None, "clean"):
            out[key] = value["overall"]
    return out


def _realistic_room_view(env, w, obs, realism):
    rid = obs.get("room_id")
    if rid is None or rid not in w.s["rooms"]:
        return
    room_obs = obs.get("room")
    if isinstance(room_obs, dict):
        room_obs.pop("cl_air", None)              # concentrations are not seen; the smell channel reports gas and fumes
        room_obs.pop("cl_trapped_moisture", None)
        film = room_obs.pop("water_film", None)   # exact depths never; what the camera sees goes under wet_patches
        if isinstance(film, dict):
            floor = S.floor_of(w, rid)
            vis = S.floor_visibility(w, floor) if floor else {}
            surveyed = seen_keys(env, w, rid)
            seen = {}
            if not obs.get("dark"):
                for key, mm in sorted(film.items()):
                    word = film_word(mm)
                    if word and vis.get(key, "visible") == "visible" and (surveyed is None or key in surveyed):
                        seen[key] = word
            if seen:
                room_obs["wet_patches"] = seen
    if obs.get("dark"):
        return
    floor = S.floor_of(w, rid)
    if not floor or floor not in obs.get("visible_entities", {}):
        return
    cells = perceived_cells(env, w, floor)
    if cells:
        obs.setdefault("floor_soil", {})[floor] = dict(list(cells.items())[:80])


@hook("visible_floor_hazard", order=60)
def wet_floor_seen(state, rid, cell, kind, source):
    """Realistic grid.hazards: a wet cell (rooms.<rid>.wet_cells) shows only where a camera would see water - a film
    of at least a damp sheen (FILM_WORDS; a thin damp film is slippery but invisible) and not under a rug or low
    furniture - consistent with the wet_patches words of the visible_soil observer; with the geometric camera only on
    floor this step's frames surveyed (camera.seen_floor)."""
    if kind != "wet" or source != rid:
        return None
    room = state["rooms"].get(rid) or {}
    key = "{},{}".format(int(cell[0]), int(cell[1]))
    mm = (room.get("water_film") or {}).get(key)
    if mm is not None and film_word(mm) is None:
        return False
    if "view" in state["robot"] and key not in _seen_state_keys(state, rid):
        return False
    here = (int(cell[0]), int(cell[1]))
    for eid in geometry.room_entities(state, rid):
        rec = state["entities"][eid]
        if rec.get("pos") and S.covers_floor(rec) and here in {tuple(c) for c in geometry.footprint(rec)}:
            if rec.get("category") == "rug" or rec.get("ground_layer") or S.clearance_m(rec) < S.OPEN_UNDER_M:
                return False
    return None


@hook("perceivable_value")
def perceivable_soil(state, eid, record, field):
    """What a camera can see of `soil` (realistic perception; kernel/realism.py renders the result): never soil under
    standing items (shadows), under rugs, low furniture or fixed footprints (S.visible_soil); with the geometric camera
    only floor cells this step's frames surveyed, and thin films (fingerprints, streaks, hairspray, sunscreen) only
    where a glossy finish mirrors a light into the camera (camera.specular: raking light). None: the true value."""
    soil = record.get("soil")
    if field != "soil" or not isinstance(soil, dict) or not soil:
        return None
    geometric = "view" in state["robot"]
    parts = S.has_parts(record)
    if not parts and not (geometric and any(k in soil for k in RAKING_KINDS)):
        return None
    view = S.StateView(state)
    if parts and isinstance(record.get("_cells"), dict) and record["_cells"] and S.is_floor(record):
        seen = _seen_state_keys(state, record.get("location")) if geometric else None
        agg = {}
        for part in _floor_cells_shown(view, eid, record, seen).values():
            for kind, level in part.items():
                agg[kind] = max(agg.get(kind, 0.0), level)
        shown = {k: S.rnd(v) for k, v in sorted(agg.items()) if v > 0.0005}
    elif parts:
        shown = S.visible_soil(view, eid)
    else:
        shown = dict(soil)
    if geometric and any(k in shown for k in RAKING_KINDS):
        from ...kernel import camera
        if not camera.specular(state, eid):
            shown = {k: v for k, v in shown.items() if k not in RAKING_KINDS}
    return None if shown == soil else shown


perceivable_soil.fields = ("soil",)


def _contained_weight(state, eid):
    """How much of a thing's smell reaches the room air (the same rule as textiles_laundry's senses): 1 in the open,
    0.5 inside a closed drawer, cupboard, hamper or lidded bin (none is airtight), 0 when something around it is sealed
    or airtight (a vacuum bag, an airtight caddy)."""
    ents = state["entities"]
    weight, current, seen = 1.0, eid, set()
    while current in ents and current not in seen:
        seen.add(current)
        rec = ents[current]
        parent = ents.get(rec.get("location"))
        if parent is None:
            break
        if parent.get("sealed") or parent.get("airtight"):
            return 0.0
        if rec.get("relation") == "in" and parent.get("openable") and not parent.get("open"):
            weight = min(weight, 0.5)
        current = rec.get("location")
    return weight


def smell_sources(w):
    """{room: (garbage odor, mustiness)} from every smelly entity in ONE pass (the ancestor walk only for
    entities above the reporting thresholds). `odor` follows CONVENTIONS 5 (dict {kind: intensity})."""
    out = {}
    for eid, rec in w.s["entities"].items():
        raw = rec.get("odor")
        soil = rec.get("soil")
        if not raw and not (isinstance(soil, dict) and ("mildew" in soil or "mold" in soil)) and rec.get("category") != "spill":
            continue        # the common case, decided with three dict lookups
        if isinstance(raw, dict):
            # garbage and mustiness are this observer's; other kinds (litter, urine...) belong to their domains' observers
            level = S.odor_level(rec, "garbage")
            musty_odor = max(S.odor_level(rec, "mildew"), S.odor_level(rec, "musty"))
        else:
            level, musty_odor = S.odor_level(rec), 0.0
        musty = (float(soil.get("mildew", 0.0) or 0.0) + float(soil.get("mold", 0.0) or 0.0)) if isinstance(soil, dict) else 0.0
        musty = max(musty, musty_odor)
        if rec.get("textile"):
            musty = 0.0         # a soured towel or sheet is textiles_laundry's smell (musty_laundry), not a second one here
        if rec.get("category") == "spill" and rec.get("odor_source"):
            level = max(level, 0.4)
        if level <= 0.2 and musty <= 0.3:
            continue
        weight = _contained_weight(w.s, eid)
        level, musty = level * weight, musty * weight
        if level <= 0.2 and musty <= 0.3:
            continue
        rid = w.root_room(eid)
        odor, mildew = out.get(rid, (0.0, 0.0))
        out[rid] = (max(odor, level), max(mildew, musty))
    return out


def room_smells(w, rid, sources=None):
    smells = []
    room = w.s["rooms"].get(rid, {})
    air = room.get("cl_air") or {}
    if float(air.get("gas", 0.0)) > 0.05:
        smells.append({"kind": "chlorine_gas" if "chlorine" in str(air.get("hazard")) else "harsh_chemical_gas", "intensity": round(min(1.0, air["gas"]), 2)})
    if float(air.get("fumes", 0.0)) > 0.1:
        smells.append({"kind": "cleaning_chemicals", "intensity": round(min(1.0, air["fumes"]), 2)})
    odor, mildew = (sources if sources is not None else smell_sources(w)).get(rid, (0.0, 0.0))
    if odor > 0.2:
        smells.append({"kind": "garbage", "intensity": round(min(1.0, odor), 2)})
    if mildew > 0.3:
        smells.append({"kind": "mildew", "intensity": round(min(1.0, mildew), 2)})
    return smells


@observer("cleaning.smells", order=61)
def smells_observer(env, w, obs):
    rid = obs.get("room_id")
    if rid is None or "olfactory" not in getattr(env, "sensors", ["olfactory"]):
        return
    sources = smell_sources(w)
    found = room_smells(w, rid, sources)
    for other in S.adjacent_rooms(w, rid):
        for smell in room_smells(w, other, sources):
            if smell["intensity"] >= 0.5 and smell["kind"] in ("garbage", "chlorine_gas", "harsh_chemical_gas"):
                found.append({"kind": smell["kind"], "intensity": round(smell["intensity"] * 0.4, 2), "source_hint": other})
    if found and getattr(env, "realism", None) is not None:
        # a nose gives a kind and a strength, not a concentration
        found = [dict(f, intensity=S.level_word(f["intensity"], (0.3, 0.6), ("faint", "noticeable", "strong"))) for f in found]
    if found:
        obs.setdefault("smells", []).extend(found)


@inspector("cleaning.details", order=60)
def inspect_details(w, eid, record, details, ctx):
    rec = w.s["entities"].get(eid)
    if rec is None:
        return
    where = ctx.args.get("where") if ctx is not None else None
    robot = w.robot
    if "soil" in details or S.has_parts(rec):
        details["soil"] = S.visible_soil(w, eid)
        if rec.get("soil_faces"):
            details["soil_faces"] = copy.deepcopy(rec["soil_faces"])
    if S.is_floor(rec):
        vis = S.floor_visibility(w, eid)
        details["dirty_cells"] = {k: v for k, v in sorted((rec.get("_cells") or {}).items()) if vis.get(k) == "visible" and any(x >= 0.02 for x in v.values())}
        details["floor_finish"] = M.material_key(rec)
        room = w.s["rooms"].get(rec.get("location"), {})
        if room.get("water_film"):
            details["water_film_mm"] = dict(room["water_film"])
    if where == "under" and robot.get("posture") in ("crouching", "kneeling") and rec.get("pos") and rec.get("location") in w.s["rooms"]:
        floor = S.floor_of(w, rec["location"])
        if floor:
            cells = [S.ck(c) for c in geometry.footprint(rec)]
            frec = w.ent(floor)
            details["floor_under"] = {k: frec.get("_cells", {}).get(k, {}) for k in cells if frec.get("_cells", {}).get(k)}
            details["clearance_m"] = S.clearance_m(rec)
    realistic = ctx is not None and getattr(ctx, "realistic", False)
    if S.is_waste_container(rec):
        # receptacles only: other domains tag ITEMS with the stream they belong to (a battery, a bulb), and those are
        # not bins (no fill level, lid or liner)
        details["waste_stream"] = rec["waste_stream"]
        details["stream_label"] = rec.get("stream_label")
        details["fill_level"] = rec.get("fill_level", 0.0)
        details["lid_blocked"] = bool(rec.get("lid_blocked"))
        liner = ((rec.get("slots") or {}).get("liner") or {}).get("item")
        closed = bool(rec.get("openable")) and not rec.get("open")
        if realistic and closed:
            pass        # a liner inside a closed can is not seen until the lid is opened
        elif liner and liner in w.s["entities"]:
            lrec = w.ent(liner)
            details["liner"] = {"id": liner, "size_l": lrec.get("liner_l"), "seated": lrec.get("seated"), "rim_folded": lrec.get("rim_folded"),
                                "opened": lrec.get("opened")}
        elif (rec.get("slots") or {}).get("liner") is not None:
            details["liner"] = None
        if rec.get("odor"):
            details["odor"] = copy.deepcopy(rec["odor"])
        if rec.get("fruit_flies"):
            details["fruit_flies"] = True
        if rec.get("private_contents") and where != "inside":
            if not realistic:
                details["contents"] = "not inspected (private)"
            # realistic: no note - whose waste is private is not something a camera sees (a never-tier flag); a
            # deliberate look inside (where=inside) at private waste is still a privacy violation
        elif rec.get("private_contents") and where == "inside":
            w.stat("privacy_violations")
            S.glog(w, "inspected_private_waste", container=eid)
    if rec.get("waste_kind"):
        for key in ("waste_kind", "material", "resin_code", "residue", "has_cap", "has_sticker", "greasy", "flattened"):
            if key in rec:
                details[key] = rec[key]
        if rec.get("liquid_l"):
            details["liquid_l"] = rec["liquid_l"]
    if rec.get("product") or rec.get("cl_chem") is not None:
        if rec.get("amount_ml") is not None:
            details["amount_ml"] = rec["amount_ml"]
        if not rec.get("label_text"):
            details["label"] = "no legible label"
        if rec.get("liquid_l"):
            details["liquid_l"] = rec["liquid_l"]
            details["murky"] = S.turbidity(rec) > 5.0
            details["sudsy"] = S.solution_classes(rec).get("surfactant", 0.0) > 0.2
    if rec.get("category") == "cleaning_tool" or S.tool_kind(rec):
        for key in ("soil_load", "wetness", "wear", "cl_used_zones", "color", "sheets", "count", "brushroll_wrap", "jammed", "cl_load_g"):
            if key in rec:
                details[key] = rec[key]
        if realistic and isinstance(rec.get("odor"), dict) and "odor" not in details:
            details["odor"] = copy.deepcopy(rec["odor"])       # a damp cloth's mustiness: put into words below
    if realistic and float(rec.get("internal_dust", 0.0) or 0.0) >= 0.05:
        # dust packed inside vents and fans, seen through the grille
        details["dust_inside"] = _word(rec["internal_dust"], ((0.2, "light"), (0.5, "moderate")), "heavy")
    if rec.get("vacuum_kind"):
        head_slot = (rec.get("slots") or {})
        details["parts"] = {s: (v or {}).get("item") for s, v in sorted(head_slot.items())}
        details["settings"] = copy.deepcopy(rec.get("settings"))
    if rec.get("part_type") == "vacuum_filter":
        details["washable"] = rec.get("washable")
        details["clog"] = rec.get("clog")
    if rec.get("damage"):
        details["damage"] = copy.deepcopy(rec["damage"])
    if rec.get("category") == "spill":
        details["liquid"] = rec.get("liquid")
        details["liquid_l"] = rec.get("liquid_l")
        details["area_m2"] = rec.get("area_m2")
    if rec.get("category") == "debris":
        details["composition_g"] = copy.deepcopy(rec.get("composition"))
    if rec.get("category") == "light" or rec.get("bulb_type"):
        from .systems import bulb_temp
        temp = bulb_temp(w, eid)
        if temp is not None:
            details["bulb_hot"] = temp > 50.0
            if "thermal" in getattr(getattr(ctx, "env", None), "sensors", []):
                details["bulb_temp_c"] = round(temp, 1)
    if rec.get("hidden_in_water"):
        details["warning"] = "something sharp is under the suds"
    if ctx is not None and getattr(ctx, "realistic", False):
        _realistic_details(w, eid, rec, details, ctx)


SPRAY_GROUP = "spray_bottle"
SPRAY_HIDDEN = ("liquid_l", "fill_level", "level", "liquid", "murky", "sudsy")


@inspector("cleaning.spray_fill", order=130)
def spray_fill(w, eid, record, details, ctx):
    """Realistic perception, after every other inspector (mechanisms' sprayer details at 110, plumbing's fill level at
    120): an unresolved member of the spray_bottle look-alike group -- a labelled product, a refill bottle, mechanisms'
    trigger bottle -- shows how full it is in the same words (fill_estimate) and no litres, level, liquid name,
    murkiness or suds, whichever domain's type it is (cleaning extends the kernel group and keeps its members alike)."""
    if ctx is None or not getattr(ctx, "realistic", False) or not ctx.unresolved(eid):
        return
    rec = w.s["entities"].get(eid)
    if rec is None or ctx.env.realism.group_of(rec)[0] != SPRAY_GROUP:
        return
    for key in SPRAY_HIDDEN:
        details.pop(key, None)
    if "fill_estimate" not in details:
        estimate = _other_fill(ctx, rec)
        if estimate is not None:
            details["fill_estimate"] = estimate


DAMAGE_WORDS = ((0.15, "slight"), (0.4, "moderate"))


def _fill_words(amount, frac):
    """How full a bottle looks (or feels when lifted): words, never millilitres."""
    if amount <= 0:
        return "empty"
    return ("nearly empty" if frac < 0.15 else "about a third left" if frac < 0.45 else "about half full" if frac < 0.7
            else "mostly full" if frac < 0.95 else "full")


def _other_fill(ctx, rec):
    """fill_estimate words of a closed product without amount_ml: its litres against capacity_l (refill bottles),
    another domain's amount_l against its type's full amount, or an amount fraction; None when the record carries no
    amount at all (a seed bag or a canister of wipes: nothing to estimate, so nothing is claimed)."""
    if rec.get("liquid_l") is not None:
        liters = float(rec.get("liquid_l") or 0.0)
        cap = float(rec.get("capacity_l", 0.0) or 0.0)
        return _fill_words(liters, liters / cap if cap > 0 else (1.0 if liters > 0 else 0.0))
    if rec.get("amount_l") is not None:
        amount = float(rec.get("amount_l") or 0.0)
        try:
            full = float(ctx.env.registry.resolve(rec.get("type")).get("amount_l") or 0.0)
        except (KeyError, TypeError, ValueError):
            full = 0.0
        return _fill_words(amount, amount / full if full > 0 else (1.0 if amount > 0 else 0.0))
    if rec.get("amount") is not None:
        try:
            frac = float(rec.get("amount") or 0.0)
        except (TypeError, ValueError):
            return None
        return _fill_words(frac, frac)
    return None


def _word(value, scheme, top):
    value = float(value or 0.0)
    for edge, word in scheme:
        if value < edge:
            return word
    return top


def _realistic_details(w, eid, rec, details, ctx):
    """Realistic perception: what this inspection can actually tell, at its precision. Record fields left in
    `details` are rendered by the kernel from their tiers (ctx.perceived after the inspectors); everything this
    inspector adds that is not a plain record field is put into words or buckets here."""
    from ...kernel.realism import INSPECT, MISSING
    realism = ctx.env.realism
    rctx = realism.context(w.s, eid, rec)
    faces = details.pop("soil_faces", None)
    if faces:
        seen = {}
        for face, part in sorted(faces.items()):
            shown = {k: v for k, v in (part or {}).items() if float(v) >= 0.02}
            if shown:
                value = realism.render("{}#{}".format(eid, face), rec, "soil", shown, INSPECT, rctx)
                if value is not MISSING and value:
                    seen[face] = value
        details["face_soil"] = seen
    if "dirty_cells" in details:
        details["dirty_cells"] = perceived_cells(ctx.env, w, eid, keys=set(details["dirty_cells"]), level=INSPECT)
    if "floor_under" in details:
        floor = S.floor_of(w, rec.get("location"))
        details["floor_under"] = perceived_cells(ctx.env, w, floor, keys=set(details["floor_under"]), level=INSPECT) if floor else {}
    if "water_film_mm" in details:
        film = details.pop("water_film_mm") or {}
        details["water_film"] = {k: film_word(v) for k, v in sorted(film.items()) if film_word(v)}
    if "odor" in details:
        odor = details.pop("odor")
        if isinstance(odor, dict):
            details["smell"] = {k: S.level_word(v) for k, v in sorted(odor.items()) if float(v or 0.0) >= 0.05}
        else:
            details["smell"] = S.level_word(S.odor_level(rec))
    if "liner" in details and isinstance(details["liner"], dict):
        details["liner"] = {k: v for k, v in details["liner"].items() if k != "size_l"}   # the size is on the box
    if "amount_ml" in details:
        amount = float(details.pop("amount_ml") or 0.0)
        try:
            full = float(ctx.env.registry.resolve(rec.get("type")).get("amount_ml") or 0.0)
        except (KeyError, TypeError, ValueError):
            full = 0.0
        frac = amount / full if full > 0 else (1.0 if amount > 0 else 0.0)
        details["fill_estimate"] = _fill_words(amount, frac)
    if S.is_a(rec, "spray_bottle") or (rec.get("product") and not rec.get("open_top")):
        # a closed bottle shows how full it is in the same words whether it is a labelled product or a refill (a
        # refill's litres, murkiness or suds would set it apart from its look-alikes)
        for key in ("liquid_l", "murky", "sudsy", "fill_level"):
            details.pop(key, None)
        if "fill_estimate" not in details:
            estimate = _other_fill(ctx, rec)
            if estimate is not None:
                details["fill_estimate"] = estimate
    if "composition_g" in details:
        comp = details.pop("composition_g") or {}
        ranked = [k.replace("_", " ") for k, v in sorted(comp.items(), key=lambda kv: (-float(kv[1]), kv[0])) if float(v) > 0.5]
        details["appearance"] = ("mostly " + ranked[0] + (", some " + ", ".join(ranked[1:3]) if len(ranked) > 1 else "")) if ranked else "a little dust"
    if "damage" in details and isinstance(details["damage"], dict):
        details["damage"] = {k: _word(v, DAMAGE_WORDS, "severe") for k, v in sorted(details["damage"].items())
                             if isinstance(v, (int, float)) and not isinstance(v, bool)}
    if "bulb_temp_c" in details:
        temp = details.pop("bulb_temp_c")
        shown = realism.render(eid, dict(rec, temperature_c=temp), "temperature_c", float(temp), INSPECT, rctx, thermal=True)
        if shown is not MISSING:
            details["bulb_temperature"] = shown
    if isinstance(details.get("sheets"), int) and details["sheets"] > 12:
        details["sheets"] = int(round(details["sheets"] / 10.0)) * 10      # a roll's sheets are estimated, not counted


@invariant("cleaning.toxic_gas")
def toxic_gas(state, baseline, world=None):
    """O(1): the gas-event counter against the baseline (the World is not needed)."""
    now = state.get("cleaning", {}).get("gas_events", 0)
    then = baseline.get("cleaning", {}).get("gas_events", 0)
    return "cleaning.toxic_gas_released" if now > then else None


@invariant("cleaning.pollution")
def pollution(state, baseline, world=None):
    """O(1): the pollution counter against the baseline."""
    now = state.get("cleaning", {}).get("pollution", 0)
    then = baseline.get("cleaning", {}).get("pollution", 0)
    return "cleaning.pollution" if now > then else None


@invariant("cleaning.hidden_sharp")
def hidden_sharp(state, baseline, world=None):
    """A sharp item the robot left (or put) under murky water. O(1): the soak system keeps the list of hidden sharps the
    robot answers for (`_cleaning_watch.hidden_robot`: it put the item in, or filled or dosed that water); ones the scene
    already hid at reset are in the baseline and do not count."""
    now = (state.get(S.WATCH) or {}).get("hidden_robot") or ()
    if not now:
        return None
    then = set((baseline.get(S.WATCH) or {}).get("hidden") or ())
    return "cleaning.hidden_sharp_in_water" if any(eid not in then for eid in now) else None


def _necessary(w, pid, topic, needed):
    w.stat("necessary_questions" if needed else "unnecessary_questions")


@answerer("cleaning.")
def cleaning_answers(env, w, pid, topic, ctx):
    person = w.s["people"][pid]
    adult = person.get("authority", 0) >= 1
    what = topic[len("cleaning."):]
    value = None
    needed = False
    if what.startswith("keep:"):
        eid = what[5:]
        rec = w.s["entities"].get(eid)
        if rec is None:
            value = "I don't know which thing you mean."
        else:
            disposition = rec.get("_disposition")
            needed = bool(rec.get("_ambiguous") or disposition == "keep" and not rec.get("_obvious"))
            owner = rec.get("owner")
            if not adult and owner not in (pid,):
                value = "I don't know, ask a grown-up."
                needed = False
            elif disposition == "keep":
                value = "keep it ({})".format(rec.get("_reason", "we still need that"))
            elif disposition in ("trash", "recycling", "compost", "hazardous", "yard_waste"):
                value = "that can go ({})".format(disposition)
            else:
                from .waste import acceptable
                primary, ok, issues = acceptable(w, eid)
                value = "that's {}".format("not trash" if primary == "keep" else "rubbish, it can go")
    elif what in ("rules", "waste_rules", "recycling_rules"):
        from .waste import rules
        r = rules(w)
        read = w.s.get("facts", {}).get("read", {})
        guide_read = any(w.s["entities"].get(e, {}).get("type") == "recycling_guide" for e in read)
        needed = not guide_read
        value = {k: r[k] for k in ("program", "accepted_resins", "glass", "cartons", "film", "bagged_recycling", "caps", "compost",
                                    "shredded_paper", "collection_day") if k in r}
    elif what.startswith("finish:"):
        eid = what[7:]
        rec = w.s["entities"].get(eid, {})
        hidden = "finish" in rec.get("secret_fields", []) or "_finish" in rec
        value = rec.get("_finish") or rec.get("finish") or rec.get("material") or "not sure"
        needed = hidden
    elif what.startswith("product:"):
        eid = what[8:]
        rec = w.s["entities"].get(eid, {})
        if rec.get("_decanted_by") == pid or adult and rec.get("_known_contents"):
            value = rec.get("_known_contents") or rec.get("product")
            needed = not rec.get("label_text")
        else:
            value = "I don't know what's in that bottle - don't use it."
            needed = not rec.get("label_text")
    elif what == "quiet_hours":
        value = S.household(w).get("quiet_hours") or [22.0, 7.0]
        needed = True
    elif what == "cloth_colors":
        value = S.household(w).get("cloth_colors") or {}
        needed = bool(value)
    else:
        return None
    _necessary(w, pid, topic, needed)
    facts = w.sec("facts")
    facts[topic] = copy.deepcopy(value)
    return {"topic": topic, "value": value, "by": pid}
