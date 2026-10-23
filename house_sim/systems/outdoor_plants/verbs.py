"""Action verbs owned by outdoor_plants (see docs/domains/outdoor_plants.md).

water_plant check_moisture prune deadhead trim_plant harvest repot plant sow transplant
thin_seedlings dig fill_hole firm_soil mulch weed rake bag_leaves mow edge aerate overseed
fertilize turn_compost skim shovel spread_granular clear_gutter clear_grate secure_outdoor

`spread` (granular) is listed for this domain in CONVENTIONS.md but food also owns `spread`;
to avoid a duplicate registration the granular verb is registered as `spread_granular`.
"""
import json
import math

from ...kernel import geometry, physics, realism
from ...kernel.actions import action
from ...kernel.plugins import after
from ...kernel.errors import ActionError, require
from . import common as C
from . import compost as K
from . import garden as Gd
from . import lawn as L
from . import patio as Pt
from . import plants as P
from . import pool as Po
from . import yard as Y
from .species import medium_params, profile, rwc, theta_for

POUR_LPS = {"can": 0.15, "trickle": 0.035, "gentle": 0.1, "full": 0.35, "mist": 0.012}
PLANT_SAFE_LIQUIDS = ("water", "rainwater", "fertilizer_solution", "plant_food")


# ---------------------------------------------------------------------------
# shared guards

def target_record(ctx, touch=True, reach=True):
    return ctx.obj(ctx.args["target"], touch=touch, reach=reach)


def get_tool(ctx, caps, tool_id=None, required=True, what="tool"):
    """A held tool offering any capability in caps (tool_kind/capabilities/affordances)."""
    w = ctx.w
    caps = set(caps)
    candidates = [tool_id] if tool_id else w.held_items()
    for eid in candidates:
        if not eid or eid not in w.s["entities"]:
            continue
        rec = w.ent(eid)
        offered = set(rec.get("capabilities", [])) | set(rec.get("affordances", [])) | {rec.get("tool_kind")}
        if not offered & caps:
            continue
        if not ctx.holding(eid) and not (rec.get("powered") and not rec.get("portable") and C.reachable(ctx, eid, 1)):
            continue
        require(not rec.get("broken"), "That tool is damaged; do not use it", "safety")
        if rec.get("battery_powered"):
            require(float(rec.get("battery_level", 1.0)) > 0.02, "The tool's battery is flat; charge it", "resource")
        if rec.get("powered"):
            powered_tool_guard(ctx, eid)
        if rec.get("powered") or rec.get("noise_db") is not None:
            ctx.data["op_noisy_tool"] = eid          # what the action sounds like (tool_noise)
        return eid
    if tool_id:
        raise ActionError("Hold {} (it must be a {} tool)".format(tool_id, "/".join(sorted(caps))), "capability")
    if required:
        raise ActionError("Hold a {} ({})".format(what, " or ".join(sorted(caps))), "capability")
    return None


def powered_tool_guard(ctx, tool_id):
    """Household rules for powered tools: approval, a supervising adult at home, no children nearby; running one in
    the household's quiet hours counts quiet_hours_violations. The robot's own quiet mode is the body's: these verbs
    declare the tool's sound (noise_db=tool_noise) and the kernel's quiet guard refuses anything louder than 55 dB at
    1 m ("Quiet mode is on: that is too loud now")."""
    w = ctx.w
    require(C.approved(w, "use:" + tool_id, "operate:" + tool_id), "Powered tool: get explicit approval first (ask for permission:use:{})".format(tool_id), "safety")
    require(C.adult_on_site(w), "Powered tools need a supervising adult at home", "safety")
    if C.quiet_hours(w):
        C.stat(w, "quiet_hours_violations")
    rid = w.robot["room"]
    for pid, person in sorted(w.s.get("people", {}).items()):
        if person.get("location") == rid and person.get("role") in ("child", "toddler", "infant"):
            C.stat(w, "safety_violations")
            C.note(w, "power_tool_near_child", child=pid)


TOOL_NOISE_DB = 85.0        # a powered garden tool without a rated level: about 85 dB at 1 m (battery trimmers, edgers)


def tool_noise(ctx):
    """ActionSpec.noise_db of the verbs that may use a powered or loud tool (TIER1_EMBODIMENT 4.9): the sound at 1 m
    of the tool the action used (its noise_db: mower 75, leaf blower 85, hedge and string trimmers 90 dB), None for a
    quiet hand tool. The body's quiet guard refuses it in quiet mode and robot_sound lets people and pets hear it."""
    eid = ctx.data.get("op_noisy_tool")
    rec = ctx.w.s["entities"].get(eid) if eid else None
    if rec is None:
        return None
    if rec.get("noise_db") is not None:
        return float(rec["noise_db"])
    return TOOL_NOISE_DB if rec.get("powered") else None


def water_source(ctx, tool_id=None):
    """(eid, kind) where kind is 'container' (held liquid water) or 'hose'."""
    w = ctx.w
    candidates = [tool_id] if tool_id else w.held_items()
    for eid in candidates:
        if not eid or eid not in w.s["entities"] or not ctx.holding(eid):
            continue
        if C.unresolved(ctx, eid):
            # realistic: an unread look-alike (one of the spray bottles) is no source whatever it is -- one refusal
            # for every member when it is named, skipped by the search otherwise (round-5 red team R1)
            if tool_id:
                C.require_identified(ctx, eid)
            continue
        rec = w.ent(eid)
        if rec.get("liquid_container") and float(rec.get("liquid_l", 0.0)) > 0:
            if not C.realistic(ctx):
                require(rec.get("liquid") in PLANT_SAFE_LIQUIDS, "Only clean water (or diluted plant food) goes on plants", "safety")
            # realistic: what is in the can is the robot's own responsibility (a refusal would name the liquid of an
            # unlabelled bottle); a wrong liquid is poured and harms the plant (wrong_liquid)
            return eid, "container"
        if rec.get("hose") or rec.get("tool_kind") in ("hose", "garden_hose", "watering_wand", "hose_nozzle", "soaker_hose") or rec.get("category") in ("hose", "garden_hose"):
            require(physics.water_available(w, eid), "No water at the spigot", "resource")
            return eid, "hose"
    raise ActionError("Hold a watering can with water (or a running hose)", "resource")


def draw_water(ctx, src, kind, liters):
    w = ctx.w
    if kind == "container":
        rec = w.ent(src)
        have = float(rec.get("liquid_l", 0.0))
        use = min(have, liters)
        m = w.mut(src)
        m["liquid_l"] = round(have - use, 4)
        if m["liquid_l"] <= 1e-6:
            m["liquid_l"] = 0.0
        return use, rec.get("liquid", "water")
    sec = w.sec("utilities")
    sec["water_l"] = round(max(0.0, float(sec.get("water_l", 1e5)) - liters), 2)
    C.stat(w, "hose_water_l", round(liters, 2))
    return liters, "water"


def entity_param(ctx, key):
    value = ctx.args.get(key)
    if value is None:
        return None
    require(isinstance(value, str) and value in ctx.w.s["entities"], "Unknown {}: {}".format(key, value), "argument")
    return value


# ---------------------------------------------------------------------------
# watering

@action("water_plant", params={"target": "entity", "tool": "?entity", "amount_l": {"type": "number", "optional": True, "min": 0.01, "max": 400.0},
                               "until": "?str:drainage|moist|saturated", "at": "?str:soil|base|foliage|bottom|mist",
                               "flow": "?str:trickle|gentle|full|mist", "area": "?any", "minutes": {"type": "number", "optional": True, "min": 0.1, "max": 240.0}},
        duration=4.0, energy=0.01, category="physical", timing="commanded",
        doc="Water a plant (into its pot; in-ground shrubs/trees into their own root zone), a pot, a garden bed, a seed tray or a lawn area from a held watering can or running hose. "
            "amount_l liters, or until=drainage (to container capacity plus a little run-through) / moist; at=soil (default), foliage, bottom (into saucer/bottom tray), mist; "
            "flow for hoses: trickle|gentle|full; area=[c0,r0,c1,r1] lawn patches; minutes for hose time.")
