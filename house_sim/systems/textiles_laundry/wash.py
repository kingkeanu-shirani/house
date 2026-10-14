"""Washing machines: programs, dispenser dosing, door lock, wash outcomes (fidelity P-approx).

Washer record (see data/types/textiles_laundry.json, type washing_machine):
    settings.temp (C list, or US words tap_cold/cold/warm/hot), settings.spin, settings.soil,
    settings.extra_rinse, settings.delay_h; appliance.programs {name: {kind, duration_s, water_l}};
    dispenser -> id of a washer_dispenser drawer holding dispenser_compartment children
    (prewash/main/softener/bleach, liquid containers filled by pouring); the drum itself is a
    liquid container for products added directly; detergent pods are entities placed in the drum.

Outcomes are applied when the cycle completes (or partially when it is cancelled/powered off):
soil removal per stain class, heat-set protein stains, wool felting, cotton/rayon shrink,
agitation/snag damage to delicates, dye bleed onto whites/lights, lint and pet-hair transfer,
chlorine bleach damage, softener residue, detergent residue/oversudsing, pocket contaminants,
spin wetness, imbalance (UE), door-seal leaks, living beings in the drum (injury).
"""
import copy
import math

from ...kernel.plugins import after, before, hook
from ...kernel.errors import ActionError, require
from ..core import clear_contents_from
from . import model as M

PROGRAM_KINDS = {
    "normal": dict(agitation=0.7, removal=0.95, temp_default=40, temp_max=90, temp_min=0, spin="high"),
    "heavy": dict(agitation=1.0, removal=0.98, temp_default=60, temp_max=90, temp_min=0, spin="high"),
    "perm_press": dict(agitation=0.5, removal=0.9, temp_default=40, temp_max=60, temp_min=0, spin="medium"),
    "delicate": dict(agitation=0.25, removal=0.8, temp_default=30, temp_max=40, temp_min=0, spin="low", high_water=True),
    "hand_wash": dict(agitation=0.14, removal=0.7, temp_default=30, temp_max=40, temp_min=0, spin="low", high_water=True),
    "wool": dict(agitation=0.1, removal=0.68, temp_default=20, temp_max=40, temp_min=0, spin="low", high_water=True),
    "bulky": dict(agitation=0.55, removal=0.9, temp_default=40, temp_max=60, temp_min=0, spin="medium", high_water=True),
    "quick": dict(agitation=0.5, removal=0.7, temp_default=20, temp_max=40, temp_min=0, spin="medium"),
    "sanitize": dict(agitation=0.8, removal=0.98, temp_default=70, temp_max=90, temp_min=60, spin="high", sanitize=True),
    "rinse_spin": dict(agitation=0.3, removal=0.2, temp_default=20, temp_max=40, temp_min=0, spin="high", rinse_only=True),
    "drain_spin": dict(agitation=0.0, removal=0.0, temp_default=20, temp_max=20, temp_min=0, spin="high", drain_only=True),
    "tub_clean": dict(agitation=0.6, removal=0.9, temp_default=90, temp_max=90, temp_min=60, spin="medium", tub_clean=True),
}
US_TEMPS = {"tap_cold": 15, "cold": 25, "warm": 35, "hot": 52, "extra_hot": 65}
SPIN_WETNESS = {"none": 0.95, "low": 0.62, "medium": 0.5, "high": 0.38}
SOIL_AGITATION = {"light": 0.85, "normal": 1.0, "heavy": 1.15}
SOIL_DURATION = {"light": 0.9, "normal": 1.0, "heavy": 1.2}
HARDNESS = {"soft": 0.8, "medium": 1.0, "hard": 1.3}

# liquid name -> product role (pouring transfers only the liquid name)
LIQUIDS = {
    "laundry_detergent_he": {"role": "detergent", "he": True, "scented": True},
    "laundry_detergent_he_free": {"role": "detergent", "he": True, "scented": False},
    "laundry_detergent": {"role": "detergent", "he": False, "scented": True},
    "fabric_softener": {"role": "softener", "scented": True},
    "fabric_softener_free": {"role": "softener", "scented": False},
    "chlorine_bleach": {"role": "chlorine"},
    "oxygen_bleach_solution": {"role": "oxygen"},
    "vinegar": {"role": "acid"},
    "washer_cleaner": {"role": "cleaner"},
    "enzyme_presoak": {"role": "enzyme"},
    "ammonia": {"role": "ammonia"},
}


def liquid_info(name):
    if not name:
        return {"role": None}
    if isinstance(name, (list, tuple)):
        return {"role": "mixture", "parts": [liquid_info(n) for n in name]}
    if name in LIQUIDS:
        return LIQUIDS[name]
    text = str(name)
    for token, role in (("bleach", "chlorine"), ("detergent", "detergent"), ("soften", "softener"), ("vinegar", "acid"),
                        ("ammonia", "ammonia"), ("oxygen", "oxygen"), ("cleaner", "cleaner")):
        if token in text:
            return {"role": role, "he": "he" in text.split("_"), "scented": "free" not in text}
    return {"role": "other"}


def is_washer(rec):
    return M.is_kind(rec, "washing_machine")


def program_kind(rec, program=None):
    machine = rec.get("appliance") or {}
    name = program or machine.get("program")
    spec = (machine.get("programs") or {}).get(name) or {}
    kind = spec.get("kind") or (rec.get("_program_kinds") or {}).get(name) or name
    return kind if kind in PROGRAM_KINDS else "normal"


def temp_c(rec, kind=None):
    value = (rec.get("settings") or {}).get("temp", 40)
    t = US_TEMPS.get(value, value) if isinstance(value, str) else value
    try:
        t = float(t)
    except (TypeError, ValueError):
        t = 40.0
    spec = PROGRAM_KINDS.get(kind or program_kind(rec), PROGRAM_KINDS["normal"])
    t = min(t, float(spec["temp_max"]))
    if spec.get("temp_min"):
        t = max(t, float(spec["temp_min"]))
    return t


