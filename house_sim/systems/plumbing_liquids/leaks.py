"""Leaks, drips, condensation, water damage, wet things (fidelity P-approx with hidden ground truth).

Leak source: any entity may carry a hidden `_leak` record:
  {"component": "cold_supply_compression_fitting" | "p_trap_slip_nut" | "supply_line" | "tank_bolt" | "wax_ring" |
                "fill_hose" | "relief_valve" | "ice_maker_line" | "door_seal" | "valve_stem" | "pipe" | ...,
   "rate_lpm": 0.02 (slow drip) .. 0.3 (steady) .. 2-30 (spray / burst),
   "when": "pressurized" (supply side: stops when the fixture stop, heater inlet or main is closed) |
           "draining" (drain side: only while water drains through) | "running" | "flushing" | "always" |
           "pressurized_hose",
   "line": "hot" | "cold", "fixture": the fixture whose valves isolate it (default: the entity),
   "to": {"entity": cabinet id} | {"room": rid, "cell": [c, r]} | {"below": rid, "cell": [c, r], "stain": stain id}}
While a spray-rate leak (2 L/min and more) runs its host shows `leaking: true` (the kernel's glance tier: a burst
sprays where anyone looking sees it; the report vocabulary's leak evidence); a slow leak shows only to a close look
(the inspector) and by the wet things around it. A repaired leak (`fixed`) stops and the flag goes. Water from an active leak lands in a catch container
sitting at the drip point (in the cabinet, or on the floor cell), else on the cabinet floor (moisture, then swelling
particleboard), the room floor (a growing puddle that may sit downhill of the source: slope), or soaks a ceiling below
(a growing stain that starts dripping after ~2 L). Water damage accrues on what gets wet; crossing 0.3 counts one
`damage`. A leak that starts running is a notice (a spray hisses: "water_spray"; a slow leak drips: "drip") and a
spray is a visual occurrence for the geometric camera.
Condensation (`condensation_prone` cold pipes) sweats in humid rooms (>= 55 % RH): wet to the touch, no drip
point, no growing puddle - not a leak.
Drips from wet things (`drip_lpm`, e.g. a watered plant pot, a dripping umbrella) land on whatever the item
stands on: a waterproof catcher (material plastic/rubber/silicone/metal/glass/ceramic or `waterproof`) at least as
large as the item collects them; an absorbent one (towel, cardboard, paper) soaks and wicks through; a small coaster
misses part of it; a food-contact catcher (baking sheet) gets contaminated by soil water.
Cold drinks and wet items left directly on unsealed wood leave a water ring after ~30 min (coaster/tray prevents).
Wet items closed in a cabinet/drawer/closet for an hour start to mildew.
"""
from ...kernel import camera, geometry, physics
from ...kernel.actions import motion_record
from ...kernel.phys import lawbook
from ...kernel.phys import liquids as PL
from ...kernel.phys import props as PP
from ...kernel.plugins import HOOKS, after, hook
from . import common as C
from . import fixtures as F
from . import liquids as L
from . import supply as S

WATERPROOF_MATS = ("plastic", "rubber", "silicone", "metal", "stainless_steel", "aluminum", "glass", "ceramic", "vinyl",
                   "polyethylene", "porcelain", "enamel", "steel")
ABSORBENT_MATS = ("cotton", "fabric", "cardboard", "paper", "terry", "microfiber", "wool", "linen")
DAMAGE_FACTOR = {"particleboard": 0.15, "mdf": 0.15, "wood": 0.05, "drywall": 0.08, "laminate": 0.06, "carpet": 0.05,
                 "hardwood": 0.04, "plywood": 0.06}


def leak_of(rec):
    leak = rec.get("_leak")
    return leak if isinstance(leak, dict) and not leak.get("fixed") else None


