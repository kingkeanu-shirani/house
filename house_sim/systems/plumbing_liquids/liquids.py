"""Liquid-in-container model (P-approx, lumped volumes).

A container that holds liquid carries (CONVENTIONS "Liquids in containers"):
  liquid        derived name of the contents (dominant liquid, "soapy_water", "mixed"...); kept as a residue name
                when liquid_l falls to 0 so later hooks still know what was in it
  liquid_l      liters of liquid
  capacity_l    liters to the brim (basins use basin_l: liters to the rim / overflow)
  fill_line_l   optional marked max/target fill line (kettle MAX, pitcher reservoir line, detergent tank line)
  temperature_c liquid temperature (the kernel thermal model relaxes it; tau grows with liquid_l)
  _mix          {liquid: liters} ground-truth composition (hidden)
  cl_chem       {product: ml} dissolved products (shared with the cleaning domain's solution model)
  potable       whether the contents are safe drinking water (derived; hose/condensate/bathroom-tap truth)
  filled_s      time the container was last filled from empty (staleness clock); stale after 24 h
  solids        {kind: grams} undissolved solids (tea leaves, pulp, food scraps, grit, carbon fines)
  ice_g         ice cubes floating in the liquid (grams); melts in the containers system
  foam_l        foam head on carbonated drinks; settles over ~40 s

Displacement: solid items resting "in" a basin raise the level by their material volume (mass / density,
Archimedes; floating ice by its own mass). Overflow returns the excess so callers can spill it.

Tier-1 physics (kernel v3.0, TIER1_PHYSICS 5.7 transfer): what is poured in mixes temperatures by heat capacity (the
vessel's body and what it already holds against the incoming liquid, phys.thermal.mix_in's rule: 85 C coffee poured
into a 21 C ceramic mug settles near 72 C), unless a domain owns the receiver's temperature; only the unfrozen part of
a liquid (ice_frac, the kernel's phase law) pours, and taking liquid out of a partly frozen holder keeps its ice.
"""
import copy

from ...kernel.phys import thermal as PT
from . import common as C

MATERIAL_DENSITY = {"ceramic": 2.4, "porcelain": 2.4, "stoneware": 2.3, "glass": 2.5, "crystal": 2.9,
                    "stainless_steel": 7.9, "steel": 7.8, "metal": 7.8, "cast_iron": 7.2, "aluminum": 2.7,
                    "copper": 8.9, "plastic": 1.05, "silicone": 1.2, "wood": 0.7, "bamboo": 0.7, "melamine": 1.5}
SOLID_CONTAINER_MATS = ("plastic", "glass", "ceramic", "porcelain", "stainless_steel", "steel", "metal", "aluminum",
                        "copper", "cast_iron", "silicone", "stoneware", "crystal", "melamine", "enamel", "rubber")
LIQUID_CATEGORIES = ("dish", "cookware", "food_container", "bucket", "liquid_container", "pitcher", "bottle",
                     "watering_can")
# a dispenser holds liquid when it dispenses one (soap, lotion, a drink): a napkin holder is a dispenser too
LIQUID_DISPENSER_KEYS = ("product", "product_tag", "liquid", "foaming", "dispenser_spigot")
SIDE_OPENINGS = ("front", "side", "back", "bottom")


def _opening(rec):
    """Where a container opens: the record's own view block, else its type's (registry metadata, never copied into
    records): "front" for a roll-top bread box; None when neither declares one."""
    own = rec.get("view")
    if isinstance(own, dict) and own.get("opening"):
        return own["opening"]
    name = rec.get("type")
    if not name:
        return None
    from ...kernel.registry import default_registry
    reg = default_registry()
    if not reg.known(name):
        return None
    return reg.view_props(name).get("opening")