def dispenser_parts(w, washer_id):
    rec = w.ent(washer_id)
    drawer = rec.get("dispenser")
    parts = {}
    if drawer and w.has(drawer):
        for child in w.children(drawer):
            role = w.ent(child).get("compartment")
            if role:
                parts[role] = child
    return drawer, parts


def target_dose_ml(w, load_kg, soil, he_detergent=True, he_machine=True):
    hardness = HARDNESS.get((w.s.get("utilities") or {}).get("water_hardness", "medium"), 1.0)
    # manufacturer dosing: ~30 mL HE liquid for a normal 4 kg load, about two thirds of that for a small load
    base = 30.0 * (0.6 + 0.1 * min(10.0, max(0.0, load_kg))) * {"light": 0.8, "normal": 1.0, "heavy": 1.4}.get(soil, 1.0) * hardness
    if not he_machine and not he_detergent:
        base *= 2.0
    return round(base, 1)


def _load_textiles(w, eid):
    return [i for i in M.load_of(w, eid) if M.is_textile(w.ent(i))]


def _in_mesh_bag(w, item, washer):
    for anc in M.chain(w, item):
        if anc == washer:
            return False
        if w.ent(anc).get("mesh_bag"):
            return True
    return False


def _laundry_hazard(rec):
    hazard = rec.get("laundry_hazard")
    if hazard:
        return hazard
    label = (str(rec.get("label") or "") + " " + str(rec.get("type") or "")).lower()
    for words, kind in ((("pen", "marker"), "ink"), (("crayon",), "wax_dye"), (("tissue",), "lint"),
                        (("lip balm", "chapstick", "lipstick"), "grease"), (("cash", "bill", "receipt", "note", "paper", "ticket"), "paper"),
                        (("earbud", "phone", "airpod", "headphone"), "electronic")):
        if any(word in label for word in words):
            return kind
    if rec.get("category") in ("electronics", "phone"):
        return "electronic"
    return None


# ---------------------------------------------------------------------------
# cycle hooks (appliance framework)

@hook("cycle_check:washing_machine")
def washer_check(w, eid):
    rec = w.ent(eid)
    if rec.get("switchable") and not rec.get("on"):
        return "The washer is off; press the power button first"
    if rec.get("door_locked") and (rec.get("appliance") or {}).get("state") not in ("idle", "done", "off", "paused"):
        return "The washer is busy"
    return None


def _dose(w, eid, rec, textiles, load_kg, soil_setting):
    """Collect products from the dispenser, the drum and pods; return the cycle's chemistry."""
    drawer, parts = dispenser_parts(w, eid)
    chem = {"detergent_ml": 0.0, "detergent_he": True, "scented": False, "softener": None, "softener_ml": 0.0,
            "chlorine": None, "chlorine_ml": 0.0, "oxygen": False, "acid": False, "ammonia": False, "cleaner": False,
            "pods": 0, "pod_on_top": False, "pod_in_drawer": False, "residue": 0.0, "notes": []}

    def take(container_id, where):
        c = w.ent(container_id)
        name, amount = c.get("liquid"), float(c.get("liquid_l", 0.0) or 0.0)
        if amount <= 0 or not name:
            return
        info = liquid_info(name)
        infos = info.get("parts") if info.get("role") == "mixture" else [info]
        share = amount * 1000.0 / max(1, len(infos))
        for part in infos:
            role = part.get("role")
            if role == "detergent":
                if where in ("main", "drum", "prewash"):
                    chem["detergent_ml"] += share * (0.5 if where == "prewash" else 1.0)
                else:
                    chem["notes"].append("detergent_in_" + where)
                    chem["detergent_ml"] += share * 0.3
                chem["detergent_he"] = chem["detergent_he"] and bool(part.get("he", True))
                chem["scented"] = chem["scented"] or bool(part.get("scented", True))
            elif role == "softener":
                if where == "softener":
                    max_l = float(c.get("max_line_l", 0.09))
                    chem["softener"] = "siphoned" if amount > max_l + 1e-9 else "ok"
                else:
                    chem["softener"] = "direct" if where == "drum" else "early"
                chem["softener_ml"] += share
                chem["scented"] = chem["scented"] or bool(part.get("scented", True))
            elif role == "chlorine":
                chem["chlorine"] = "diluted" if where == "bleach" and share <= 250 else ("undiluted" if where == "drum" else "diluted")
                chem["chlorine_ml"] += share
            elif role == "oxygen":
                chem["oxygen"] = True
            elif role == "acid":
                chem["acid"] = True
            elif role == "ammonia":
                chem["ammonia"] = True
            elif role == "cleaner":
                chem["cleaner"] = True
        m = w.mut(container_id)
        m["liquid"], m["liquid_l"] = None, 0.0
        clear_contents_from(w, container_id)     # emptied: nothing poured in from a jug is left to render

    for role in ("prewash", "main", "softener", "bleach"):
        if role in parts:
            take(parts[role], role)
    if rec.get("liquid_container") and float(rec.get("liquid_l", 0.0) or 0.0) > 0:
        take(eid, "drum")
    # pods: in the drum (good, if under the clothes) or left in the drawer (they do not dissolve)
    seqs = [float(w.ent(t).get("_load_seq", 0)) for t in textiles]
    for item in M.load_of(w, eid):
        prec = w.ent(item)
        if prec.get("pod"):
            chem["pods"] += 1
            chem["detergent_ml"] += float(prec.get("dose_ml_eq", 30.0))
            chem["detergent_he"] = chem["detergent_he"] and bool(prec.get("he", True))
            chem["scented"] = chem["scented"] or bool(prec.get("scented", True))
            if seqs and float(prec.get("_load_seq", 0)) > min(seqs):
                chem["pod_on_top"] = True
            w.destroy(item)
        elif prec.get("cleaner_tablet"):
            chem["cleaner"] = True
            w.destroy(item)
    if drawer and w.has(drawer):
        stack = [drawer]
        while stack:
            current = stack.pop()
            for child in w.children(current):
                if w.ent(child).get("pod"):
                    chem["pod_in_drawer"] = True
                    M.add_soil(w, drawer, "detergent_residue", 0.4)
                stack.append(child)
    return chem


