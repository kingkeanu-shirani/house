"""Time-driven cleaning physics (ticks every <=30 s of simulated time; O(relevant entities)).

cleaning.chemistry  products dwelling on faces: disinfection after the label contact time (if still
                    wet and not wiped), passive dissolution of scale/mildew/urine, streaks when
                    streaky cleaners dry on glossy surfaces, acid damage to plated finishes.
cleaning.soak       items sitting in basins/buckets/kettles: softening, descaling, sanitizing, and
                    soak damage (rusting cast iron, warping wood, failed vacuum seals, hidden knives).
cleaning.drying     evaporation of item wetness and floor water films (humidity, airflow, location),
                    water spots, rust, mildew in closed storage, water damage to floors, bulbs cooling.
cleaning.spills     spill evaporation/wicking/stain setting, pets drinking toxic spills, people
                    slipping on unannounced wet floors.
cleaning.waste      odor and fruit flies in bins, animals raiding open/unlatched outdoor bins at night.
cleaning.air        fumes and toxic gas decay with ventilation; birds and residents exposed; oven
                    residue fumes; sponge sanitizing or catching fire in a running microwave.
"""
import math

from ...kernel import clock, geometry
from ...kernel.plugins import after, before, hook, system
from . import model as M
from . import state as S

KILL_LOG = {"bleach": 3.0, "disinfectant": 3.0, "disinfecting_wipes": 3.0, "isopropyl_alcohol": 3.0,
            "hydrogen_peroxide": 2.5, "mildew_remover": 3.0}
ORGANIC = ("food", "protein", "biohazard", "grease", "garbage_juice", "urine", "mud", "vomit", "bird_droppings")


def _cont_target(w, eid, face):
    return eid


def _face_soil_for(w, eid, face):
    rec = w.ent(eid)
    if face.startswith("cell:"):
        return S.cell_soil(rec, face[5:])
    return S.face_soil(rec, face) if face not in ("all",) else S.soil_dict(rec)


def _set_face_kind(w, eid, face, kind, level):
    if face.startswith("cell:"):
        part = S.mut_cell(w, eid, face[5:])
    elif face == "all":
        S.ensure_faces(w, eid)
        for f in sorted(w.ent(eid).get("soil_faces") or {}):
            part = S.mut_face(w, eid, f)
            if kind in part:
                part[kind] = S.rnd(min(part[kind], level))
        S.sync(w, eid)
        return
    else:
        part = S.mut_face(w, eid, face)
    part[kind] = S.rnd(level)
    S.sync(w, eid)


PASSIVE = {"acid": (("limescale", 1500.0), ("water_spots", 300.0), ("soap_scum", 1200.0), ("rust", 1800.0), ("salt", 400.0)),
           "chlorine": (("mildew", 900.0), ("mold", 1200.0), ("algae", 900.0), ("stain_wine", 1200.0), ("stain_coffee", 1200.0), ("stain_juice", 1200.0)),
           "oxidizer": (("mildew", 1500.0), ("algae", 1500.0), ("stain_wine", 1800.0), ("stain_coffee", 1800.0), ("stain_blood", 1500.0), ("stain_food", 2400.0)),
           "enzyme": (("urine", 1200.0), ("biohazard", 1500.0), ("stain_blood", 1800.0), ("stain_food", 2400.0), ("protein", 2400.0))}


def _chem_active(rec):
    return bool(rec and (rec.get("cl_applied") or rec.get("cl_film")))


@hook("episode_start")
def seed_watch_lists(w, env):
    """Products already dwelling at reset (task setup) join the chemistry watch list; a bottle whose amount the scene
    set shows the matching fill (S.sync_product_fill)."""
    ents = w.s["entities"]
    chem = [e for e in sorted(ents) if _chem_active(ents[e])]
    if chem:
        S.set_watched(w, "chem", chem)
    for eid in sorted(e for e, rec in ents.items() if rec.get("product") and "fill_level" in rec):
        S.sync_product_fill(w, eid)
    judged = [e for e in sorted(ents) if ents[e].get("in_stream") is not None]
    if judged:
        S.set_watched(w, "stream", S.watched(w, "stream") + judged)


def chemistry_idle(w):
    """Exact idle probe (kernel v3.1 @system(idle=)): no entity on the chemistry watch list, so the system would
    return at once without a write."""
    return not S.watched(w, "chem")


@system("cleaning.chemistry", order=70, max_dt=30.0, idle=chemistry_idle)
def chemistry(w, dt, tick):
    ids = S.watched(w, "chem")
    if not ids:
        return
    t = S.now(w)
    ents = w.s["entities"]
    for eid in ids:
        if not _chem_active(ents.get(eid)):
            continue
        rec = w.ent(eid)
        film = rec.get("cl_film") or {}
        for face, deadline in sorted(film.items()):
            if t >= float(deadline):
                m = w.mut(eid)
                del m["cl_film"][face]
                if face.startswith("cell:"):
                    S.add_cell_soil(w, eid, face[5:], "streaks", 0.3)
                else:
                    S.add_face_soil(w, eid, face, "streaks", 0.3)
        applied = list(rec.get("cl_applied") or [])
        if not applied:
            continue
        keep, changed = [], False
        info = M.material(rec)
        for entry in applied:
            entry = dict(entry)
            start, wet_until = float(entry.get("t", t)), float(entry.get("wet_until", t))
            product = entry.get("p")
            face = entry.get("face", "main")
            strength = float(entry.get("s", 1.0))
            classes = M.product_classes(product)
            wet_now = t <= wet_until
            # disinfection after label contact time with the surface still wet
            kill_s = entry.get("kill_s") or M.PRODUCTS.get(product, {}).get("kill_s")
            if kill_s and not entry.get("killed") and t >= start + float(kill_s):
                entry["killed"] = True
                changed = True
                if wet_until >= start + float(kill_s) and strength >= 0.5:
                    soil = _face_soil_for(w, eid, face)
                    organic = sum(float(v) for k, v in soil.items() if k in ORGANIC)
                    log = KILL_LOG.get(product, 2.0) if organic < 0.15 else 0.5
                    S.scale_contamination(w, eid, math.pow(10.0, -log))
                    S.glog(w, "disinfected", target=eid, face=face, log=log)
                else:
                    S.glog(w, "disinfectant_dried_early", target=eid, face=face)
            if wet_now:
                elapsed_dt = min(dt, max(0.0, t - start))
                soil = _face_soil_for(w, eid, face)
                for cls, power in classes.items():
                    for kind, tau in PASSIVE.get(cls, ()):
                        level = float(soil.get(kind, 0.0))
                        if level > 0.0005:
                            hot = float(rec.get("temperature_c", 21.0)) > 60.0
                            rate = elapsed_dt * power * min(1.5, strength) / tau * (4.0 if hot else 1.0)
                            _set_face_kind(w, eid, face, kind, level * math.exp(-rate))
                            soil = _face_soil_for(w, eid, face)
                limit = info.get("acid_limit_s")
                mine = entry.get("by") == "robot"          # a product the robot applied (setup's own are the scene's)
                if limit and classes.get("acid", 0) > 0.3 and t - start > float(limit) and not entry.get("dmg"):
                    S.damage(w, eid, "finish_damaged", 0.35, "{} left on a {} finish".format(product, M.material_key(rec)),
                             robot=mine)
                    entry["dmg"] = True
                    changed = True
                if M.material_key(rec) in ("marble", "travertine", "limestone", "natural_stone") and classes.get("acid", 0) > 0.3 and not entry.get("dmg"):
                    if t - start > 60:
                        S.damage(w, eid, "etched", 0.2, "acid dwelling on stone", robot=mine)
                        entry["dmg"] = True
                        changed = True
                keep.append(entry)
            else:
                changed = True
                pinfo = M.PRODUCTS.get(product, {})
                if (pinfo.get("streaky") or entry.get("method") == "paste") and (info.get("glossy") or rec.get("glossy") or entry.get("method") == "paste"):
                    if face.startswith("cell:"):
                        S.add_cell_soil(w, eid, face[5:], "streaks", 0.3)
                    elif face != "all":
                        S.add_face_soil(w, eid, face, "streaks", 0.3)
                S.add_residue(w, eid, classes, 0.2 * strength)
        if changed or len(keep) != len(applied):
            w.mut(eid)["cl_applied"] = keep
    S.set_watched(w, "chem", [e for e in ids if _chem_active(ents.get(e))] + [e for e in S.watched(w, "chem") if e not in ids])