def liquid_tight(rec):
    """Whether a container's walls and floor keep liquid in (independent review L3: a bread box, a colander and a napkin
    holder held poured mop water): not perforated (a colander's holes, `perforated`), not porous or soft-sided, and
    opening at the top (what is poured into a roll-top bread box, which opens at the front, runs straight out). An
    explicit `watertight` decides."""
    if "watertight" in rec:
        return bool(rec.get("watertight"))
    if rec.get("perforated") or C.num(rec.get("holes_mm")) > 0:
        return False
    if rec.get("porous") or rec.get("soft_sided"):
        return False
    return _opening(rec) not in SIDE_OPENINGS


def holds_liquid(rec):
    """Whether liquid poured into rec stays in it: a liquid container or basin, or a solid portable vessel of a
    liquid-holding category (a cup, pot, bowl, bucket, pitcher, bottle, food tub) whose walls are liquid-tight."""
    if not rec:
        return False
    if rec.get("watertight") is False or rec.get("perforated"):
        return False
    if rec.get("liquid_container") or rec.get("watertight") or rec.get("basin_l") or rec.get("toilet"):
        return True
    if rec.get("porous") or rec.get("soft_sided") or rec.get("surface") and not rec.get("container"):
        return False
    if rec.get("container") and rec.get("portable") and C.num(rec.get("capacity_l")) > 0:
        if rec.get("material", "plastic") not in SOLID_CONTAINER_MATS:
            return False
        liquid_dispenser = rec.get("category") == "dispenser" and any(rec.get(k) for k in LIQUID_DISPENSER_KEYS)
        if (rec.get("category") in LIQUID_CATEGORIES or liquid_dispenser) and liquid_tight(rec):
            return True
    return False


def is_basin(rec):
    return C.num(rec.get("basin_l")) > 0


# ----------------------------------------------------------------------------------
# which entities the per-substep container processes (foam, fizz, ice, hot hazard, filter pitchers) and the slow
# freshness process look at: a static index of plausible liquid holders plus `plumbing.holders_extra`, ids that
# received liquid at runtime although nothing about their type said they hold liquid (kept by write_state and by the
# census system for liquid that other domains put in such things).

HOLDER_CATEGORIES = LIQUID_CATEGORIES + ("pet_bowl", "drinkware", "cup", "glass", "mug")


def holder_excluded(rec):
    return is_basin(rec) or bool(rec.get("toilet")) or rec.get("category") == "spill" or bool(rec.get("hose"))


def holder_candidate(rec):
    if holder_excluded(rec):
        return False
    return ("liquid_l" in rec or "ice_g" in rec or "reservoir_l" in rec or bool(rec.get("liquid_container"))
            or rec.get("category") in HOLDER_CATEGORIES or holds_liquid(rec))


def holding(rec):
    """Dynamic filter: liquid, ice or a filter-pitcher reservoir inside right now."""
    return C.num(rec.get("liquid_l")) > 0 or C.num(rec.get("ice_g")) > 0 or bool(rec.get("reservoir_l"))


def track_holder(w, eid):
    sec = C.psec(w)
    extra = sec.setdefault("holders_extra", [])
    if eid not in extra:
        extra.append(eid)
        extra.sort()


def holder_ids(w):
    """Sorted ids of every entity that may hold liquid (static candidates + runtime extras)."""
    static = C.index(w, "holders", holder_candidate)
    extra = C.pget(w, "holders_extra") or []
    entities = w.s["entities"]
    if not extra:
        return C.live(w, static)
    return sorted(set(e for e in static if e in entities) | set(e for e in extra if e in entities and not holder_excluded(entities[e])))


def cap_l(rec):
    if is_basin(rec):
        return C.num(rec.get("basin_l"))
    return C.num(rec.get("capacity_l"))


def liters(rec):
    return C.num(rec.get("liquid_l"))


def frozen_frac(rec):
    """The kernel's ice_frac of a holder's liquid (phase law): the part of liquid_l that is ice."""
    return C.clamp(C.num(rec.get("ice_frac")), 0.0, 1.0)


def free_l(rec):
    """Liters that can pour out: the liquid that is not frozen (phys.liquids.free_liquid_l)."""
    return liters(rec) * (1.0 - frozen_frac(rec))