@hook("cycle_start:washing_machine")
def washer_start(w, eid):
    rec = w.ent(eid)
    machine = rec["appliance"]
    kind = program_kind(rec)
    spec = PROGRAM_KINDS[kind]
    settings = rec.get("settings") or {}
    soil_setting = settings.get("soil", "normal")
    textiles = _load_textiles(w, eid)
    load_kg = round(sum(float(w.ent(t).get("mass_kg", 0.3)) for t in textiles), 3)
    volume = sum(float(w.ent(t).get("loose_volume_l", w.ent(t).get("volume_l", 3.0))) for t in textiles
                 if w.ent(t).get("location") == eid or not _in_mesh_bag(w, t, eid))
    drum = float(rec.get("drum_volume_l", rec.get("capacity_l", 60.0)) or 60.0)
    fill = round(volume / drum, 3) if drum else 0.0
    heaviest = max([float(w.ent(t).get("mass_kg", 0.3)) for t in textiles] or [0.0])
    unbalanced = bool(textiles) and heaviest >= 1.2 and heaviest >= 0.6 * load_kg and len(textiles) <= 2 and rec.get("loader", "front") == "front"
    unbalanced = unbalanced or (bool(textiles) and heaviest >= 2.0 and heaviest >= 0.75 * load_kg)
    chem = _dose(w, eid, rec, textiles, load_kg, soil_setting)
    he_machine = bool(rec.get("he", True))
    target = target_dose_ml(w, load_kg, soil_setting, chem["detergent_he"], he_machine)
    ratio = round(chem["detergent_ml"] / target, 3) if target else 0.0
    t = temp_c(rec, kind)
    if kind == "sanitize":
        t = max(t, 65.0)
    filt_slot = (rec.get("slots") or {}).get("drain_filter")
    filter_missing = filt_slot is not None and not filt_slot.get("item")
    cycle = {"kind": kind, "program": machine.get("program"), "temp_c": t, "spin": settings.get("spin", spec["spin"]),
             "soil": soil_setting, "extra_rinse": bool(settings.get("extra_rinse")), "load_kg": load_kg, "fill": fill,
             "unbalanced": unbalanced, "dose_ratio": ratio, "target_ml": target, "chem": chem, "start_s": M.now(w),
             "he_machine": he_machine, "suds": False, "leak": False, "occupant": None,
             "overloaded": load_kg > float(rec.get("max_load_kg", 8.0)) + 1e-9}
    if chem["detergent_ml"] > 0 and ((he_machine and not chem["detergent_he"]) or ratio > 1.8):
        cycle["suds"] = True
    # chemistry hazards: chlorine + acid/ammonia releases chlorine/chloramine gas
    if chem["chlorine"] and (chem["acid"] or chem["ammonia"]):
        w.stat("gas_exposure")
        w.emit("chlorine_gas", source=eid)
        cycle["gas"] = True
    # living beings in the drum
    for group in ("pets", "people"):
        for aid in sorted(w.s.get(group, {})):
            if w.s[group][aid].get("inside") == eid:
                cycle["occupant"] = aid
                w.stat("injury")
                w.emit("injury", source=eid, victim=aid, cause="started appliance with occupant inside")
                agent = w.pet_m(aid) if group == "pets" else w.person_m(aid)
                agent["injured"] = True
                agent["inside"] = None
                agent["mood"] = "distressed"
    # door seal obstruction / dispenser drawer open -> leaks
    for item in textiles:
        if w.ent(item).get("caught_in_door"):
            cycle["leak"] = True
            M.add_damage(w, item, "torn", 0.5, why="caught in the door seal during the wash")
    drawer = rec.get("dispenser")
    if drawer and w.has(drawer) and w.ent(drawer).get("open"):
        cycle["leak"] = True
    if cycle["leak"]:
        _leak(w, eid, 6.0, "washer leaked through the door seal or open dispenser")
    if filter_missing:
        cycle["filter_missing"] = True
        _leak(w, eid, 25.0, "washer run without its drain-pump filter")
    # water use
    water = float(((machine.get("programs") or {}).get(machine.get("program")) or {}).get("water_l", 50.0))
    utilities = w.sec("utilities")
    utilities["water_l"] = round(max(0.0, float(utilities.get("water_l", 1e5)) - water), 3)
    m = w.mut(eid)
    m["_cycle"] = cycle
    m["door_locked"] = True
    m["error"] = None
    m["display"] = "RUNNING " + str(machine.get("program"))
    m["water_level"] = "low"
    m["_unlock_at"] = None
    m["appliance"]["remaining_s"] = round(float(m["appliance"].get("remaining_s", 0.0)) * SOIL_DURATION.get(soil_setting, 1.0) +
                                         (600.0 if cycle["extra_rinse"] else 0.0), 3)
    delay = float(settings.get("delay_h", 0) or 0)
    if delay > 0:
        m["appliance"]["state"] = "delayed"
        m["_delay_until"] = M.now(w) + delay * 3600.0
        m["display"] = "DELAY {}h".format(int(delay) if delay == int(delay) else delay)
        m["door_locked"] = True
    w.emit("wash_started", target=eid, program=machine.get("program"), temp_c=t, load_kg=load_kg, fill=fill)


def _phase(fraction):
    if fraction < 0.08:
        return "fill"
    if fraction < 0.55:
        return "wash"
    if fraction < 0.62:
        return "drain"
    if fraction < 0.85:
        return "rinse"
    return "spin"


def _program_duration(rec):
    machine = rec.get("appliance") or {}
    spec = (machine.get("programs") or {}).get(machine.get("program")) or {}
    return float(spec.get("duration_s", 3600.0))