def leak_active(w, eid, leak, cache=None):
    when = leak.get("when", "pressurized")
    fixture = leak.get("fixture") or eid
    frec = w.s["entities"].get(fixture) or {}
    if when == "always":
        return 1.0
    if when == "pressurized":
        if frec.get("valve_kind") and fixture == eid:
            return S.main_factor(w) if not frec.get("main") else (1.0 if C.supply_ok(w) else 0.0)
        if frec.get("toilet") and leak.get("component") in ("tank_bolt", "supply_line", "supply_stop_stem", "fill_valve"):
            if leak.get("component") == "supply_stop_stem":
                return S.main_factor(w)
            return S.line_factor(w, fixture, "cold", cache) if leak.get("component") == "supply_line" else \
                (1.0 if C.num(frec.get("tank_level_l"), 6.0) > 0.5 else 0.0)
        line = leak.get("line") or "cold"
        if F.fkind(frec) == "spigot" and leak.get("component") == "frozen_supply_pipe":
            interior = frec.get("interior_valve")
            if interior in w.s["entities"]:
                return min(S.main_factor(w), S.flow_factor(w.ent(interior)))
            return S.main_factor(w)
        return S.line_factor(w, fixture, line, cache)
    if when == "draining":
        return 1.0 if C.now(w) <= C.num(frec.get("_draining_until_s"), -1.0) or (frec.get("water_running") and frec.get("plug", "open") == "open") else 0.0
    if when == "running":
        return 1.0 if frec.get("water_running") else 0.0
    if when == "flushing":
        return 1.0 if C.num(frec.get("tank_level_l"), 6.0) < C.num(frec.get("tank_l"), 6.0) - 0.05 else 0.0
    if when == "pressurized_hose":
        return 1.0 if frec.get("pressurized") or frec.get("water_running") or (frec.get("attached_to") and frec.get("liquid_l", 0) > 0 and
                                                                            C.num((w.s["entities"].get(frec.get("attached_to")) or {}).get("settings", {}).get("valve")) > 0) else 0.0
    if when == "rain":
        weather = w.s.get("weather") or {}
        raining = weather.get("condition") in ("rain", "storm")
        window = leak.get("window")
        if window and window in w.s["entities"]:
            return 1.0 if raining and w.ent(window).get("open") else 0.0
        return 1.0 if raining else 0.0
    return 0.0


def _catcher_at(w, rid, cell, inside=None):
    """A liquid container (open) at the drip point: inside a cabinet, or standing on that floor cell."""
    if inside is not None:
        for child in w.children(inside):
            rec = w.ent(child)
            if rec.get("relation") == "in" and L.holds_liquid(rec) and (rec.get("open", True) or not rec.get("openable")):
                if L.liters(rec) < L.cap_l(rec) - 1e-4:
                    return child
        return None
    for child in w.children(rid):
        rec = w.ent(child)
        # a catch container standing on the floor (a bucket, a pan): never a fixture on that cell - a sink basin or a
        # toilet bowl above a leaking stop does not catch what sprays out below it
        if not rec.get("portable") or L.is_basin(rec) or rec.get("toilet") or rec.get("mounted"):
            continue
        if rec.get("pos") and list(rec.get("pos")) == list(cell) and L.holds_liquid(rec) and not rec.get("category") == "spill":
            if (rec.get("open", True) or not rec.get("openable")) and L.liters(rec) < L.cap_l(rec) - 1e-4:
                return child
    return None


def absorb_cap_l(w, eid):
    """Liters an item soaks up when saturated: the kernel's absorption capacity (phys.liquids.absorb_capacity_l of
    its props: absorption per kg x dry mass; 0 for waterproof things and films)."""
    return PL.absorb_capacity_l(PP.props(w.s, eid))


def _absorber_at(w, rid, cell, inside=None):
    pool = w.children(inside) if inside is not None else [c for c in w.children(rid) if list(w.ent(c).get("pos") or []) == list(cell)]
    for child in pool:
        rec = w.ent(child)
        if rec.get("portable") and C.num(rec.get("wetness")) < 0.95 and absorb_cap_l(w, child) > 0:
            return child
    return None


def accrue_damage(w, eid, liters_, material=None, cause=None):
    """Water damage on what water reaches (factor per liter by material); crossing 0.3 swells it and counts one
    damage: the robot's when it set up the water's cause (cause: the leak host or dripping item, common.harm_by)."""
    rec = w.ent(eid)
    mat = material or rec.get("base_material") or rec.get("material", "wood")
    factor = DAMAGE_FACTOR.get(mat, 0.03)
    before = C.num(rec.get("water_damage"))
    after_ = C.clamp(before + liters_ * factor, 0.0, 1.0)
    m = w.mut(eid)
    m["water_damage"] = C.rnd(after_, 4)
    if before < 0.3 <= after_:
        C.harm(w, "damage", cause or eid)
        C.plog(w, "water_damage", target=eid, level=C.rnd(after_, 3))
        m["swollen"] = mat in ("particleboard", "mdf", "laminate")