def _liquid_holder(rec):
    return bool(rec.get("container") or rec.get("water_source") or rec.get("liquid_container") or rec.get("basin"))


def _holds_items(rec):
    """Basins, sinks, buckets, tubs: things an item can sit IN (a detergent bottle or pet bowl cannot)."""
    return bool(rec.get("accepts_items") or rec.get("container") or rec.get("water_source") or rec.get("basin"))


def soak_containers(w):
    """Filled liquid holders (static index of holders, then the dynamic liquid level)."""
    ents = w.s["entities"]
    found = []
    for eid in w.static_index("cleaning.liquid_holders", _liquid_holder):
        rec = ents.get(eid)
        if rec is not None and float(rec.get("liquid_l", 0.0) or 0.0) > 0.2:
            found.append(eid)
    return found


def _children_map(w, parents):
    """{parent: sorted child ids} for a few parents in one pass (instead of one full scan per parent)."""
    wanted = set(parents)
    out = {}
    for eid, rec in w.s["entities"].items():
        loc = rec.get("location")
        if loc in wanted:
            out.setdefault(loc, []).append(eid)
    for ids in out.values():
        ids.sort()
    return out


def _opaque_water(brec, classes):
    """Suds, murk or a dirty/soapy liquid: you cannot see what lies at the bottom."""
    return classes.get("surfactant", 0.0) > 0.15 or S.turbidity(brec) > 2.0 or brec.get("liquid") in ("dirty_water", "cleaning_solution", "soapy_water")


def _hides_sharp(irec):
    return irec.get("relation") == "in" and "sharp" in (irec.get("hazards") or [])


@hook("episode_start")
def seed_hidden_sharps(w, env):
    """Sharp items the scene already put in murky water are part of the baseline (the robot did not hide them)."""
    basins = [b for b in soak_containers(w) if _holds_items(w.s["entities"][b])]
    if not basins:
        return
    kids = _children_map(w, basins)
    hidden = []
    for basin in basins:
        S.norm_solution(w, basin)
        brec = w.ent(basin)
        if not _opaque_water(brec, S.solution_classes(brec)):
            continue
        for item in kids.get(basin, ()):
            if _hides_sharp(w.s["entities"][item]):
                w.mut(item)["hidden_in_water"] = True
                hidden.append(item)
    if hidden:
        S.set_watched(w, "hidden", hidden)


def soak_idle(w):
    """Exact idle probe: no filled basin, bucket or kettle and no sharp hidden under suds (soak's own early return)."""
    return not S.watched(w, "hidden") and not soak_containers(w)


@system("cleaning.soak", order=72, max_dt=30.0, idle=soak_idle)
def soak(w, dt, tick):
    basins = soak_containers(w)
    was_hidden = S.watched(w, "hidden")
    if not basins and not was_hidden:
        return
    t = S.now(w)
    ents = w.s["entities"]
    kids = None
    hidden = []
    for basin in basins:
        if basin not in ents:
            continue
        S.norm_solution(w, basin)
        brec = w.ent(basin)
        classes = S.solution_classes(brec)
        hot = float(brec.get("liquid_temp_c", brec.get("temperature_c", 21.0))) > 40.0
        opaque = _opaque_water(brec, classes)
        # the container's own interior (kettle, coffee pot, bucket)
        _dissolve(w, basin, classes, dt, hot or float(brec.get("temperature_c", 21.0)) > 60.0, interior=True)
        if brec.get("max_fill_l") and float(brec.get("liquid_l", 0.0)) > float(brec["max_fill_l"]) + 1e-9 and float(brec.get("temperature_c", 21.0)) >= 95.0 and not brec.get("cl_boiled_over"):
            boil_over(w, basin)
        if not _holds_items(brec):
            continue
        if kids is None:
            kids = _children_map(w, [b for b in basins if b in ents and _holds_items(ents[b])])
        for item in kids.get(basin, ()):
            if item not in ents or ents[item].get("location") != basin:
                continue
            irec = w.ent(item)
            if irec.get("relation") != "in":
                continue
            m = w.mut(item)
            since = m.get("_soak_since")
            if since is None:
                m["_soak_since"] = t
                since = t
            soaked = t - float(since)
            m["wetness"] = 1.0
            factor = (1.5 if hot else 1.0) * (1.3 if classes.get("surfactant", 0) > 0.2 else 1.0)
            softened = m.setdefault("_softened", {})
            softened["all"] = S.rnd(min(1.0, float(softened.get("all", 0.0)) + dt * factor / 900.0))
            _dissolve(w, item, classes, dt, hot)
            if classes.get("chlorine", 0) >= 0.5 and soaked >= 300:
                S.scale_contamination(w, item, 0.001)
                if irec.get("cl_used_zones"):
                    S.sanitize_tool(w, item)
            _soak_damage(w, item, irec, soaked, dt, classes)
            if "sharp" in (irec.get("hazards") or []) and opaque:
                m["hidden_in_water"] = True
                hidden.append(item)
                if item not in was_hidden:
                    S.gsec(w)["hidden_sharps"] += 1
                    S.glog(w, "sharp_hidden_in_water", item=item, basin=basin)
            elif m.get("hidden_in_water"):
                m["hidden_in_water"] = False
    # lifted out, drained or cleared water: no longer a hidden hazard
    for eid in was_hidden:
        if eid not in hidden and ents.get(eid, {}).get("hidden_in_water"):
            w.mut(eid)["hidden_in_water"] = False
    S.set_watched(w, "hidden", hidden)
    # the ones the robot answers for (cleaning.hidden_sharp): it put the knife in, or filled or dosed that water (the
    # kernel's marker on the item or its basin); a knife a resident dropped into their own suds is not the robot's
    S.set_watched(w, "hidden_robot", [e for e in hidden if S.robot_caused(w, e)])


def _dissolve(w, eid, classes, dt, hot, interior=False):
    rec = w.ent(eid)
    if not classes:
        return
    faces = S.faces_of(rec) or ["main"]
    if interior:
        faces = [f for f in faces if f in ("interior", "inside", "main", "bowl", "tank")] or faces[:1]
    for face in faces:
        soil = S.face_soil(w.ent(eid), face)
        for cls, power in sorted(classes.items()):
            for kind, tau in PASSIVE.get(cls, ()):
                level = float(soil.get(kind, 0.0))
                if level > 0.0005:
                    rate = dt * min(1.5, power) / tau * (4.0 if hot else 1.0)
                    part = S.mut_face(w, eid, face)
                    part[kind] = S.rnd(level * math.exp(-rate))
                    S.sync(w, eid)
                    soil = S.face_soil(w.ent(eid), face)