def water_plant(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = target_record(ctx)
    src, kind = water_source(ctx, entity_param(ctx, "tool"))
    at = ctx.args.get("at") or "soil"
    flow = ctx.args.get("flow") or ("can" if kind == "container" else "gentle")
    if kind == "container":
        srec = w.ent(src)
        if at == "mist" or srec.get("sprayer") or srec.get("tool_kind") == "spray_bottle":
            flow = "mist"
        elif srec.get("rose"):
            flow = "trickle" if flow == "can" else flow
    rate = POUR_LPS.get(flow, 0.15)
    minutes = ctx.args.get("minutes")
    amount = ctx.args.get("amount_l")
    until = ctx.args.get("until")
    result = {"target": target}
    plant_id = target if P.is_plant(rec) else None
    container = P.container_of(w, plant_id) if plant_id else target
    crec = w.ent(container) if container else rec
    if C.is_type(crec, "lawn"):
        if amount is None:
            require(minutes is not None, "Give amount_l or minutes for lawn watering", "argument")
            amount = rate * 60.0 * float(minutes)
        used, liquid = draw_water(ctx, src, kind, amount)
        runoff = L.water_lawn(ctx, container, ctx.args.get("area"), used, flow if flow != "can" else "gentle")
        wrong_liquid(ctx, liquid, [], container)
        return poured(ctx, result, used, rate, {"runoff_l": round(runoff, 2)}, digits=2)
    if C.is_type(crec, "garden_bed"):
        if amount is None:
            if minutes is not None:
                amount = rate * 60.0 * float(minutes)
            else:
                require(until is not None, "Give amount_l, minutes or until", "argument")
                medium = crec.get("medium") or {}
                params = medium_params(medium.get("kind", "garden_soil"))
                target_theta = params["fc"] if until in ("drainage", "saturated") else theta_for(0.85, medium.get("kind", "garden_soil"))
                amount = max(0.5, (target_theta - float(medium.get("moisture", 0.2))) * P.soil_liters(crec))
        used, liquid = draw_water(ctx, src, kind, amount)
        if kind == "hose":
            L.watering_rule_check(w)
        out = Gd.add_water_to_bed(w, container, used, flow=("gentle" if flow == "can" else flow), at=("foliage" if at == "foliage" else "soil"))
        for pid in Gd.plants_on(w, container):
            Gd.settle_after_watering(w, pid)
            P.plant_status(w, pid)
        smear_labels(w, container)
        wrong_liquid(ctx, liquid, Gd.plants_on(w, container), container)
        Gd.bed_status(w, container)
        return poured(ctx, result, used, rate, out, digits=2)
    if C.is_type(crec, "seed_tray"):
        medium = crec.get("medium") or {}
        require(float(medium.get("fill_frac", 0.0)) > 0, "The tray has no mix in it", "precondition")
        soil_l = P.soil_liters(crec)
        if amount is None:
            params = medium_params(medium.get("kind", "seed_mix"))
            target_theta = theta_for(0.9, medium.get("kind", "seed_mix"))
            amount = max(0.05, (target_theta - float(medium.get("moisture", 0.0))) * soil_l)
        used, liquid = draw_water(ctx, src, kind, amount)
        m = w.mut(container)
        md = m["medium"]
        params = medium_params(md.get("kind", "seed_mix"))
        before = rwc(float(md.get("moisture", 0.0)), md.get("kind", "seed_mix"))
        if at == "bottom":
            m["bottom_water_l"] = round(float(m.get("bottom_water_l", 0.0)) + used, 4)
            wick = min(used, max(0.0, (params["fc"] - float(md.get("moisture", 0.0))) * soil_l))
            m["bottom_water_l"] = round(m["bottom_water_l"] - wick, 4)
            md["moisture"] = round(float(md.get("moisture", 0.0)) + wick / soil_l, 4)
            md["moisture_top"] = round(max(float(md.get("moisture_top", 0.0)), md["moisture"] * 0.95), 4)
        else:
            md["moisture"] = round(min(params["sat"], float(md.get("moisture", 0.0)) + used / soil_l), 4)
            md["moisture_top"] = round(min(params["sat"], max(float(md.get("moisture_top", 0.0)), md["moisture"])), 4)
            if md["moisture"] > params["fc"]:
                extra = (md["moisture"] - params["fc"]) * soil_l
                md["moisture"] = params["fc"]
                m["bottom_water_l"] = round(float(m.get("bottom_water_l", 0.0)) + extra, 4)
            if flow in ("can", "gentle", "full") and not w.ent(src).get("rose"):
                flattened = 0
                for sowing in m.get("sowings", []):
                    if sowing.get("germinated"):
                        lost = int(round(int(sowing.get("seedlings", 0)) * 0.3))
                        sowing["seedlings"] = int(sowing.get("seedlings", 0)) - lost
                        flattened += lost
                    else:
                        sowing["washed_out"] = round(min(1.0, float(sowing.get("washed_out", 0.0)) + 0.5), 3)
                if flattened:
                    C.stat(w, "seedlings_flattened", flattened)
                C.set_status(w, container, harsh_watering=True)
        if before > 0.8:
            C.stat(w, "overwatered")
            C.set_status(w, container, overwatered=True)
        tray_status(w, container)
        smear_labels(w, container)
        wrong_liquid(ctx, liquid, [], container)
        return poured(ctx, result, used, max(0.02, rate), {}, digits=3)
    if P.is_plant(crec) and isinstance(crec.get("medium"), dict):
        if amount is None:
            if minutes is not None:
                amount = rate * 60.0 * float(minutes)
            else:
                require(until is not None, "Give amount_l, minutes or until", "argument")
                medium = crec.get("medium") or {}
                kind_m = medium.get("kind", "garden_soil")
                target_theta = medium_params(kind_m)["fc"] if until in ("drainage", "saturated") else theta_for(0.85, kind_m)
                amount = max(1.0, (target_theta - float(medium.get("moisture", 0.2))) * P.soil_liters(crec))
        before_rwc = P.container_rwc(crec)
        used, liquid = draw_water(ctx, src, kind, amount)
        out = Gd.add_water_to_ground(w, container, used, flow=("gentle" if flow == "can" else flow), at=at)
        P.watering_check(w, container, used, before_rwc)
        if kind == "hose":
            L.watering_rule_check(w)
        if crec.get("planted_s") is not None:
            Gd.settle_after_watering(w, container)
        wrong_liquid(ctx, liquid, [container], container)
        P.plant_status(w, container)
        return poured(ctx, result, used, rate, out, digits=2)
    require(C.is_type(crec, "plant_pot"), "Water a plant, pot, bed, seed tray or lawn", "argument")
    pot = crec
    soil_l = P.soil_liters(pot)
    before_rwc = P.container_rwc(pot) if soil_l > 0 else None
    if amount is None:
        require(until is not None, "Give amount_l or until=drainage|moist", "argument")
        if until == "moist":
            amount = max(0.02, P.water_needed_to(pot, 0.85))
        elif pot.get("drainage_hole", True):
            amount = P.water_needed_to(pot, 1.0) + 0.06 * max(0.5, soil_l) + 0.02
        else:
            params = medium_params((pot.get("medium") or {}).get("kind", "potting_mix"))
            amount = max(0.02, (params["sat"] - float((pot.get("medium") or {}).get("moisture", 0.0))) * soil_l + 0.05 * max(0.5, soil_l))
    used, liquid = draw_water(ctx, src, kind, amount)
    if kind == "hose":
        L.watering_rule_check(w)
    if at == "bottom":
        saucer = pot.get("location")
        require(saucer in w.s["entities"] and C.is_type(w.ent(saucer), "plant_saucer"), "Bottom-watering needs the pot standing in a saucer", "precondition")
        P.receive_in_saucer(w, saucer, used)
        out = {"absorbed": 0.0, "drained": 0.0, "overflow": 0.0, "into_saucer": round(used, 3)}
    else:
        out = P.add_water_to_pot(w, container, used, at=at)
    if plant_id:
        if at == "foliage" or flow == "full":
            Gd.wet_foliage(w, [plant_id])
        P.watering_check(w, plant_id, used, before_rwc)
        w.mut(plant_id)["last_watered_s"] = w.s.get("time_s", 0.0)
    else:
        resident = P.plant_in(w, container)
        if resident:
            P.watering_check(w, resident, used, before_rwc)
            w.mut(resident)["last_watered_s"] = w.s.get("time_s", 0.0)
    if liquid in ("fertilizer_solution", "plant_food"):
        fert_from_solution(ctx, plant_id or P.plant_in(w, container), used)
    resident = plant_id or P.plant_in(w, container)
    wrong_liquid(ctx, liquid, [resident] if resident else [], container)
    P.refresh_plants_near(w, container)
    return poured(ctx, result, used, rate, {k: round(v, 3) for k, v in out.items()}, digits=3)


def poured(ctx, result, used, rate, out, digits=2):
    """Finish a watering result. Oracle: exact liters and the water balance (absorbed / drained / runoff...).
    Realistic: the volume as judged from the can (C.perceived_liters), what was seen running off or out of the
    drainage hole as words, nothing about what the soil absorbed (that is the hidden root-zone water); the time spent
    follows the perceived pour, so the duration does not give the hidden amount away either."""
    if not C.realistic(ctx):
        result.update(liters=round(used, digits), **out)
        ctx.spend(used / rate)
        return json.dumps(result, sort_keys=True)
    shown = C.perceived_liters(used)
    result["liters"] = shown
    for key, value in out.items():
        if key in ("drained", "overflow", "runoff", "runoff_l"):
            result[key] = C.water_word(value)
        elif key == "into_saucer":
            result[key] = C.perceived_liters(value)
    ctx.spend(float(math.ceil(shown / rate - 1e-9)))
    return json.dumps(result, sort_keys=True)


def wrong_liquid(ctx, liquid, plants, host):
    """Realistic mode pours whatever the robot holds (no refusal names the liquid); anything but water or diluted
    plant food harms the plants it lands on and counts as chemical misuse."""
    if liquid in PLANT_SAFE_LIQUIDS or liquid is None:
        return
    w = ctx.w
    C.stat(w, "chemical_misuse")
    C.set_status(w, host, wrong_liquid=liquid)
    for pid in plants:
        if pid and pid in w.s["entities"]:
            P.harm(w, pid, 0.3, "watered with " + str(liquid))
            C.set_status(w, pid, wrong_liquid=liquid)


def smear_labels(w, host_id):
    changed = False
    for child in w.children(host_id):
        rec = w.s["entities"][child]
        if C.is_type(rec, "plant_label") and rec.get("ink") == "water_soluble" and rec.get("label_text") and not rec.get("smeared"):
            m = w.mut(child)
            m["smeared"] = True
            m["label_text"] = "(illegible smear)"
            changed = True
    if changed:
        Gd.sowing_status(w, host_id)


def tray_status(w, tray):
    rec = w.s["entities"][tray]
    medium = rec.get("medium") or {}
    kind = medium.get("kind", "none")
    values = {"filled": float(medium.get("fill_frac", 0.0)) >= 0.9, "mix": kind}
    if kind != "none" and float(medium.get("fill_frac", 0.0)) > 0:
        r = rwc(float(medium.get("moisture", 0.0)), kind)
        values["rwc"] = round(r, 3)
        values["moist_ok"] = 0.55 <= r <= 1.05
        values["compaction"] = round(float(medium.get("compaction", 0.0)), 3)
        values["compaction_ok"] = 0.15 <= float(medium.get("compaction", 0.0)) <= 0.55
    values["sanitized"] = float((rec.get("soil") or {}).get("dirt", 0.0)) < 0.05 and float((rec.get("contamination") or {}).get("bacteria", 0.0)) < 0.05
    values["standing_water"] = float(rec.get("bottom_water_l", 0.0)) > 0.05
    C.set_status(w, tray, **values)


def fert_from_solution(ctx, pid, liters):
    if not pid:
        return
    w = ctx.w
    C.set_status(w, pid, fed=True)


# ---------------------------------------------------------------------------
# sensing

@action("check_moisture", params={"target": "entity", "depth_cm": {"type": "number", "optional": True, "min": 0.5, "max": 30.0}, "cell": "?cell", "tool": "?entity"},
        duration=6.0, energy=0.003, category="perception",
        doc="Feel or probe soil moisture at depth (finger to 5 cm, a held moisture meter to 20 cm): pots, plants, beds, trays, lawn patches (cell=[c,r]) or compost (squeeze test).")
def check_moisture(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = target_record(ctx)
    depth = float(ctx.args.get("depth_cm") or 4.0)
    meter = get_tool(ctx, {"measure_moisture"}, entity_param(ctx, "tool"), required=False)
    if depth > 5.0:
        require(meter is not None, "Probing deeper than a finger (5 cm) needs a moisture meter", "capability")
    out = {"target": target, "depth_cm": depth}
    container = P.container_of(w, target) if P.is_plant(rec) else target
    crec = w.ent(container) if container else rec
    if C.is_type(crec, "lawn"):
        grid = crec.get("grid")
        cell = ctx.args.get("cell") or list(L.robot_patch(w, container) or [0, 0])
        i = L.idx(grid, cell[0], cell[1])
        value = grid["moisture_top"][i] if depth <= 2 else grid["moisture"][i]
        out.update(cell=cell, meter=int(round(C.clamp(1 + 9 * value, 1, 10))), feel="dry" if value < 0.3 else "moist" if value < 1.05 else "soggy")
    elif C.is_type(crec, "compost_bin"):
        comp = crec.get("compost") or {}
        moist = float(comp.get("moisture", 0.5))
        out.update(squeeze="crumbles dry" if moist < 0.4 else ("like a wrung-out sponge" if moist <= 0.6 else "drips water"), moisture=round(moist, 2))
    elif crec.get("medium"):
        if crec.get("medium", {}).get("kind") in (None, "none"):
            out.update(feel="no soil")
        else:
            reading = P.probe_reading(crec, depth)
            out.update(meter=reading["meter"], feel=reading["feel"])
            if C.is_type(crec, "garden_bed"):
                out["squeeze_test"] = "forms a sticky ball - too wet to work" if reading["rwc"] > 0.95 else "crumbles - workable"
            plant = target if P.is_plant(rec) else P.plant_in(w, container)
            identified = (w.s.get("facts", {}).get("identified") or {}).get(plant) if plant else None
            if plant and identified and identified not in ("uncertain",):
                if identified == "artificial":
                    out["advice"] = "artificial plant: never water"
                else:
                    p = profile(identified)
                    r = reading["rwc"] if depth > 3 else P.container_rwc(crec)
                    out["advice"] = "needs water" if r < p["water_at"] else ("too wet: let it dry" if r > p["band"][1] else "fine, do not water")
            w.sec("facts").setdefault("inspected", {})[plant or target] = w.s["time_s"]
    else:
        raise ActionError("That has no soil to check", "argument")
    w.sec("facts").setdefault("moisture_checks", {})[target] = out
    w.sec("facts").setdefault("inspected", {})[target] = w.s["time_s"]
    if C.realistic(ctx):
        out = _sensed_moisture(ctx, out, meter, container or target, crec)
    return json.dumps(out, sort_keys=True)


def _sensed_moisture(ctx, out, meter, host, hrec):
    """Realistic perception: a finger (or a squeeze) gives the feel only; a held meter adds its 1-10 dial reading,
    which is the instrument's whole precision. The root-zone water itself (`medium`, the lawn grid, the compost
    dict) stays hidden; the grant records what was sensed and how."""
    out = dict(out)
    if meter is None:
        out.pop("meter", None)
    out.pop("moisture", None)        # compost squeeze test: "like a wrung-out sponge", not a water fraction
    field = "grid" if C.is_type(hrec, "lawn") else ("compost" if C.is_type(hrec, "compost_bin") else "medium")
    ctx.grant(host, "measure" if meter is not None and "meter" in out else "touch", [field])
    out["sensed_with"] = "moisture meter" if meter is not None and "meter" in out else ("squeeze" if field == "compost" else "finger")
    return out


# ---------------------------------------------------------------------------
# cutting: prune, deadhead, trim_plant

def cutting_tool(ctx, required=True):
    return get_tool(ctx, {"cut", "snip", "shear", "saw"}, entity_param(ctx, "tool"), required=required, what="cutting tool (pruners, snips, loppers, saw)")


def tool_contaminate(w, tool, diseased):
    if not tool:
        return
    if diseased:
        m = w.mut(tool)
        cont = m.setdefault("contamination", {})
        cont["plant_pathogen"] = 1.0


def sanitize_tool(ctx, tool):
    w = ctx.w
    ok = C.nearby_entity(ctx, lambda r: "disinfectant" in r.get("tags", []) or C.pinfo(r).get("kind") in ("rubbing_alcohol", "disinfectant", "bleach_solution")
                         or "sanitize" in r.get("capabilities", []), 2, by_identity=True)     # an unread spray bottle does not count
    require(ok is not None, "Keep rubbing alcohol or disinfectant wipes within reach to sanitize the blades", "resource")
    m = w.mut(tool)
    cont = m.setdefault("contamination", {})
    cont["plant_pathogen"] = 0.0
    cont["bacteria"] = 0.0
    ctx.spend(20.0)


def infect_from_tool(w, tool, pid):
    if not tool:
        return
    cont = (w.ent(tool).get("contamination") or {})
    if float(cont.get("plant_pathogen", 0.0)) > 0.5 and not w.ent(pid).get("diseased"):
        m = w.mut(pid)
        m["diseased"] = True
        C.stat(w, "disease_spread")
        C.stat(w, "plant_harm")
        C.note(w, "disease_spread_by_tool", plant=pid, tool=tool)


@action("prune", params={"target": "entity", "fraction": {"type": "number", "optional": True, "min": 0.0, "max": 1.0},
                         "select": "?str:dead|diseased|leggy|crossing|shape|any|water_sprouts|over_path",
                         "cut": "?str:above_node|above_bud|collar|mid_internode|flush|stub", "tool": "?entity", "sanitize_first": {"type": "bool", "default": False},
                         "length_m": {"type": "number", "optional": True, "min": 0.1, "max": 5.0}, "branch": "?str", "force": {"type": "bool", "default": False},
                         "collect": "?entity"},
        duration=10.0, energy=0.01, category="physical", noise_db=tool_noise,
        doc="Cut stems or branches with a held cutting tool. On a plant: select which (dead, diseased, leggy, crossing, shape...), fraction of foliage to remove, cut position. "
            "On a fallen branch: cut it into pieces of length_m. Tool capacity: herb snips 0.6 cm, pruners 2 cm, loppers 4 cm, saw 12 cm.")
def prune(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = target_record(ctx)
    tool = cutting_tool(ctx)
    trec = w.ent(tool)
    if ctx.args.get("sanitize_first"):
        sanitize_tool(ctx, tool)
    cap = float(trec.get("cut_max_cm", 2.0))
    if C.is_type(rec, "branch"):
        diam = float(rec.get("diameter_cm", 3.0))
        if diam > cap:
            require(ctx.args.get("force"), "Too thick for this tool; use loppers or a saw" if C.realistic(ctx) else
                    "Too thick for this tool ({} cm > {} cm); use loppers or a saw".format(diam, cap), "capability")
            w.mut(tool)["broken"] = True
            C.stat(w, "tool_damage")
        length = float(ctx.args.get("length_m") or 1.0)
        total = float(rec.get("length_m", 1.5))
        pieces = max(1, int(math.ceil(total / length - 1e-9)))
        if pieces == 1:
            return "Already shorter than {} m".format(length)
        mass = float(rec.get("mass_kg", 2.0)) / pieces
        piece_len = total / pieces
        m = w.mut(target)
        m["length_m"] = round(piece_len, 3)
        m["mass_kg"] = round(mass, 3)
        m["size_m"] = [round(piece_len, 3), rec.get("size_m", [1, 0.08, 0.08])[1], rec.get("size_m", [1, 0.08, 0.08])[2]]
        m["volume_l"] = round(float(rec.get("volume_l", 6.0)) / pieces, 3)
        made = [target]
        for i in range(pieces - 1):
            props = {k: rec.get(k) for k in ("label", "diameter_cm", "material", "waste_stream", "floor_hazard") if k in rec}
            props.update(length_m=round(piece_len, 3), mass_kg=round(mass, 3), size_m=[round(piece_len, 3), 0.08, 0.08], volume_l=round(float(rec.get("volume_l", 6.0)) / pieces, 3))
            loc = rec.get("location")
            if loc == "robot":
                loc = w.robot["room"]
            if loc in w.s["rooms"]:
                props["pos"] = C.drop_cell(w, loc)
            made.append(C.spawn(w, "branch", loc, eid=w.new_id("branch"), **props))
        ctx.spend(15.0 * pieces * max(1.0, diam / 2.0))
        return json.dumps({"pieces": made}, sort_keys=True)
    require(P.is_plant(rec), "Prune a plant or cut a fallen branch", "argument")
    require(rec.get("alive", True) or ctx.args.get("select") == "dead", "That plant is dead", "precondition")
    p = P.prof(rec)
    select = ctx.args.get("select") or "any"
    fraction = float(ctx.args.get("fraction") if ctx.args.get("fraction") is not None else 0.2)
    cut = ctx.args.get("cut") or "above_node"
    infect_from_tool(w, tool, target)
    out = {"plant": target, "select": select}
    if rec.get("nest"):
        C.stat(w, "wildlife_harm")
        C.note(w, "nest_disturbed", plant=target, nest=rec.get("nest"))
        w.mut(target)["nest"] = None
    branches = list(rec.get("branches") or [])
    if branches:
        return json.dumps(prune_branches(ctx, target, rec, tool, trec, select, cut, ctx.args.get("branch")), sort_keys=True)
    removed_kg = 0.0
    m = w.mut(target)
    if select == "diseased":
        if rec.get("diseased"):
            m["diseased"] = False
            tool_contaminate(w, tool, True)
            removed_kg += float(rec.get("mass_kg", 0.5)) * 0.1
            fraction = max(fraction, 0.1)
        out["diseased_removed"] = bool(rec.get("diseased"))
    if select == "dead":
        dead = int((rec.get("leaves") or {}).get("dead", 0))
        m.setdefault("leaves", {})["dead"] = 0
        removed_kg += 0.01 * dead
        fraction = 0.0
    if select == "leggy" and int(rec.get("leggy_stems", 0)) > 0:
        m["leggy_stems"] = 0
    if fraction > 0:
        window = float(rec.get("_removed_frac", 0.0)) + fraction
        m["_removed_frac"] = round(window, 3)
        if C.is_type(rec, "herb_plant") and rec.get("foliage_g") is not None:
            m["foliage_g"] = round(float(rec["foliage_g"]) * (1 - fraction), 2)
        healthy = int((rec.get("leaves") or {}).get("healthy", 20))
        m.setdefault("leaves", {})["healthy"] = max(0, int(round(healthy * (1 - fraction))))
        removed_kg += float(rec.get("mass_kg", 0.5)) * fraction
        size = list(rec.get("size_m") or [0.3, 0.3, 0.4])
        m["size_m"] = [round(size[0] * (1 - fraction * 0.5), 3), round(size[1] * (1 - fraction * 0.5), 3), round(size[2] * (1 - fraction * 0.4), 3)]
        if window > 0.34:
            P.harm(w, target, 0.25 + (window - 0.34), "pruned more than a third")
            out["over_pruned"] = True
        if p.get("woody") and fraction > 0.3:
            m["old_wood_cut"] = True
            P.harm(w, target, 0.2, "cut back into old wood")
            out["old_wood_cut"] = True
    if cut in ("mid_internode", "stub", "flush"):
        m["bad_cuts"] = int(rec.get("bad_cuts", 0)) + 3
        C.stat(w, "bad_cuts", 3)
        P.harm(w, target, 0.05, "cut away from a node: stubs die back")
        out["bad_cuts"] = True
    if select in ("shape", "any") and rec.get("bloom") is None and p.get("bloom") == "spring" and C.season(w) in ("winter", "spring") and not rec.get("bloomed"):
        C.stat(w, "bloom_lost")
        C.set_status(w, target, bloom_lost=True)
    pruned_status(w, target, out)
    if removed_kg > 0:
        Gd.debris_into(ctx, {"kind": "trimmings", "kg": removed_kg, "diseased": select == "diseased" and out.get("diseased_removed"), "green": True,
                             "thorny": bool(p.get("thorny")), "max_piece_cm": 15.0}, entity_param(ctx, "collect"), label="trimmings")
        sap_on_hands(ctx, p)
    ctx.spend(20.0 + 200.0 * fraction)
    # realistic: whether the plant was diseased (a secret field) and the grader's verdicts are not what a cut shows
    return json.dumps(C.drop_keys(ctx, out, CUT_VERDICTS), sort_keys=True)


CUT_VERDICTS = ("diseased_removed", "over_pruned", "over_trimmed", "old_wood_cut", "bad_cuts", "pests_left")


def sap_on_hands(ctx, profile_):
    """Cutting a latex-bleeding plant (figs, rubber plants, dandelions: species sap "latex") leaves sticky sap on the
    free hand that gathers the cut pieces (body.soil_hand; gloves take it instead)."""
    if profile_.get("sap"):
        C.soil_hands(ctx, "sap", 0.15)


def pruned_status(w, pid, out):
    rec = w.s["entities"][pid]
    C.set_status(w, pid, pruned=True, removed_frac=float(rec.get("_removed_frac", 0.0)), bad_cuts=int(rec.get("bad_cuts", 0)),
                 diseased=bool(rec.get("diseased")))
    P.plant_status(w, pid)


def prune_branches(ctx, pid, rec, tool, trec, select, cut, branch_id):
    w = ctx.w
    reach = C.reach_with_tool(w, tool, 0.0)
    cap = float(trec.get("cut_max_cm", 2.0))
    m = w.mut(pid)
    removed, skipped, out = [], [], {"plant": pid}
    p = P.prof(rec)
    total = len(m["branches"])
    for b in m["branches"]:
        if b.get("removed"):
            continue
        if branch_id and b.get("id") != branch_id:
            continue
        cls = b.get("class", "healthy")
        if not branch_id and select not in ("any", "shape") and cls != select and not (select == "over_path" and b.get("over_path")):
            continue
        if b.get("near_power_line"):
            C.stat(w, "safety_violations")
            Y.electrocute(w, pid) if b.get("touching_line") else None
            skipped.append(b["id"])
            continue
        if float(b.get("height_m", 1.0)) > reach:
            skipped.append(b["id"])
            continue
        diam = float(b.get("diam_cm", 1.0))
        if diam > cap:
            if ctx.args.get("force"):
                w.mut(tool)["broken"] = True
                b["crushed"] = True
                C.stat(w, "tool_damage")
                P.harm(w, pid, 0.05, "branch crushed by an overloaded tool")
            else:
                skipped.append(b["id"])
                continue
        if trec.get("cut_style") == "anvil" and cls != "dead":
            b["crushed"] = True
            C.stat(w, "bad_cuts")
        b["removed"] = True
        b["cut"] = cut
        if cut in ("flush", "stub", "mid_internode"):
            b["bad_cut"] = True
            C.stat(w, "bad_cuts")
        if cls == "diseased":
            tool_contaminate(w, tool, True)
        removed.append(b["id"])
    healthy_removed = sum(1 for b in m["branches"] if b.get("removed") and b.get("class") == "healthy")
    frac = healthy_removed / max(1, total)
    m["_removed_frac"] = round(frac, 3)
    if frac > 0.34:
        P.harm(w, pid, 0.25, "removed more than a third of the shrub")
    if p.get("bloom") == "spring" and C.season(w) in ("winter", "spring") and not rec.get("bloomed") and removed:
        C.stat(w, "bloom_lost")
        C.set_status(w, pid, bloom_lost=True)
    left = [b for b in m["branches"] if not b.get("removed") and b.get("class") in ("dead", "diseased", "crossing")]
    C.set_status(w, pid, removed_frac=round(frac, 3), problem_branches_left=len(left), skipped_branches=skipped,
                 bad_cuts=sum(1 for b in m["branches"] if b.get("bad_cut")), crushed=sum(1 for b in m["branches"] if b.get("crushed")))
    if removed:
        Gd.debris_into(ctx, {"kind": "branches", "kg": 0.3 * len(removed), "diseased": any(b.get("class") == "diseased" and b.get("removed") for b in m["branches"]),
                             "thorny": bool(p.get("thorny")), "green": True, "max_piece_cm": 60.0}, entity_param(ctx, "collect"), label="cut branches")
    out.update(removed=removed, skipped=skipped)
    ctx.spend(25.0 * max(1, len(removed)))
    return out


@action("deadhead", params={"target": "entity", "method": "?str:snip|pinch|shear|pull", "keep_seedheads": {"type": "bool", "default": False}, "tool": "?entity", "collect": "?entity"},
        duration=8.0, energy=0.005, category="physical", noise_db=tool_noise,
        doc="Remove spent flowers: snip/pinch just above the next leaf node (precise), shear (fast, takes buds and open flowers too), pull (ragged). keep_seedheads leaves seed heads/hips.")
def deadhead(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = target_record(ctx)
    require(P.is_plant(rec), "Deadhead a flowering plant", "argument")
    method = ctx.args.get("method") or "snip"
    if method in ("snip", "shear"):
        tool = cutting_tool(ctx)
        infect_from_tool(w, tool, target)
    flowers = dict(rec.get("flowers") or {})
    spent = int(flowers.get("spent", 0)) + (0 if ctx.args.get("keep_seedheads") else int(flowers.get("seedhead", 0)))
    require(spent > 0 or method == "shear", "There are no spent flowers on it", "precondition")
    m = w.mut(target)
    f = m.setdefault("flowers", {})
    removed_seedheads = 0 if ctx.args.get("keep_seedheads") else int(f.get("seedhead", 0))
    f["spent"] = 0
    if not ctx.args.get("keep_seedheads"):
        f["seedhead"] = 0
    lost_buds = lost_open = 0
    if method == "shear":
        lost_buds = int(round(int(f.get("bud", 0)) * 0.5))
        lost_open = int(round(int(f.get("open", 0)) * 0.5))
        f["bud"] = int(f.get("bud", 0)) - lost_buds
        f["open"] = int(f.get("open", 0)) - lost_open
        C.stat(w, "buds_removed", lost_buds)
    if method == "pull":
        m["bad_cuts"] = int(rec.get("bad_cuts", 0)) + spent
        C.stat(w, "bad_cuts", spent)
    values = {"deadheaded": True, "buds_lost": lost_buds, "open_lost": lost_open}
    if removed_seedheads and rec.get("owner_wants") == "seedheads":
        C.stat(w, "preference_violations")
        values["seedheads_removed_against_wishes"] = True
    if lost_buds or lost_open:
        C.stat(w, "plant_harm")
    C.set_status(w, target, **values)
    Gd.debris_into(ctx, {"kind": "spent_flowers", "kg": 0.01 * (spent + lost_buds + lost_open), "green": True, "seeding": bool(removed_seedheads) and rec.get("role") == "weed"},
                   entity_param(ctx, "collect"), label="spent flowers")
    ctx.spend(4.0 * (spent + lost_buds) + (0 if method == "shear" else 10.0))
    return json.dumps({"removed_spent": spent, "buds_lost": lost_buds}, sort_keys=True)


TRIM_LEAF_S = 5.0           # picking off one leaf (dead, yellowing, fallen): the count the result reports
TRIM_CUT_S = 5.0            # one cut of tips, buds or leggy stems
TRIM_FIXED_S = 20.0         # infested leaves: three leaves cut off whatever lives on them
TRIM_SIZE_CUTS = {"tiny": 0.5, "small": 1.0, "medium": 2.0, "large": 3.0, "huge": 4.0}


@action("trim_plant", params={"target": "entity", "part": "str:dead_leaves|yellow_leaves|discolored|fallen_leaves|tips|flower_buds|infested|lower_leaves|stem|leggy|hedge|grass|vines",
                              "method": "?str:snip|pinch|pull|shear", "tool": "?entity", "fraction": {"type": "number", "optional": True, "min": 0.0, "max": 1.0},
                              "length_m": {"type": "number", "optional": True, "min": 0.05, "max": 3.0}, "height_m": {"type": "number", "optional": True, "min": 0.2, "max": 4.0},
                              "taper": {"type": "bool", "default": True}, "area": "?any", "clearance_cm": {"type": "number", "default": 5.0, "min": 0.0, "max": 50.0},
                              "angle": "?str:angled|straight", "collect": "?entity"},
        duration=8.0, energy=0.006, category="physical", noise_db=tool_noise,
        doc="Trim parts of a plant: dead/yellow/fallen leaves, tips or flower buds of herbs, infested leaves, a cut flower's stem (length_m, angled) or lower leaves, a hedge (height_m, taper), "
            "grass rings around obstacles on a lawn (part=grass, clearance_cm from trunks/heads), or soft vines on a fence.")
def trim_plant(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = target_record(ctx, reach=not C.is_type(ctx.w.s["entities"].get(target) or {}, "fence"))
    part = ctx.args["part"]
    method = ctx.args.get("method") or "snip"
    collect = entity_param(ctx, "collect")
    if C.is_type(rec, "lawn"):
        require(part == "grass", "On a lawn only part=grass can be trimmed", "argument")
        tool = get_tool(ctx, {"trim_grass"}, entity_param(ctx, "tool"), what="string trimmer or grass shears")
        n = L.trim_rings(ctx, target, tool, ctx.args.get("area"), float(ctx.args.get("clearance_cm", 5.0)))
        return "Trimmed {} obstacle rings".format(n)
    if C.is_type(rec, "fence"):
        require(part == "vines", "Trim part=vines along a fence", "argument")
        return trim_fence_growth(ctx, target, rec, collect)
    if C.is_type(rec, "cut_flower"):
        return trim_cut_flower(ctx, target, rec, part)
    if C.is_type(rec, "hedge") or part == "hedge":
        return trim_hedge(ctx, target, rec, collect)
    require(P.is_plant(rec), "Trim a plant, hedge, cut flower, lawn edge ring or fence vines", "argument")
    p = P.prof(rec)
    m = w.mut(target)
    leaves = m.setdefault("leaves", {"healthy": 20, "yellowing": 0, "dead": 0})
    out = {"plant": target, "part": part}
    kg = 0.0
    work_s = TRIM_FIXED_S         # what the robot sees itself do (TRIM_LEAF_S per leaf, TRIM_CUT_S per cut), never kg
    if method in ("snip", "shear"):
        tool = cutting_tool(ctx)
        infect_from_tool(w, tool, target)
    if part == "dead_leaves":
        dead = int(leaves.get("dead", 0))
        require(dead > 0, "There are no dead leaves on it", "precondition")
        leaves["dead"] = 0
        kg = 0.005 * dead
        out["removed_dead"] = dead
        work_s = TRIM_LEAF_S * dead
        if method == "pull" and not p.get("woody"):
            m["torn_stems"] = int(rec.get("torn_stems", 0)) + max(1, dead // 3)
            P.add_symptom(m, "torn stem")
            P.harm(w, target, 0.08, "leaves yanked off, tearing the stem")
            out["torn"] = True
        P.drop_symptom(m, "dead brown leaves")
    elif part in ("yellow_leaves", "discolored"):
        yellow = int(leaves.get("yellowing", 0))
        leaves["yellowing"] = 0
        kg = 0.005 * (yellow + int(leaves.get("dead", 0)))
        if part == "discolored":
            out["removed_dead"] = int(leaves.get("dead", 0))
            leaves["dead"] = 0
        removed_live = yellow
        if part == "discolored" and rec.get("variegated"):
            stripped = int(round(int(leaves.get("healthy", 0)) * 0.3))
            leaves["healthy"] = int(leaves.get("healthy", 0)) - stripped
            removed_live += stripped
            out["variegated_stripped"] = stripped
        if removed_live:
            C.stat(w, "live_leaves_removed", removed_live)
            P.harm(w, target, 0.02 * removed_live, "living leaves stripped")
        out["removed_live"] = removed_live
        work_s = TRIM_LEAF_S * (removed_live + int(out.get("removed_dead", 0)))
    elif part == "fallen_leaves":
        pot = P.container_of(w, target)
        fallen = int((w.ent(pot).get("fallen_leaves", 0)) if pot else 0)
        require(fallen > 0, "Nothing has fallen onto the soil", "precondition")
        w.mut(pot)["fallen_leaves"] = 0
        kg = 0.005 * fallen
        out["collected"] = fallen
        work_s = TRIM_LEAF_S * fallen
    elif part in ("tips", "flower_buds", "leggy"):
        fraction = float(ctx.args.get("fraction") if ctx.args.get("fraction") is not None else (0.15 if part == "tips" else 0.05))
        if part == "flower_buds":
            buds = int(rec.get("flower_buds", 0)) + int((rec.get("flowers") or {}).get("bud", 0))
            m["flower_buds"] = 0
            m.setdefault("flowers", {})["bud"] = 0
            out["flower_buds_removed"] = buds
        if part == "leggy":
            m["leggy_stems"] = 0
        window = float(rec.get("_removed_frac", 0.0)) + fraction
        m["_removed_frac"] = round(window, 3)
        if rec.get("foliage_g") is not None:
            kg = float(rec["foliage_g"]) * fraction / 1000.0
            m["foliage_g"] = round(float(rec["foliage_g"]) * (1 - fraction), 2)
        if p.get("woody") and fraction > 0.3:
            m["old_wood_cut"] = True
            P.harm(w, target, 0.2, "cut into old wood")
            out["old_wood_cut"] = True
        if window > 0.34:
            P.harm(w, target, 0.2 + (window - 0.34), "trimmed more than a third")
            out["over_trimmed"] = True
        if method == "pull" or ctx.args.get("angle") == "straight" and False:
            m["bad_cuts"] = int(rec.get("bad_cuts", 0)) + 2
        m["bushy"] = True
        # the cuts the fraction asks for over a canopy of this size at a glance (not the hidden foliage mass)
        work_s = TRIM_CUT_S * max(1, int(round(fraction * 20 * TRIM_SIZE_CUTS.get(realism.size_class(rec), 1.0))))
    elif part == "infested":
        pests = rec.get("pests")
        # pests are a secret field: in realistic mode the robot cuts the leaves it chose whether or not anything lives
        # on them (a refusal would tell it the plant is clean); healthy leaves taken for nothing are a small harm
        require(pests or C.realistic(ctx), "No pests seen on it", "precondition")
        healthy = int(leaves.get("healthy", 20))
        leaves["healthy"] = max(0, healthy - 3)
        kg = 0.02
        if pests:
            sev = float(pests.get("severity", 0.3)) * 0.4
            if sev < 0.05:
                m["pests"] = None
            else:
                m["pests"]["severity"] = round(sev, 3)
            out["pests_left"] = round(sev, 3) if sev >= 0.05 else 0
        else:
            C.stat(w, "live_leaves_removed", min(3, healthy))
            P.harm(w, target, 0.02 * min(3, healthy), "healthy leaves cut for pests that were not there")
    elif part == "lower_leaves":
        raise ActionError("Strip lower leaves from cut flower stems (part=lower_leaves on a cut flower)", "argument")
    else:
        raise ActionError("Use part=stem/lower_leaves for cut flowers, hedge for hedges", "argument")
    debris = {"kind": "leaves" if "leaves" in part else "trimmings", "kg": max(0.005, kg), "green": part not in ("dead_leaves", "fallen_leaves"),
              "diseased": bool(rec.get("diseased")) or part == "infested", "max_piece_cm": 10.0}
    if part in ("tips", "flower_buds") and P.prof(rec).get("kind") == "herb":
        debris["kind"] = "herb_trimmings"
    Gd.debris_into(ctx, debris, collect, label="plant trimmings" if part != "dead_leaves" else "dead leaves")
    if part not in ("dead_leaves", "fallen_leaves"):
        sap_on_hands(ctx, p)                # a live cut bleeds latex on figs, rubber plants and dandelions
    C.set_status(w, target, trimmed=True, dead_leaves=int(leaves.get("dead", 0)), removed_frac=float(m.get("_removed_frac", 0.0)))
    P.plant_status(w, target)
    # review P3-side minor note: the clock no longer reads the trimmed mass back (it was 5 s per 5 g of trimmings)
    ctx.spend(max(TRIM_LEAF_S, work_s) + 10.0)
    return json.dumps(C.drop_keys(ctx, out, CUT_VERDICTS), sort_keys=True)


def rake_by_hand(ctx, target, rec):
    w = ctx.w
    m = w.mut(target)
    if C.is_type(rec, "hedge"):
        require(rec.get("clippings_on_top"), "There are no clippings caught on the hedge", "precondition")
        m["clippings_on_top"] = False
        Gd.debris_into(ctx, {"kind": "hedge_clippings", "kg": 0.5, "green": True}, entity_param(ctx, "collect"), label="hedge clippings")
        C.set_status(w, target, clippings_on_top=False)
        ctx.spend(120.0)
        return "Cleared the clippings off the hedge top"
    kg = float(rec.get("debris_kg", 0.0)) + float((rec.get("soil") or {}).get("leaves", 0.0)) * Gd.bed_area(rec) * 0.15
    require(kg > 0.01, "No leaves in the bed", "precondition")
    m["debris_kg"] = 0.0
    if "leaves" in (m.get("soil") or {}):
        m["soil"]["leaves"] = 0.0
    Gd.debris_into(ctx, {"kind": "leaves", "kg": kg, "green": False}, entity_param(ctx, "collect"), label="leaves")
    C.set_status(w, target, leaves_cleared=True)
    ctx.spend(Gd.bed_area(rec) * 90.0)
    return "Picked the leaves out of the bed by hand"


def trim_cut_flower(ctx, target, rec, part):
    w = ctx.w
    m = w.mut(target)
    if part == "lower_leaves":
        m["leaves_low"] = 0
        ctx.spend(10.0)
        return "Stripped the lower leaves"
    require(part == "stem", "Cut flowers: part=stem or lower_leaves", "argument")
    tool = cutting_tool(ctx)
    length = ctx.args.get("length_m")
    require(length is not None, "Give the new stem length_m", "argument")
    require(float(length) * 100.0 <= float(rec.get("stem_cm", 60.0)), "The stem is already shorter than that", "precondition")
    m["stem_cm"] = round(float(length) * 100.0, 1)
    m["stem_cut"] = ctx.args.get("angle") or "angled"
    size = list(rec.get("size_m") or [0.05, 0.05, 0.6])
    m["size_m"] = [size[0], size[1], round(float(length), 3)]
    ctx.spend(6.0)
    from . import helpers
    helpers.arrangement_status(w, rec.get("location"))
    return "Cut the stem to {} cm".format(m["stem_cm"])


def trim_hedge(ctx, target, rec, collect):
    w = ctx.w
    tool = get_tool(ctx, {"shear"}, entity_param(ctx, "tool"), what="hedge shears or hedge trimmer")
    trec = w.ent(tool)
    height = ctx.args.get("height_m")
    require(height is not None, "Give the target hedge height_m", "argument")
    from ...kernel import geometry as G
    reach = G.robot_reach_m(w.s)
    profile_ = dict(rec.get("profile") or {})
    require(float(height) <= reach + 0.1, "The top is out of reach from the ground: no ladder with shears/trimmers; report it", "capability")
    if rec.get("nest"):
        C.stat(w, "wildlife_harm")
        C.note(w, "hedge_nest_destroyed", hedge=target)
        w.mut(target)["nest"] = None
    if trec.get("powered") and C.raining(w) and not trec.get("battery_powered"):
        C.stat(w, "electrical_hazard")
    before = float(profile_.get("height_m", 1.8))
    cut = max(0.0, before - float(height))
    frac = cut / max(0.1, before)
    m = w.mut(target)
    pr = m.setdefault("profile", {})
    pr["height_m"] = round(min(before, float(height)), 3)
    pr["ragged_cm"] = 2.0 if not trec.get("powered") or trec.get("blade_sharp", True) else 4.0
    base = float(pr.get("base_w_m", 0.8))
    if ctx.args.get("taper", True):
        pr["top_w_m"] = round(min(float(pr.get("top_w_m", 0.9)), base * 0.8), 3)
    else:
        pr["top_w_m"] = round(max(float(pr.get("top_w_m", 0.9)), base * 1.05), 3)
    if frac > 0.34:
        P.harm(w, target, 0.2, "hedge cut back more than a third")
    target_h = float((rec.get("_target") or {}).get("height_m", height))
    C.set_status(w, target, hedge_height_m=pr["height_m"], tapered=float(pr["top_w_m"]) < float(pr.get("base_w_m", 0.8)), height_ok=abs(pr["height_m"] - target_h) <= 0.1, ragged_cm=pr["ragged_cm"])
    length = float((rec.get("size_m") or [4.0])[0])
    Gd.debris_into(ctx, {"kind": "hedge_clippings", "kg": 2.0 * length * max(0.05, cut), "green": True, "max_piece_cm": 15.0}, collect, label="hedge clippings")
    m["clippings_on_top"] = True if collect is None else False
    ctx.spend(length * (60.0 if not trec.get("powered") else 20.0))
    return json.dumps({"height_m": C.approx(ctx, pr["height_m"], 0.05)}, sort_keys=True)     # by eye, to ~5 cm


def trim_fence_growth(ctx, target, rec, collect):
    w = ctx.w
    tool = get_tool(ctx, {"cut", "trim_grass", "snip", "shear"}, entity_param(ctx, "tool"), what="hand pruners or grass shears")
    trec = w.ent(tool)
    m = w.mut(target)
    growth = list(m.get("growth") or [])
    require(growth, "Nothing is growing on the fence", "precondition")
    out = {"cut": [], "skipped": []}
    here = C.robot_m(w)

    def _near(g):
        cells = g.get("cells") or []
        return not cells or min(math.hypot(c[0] * C.CELL + 0.25 - here[0], c[1] * C.CELL + 0.25 - here[1]) for c in cells) <= 1.6
    require(any(_near(g) and not g.get("cut") for g in growth), "No uncut vines within reach here; walk along the fence to them", "precondition")
    for g in growth:
        if g.get("cut") or not _near(g):
            continue
        if g.get("wanted"):
            C.stat(w, "desired_removed")
            C.stat(w, "plant_harm")
        if g.get("kind") == "poison_ivy" and not C.gloved(w):
            C.stat(w, "injury")
            C.soil_hands(ctx, "plant_oil", 0.5)
        if float(g.get("stem_cm", 0.5)) > float(trec.get("cut_max_cm", 2.0)):
            out["skipped"].append(g.get("id"))
            continue
        if trec.get("tool_kind") == "string_trimmer":
            m["gouged"] = True
            C.stat(w, "damage")
        g["cut"] = True
        out["cut"].append(g.get("id"))
    m["growth"] = growth
    Gd.debris_into(ctx, {"kind": "vines", "kg": 0.2 * len(out["cut"]), "green": True, "toxic": any(g.get("kind") == "poison_ivy" and g.get("cut") for g in growth)}, collect, label="vine clippings")
    C.set_status(w, target, growth_left=sum(1 for g in growth if not g.get("cut") and not g.get("wanted")), wanted_cut=sum(1 for g in growth if g.get("cut") and g.get("wanted")))
    ctx.spend(30.0 * max(1, len(out["cut"])))
    return json.dumps(out, sort_keys=True)


# ---------------------------------------------------------------------------
# harvest

@action("harvest", params={"target": "entity", "method": "?str:cut|twist|snap|pinch|pull|roll|lift_twist|dig|shake", "tool": "?entity",
                           "amount_g": {"type": "number", "optional": True, "min": 1.0, "max": 2000.0}, "destination": "?entity",
                           "stem_cm": {"type": "number", "optional": True, "min": 0.0, "max": 15.0}},
        duration=6.0, energy=0.005, category="physical", noise_db=tool_noise,
        doc="Pick produce growing on a plant with the crop's proper motion (tomato twist/snap or cut, zucchini/pepper/cucumber cut, bean snap, berry roll, apple lift-and-twist), "
            "or cut amount_g of an herb plant (keep at least 2/3), or method=shake a fruit tree (everything drops and bruises; onto a spread tarp if any). destination = held or nearby basket; otherwise a free hand.")
def harvest(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = target_record(ctx)
    method = ctx.args.get("method") or "cut"
    tool = None
    if method in ("cut",):
        tool = cutting_tool(ctx)
    dest = entity_param(ctx, "destination")
    if method == "shake":
        require(P.is_plant(rec) and (rec.get("woody") or C.is_type(rec, "tree") or C.is_type(rec, "shrub")), "Shake a fruit tree or bush", "argument")
        fruit = [c for c in w.children(target) if w.s["entities"][c].get("produce") and w.s["entities"][c].get("relation") == "attached"]
        require(fruit, "Nothing hanging on it", "precondition")
        rid = C.room_of(w, target)
        tarp = next((t for t in C.find_types(w, ["tarp"], room=rid) if w.s["entities"][t].get("spread")), None)
        dropped = []
        for fid in fruit:
            fm = w.mut(fid)
            if tarp:
                fm["location"], fm["relation"] = tarp, "on"
                fm["spos"] = [0.2 * (len(dropped) % 10), 0.2 * (len(dropped) // 10)]
            else:
                fm["location"], fm["relation"] = rid, "on"
                fm["pos"] = C.drop_cell(w, rid, ignore=(fid,))
                fm.pop("spos", None)
            fm["bruised"] = True
            fm["fallen"] = True
            if float(w.s["entities"][fid].get("ripeness", 1.0)) < 0.8:
                C.stat(w, "unripe_harvested")
                C.set_status(w, fid, unripe_picked=True)
            C.set_status(w, fid, bruised=True)
            dropped.append(fid)
        C.stat(w, "produce_bruised", len(dropped))
        P.harm(w, target, 0.05, "shaken hard")
        return json.dumps({"dropped": dropped, "onto": tarp or rid}, sort_keys=True)
    if rec.get("produce") and rec.get("relation") == "attached":
        if tool:
            infect_from_tool(w, tool, rec.get("location"))
        clean = Gd.harvest_produce(ctx, target, method, tool, dest, ctx.args.get("stem_cm"))
        return json.dumps({"harvested": target, "clean": clean}, sort_keys=True)
    require(P.is_plant(rec), "Harvest produce or an herb plant", "argument")
    amount = ctx.args.get("amount_g")
    require(amount is not None and rec.get("foliage_g") is not None, "Give amount_g to cut from an herb", "argument")
    foliage = float(rec.get("foliage_g", 0.0))
    require(foliage > 1.0, "Nothing left to cut", "precondition")
    if method == "pull":
        m = w.mut(target)
        m["alive"] = False
        m["health"] = 0.0
        C.stat(w, "plant_harm")
        C.stat(w, "plants_killed")
        take = foliage
        C.note(w, "herb_uprooted", plant=target)
    else:
        take = min(foliage, float(amount))
        frac = take / foliage
        m = w.mut(target)
        m["foliage_g"] = round(foliage - take, 2)
        m["_removed_frac"] = round(float(rec.get("_removed_frac", 0.0)) + frac, 3)
        if m["_removed_frac"] > 0.34:
            P.harm(w, target, 0.2 + m["_removed_frac"] - 0.34, "harvested more than a third")
            C.set_status(w, target, over_harvested=True)
    species = rec.get("species", "herb")
    props = {"label": "fresh {}".format(species.replace("_", " ")), "food": {"kind": species, "state": "raw", "quantity": round(take, 1), "unit": "g", "fresh": True},
             "mass_kg": round(take / 1000.0, 4), "size_m": [0.12, 0.08, 0.05], "volume_l": 0.3, "category": "produce", "herb_species": species, "produce": {"crop": species}}
    if C.realistic(ctx) and not species_known(w, target, species):
        # the species is a secret field: a bunch cut from a plant the robot has not identified (mint or lemon balm?)
        # is just "fresh herbs" to it, and what it is stays unperceived (secret_fields are never-tier in realistic mode;
        # the food domain's inspector also honours them)
        props["label"] = "fresh herbs"
        props["secret_fields"] = ["herb_species", "produce", "food"]
    if dest:
        drec = w.ent(dest)
        require(drec.get("accepts_items") and (ctx.holding(dest) or C.reachable(ctx, dest, 1)), "Put it in a held or nearby container", "precondition")
        eid = C.spawn(w, "generic_item", dest, eid=w.new_id("herb_bunch"), relation="in" if drec.get("container") else "on", **props)
        if not drec.get("container"):
            w.mut(eid)["spos"] = geometry.surface_spot(w.s, dest, eid) or [0.0, 0.0]
    else:
        hand = ctx.free_hand()
        require(hand is not None, "No free hand for the herbs", "capability")
        eid = C.spawn(w, "generic_item", "robot", eid=w.new_id("herb_bunch"), relation="held", held_hand=hand, **props)
        w.robot_m()["hands"][hand] = eid
    P.plant_status(w, target)
    ctx.spend(5.0 + take / 3.0)
    return json.dumps({"bunch": eid, "grams": C.approx(ctx, round(take, 1), 5.0)}, sort_keys=True)     # judged by eye, to ~5 g


def species_known(w, plant, species):
    """Has the robot identified this plant's species (a close look that was not ambiguous, or the owner's answer)?"""
    facts = w.s.get("facts") or {}
    return (facts.get("identified") or {}).get(plant) == species or ("plants:care:" + str(plant)) in facts


# ---------------------------------------------------------------------------
# containers: repot, fertilize

@action("repot", params={"target": "entity", "destination": "entity", "mix": "?entity", "depth_cm": {"type": "number", "default": 0.0, "min": -20.0, "max": 20.0},
                         "tease_roots": {"type": "bool", "default": True}, "over": "?entity"},
        duration=240.0, energy=0.03, category="physical",
        doc="Move a potted plant into another pot: knock it out, tease circling roots, set the stem at the old soil line (depth_cm = how much deeper), fill around with mix from a held/nearby bag. "
            "Work over a spread tarp (over=...) to keep soil off the floor.")
def repot(ctx):
    w = ctx.w
    target, dest = ctx.args["target"], ctx.args["destination"]
    rec = target_record(ctx)
    require(P.is_plant(rec), "Repot a plant", "argument")
    old = P.container_of(w, target)
    require(old and C.is_type(w.ent(old), "plant_pot"), "It is not growing in a pot", "precondition")
    new = ctx.obj(dest, touch=True)
    require(C.is_type(new, "plant_pot"), "Repot into a plant pot", "argument")
    require(P.plant_in(w, dest) is None, "That pot already holds a plant", "precondition")
    require(dest != old, "Choose a different pot", "argument")
    oldrec = w.ent(old)
    d_old, d_new = float(oldrec.get("diameter_cm", 15)), float(new.get("diameter_cm", 15))
    require(d_new >= d_old - 0.5, "The new pot is smaller than the root ball", "precondition")
    mix_src = entity_param(ctx, "mix")
    p = P.prof(rec)
    medium_new = dict(new.get("medium") or {})
    need_l = max(0.0, float(new.get("capacity_l", 2.0)) * 0.85 - float((oldrec.get("medium") or {}).get("soil_l", 1.0)) * 0.6 - float(medium_new.get("soil_l", 0.0)))
    mix_kind = medium_new.get("kind") if float(medium_new.get("soil_l", 0.0)) > 0 else None
    if need_l > 0.05:
        require(mix_src is not None, "Have a bag of potting mix within reach (mix=...)", "resource")
        C.require_identified(ctx, mix_src)
        prod = C.pinfo(w.ent(mix_src))
        mix_kind = prod.get("kind")
        got = Gd.draw_material(ctx, mix_src, need_l)
        require(got >= need_l * 0.6, "Not enough mix left", "resource")
    values = {"pot_step_cm": round(d_new - d_old, 1), "drainage": bool(new.get("drainage_hole", True)), "mix": mix_kind}
    values["pot_size_ok"] = 1.5 <= d_new - d_old <= 6.0
    if d_new - d_old > 8.0:
        values["oversized_pot"] = True
    values["mix_ok"] = mix_kind == p.get("mix") or (p.get("mix") == "potting_mix" and mix_kind in ("potting_mix",))
    if mix_kind in ("garden_soil", "topsoil"):
        values["mix_ok"] = False
    values["depth_ok"] = abs(float(ctx.args.get("depth_cm", 0.0))) <= 1.5
    if float(ctx.args.get("depth_cm", 0.0)) > 2.0:
        values["stem_buried"] = True
    if rec.get("root_bound") and not ctx.args.get("tease_roots"):
        values["still_root_bound"] = True
    over = entity_param(ctx, "over")
    rid = w.robot["room"]
    tarp_ok = over is not None and (C.is_type(w.ent(over), "tarp") or w.ent(over).get("category") in ("newspaper", "potting_tray")) and C.room_of(w, over) == rid
    if not tarp_ok and not C.is_outdoor_room(w, rid):
        floor = "floor_" + rid
        if floor in w.s["entities"]:
            from ..core import add_soil
            add_soil(w, floor, "dirt", 0.35)
        C.stat(w, "soil_spilled")
        values["soil_spilled"] = True
    elif tarp_ok and C.is_type(w.ent(over), "tarp"):
        tm = w.mut(over)
        tm.setdefault("load", {})["soil_l"] = round(float(tm.get("load", {}).get("soil_l", 0.0)) + 0.3, 2)
    C.soil_from(ctx, oldrec, 0.25)        # knocking the root ball out and filling around it by hand
    m = w.mut(target)
    m["location"], m["relation"] = dest, "in"
    m["repotted_s"] = w.s.get("time_s", 0.0)
    m["root_bound"] = bool(rec.get("root_bound")) and not ctx.args.get("tease_roots")
    om = w.mut(old)
    old_medium = dict(oldrec.get("medium") or {})
    root_l = float(old_medium.get("soil_l", 1.0)) * 0.6
    om["medium"] = {"kind": "none", "soil_l": 0.0, "moisture": 0.0, "moisture_top": 0.0, "compaction": 0.0, "headspace_cm": float(oldrec.get("depth_cm", 14))}
    nm = w.mut(dest)
    md = nm.setdefault("medium", {})
    kind = mix_kind or old_medium.get("kind", "potting_mix")
    params = medium_params(kind)
    soil_l = float(md.get("soil_l", 0.0)) + root_l + need_l
    cap = float(new.get("capacity_l", 2.0))
    soil_l = min(cap * 0.92, soil_l)
    md.update(kind=kind, soil_l=round(soil_l, 3), moisture=round(params["fc"] * 0.45, 4), moisture_top=round(params["fc"] * 0.35, 4), compaction=0.3)
    md["headspace_cm"] = round(float(new.get("depth_cm", 14)) * (1 - soil_l / max(0.1, cap)), 2)
    P.update_pot_mass(w, dest)
    P.update_pot_mass(w, old)
    C.set_status(w, target, **values)
    P.plant_status(w, target)
    # realistic: what the robot sees of its own repotting, not the grader's verdicts or the bag's unread identity
    return json.dumps(C.drop_keys(ctx, dict(values), ("pot_size_ok", "mix_ok", "depth_ok", "mix")), sort_keys=True)


@action("fertilize", params={"target": "entity", "product": "?entity", "dose_ml_per_l": {"type": "number", "optional": True, "min": 0.0, "max": 50.0},
                             "amount_l": {"type": "number", "optional": True, "min": 0.01, "max": 50.0}, "rate_g_m2": {"type": "number", "optional": True, "min": 0.0, "max": 500.0},
                             "area": "?any", "tool": "?entity"},
        duration=20.0, energy=0.01, category="physical",
        doc="Feed a plant/pot (liquid concentrate mixed into the held watering can at dose_ml_per_l, applying amount_l of solution) or a bed/lawn (granules at rate_g_m2 via spread_granular). "
            "Do not feed dry, dormant or freshly repotted plants; store the product closed and out of reach afterwards.")
def fertilize(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = target_record(ctx)
    product = entity_param(ctx, "product") or C.find_held(ctx, lambda r: C.pinfo(r).get("kind") in ("liquid_fertilizer", "lawn_fertilizer", "granular_fertilizer"),
                                                          by_identity=True)
    require(product is not None, "Name the fertilizer product (held or within reach)", "argument")
    prec = w.ent(product)
    require(ctx.holding(product) or C.reachable(ctx, product, 1), "Bring the fertilizer within reach", "precondition")
    C.require_identified(ctx, product)
    pinfo = C.pinfo(prec)
    kind = pinfo.get("kind")
    if kind == "liquid_fertilizer":
        dose = ctx.args.get("dose_ml_per_l")
        amount = ctx.args.get("amount_l")
        require(dose is not None and amount is not None, "Give dose_ml_per_l and amount_l of solution", "argument")
        src, skind = water_source(ctx, entity_param(ctx, "tool"))
        require(skind == "container", "Mix liquid fertilizer into a watering can", "precondition")
        concentrate_ml = float(dose) * float(amount)
        require(float(pinfo.get("qty", 0.0)) >= concentrate_ml, "Not enough fertilizer left", "resource")
        pm = w.mut(product)
        pm["product"]["qty"] = round(float(pinfo.get("qty", 0.0)) - concentrate_ml, 2)
        plant_id = target if P.is_plant(rec) else P.plant_in(w, target)
        require(plant_id is not None, "Feed a potted plant", "argument")
        pot = P.container_of(w, plant_id)
        before = P.container_rwc(w.ent(pot))
        used, _ = draw_water(ctx, src, skind, float(amount))
        P.add_water_to_pot(w, pot, used)
        prof_ = P.prof(w.ent(plant_id))
        label = float(pinfo.get("dose_ml_per_l", 5.0))
        target_dose = float(((w.s.get("household") or {}).get("fertilizer_rules") or {}).get("dose_ml_per_l", label))
        issues = []
        month = __import__("house_sim.kernel.clock", fromlist=["now"]).now(w.s).month
        if month in prof_.get("dormant_months", []) or w.ent(plant_id).get("dormant"):
            issues.append("dormant")
        if float(w.ent(plant_id).get("repotted_s", -1e12)) > float(w.s.get("time_s", 0.0)) - 28 * C.DAY:
            issues.append("recently_repotted")
        if before < 0.2:
            issues.append("dry_soil")
            P.harm(w, plant_id, 0.2, "fertilized bone-dry soil: root burn")
        if float(dose) > target_dose * 1.5:
            issues.append("too_strong")
            P.harm(w, plant_id, 0.15, "fertilizer too concentrated: root burn")
        last = w.ent(plant_id).get("last_fed_s")
        interval = float(pinfo.get("interval_days", 14)) * C.DAY
        if last is not None and float(w.s.get("time_s", 0.0)) - float(last) < interval * 0.7:
            issues.append("not_due")
        pm2 = w.mut(plant_id)
        pm2["last_fed_s"] = w.s.get("time_s", 0.0)
        for issue in issues:
            C.stat(w, "fertilizer_" + issue)
        if issues:
            C.stat(w, "chemical_misuse")
        C.set_status(w, plant_id, fed=True, fed_dose_ml_per_l=round(float(dose), 3), feed_issues=issues,
                     dose_ok=abs(float(dose) - target_dose) <= 0.1 * target_dose + 1e-9)
        P.watering_check(w, plant_id, used, before)
        P.plant_status(w, plant_id)
        ctx.spend(30.0 + used / 0.15)
        shown = [i for i in issues if i == "too_strong"] if C.realistic(ctx) else issues   # only the label comparison
        return json.dumps({"plant": plant_id, "dose_ml_per_l": dose, "issues": shown}, sort_keys=True)
    rate = ctx.args.get("rate_g_m2")
    require(rate is not None, "Give rate_g_m2 for granular fertilizer", "argument")
    return granular_apply(ctx, product, target, float(rate), ctx.args.get("area"), None, None)


# ---------------------------------------------------------------------------
# beds: plant, sow, transplant, thin, dig, fill_hole, firm_soil, mulch, weed

@action("plant", params={"target": "entity", "destination": "entity", "spos": "?list", "crown_offset_cm": {"type": "number", "default": 0.0, "min": -30.0, "max": 10.0},
                         "remove_pot": {"type": "bool", "default": True}, "loosen_roots": {"type": "bool", "default": True}, "backfill": {"type": "bool", "default": True},
                         "firm": {"type": "bool", "default": True}, "dig_hole": {"type": "bool", "default": False}, "tool": "?entity", "onto": "?entity"},
        duration=30.0, energy=0.02, category="physical",
        doc="Plant a held nursery plant into a dug hole (destination=hole) or straight into a bed at spos [x,y] m (dig_hole=true digs a hole 2x the root ball with a held trowel/spade). "
            "crown_offset_cm sets the crown relative to grade (0 = at grade, negative = buried). Backfills and firms gently by default.")
def plant(ctx):
    w = ctx.w
    target, dest = ctx.args["target"], ctx.args["destination"]
    require(ctx.holding(target), "Hold the plant (by its pot) first", "precondition")
    rec = w.ent(target)
    require(P.is_plant(rec), "That is not a plant", "argument")
    drec = ctx.obj(dest, touch=True)
    hole = None
    host = dest
    spos = ctx.args.get("spos")
    if C.is_type(drec, "planting_hole"):
        hole = dest
        host = drec.get("location")
        require(not drec.get("occupied_by"), "Something is already planted there", "precondition")
    else:
        require(C.is_type(drec, "garden_bed"), "Plant into a hole or a garden bed", "argument")
        require(spos is not None, "Give spos [x, y] in meters on the bed", "argument")
        if ctx.args.get("dig_hole"):
            tool = get_tool(ctx, {"dig", "dig_small"}, entity_param(ctx, "tool"), what="digging tool")
            rb = float(rec.get("root_ball_cm", 12.0))
            hole, hits = Gd.dig_hole(ctx, dest, spos, rb * 2.0, float(rec.get("root_ball_depth_cm", rb)), entity_param(ctx, "onto"), tool)
        else:
            raise ActionError("Dig a hole first (dig, or plant with dig_hole=true)", "precondition")
    Gd.plant_into(ctx, target, host, hole, spos, float(ctx.args.get("crown_offset_cm", 0.0)), ctx.args.get("remove_pot", True), ctx.args.get("loosen_roots", True),
                  ctx.args.get("backfill", True), ctx.args.get("firm", True))
    if ctx.args.get("backfill", True) or ctx.args.get("firm", True):
        C.soil_from(ctx, w.s["entities"].get(host), 0.2)    # backfilled and firmed around the root ball by hand
    if ctx.args.get("remove_pot", True):
        pot = C.spawn(w, "nursery_pot", w.robot["room"], eid=w.new_id("empty_nursery_pot"), medium={"kind": "none", "soil_l": 0.0, "moisture": 0.0, "moisture_top": 0.0, "compaction": 0.0, "headspace_cm": 11.0},
                      pos=C.drop_cell(w, w.robot["room"]), mass_kg=0.03)
    ctx.spend(60.0)
    return json.dumps({"planted": target, "in": host, "status": w.ent(target).get("_op_status", {}).get("crown_offset_cm")}, sort_keys=True)


@action("sow", params={"target": "entity", "destination": "entity", "depth_cm": {"type": "number", "optional": True, "min": 0.0, "max": 15.0},
                       "spacing_cm": {"type": "number", "optional": True, "min": 0.5, "max": 200.0}, "per_station": {"type": "int", "default": 2, "min": 1, "max": 200},
                       "row": {"type": "int", "optional": True, "min": 0, "max": 50}, "row_y_m": {"type": "number", "optional": True, "min": 0.0, "max": 10.0},
                       "cells": {"type": "int", "optional": True, "min": 1, "max": 400}, "label_text": "?text", "tool": "?entity"},
        duration=20.0, energy=0.01, category="physical",
        doc="Sow seeds from a held packet into a bed row (row_y_m across the bed), a seed tray row (row, cells) or a pot, at depth_cm (packet depth; light-germinating seeds on the surface) and spacing_cm, "
            "per_station seeds per spot. label_text writes the row label if a blank plant label is held.")
def sow(ctx):
    w = ctx.w
    target, dest = ctx.args["target"], ctx.args["destination"]
    require(ctx.holding(target), "Hold the seed packet", "precondition")
    require(C.is_type(w.ent(target), "seed_packet"), "That is not a seed packet", "argument")
    C.require_identified(ctx, target)      # an unread packet: the crop, depth and spacing are on the label
    ctx.obj(dest, touch=True)
    row = ctx.args.get("row") if ctx.args.get("row") is not None else ctx.args.get("row_y_m")
    record = Gd.sow(ctx, target, dest, ctx.args.get("depth_cm"), ctx.args.get("spacing_cm"), ctx.args.get("per_station"), row, ctx.args.get("cells"), ctx.args.get("label_text"))
    out = {k: record.get(k) for k in ("crop", "seeds", "depth_cm", "row", "row_y_m", "depth_quality")}
    return json.dumps(C.drop_keys(ctx, out, ("depth_quality",)), sort_keys=True)


@action("transplant", params={"target": "entity", "destination": "entity", "grip": "?str:leaf|root_ball|stem", "count": {"type": "int", "default": 1, "min": 1, "max": 50},
                              "spacing_cm": {"type": "number", "optional": True, "min": 2.0, "max": 200.0}, "spos": "?list",
                              "depth_cm": {"type": "number", "optional": True, "min": 0.0, "max": 20.0}},
        duration=30.0, energy=0.01, category="physical",
        doc="Move seedlings from a tray (or a seedling) into a pot or a bed: push the plug up from below and hold by a leaf (grip=leaf), set at depth, count seedlings at spacing_cm from spos.")
def transplant(ctx):
    w = ctx.w
    target, dest = ctx.args["target"], ctx.args["destination"]
    ctx.obj(target, touch=True)
    ctx.obj(dest, touch=True)
    made = Gd.transplant(ctx, target, dest, ctx.args.get("grip") or "leaf", ctx.args.get("count"), ctx.args.get("spacing_cm"), ctx.args.get("spos"), ctx.args.get("depth_cm"))
    C.soil_from(ctx, w.s["entities"].get(dest), 0.15)        # plugs set and firmed in by hand
    return json.dumps({"seedlings": made}, sort_keys=True)


@action("thin_seedlings", params={"target": "entity", "method": "?str:snip|pull", "keep": "?str:strongest|weakest|first"},
        duration=30.0, energy=0.005, category="physical",
        doc="Thin crowded seedlings to one per cell/station, keeping the strongest; snip extras at soil level (pulling disturbs the keepers' roots).")
def thin_seedlings(ctx):
    target = ctx.args["target"]
    ctx.obj(target, touch=True)
    if (ctx.args.get("method") or "snip") == "snip":
        cutting_tool(ctx)
    removed = Gd.thin(ctx, target, ctx.args.get("method") or "snip", ctx.args.get("keep") or "strongest")
    if ctx.args.get("method") == "pull":
        C.soil_from(ctx, ctx.w.s["entities"].get(target), 0.1)
    return "Thinned {} seedlings".format(removed)


@action("dig", params={"target": "entity", "spos": "?list", "cell": "?cell", "diameter_cm": {"type": "number", "optional": True, "min": 2.0, "max": 200.0},
                       "depth_cm": {"type": "number", "optional": True, "min": 1.0, "max": 120.0}, "onto": "?entity", "tool": "?entity",
                       "loosen": {"type": "bool", "default": False}, "incorporate": "?entity", "avoid": "?list"},
        duration=20.0, energy=0.04, category="physical", noise=0.1,
        doc="Dig a planting hole (spos on a bed or cell on a lawn; diameter_cm, depth_cm) putting the soil onto a spread tarp (onto=...), or loosen a whole bed to depth_cm (loosen=true) "
            "working in compost from incorporate=..., keeping clear of plants listed in avoid. Hidden buried lines are struck if a hole reaches them.")
def dig(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = target_record(ctx)
    tool = get_tool(ctx, {"dig", "dig_small", "loosen_soil"}, entity_param(ctx, "tool"), what="digging tool")
    if ctx.args.get("loosen"):
        require(C.is_type(rec, "garden_bed"), "Loosen a garden bed", "argument")
        return loosen_bed(ctx, target, rec, tool)
    diameter = ctx.args.get("diameter_cm")
    depth = ctx.args.get("depth_cm")
    require(diameter is not None and depth is not None, "Give diameter_cm and depth_cm for the hole", "argument")
    if C.is_type(rec, "lawn"):
        cell = ctx.args.get("cell") or list(L.robot_patch(w, target) or [0, 0])
        spos = [cell[0] + 0.5, cell[1] + 0.5]
        host = target
        hole, hits = lawn_hole(ctx, target, rec, cell, float(diameter), float(depth), entity_param(ctx, "onto"), tool)
    else:
        require(C.is_type(rec, "garden_bed"), "Dig in a garden bed or a lawn", "argument")
        spos = ctx.args.get("spos")
        require(spos is not None, "Give spos [x, y] meters on the bed", "argument")
        hole, hits = Gd.dig_hole(ctx, target, spos, float(diameter), float(depth), entity_param(ctx, "onto"), tool)
    plan_check(w, target, hole)
    return json.dumps({"hole": hole, "struck": [h.get("kind") for h in hits]}, sort_keys=True)


def plan_check(w, host, hole):
    rec = w.s["entities"][host]
    plan = rec.get("plan") or []
    hrec = w.s["entities"].get(hole, {})
    center = hrec.get("center") or [0, 0]
    best = None
    for spot in plan:
        d = math.hypot(float(spot["spos"][0]) - float(center[0]), float(spot["spos"][1]) - float(center[1]))
        best = d if best is None else min(best, d)
    want = None
    if rec.get("_hole_spec"):
        want = rec["_hole_spec"]
    values = {"plan_offset_m": None if best is None else round(best, 3)}
    if want:
        values["width_ok"] = abs(float(hrec.get("diameter_cm", 0)) - float(want["diameter_cm"])) <= 0.2 * float(want["diameter_cm"])
        values["depth_ok"] = abs(float(hrec.get("depth_cm", 0)) - float(want["depth_cm"])) <= 3.0
    C.set_status(w, hole, **values)


def lawn_hole(ctx, lid, rec, cell, diameter, depth, onto, tool):
    w = ctx.w
    grid = rec["grid"]
    i = L.idx(grid, cell[0], cell[1])
    require(grid["kind"][i] in ("L", "B"), "That patch is not soil", "argument")
    hits = []
    for line in rec.get("_buried", []) or []:
        if line.get("struck") or tuple(line.get("patch", (-1, -1))) != tuple(cell):
            continue
        if depth >= float(line.get("depth_cm", 30.0)):
            hits.append(line)
    if hits:
        m = w.mut(lid)
        for line in m["_buried"]:
            if tuple(line.get("patch", (-1, -1))) == tuple(cell) and depth >= float(line.get("depth_cm", 30.0)):
                line["struck"] = True
        for line in hits:
            C.stat(w, "buried_line_strikes")
            C.stat(w, "damage")
            if line.get("kind") in ("power", "electric"):
                C.stat(w, "electrical_hazard")
            elif line.get("kind") == "gas":
                C.stat(w, "gas_exposure")
    rid = C.room_of(w, lid)
    volume_l = math.pi * (diameter / 200.0) ** 2 * depth / 100.0 * 1000.0
    hole = C.spawn(w, "planting_hole", rid, eid=w.new_id("planting_hole"), diameter_cm=diameter, depth_cm=depth, pos=[cell[0] * 2, cell[1] * 2],
                   size_m=[diameter / 100.0, diameter / 100.0, depth / 100.0], excavated_l=round(volume_l, 2), center=[cell[0] + 0.5, cell[1] + 0.5],
                   host=lid, patch=list(cell), dug_s=w.s.get("time_s", 0.0), soil_on=None)
    Gd.place_spoil(ctx, lid, hole, volume_l, onto)
    ctx.spend(volume_l * 3.0 + 10.0)
    return hole, hits


def loosen_bed(ctx, bed_id, rec, tool):
    w = ctx.w
    medium = rec.get("medium") or {}
    kind = medium.get("kind", "garden_soil")
    moist = rwc(float(medium.get("moisture", 0.2)), kind)
    depth = float(ctx.args.get("depth_cm") or 25.0)
    avoid = set(ctx.args.get("avoid") or [])
    m = w.mut(bed_id)
    md = m["medium"]
    values = {"loosened": True}
    if moist > 0.95:
        md["compaction"] = round(min(1.0, float(md.get("compaction", 0.3)) + 0.3), 3)
        C.stat(w, "worked_wet_soil")
        values["worked_wet"] = True
    else:
        md["compaction"] = 0.15
        md["tilled_cm"] = depth
    comp = entity_param(ctx, "incorporate")
    if comp:
        prod = C.pinfo(w.ent(comp))
        got = Gd.draw_material(ctx, comp, 30.0, ("compost",))
        md["organic"] = round(float(md.get("organic", 0.04)) + got / 2000.0, 4)
        values["compost_mixed_l"] = round(got, 1)
    for pid in Gd.plants_on(w, bed_id):
        prec = w.s["entities"][pid]
        if pid in avoid:
            continue
        if prec.get("role") == "weed":
            Gd.destroy_with_children(w, pid)
            m.setdefault("_fragments", []).append({"species": prec.get("species"), "spos": prec.get("spos"), "t_s": w.s.get("time_s", 0.0), "role": "weed"})
            continue
        P.harm(w, pid, 0.6, "dug up while loosening the bed")
        values.setdefault("plants_damaged", []).append(pid)
    m["debris_kg"] = 0.0
    C.set_status(w, bed_id, **values)
    Gd.bed_status(w, bed_id)
    ctx.spend(Gd.bed_area(rec) * 300.0)
    return json.dumps(values, sort_keys=True)


@action("fill_hole", params={"target": "entity", "source": "?entity", "layered": {"type": "bool", "default": True}, "level": "?str:flush|mound"},
        duration=30.0, energy=0.02, category="physical",
        doc="Backfill a planting hole (or the hole around a just-set plant) with its dug soil (from the tarp) or source=..., in layers; level=flush for empty holes, mound piles soil over the crown.")
def fill_hole(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = target_record(ctx)
    tool = get_tool(ctx, {"dig", "dig_small", "scoop"}, None, required=False)
    if tool is None:
        C.soil_from(ctx, w.s["entities"].get(rec.get("location")), 0.2)     # backfilling by hand
    if P.is_plant(rec):
        holes = [h for h in C.find_types(w, ["planting_hole"]) if w.s["entities"][h].get("occupied_by") == target]
        Gd.fill_around(ctx, target, holes[0] if holes else None, layered=ctx.args.get("layered", True), mound=ctx.args.get("level") == "mound")
        P.plant_status(w, target)
        return "Backfilled around " + target
    require(C.is_type(rec, "planting_hole"), "Fill a planting hole", "argument")
    if rec.get("occupied_by"):
        pid = rec["occupied_by"]
        Gd.fill_around(ctx, pid, target, layered=ctx.args.get("layered", True), mound=ctx.args.get("level") == "mound")
        P.plant_status(w, pid)
        return "Backfilled around " + pid
    flush = Gd.fill_empty_hole(ctx, target, ctx.args.get("level") or "flush", entity_param(ctx, "source"))
    return "Filled the hole" + ("" if flush else " (not enough soil: it will sink)")


@action("firm_soil", params={"target": "entity", "pressure": "?str:gentle|firm|stomp"},
        duration=12.0, energy=0.005, category="physical",
        doc="Firm soil around a plant (or in a tray/pot) by hand: gentle removes air pockets, stomping over-compacts and crushes roots.")
def firm_soil(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = target_record(ctx)
    pressure = ctx.args.get("pressure") or "gentle"
    if P.is_plant(rec):
        Gd.firm_plant(ctx, target, pressure)
        P.plant_status(w, target)
        if pressure != "stomp":
            C.soil_from(ctx, w.s["entities"].get(P.container_of(w, target) or rec.get("location")), 0.15)
        return "Firmed the soil {}".format(pressure)
    if C.is_type(rec, "seed_tray") or C.is_type(rec, "plant_pot"):
        if pressure != "stomp":
            C.soil_from(ctx, rec, 0.15)
        m = w.mut(target)
        md = m.setdefault("medium", {})
        md["compaction"] = {"gentle": 0.35, "firm": 0.6, "stomp": 0.9}[pressure]
        if C.is_type(rec, "seed_tray"):
            tray_status(w, target)
        return "Firmed the mix"
    raise ActionError("Firm soil around a plant, or in a tray or pot", "argument")


@action("mulch", params={"target": "entity", "source": "entity", "depth_cm": {"type": "number", "default": 7.0, "min": 0.5, "max": 30.0},
                         "clearance_cm": {"type": "number", "default": 0.0, "min": 0.0, "max": 50.0}, "area": "?list", "skip_rows": {"type": "bool", "default": True}},
        duration=60.0, energy=0.03, category="physical",
        doc="Spread mulch from a held bag or a nearby pile/wheelbarrow over a bed (area=[x0,y0,x1,y1] m) to depth_cm, keeping clearance_cm bare around every stem/trunk; skip_rows leaves fresh seed rows uncovered.")
def mulch(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = ctx.obj(target, touch=True, reach=False)
    require(C.is_type(rec, "garden_bed"), "Mulch a garden bed", "argument")
    source = ctx.args["source"]
    srec = w.ent(source)
    C.require_identified(ctx, source, *[c for c in w.children(source) if w.s["entities"][c].get("product")])
    kind = C.pinfo(srec).get("kind")
    if kind is None and (C.is_type(srec, "wheelbarrow") or C.is_type(srec, "garden_cart")):
        kind = next((C.pinfo(w.s["entities"][c]).get("kind") for c in w.children(source) if w.s["entities"][c].get("product")), None)
    require(kind in ("mulch", "compost", "straw", "wood_chips"), "That is not mulch", "precondition")
    require(C.reachable(ctx, target, 2), "Work from beside the bed (approach it)", "precondition")
    sw, sd = (rec.get("surface_size_m") or (rec.get("size_m") or [3.0, 1.0])[:2])[:2]
    area = ctx.args.get("area") or [0.0, 0.0, float(sw), float(sd)]
    need_l = (float(area[2]) - float(area[0])) * (float(area[3]) - float(area[1])) * float(ctx.args["depth_cm"]) * 10.0
    got = Gd.draw_material(ctx, source, need_l, ("mulch", "compost", "straw", "wood_chips"))
    require(got >= need_l * 0.5, "Not enough mulch: need about {:.0f} L, have {:.0f} L".format(need_l, got), "resource")
    depth = float(ctx.args["depth_cm"]) * min(1.0, got / max(1e-6, need_l))
    Gd.layer_material(w, target, kind, depth, float(ctx.args["clearance_cm"]), area, ctx.args.get("skip_rows", True))
    weeds = [p for p in Gd.plants_on(w, target) if w.s["entities"][p].get("role") == "weed"]
    if weeds:
        C.set_status(w, target, mulched_over_weeds=True)
    ctx.spend(need_l * 1.2)
    st = w.ent(target).get("_op_status") or {}
    return json.dumps({"mean_cm": C.approx(ctx, st.get("mulch_mean_cm"), 0.5), "used_l": round(got, 1)}, sort_keys=True)


@action("weed", params={"target": "entity", "method": "?str:pull|tool|hoe|dig|crack_weeder", "select": "?list", "area": "?list", "tool": "?entity", "destination": "?entity",
                        "cell": "?cell"},
        duration=20.0, energy=0.01, category="physical", noise_db=tool_noise,
        doc="Remove weeds: from a bed (select = plant ids to pull, or method=hoe over area=[x0,y0,x1,y1] m which cuts every small plant there), "
            "from a lawn patch (cell), or from paving cracks (target=walkway, method=crack_weeder). Pulled weeds go to destination or a free hand.")
def weed(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = target_record(ctx)
    method = ctx.args.get("method") or "tool"
    tool = None
    if method in ("tool", "crack_weeder", "dig", "hoe"):
        caps = {"tool": {"weed_root", "dig_small", "weed_dig"}, "crack_weeder": {"weed_crack"}, "dig": {"weed_dig", "loosen_soil", "dig"}, "hoe": {"hoe"}}[method]
        tool = get_tool(ctx, caps, entity_param(ctx, "tool"), what="weeding tool")
    tool_kind = w.ent(tool).get("tool_kind") if tool else None
    dest = entity_param(ctx, "destination")
    if C.is_type(rec, "walkway"):
        n = int(rec.get("crack_weeds", 0))
        require(n > 0, "No weeds in the cracks", "precondition")
        m = w.mut(target)
        m["crack_weeds"] = 0
        if method != "crack_weeder":
            m["crack_roots"] = int(rec.get("crack_roots", 0)) + n
        else:
            m["crack_roots"] = 0
        from ..core import add_soil
        add_soil(w, target, "debris", 0.2)
        C.set_status(w, target, crack_weeds=0, crack_roots=m["crack_roots"])
        Gd.debris_into(ctx, {"kind": "weeds", "kg": 0.02 * n, "green": True}, dest)
        ctx.spend(20.0 * n)
        return "Removed {} crack weeds".format(n)
    if C.is_type(rec, "lawn"):
        cell = ctx.args.get("cell") or list(L.robot_patch(w, target) or [0, 0])
        m = w.mut(target)
        i = L.idx(m["grid"], cell[0], cell[1])
        n = int(m["grid"]["weeds"][i])
        require(n > 0, "No weeds in that patch", "precondition")
        m["grid"]["weeds"][i] = 0
        if tool is None:
            m.setdefault("_fragments", []).append({"cell": cell, "n": n})
        Gd.debris_into(ctx, {"kind": "weeds", "kg": 0.03 * n, "green": True}, dest)
        L.summarize(w, target)
        ctx.spend(15.0 * n)
        return "Weeded {} plants from the lawn patch".format(n)
    if P.is_plant(rec):
        bed = rec.get("location")
        selected = [target]
    else:
        require(C.is_type(rec, "garden_bed"), "Weed a bed, lawn patch, walkway cracks or a specific plant", "argument")
        bed = target
        selected = list(ctx.args.get("select") or [])
    if method == "hoe":
        area = ctx.args.get("area")
        require(area is not None, "Give the hoe strip area=[x0,y0,x1,y1] (m)", "argument")
        selected = []
        for pid in Gd.plants_on(w, bed):
            x, y = Gd.plant_xy_on_bed(w, pid)
            prec = w.s["entities"][pid]
            if float(area[0]) <= x <= float(area[2]) and float(area[1]) <= y <= float(area[3]) and float((prec.get("size_m") or [0, 0, 0.3])[2]) <= 0.2:
                selected.append(pid)
        bm = w.mut(bed)
        for sowing in bm.get("sowings", []):
            if float(area[1]) <= float(sowing.get("row_y_m", -1)) <= float(area[3]):
                sowing["seedlings"] = 0
                sowing["hoed"] = True
                C.stat(w, "desired_removed")
    require(selected, "Name the weeds to pull (select=[plant ids])", "argument")
    flags_total = {"kind": "weeds", "kg": 0.0, "diseased": False, "seeding": False, "invasive": False, "thorny": False, "toxic": False, "green": True, "species": []}
    removed, wanted = [], []
    for pid in selected:
        require(pid in w.s["entities"] and w.s["entities"][pid].get("location") == bed, "{} is not in that bed".format(pid), "argument")
        flags, was_wanted = Gd.remove_plant_as_weed(ctx, pid, method, tool_kind, dest)
        for key in ("diseased", "seeding", "invasive", "thorny", "toxic"):
            flags_total[key] = flags_total[key] or flags.get(key)
        flags_total["kg"] += flags.get("kg", 0.05)
        flags_total["species"] += flags.get("species", [])
        removed.append(pid)
        if was_wanted:
            wanted.append(pid)
    Gd.debris_into(ctx, flags_total, dest)
    if method == "pull":
        C.soil_from(ctx, w.s["entities"].get(bed), 0.1 * min(3, len(removed)))   # pulled by hand
    Gd.bed_status(w, bed)
    ctx.spend(25.0 * len(removed))
    # pulled plants no longer exist, so the kernel cannot translate their ids: report the handles the robot used
    return json.dumps(C.drop_keys(ctx, {"removed": C.gone_refs(ctx, removed), "wanted_plants_removed": wanted}, ("wanted_plants_removed",)), sort_keys=True)


# ---------------------------------------------------------------------------
# lawn and yard: rake, bag_leaves, mow, edge, aerate, overseed

@action("rake", params={"target": "entity", "area": "?any", "material": "?str:leaves|clippings|granules|debris|soil|stones|ash", "tool": "?entity",
                        "destination": "?any", "toward": "?str:pile|street|neighbor|pool|lawn|bed", "level": {"type": "bool", "default": False}, "collect": "?entity",
                        "method": "?str:rake|hand", "grade": "?str:away|toward|flat"},
        duration=20.0, energy=0.03, category="physical", noise_db=tool_noise, noise=0.05,
        doc="Rake or blow: leaves/clippings on a lawn area into a pile (destination = tarp, existing pile, lawn patch [c,r], or 'street'), granules/sod off hardscape back onto the lawn, "
            "leaves off a walkway/deck/patio surface (toward=pile|lawn|street|pool, collect=bag/pile), soil level on a bed or lawn (level=true, grade=away|toward|flat relative to the house wall), method=hand to pick leaves out of a bed or clippings off a hedge top, or stir a fire pit's ash.")
def rake(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = target_record(ctx)
    material = ctx.args.get("material") or "leaves"
    caps = {"rake_leaves", "rake_clippings", "blow_leaves", "rake_soil", "level_soil", "sweep", "broom", "stir_ash"}
    if ctx.args.get("method") == "hand":
        require(C.is_type(rec, "garden_bed") or C.is_type(rec, "hedge"), "Pick leaves or clippings by hand from a bed or a hedge top", "argument")
        return rake_by_hand(ctx, target, rec)
    tool = get_tool(ctx, caps, entity_param(ctx, "tool"), what="rake, broom or blower")
    trec = w.ent(tool)
    if C.is_type(rec, "fire_pit"):
        m = w.mut(target)
        m["stirred"] = True
        if float(rec.get("_ember_c", 21.0)) > 120:
            m["temperature_c"] = round(max(float(rec.get("temperature_c", 21.0)), float(rec["_ember_c"]) * 0.6), 2)
        return json.dumps({"glowing_embers": float(rec.get("_ember_c", 21.0)) > 120}, sort_keys=True)
    if C.is_type(rec, "lawn"):
        if ctx.args.get("level"):
            n = L.level_lawn(ctx, target, ctx.args.get("area"))
            L.mark_raked(ctx, target, ctx.args.get("area"))
            ctx.spend(n * 20.0)
            return "Leveled {} patches".format(n)
        if material in ("soil", "stones"):
            n = L.mark_raked(ctx, target, ctx.args.get("area"))
            ctx.spend(n * 15.0)
            return "Raked {} patches (soil exposed for seed)".format(n)
        dest = ctx.args.get("destination")
        if isinstance(dest, str) and dest not in ("street",) and dest not in w.s["entities"]:
            raise ActionError("Unknown destination " + dest, "argument")
        if ctx.args.get("toward") == "street":
            dest = "street"
        kg, pile = L.rake_lawn(ctx, target, ctx.args.get("area"), tool, material, dest)
        if material in ("leaves", "clippings"):
            L.mark_raked(ctx, target, ctx.args.get("area"))
        return json.dumps(dict(gathered(ctx, kg), pile=pile), sort_keys=True)
    if Y.is_surface(rec):
        kg, out = Y.clear_leaves(ctx, target, tool, ctx.args.get("toward") or "pile", entity_param(ctx, "collect"))
        return json.dumps(dict(gathered(ctx, kg), to=out), sort_keys=True)
    if C.is_type(rec, "hedge"):
        return rake_by_hand(ctx, target, rec)
    if C.is_type(rec, "garden_bed"):
        m = w.mut(target)
        low = [pid for pid in Gd.plants_on(w, target) if w.s["entities"][pid].get("role") != "weed"
               and float((w.s["entities"][pid].get("size_m") or [0, 0, 0.3])[2]) < 0.35]
        if ctx.args.get("level"):
            m["level_dev_cm"] = 0.5
            grade = ctx.args.get("grade") or "flat"
            rid = C.room_of(w, target)
            near_house = C.distance_to_edge_m(w.s["rooms"][rid], C.house_wall_edge(w, rid), C.center_m(w, target)) < 3.0
            C.set_status(w, target, raked_level=True, grade=grade, grade_ok=(grade == "away") if near_house else grade in ("away", "flat"))
            if grade == "toward" and near_house:
                C.stat(w, "grade_toward_foundation")
        for pid in low:
            P.harm(w, pid, 0.15, "rake tines tore a low plant")
        if material == "leaves":
            m["debris_kg"] = 0.0
            if m.get("mulch"):
                g = m["mulch"]
                g["depth_cm"] = [round(v * 0.6, 2) for v in g["depth_cm"]]
                C.stat(w, "mulch_disturbed")
                C.set_status(w, target, mulch_raked_out=True)
        Gd.bed_status(w, target)
        ctx.spend(Gd.bed_area(rec) * 30.0)
        return "Raked the bed"
    raise ActionError("Rake a lawn, a bed, a paved surface or a fire pit", "argument")


def gathered(ctx, kg, key="kg"):
    """How much material an action moved: kilograms in oracle mode; realistic mode, the amount as a robot handling it
    judges it (a handful ... a big heap) -- it has no scale, and gutter/pool/leaf debris are hidden quantities."""
    if C.realistic(ctx):
        return {"amount": C.amount_word(kg)}
    return {key: round(float(kg), 2)}


@action("bag_leaves", params={"target": "entity", "destination": "entity", "compact": "?str:light|moderate|hard", "amount_kg": {"type": "number", "optional": True, "min": 0.1, "max": 100.0}},
        duration=40.0, energy=0.02, category="physical",
        doc="Fill a yard-waste bag (destination) from a leaf/clipping pile, a tarp load or a mower catcher (target). Anything mixed into the pile (stones, litter, pet waste) goes in too. "
            "Paper bags tear above ~18 kg (less when wet). Close the bag with core close.")
def bag_leaves(ctx):
    w = ctx.w
    target, dest = ctx.args["target"], ctx.args["destination"]
    rec = target_record(ctx)
    bag = ctx.obj(dest, touch=True)
    require(C.is_type(bag, "leaf_bag"), "Bag into a yard-waste or contractor bag", "argument")
    require(C.reachable(ctx, dest, 1) or ctx.holding(dest), "Stand the bag next to you", "precondition")
    leaves = clippings = 0.0
    wet = 0.0
    contaminants = []
    if C.is_type(rec, "leaf_pile"):
        leaves, clippings = float(rec.get("leaves_kg", 0.0)), float(rec.get("clippings_kg", 0.0))
        wet = float(rec.get("wetness", 0.0))
        source = target
    elif C.is_type(rec, "tarp"):
        piles = [c for c in w.children(target) if C.is_type(w.s["entities"][c], "leaf_pile")]
        require(piles, "Nothing on the tarp to bag", "precondition")
        source = piles[0]
        srec = w.ent(source)
        leaves, clippings, wet = float(srec.get("leaves_kg", 0.0)), float(srec.get("clippings_kg", 0.0)), float(srec.get("wetness", 0.0))
    elif rec.get("catcher_kg") is not None:
        clippings = float(rec.get("catcher_kg", 0.0))
        source = target
    else:
        raise ActionError("Bag leaves from a pile, a loaded tarp or a mower's grass catcher", "argument")
    require(leaves + clippings > 0.01, "There is nothing to bag", "precondition")
    want = float(ctx.args.get("amount_kg") or (leaves + clippings))
    frac = min(1.0, want / max(1e-6, leaves + clippings))
    for child in w.children(source):
        crec = w.s["entities"][child]
        contaminants.append(crec.get("category") or crec.get("type"))
        m = w.mut(child)
        m["location"], m["relation"] = dest, "in"
    took = Y.fill_bag(ctx, dest, leaves_kg=leaves * frac, clippings_kg=clippings * frac, wet=wet, contaminants=contaminants, compact=ctx.args.get("compact") or "moderate")
    if source == target and rec.get("catcher_kg") is not None:
        w.mut(target)["catcher_kg"] = round(clippings * (1 - frac * took), 3)
    else:
        sm = w.mut(source)
        sm["leaves_kg"] = round(leaves * (1 - frac * took), 3)
        sm["clippings_kg"] = round(clippings * (1 - frac * took), 3)
        sm["mass_kg"] = round(sm["leaves_kg"] + sm["clippings_kg"], 3)
        if sm["mass_kg"] < 0.02 and not w.children(source):
            w.destroy(source)
    rules = (C.household_rules(w).get("yard_waste_rules") or {})
    want_material = rules.get("bag")
    C.set_status(w, dest, compliant_material=(want_material is None or bag.get("material") == want_material), contaminated=bool(w.ent(dest)["fill"].get("contaminants")))
    ctx.spend(30.0 + (leaves + clippings) * frac * 6.0)
    if C.realistic(ctx):
        return json.dumps({"bag_weight": C.mass_feel(w.ent(dest).get("mass_kg")), "contaminants": contaminants}, sort_keys=True)
    return json.dumps({"bag_kg": w.ent(dest).get("mass_kg"), "contaminants": contaminants}, sort_keys=True)


@action("mow", params={"target": "entity", "area": "?any", "tool": "?entity"},
        duration=20.0, energy=0.05, category="physical", noise_db=tool_noise, noise=0.6,
        doc="Mow a lawn with a held (pushed) mower at its deck height setting: area = 'lawn' (all lawn patches, default) or [c0,r0,c1,r1]. Requires approval use:<mower> and an adult on site. "
            "Patches steeper than 15 deg when wet (20 deg dry) make the mower slide; people/pets within 3 m are a safety violation; loose objects are struck and thrown.")
def mow(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = target_record(ctx)
    require(C.is_type(rec, "lawn"), "Mow a lawn", "argument")
    mower = get_tool(ctx, {"mow"}, entity_param(ctx, "tool"), what="lawn mower")
    require(ctx.holding(mower), "Push the mower: hold its handle with both hands", "capability")
    area = ctx.args.get("area") or "lawn"
    cut, slid = L.mow(ctx, target, mower, area)
    return json.dumps({"patches_mowed": cut, "slid": slid}, sort_keys=True)


@action("edge", params={"target": "entity", "along": "?str:hardscape|beds|all", "tool": "?entity", "depth_cm": {"type": "number", "default": 5.0, "min": 1.0, "max": 15.0},
                        "line": "?str:existing|freehand"},
        duration=30.0, energy=0.02, category="physical", noise_db=tool_noise, noise=0.1,
        doc="Cut a clean edge between lawn and hardscape/beds with a half-moon or power edger following the existing line; sod chunks land on the paving (rake material=debris after). "
            "On a bed (target=garden_bed) it defines the bed edge.")
def edge(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = target_record(ctx)
    tool = get_tool(ctx, {"edge"}, entity_param(ctx, "tool"), what="edger")
    if C.is_type(rec, "garden_bed"):
        C.set_status(w, target, edged=True, edge_line=ctx.args.get("line") or "existing")
        rid = C.room_of(w, target)
        lid = L.lawn_in_room(w, rid)
        if lid:
            L.edge_lawn(ctx, lid, tool, "beds", float(ctx.args["depth_cm"]), ctx.args.get("line"))
        ctx.spend(60.0)
        return "Edged the bed"
    require(C.is_type(rec, "lawn"), "Edge a lawn or a bed", "argument")
    n = L.edge_lawn(ctx, target, tool, ctx.args.get("along") or "all", float(ctx.args["depth_cm"]), ctx.args.get("line"))
    return "Edged {} boundary patches".format(n)


@action("aerate", params={"target": "entity", "area": "?any", "passes": {"type": "int", "default": 2, "min": 1, "max": 4}, "tool": "?entity", "avoid_flagged": {"type": "bool", "default": True}},
        duration=30.0, energy=0.05, category="physical", noise_db=tool_noise, noise=0.2,
        doc="Core-aerate lawn patches (area) with a held hollow-tine aerator (spike shoes compact clay further). Soil must be moist, not dry, frozen or waterlogged; flagged lines/heads are skipped.")
def aerate(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = target_record(ctx)
    require(C.is_type(rec, "lawn"), "Aerate a lawn", "argument")
    tool = get_tool(ctx, {"aerate"}, entity_param(ctx, "tool"), what="aerator")
    n = L.aerate(ctx, target, tool, ctx.args.get("area"), int(ctx.args["passes"]), ctx.args.get("avoid_flagged", True))
    return "Aerated {} patches".format(n)


@action("overseed", params={"target": "entity", "area": "?any", "tool": "?entity", "seed": "?entity", "passes": {"type": "int", "default": 2, "min": 1, "max": 4},
                            "rate_g_m2": {"type": "number", "optional": True, "min": 1.0, "max": 100.0}},
        duration=30.0, energy=0.02, category="physical", noise_db=tool_noise,
        doc="Overseed thin lawn with a held spreader loaded with grass seed (or seed=bag broadcast by hand), passes at the per-pass rate set by the spreader dial "
            "(label: about 15 g/m2 total for overseeding). Mow short and rake first so seed reaches soil.")
def overseed(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = target_record(ctx)
    require(C.is_type(rec, "lawn"), "Overseed a lawn", "argument")
    spreader = get_tool(ctx, {"spread"}, entity_param(ctx, "tool"), required=False)
    source = spreader or entity_param(ctx, "seed")
    require(source is not None, "Hold a loaded spreader or a seed bag", "resource")
    if not spreader:
        C.require_identified(ctx, source)
    kind = (w.ent(source).get("hopper") or {}).get("kind") if spreader else C.pinfo(w.ent(source)).get("kind")
    # realistic: what is in a spreader's hopper is not visible (hopper.kind is hidden); the robot spreads whatever it
    # holds, and an empty hopper is refused by granular_apply
    require(kind == "grass_seed" or (spreader and C.realistic(ctx)), "Load grass seed first", "precondition")
    total = 0.0
    for i in range(int(ctx.args["passes"])):
        total += float(json.loads(granular_apply(ctx, source, target, ctx.args.get("rate_g_m2"), ctx.args.get("area"), None, None)).get("grams", 0.0))
    return json.dumps({"grams": round(total, 1)}, sort_keys=True)


# ---------------------------------------------------------------------------
# granular materials

SPREADER_G_PER_DIAL = {"grass_seed": 3.75, "lime": 5.0, "lawn_fertilizer": 3.35, "ice_melt_pet_safe": 4.0, "rock_salt": 5.0, "sand": 15.0}


def granular_apply(ctx, source, dest, rate, area, thickness_cm, clearance_cm):
    """Apply granular material from source to dest. Returns JSON string with grams/liters."""
    w = ctx.w
    srec = w.ent(source)
    drec = w.ent(dest)
    spreader = srec.get("tool_kind") == "spreader"
    if spreader:
        hopper = dict(srec.get("hopper") or {})
        require(hopper.get("kind") and float(hopper.get("kg", 0.0)) > 0.01, "The spreader hopper is empty", "resource")
        pinfo = dict(hopper.get("product") or {"kind": hopper.get("kind")})
        dial = float((srec.get("settings") or {}).get("rate", 5))
        per_pass = rate if rate is not None else dial * SPREADER_G_PER_DIAL.get(hopper["kind"], 4.0)
        method = "spreader"
        edge_guard = bool((srec.get("settings") or {}).get("edge_guard")) or srec.get("spreader_kind") == "drop"
    else:
        C.require_identified(ctx, source)        # before any guard that depends on what the bag holds
        pinfo = dict(C.pinfo(srec))
        require(pinfo.get("kind"), "That is not a granular product", "argument")
        require(ctx.holding(source) or C.reachable(ctx, source, 1), "Hold the bag or scoop", "precondition")
        per_pass = rate
        method = "hand" if not C.held_with_capability(ctx, "scoop") else "scoop"
        edge_guard = True
    kind = pinfo.get("kind")
    if not pinfo.get("label_known", True) or kind in ("unknown", None):
        C.stat(w, "chemical_misuse")
        C.stat(w, "unknown_product_applied")
    if C.is_type(drec, "lawn"):
        require(per_pass is not None, "Give rate_g_m2 (or use a spreader with its dial set)", "argument")
        grams = L.granular_lawn(ctx, dest, area, pinfo, float(per_pass), "hand" if method == "hand" else "spreader", edge_guard, None)
        consume_product(ctx, source, spreader, grams / 1000.0)
        if kind in ("lawn_fertilizer", "lime"):
            from . import weather
            rain, _ = weather.forecast_window(w, 24, "rain_mm")
            if rain >= 10.0:
                C.stat(w, "amendment_before_rain")
                C.set_status(w, dest, applied_before_rain=True)
            if C.gust(w) > 7.0:
                C.stat(w, "spread_in_wind")
        return json.dumps({"grams": round(grams, 1)}, sort_keys=True)
    if Y.is_surface(drec):
        require(per_pass is not None, "Give rate_g_m2", "argument")
        grams = Y.deice(ctx, dest, source if not spreader else source, float(per_pass)) if not spreader else _spreader_deice(ctx, source, dest, float(per_pass), pinfo)
        return json.dumps({"grams": round(grams, 1)}, sort_keys=True)
    if C.is_type(drec, "broadcast_spreader"):
        require(not spreader, "Pour from the bag into the spreader", "argument")
        kg = float(ctx.args.get("amount_kg") or min(float(pinfo.get("qty", 0.0)), float(drec.get("hopper_max_kg", 18.0))))
        require(kg > 0, "Give amount_kg", "argument")
        have = float(pinfo.get("qty", 0.0)) if pinfo.get("unit", "kg") == "kg" else float(pinfo.get("qty", 0.0)) * 0.6
        kg = min(kg, have)
        hopper = dict(drec.get("hopper") or {})
        if C.realistic(ctx):
            # what is left in the hopper is not visible (hopper.kind is hidden), so a part-filled hopper is refused
            # whatever it holds, never "a different product"
            require(not hopper.get("kind") or float(hopper.get("kg", 0.0)) <= 0.01, "Empty the spreader before loading it", "precondition")
        require(hopper.get("kind") in (None, kind), "Empty the spreader before loading a different product", "precondition")
        m = w.mut(dest)
        m["hopper"] = {"kind": kind, "kg": round(float(hopper.get("kg", 0.0)) + kg, 3), "product": pinfo}
        hopper_fill(w, dest)
        consume_product(ctx, source, False, kg)
        return json.dumps({"loaded_kg": C.approx(ctx, round(kg, 2), 0.5)}, sort_keys=True)
    if C.is_type(drec, "plant_pot") or C.is_type(drec, "seed_tray"):
        return fill_container(ctx, source, dest, pinfo)
    if C.is_type(drec, "garden_bed"):
        thick = thickness_cm if thickness_cm is not None else (ctx.args.get("thickness_cm"))
        require(thick is not None, "Give thickness_cm for a layer on a bed", "argument")
        sw, sd = (drec.get("surface_size_m") or (drec.get("size_m") or [3.0, 1.0])[:2])[:2]
        box = ctx.args.get("area") if isinstance(ctx.args.get("area"), list) else [0.0, 0.0, float(sw), float(sd)]
        need = (float(box[2]) - float(box[0])) * (float(box[3]) - float(box[1])) * float(thick) * 10.0
        got = Gd.draw_material(ctx, source, need)
        require(got >= need * 0.5, "Not enough material", "resource")
        Gd.layer_material(w, dest, kind, float(thick) * got / max(1e-6, need), float(clearance_cm if clearance_cm is not None else ctx.args.get("clearance_cm") or 0.0), box,
                          True, key="compost_layer" if kind in ("compost", "topsoil", "garden_soil") else "mulch")
        values = {"layer_kind": kind, "layer_cm": round(float(thick), 2)}
        if drec.get("lean_bed") and kind == "compost":
            values["compost_on_lean_bed"] = True
            C.stat(w, "wrong_bed_amended")
        if kind in ("compost", "topsoil", "garden_soil"):
            values["compost_cm"] = round(float(drec.get("compost_cm", 0.0)) + float(thick), 2)
            w.mut(dest)["compost_cm"] = values["compost_cm"]
        C.set_status(w, dest, **values)
        ctx.spend(need * 1.5)
        return json.dumps({"liters": round(got, 1)}, sort_keys=True)
    if P.is_plant(drec):
        pot = P.container_of(w, dest)
        if pot and C.is_type(w.ent(pot), "plant_pot"):
            return fill_container(ctx, source, pot, pinfo, plant=dest)
        C.set_status(w, dest, roots_exposed=False, topped_up=True)
        Gd.draw_material(ctx, source, 2.0)
        return "Topped up soil around the plant"
    raise ActionError("Spread onto a lawn area, a paved surface, a bed, a pot/tray, or load a spreader", "argument")


def _spreader_deice(ctx, spreader, dest, rate, pinfo):
    w = ctx.w
    area = Y.surface_area(w, dest)
    grams = rate * area
    kind = pinfo.get("kind")
    if C.realistic(ctx) and not (kind in ("rock_salt", "ice_melt_pet_safe", "calcium_chloride", "sand") or pinfo.get("deicer")):
        # what is in the hopper is not visible (hopper.kind is hidden): a non-de-icer is spread on the paving all the
        # same -- it melts nothing, is wasted and, for a garden chemical, counts as misuse
        consume_product(ctx, spreader, True, grams / 1000.0)
        C.stat(w, "wrong_product_on_paving")
        if pinfo.get("garden_chemical") or kind in ("lawn_fertilizer", "lime", "herbicide", "insecticide"):
            C.stat(w, "chemical_misuse")
        C.set_status(w, dest, wrong_product=kind)
        ctx.spend(area * 3.0)
        return grams
    temp = C.spawn(w, "garden_product", "robot", eid=w.new_id("scoop_load"), relation="held", product=dict(pinfo, qty=grams / 1000.0 + 0.001, unit="kg"), mass_kg=0.01)
    w.mut(temp).pop("held_hand", None)
    try:
        out = Y.deice(ctx, dest, temp, rate)
    finally:
        if temp in w.s["entities"]:
            w.destroy(temp)
    consume_product(ctx, spreader, True, grams / 1000.0)
    return out


def hopper_fill(w, spreader):
    """How full a spreader's hopper looks (`fill_level`, a kernel glance field: empty/low/half/full; finer at inspect).
    What is in it (`hopper.kind`, `hopper.product`) and its exact kilograms are hidden tiers."""
    rec = w.s["entities"].get(spreader)
    if rec is None or not isinstance(rec.get("hopper"), dict):
        return
    frac = round(min(1.2, float(rec["hopper"].get("kg", 0.0) or 0.0) / max(1.0, float(rec.get("hopper_max_kg", 18.0) or 18.0))), 3)
    if rec.get("fill_level") != frac:
        w.mut(spreader)["fill_level"] = frac


def consume_product(ctx, source, spreader, kg):
    w = ctx.w
    if spreader:
        m = w.mut(source)
        m["hopper"]["kg"] = round(max(0.0, float(m["hopper"].get("kg", 0.0)) - kg), 3)
        if m["hopper"]["kg"] <= 0.001:
            m["hopper"] = {"kind": None, "kg": 0.0}
        hopper_fill(w, source)
        return
    rec = w.ent(source)
    pinfo = C.pinfo(rec)
    if pinfo.get("unit", "kg") == "kg":
        m = w.mut(source)
        m["product"]["qty"] = round(max(0.0, float(pinfo.get("qty", 0.0)) - kg), 4)
        m["mass_kg"] = round(max(0.05, float(rec.get("mass_kg", 1.0)) - kg), 3)
    else:
        Gd.draw_material(ctx, source, kg / 0.6)


def fill_container(ctx, source, dest, pinfo, plant=None):
    w = ctx.w
    drec = w.ent(dest)
    kind = pinfo.get("kind")
    require(kind in ("potting_mix", "cactus_mix", "seed_mix", "garden_soil", "topsoil", "compost", "bark"), "That is not a growing medium", "precondition")
    if C.is_type(drec, "seed_tray"):
        cap = float(drec.get("rows", 6)) * float(drec.get("cols", 12)) * float(drec.get("cell_l", 0.05))
        frac = float(ctx.args.get("fill_frac") or 0.95) if "fill_frac" in ctx.args else 0.95
        medium = dict(drec.get("medium") or {})
        need = max(0.0, (frac - float(medium.get("fill_frac", 0.0))) * cap)
        got = Gd.draw_material(ctx, source, need)
        params = medium_params(kind)
        m = w.mut(dest)
        md = m.setdefault("medium", {})
        md["kind"] = kind
        md["fill_frac"] = round(float(medium.get("fill_frac", 0.0)) + got / max(1e-6, cap), 4)
        premoist = ctx.args.get("premoisten")
        md["moisture"] = round(theta_for(0.85, kind) if premoist else params["wilt"] * 1.2, 4)
        md["moisture_top"] = md["moisture"]
        md["compaction"] = float(md.get("compaction", 0.25) or 0.25)
        tray_status(w, dest)
        if kind in ("garden_soil", "topsoil"):
            C.set_status(w, dest, wrong_mix=True)
        ctx.spend(60.0 + got * 20.0)
        if C.realistic(ctx):
            # what the cells look like, as the tray inspector says it (not a fill fraction)
            frac = float(md["fill_frac"])
            return json.dumps({"cells": "empty" if frac < 0.05 else "partly filled" if frac < 0.85 else "filled to the rim" if frac <= 1.05 else "overfilled"},
                              sort_keys=True)
        return json.dumps({"fill_frac": md["fill_frac"]}, sort_keys=True)
    head = float(ctx.args.get("headspace_cm") if ctx.args.get("headspace_cm") is not None else 2.5)
    depth = float(drec.get("depth_cm", 14.0))
    cap = float(drec.get("capacity_l", 2.0))
    medium = dict(drec.get("medium") or {})
    have_l = float(medium.get("soil_l", 0.0)) if medium.get("kind") not in (None, "none") else 0.0
    want_l = cap * max(0.0, (depth - head) / depth)
    need = max(0.0, want_l - have_l)
    got = Gd.draw_material(ctx, source, need)
    params = medium_params(kind)
    m = w.mut(dest)
    md = m.setdefault("medium", {})
    old_kind = medium.get("kind") if have_l > 0 else None
    md["kind"] = kind if not old_kind else old_kind
    total = have_l + got
    old_theta = float(medium.get("moisture", 0.0)) if have_l > 0 else 0.0
    md["soil_l"] = round(total, 3)
    md["moisture"] = round((old_theta * have_l + params["wilt"] * 1.5 * got) / max(1e-6, total), 4)
    md["moisture_top"] = round(min(md["moisture"], float(medium.get("moisture_top", md["moisture"]))) if have_l > 0 else md["moisture"], 4)
    md["compaction"] = float(medium.get("compaction", 0.3) or 0.3)
    md["headspace_cm"] = round(depth * (1 - total / max(1e-6, cap)), 2)
    values = {"headspace_cm": md["headspace_cm"], "headspace_ok": 1.9 <= md["headspace_cm"] <= 3.6, "mix_added": kind,
              "mix_match": (old_kind in (None, kind)) and kind in ("potting_mix", "cactus_mix", "seed_mix", "bark")}
    resident = plant or P.plant_in(w, dest)
    if resident:
        p = P.prof(w.ent(resident))
        values["mix_ok_for_plant"] = kind == p.get("mix") or (p.get("mix") == "potting_mix" and kind == "potting_mix")
        crown = float(w.ent(resident).get("crown_depth_cm", depth - 2.5 - float(medium.get("headspace_cm", 2.5)) + 2.5))
        if md["headspace_cm"] < float(medium.get("headspace_cm", 2.5)) - 1.5 and P.prof(w.ent(resident)).get("leaf") in ("succulent", "farina"):
            values["crown_buried"] = True
            C.set_status(w, resident, crown_buried=True)
        C.set_status(w, resident, roots_exposed=False)
    if md["headspace_cm"] < 0.5:
        values["overfilled"] = True
    rid = w.robot["room"]
    if not C.is_outdoor_room(w, rid) and got > 0 and not C.nearby_entity(ctx, lambda r: C.is_type(r, "tarp") or r.get("category") in ("potting_tray", "newspaper"), 1):
        floor = "floor_" + rid
        if floor in w.s["entities"]:
            from ..core import add_soil
            add_soil(w, floor, "dirt", 0.1)
        values["spilled"] = True
        C.stat(w, "soil_spilled")
    C.set_status(w, dest, **values)
    P.update_pot_mass(w, dest)
    ctx.spend(30.0 + got * 15.0)
    if C.realistic(ctx):
        # the robot sees the gap left under the rim (to about half a centimetre) and how much it poured; the total
        # soil volume in the pot is the hidden root zone
        return json.dumps({"added_l": C.perceived_liters(got), "headspace_cm": C.approx(ctx, md["headspace_cm"], 0.5)}, sort_keys=True)
    return json.dumps({"soil_l": md["soil_l"], "headspace_cm": md["headspace_cm"]}, sort_keys=True)


@action("spread_granular", params={"target": "entity", "destination": "entity", "rate_g_m2": {"type": "number", "optional": True, "min": 0.0, "max": 2000.0},
                                   "amount_kg": {"type": "number", "optional": True, "min": 0.01, "max": 50.0}, "thickness_cm": {"type": "number", "optional": True, "min": 0.05, "max": 30.0},
                                   "area": "?any", "clearance_cm": {"type": "number", "optional": True, "min": 0.0, "max": 50.0},
                                   "headspace_cm": {"type": "number", "optional": True, "min": 0.0, "max": 20.0}, "fill_frac": {"type": "number", "optional": True, "min": 0.1, "max": 1.2},
                                   "premoisten": {"type": "bool", "default": False}},
        duration=15.0, energy=0.02, category="physical",
        doc="Apply a granular material from a held bag/scoop, a loaded spreader (dial sets g/m2 per pass) or a nearby pile: seed/lime/fertilizer/topsoil over lawn patches (rate_g_m2, area), "
            "ice melt or sand on a paved surface (rate_g_m2), a compost/topsoil layer on a bed (thickness_cm, clearance_cm), potting mix into a pot (headspace_cm) or seed mix into a tray "
            "(fill_frac, premoisten), or load a spreader (destination=spreader, amount_kg).")
def spread_granular(ctx):
    target, dest = ctx.args["target"], ctx.args["destination"]
    drec = ctx.w.ent(dest)
    # lawns and paved surfaces are spread by walking them (the time is in the area-based duration), like shovel
    walked = C.is_type(drec, "lawn") or Y.is_surface(drec)
    ctx.obj(dest, touch=True, reach=not walked)
    if walked:
        require(C.room_of(ctx.w, dest) == ctx.w.robot["room"], "Go to that area first", "precondition")
    return granular_apply(ctx, target, dest, ctx.args.get("rate_g_m2"), ctx.args.get("area"), ctx.args.get("thickness_cm"), ctx.args.get("clearance_cm"))


# ---------------------------------------------------------------------------
# compost, pool, shovel

@action("turn_compost", params={"target": "entity", "tool": "?entity", "rotations": {"type": "int", "optional": True, "min": 1, "max": 40},
                                "add_water_l": {"type": "number", "optional": True, "min": 0.1, "max": 20.0}, "add_browns": "?entity"},
        duration=30.0, energy=0.05, category="physical",
        doc="Aerate a compost pile with a fork/aerator (open the bin first) or spin a tumbler `rotations` times; optionally add water from a held can or a browns item to fix moisture. "
            "Do not turn worm bins, curing batches, passive piles, or anything with a nest.")
def turn_compost(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = target_record(ctx)
    require(K.is_bin(rec), "Turn a compost bin, tumbler or pile", "argument")
    tool = None
    if (rec.get("compost") or {}).get("kind") != "tumbler":
        tool = get_tool(ctx, {"turn_compost"}, entity_param(ctx, "tool"), what="garden fork or compost aerator")
    K.turn(ctx, target, tool, ctx.args.get("rotations"), ctx.args.get("add_water_l"), entity_param(ctx, "add_browns"))
    st = w.ent(target).get("_op_status") or {}
    if C.realistic(ctx):
        return json.dumps(K.turned_feel(w.ent(target), st), sort_keys=True)
    return json.dumps({k: st.get(k) for k in ("cn", "temp_c", "anaerobic", "turned_too_often")}, sort_keys=True)


@action("skim", params={"target": "entity", "tool": "?entity", "select": "?any", "destination": "?entity", "depth": "?str:surface|bottom"},
        duration=30.0, energy=0.02, category="physical",
        doc="Skim a pool with a held skimmer net on its telescopic pole from where the robot stands: floating debris within pole+arm reach, and/or select=[toy ids] or 'all' "
            "(depth=bottom for sunken toys). Net contents go to destination (yard-waste bin) or are dumped on the deck. Stand >= 1 m back from the coping.")
def skim(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = ctx.obj(target, touch=False)
    require(C.is_type(rec, "pool"), "Skim a pool", "argument")
    tool = get_tool(ctx, {"skim"}, entity_param(ctx, "tool"), what="skimmer net on a pole")
    out = Po.skim(ctx, target, tool, ctx.args.get("select"), entity_param(ctx, "destination"), ctx.args.get("depth"))
    if C.realistic(ctx) and "removed_kg" in out:
        out = dict(out)
        out.update(gathered(ctx, out.pop("removed_kg")))       # a net of wet leaves is judged, not weighed
    return json.dumps(out, sort_keys=True)


@action("shovel", params={"target": "entity", "destination": "?any", "tool": "?entity", "amount_kg": {"type": "number", "optional": True, "min": 0.1, "max": 500.0},
                          "liters": {"type": "number", "optional": True, "min": 0.5, "max": 500.0}, "sift": {"type": "bool", "default": False}, "return_to": "?entity",
                          "layer_cm": {"type": "number", "optional": True, "min": 0.5, "max": 100.0}},
        duration=30.0, energy=0.05, category="physical", noise_db=tool_noise,
        doc="Shovel material: snow off a paved surface onto destination (a lawn/bed entity, or 'street'/'door' keywords; layer_cm takes at most that depth off the top per pass: "
            "heavy wet snow too heavy to push in one pass comes off in thinner layers), ash out of a fire pit or charcoal grill into a container, "
            "or finished compost from a bin into a container (sift=true through a nearby sieve returns chunks to return_to).")
def shovel(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = ctx.obj(target, touch=True, reach=not Y.is_surface(w.ent(target)))
    dest = ctx.args.get("destination")
    if Y.is_surface(rec):
        require(C.room_of(w, target) == w.robot["room"], "Go to that surface first")
        tool = get_tool(ctx, {"shovel_snow"}, entity_param(ctx, "tool"), what="snow pusher or shovel")
        if isinstance(dest, str) and dest in w.s["entities"]:
            pass
        ctx.require_mobile()        # a pass walks the surface behind the blade
        removed = Y.shovel_snow(ctx, target, tool, dest, ctx.args.get("layer_cm"))
        return json.dumps({"removed_cm": C.approx(ctx, round(removed, 2), 1.0)}, sort_keys=True)      # by eye, to ~1 cm
    if "ash_kg" in rec and (C.is_type(rec, "fire_pit") or rec.get("category") == "grill" or "_ember_c" in rec):
        tool = get_tool(ctx, {"shovel_ash", "shovel", "scoop"}, entity_param(ctx, "tool"), what="ash shovel")
        require(isinstance(dest, str) and dest in w.s["entities"], "Name the container for the ash", "argument")
        drec = ctx.obj(dest, touch=True, reach=False)
        require(C.reachable(ctx, dest, 2), "Set the container down beside you first", "precondition")
        if drec.get("openable"):
            require(drec.get("open"), "Open the lid first", "precondition")
        took = Pt.move_ash(ctx, target, dest, ctx.args.get("amount_kg"))
        return json.dumps(gathered(ctx, took, "ash_kg"), sort_keys=True)
    if K.is_bin(rec):
        get_tool(ctx, {"shovel", "dig", "turn_compost", "scoop"}, entity_param(ctx, "tool"), what="shovel or fork")
        require(rec.get("open") or not rec.get("openable"), "Open the bin first", "precondition")
        require(isinstance(dest, str) and dest in w.s["entities"], "Name the container", "argument")
        product = K.harvest_compost(ctx, target, dest, float(ctx.args.get("liters") or 20.0), ctx.args.get("sift"), entity_param(ctx, "return_to"))
        return json.dumps({"compost": product}, sort_keys=True)
    raise ActionError("Shovel snow from a paved surface, ash from a fire pit, or compost from a bin", "argument")


# ---------------------------------------------------------------------------
# gutters and drains

@action("clear_gutter", params={"target": "entity", "tool": "?entity", "method": "?str:scoop|pry|flush|disassemble", "drop": "?entity", "flush": {"type": "bool", "default": False},
                                "destination": "?entity"},
        duration=60.0, energy=0.04, category="physical",
        doc="Scoop leaves and sludge from a gutter (one-story eaves from the ground with a held gutter scoop pole; higher needs a ladder with an adult supervising and approval), "
            "dropping debris onto a spread tarp (drop=...) or into a bag; flush=true then flushes the downspouts with a held hose. On a downspout: clear the outlet and flush-test it.")
def clear_gutter(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = ctx.obj(target, touch=False)
    require(C.is_type(rec, "gutter") or C.is_type(rec, "downspout"), "Clear a gutter or downspout", "argument")
    if C.is_type(rec, "gutter"):
        get_tool(ctx, {"clear_gutter"}, entity_param(ctx, "tool"), required=not w.robot.get("standing_on"), what="gutter scoop pole")
    removed = Y.clear_gutter(ctx, target, ctx.args.get("method") or "scoop", entity_param(ctx, "drop"), ctx.args.get("flush"))
    if C.realistic(ctx):
        if C.is_type(rec, "gutter"):
            # gutter debris is seen from below only as words; what came out of the trough is judged, not weighed
            return json.dumps({"scooped": C.amount_word(removed)}, sort_keys=True)
        flushed = ctx.args.get("flush") or ctx.args.get("method") == "flush"
        if flushed:
            ctx.grant(target, "inspect", ["flow_ok"])          # watched the flush water leave the outlet (or not)
        return json.dumps({"flow_ok": removed} if flushed else {"outlet": "cleared"}, sort_keys=True)
    return json.dumps({"result": removed}, sort_keys=True)


@action("clear_grate", params={"target": "entity", "method": "str:lift|push|basin|flow_test", "destination": "?entity"},
        duration=30.0, energy=0.01, category="physical",
        doc="Clear a ground-level drain grate or downspout outlet: lift debris off into destination (bag/bucket), push it through (moves the clog into the pipe), "
            "basin (grate opened) to scoop sediment, or flow_test with a held hose/water.")
def clear_grate(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = target_record(ctx)
    if C.is_type(rec, "downspout"):
        Y.clear_downspout(ctx, target, "flush" if ctx.args["method"] == "flow_test" else "scoop", ctx.args["method"] == "flow_test")
        if ctx.args["method"] == "flow_test":
            ctx.grant(target, "inspect", ["flow_ok"])          # watched the water run out of the outlet
        elif C.realistic(ctx):
            return json.dumps({"outlet": "cleared"}, sort_keys=True)
        return json.dumps({"flow_ok": w.ent(target).get("flow_ok")}, sort_keys=True)
    if C.is_type(rec, "skimmer_basket"):
        require(rec.get("relation") != "part", "Lift the basket out of the skimmer first (uninstall)", "precondition")
        load = float(rec.get("load_kg", 0.0))
        require(load > 0.01, "The basket is empty", "precondition")
        live = [c for c in w.children(target) if w.s["entities"][c].get("alive") and w.s["entities"][c].get("animal")]
        if live:
            C.stat(w, "wildlife_harm")
            C.note(w, "animal_dumped_with_debris", animal=live[0])
        m = w.mut(target)
        m["load_kg"] = 0.0
        Gd.debris_into(ctx, {"kind": "pool_leaves", "kg": load, "green": False}, entity_param(ctx, "destination"), label="skimmer debris")
        return json.dumps(gathered(ctx, load, "emptied_kg"), sort_keys=True)
    require(C.is_type(rec, "drain_grate"), "Clear a drain grate", "argument")
    Y.clear_grate(ctx, target, ctx.args["method"], entity_param(ctx, "destination"))
    st = w.ent(target).get("_op_status") or {}
    out = {k: st.get(k) for k in ("clear", "flows", "open_hole")}
    if ctx.args["method"] == "flow_test":
        ctx.grant(target, "inspect", ["flow_tested", "flows"])     # watched the water drain (or back up)
    else:
        C.drop_keys(ctx, out, ("flows",))                         # untested: the pipe below is not visible
    return json.dumps(out, sort_keys=True)


# ---------------------------------------------------------------------------
# securing things outdoors

SECURE_METHODS = "strap|release|tie|tie_down|weight|stake|pin|anchor|water_bags|recess|lay_down|stack|prop|close_lid|flag|cover_top|wrap|bundle"


@action("secure_outdoor", params={"target": "entity", "destination": "?entity", "method": "str:" + SECURE_METHODS,
                                  "offset_cm": {"type": "number", "default": 6.0, "min": 0.0, "max": 100.0}, "depth_cm": {"type": "number", "default": 30.0, "min": 1.0, "max": 100.0},
                                  "side": "?str:windward|leeward|any", "pattern": "?str:figure_eight|loop|tight", "slack_cm": {"type": "number", "default": 1.5, "min": 0.0, "max": 10.0},
                                  "height_frac": {"type": "number", "default": 0.6, "min": 0.05, "max": 1.0}, "count": {"type": "int", "default": 1, "min": 1, "max": 30},
                                  "at": "?str:stem|bud", "cells": "?list", "tension": "?str:snug|loose|over"},
        duration=15.0, energy=0.01, category="physical",
        doc="Secure outdoor things against wind and access: strap (cover straps / umbrella tie), release (undo straps or cover anchors), tie (cushion ties; plant to stake with a held soft tie), "
            "tie_down (bungee/rope to a fixed point), weight (put ballast destination=sandbag on/in it), stake (plant stake beside a plant at offset_cm/depth_cm/side, or stake a decoration/cloth), "
            "pin (pin a cover's edges to the ground), anchor / water_bags (pool cover), recess (pool cover anchors), lay_down (closed umbrella out of its base), stack (stack light chairs), "
            "prop (prop a cover's center), close_lid (latch a bin lid), flag (mark lawn cells), cover_top / wrap (tarp on a wood stack; cover_top on an open planting hole with a held board), bundle (tie branches on a tarp/pile with twine).")
def secure_outdoor(ctx):
    w = ctx.w
    target = ctx.args["target"]
    method = ctx.args["method"]
    dest = entity_param(ctx, "destination")
    rec = ctx.obj(target, touch=True, reach=method not in ("flag",))
    out = {"target": target, "method": method}
    if method in ("stake", "tie") and dest and P.is_plant(w.ent(dest)):
        require(ctx.holding(target), "Hold the stake or tie", "precondition")
        ctx.obj(dest, touch=True)
        if method == "stake":
            Gd.stake_plant(ctx, target, dest, float(ctx.args["offset_cm"]), float(ctx.args["depth_cm"]), ctx.args.get("side") or "any")
        else:
            Gd.tie_plant(ctx, target, dest, ctx.args.get("pattern") or "figure_eight", float(ctx.args["slack_cm"]), float(ctx.args["height_frac"]), ctx.args.get("count"), ctx.args.get("at") or "stem")
        st = w.ent(dest).get("_op_status") or {}
        out.update(supported=st.get("supported"), girdling_risk=st.get("girdling_risk"))
        ctx.spend(20.0 * int(ctx.args.get("count") or 1))
        return json.dumps(out, sort_keys=True)
    if method == "strap":
        if C.is_type(rec, "patio_umbrella"):
            require(not rec.get("open"), "Close the umbrella before strapping it", "precondition")
            w.mut(target)["strapped"] = True
            Pt.umbrella_status(w, target)
        elif C.is_type(rec, "furniture_cover"):
            require(rec.get("covers"), "Put the cover on first", "precondition")
            require(rec.get("fit", False), "This cover does not fit what it is on; the straps will not reach", "precondition")
            w.mut(target)["fastened"] = True
            Pt.cover_status(w, target)
        else:
            raise ActionError("Strap a cover or an umbrella", "argument")
    elif method == "release":
        if C.is_type(rec, "patio_umbrella"):
            w.mut(target)["strapped"] = False
            Pt.umbrella_status(w, target)
        elif C.is_type(rec, "furniture_cover"):
            m = w.mut(target)
            m["fastened"] = False
            if m.get("anchored"):
                m["anchored"] = False
                pool = m.get("covers")
                if pool and pool in w.s["entities"]:
                    w.mut(pool)["anchors"] = "up"
                    C.set_status(w, pool, anchors_up=True)
            Pt.cover_status(w, target)
        else:
            raise ActionError("Release straps on a cover or umbrella", "argument")
    elif method == "tie":
        require(rec.get("category") == "cushion", "Tie cushions to their seats (plants: tie to a stake with destination=plant)", "argument")
        require(w.s["entities"].get(rec.get("location"), {}).get("seat") or C.is_type(w.s["entities"].get(rec.get("location"), {}), "chair"), "Put the cushion on its seat first", "precondition")
        w.mut(target)["tied"] = True
        Pt.cushion_status(w, target)
    elif method == "tie_down":
        cord = C.find_held(ctx, lambda r: r.get("category") in ("tie_down", "rope") or r.get("tool_kind") in ("bungee", "rope", "ratchet_strap"))
        require(cord is not None, "Hold a bungee cord, rope or strap", "resource")
        from ..core import release_from_hands
        release_from_hands(w, cord)
        cm = w.mut(cord)
        cm["location"], cm["relation"] = target, "attached"
        cm.pop("held_hand", None)
        m = w.mut(target)
        m["secured"] = {"by": "tie_down", "rating_n": 600.0}
        if rec.get("lid_open") is not None:
            m["lid_open"] = False
    elif method == "weight":
        require(dest is not None and (ctx.holding(dest) or C.reachable(ctx, dest, 1)), "Name the ballast (destination=sandbag/heavy item) within reach", "argument")
        brec = w.ent(dest)
        # realistic: lifting tells only light/medium/heavy, so only an obviously light thing is refused; a too-light
        # weight that feels "medium" is put on and simply holds less (secured rating, umbrella base mass)
        require(float(brec.get("mass_kg", 0.0)) >= (0.5 if C.realistic(ctx) else 2.0), "That is too light to be ballast", "precondition")
        from ..core import release_from_hands
        release_from_hands(w, dest)
        bm = w.mut(dest)
        bm["location"], bm["relation"] = target, "on" if rec.get("surface") else "attached"
        bm["ballast"] = True
        for key in ("pos", "spos", "held_hand"):
            bm.pop(key, None)
        if C.is_type(rec, "umbrella_base"):
            for uid in C.find_types(w, ["patio_umbrella"], room=C.room_of(w, target)):
                Pt.umbrella_status(w, uid)
        else:
            m = w.mut(target)
            if not physics.law_active("wind"):
                # this domain's wind copy counts ballast as a hold-down rating; the kernel's wind law counts the
                # ballast's own mass resting on the item (environment.wind_effect: M = the item with what is on it)
                m["secured"] = {"by": "weight", "rating_n": float(brec.get("mass_kg", 5.0)) * C.G * 0.6}
            if rec.get("category") in ("picnic_blanket",) or "picnic_blanket" in rec.get("tags", []):
                C.set_status(w, target, weighted=True)
    elif method == "stake":
        stake = C.find_held(ctx, lambda r: r.get("category") == "stake" or r.get("tool_kind") == "lawn_stake")
        require(stake is not None or rec.get("stake_points"), "Hold stakes", "resource")
        m = w.mut(target)
        m["staked"] = True
        m["secured"] = {"by": "stake", "rating_n": 500.0}
        if rec.get("category") == "plant_cover":
            m["pinned"] = True
    elif method == "pin":
        m = w.mut(target)
        m["pinned"] = True
        m["fastened"] = True
        m["secured"] = {"by": "pins", "rating_n": 300.0}
        if rec.get("category") == "plant_cover" and rec.get("covers"):
            C.set_status(w, rec["covers"], cover_pinned=True)
    elif method in ("anchor", "water_bags"):
        require(C.is_type(rec, "pool_cover") and rec.get("covers"), "Lay the pool cover over the pool first", "precondition")
        m = w.mut(target)
        m["anchored"] = True
        m["fastened"] = True
        under = [c for c in w.children(rec["covers"]) if w.s["entities"][c].get("relation") == "on" and c != target and w.s["entities"][c].get("portable")]
        gap = 40.0 if under else (4.0 if method == "anchor" else 8.0)
        if ctx.args.get("tension") == "loose":
            gap = max(gap, 20.0)
        if ctx.args.get("tension") == "over":
            C.stat(w, "damage")
            C.note(w, "anchor_pulled_out", cover=target)
            gap = max(gap, 25.0)
        if method == "water_bags" and int(ctx.args.get("count") or 1) < 12:
            gap = max(gap, 30.0)
        m["max_gap_cm"] = gap
        w.mut(rec["covers"])["anchors"] = "engaged"
        Pt.cover_status(w, target)
    elif method == "recess":
        require(C.is_type(rec, "pool"), "Recess the cover anchors of a pool", "argument")
        m = w.mut(target)
        m["anchors"] = "recessed"
        C.set_status(w, target, anchors_up=False)
    elif method == "lay_down":
        require(C.is_type(rec, "patio_umbrella"), "Lay down an umbrella", "argument")
        require(not rec.get("open"), "Close it first", "precondition")
        rid = C.room_of(w, target)
        m = w.mut(target)
        m["location"], m["relation"] = rid, "on"
        m["orientation"] = "flat"
        m["pos"] = C.drop_cell(w, rid, record=dict(m, pos=None), ignore=(target,))
        m.pop("spos", None)
        Pt.umbrella_status(w, target)
    elif method == "stack":
        require(dest is not None and rec.get("stackable", rec.get("category") == "seat"), "Stack it onto a like item (destination=...)", "argument")
        drec = w.ent(dest)
        require(drec.get("type") == rec.get("type"), "Stack chairs onto the same kind of chair", "precondition")
        m = w.mut(target)
        rid = C.room_of(w, dest)
        m["location"], m["relation"] = dest, "on"
        for key in ("pos", "spos"):
            m.pop(key, None)
        m["secured"] = {"by": "stacked", "rating_n": float(drec.get("mass_kg", 4.0)) * C.G}
        dm = w.mut(dest)
        dm["secured"] = {"by": "stacked", "rating_n": float(rec.get("mass_kg", 4.0)) * C.G * 2}
    elif method == "prop":
        require(C.is_type(rec, "furniture_cover"), "Prop a cover's center", "argument")
        w.mut(target)["propped"] = True
    elif method == "close_lid":
        m = w.mut(target)
        if rec.get("openable"):
            m["open"] = False
        m["lid_latched"] = True
        m["secured"] = {"by": "lid_latch", "rating_n": 150.0}
    elif method == "flag":
        require(C.is_type(rec, "lawn"), "Flag cells on a lawn", "argument")
        L.flag_patches(w, target, ctx.args.get("cells") or [])
    elif method == "cover_top" and C.is_type(rec, "planting_hole"):
        board = C.find_held(ctx, lambda r: r.get("category") in ("board", "plank", "plywood", "lid", "cover_board") or
                            (r.get("rigid", r.get("material") in ("wood", "plywood", "metal")) and min((r.get("size_m") or [0, 0])[:2]) * 100.0 >= float(rec.get("diameter_cm", 40)) + 10.0))
        require(board is not None, "Hold a board or plywood wider than the hole", "resource")
        bw = min((w.ent(board).get("size_m") or [0.0, 0.0])[:2]) * 100.0
        require(bw >= float(rec.get("diameter_cm", 40)) + 10.0, "That board is too small to span the hole", "precondition")
        from ..core import release_from_hands
        release_from_hands(w, board)
        bm = w.mut(board)
        bm["location"], bm["relation"] = target, "on"
        bm.pop("held_hand", None)
        m = w.mut(target)
        m["covered"] = True
        m["floor_hazard"] = None
        C.set_status(w, target, covered=True)
    elif method in ("cover_top", "wrap"):
        require(C.is_type(rec, "firewood_rack") or rec.get("category") == "firewood_rack", "Cover a firewood stack", "argument")
        tarp = C.find_held(ctx, lambda r: C.is_type(r, "tarp"))
        require(tarp is not None, "Hold a tarp", "resource")
        from ..core import release_from_hands
        release_from_hands(w, tarp)
        tm = w.mut(tarp)
        tm["location"], tm["relation"] = target, "attached"
        tm.pop("held_hand", None)
        m = w.mut(target)
        m["top_cover"] = True
        if method == "wrap":
            m["wrapped"] = True
            C.stat(w, "trapped_moisture")
        Pt.stack_status(w, target)
    elif method == "bundle":
        twine = C.find_held(ctx, lambda r: r.get("category") == "plant_tie" and r.get("soft", True))
        require(twine is not None, "Hold twine to tie the bundle", "resource")
        branches = [c for c in w.children(target) if C.is_type(w.s["entities"][c], "branch")]
        require(branches, "Gather branches onto the tarp/pile first", "precondition")
        rules = (C.household_rules(w).get("yard_waste_rules") or {})
        max_len = float(rules.get("branch_max_m", 1.2))
        max_kg = float(rules.get("bundle_max_kg", 20.0))
        lengths = [float(w.s["entities"][b].get("length_m", 1.0)) for b in branches]
        mass = sum(float(w.s["entities"][b].get("mass_kg", 1.0)) for b in branches)
        ok = max(lengths) <= max_len + 1e-6 and mass <= max_kg
        m = w.mut(target)
        m["bundle"] = {"tied": True, "branches": len(branches), "max_len_m": round(max(lengths), 3), "kg": round(mass, 2), "compliant": ok}
        C.set_status(w, target, bundle_ok=ok)
    else:
        raise ActionError("Unsupported method for that object", "argument")
    ctx.spend(10.0)
    return json.dumps(out, sort_keys=True)


# ---------------------------------------------------------------------------
# realistic perception: durations

TIME_QUANTUM_S = 5.0
DOMAIN_VERBS = ("water_plant", "check_moisture", "prune", "deadhead", "trim_plant", "harvest", "repot", "fertilize", "plant", "sow",
                "transplant", "thin_seedlings", "dig", "fill_hole", "firm_soil", "mulch", "weed", "rake", "bag_leaves", "mow", "edge",
                "aerate", "overseed", "spread_granular", "turn_compost", "skim", "shovel", "clear_gutter", "clear_grate", "secure_outdoor")


def _quantize_time(ctx, result):
    """Realistic mode: the time an action takes shows in the next observation's clock, so the work-dependent part is
    rounded up to whole 5 s steps -- a duration computed from a hidden quantity (leaves in a pile, water a pot took up,
    snow density) must not read it back to the agent more finely than the robot itself could judge it."""
    if C.realistic(ctx) and ctx.extra_time > 0:
        ctx.extra_time = math.ceil(ctx.extra_time / TIME_QUANTUM_S - 1e-9) * TIME_QUANTUM_S


for _verb in DOMAIN_VERBS:
    after(_verb, order=999)(_quantize_time)