def mixed_temp_c(rec, into_l, temp_in, liquid):
    """Bulk temperature after into_l liters of liquid at temp_in join a holder: heat-capacity weighted (the holder's
    body and contents against the incoming liquid, phys.thermal.mix_in's rule). A holder whose temperature a domain
    owns (thermal_owner, a phase plateau) mixes by liquid volume only (its owner models the rest)."""
    have = liters(rec)
    t_old = C.num(rec.get("temperature_c"), 21.0)
    if into_l <= 0:
        return t_old
    if PT.owned(rec):
        return (t_old * have + temp_in * into_l) / (have + into_l) if have + into_l > 0 else temp_in
    incoming = into_l * C.density_kg_l(liquid) * PT.liquid_cp(liquid or "water")
    mine = PT.heat_capacity_j_k(rec)
    return (mine * t_old + incoming * temp_in) / max(1e-9, mine + incoming)


def fill_frac(rec):
    cap = cap_l(rec)
    return liters(rec) / cap if cap > 0 else 0.0


def mix_of(rec):
    mix = rec.get("_mix")
    if isinstance(mix, dict) and mix:
        return {k: float(v) for k, v in mix.items() if C.num(v) > 0}
    if liters(rec) > 0 and rec.get("liquid"):
        return {rec["liquid"]: liters(rec)}
    return {}


def material_volume_l(rec):
    if rec.get("displace_l") is not None:
        return C.num(rec.get("displace_l"))
    density = MATERIAL_DENSITY.get(rec.get("material", "plastic"), 1.5)
    mass = C.num(rec.get("mass_kg"), 0.3)
    if density < 1.0:
        return mass  # floats: displaces its own mass of water
    return mass / density


def displacement_l(w, eid):
    rec = w.ent(eid)
    total = C.num(rec.get("ice_g")) / 1000.0
    entities = w.s["entities"]
    # w.children's scan (sorted ids located in eid) as one list comprehension: the same children, summed in the same order
    for child in sorted([c for c, r in entities.items() if r.get("location") == eid]):
        crec = entities[child]
        if crec.get("relation") != "in" or crec.get("category") == "spill":
            continue
        total += material_volume_l(crec) + C.num(crec.get("liquid_l")) * (0.0 if crec.get("open", True) else 1.0)
    return total


def derive_name(mix, chem, total):
    """Name of a liquid from its composition (liters) and dissolved products (ml)."""
    if total <= 1e-9 or not mix:
        return None
    frac = {k: v / total for k, v in mix.items()}
    for bad, threshold in (("sewage", 0.005), ("toilet_water", 0.01)):
        if frac.get(bad, 0.0) >= threshold:
            return bad
    surf_ml = sum(C.num(v) for k, v in (chem or {}).items() if C.info(k).get("surfactant") or k in ("dish_soap", "hand_soap", "laundry_detergent", "all_purpose"))
    other_chem = sum(C.num(v) for k, v in (chem or {}).items() if not (C.info(k).get("surfactant") or k in ("dish_soap", "hand_soap", "laundry_detergent")))
    if frac.get("mop_water", 0.0) >= 0.05:
        return "mop_water"
    if frac.get("dirty_water", 0.0) >= 0.05:
        return "mop_water" if other_chem > 0 else "dirty_water"
    if frac.get("condensate", 0.0) >= 0.05:
        return "condensate"
    for name in ("hose_water", "rainwater"):
        if frac.get(name, 0.0) >= 0.05:
            return name
    dominant = max(sorted(frac), key=lambda k: frac[k])
    waterish = sum(frac.get(k, 0.0) for k in C.WATERLIKE)
    products = {k: v for k, v in frac.items() if C.info(k).get("product")}
    if waterish >= 0.7:
        if frac.get("liquid_fertilizer", 0.0) > 0 or frac.get("fertilizer_solution", 0.0) > 0:
            return "fertilizer_solution"
        if frac.get("bleach", 0.0) > 0 or other_chem > 0 and surf_ml <= 0:
            return "mop_water" if other_chem / (total * 1000.0) > 0.002 or frac.get("bleach", 0.0) > 0 else "water"
        surf = surf_ml / (total * 1000.0) + sum(v for k, v in products.items() if C.info(k).get("surfactant"))
        if surf > 0.0002:
            return "soapy_water"
        if frac.get("soapy_water", 0.0) > 0.2:
            return "soapy_water"
        drinks = {k: v for k, v in frac.items() if C.info(k).get("drink") and k not in C.WATERLIKE}
        if drinks and sum(drinks.values()) >= 0.15:
            return max(sorted(drinks), key=lambda k: drinks[k])
        if all(k in ("filtered_water", "distilled_water") for k in frac if frac[k] > 0.001):
            return "filtered_water" if frac.get("filtered_water", 0) >= frac.get("distilled_water", 0) else "distilled_water"
        if frac.get("ice_water", 0.0) > 0.5:
            return "ice_water"
        return "water"
    if frac[dominant] >= 0.85:
        return dominant
    drinks = [k for k in frac if C.info(k).get("drink")]
    if drinks and all(C.info(k).get("drink") for k in frac):
        nonwater = [k for k in frac if k not in C.WATERLIKE]
        if len(nonwater) == 1:
            return nonwater[0]
    return "mixed"