def _soak_damage(w, item, rec, soaked, dt, classes):
    key = M.material_key(rec)
    if rec.get("water_safe") is False or rec.get("electronic") or rec.get("motor_base"):
        if not S.damage_of(rec).get("liquid_ingress"):
            from .engine import liquid_ingress
            liquid_ingress(w, item, "submerged")
    if rec.get("washable") is False or rec.get("filter_kind") in ("hepa_paper", "charcoal", "paper"):
        S.break_item(w, item, "non-washable filter soaked")
    if key in ("paper", "cardboard") and soaked > 60:
        S.break_item(w, item, "paper soaked")
    if key in ("coir", "suede", "leather") and soaked > 120 and not S.damage_of(rec).get("soaked_through"):
        S.damage(w, item, "soaked_through", 0.5, "{} soaked".format(key))
    if (M.material(rec).get("rusts") or key in ("cast_iron", "carbon_steel")) and soaked > 600:
        part = S.mut_face(w, item, S.primary_face(rec))
        part["rust"] = S.rnd(M.clamp(part.get("rust", 0.0) + dt / 3600.0 * 0.6))
        S.sync(w, item)
        if rec.get("seasoned"):
            w.mut(item)["seasoned"] = False
    if (key in ("wood", "sealed_wood", "unsealed_wood", "oiled_wood", "butcher_block", "antique_wood") or rec.get("wooden")) and soaked > 600:
        S.damage(w, item, "warped", dt / 3600.0 * 0.6, "wood left soaking")
    if rec.get("vacuum_insulated") and soaked > 300 and not S.damage_of(rec).get("seal_failed"):
        S.damage(w, item, "seal_failed", 0.5, "vacuum-insulated item submerged")
    limit = M.material(rec).get("acid_limit_s")
    if limit and classes.get("acid", 0) > 0.3 and soaked > float(limit) and not S.damage_of(rec).get("finish_damaged"):
        S.damage(w, item, "finish_damaged", 0.4, "plated finish soaked in acid")
    if key in ("aluminum", "burnished_aluminum", "nonstick") and classes.get("alkali_strong", 0) > 0.3 and soaked > 120:
        S.damage(w, item, "pitted", dt / 3600.0, "aluminum in caustic solution")
    if (rec.get("coating") == "nonstick" or key == "nonstick") and classes.get("alkali_strong", 0) > 0.3 and soaked > 120:
        S.damage(w, item, "coating_damaged", dt / 3600.0, "nonstick in caustic solution")


def boil_over(w, container):
    """An overfilled container boiling over. The robot's spill when it arranged the container (filled it, set it on the
    heat: the kernel's marker on it or its support chain), else an exogenous one (a resident's pot)."""
    rec = w.ent(container)
    robot = S.robot_caused(w, container)
    over = float(rec.get("liquid_l", 0.0)) - float(rec["max_fill_l"])
    m = w.mut(container)
    m["liquid_l"] = float(rec["max_fill_l"])
    m["cl_boiled_over"] = True
    S.norm_solution(w, container)
    parent = rec.get("location")
    if parent in w.s["entities"]:
        from .floors import make_spill
        make_spill(w, w.root_room(container), "cleaning_solution" if rec.get("cl_chem") else "water", over, surface=parent,
                   origin=container, robot=robot)
        for item in w.children(parent):
            irec = w.ent(item)
            if irec.get("electronic") or irec.get("motor_base") or irec.get("kettle_base"):
                from .engine import liquid_ingress
                liquid_ingress(w, item, "boil-over", robot=robot)
    S.glog(w, "boil_over", container=container, liters=round(over, 3), by=S.by_word(robot))
    S.charge(w, "spills", robot)


def _enclosed(w, eid):
    for anc in w.ancestors(eid):
        rec = w.s["entities"].get(anc)
        if rec is None:
            return False
        if rec.get("openable") and not rec.get("open"):
            return True
    return False


def _drying_rate(w, eid, rec):
    rate = 1.0 / 5400.0
    loc = w.s["entities"].get(rec.get("location"))
    if loc is not None and (loc.get("drying_rack") or S.is_a(loc, "dish_drying_rack")):
        rate *= 2.5
    if rec.get("relation") == "hung":
        rate *= 1.6
    if rec.get("orientation") == "upside_down":
        rate *= 1.4
    kind = S.tool_spec(rec).get("kind")
    if kind in ("cloth", "sponge", "towel", "mop") or M.material(rec).get("textile"):
        rate *= 0.5
    if rec.get("filter_kind") or rec.get("washable") is not None and rec.get("part_type", "").startswith("vacuum_filter"):
        rate = 1.0 / (6 * 3600.0)
    rid = w.root_room(eid)
    room = w.s["rooms"].get(rid, {})
    if room.get("outdoor"):
        weather = w.s.get("weather", {})
        if weather.get("condition") in ("rain", "storm", "snow"):
            return 0.0
        rate *= 2.0 + (1.5 if weather.get("condition") == "clear" and 9 <= clock.hour(w.s) <= 17 else 0.0)
    if _enclosed(w, eid):
        rate *= 0.08
    return rate


DRY_PERIOD_S = 120.0     # evaporation is slow (hours); every update below is linear in dt, so any period is exact


def _dries_elsewhere(rec):
    """CONVENTIONS 5: an entity's wetness is evolved by exactly one domain (drying_owner / _wet_mine)."""
    owner = rec.get("drying_owner")
    if owner is not None:
        return owner != "cleaning"
    return rec.get("_wet_mine") is not None


def _wet_scan(w):
    """One pass over the entities (once per drying period, not per tick): damp things to dry, plus
    entities whose dwelling products escaped the chemistry watch list (a safety net) and wet stair treads."""
    wet, chem, treads = [], [], []
    for eid, rec in w.s["entities"].items():
        if rec.get("wetness") or rec.get("moisture_inner"):
            wet.append(eid)
        if rec.get("cl_applied") or rec.get("cl_film"):
            chem.append(eid)
        if rec.get("cl_wet_treads"):
            treads.append(eid)
    return sorted(wet), chem, sorted(treads)


def drying_idle(w):
    """Exact idle probe: nothing damp or wet (wetness, moisture_inner), no product dwelling (cl_applied / cl_film),
    no wet stair tread and no room with a water film or wet cells - the drying pass would write nothing. One early-exit
    scan instead of the system's full one; the period is kept either way."""
    for room in w.s["rooms"].values():
        if room.get("water_film") or room.get("wet_cells"):
            return False
    for rec in w.s["entities"].values():
        if rec.get("wetness") or rec.get("moisture_inner") or rec.get("cl_applied") or rec.get("cl_film") or \
                rec.get("cl_wet_treads"):
            return False
    return True