def deliver_leak_water(w, eid, leak, liters_):
    to = leak.get("to") or {}
    if "entity" in to and to["entity"] in w.s["entities"]:
        cab = to["entity"]
        catcher = _catcher_at(w, None, None, inside=cab)
        if catcher:
            over = L.add(w, catcher, L.make_parcel("water", liters_, source=eid))
            if not over:
                return "caught"
            liters_ = over["liters"]
        absorber = _absorber_at(w, None, None, inside=cab)
        if absorber:
            # a towel or rag stored under the sink soaks it up to saturation (the kernel's wet: wetness and moisture
            # rise by liters / absorb_l, and phys.wetted hooks see it); the rest reaches the cabinet floor
            liters_ -= PL.wet(w, absorber, liters_, "water", source=eid)
            if liters_ <= 1e-6:
                return "absorbed"
        m = w.mut(cab)
        m["cabinet_water_l"] = C.rnd(C.num(m.get("cabinet_water_l")) + liters_, 4)
        m["wetness"] = max(C.num(m.get("wetness")), 0.8)
        m["moisture"] = "wet"
        accrue_damage(w, cab, liters_, material=w.ent(cab).get("base_material", "particleboard"), cause=eid)
        if C.num(m["cabinet_water_l"]) > 0.8:
            out = C.num(m["cabinet_water_l"]) - 0.8
            m["cabinet_water_l"] = 0.8
            C.add_floor_water(w, C.room_of(w, cab), C.cell_of(w, cab), "water", out, source=eid)
        return "cabinet"
    if "below" in to:
        rid = to["below"]
        stain = to.get("stain")
        if stain and stain in w.s["entities"]:
            m = w.mut(stain)
            m["ceiling_water_l"] = C.rnd(C.num(m.get("ceiling_water_l")) + liters_, 4)
            m["stain_m2"] = C.rnd(min(4.0, 0.05 + C.num(m["ceiling_water_l"]) * 0.12), 3)
            m["wet"] = True
            m["growing"] = True
            m["moisture"] = "wet"
            accrue_damage(w, stain, liters_, material="drywall", cause=eid)
            if C.num(m["ceiling_water_l"]) < 2.0:
                return "ceiling"
            if not m.get("dripping"):
                m["dripping"] = True
                camera.visual_event(w, "plumbing_ceiling_drip", source=stain, size_m=0.05, emit=False)
                C.notice(w, "drip", source=stain, room=rid)
        cell = to.get("cell") or [0, 0]
        catcher = _catcher_at(w, rid, cell)
        if catcher:
            over = L.add(w, catcher, L.make_parcel("water", liters_, source=eid))
            if not over:
                return "caught"
            liters_ = over["liters"]
        C.add_floor_water(w, rid, cell, "water", liters_, source=eid)
        return "floor_below"
    rid = to.get("room") or C.room_of(w, eid)
    cell = to.get("cell") or C.cell_of(w, eid)
    if rid not in w.s["rooms"]:
        return None
    catcher = _catcher_at(w, rid, cell)
    if catcher:
        over = L.add(w, catcher, L.make_parcel("water", liters_, source=eid))
        if not over:
            return "caught"
        liters_ = over["liters"]
    if C.is_outdoor(w, rid):
        C.outdoor_water(w, rid, cell, "water", liters_, source=eid)
        return "ground"
    C.add_floor_water(w, rid, cell, "water", liters_, source=eid)
    floor = C.floor_entity(w, rid)
    if floor:
        accrue_damage(w, floor, liters_ * 0.2, material=C.floor_material(w, rid), cause=eid)
    return "floor"


def _leak_candidate(rec):
    # `_leak` comes from setup/scenarios (any entity) or is added at runtime by this domain's own mechanics, always
    # on its own entities (snapped stems, split hoses, burst spigots, strained appliance hoses)
    return "_leak" in rec or C.is_mine(rec)


def leak_ids(w):
    return C.index(w, "leak_candidates", _leak_candidate)


SPRAY_LPM = 2.0      # from about 2 L/min a leak sprays and hisses (a burst pipe, a snapped stem, a split hose)


def leak_started(w, eid, leak):
    """A leak starts running (pressure came on, a drain started flowing, a pipe burst): a spray is a visual
    occurrence at the leak and a hiss (notice "water_spray", about 65 dB); a slow leak only drips ("drip", 45 dB),
    too small for the camera to catch it happening."""
    if C.num(leak.get("rate_lpm"), 0.05) >= SPRAY_LPM:
        camera.visual_event(w, "plumbing_leak_spray", source=eid, size_m=0.5, emit=False)
        C.notice(w, "water_spray", source=eid)
    else:
        C.notice(w, "drip", source=eid)


def leaks_idle(w, cache):
    """True when leak_step would change nothing: every leak record inactive (or repaired) and marked so. Pure."""
    entities = w.s["entities"]
    for eid in leak_ids(w):
        rec = entities.get(eid)
        if rec is None or not isinstance(rec.get("_leak"), dict):
            continue
        if rec.get("_leak_active") or rec.get("leaking"):
            return False
        leak = leak_of(rec)
        if leak is not None and leak_active(w, eid, leak, cache) > 0:
            return False
    return True