def potable_of(rec, mix=None):
    mix = mix if mix is not None else mix_of(rec)
    if not mix:
        return None
    if rec.get("carbon_fines") or rec.get("_nonpotable"):
        return False
    for name, amount in mix.items():
        if amount <= 1e-6:
            continue
        if not C.info(name).get("potable", False):
            return False
    if any(C.num(v) > 0.5 and k not in ("dish_soap",) for k, v in (rec.get("cl_chem") or {}).items()):
        return False
    return True


def alcoholic(rec):
    mix = mix_of(rec)
    total = sum(mix.values())
    if total <= 0:
        return False
    return sum(v for k, v in mix.items() if C.info(k).get("alcohol")) / total >= 0.005


def _concentrations(rec, total):
    """The derived per-liter values of a holder's dissolved products: soap_ml_per_l (surfactants) and chem_ml_per_l
    ({product: ml/L}); only the keys the record carries or now needs."""
    out = {}
    chem_now = rec.get("cl_chem") or {}
    surf = sum(C.num(v) for k, v in chem_now.items() if C.info(k).get("surfactant") or k in ("dish_soap", "hand_soap", "laundry_detergent"))
    if surf > 0 or "soap_ml_per_l" in rec:
        out["soap_ml_per_l"] = C.rnd(surf / total, 3) if total > 1e-6 else 0.0
    if chem_now or "chem_ml_per_l" in rec:
        out["chem_ml_per_l"] = {k: C.rnd(C.num(v) / total, 3) for k, v in sorted(chem_now.items())} if total > 1e-6 else {}
    return out


def refresh_derived(w, eid):
    """Re-derive the name, potability and concentrations of what eid holds from its current composition and dissolved
    products, amounts unchanged: another domain's pour hook (cleaning's solution model) moved cl_chem after this
    domain's own write. Writes only the values that changed."""
    rec = w.ent(eid)
    total = liters(rec)
    if total <= 1e-6:
        return
    mix = mix_of(rec)
    want = dict(_concentrations(rec, total))
    name = derive_name(mix, rec.get("cl_chem"), sum(mix.values()))
    if name is not None:
        want["liquid"] = name
    pot = potable_of(rec, mix)
    if pot is not None:
        want["potable"] = pot
    changed = {k: v for k, v in want.items() if rec.get(k) != v}
    if changed:
        w.mut(eid).update(changed)