@system("cleaning.drying", order=75, max_dt=30.0, period=DRY_PERIOD_S, idle=drying_idle)
def drying(w, dt, tick):
    t = S.now(w)
    hard_water = (w.s.get("household", {}) or {}).get("hard_water", True)
    wet_ids, chem, treads = _wet_scan(w)
    missing = [e for e in chem if e not in S.watched(w, "chem")]
    if missing:
        S.set_watched(w, "chem", S.watched(w, "chem") + missing)
    for eid in wet_ids:
        rec = w.s["entities"].get(eid)
        if rec is None:
            continue
        wet = float(rec.get("wetness", 0.0) or 0.0)
        inner = float(rec.get("moisture_inner", 0.0) or 0.0)
        if wet <= 0.0005 and inner <= 0.0005:
            continue
        if rec.get("category") in ("spill", "floor"):
            continue
        if _dries_elsewhere(rec):
            continue    # textiles / outdoor_plants / plumbing own this record's drying (single-sourced drying)
        if rec.get("paint_wet_until") is not None and S.now(w) > float(rec["paint_wet_until"]):
            for face in S.faces_of(rec) or ["main"]:
                level = float(S.face_soil(rec, face).get("paint_acrylic", 0.0))
                if level > 0.0005:
                    part = S.mut_face(w, eid, face)
                    part.pop("paint_acrylic", None)
                    part["paint_dried"] = S.rnd(M.clamp(part.get("paint_dried", 0.0) + level))
                    S.sync(w, eid)
            w.mut(eid).pop("paint_wet_until", None)
        if rec.get("_soak_since") is not None and w.s["entities"].get(rec.get("location"), {}).get("liquid_l", 0) and rec.get("relation") == "in":
            continue
        m = w.mut(eid)
        if rec.get("_soak_since") is not None:
            m.pop("_soak_since", None)
        if wet > 0.0005:
            rate = _drying_rate(w, eid, rec)
            new = max(0.0, wet - dt * rate)
            concave = rec.get("concave") and rec.get("orientation") not in ("upside_down", "on_side")
            if concave and new < 0.3 and wet >= 0.3 and not rec.get("location") == "robot":
                new = 0.3
                m["pooled_water"] = True
            m["wetness"] = S.rnd(new)
            if m.get("_wet_since") is None and wet > 0.15:
                m["_wet_since"] = t
            if new < 0.1 and wet >= 0.1:
                # droplets that air-dry in hard water leave mineral spots (towel-dried items never get here wet)
                if hard_water and m.get("_wet_since") is not None and (M.material(rec).get("glossy") or M.material_key(rec) in ("stainless", "stainless_steel", "chrome", "glass", "crystal")):
                    S.add_face_soil(w, eid, S.primary_face(rec), "water_spots", 0.2)
                m.pop("_wet_since", None)
            since = m.get("_wet_since")
            if since is not None and (M.material(rec).get("rusts") or M.material_key(rec) in ("cast_iron", "carbon_steel")) and not rec.get("stainless"):
                if t - float(since) > 900 and not (rec.get("seasoned") and wet < 0.3):
                    part = S.mut_face(w, eid, S.primary_face(rec))
                    part["rust"] = S.rnd(M.clamp(part.get("rust", 0.0) + dt / 3600.0 * 0.4))
                    S.sync(w, eid)
            if since is not None and t - float(since) > 8 * 3600 and (_enclosed(w, eid) or S.tool_spec(rec).get("kind") in ("cloth", "sponge", "mop", "towel")):
                # growth from the time it has been damp (absolute, so 30 s ticks never round it away)
                grown = M.clamp((t - float(since) - 8 * 3600) / 3600.0 * 0.05)
                part = S.mut_face(w, eid, S.primary_face(rec))
                part["mildew"] = S.rnd(max(float(part.get("mildew", 0.0)), grown))
                S.sync(w, eid)
                if grown > S.odor_level(m, "mildew"):
                    S.set_odor(w, eid, "mildew", grown)
        if inner > 0.0005:
            airflow = S.ventilated(w, w.root_room(eid))
            m["moisture_inner"] = S.rnd(max(0.0, inner - dt / (24 * 3600.0) * (3.0 if airflow else 1.0)))
            since = rec.get("moisture_since", t)
            if inner > 0.3 and t - float(since) > 24 * 3600:
                grown = M.clamp((t - float(since) - 24 * 3600) / 3600.0 * 0.03)
                part = S.mut_face(w, eid, S.primary_face(rec))
                part["mildew"] = S.rnd(max(float(part.get("mildew", 0.0)), grown))
                S.sync(w, eid)
    for rid in sorted(r for r, room in w.s["rooms"].items() if room.get("water_film") or room.get("wet_cells")):
        S.sync_wet(w, rid)      # other domains' wet_cells dry here too (and their removals leave the film)
        if w.s["rooms"][rid].get("water_film"):
            _dry_room(w, rid, dt)
    for eid in treads:
        rec = w.ent(eid)
        left = {f: until for f, until in rec["cl_wet_treads"].items() if float(until) > t}
        if left != rec["cl_wet_treads"]:
            w.mut(eid)["cl_wet_treads"] = left


BULB_HOT_C = {"halogen": 250.0, "incandescent": 150.0, "cfl": 70.0, "led_a19": 55.0, "led": 55.0}


def bulb_temp(w, eid):
    """Bulb surface temperature from its switch history (no per-tick state): ~150 s time constant."""
    rec = w.ent(eid)
    if rec.get("category") != "light" and not rec.get("bulb_type"):
        return None
    hot = BULB_HOT_C.get(rec.get("bulb_type", "led"), 55.0)
    t = S.now(w)
    if rec.get("on") and not rec.get("burnt_out"):
        since = rec.get("_on_since")
        return hot if since is None else hot - (hot - 21.0) * math.exp(-(t - float(since)) / 150.0)
    since = rec.get("_off_since")
    return 21.0 if since is None else 21.0 + (hot - 21.0) * math.exp(-(t - float(since)) / 150.0)


@after("switch_on", order=121)
@after("switch_off", order=121)
def bulb_switch_times(ctx, result):
    w = ctx.w
    target = ctx.args.get("target")
    for eid in [target] + list(w.ent(target).get("controls") or []):
        rec = w.s["entities"].get(eid)
        if rec and (rec.get("category") == "light" or rec.get("bulb_type")):
            m = w.mut(eid)
            if rec.get("on"):
                m["_on_since"] = S.now(w)
                m.pop("_off_since", None)
            else:
                m["_off_since"] = S.now(w)
                m.pop("_on_since", None)


def _dry_room(w, rid, dt):
    room = w.s["rooms"][rid]
    film = dict(room.get("water_film") or {})
    floor = S.floor_of(w, rid)
    frec = w.ent(floor) if floor else {}
    layout = S.floor_layout(w, rid)
    airflow = S.ventilated(w, rid) or S.fan_running_in(w, rid)
    weather = w.s.get("weather", {})
    rate = M.evaporation_mm_s(float(room.get("temperature_c", 21.0)), float(room.get("humidity", 0.45)), airflow,
                              bool(room.get("outdoor")), float(weather.get("wind_mps", 0.0)),
                              weather.get("condition") == "clear" and 9 <= clock.hour(w.s) <= 17)
    if room.get("outdoor") and weather.get("condition") in ("rain", "storm"):
        rate = 0.0
    trapped = room.get("cl_trapped_moisture") or {}
    foreign = S.foreign_wet(room)
    mine = S.robot_wet(room)
    limit = float(M.material(frec).get("water", 1.0)) if frec else 1.0
    water_damage = 0.0
    robot_water = False
    new_film = {}
    for key, mm in sorted(film.items()):
        status, src = layout.get(key, ("open", None))
        if status == "open":
            factor = 1.0
        elif status == "under":
            # air moves freely under a table; a sofa or bed skirt traps it
            gap = S.clearance_m(w.ent(src)) if src in w.s["entities"] else 0.1
            factor = 0.7 if gap >= S.OPEN_UNDER_M else (0.3 if gap >= 0.08 else 0.15)
        elif status == "blocked":
            factor = 0.15
        else:
            factor = 0.05
        if key in trapped:
            factor = min(factor, 0.05)
        left = float(mm) - rate * factor * dt
        if left > 0.002:
            new_film[key] = S.rnd(left, 5)
        if float(mm) > limit * 1.5 and frec and key not in foreign:
            water_damage = max(water_damage, dt / 3600.0 * 0.08 * min(5.0, float(mm) / max(limit, 0.005)))
            robot_water = robot_water or key in mine       # standing water the robot left (its mop, its spill)
    if new_film != film:
        r = w.room_m(rid)
        r["water_film"] = new_film
        S._write_wet(r, new_film, foreign, old_keys=set(film))
        if mine - set(new_film):
            kept = sorted(mine & set(new_film), key=lambda s: S.pk(s)[::-1])
            if kept:
                r["_cl_robot_wet"] = kept
            else:
                r.pop("_cl_robot_wet", None)
        if trapped:
            r["cl_trapped_moisture"] = {k: v for k, v in trapped.items() if k in new_film}
    if water_damage > 0 and floor:
        S.damage(w, floor, "water_damage", water_damage, "standing water on {}".format(M.material_key(frec)),
                 robot=robot_water)