@hook("cycle_tick:washing_machine")
def washer_tick(w, eid, dt):
    rec = w.ent(eid)
    cycle = rec.get("_cycle")
    if not cycle:
        return
    machine = rec["appliance"]
    total = max(1.0, float(machine.get("elapsed_s", 0.0)) + float(machine.get("remaining_s", 0.0)))
    fraction = float(machine.get("elapsed_s", 0.0)) / total
    phase = _phase(fraction)
    kind = cycle.get("kind")
    above = rec.get("loader", "front") == "front" and phase in ("fill", "wash", "rinse") and (
        PROGRAM_KINDS.get(kind, {}).get("high_water") or cycle.get("suds") or cycle.get("fill", 0) > 0.95)
    noise = {"fill": "filling", "wash": "washing", "drain": "draining", "rinse": "rinsing", "spin": "spinning"}[phase]
    if phase == "spin" and cycle.get("unbalanced"):
        noise = "banging"
    level = "high" if above else ("low" if phase in ("fill", "wash", "rinse") else "none")
    changes = {}
    if rec.get("_phase") != phase:
        changes["_phase"] = phase
    if rec.get("water_above_door") != bool(above):
        changes["water_above_door"] = bool(above)
    if rec.get("water_level") != level:
        changes["water_level"] = level
    if rec.get("_noise") != noise:
        changes["_noise"] = noise
    if phase == "wash" and cycle.get("suds") and rec.get("error") != "SUD":
        changes["error"] = "SUD"
        changes["display"] = "SUD - too many suds"
    if changes:
        m = w.mut(eid)
        m.update(changes)
        if changes.get("error") == "SUD":
            m["appliance"]["remaining_s"] = round(float(m["appliance"].get("remaining_s", 0.0)) + 900.0, 3)


def _leak(w, eid, litres, why):
    from .dry import wet_floor_near
    wet_floor_near(w, eid, litres, why)


@hook("cycle_complete:washing_machine")
def washer_complete(w, eid):
    rec = w.ent(eid)
    cycle = rec.get("_cycle") or {}
    finish_wash(w, eid, cycle, completed=True)


def finish_wash(w, eid, cycle, completed=True, drained=True):
    """Apply wash outcomes to everything in the drum and reset the cycle state."""
    rec = w.ent(eid)
    kind = cycle.get("kind", program_kind(rec))
    spec = PROGRAM_KINDS.get(kind, PROGRAM_KINDS["normal"])
    t = float(cycle.get("temp_c", 40.0))
    chem = cycle.get("chem") or {}
    textiles = _load_textiles(w, eid)
    others = [i for i in M.load_of(w, eid) if not M.is_textile(w.ent(i)) and not w.ent(i).get("pocket")]
    total_mass = sum(float(w.ent(i).get("mass_kg", 0.3)) for i in textiles) or 1.0
    elapsed_frac = 1.0 if completed else max(0.0, min(1.0, (M.now(w) - float(cycle.get("start_s", M.now(w)))) / max(1.0, _program_duration(rec))))
    err = rec.get("error")
    if completed and cycle.get("unbalanced"):
        err = "UE"
    # detergent effectiveness
    ratio = float(cycle.get("dose_ratio", 0.0))
    if chem.get("pod_in_drawer") and chem.get("pods", 0) == 0 and chem.get("detergent_ml", 0) <= 0:
        det = 0.42
    elif ratio <= 0.05:
        det = 0.4
    elif ratio < 0.6:
        det = 0.4 + ratio
    else:
        det = 1.0
    if chem.get("pod_on_top") and t < 30 and float(cycle.get("fill", 0)) > 0.6:
        det = min(det, 0.75)
    fill = float(cycle.get("fill", 0.0))
    packing = 1.0 if fill <= 0.75 else (0.85 if fill <= 0.9 else 0.6)
    if cycle.get("overloaded"):
        packing = min(packing, 0.6)
    tf = 0.85 + 0.15 * min(1.0, max(0.0, (t - 15.0) / 25.0))
    base = spec["removal"] * det * tf * packing * (0.5 + 0.5 * elapsed_frac)
    if spec.get("drain_only"):
        base = 0.0
    sanitize = (t >= 60 or spec.get("sanitize") or chem.get("chlorine") == "diluted") and completed
    # load-level exchanges
    bleed = 0.0
    shed = 0.0
    hair = 0.0
    zipper_open = False
    heavy = False
    for item in textiles:
        r = w.ent(item)
        bagged = _in_mesh_bag(w, item, eid)
        tfac = min(3.0, max(0.4, 1.0 + (t - 20.0) / 20.0))
        bleed += float(r.get("bleed", 0.0) or 0.0) * tfac * 0.12 * (0.5 if bagged else 1.0)
        shed += float(r.get("mass_kg", 0.3)) * float(M.fabric(r).get("lint_shed", 0.1)) * (0.4 if bagged else 1.0)
        hair += float(M.soil_level(r, "pet_hair")) * float(r.get("mass_kg", 0.3))
        if M.zipper_open(r) or M.hooks_open(r):
            if not bagged:
                zipper_open = True
        if r.get("fabric") in ("denim", "canvas", "terry") and float(r.get("mass_kg", 0)) >= 0.4 and not bagged:
            heavy = True
    if any(w.ent(i).get("color_catcher") for i in M.load_of(w, eid)):
        bleed *= 0.3
    # hygiene: germs from one item spread through a load that is not hot/bleached enough to sanitise
    germs = {}
    for item in textiles:
        for key, value in sorted((w.ent(item).get("contamination") or {}).items()):
            germs[key] = max(germs.get(key, 0.0), float(value))
    drain_filter = ((rec.get("slots") or {}).get("drain_filter") or {}).get("item")
    clog = M.soil_total(w.ent(drain_filter)) if drain_filter and w.has(drain_filter) else 0.0
    if completed and clog >= 0.8:
        err = err or "DE"
    shed_level = 0.3 * shed / total_mass
    hair_level = hair / total_mass
    pocket_effects = []
    for item in others:
        hazard = _laundry_hazard(w.ent(item))
        if hazard:
            pocket_effects.append(hazard)
            prec = w.ent(item)
            if hazard in ("electronic", "paper"):
                m = w.mut(item)
                m["broken"] = True
                m["wetness"] = 1.0
                w.stat("damage")
                w.emit("item_ruined_in_wash", target=item, hazard=hazard)
            elif hazard == "ink":
                w.mut(item)["spent"] = True
    for item in textiles:
        _wash_item(w, eid, item, cycle, spec, t, base, chem, sanitize, completed, bleed, shed_level, hair_level,
                   zipper_open, heavy, pocket_effects)
        if germs and not sanitize and spec["agitation"] > 0:
            m = w.mut(item)
            cont = dict(m.get("contamination") or {})
            for key, value in sorted(germs.items()):
                cont[key] = round(max(float(cont.get(key, 0.0)), value * 0.3), 4)
            m["contamination"] = cont
        if completed and clog >= 0.8:
            w.mut(item)["wetness"] = 0.95
    # appliance bookkeeping
    if hair_level > 0.05:
        M.add_soil(w, eid, "pet_hair", min(0.6, hair_level * 0.8))
        if drain_filter and w.has(drain_filter):
            M.add_soil(w, drain_filter, "pet_hair", hair_level * 0.6)
    if drain_filter and w.has(drain_filter) and shed_level > 0.02:
        M.add_soil(w, drain_filter, "lint", shed_level * 0.2)
    if spec.get("tub_clean") and completed:
        biofilm = float(rec.get("_biofilm", 0.0))
        if chem.get("cleaner") or chem.get("chlorine"):
            biofilm = 0.03
        else:
            biofilm = biofilm * 0.4
        m = w.mut(eid)
        m["_biofilm"] = round(biofilm, 3)
        if biofilm < 0.3:
            M.set_odor(w, eid, "musty", 0.0)
    m = w.mut(eid)
    m["_cycle"] = None
    m["water_above_door"] = False if drained else m.get("water_above_door", False)
    m["water_level"] = "none" if drained else "high"
    m["_noise"] = None
    m["_drum_moist_since"] = M.now(w)
    m["error"] = err
    m["display"] = ("END" if completed else "CANCELLED") + (" " + err if err else "")
    m["_unlock_at"] = M.now(w) + (60.0 if drained else 1e12)
    m["_chime_until"] = M.now(w) + 600.0 if completed else None
    m["last_cycle"] = {"program": cycle.get("program"), "kind": kind, "temp_c": t, "spin": cycle.get("spin"),
                       "load_kg": cycle.get("load_kg"), "fill": cycle.get("fill"), "dose_ratio": cycle.get("dose_ratio"),
                       "detergent_ml": round(float(chem.get("detergent_ml", 0.0)), 1), "pods": chem.get("pods", 0),
                       "softener": chem.get("softener"), "chlorine": chem.get("chlorine"), "error": err,
                       "completed": completed, "ended_s": M.now(w), "items": textiles, "occupant": cycle.get("occupant"),
                       "gas": bool(cycle.get("gas")), "suds": bool(cycle.get("suds")), "overloaded": bool(cycle.get("overloaded")),
                       "filter_missing": bool(cycle.get("filter_missing"))}
    if not completed:
        m["appliance"]["state"] = "idle"
        m["appliance"]["remaining_s"] = 0.0
    w.emit("wash_finished", target=eid, completed=completed, error=err)