def write_state(w, eid, mix, chem=None, temp=None, extra=None):
    """Store a composition (journaled), deriving liquid name, liters and potability."""
    before = w.ent(eid)
    if not holder_candidate(before) and not holder_excluded(before):
        track_holder(w, eid)
    m = w.mut(eid)
    total = sum(v for v in mix.values() if v > 0)
    clean = {k: C.rnd(v, 5) for k, v in sorted(mix.items()) if v > 1e-6}
    m["_mix"] = clean
    m["liquid_l"] = C.rnd(total, 4)
    if chem is not None:
        m["cl_chem"] = {k: C.rnd(v, 3) for k, v in sorted(chem.items()) if v > 0.001}
        m["_cl_ref_l"] = m["liquid_l"]
    name = derive_name(clean, m.get("cl_chem"), total)
    if name is not None:
        m["liquid"] = name
    if temp is not None:
        m["temperature_c"] = C.rnd(temp, 2)
    if extra:
        m.update(extra)
    pot = potable_of(m, clean)
    if pot is not None:
        m["potable"] = pot
    m.update(_concentrations(m, total))
    if total <= 1e-6:
        m["liquid_l"] = 0.0
        m.pop("foam_l", None)
        if isinstance(m.get("cl_chem"), dict) and m["cl_chem"]:
            m["cl_chem"] = {}
            m["_cl_ref_l"] = 0.0
        if m.get(C.UNIDENTIFIED):
            C.mark_unidentified(w, eid, False)       # emptied: whatever goes in next is seen afresh
        clear_provenance(w, eid)
    elif m.get(C.UNIDENTIFIED):
        C.mark_unidentified(w, eid, True)
    return m


def take(w, eid, amount, carry_chem=True):
    """Remove `amount` liters (proportional composition; at most the unfrozen part, free_l: the ice stays, so the
    holder's ice_frac grows). Returns a parcel dict. carry_chem=False: the dissolved-solution model (cl_chem and the
    dirt in it, cl_soil_g) stays put for its owner's pour hook to move (transfer.solution_carried_by_hook), so the
    parcel carries neither and the source keeps both until that hook rescales them."""
    rec = w.ent(eid)
    have = liters(rec)
    frozen = frozen_frac(rec)
    amount = C.clamp(float(amount), 0.0, have * (1.0 - frozen))
    mix = mix_of(rec)
    chem = dict(rec.get("cl_chem") or {})
    frac = amount / have if have > 0 else 0.0
    parcel = {"liquid": rec.get("liquid"), "liters": amount, "temp_c": C.num(rec.get("temperature_c"), 21.0),
              "mix": {k: v * frac for k, v in mix.items()}, "chem": {k: C.num(v) * frac for k, v in chem.items()} if carry_chem else {},
              "solids": {k: C.num(v) * frac for k, v in (rec.get("solids") or {}).items()},
              "flags": {k: rec.get(k) for k in ("carbon_fines", "_nonpotable", C.UNIDENTIFIED) if rec.get(k)},
              "filled_s": rec.get("filled_s"), "source": eid, "origin": origin_of(w, eid),
              "soil_g": C.num(rec.get("cl_soil_g")) * frac if carry_chem else 0.0,
              "ice_g": C.num(rec.get("ice_g")) * frac}
    if not carry_chem and chem:
        # what the hook will move with it: the parcel is still bleach mop water (its name and the harm it does)
        parcel["named_chem"] = {k: C.num(v) * frac for k, v in chem.items()}
    rest = {k: v - parcel["mix"].get(k, 0.0) for k, v in mix.items()}
    new_chem = {k: C.num(v) - parcel["chem"].get(k, 0.0) for k, v in chem.items()} if carry_chem else None
    extra = {}
    if rec.get("solids"):
        extra["solids"] = {k: C.rnd(C.num(v) - parcel["solids"].get(k, 0.0), 3) for k, v in rec["solids"].items()
                           if C.num(v) - parcel["solids"].get(k, 0.0) > 0.01}
    if rec.get("cl_soil_g") and carry_chem:
        extra["cl_soil_g"] = C.rnd(C.num(rec.get("cl_soil_g")) - parcel["soil_g"], 3)
    if rec.get("ice_g"):
        extra["ice_g"] = C.rnd(C.num(rec.get("ice_g")) - parcel["ice_g"], 2)
    if frozen > 0 and have - amount > 1e-6:
        extra["ice_frac"] = C.rnd(min(1.0, frozen * have / (have - amount)), 4)     # the ice volume stays put
    write_state(w, eid, rest, chem=new_chem, extra=extra)
    if have - amount <= 1e-6:
        residue(w, eid, mix)
    return parcel