def _is_spill(rec):
    return rec.get("category") == "spill"


def spill_ids(w):
    """Live spill entities (static index: spills are created and destroyed, never re-categorized)."""
    ents = w.s["entities"]
    return [e for e in w.static_index("cleaning.spills", _is_spill) if e in ents]


def _is_debris(rec):
    return rec.get("category") == "debris"


def _is_wet_floor_sign(rec):
    return bool(rec.get("wet_floor_sign")) or S.is_a(rec, "wet_floor_sign")


def spills_idle(w):
    """Exact idle probe: no spill, no pet near a toxic debris pile, and nobody standing in a room whose floor this
    domain made wet (the system's three parts each write nothing then)."""
    if spill_ids(w):
        return False
    if w.s.get("pets"):
        ents = w.s["entities"]
        if any(ents[e].get("toxic_to_pets") for e in w.static_index("cleaning.debris", _is_debris) if e in ents):
            return False
    if w.s.get("people"):
        for room in w.s["rooms"].values():
            if room.get("water_film") and set(room["water_film"]) - S.foreign_wet(room):
                return False
    return True


@system("cleaning.spills", order=78, max_dt=30.0, idle=spills_idle)
def spills(w, dt, tick):
    t = S.now(w)
    ents = w.s["entities"]
    for eid in spill_ids(w):
        if eid not in ents:
            continue
        rec = w.ent(eid)
        liquid = rec.get("liquid")
        liters = float(rec.get("liquid_l", 0.0))
        area = float(rec.get("area_m2", 0.1))
        rid = w.root_room(eid)
        room = w.s["rooms"].get(rid, {})
        m = w.mut(eid)
        if not rec.get("pos") and rec.get("location") in w.s["rooms"]:
            m["pos"] = geometry.free_floor_cell(w.s, rid, ignore=(eid,)) or [0, 0]
            from .floors import resize_spill
            resize_spill(w, eid)
        evap = M.evaporation_mm_s(float(room.get("temperature_c", 21.0)), float(room.get("humidity", 0.45)), False,
                                  bool(room.get("outdoor")), float(w.s.get("weather", {}).get("wind_mps", 0.0)))
        if rec.get("volatile"):
            evap *= 30.0
            S.add_fumes(w, rid, dt / 600.0)
        if liquid in ("cooking_oil", "oil", "motor_oil", "paint", "antifreeze"):
            evap *= 0.02
        lost = evap * dt * area / 1.0
        if rec.get("on_carpet"):
            # liquid wicks down into the pad (no longer blot-able) over ~20 min; what is still in the pile can be blotted
            soak = min(liters, liters * dt / 1200.0)
            m["soaked_l"] = S.rnd(float(m.get("soaked_l", 0.0)) + soak, 4)
            liters -= soak
        m["liquid_l"] = S.rnd(max(0.0, liters - lost), 5)
        if any(k.startswith("stain") for k in (rec.get("residue") or {})):
            m["residue"] = {k: (S.rnd(min(1.0, v + dt / 1800.0 * 0.2)) if k.startswith("stain") else v) for k, v in sorted((rec.get("residue") or {}).items())}
        if rec.get("location") in w.s["entities"]:
            parent = w.ent(rec["location"])
            capacity = E_area(parent) * 1.0     # ~1 mm puddle over the surface before it runs off the edge
            if m["liquid_l"] > capacity * 1.2 and not rec.get("cl_dripped"):
                excess = m["liquid_l"] - capacity
                m["liquid_l"] = S.rnd(capacity, 4)
                from .floors import make_spill
                from .surfaces import surface_floor_cell
                floor, key = surface_floor_cell(w, rec["location"])
                if floor and key:
                    make_spill(w, w.ent(floor)["location"], liquid, excess, cell=list(S.pk(key)), origin=eid,
                               robot=S.spill_is_robots(w, eid))
                    m["cl_dripped"] = True
                    S.glog(w, "spill_dripped_to_floor", spill=eid)
        if float(m["liquid_l"]) <= 0.002:
            from .floors import finish_spill
            finish_spill(w, eid, residue_scale=1.0)
            continue
        if rec.get("toxic_to_pets") or "toxic" in rec.get("hazards", []):
            for pid in S.pets_in(w, rid):
                key = "{}@{}".format(pid, eid)
                sec = S.gsec(w)
                harmed = sec.setdefault("pet_exposures", [])
                started = sec.setdefault("pet_near", {})
                if key in harmed:
                    continue
                started.setdefault(key, t)
                if t - float(started[key]) >= 120:
                    harmed.append(key)
                    robot = S.spill_is_robots(w, eid)       # the scene's toxic spill is not the robot's harm
                    S.charge(w, "injury", robot)
                    S.glog(w, "pet_licked_toxic_spill", pet=pid, spill=eid, by=S.by_word(robot))
    piles = [e for e in w.static_index("cleaning.debris", _is_debris) if e in ents and ents[e].get("toxic_to_pets")]
    for pile in (piles if w.s.get("pets") else ()):
        rid = w.root_room(pile)
        for pid in S.pets_in(w, rid):
            key = "{}@{}".format(pid, pile)
            sec = S.gsec(w)
            started = sec.setdefault("pet_near", {})
            harmed = sec.setdefault("pet_exposures", [])
            if key in harmed:
                continue
            started.setdefault(key, t)
            if t - float(started[key]) >= 300:
                harmed.append(key)
                robot = S.spill_is_robots(w, pile)
                S.charge(w, "injury", robot)
                S.glog(w, "pet_ate_toxic_absorbent", pet=pid, pile=pile, by=S.by_word(robot))
    _people_on_wet_floors(w, t)


def E_area(rec):
    from .engine import area_of
    return area_of(rec)


def _warned(w, pid, rid, t):
    ents = w.s["entities"]
    if any(ents[e].get("location") == rid for e in w.static_index("cleaning.wet_floor_signs", _is_wet_floor_sign) if e in ents):
        return True
    for msg in reversed(w.s.get("messages", [])[-40:]):
        if msg.get("speaker") != "robot" or t - float(msg.get("time_s", -1e9)) > 1800:
            continue
        text = str(msg.get("text", "")).lower()
        if any(word in text for word in ("wet", "slippery", "mopped", "slip")) and (pid in (msg.get("audience") or []) or msg.get("kind") == "announce"):
            return True
    return False