def drips_idle(w):
    """True when drip_step would change nothing: no wet thing dripping (drip_lpm > 0). Pure."""
    entities = w.s["entities"]
    return not any(C.num((entities.get(eid) or {}).get("drip_lpm")) > 0 for eid in C.index(w, "drippers", _dripper))


def leak_step(w, dt, cache):
    """Advance every active leak. Returns total pressurized leak flow (L/min) for the meter."""
    meter = 0.0
    entities = w.s["entities"]
    for eid in leak_ids(w):
        rec = entities.get(eid)
        if rec is None or not isinstance(rec.get("_leak"), dict):
            continue
        leak = leak_of(rec)
        if not leak:
            if rec.get("_leak_active") or rec.get("leaking"):
                m = w.mut(eid)               # repaired: it no longer leaks
                m["_leak_active"] = False
                m.pop("leaking", None)
            continue
        factor = leak_active(w, eid, leak, cache)
        active = factor > 0
        spraying = active and C.num(leak.get("rate_lpm"), 0.05) >= SPRAY_LPM
        if bool(rec.get("_leak_active")) != active or bool(rec.get("leaking")) != spraying:
            started = active and not rec.get("_leak_active")
            m = w.mut(eid)
            m["_leak_active"] = active
            if spraying:
                m["leaking"] = True          # a spray shows at a glance (kernel tier); a slow drip only to a close look
            else:
                m.pop("leaking", None)
            if started:
                leak_started(w, eid, leak)
        if not active:
            continue
        lpm = C.num(leak.get("rate_lpm"), 0.05) * factor
        liters_ = lpm * dt / 60.0
        if leak.get("when") in ("pressurized", "pressurized_hose"):
            meter += lpm
            C.use_water(w, liters_)
        where = deliver_leak_water(w, eid, leak, liters_)
        m = w.mut(eid)
        m["_leaked_l"] = C.rnd(C.num(rec.get("_leaked_l")) + liters_, 4)
        if where and leak.get("_where") != where:
            m["_leak"]["_where"] = where
    return meter


# ----------------------------------------------------------------------------------
# condensation, drips, rings, closed wet storage, fallback drying

def _condensation_prone(rec):
    return bool(rec.get("condensation_prone"))


def condensation_step(w):
    for eid in C.live(w, C.index(w, "condensation", _condensation_prone)):
        rec = w.ent(eid)
        rid = C.room_of(w, eid)
        humid = C.num((w.s["rooms"].get(rid) or {}).get("humidity"), 0.45)
        sweating = humid >= 0.55
        if sweating:
            # this domain owns the film's wetness while it sweats (CONVENTIONS 5 drying_owner / _wet_mine): other
            # domains' drying would otherwise evaporate a film that the cold pipe keeps re-forming
            if not rec.get("sweating") or C.num(rec.get("wetness")) < 0.4 or rec.get("drying_owner") != "plumbing_liquids":
                m = w.mut(eid)
                m["sweating"] = True
                m["wetness"] = max(0.4, C.num(rec.get("wetness")))
                m["drying_owner"] = "plumbing_liquids"
                m["_wet_mine"] = 0.4
        elif rec.get("sweating"):
            m = w.mut(eid)
            m["sweating"] = False
            m["wetness"] = 0.0
            if m.get("drying_owner") == "plumbing_liquids":
                m.pop("drying_owner", None)
                m.pop("_wet_mine", None)


def _waterproof(rec):
    return bool(rec.get("waterproof")) or (rec.get("material") in WATERPROOF_MATS and not rec.get("porous"))


def _water_sensitive(rec):
    """Gear and water-sensitive things are no drip catchers whatever their case is made of: water standing on a plastic
    television or radio gets in through its vents."""
    return bool(rec.get("electronic")) or rec.get("water_safe") is False


def _footprint_area(rec):
    size = rec.get("size_m") or [0.1, 0.1, 0.1]
    return float(size[0]) * float(size[1])


def _dripper(rec):
    # wet things that drip (watered pots, umbrellas, garments): drip_lpm is set up by scenarios on other domains' items
    return "drip_lpm" in rec and not C.is_mine(rec)


def drip_step(w, dt):
    entities = w.s["entities"]
    for eid in C.index(w, "drippers", _dripper):
        rec = entities.get(eid)
        if rec is None or C.num(rec.get("drip_lpm")) <= 0:
            continue
        lpm = C.num(rec.get("drip_lpm"))
        liters_ = lpm * dt / 60.0
        left = rec.get("drip_left_l")
        if left is not None:
            liters_ = min(liters_, C.num(left))
            m = w.mut(eid)
            m["drip_left_l"] = C.rnd(C.num(left) - liters_, 4)
            if m["drip_left_l"] <= 1e-6:
                m["drip_lpm"] = 0.0
        if liters_ <= 0:
            continue
        drip_onto(w, eid, liters_, rec.get("drip_liquid") or "water")