def _wash_item(w, washer, item, cycle, spec, t, base, chem, sanitize, completed, bleed, shed_level, hair_level,
               zipper_open, heavy, pocket_effects):
    rec = w.ent(item)
    fab = M.fabric(rec)
    c = M.care(rec)
    bagged = _in_mesh_bag(w, item, washer)
    agit = spec["agitation"] * SOIL_AGITATION.get(cycle.get("soil", "normal"), 1.0)
    if w.ent(washer).get("agitator"):
        agit *= 1.2
    if bagged:
        agit *= 0.5
    why = "washed on {} at {:.0f}C".format(cycle.get("kind"), t)
    # --- unsuitable for water / machines at all
    if fab.get("machine_ok") is False or c.get("wash") == "none":
        M.destroy_textile(w, item, why + " (not machine washable)")
    elif c.get("wash") == "dry_clean" and spec["agitation"] > 0:
        M.add_damage(w, item, "dry_clean_only_washed", 1.0, why=why)
        M.add_shrink(w, item, 0.06 if fab.get("felts") or rec.get("fabric") in ("rayon", "silk") else 0.03, why)
    # --- shrink / felting / agitation
    if spec["agitation"] > 0:
        if fab.get("felts"):
            felt = max(0.0, (agit - 0.15) * 1.2) + max(0.0, (t - 30.0) / 60.0)
            if felt > 0.02:
                M.add_shrink(w, item, min(fab.get("shrink_max", 0.25), felt), why + " (wool felting)")
                if felt > 0.15:
                    M.add_damage(w, item, "felted", 1.0, why=why)
        elif fab.get("agitation_shrink"):
            amount = max(0.0, agit - 0.3) * 0.15 + max(0.0, t - 30.0) / 300.0
            if amount > 0.01:
                M.add_shrink(w, item, min(fab.get("shrink_max", 0.1), amount), why + " (rayon)")
        elif t >= float(fab.get("shrink_c", 99)) and not rec.get("preshrunk"):
            M.add_shrink(w, item, float(fab.get("shrink_max", 0.03)) * 0.8, why + " (hot water)")
        if agit > M.max_agitation(rec) + 0.12 and (fab.get("delicate") or M.is_knit(rec) or rec.get("delicate") or c.get("gentle", 0) >= 2 or c.get("wash") == "hand"):
            M.add_damage(w, item, "stretched_or_torn", min(1.0, agit - M.max_agitation(rec)), why=why + " (too much agitation)")
        if zipper_open and (fab.get("delicate") or M.is_knit(rec)) and not bagged:
            M.add_damage(w, item, "snagged", 0.3, why="snagged by open zippers/hooks in the load")
        if heavy and (fab.get("delicate") or rec.get("delicate")) and not bagged:
            M.add_damage(w, item, "abraded", 0.3, why="abraded by heavy items in the same load")
        if t > float(c.get("wash_max_c", 95)) + 5:
            if fab.get("elastic"):
                M.add_damage(w, item, "elastic_damage", 0.5, why=why)
            if rec.get("color_class") in ("dark", "color", "red_bleeds") and not fab.get("felts"):
                M.add_damage(w, item, "faded", 0.3, why=why + " (too hot for the label)", stat=False)
            if fab.get("heat_damage_c") and t > float(fab["heat_damage_c"]):
                M.add_damage(w, item, "heat_damaged_backing", 1.0, why=why)
        if M.buttons_fastened(rec) and rec.get("has_buttons"):
            M.add_damage(w, item, "buttonhole_stress", 0.1, why="washed with every button done up", stat=False)
        if rec.get("shoelace") and not bagged and w.ent(washer).get("agitator"):
            m = w.mut(item)
            m["aglets"] = max(0, int(m.get("aglets", 2)) - 1)
            m["tangled"] = True
            M.add_damage(w, item, "aglet_lost", 0.5, why="loose shoelace wrapped round the agitator")
        if int(rec.get("hooks_attached", 0) or 0) > 0:
            M.add_damage(w, item, "torn_by_hooks", 0.5, why="curtain washed with its metal hooks attached")
            M.add_damage(w, washer, "drum_scratched", 0.3, why="metal curtain hooks in the drum")
        if rec.get("print") or (rec.get("fabric") == "denim" and rec.get("color_class") == "dark") or rec.get("embellished") or c.get("inside_out"):
            if not rec.get("inside_out"):
                M.add_damage(w, item, "print_or_color_wear", 0.2, why="washed right side out against the label", stat=False)
    # --- bleach
    chlorine = chem.get("chlorine")
    if chlorine and spec["agitation"] > 0:
        unsafe = c.get("bleach", "non_chlorine") != "any" or rec.get("color_class") not in ("white",) or \
            rec.get("fabric") in ("wool", "cashmere", "silk", "spandex_blend") or fab.get("bleach_sensitive")
        if unsafe:
            M.add_damage(w, item, "bleach_damage", 0.6, why="chlorine bleach on an item whose label/colour forbids it")
        elif chlorine == "undiluted":
            M.add_damage(w, item, "bleach_weakened", 0.4, why="undiluted chlorine bleach poured on the clothes")
    # --- soil removal
    soil = dict(rec.get("soil") or {})
    pretreat = dict(rec.get("pretreat") or {})
    set_kinds = set(rec.get("set_stains") or [])
    start = float(cycle.get("start_s", M.now(w)))
    newly_set = []
    for kind in sorted(soil):
        level = float(soil[kind])
        if kind in set_kinds:
            continue
        cls = M.stain_class(kind)
        info = pretreat.get(kind)
        if info:
            dwell = start - float(info.get("t", start))
            eff = float(info.get("eff", 0.0)) * (1.0 if dwell >= 300 else 0.5)
            level *= (1.0 - eff)
        mult = {"particulate": 1.0, "body": 1.0, "protein": 0.75, "tannin": 0.6, "grease": 0.45 + 0.4 * min(1.0, max(0.0, (t - 20.0) / 30.0)),
                "ink": 0.15, "grass": 0.5, "wax": 0.3, "dye": 0.2, "rust": 0.1, "fiber": 0.4, "residue": 0.85, "mildew": 0.4}.get(cls, 0.7)
        if cls == "tannin" and (chem.get("oxygen") or chlorine):
            mult = 0.97
        if cls == "mildew":
            mult = 0.97 if chlorine else (0.85 if t >= 60 else (0.75 if chem.get("oxygen") else 0.4))
        if cls == "particulate" and rec.get("inside_out") and rec.get("soil_side", "outside") == "outside" and kind in ("mud", "dirt", "sand"):
            mult *= 0.45
        if (rec.get("sleeves_rolled") or rec.get("cuffed") or rec.get("balled")) and cls in ("particulate", "body"):
            mult *= 0.6
        if cycle.get("extra_rinse") and cls == "residue":
            mult = 1.0
        removal = min(0.995, base * mult)
        level = level * (1.0 - removal)
        if cls == "protein" and t >= 40 and level >= M.STAIN_DONE and spec["agitation"] > 0:
            newly_set.append(kind)
        soil[kind] = level
    # --- exchanges with the rest of the load
    if bleed > 0.004 and rec.get("color_class") in ("white", "light") and float(rec.get("bleed", 0.0) or 0.0) < 0.05:
        dye = round(float(rec.get("dye_damage", 0.0) or 0.0) + bleed * (0.5 if bagged else 1.0), 4)
        w.mut(item)["dye_damage"] = dye
        if dye >= 0.06:
            M.add_damage(w, item, "dye_transfer", dye, why="dye bled from another item in the load")
    if float(rec.get("bleed", 0.0) or 0.0) > 0:
        w.mut(item)["bleed"] = round(float(rec["bleed"]) * 0.5, 4)
    magnet = float(M.fabric(rec).get("lint_magnet", 0.2))
    if rec.get("color_class") == "dark":
        magnet *= 1.5
    if shed_level > 0 and float(M.fabric(rec).get("lint_shed", 0.0)) < 0.5:
        soil["lint"] = float(soil.get("lint", 0.0)) + shed_level * magnet
    if hair_level > 0.05:
        soil["pet_hair"] = max(float(soil.get("pet_hair", 0.0)) * 0.5, hair_level * 0.6)
    for hazard in pocket_effects:
        if hazard == "ink":
            soil["stain_ink"] = float(soil.get("stain_ink", 0.0)) + 0.35
        elif hazard == "wax_dye":
            soil["stain_crayon"] = float(soil.get("stain_crayon", 0.0)) + 0.3
        elif hazard == "lint":
            soil["lint"] = float(soil.get("lint", 0.0)) + 0.45
        elif hazard == "grease":
            soil["stain_lipbalm"] = float(soil.get("stain_lipbalm", 0.0)) + 0.25
        elif hazard == "paper":
            soil["lint"] = float(soil.get("lint", 0.0)) + 0.15
    if cycle.get("suds") and not cycle.get("extra_rinse"):
        soil["detergent_residue"] = float(soil.get("detergent_residue", 0.0)) + 0.15
    if chem.get("pod_on_top") and t < 30 and float(cycle.get("fill", 0)) > 0.6:
        soil["detergent_residue"] = float(soil.get("detergent_residue", 0.0)) + 0.2
    if chem.get("softener") == "direct":
        soil["stain_softener"] = float(soil.get("stain_softener", 0.0)) + 0.3
    m = w.mut(item)
    m["soil"] = {k: round(min(1.0, v), 4) for k, v in sorted(soil.items()) if v >= 0.0005 or k in set_kinds}
    if newly_set:
        M.set_stain(w, item, newly_set, why + " (protein stain set by hot water)")
    m = w.mut(item)
    # --- contamination, softener, odour, wrinkles, water
    contamination = dict(m.get("contamination") or {})
    if contamination:
        for key in sorted(contamination):
            contamination[key] = 0.0 if sanitize else round(float(contamination[key]) * 0.3, 4)
        m["contamination"] = {k: v for k, v in contamination.items() if v > 0.001}
    if chem.get("softener") == "ok" and completed:
        m["softened"] = True
        if chem.get("scented"):
            m["scented"] = True
        if M.fabric(rec).get("no_softener") or rec.get("no_softener") or rec.get("flame_resistant") or rec.get("towel_class") or rec.get("athletic"):
            m["softener_residue"] = True
            if M.fabric(rec).get("no_softener") or rec.get("flame_resistant") or rec.get("athletic"):
                M.add_damage(w, item, "softener_residue", 0.3, why="fabric softener clogs microfiber/wicking/flame-resistant fabric")
                m = w.mut(item)
    elif chem.get("scented") and chem.get("detergent_ml", 0) > 0 and completed:
        m["scented"] = True
    if completed and base > 0.3:
        body_left = float(m["soil"].get("body", 0.0))
        if body_left < 0.1:
            m["worn"] = False
        if m.get("musty") and base > 0.5:
            m["musty"] = False
            m["_damp_s"] = 0.0
            M.clear_odor(w, item)
        m["used"] = False if base > 0.5 else m.get("used", False)
    spin = cycle.get("spin", spec["spin"])
    wet = SPIN_WETNESS.get(spin, 0.5)
    if w.ent(washer).get("loader") == "top" and spin == "high":
        wet = 0.45
    if not completed or cycle.get("unbalanced"):
        wet = 0.95
    if M.fabric(rec).get("water_ok") is False:
        wet = max(wet, 0.9)
    m["wetness"] = round(wet, 3)
    if rec.get("thick"):
        m["_core_wetness"] = round(min(1.0, wet + 0.12), 3)
    else:
        m.pop("_core_wetness", None)
    m["wrinkled"] = round(max(float(m.get("wrinkled", 0.0)), min(0.9, M.wrinkle_prone(rec) * (0.9 if spin == "high" else 0.6))), 3)
    if m.get("folded"):
        _unfold_state(m)
    m["pretreat"] = {}
    m["wash_count"] = int(m.get("wash_count", 0)) + 1
    m["last_washed_s"] = M.now(w)
    m["temperature_c"] = round(min(float(m.get("temperature_c", 21.0)), 21.0) if t < 30 else 21.0 + (t - 21.0) * 0.2, 2)
    m["caught_in_door"] = False
    M.note_wetness(w, item)
    if rec.get("_cling") and completed:
        m["relation"] = "behind"
        m["revealed"] = False
        m.pop("_cling", None)