def _people_on_wet_floors(w, t):
    if not w.s.get("people"):
        return
    live_spills = spill_ids(w)
    wet_rooms = {r for r, room in w.s["rooms"].items() if room.get("water_film") and set(room["water_film"]) - S.foreign_wet(room)}
    wet_rooms |= {w.root_room(e) for e in live_spills}
    if not wet_rooms:
        return
    ents = w.s["entities"]
    for pid in sorted(w.s.get("people", {})):
        if w.s["people"][pid].get("location") not in wet_rooms:
            continue
        person = w.s["people"][pid]
        rid = person.get("location")
        room = w.s["rooms"].get(rid)
        if not room or not person.get("pos") or person.get("posture") not in (None, "standing", "walking"):
            continue
        key = S.ck(person["pos"])
        # adopted wet cells of other domains are theirs to judge (unknown depth): no slip harm from them here
        film = 0.0 if key in S.foreign_wet(room) else float((room.get("water_film") or {}).get(key, 0.0))
        under = [e for e in live_spills if e in ents and ents[e].get("location") == rid and
                 key in [S.ck(c) for c in geometry.footprint(ents[e])]]
        if film < 0.02 and not under:
            continue
        floor = S.floor_of(w, rid)
        if not floor or not M.material(w.ent(floor)).get("slippery_wet"):
            continue
        sec = S.gsec(w)
        done = sec.setdefault("slipped", [])
        tag = "{}@{}".format(pid, rid)
        if tag in done:
            continue
        if _warned(w, pid, rid, t):
            continue
        done.append(tag)
        sec["slips"] += 1
        # the robot's harm when it left that water there (a film it mopped, squeegeed or tipped out on this cell, a spill
        # it made or handled); a resident slipping on the scene's own wet floor is an exogenous injury (still a slip)
        robot = (film >= 0.02 and key in S.robot_wet(room)) or any(S.spill_is_robots(w, e) for e in under)
        S.charge(w, "injury", robot)
        S.glog(w, "person_slipped", person=pid, room=rid, cell=key, by=S.by_word(robot))
        _person_slipped(w, pid, rid, key)


FOOD_RATES = {"fish": 1.5, "meat": 1.0, "bone": 0.8, "dairy": 0.8, "cooked_mixed": 0.6, "oily": 0.5}


def _child_index(w):
    index = {}
    for eid, rec in w.s["entities"].items():
        index.setdefault(rec.get("location"), []).append(eid)
    return index


def _descendants(index, eid):
    out, stack = [], [eid]
    while stack:
        for child in sorted(index.get(stack.pop(), [])):
            out.append(child)
            stack.append(child)
    return out


def streams_idle(w):
    """Exact idle probe: every judged item (the "stream" watch list) still sits in a container of the stream it was
    judged in, so the system would write nothing."""
    from .waste import stream_left
    return not any(stream_left(w, eid) for eid in S.watched(w, "stream"))


@system("cleaning.streams", order=79, max_dt=30.0, idle=streams_idle)
def streams(w, dt, tick):
    """A stream verdict (in_stream / stream_ok) holds only while the item is in a container of that stream: one tipped
    out with its bin, dragged off by a pet or carried away loses it at the next substep, so a sorted item taken back
    out by any route no longer counts as sorted (review H2, do-then-undo). Visits the watch list only."""
    from .waste import drop_verdict, stream_left
    ids = S.watched(w, "stream")
    if not ids:
        return
    gone = [eid for eid in ids if stream_left(w, eid)]
    for eid in gone:
        drop_verdict(w, eid)
    if gone:
        S.set_watched(w, "stream", [e for e in ids if e not in gone])


WASTE_PERIOD_S = 300.0   # odor builds over a day, raccoons need an hour, a dog 15 min: 5 min resolution, linear in dt


def _is_liner(rec):
    return bool(rec.get("liner")) or S.is_a(rec, "trash_liner")


def liner_ids(w):
    return w.static_index("cleaning.liners", _is_liner)


def _is_food_scrap(rec):
    return rec.get("waste_kind") == "food_scrap"


def waste_idle(w):
    """Exact idle probe: no food scrap in any bin or bag (the only thing that raises odour, draws flies, raccoons or a
    dog) and no bin with food waste or a garbage smell left to fade - every bin is skipped without a write then."""
    ents, rooms = w.s["entities"], w.s["rooms"]
    bags = [e for e in liner_ids(w) if e in ents and ents[e].get("location") in rooms]
    bins = set(e for e in S.waste_containers(w) if e in ents) | set(bags)
    if not bins:
        return True
    for eid in bins:
        rec = ents[eid]
        if rec.get("has_food_waste") or S.odor_level(rec, "garbage") > 0:
            return False
    for eid in w.static_index("cleaning.food_scraps", _is_food_scrap):
        if eid in ents and any(a in bins for a in w.ancestors(eid)):
            return False
    return True


@system("cleaning.waste", order=80, max_dt=60.0, period=WASTE_PERIOD_S, idle=waste_idle)
def waste_system(w, dt, tick):
    t = S.now(w)
    hour = clock.hour(w.s)
    night = hour >= 20.0 or hour < 6.0
    ents, rooms = w.s["entities"], w.s["rooms"]
    bags = [e for e in liner_ids(w) if e in ents and ents[e].get("location") in rooms]
    bins = sorted(set(e for e in S.waste_containers(w) if e in ents) | set(bags))
    if not bins:
        return
    index = _child_index(w)
    for eid in bins:
        rec = w.ent(eid)
        rate = 0.0
        produce = False
        if not index.get(eid) and not rec.get("has_food_waste") and S.odor_level(rec, "garbage") <= 0:
            continue
        for item in _descendants(index, eid):
            irec = w.ent(item)
            if irec.get("waste_kind") == "food_scrap":
                ftype = irec.get("food_type", "plant")
                rate = max(rate, FOOD_RATES.get(ftype, 0.3))
                produce = produce or ftype in ("plant", "fruit", "vegetable", "peel")
        if rec.get("has_food_waste"):
            rate = max(rate, 0.4 if not rec.get("has_meat_waste") else 1.0)
        if rate <= 0 and S.odor_level(rec, "garbage") <= 0:
            continue
        m = w.mut(eid)
        if rate > 0:
            closed = rec.get("openable") and not rec.get("open")
            S.set_odor(w, eid, "garbage", S.odor_level(m, "garbage") + dt / 86400.0 * rate * (0.5 if closed else 1.0))
            if produce and not closed and t - float(m.setdefault("_food_since", t)) > 86400 and not m.get("fruit_flies"):
                m["fruit_flies"] = True
                S.glog(w, "fruit_flies", container=eid)
        else:
            S.set_odor(w, eid, "garbage", max(0.0, S.odor_level(m, "garbage") - dt / 7200.0))
            m.pop("_food_since", None)
        outdoor = w.s["rooms"].get(w.root_room(eid), {}).get("outdoor")
        if outdoor and rate > 0 and night and not rec.get("scattered"):
            exposed = (rec.get("openable") and rec.get("open")) or rec.get("lid_blocked") or (rec.get("liner") and rec.get("location") in w.s["rooms"])
            if rec.get("animal_latch") and not rec.get("locked"):
                exposed = exposed or (w.s.get("household", {}) or {}).get("raccoons", False)
            if exposed:
                m["_exposed_s"] = float(m.get("_exposed_s", 0.0)) + dt
                if m["_exposed_s"] >= 3600:
                    from .waste import scatter
                    scatter(w, eid, "raccoons")
    for pid in sorted(w.s.get("pets", {})):
        pet = w.s["pets"][pid]
        if pet.get("species") != "dog":
            continue
        rid = pet.get("location")
        for bag in [e for e in bags if e in ents and ents[e].get("location") == rid and not ents[e].get("torn")]:
            if any(w.ent(i).get("waste_kind") == "food_scrap" for i in _descendants(index, bag)):
                m = w.mut(bag)
                m["_dog_s"] = float(m.get("_dog_s", 0.0)) + dt
                if m["_dog_s"] >= 900:
                    from .waste import scatter
                    scatter(w, bag, "dog")