def drip_onto(w, eid, liters_, liquid, depth=0, cause=None):
    """Drips from eid land on what it stands on (a catcher, an absorbent, a surface, the floor), passing on what that
    does not keep. cause: the item that drips (a harm is the robot's when the robot set it there, common.harm_by)."""
    rec = w.ent(eid)
    support = rec.get("location")
    cause = cause or eid
    if depth > 4 or support is None:
        return
    if support in w.s["rooms"]:
        C.add_floor_water(w, support, rec.get("pos") or C.cell_of(w, eid), liquid, liters_, source=eid, cause=cause)
        floor = C.floor_entity(w, support)
        if floor and C.floor_material(w, support) in ("hardwood", "wood", "laminate", "carpet"):
            accrue_damage(w, floor, liters_ * 0.5, material=C.floor_material(w, support), cause=cause)
        return
    if support not in w.s["entities"] or support == "robot":
        return
    srec = w.ent(support)
    sec = C.psec(w)
    caught = sec.setdefault("drips_caught", {})
    if _waterproof(srec) and not _water_sensitive(srec):
        miss = 0.0
        if _footprint_area(srec) < 0.9 * _footprint_area(rec):
            miss = 1.0 - _footprint_area(srec) / max(1e-6, _footprint_area(rec))
        kept = liters_ * (1.0 - miss)
        if srec.get("food_safe") and liquid != "water" or srec.get("food_safe") and rec.get("soil_water"):
            C.contaminate(w, support, "soil", 0.3)
            C.contaminate(w, support, "bacteria", 0.2)
            sec["food_contact_contaminated"] = True
        elif srec.get("food_safe") and rec.get("category") in ("plant", "pot", "planter", "houseplant") or srec.get("food_safe") and rec.get("medium"):
            C.contaminate(w, support, "soil", 0.3)
            sec["food_contact_contaminated"] = True
        if L.holds_liquid(srec):
            over = L.add(w, support, L.make_parcel(liquid, kept, source=eid))
            if over:
                drip_onto(w, support, over["liters"], liquid, depth + 1, cause)
        else:
            m = w.mut(support)
            m["pooled_l"] = C.rnd(C.num(m.get("pooled_l")) + kept, 4)
            capacity = _footprint_area(srec) * 2.0
            if C.num(m["pooled_l"]) > capacity:
                extra = C.num(m["pooled_l"]) - capacity
                m["pooled_l"] = C.rnd(capacity, 4)
                drip_onto(w, support, extra, liquid, depth + 1, cause)
        caught[support] = C.rnd(C.num(caught.get(support)) + kept, 4)
        if miss > 0:
            _onto_below(w, support, liters_ * miss, liquid, depth, cause)
        return
    if absorb_cap_l(w, support) > 0 or srec.get("material") in ABSORBENT_MATS or srec.get("absorbent"):
        # an absorbent catcher (towel, cardboard, paper) soaks the drips up to saturation (the kernel's wet, by its
        # absorption capacity), then they wick through to what is below
        kept = PL.wet(w, support, liters_, liquid, source=eid) if absorb_cap_l(w, support) > 0 else 0.0
        if kept <= 1e-9 and absorb_cap_l(w, support) <= 0:
            before = C.num(srec.get("wetness"))
            kept = min(liters_, max(0.0, 1.0 - before) * 0.3)          # no kernel capacity: a 0.3 L soak
            w.mut(support)["wetness"] = C.rnd(min(1.0, before + kept / 0.3), 3)
        if srec.get("material") in ("cardboard", "paper"):
            w.mut(support)["soggy"] = True
        if liters_ - kept > 1e-9:
            drip_onto(w, support, liters_ - kept, liquid, depth + 1, cause)
        return
    # not waterproof and not absorbent: the water sits on it (wood rings, electronics). Its film is the kernel's wetting
    # (phys.wetted hooks see every drip: electrical gear takes the ingress and its attribution); what stands on it is
    # this domain's pooled_l, and water-sensitive gear is water damaged once common.WATER_DAMAGE_L has gathered there
    C.wet(w, support, liters_, liquid, source=cause)
    if support not in w.s["entities"]:
        return
    srec = w.ent(support)
    if srec.get("material") in ("wood", "hardwood", "mdf", "particleboard") and not srec.get("sealed"):
        accrue_damage(w, support, liters_, material=srec.get("material"), cause=cause)
        m = w.mut(support)
        m["_water_ring"] = C.rnd(C.num(m.get("_water_ring")) + liters_ * 10.0, 3)
    if srec.get("electronic") or srec.get("water_safe") is False:
        if not srec.get("water_damaged") and C.num(srec.get("pooled_l")) + liters_ >= C.WATER_DAMAGE_L:
            w.mut(support)["water_damaged"] = True
            C.harm(w, "damage", cause)
    if C.num(w.ent(support).get("pooled_l")) > 0.05:
        drip_onto(w, support, liters_, liquid, depth + 1, cause)
    else:
        w.mut(support)["pooled_l"] = C.rnd(C.num(w.ent(support).get("pooled_l")) + liters_, 4)