def residue(w, eid, mix):
    """A container emptied of a drink or dirty water keeps a film (soil) until washed."""
    rec = w.ent(eid)
    if C.is_mine(rec) and C.kind(rec) in ("sink", "tub", "shower", "toilet"):
        return
    for name, amount in sorted(mix.items()):
        res = C.info(name).get("residue") or {}
        for kind, level in sorted(res.items()):
            if amount > 0.005 and kind not in ("biohazard",):
                C.add_soil(w, eid, kind, 0.12 * level)
            elif kind == "biohazard" and amount > 0.005:
                C.contaminate(w, eid, "biohazard", 0.5)


def split_parcel(parcel, frac):
    """(part, rest): a parcel divided by volume, frac of it in part (composition, dissolved products and the products
    a pour hook moves scaled alike; solids stay with the rest). Pure: the parcel itself is not changed."""
    frac = C.clamp(C.num(frac), 0.0, 1.0)
    out = []
    for share in (frac, 1.0 - frac):
        piece = dict(parcel, liters=C.num(parcel.get("liters")) * share,
                     mix={k: v * share for k, v in (parcel.get("mix") or {}).items()},
                     chem={k: C.num(v) * share for k, v in (parcel.get("chem") or {}).items()})
        if parcel.get("named_chem"):
            piece["named_chem"] = {k: C.num(v) * share for k, v in parcel["named_chem"].items()}
        out.append(piece)
    out[0]["solids"] = {}
    return out[0], out[1]


def make_parcel(liquid, liters_, temp_c=21.0, chem=None, solids=None, flags=None, source=None, fresh=True):
    return {"liquid": liquid, "liters": float(liters_), "temp_c": float(temp_c), "mix": {liquid: float(liters_)},
            "chem": dict(chem or {}), "solids": dict(solids or {}), "flags": dict(flags or {}), "source": source,
            "fresh": fresh, "soil_g": 0.0, "ice_g": 0.0}