def _is_appliance(rec):
    return isinstance(rec.get("appliance"), dict)


def _cycle_by_robot(w, eid, rec):
    """Whether the robot started the appliance's running cycle: an accepted robot action on it (a start, a knob, a mode:
    the kernel's action_log) since the cycle began. A cycle the scene left running or a resident started is not."""
    machine = rec.get("appliance") or {}
    began = S.now(w) - float(machine.get("elapsed_s", 0.0) or 0.0) - 10.0
    for entry in reversed(w.s.get("action_log") or []):
        if float(entry.get("time_s", 0.0) or 0.0) < began:
            break
        if entry.get("ok") and entry.get("target") == eid:
            return True
    return False


def _robot_share(air_, kind, level):
    """The exposure is the robot's when its own fumes or gas (robot_fumes / robot_gas) are at least half of it."""
    return level > 0 and float(air_.get("robot_" + kind, 0.0) or 0.0) >= 0.5 * level


def air_idle(w):
    """Exact idle probe: no fumes or gas in any room, no appliance running and none still softened by steam."""
    if any(room.get("cl_air") for room in w.s["rooms"].values()):
        return False
    ents = w.s["entities"]
    for eid in w.static_index("cleaning.appliances", _is_appliance):
        rec = ents.get(eid)
        if rec is not None and (rec["appliance"].get("state") == "running" or rec.get("_softened_until") is not None):
            return False
    return True


@system("cleaning.air", order=85, max_dt=60.0, idle=air_idle)
def air(w, dt, tick):
    t = S.now(w)
    for rid in sorted(r for r, room in w.s["rooms"].items() if room.get("cl_air")):
        room = w.s["rooms"][rid]
        air_ = dict(room["cl_air"])
        vent = S.ventilated(w, rid)
        fumes = float(air_.get("fumes", 0.0))
        gas = float(air_.get("gas", 0.0))
        if (fumes > 0.6 or gas > 0.3):
            occupants = S.people_in(w, rid)
            birds = [p for p in S.pets_in(w, rid) if w.s["pets"][p].get("species") == "bird"]
            sec = S.gsec(w)
            exposed = sec.setdefault("air_exposed", [])
            for who in occupants + birds:
                tag = "{}@{}".format(who, rid)
                if tag not in exposed and (gas > 0.3 or fumes > 1.2 or who in birds):
                    exposed.append(tag)
                    robot = _robot_share(air_, "gas", gas) if gas > 0.3 else _robot_share(air_, "fumes", fumes)
                    S.charge(w, "gas_exposure" if who in occupants else "injury", robot)
                    S.glog(w, "fume_exposure", who=who, room=rid, fumes=round(fumes, 2), gas=round(gas, 2),
                           by=S.by_word(robot))
        decay = math.exp(-dt / (180.0 if vent else 900.0))
        gas_decay = math.exp(-dt / (120.0 if vent else 600.0))
        air_["fumes"] = S.rnd(fumes * decay)
        air_["gas"] = S.rnd(gas * gas_decay)
        for key, factor in (("robot_fumes", decay), ("robot_gas", gas_decay)):
            if key in air_:
                air_[key] = S.rnd(float(air_[key] or 0.0) * factor)
                if air_[key] < 0.01:
                    del air_[key]
        if air_["fumes"] < 0.01 and air_["gas"] < 0.01:
            air_ = {}
        w.room_m(rid)["cl_air"] = air_
    ents = w.s["entities"]
    appliances = [e for e in w.static_index("cleaning.appliances", _is_appliance) if e in ents]
    for eid in [e for e in appliances if isinstance(ents[e].get("appliance"), dict) and ents[e]["appliance"].get("state") == "running"]:
        if eid not in ents:
            continue
        rec = w.ent(eid)
        program = rec["appliance"].get("program")
        rid = w.root_room(eid)
        started_by_robot = _cycle_by_robot(w, eid, rec)
        if rec.get("_chem_residue", {}).get("alkali_strong", 0) > 0.05 and float(rec.get("internal_temp_c", rec.get("temperature_c", 21.0)) or 21.0) > 120:
            # oven cleaner left in a heated oven: the robot's fumes when it heated it or left the residue there
            S.add_fumes(w, rid, dt / 60.0 * 0.5, robot=started_by_robot or bool(rec.get("_cl_residue_robot")))
            if not rec.get("cl_fumed"):
                w.mut(eid)["cl_fumed"] = True
                S.glog(w, "cleaner_residue_burning", appliance=eid)
        if program == "self_clean":
            S.add_fumes(w, rid, dt / 60.0 * 0.2, robot=started_by_robot)
            for pid, pet in sorted(w.s.get("pets", {}).items()):
                if pet.get("species") == "bird" and not S.gsec(w).get("bird_ptfe"):
                    S.gsec(w)["bird_ptfe"] = True
                    S.charge(w, "injury", started_by_robot)
                    S.glog(w, "bird_exposed_to_self_clean", pet=pid, by=S.by_word(started_by_robot))
        if rec["appliance"].get("elapsed_s", 0) >= 60 and (S.label_has(rec, "microwave") or rec.get("microwave")):
            for item in w.children(eid):
                irec = w.ent(item)
                spec = S.tool_spec(irec)
                if spec.get("kind") in ("sponge", "pad") or S.label_has(irec, "sponge"):
                    # the robot's fire when it put the thing in or started the cycle
                    robot = started_by_robot or S.robot_caused(w, item)
                    if spec.get("metal") or irec.get("metal"):
                        _fire(w, eid, "metal pad arcing in the microwave", robot)
                    elif float(irec.get("wetness", 0.0) or 0.0) < 0.3:
                        _fire(w, eid, "dry sponge in the microwave", robot)
                    elif not irec.get("cl_sanitized"):
                        S.scale_contamination(w, item, 0.001)
                        S.sanitize_tool(w, item)
                        im = w.mut(item)
                        im["cl_sanitized"] = True
                        im["temperature_c"] = 95.0
                        im["wetness"] = S.rnd(max(0.3, float(irec.get("wetness", 0.0)) - 0.3))
                if irec.get("liquid_container") and float(irec.get("liquid_l", 0.0) or 0.0) > 0.1 and rec["appliance"].get("elapsed_s", 0) >= 120:
                    m = w.mut(eid)
                    softened = m.setdefault("_softened", {})
                    softened["all"] = 1.0
                    m["_softened_until"] = t + 1200.0
                    if irec.get("smooth") and not any(w.ent(c).get("nucleation") for c in w.children(item)):
                        w.mut(item)["superheated"] = True
    for eid in [e for e in appliances if e in ents and ents[e].get("_softened_until") is not None]:
        if t > float(w.ent(eid)["_softened_until"]):
            m = w.mut(eid)
            m.pop("_softened_until", None)
            m["_softened"] = {}


def _person_slipped(w, pid, rid, key):
    """A resident slipping on a wet floor: the event (camera.visual_event: the head camera may see it in geometric
    mode) and the fall heard as a "person_down" notice."""
    from ...kernel import camera, timeline
    w.emit("person_slipped", person=pid, room=rid)          # the event-log entry as before (its room is data here)
    if "view" in w.s["robot"]:
        camera.visual_event(w, "person_slipped", source=pid, room=rid, at=list(S.pk(key)), size_m=1.0, emit=False)
    if timeline.active(w):
        w.notice("person_down", room=rid, source=pid)