def _onto_below(w, support, liters_, liquid, depth, cause=None):
    srec = w.ent(support)
    parent = srec.get("location")
    if parent in w.s["rooms"]:
        C.add_floor_water(w, parent, srec.get("pos") or C.cell_of(w, support), liquid, liters_, source=support, cause=cause)
    elif parent in w.s["entities"]:
        drip_onto(w, support, liters_, liquid, depth + 1, cause)


RING_SURFACES = ("wood", "hardwood", "walnut", "oak", "pine")


def _ring_or_mildew_candidates(w):
    """Wet items (>= 0.3) and liquid holders: one cheap pass every slow period (wetness is dynamic on every entity)."""
    out = []
    for eid, rec in w.s["entities"].items():
        wet = rec.get("wetness")
        liq = rec.get("liquid_l")
        if (wet and C.num(wet) >= 0.3) or (liq and C.num(liq) > 0.02):
            out.append(eid)
    out.sort()
    return out


def ring_and_mildew_step(w, dt):
    now = C.now(w)
    for eid in _ring_or_mildew_candidates(w):
        rec = w.s["entities"][eid]
        loc = rec.get("location")
        parent = w.s["entities"].get(loc) if isinstance(loc, str) else None
        if parent is None:
            continue
        cold_drink = L.liters(rec) > 0.02 and C.num(rec.get("temperature_c"), 21.0) < 15.0 and rec.get("relation") == "on"
        wet_item = C.num(rec.get("wetness")) > 0.5 and rec.get("relation") == "on"
        if (cold_drink or wet_item) and parent.get("material") in RING_SURFACES and not parent.get("sealed") \
                and not parent.get("coaster") and parent.get("surface", True):
            m = w.mut(loc)
            ring = C.num(parent.get("_water_ring")) + dt / 1800.0
            m["_water_ring"] = C.rnd(ring, 4)
            if ring >= 1.0 and not parent.get("water_ring"):
                m["water_ring"] = True
                C.damage(w, loc, "water ring on the wood", kind="water_ring", severity=0.3, cause=eid)
        if C.num(rec.get("wetness")) >= 0.3 and rec.get("relation") == "in" and parent.get("openable") and not parent.get("open") \
                and parent.get("category") not in ("appliance", "washer", "dryer", "dishwasher") and not parent.get("appliance"):
            m = w.mut(eid)
            m["_closed_wet_s"] = C.rnd(C.num(rec.get("_closed_wet_s")) + dt, 1)
            if C.num(m["_closed_wet_s"]) >= 3600.0 and not rec.get("_mildewed"):
                m["_mildewed"] = True
                C.add_soil(w, eid, "mildew", 0.25)
                C.add_soil(w, loc, "mildew", 0.1)
                C.plog(w, "mildew", item=eid, storage=loc)


def _enclosed(w, eid):
    location, seen = w.ent(eid).get("location"), set()
    while location in w.s["entities"] and location not in seen:
        seen.add(location)
        parent = w.ent(location)
        if parent.get("openable") and not parent.get("open"):
            return True
        location = parent.get("location")
    return False


def evaporation_mm_h(room, outdoor_weather=None):
    temp = C.num(room.get("temperature_c"), 21.0)
    rh = C.num(room.get("humidity"), 0.45)
    if outdoor_weather is not None:
        temp = C.num(outdoor_weather.get("temperature_c"), 15.0)
        rh = 0.6
    base = 0.08 * max(0.2, 1.0 + (temp - 21.0) * 0.05) * max(0.05, 1.0 - rh)
    return base