def add(w, eid, parcel, now_s=None, limit=None):
    """Pour a parcel into a container. Returns the overflowed parcel (liters beyond the brim) or None.

    limit: liters available before the brim (defaults to capacity minus contents minus displacement)."""
    rec = w.ent(eid)
    amount = float(parcel.get("liters", 0.0))
    if amount <= 0:
        return None
    have = liters(rec)
    room_left = (cap_l(rec) - have - displacement_l(w, eid)) if limit is None else float(limit)
    room_left = max(0.0, room_left)
    into = min(amount, room_left) if cap_l(rec) > 0 else amount
    frac = into / amount if amount > 0 else 0.0
    mix = mix_of(rec)
    was_empty = have <= max(0.002, 0.02 * cap_l(rec))
    for k, v in parcel.get("mix", {}).items():
        mix[k] = mix.get(k, 0.0) + v * frac
    chem = dict(rec.get("cl_chem") or {})
    for k, v in parcel.get("chem", {}).items():
        chem[k] = C.num(chem.get(k)) + C.num(v) * frac
    t_in = C.num(parcel.get("temp_c"), 21.0)
    if into > 0:
        temp = mixed_temp_c(rec, into, t_in, parcel_name(parcel))
    else:
        temp = C.num(rec.get("temperature_c"), 21.0) if have > 0 else t_in
    extra = {}
    if into > 0 and frozen_frac(rec) > 0 and have + into > 1e-9:
        extra["ice_frac"] = C.rnd(frozen_frac(rec) * have / (have + into), 4)          # liquid joins the ice
    if parcel.get("solids"):
        solids = dict(rec.get("solids") or {})
        for k, v in parcel["solids"].items():
            solids[k] = C.rnd(C.num(solids.get(k)) + C.num(v) * frac, 3)
        extra["solids"] = solids
    if parcel.get("soil_g"):
        extra["cl_soil_g"] = C.rnd(C.num(rec.get("cl_soil_g")) + parcel["soil_g"] * frac, 3)
    if parcel.get("ice_g"):
        extra["ice_g"] = C.rnd(C.num(rec.get("ice_g")) + parcel["ice_g"] * frac, 2)
    for flag, value in (parcel.get("flags") or {}).items():
        if value:
            extra[flag] = value
    if into > 0 and parcel.get("origin"):
        mark_provenance(w, eid, parcel["origin"])
    if was_empty:
        extra["filled_s"] = C.now(w) if now_s is None else now_s
        if parcel.get("filled_s") is not None and not parcel.get("fresh", False):
            extra["filled_s"] = parcel["filled_s"]
    write_state(w, eid, mix, chem=chem if (chem or rec.get("cl_chem")) else None, temp=temp, extra=extra)
    if amount - into > 1e-6:
        over = dict(parcel)
        scale = (amount - into) / amount
        over.update(liters=amount - into, mix={k: v * scale for k, v in parcel.get("mix", {}).items()},
                    chem={k: C.num(v) * scale for k, v in parcel.get("chem", {}).items()}, solids={})
        if parcel.get("named_chem"):
            over["named_chem"] = {k: C.num(v) * scale for k, v in parcel["named_chem"].items()}
        return over
    return None


def origin_of(w, eid):
    """Whose identity liquid taken out of eid carries (kernel v2.9 contents_from contract, core.contents_origin):
    eid's own contents_from origin when it holds liquid poured in from elsewhere, else eid. Taken before the take
    empties eid (which clears its provenance), so a chain stays one deep."""
    from ..core import contents_origin
    return contents_origin(w, eid)


def mark_provenance(w, eid, origin):
    """eid received liquid from a container (a parcel with an origin): core.mark_contents_from, so realistic
    perception names what eid holds after the origin's look-alike descriptor until the robot identifies the origin.
    Taps, leaks and melt make parcels without an origin and leave provenance alone."""
    from ..core import mark_contents_from
    mark_contents_from(w, eid, origin)


def clear_provenance(w, eid):
    """The last liquid left eid: drop its contents_from, unless it still holds other contents (a product it
    dispenses, contents another domain tracks) whose provenance that field may describe."""
    rec = w.ent(eid)
    if rec.get("contents") or C.num(rec.get("amount_ml")) > 0:
        return
    from ..core import clear_contents_from
    clear_contents_from(w, eid)


def parcel_name(parcel):
    """What a parcel is: named from its composition and the dissolved products it carries (or that the solution
    model's pour hook moves with it, named_chem)."""
    total = sum(parcel.get("mix", {}).values()) or parcel.get("liters", 0.0)
    chem = parcel.get("chem") or parcel.get("named_chem")
    return derive_name(parcel.get("mix", {}), chem, total) or parcel.get("liquid") or "water"


def is_drinking_water(rec, now_s=None, max_age_s=86400.0):
    """Fresh, potable plain water (the household's drinking standard)."""
    if liters(rec) <= 0:
        return False
    if rec.get("liquid") not in ("water", "filtered_water", "ice_water", "distilled_water"):
        return False
    if not rec.get("potable", True) or rec.get("carbon_fines"):
        return False
    if now_s is not None and rec.get("filled_s") is not None and now_s - C.num(rec.get("filled_s")) > max_age_s:
        return False
    return True


def snapshot(rec):
    return {k: copy.deepcopy(rec.get(k)) for k in ("liquid", "liquid_l", "_mix", "cl_chem", "temperature_c", "potable")}
