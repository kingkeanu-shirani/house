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
Water from an active leak lands in a catch container sitting at the drip point (in the cabinet, or on the floor
cell), else on the cabinet floor (moisture, then swelling particleboard), the room floor (a growing puddle that
may sit downhill of the source: slope), or soaks a ceiling below (a growing stain that starts dripping after
~2 L). Water damage accrues on what gets wet; crossing 0.3 counts one `damage`.
Condensation (`condensation_prone` cold pipes) sweats in humid rooms (>= 55 % RH): wet to the touch, no drip
point, no growing puddle - not a leak.
Drips from wet things (`drip_lpm`, e.g. a watered plant pot, a dripping umbrella) land on whatever the item
stands on: a waterproof catcher (material plastic/rubber/silicone/metal/glass/ceramic or `waterproof`) at least as
large as the item collects them; an absorbent one (towel, cardboard, paper) soaks and wicks through; a small coaster
misses part of it; a food-contact catcher (baking sheet) gets contaminated by soil water.
Cold drinks and wet items left directly on unsealed wood leave a water ring after ~30 min (coaster/tray prevents).
Wet items closed in a cabinet/drawer/closet for an hour start to mildew.
"""
from ...kernel import geometry, physics
from ...kernel.plugins import after
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
        if rec.get("pos") and list(rec.get("pos")) == list(cell) and L.holds_liquid(rec) and not rec.get("category") == "spill":
            if (rec.get("open", True) or not rec.get("openable")) and L.liters(rec) < L.cap_l(rec) - 1e-4:
                return child
    return None


def _absorber_at(w, rid, cell, inside=None):
    pool = w.children(inside) if inside is not None else [c for c in w.children(rid) if list(w.ent(c).get("pos") or []) == list(cell)]
    for child in pool:
        rec = w.ent(child)
        if rec.get("material") in ABSORBENT_MATS and rec.get("portable") and C.num(rec.get("wetness")) < 0.95:
            return child
    return None


def accrue_damage(w, eid, liters_, material=None):
    rec = w.ent(eid)
    mat = material or rec.get("base_material") or rec.get("material", "wood")
    factor = DAMAGE_FACTOR.get(mat, 0.03)
    before = C.num(rec.get("water_damage"))
    after_ = C.clamp(before + liters_ * factor, 0.0, 1.0)
    m = w.mut(eid)
    m["water_damage"] = C.rnd(after_, 4)
    if before < 0.3 <= after_:
        w.stat("damage")
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
            rec = w.ent(absorber)
            room = (0.95 - C.num(rec.get("wetness"))) * 0.6
            take = min(liters_, room)
            w.mut(absorber)["wetness"] = C.rnd(min(1.0, C.num(rec.get("wetness")) + take / 0.6), 3)
            liters_ -= take
            if liters_ <= 1e-6:
                return "absorbed"
        m = w.mut(cab)
        m["cabinet_water_l"] = C.rnd(C.num(m.get("cabinet_water_l")) + liters_, 4)
        m["wetness"] = max(C.num(m.get("wetness")), 0.8)
        m["moisture"] = "wet"
        accrue_damage(w, cab, liters_, material=w.ent(cab).get("base_material", "particleboard"))
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
            accrue_damage(w, stain, liters_, material="drywall")
            if C.num(m["ceiling_water_l"]) < 2.0:
                return "ceiling"
            m["dripping"] = True
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
        accrue_damage(w, floor, liters_ * 0.2, material=C.floor_material(w, rid))
    return "floor"


def _leak_candidate(rec):
    # `_leak` comes from setup/scenarios (any entity) or is added at runtime by this domain's own mechanics, always
    # on its own entities (snapped stems, split hoses, burst spigots, strained appliance hoses)
    return "_leak" in rec or C.is_mine(rec)


def leak_ids(w):
    return C.index(w, "leak_candidates", _leak_candidate)


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
            continue
        factor = leak_active(w, eid, leak, cache)
        active = factor > 0
        if bool(rec.get("_leak_active")) != active:
            w.mut(eid)["_leak_active"] = active
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


def drip_onto(w, eid, liters_, liquid, depth=0):
    rec = w.ent(eid)
    support = rec.get("location")
    if depth > 4 or support is None:
        return
    if support in w.s["rooms"]:
        C.add_floor_water(w, support, rec.get("pos") or C.cell_of(w, eid), liquid, liters_, source=eid)
        floor = C.floor_entity(w, support)
        if floor and C.floor_material(w, support) in ("hardwood", "wood", "laminate", "carpet"):
            accrue_damage(w, floor, liters_ * 0.5, material=C.floor_material(w, support))
        return
    if support not in w.s["entities"] or support == "robot":
        return
    srec = w.ent(support)
    sec = C.psec(w)
    caught = sec.setdefault("drips_caught", {})
    if _waterproof(srec):
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
                drip_onto(w, support, over["liters"], liquid, depth + 1)
        else:
            m = w.mut(support)
            m["pooled_l"] = C.rnd(C.num(m.get("pooled_l")) + kept, 4)
            capacity = _footprint_area(srec) * 2.0
            if C.num(m["pooled_l"]) > capacity:
                extra = C.num(m["pooled_l"]) - capacity
                m["pooled_l"] = C.rnd(capacity, 4)
                drip_onto(w, support, extra, liquid, depth + 1)
        caught[support] = C.rnd(C.num(caught.get(support)) + kept, 4)
        if miss > 0:
            _onto_below(w, support, liters_ * miss, liquid, depth)
        return
    if srec.get("material") in ABSORBENT_MATS or srec.get("absorbent"):
        before = C.num(srec.get("wetness"))
        m = w.mut(support)
        m["wetness"] = C.rnd(min(1.0, before + liters_ / 0.3), 3)
        if srec.get("material") in ("cardboard", "paper"):
            m["soggy"] = True
        if before >= 0.95:
            drip_onto(w, support, liters_ * 0.9, liquid, depth + 1)
        return
    # not waterproof and not absorbent: the water sits on it (wood rings, electronics)
    m = w.mut(support)
    m["wetness"] = max(C.num(srec.get("wetness")), 0.7)
    if srec.get("material") in ("wood", "hardwood", "mdf", "particleboard") and not srec.get("sealed"):
        accrue_damage(w, support, liters_, material=srec.get("material"))
        m = w.mut(support)
        m["_water_ring"] = C.rnd(C.num(m.get("_water_ring")) + liters_ * 10.0, 3)
    if srec.get("electronic") or srec.get("water_safe") is False:
        if not srec.get("water_damaged"):
            w.mut(support)["water_damaged"] = True
            w.stat("damage")
    if C.num(w.ent(support).get("pooled_l")) > 0.05:
        drip_onto(w, support, liters_, liquid, depth + 1)
    else:
        w.mut(support)["pooled_l"] = C.rnd(C.num(w.ent(support).get("pooled_l")) + liters_, 4)


def _onto_below(w, support, liters_, liquid, depth):
    srec = w.ent(support)
    parent = srec.get("location")
    if parent in w.s["rooms"]:
        C.add_floor_water(w, parent, srec.get("pos") or C.cell_of(w, support), liquid, liters_, source=support)
    elif parent in w.s["entities"]:
        drip_onto(w, support, liters_, liquid, depth + 1)


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
                C.damage(w, loc, "water ring on the wood", kind="water_ring", severity=0.3)
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
# carrying liquids (only when the manipulation domain's carry dynamics did not already run)

MOTION = {"careful": 0.5, "normal": 1.0, "fast": 1.7}


def slosh_spill_l(rec, motion):
    have = L.liters(rec)
    cap = L.cap_l(rec) if not rec.get("ice_tray") else C.num(rec.get("cells"), 14) * C.num(rec.get("cell_ml"), 28.0) / 1000.0
    if have <= 0 or cap <= 0:
        return 0.0
    if rec.get("openable") and not rec.get("open", True):
        return 0.0
    if rec.get("lid") and rec.get("open") is False:
        return 0.0
    size = rec.get("size_m") or [0.1, 0.1, 0.1]
    inner = rec.get("inner_size_m") or [float(size[0]) * 0.9, float(size[1]) * 0.9, float(size[2]) * 0.9]
    if rec.get("ice_tray"):
        diameter, height = 0.035, 0.025
    else:
        diameter, height = max(0.03, min(float(inner[0]), float(inner[1]))), max(0.02, float(inner[2]))
    amplitude = 0.010 * motion * (diameter / 0.08) ** 0.5
    headroom = max(0.0, (1.0 - have / cap) * height)
    if amplitude <= headroom:
        return 0.0
    area = cap * 1e-3 / max(0.02, height)
    return min(have, round((amplitude - headroom) * area * 1000.0 * 0.6, 4))


def _carry_hook(name):
    def hook_fn(ctx, result):
        if ctx.data.get("carry_slosh_applied"):
            return
        w = ctx.w
        held = w.held_items()
        if not held:
            return
        speed = ctx.args.get("speed") or w.robot.get("speed_mode", "normal")
        motion = MOTION.get(speed, 1.0)
        if name == "move":
            conn = w.s.get("connections", {}).get(ctx.data.get("connection") or "", {})
            if conn.get("kind") == "stairs":
                motion *= 1.8
        elif name == "approach":
            motion *= 0.8
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


def slosh_capacity(rec):
    return C.num(rec.get("cells"), 14) * C.num(rec.get("cell_ml"), 28.0) / 1000.0 if rec.get("ice_tray") else L.cap_l(rec)


for _name in ("move", "approach", "walk_to"):
    _carry_hook(_name)


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
        m["_leak"] = {"component": "hose_fitting" if defect == "none" else "hose_" + defect, "rate_lpm": rate, "when": "pressurized",
                      "line": rec.get("line", "cold"), "fixture": appliance, "to": {"room": C.room_of(w, appliance), "cell": C.cell_of(w, appliance)},
                      "started_s": C.now(w)}
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