def fallback_drying(w, dt):
    """Only when the cleaning domain is not loaded: evaporate water spills/wet cells and dry wet items."""
    if not C.system_present("cleaning.spills"):
        for eid in sorted(e for e, r in w.s["entities"].items() if r.get("category") == "spill"):
            rec = w.ent(eid)
            if rec.get("liquid") not in ("water", "hose_water", "filtered_water", "rainwater", "condensate", "soapy_water"):
                continue
            rid = C.room_of(w, eid)
            room = w.s["rooms"].get(rid) or {}
            mm_h = evaporation_mm_h(room, w.s.get("weather") if room.get("outdoor") else None)
            lost = mm_h * C.num(rec.get("area_m2"), 0.1) * dt / 3600.0
            left = C.num(rec.get("liquid_l")) - lost
            if left <= 0.002:
                if rec.get("pos") and rid in w.s["rooms"]:
                    rm = w.room_m(rid)
                    rm["wet_cells"] = [c for c in rm.get("wet_cells", []) if list(c) != list(rec["pos"])]
                w.destroy(eid)
            else:
                w.mut(eid)["liquid_l"] = C.rnd(left, 5)
    if not C.system_present("cleaning.drying"):
        for eid in sorted(e for e, r in w.s["entities"].items() if C.num(r.get("wetness")) > 0.0005 and r.get("category") not in ("spill", "floor")):
            rec = w.ent(eid)
            if C.is_mine(rec) and (L.liters(rec) > 0 or rec.get("water_running")):
                continue
            if rec.get("sweating") or rec.get("_wet_mine") is not None or rec.get("drying_owner") not in (None, "plumbing_liquids"):
                continue    # single-sourced drying (CONVENTIONS 5): another domain evolves this entity's wetness
            rid = C.room_of(w, eid)
            room = w.s["rooms"].get(rid) or {}
            rate = 1.0 / 5400.0 * max(0.1, 1.0 - C.num(room.get("humidity"), 0.45)) / 0.55
            if rec.get("material") in ABSORBENT_MATS or rec.get("category") in ("towel", "cloth", "garment"):
                rate *= 0.5
            if _enclosed(w, eid):
                rate *= 0.08
            m = w.mut(eid)
            m["wetness"] = C.rnd(max(0.0, C.num(rec.get("wetness")) - rate * dt), 4)
            inner = C.num(rec.get("moisture_inner"))
            if inner > 0:
                m["moisture_inner"] = C.rnd(max(0.0, inner - rate * dt / 4.0), 4)


# ----------------------------------------------------------------------------------
# carrying liquids. The kernel owns carry slosh (law "carry", TIER1_PHYSICS 5.8): once the law is active its
# action-phase settle sloshes what the robot carries and this domain's hook below does nothing. Until then (the law is
# deferred) the hook applies the same model on the same motion record, and only when the manipulation (or climate)
# carry model did not already run for the trip (their ctx.data["carry_slosh_applied"]; the flip retires the flag).

TRAY_CELL_D_M = 0.035        # an ice-tray cell: 3.5 cm across, 2.5 cm deep; each cell sloshes on its own
TRAY_CELL_H_M = 0.025


def slosh_capacity(rec):
    return C.num(rec.get("cells"), 14) * C.num(rec.get("cell_ml"), 28.0) / 1000.0 if rec.get("ice_tray") else L.cap_l(rec)


def slosh_spill_l(rec, motion):
    """Liters an open container loses to slosh at a motion factor: the kernel's model (phys.liquids.slosh_spill_l:
    amplitude 0.010 x motion x sqrt(D / 0.08) m above the headroom) on this domain's capacity (a basin's basin_l). An
    ice tray sloshes cell by cell (a 3.5 cm cell under 2.5 cm walls), so the model runs on one cell and counts all."""
    have = L.liters(rec)
    cap = slosh_capacity(rec)
    if have <= 0 or cap <= 0:
        return 0.0
    if rec.get("lid") and rec.get("open") is False:
        return 0.0
    if rec.get("ice_tray"):
        cells = max(1, int(C.num(rec.get("cells"), 14)))
        cell = {"liquid": rec.get("liquid"), "liquid_l": have / cells, "capacity_l": cap / cells,
                "size_m": [TRAY_CELL_D_M, TRAY_CELL_D_M, TRAY_CELL_H_M / 0.9], "ice_frac": rec.get("ice_frac"),
                "open": rec.get("open", True), "openable": rec.get("openable")}
        return min(have, round(PL.slosh_spill_l(cell, motion) * cells, 4))
    view = dict(rec)
    view["capacity_l"] = cap
    return PL.slosh_spill_l(view, motion)


def trip_motion(w, record):
    """Gait motion factor of a trip (phys.liquids.carry_motion: the effective speed's factor times the
    phys.carry_motion hooks). Until a domain registers that hook for stairs (navigation_robot's adoption), a flight
    of stairs counts x1.8 here, as it will there."""
    factor = PL.carry_motion(w, record)
    if not HOOKS.get("phys.carry_motion"):
        conn = w.s.get("connections", {}).get((record or {}).get("connection") or "", {})
        if conn.get("kind") == "stairs" or (record or {}).get("stairs_up") or (record or {}).get("stairs_down"):
            factor *= 1.8
    return factor