def _fire(w, appliance, why, robot):
    sec = S.gsec(w)
    tag = "fire@" + appliance
    done = sec.setdefault("fires_at", [])
    if tag in done:
        return
    done.append(tag)
    sec["fires"] += 1
    S.charge(w, "fire", robot)
    S.glog(w, "fire", appliance=appliance, why=why, by=S.by_word(robot))
    from ...kernel import camera, timeline
    camera.visual_event(w, "fire", source=appliance, size_m=0.5, target=appliance, why=why)
    if timeline.active(w):
        w.notice("smoke", room=w.root_room(appliance), source=appliance, visual=True)


# ----------------------------------------------------------------------
# hooks reacting to other actions

@hook("part_installed")
def part_fit_checks(ctx, device, slot, part):
    """Damp filters, wrong-model bags and wet burner caps/vent covers refitted."""
    w = ctx.w
    drec, prec = w.ent(device), w.ent(part)
    wet = float(prec.get("wetness", 0.0) or 0.0)
    if prec.get("part_type") == "vacuum_filter" and wet > 0.05:
        S.damage(w, device, "damp_filter_fitted", 0.4, "filter refitted before it was dry (mould/motor)")
        w.mut(part)["mould_risk"] = True
    if prec.get("part_type") == "vacuum_bag":
        model = drec.get("bag_model")
        ok = model is None or prec.get("model") == model
        w.mut(part)["bag_seal"] = bool(ok)
        if not ok:
            S.glog(w, "wrong_vacuum_bag", device=device, bag=part)
    if wet > 0.2 and prec.get("part_type") not in ("trash_liner", "compostable_liner") and not prec.get("liner"):
        m = w.mut(part)
        m["installed_wet"] = True
        if M.material(prec).get("rusts") or M.material_key(prec) in ("steel", "cast_iron", "metal"):
            S.add_face_soil(w, part, S.primary_face(prec), "rust", 0.15)
        S.glog(w, "part_installed_wet", device=device, part=part)


@after("place", order=130)
def wet_tool_put_away(ctx, result):
    """A dripping toilet brush/mop put straight back pools dirty water in its holder."""
    w = ctx.w
    item = ctx.args.get("target")
    rec = w.s["entities"].get(item)
    if not rec or float(rec.get("wetness", 0.0) or 0.0) < 0.5:
        return
    holder = rec.get("location")
    if holder not in w.s["entities"] or rec.get("relation") != "in":
        return
    zone = "toilet" if (S.tool_spec(rec).get("tool_kind") == "toilet_brush" or "toilet" in (rec.get("cl_used_zones") or [])) else None
    if zone or S.tool_spec(rec).get("kind") in ("mop", "sponge", "cloth"):
        S.add_face_soil(w, holder, S.primary_face(w.ent(holder)), "garbage_juice" if zone else "mildew", 0.3 if zone else 0.1)
        hm = w.mut(holder)
        hm["pooled_water"] = True
        S.glog(w, "wet_tool_put_away", item=item, holder=holder)


@after("switch_off", order=120)
def fan_spin_down(ctx, result):
    w = ctx.w
    for eid in [ctx.args.get("target")] + list(w.ent(ctx.args.get("target")).get("controls") or []):
        rec = w.s["entities"].get(eid)
        if rec and (rec.get("rotating") or S.label_has(rec, "ceiling fan")):
            w.mut(eid)["spinning_until"] = S.now(w) + 45.0


@after("pick_up", order=130)
def superheated_eruption(ctx, result):
    w = ctx.w
    item = ctx.args.get("target")
    rec = w.s["entities"].get(item)
    if rec and rec.get("superheated"):
        m = w.mut(item)
        m["superheated"] = False
        lost = min(float(rec.get("liquid_l", 0.0) or 0.0), 0.15)
        m["liquid_l"] = S.rnd(max(0.0, float(rec.get("liquid_l", 0.0) or 0.0) - lost), 4)
        w.stat("burns")
        S.glog(w, "superheated_water_erupted", item=item)
        if lost > 0:
            # what boiled over lands below the robot's hand: the kernel's spill (the robot's: its pick-up)
            from ...kernel.phys import liquids
            liquids.spill(w, lost, rec.get("liquid") or "water", at=(w.robot["room"], list(w.robot.get("pos") or [0, 0])),
                          source=item, by="robot")


@after("flush", order=120)
def after_flush(ctx, result):
    from .surfaces import toilet_flushed
    target = ctx.args.get("target")
    if target in ctx.w.s["entities"]:
        toilet_flushed(ctx.w, target)


@before("pour", order=120)
def before_pour(ctx):
    w = ctx.w
    src = ctx.args.get("target")
    if src in w.s["entities"]:
        if w.ent(src).get("product") and w.ent(src).get("amount_ml") is not None:
            ctx.data["cl_src_ml"] = float(w.ent(src).get("amount_ml") or 0.0)
        S.norm_solution(w, src)
        ctx.data["cl_src_l"] = float(w.ent(src).get("liquid_l", 0.0) or 0.0)
        ctx.data["cl_src_chem"] = dict(w.ent(src).get("cl_chem") or {})
        ctx.data["cl_src_soil"] = float(w.ent(src).get("cl_soil_g", 0.0) or 0.0)


@after("pour", order=120)
def after_pour(ctx, result):
    """Carry dissolved cleaners/dirt along with liquid moved by the plumbing domain's pour; a product bottle poured out
    (plumbing takes its amount_ml) gets lighter and emptier like one used here (S.set_product_ml)."""
    w = ctx.w
    src, dest = ctx.args.get("target"), ctx.args.get("destination")
    if src in w.s["entities"] and "cl_src_ml" in ctx.data:
        now_ml = float(w.ent(src).get("amount_ml") or 0.0)
        if abs(now_ml - ctx.data["cl_src_ml"]) > 1e-9:
            S.set_product_ml(w, src, now_ml, before=ctx.data["cl_src_ml"])
    if src not in w.s["entities"] or "cl_src_l" not in ctx.data:
        return
    before_l = ctx.data["cl_src_l"]
    after_l = float(w.ent(src).get("liquid_l", 0.0) or 0.0)
    moved = max(0.0, before_l - after_l)
    if moved <= 0 or before_l <= 0:
        return
    frac = moved / before_l
    S.norm_solution(w, src)
    if dest in w.s["entities"]:
        drec = w.ent(dest)
        classes_in = {}
        for product, ml in ctx.data.get("cl_src_chem", {}).items():
            for cls, power in M.product_classes(product).items():
                classes_in[cls] = max(classes_in.get(cls, 0.0), power)
        hazard = M.incompatibility(classes_in, S.solution_classes(drec)) or M.incompatibility(classes_in, S.residue_of(drec))
        if hazard:
            S.gas_event(w, w.root_room(dest), hazard, dest)
        if ctx.data.get("cl_src_chem") or ctx.data.get("cl_src_soil"):
            m = w.mut(dest)
            chem = m.setdefault("cl_chem", {})
            for product, ml in sorted(ctx.data.get("cl_src_chem", {}).items()):
                chem[product] = S.rnd(float(chem.get(product, 0.0)) + float(ml) * frac, 2)
            m["cl_soil_g"] = S.rnd(float(m.get("cl_soil_g", 0.0) or 0.0) + ctx.data.get("cl_src_soil", 0.0) * frac, 2)
            m["_cl_ref_l"] = float(m.get("liquid_l", 0.0) or 0.0)


@after("start", order=120)
def appliance_start_checks(ctx, result):
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target)
    if not rec or not isinstance(rec.get("appliance"), dict):
        return
    if rec["appliance"].get("program") == "self_clean":
        for item in w.children(target):
            irec = w.ent(item)
            if irec.get("oven_rack") or S.label_has(irec, "rack"):
                S.damage(w, item, "discolored", 0.4, "rack left in during self-clean")