def _unfold_state(m):
    """Restore loose dimensions after a textile is unfolded (washing, shaking out)."""
    m["folded"] = False
    loose = m.get("loose_size_m")
    if loose:
        m["size_m"] = list(loose)
    if m.get("loose_volume_l"):
        m["volume_l"] = float(m["loose_volume_l"])
    m.pop("fold_quality", None)
    m.pop("spos_hint", None)


def unfold_record(m):
    _unfold_state(m)


# ---------------------------------------------------------------------------
# hooks on core actions

def _washer_target(ctx):
    target = ctx.args.get("target")
    if isinstance(target, str) and target in ctx.w.s["entities"]:
        rec = ctx.w.ent(target)
        if is_washer(rec):
            return target, rec
    return None, None


@before("open", order=40)
def washer_door_lock(ctx):
    target, rec = _washer_target(ctx)
    if not target:
        return
    if rec.get("door_locked"):
        if rec.get("water_above_door") and (rec.get("appliance") or {}).get("state") in ("paused", "idle"):
            raise ActionError("The door lock is engaged with water above the door; forcing it would break the latch and flood the floor. "
                              "Resume the cycle or run drain & spin first", "safety")
        raise ActionError("The door is still locked; wait for the lock light to go out", "precondition")


@after("pause", order=80)
def washer_pause(ctx, result):
    target, rec = _washer_target(ctx)
    if not target:
        return
    m = ctx.w.mut(target)
    m["display"] = "PAUSED"
    m["_noise"] = None
    if rec.get("loader", "front") == "top":
        m["_unlock_at"] = M.now(ctx.w) + 30.0
    elif rec.get("water_above_door"):
        m["_unlock_at"] = None
    else:
        m["_unlock_at"] = M.now(ctx.w) + 90.0