def _carry_hook(name):
    def hook_fn(ctx, result):
        if lawbook.active("carry"):
            return          # kernel owns it: the carry law in the action-phase settle
        if ctx.data.get("carry_slosh_applied"):
            return          # manipulation's (or climate's) model already sloshed this trip
        w = ctx.w
        held = w.held_items()
        if not held:
            return
        record = motion_record(ctx)
        if not record or not (record.get("path") or record.get("from_room") != record.get("to_room")):
            return
        motion = trip_motion(w, record)
        for eid in held:
            rec = w.ent(eid)
            amount = slosh_spill_l(rec, motion)
            if amount > 0:
                parcel = L.take(w, eid, amount)
                rid = w.robot["room"]
                if amount >= 0.005:      # a few droplets just evaporate; more is a spill on the floor
                    C.add_floor_water(w, rid, list(w.robot.get("pos") or [0, 0]), L.parcel_name(parcel), parcel["liters"], source=eid)
                C.plog(w, "carry_slosh", item=eid, liters=C.rnd(amount, 4))
                if rec.get("ice_tray"):
                    w.mut(eid)["cell_fill"] = C.rnd(L.liters(w.ent(eid)) / max(1e-6, slosh_capacity(rec)), 3)
    hook_fn.__name__ = "carry_slosh_" + name
    after(name, order=170)(hook_fn)
    return hook_fn


for _name in ("move", "approach", "walk_to"):
    _carry_hook(_name)


@hook("phys.settle_extra")
def tray_follows_its_water(w, eid, ctx):
    """An ice tray's cell fill follows its water when a kernel law moved it (the carry law sloshed it, the open_spill
    law ran it out of a tilted tray): cell_fill stays liquid / cell capacity, and water that ran out of a tray resting
    in a freezer is recorded where it froze (frozen_spill_l on the freezer or the shelf item it stands on), as the
    domain's own tilt rule does. Runs on every node of every settle pass: returns at once for anything but a tray."""
    rec = w.s["entities"].get(eid)
    if rec is None or not rec.get("ice_tray") or C.num(rec.get("frozen_frac")) >= 1.0:
        return
    cap = slosh_capacity(rec)
    if cap <= 0:
        return
    have = L.liters(rec)
    before = C.num(rec.get("cell_fill")) * cap
    if abs(have / cap - C.num(rec.get("cell_fill"))) < 5e-4:
        return
    m = w.mut(eid)
    m["cell_fill"] = C.rnd(have / cap, 3)
    lost = before - have
    loc = rec.get("location")
    parent = w.s["entities"].get(loc) if isinstance(loc, str) else None
    if lost > 1e-4 and parent is not None and loc != "robot":
        holder = parent.get("location") if parent.get("portable") and parent.get("location") in w.s["entities"] else loc
        if physics.ambient(w.s, eid)[0] <= 0.0:
            hm = w.mut(holder)
            hm["frozen_spill_l"] = C.rnd(C.num(hm.get("frozen_spill_l")) + lost, 4)


# ----------------------------------------------------------------------------------
# appliance supply hoses (T-0c0c "check appliance hoses visually")

def appliance_hoses(w, appliance):
    return [c for c in w.children(appliance) if (w.ent(c).get("plumb") or {}).get("kind") == "appliance_hose"]


def strain_hoses(w, appliance, why):
    for hose in appliance_hoses(w, appliance):
        rec = w.ent(hose)
        if rec.get("strained") or C.num(rec.get("slack_m"), 0.4) >= 0.5:
            continue
        defect = rec.get("_defect") or "none"
        m = w.mut(hose)
        m["strained"] = True
        rate = 2.0 if defect in ("crack", "bulge") else 0.15
        m["_leak"] = C.robot_leak(w, {"component": "hose_fitting" if defect == "none" else "hose_" + defect, "rate_lpm": rate,
                                      "when": "pressurized", "line": rec.get("line", "cold"), "fixture": appliance,
                                      "to": {"room": C.room_of(w, appliance), "cell": C.cell_of(w, appliance)}, "started_s": C.now(w)})
        C.plog(w, "hose_strained", hose=hose, appliance=appliance, why=why)


def _appliance_moved(verb):
    def hook_fn(ctx, result):
        w = ctx.w
        eid = ctx.args.get("target")
        if eid in w.s["entities"] and appliance_hoses(w, eid):
            strain_hoses(w, eid, verb)
    hook_fn.__name__ = "appliance_moved_" + verb
    after(verb, order=160)(hook_fn)


for _verb in ("pull", "drag", "slide", "push", "pull_handle"):
    _appliance_moved(_verb)