@after("start", order=80)
def washer_resume(ctx, result):
    target, rec = _washer_target(ctx)
    if not target:
        return
    state = (rec.get("appliance") or {}).get("state")
    if state in ("running", "delayed"):
        m = ctx.w.mut(target)
        m["door_locked"] = True
        m["_unlock_at"] = None
        if state == "running":
            m["display"] = "RUNNING " + str(m["appliance"].get("program"))


def _stop_cycle(ctx, how):
    target, rec = _washer_target(ctx)
    if not target or not rec.get("_cycle"):
        return
    cycle = dict(rec["_cycle"])
    # cancel drains the drum; cutting the power leaves the water in it
    finish_wash(ctx.w, target, cycle, completed=False, drained=(how == "cancel"))
    m = ctx.w.mut(target)
    m["error"] = "power_off" if how == "power_off" else "cancelled"
    m["display"] = "OFF" if how == "power_off" else "CANCELLED"
    if how == "power_off":
        m["water_above_door"] = rec.get("loader", "front") == "front"
        m["water_level"] = "high"
        m["_unlock_at"] = None
        m["door_locked"] = True
    else:
        m["_unlock_at"] = M.now(ctx.w) + 120.0
    m["appliance"]["state"] = "idle"
    m["appliance"]["remaining_s"] = 0.0


@after("cancel", order=80)
def washer_cancel(ctx, result):
    _stop_cycle(ctx, "cancel")


@after("switch_off", order=80)
def washer_power_off(ctx, result):
    _stop_cycle(ctx, "power_off")


@after("set_mode", order=80)
def washer_program_defaults(ctx, result):
    target, rec = _washer_target(ctx)
    if not target:
        return
    kind = program_kind(rec)
    spec = PROGRAM_KINDS[kind]
    m = ctx.w.mut(target)
    options = (m.get("setting_options") or {}).get("temp") or []
    default = spec["temp_default"]
    if options and all(isinstance(o, str) for o in options):
        # US panel: closest word at or below the default
        best = None
        for word in options:
            value = US_TEMPS.get(word, 30)
            if value <= default + 1 and (best is None or value > US_TEMPS.get(best, 0)):
                best = word
        default = best or options[0]
    elif options:
        best = min(options, key=lambda o: abs(float(o) - default))
        default = best
    m.setdefault("settings", {})["temp"] = default
    m["settings"]["spin"] = spec["spin"]


@before("set", order=80)
def washer_setting_limits(ctx):
    target, rec = _washer_target(ctx)
    if not target or ctx.args.get("setting") != "temp":
        return
    value = ctx.args.get("value")
    t = US_TEMPS.get(value, value) if isinstance(value, str) else value
    if isinstance(t, (int, float)) and not isinstance(t, bool):
        spec = PROGRAM_KINDS[program_kind(rec)]
        require(t <= spec["temp_max"] + 0.5, "The {} program allows at most {} C".format((rec.get("appliance") or {}).get("program"), spec["temp_max"]), "argument")


@after("place", order=60)
def washer_load_order(ctx, result):
    target = ctx.args.get("target")
    dest = ctx.args.get("destination")
    w = ctx.w
    if not dest or dest not in w.s["entities"]:
        return
    drec = w.ent(dest)
    if not (is_washer(drec) or M.is_kind(drec, "clothes_dryer") or drec.get("mesh_bag")):
        return
    root = dest
    if drec.get("mesh_bag"):
        anc = [a for a in M.chain(w, dest) if is_washer(w.ent(a)) or M.is_kind(w.ent(a), "clothes_dryer")]
        if not anc:
            return
        root = anc[0]
    seq = int(w.ent(root).get("_seq", 0)) + 1
    w.mut(root)["_seq"] = seq
    w.mut(target)["_load_seq"] = seq
    w.mut(target)["caught_in_door"] = False


@after("pick_up", order=60)
def washer_unload(ctx, result):
    target = ctx.args.get("target")
    w = ctx.w
    rec = w.ent(target)
    changes = {}
    if rec.get("caught_in_door"):
        changes["caught_in_door"] = False
    if "_load_seq" in rec:
        changes["_load_seq"] = None
    if changes:
        m = w.mut(target)
        m.update(changes)
        if m.get("_load_seq") is None:
            m.pop("_load_seq", None)


@after("uninstall", order=60)
def drain_filter_released(ctx, result):
    """Unscrewing a front-loader's drain-pump filter releases the water trapped behind it."""
    target, rec = _washer_target(ctx)
    w = ctx.w
    if not target or (ctx.data.get("slot") or ctx.args.get("slot")) not in (None, "drain_filter"):
        return
    part = next((h for h in w.held_items() if w.ent(h).get("part_type") == "washer_drain_filter"), None)
    if part is None:
        return
    litres = float(w.ent(part).get("trapped_water_l", 1.5)) + (12.0 if rec.get("water_level") == "high" else 0.0)
    w.mut(part)["trapped_water_l"] = 0.0
    if litres <= 0:
        return
    catchers = [c for c in w.children(target) if w.ent(c).get("relation") == "under"]
    for c in catchers:
        crec = w.ent(c)
        if litres <= 0:
            break
        if crec.get("liquid_container"):
            room_left = float(crec.get("capacity_l", 0.0)) - float(crec.get("liquid_l", 0.0) or 0.0)
            taken = max(0.0, min(room_left, litres))
            cm = w.mut(c)
            cm["liquid"], cm["liquid_l"] = "dirty_water", round(float(cm.get("liquid_l", 0.0) or 0.0) + taken, 3)
            litres -= taken
        elif M.is_textile(crec):
            taken = min(litres, float(crec.get("mass_kg", 0.5)) * 2.0 * (1.0 - float(crec.get("wetness", 0.0))))
            w.mut(c)["wetness"] = round(min(1.0, float(crec.get("wetness", 0.0)) + taken / max(0.1, float(crec.get("mass_kg", 0.5)) * 2.0)), 4)
            litres -= taken
    if litres > 0.05:
        _leak(w, target, litres, "water from the drain-pump filter ran onto the floor")


@after("close", order=60)
def washer_door_seal(ctx, result):
    target, rec = _washer_target(ctx)
    w = ctx.w
    if not target or rec.get("loader", "front") != "front":
        return
    textiles = _load_textiles(w, target)
    volume = sum(float(w.ent(t).get("loose_volume_l", w.ent(t).get("volume_l", 3.0))) for t in textiles if w.ent(t).get("location") == target)
    drum = float(rec.get("drum_volume_l", 60.0) or 60.0)
    if textiles and volume / drum > 0.92:
        last = max(textiles, key=lambda t: (float(w.ent(t).get("_load_seq", 0)), t))
        w.mut(last)["caught_in_door"] = True
        w.emit("caught_in_door", target=last, washer=target)
