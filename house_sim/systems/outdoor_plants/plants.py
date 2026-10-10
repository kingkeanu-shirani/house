"""Plant physiology and container water physics (fidelity P-approx).

Water: a pot's medium holds soil_l liters at volumetric water content theta (moisture)
with a faster-drying top layer (moisture_top). Watering adds liters/soil_l to theta;
above saturation water runs straight through; with a drainage hole everything above
container capacity drains into the saucer below (saucer overflow spills onto the
furniture/floor beneath it); without a hole the pot waterlogs and overflows its rim.
Plants transpire et L/day scaled by pot size, temperature, light, outdoor sun/wind and
water stress (stomata close as the root zone dries). Outdoor exposed pots catch rain.

Health (secret, 0..1) integrates drought, waterlogging/root rot, light outside the
species band (leggy growth / scorch), heat and cold zones near radiators, heaters,
AC vents and cold glass, frost below the species hardiness, pests and handling damage.
Symptoms (visible list) reveal the causes the way a person would see them.

Derived evaluation fields go in _op_status (never observed): moisture dry|ok|wet, rwc,
overwatered, light low|ok|high, direct_sun, zone_temp_c, temp cold|ok|hot,
pet_reachable, net_turn_deg, isolated, bud_drop, ...
"""
import json
import math

from ...kernel import physics
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, answerer, before, hook, inspector, system
from . import common as C
from .species import SPECIES, dormant, medium_params, profile, rwc, theta_for

WINDOW_DLI = {"south": 22.0, "west": 16.0, "east": 13.0, "north": 5.0}
SEASON_LIGHT = {"summer": 1.2, "spring": 0.95, "autumn": 0.85, "winter": 0.55}
OUTDOOR_DLI = {"summer": 45.0, "spring": 32.0, "autumn": 25.0, "winter": 12.0}
PET_REACH_M = {"dog": 1.0, "cat": 1.7, "rabbit": 0.4, "bird": 2.5}
FLOW_LPS = {"trickle": 0.035, "gentle": 0.1, "full": 0.35, "mist": 0.01, "can": 0.15}


# ---------------------------------------------------------------------------
# lookups

def is_plant(record):
    return C.is_type(record, "plant")


def plants_in_world(w, room=None):
    return C.find_types(w, "plant", room=room)


# Per-slow-tick memo (positions, lights and heat sources do not change inside one slow plant update).
_MEMO = None


def _memo_get(key, fn):
    if _MEMO is None:
        return fn()
    if key not in _MEMO:
        _MEMO[key] = fn()
    return _MEMO[key]


def _room_plant_centers(w, rid):
    """[(plant id, center xy)] for the plants in a room (memoized during a slow tick)."""
    return _memo_get(("room_plants", rid), lambda: [(o, C.center_m(w, o)) for o in plants_in_world(w, room=rid)])


def windows(w):
    return C.static_ids(w, "windows", lambda r: r.get("category") == "window")


def plants_within(w, eid):
    """Plants that are eid or inside/on it (any depth), sorted; O(plants) instead of walking every entity."""
    ents = w.s["entities"]
    return [p for p in C.type_index(w, "plant") if p in ents and (p == eid or w.within(p, eid))]


def container_of(w, pid):
    """The entity holding a plant's root zone: its pot/bed/tray, or the plant itself for in-ground shrubs/trees."""
    record = w.s["entities"].get(pid, {})
    loc = record.get("location")
    rec = w.s["entities"].get(loc)
    if rec is not None and isinstance(rec.get("medium"), dict):
        return loc
    if isinstance(record.get("medium"), dict) and is_plant(record):
        return pid
    return None


def plant_in(w, container_id):
    for child in C.kids_of_type(w, container_id, "plant"):
        return child
    return None


def species_of(record):
    return record.get("species", "unknown")


def prof(record):
    return profile(species_of(record))


def pot_area_m2(pot):
    d = float(pot.get("diameter_cm", 15.0)) / 100.0
    return math.pi * (d / 2.0) ** 2


def medium_kind(rec):
    return (rec.get("medium") or {}).get("kind", "potting_mix")


def soil_liters(rec):
    medium = rec.get("medium") or {}
    if C.is_type(rec, "garden_bed"):
        size = rec.get("size_m") or [3.0, 1.0]
        return float(size[0]) * float(size[1]) * float(medium.get("depth_cm", 30)) * 10.0
    if C.is_type(rec, "seed_tray"):
        return float(rec.get("rows", 6)) * float(rec.get("cols", 12)) * float(rec.get("cell_l", 0.05)) * float(medium.get("fill_frac", 0.0))
    return float(medium.get("soil_l", 0.0))


def container_rwc(rec):
    medium = rec.get("medium") or {}
    kind = medium.get("kind", "potting_mix")
    if kind == "none" or soil_liters(rec) <= 1e-6:
        return 0.0
    return rwc(float(medium.get("moisture", 0.0)), kind)


def plant_rwc(w, pid):
    cid = container_of(w, pid)
    if cid is None:
        return None
    return container_rwc(w.s["entities"][cid])


def update_pot_mass(w, pot_id):
    pot = w.s["entities"][pot_id]
    if not C.is_type(pot, "plant_pot"):
        return
    medium = pot.get("medium") or {}
    params = medium_params(medium.get("kind"))
    soil_l = float(medium.get("soil_l", 0.0))
    mass = float(pot.get("base_mass_kg", 0.3)) + soil_l * params["bulk_kg_l"] + soil_l * float(medium.get("moisture", 0.0))
    mass = round(mass, 3)
    if abs(mass - float(pot.get("mass_kg", 0.0))) > 1e-3:
        w.mut(pot_id)["mass_kg"] = mass


# ---------------------------------------------------------------------------
# water physics

def _spill_below(w, eid, liters, liquid="water", natural=False):
    """Liters overflowing eid fall onto its parent surface/floor.

    natural=True: rain overflowing an exposed outdoor pot/saucer. The rain is wetting the furniture and
    paving around it anyway, so it is runoff, not a spill the robot caused (no `spills`/`damage`)."""
    if liters <= 1e-4:
        return
    state = w.s
    record = state["entities"][eid]
    loc = record.get("location")
    rid = C.room_of(w, eid)
    if loc in state["entities"]:
        parent = state["entities"][loc]
        if C.is_type(parent, "plant_saucer"):
            receive_in_saucer(w, loc, liters, natural=natural)
            return
        if C.is_type(parent, "garden_bed") or C.is_type(parent, "lawn") or C.is_type(parent, "tarp"):
            return
        if natural and rid and C.is_outdoor_room(w, rid):
            return
        onto = loc if (parent.get("category") in ("furniture", "seat") or parent.get("surface") or parent.get("blocks_path")) else None
        cell = C.floor_cell_of(w, loc)
        if rid and C.is_outdoor_room(w, rid) and str(parent.get("material", "")) not in C.WOOD:
            onto = None
        C.add_spill(w, rid, cell, liters, liquid, onto=onto)
        return
    if rid and C.is_outdoor_room(w, rid):
        return  # drains into the ground/paving outdoors
    C.add_spill(w, rid, C.floor_cell_of(w, eid), liters, liquid)


def receive_in_saucer(w, saucer_id, liters, natural=False):
    saucer = w.s["entities"][saucer_id]
    cap = float(saucer.get("capacity_l", 0.4))
    have = float(saucer.get("liquid_l", 0.0))
    total = have + liters
    m = w.mut(saucer_id)
    m["liquid"] = "water"
    m["liquid_l"] = round(min(cap, total), 4)
    if total > cap + 1e-6:
        C.note(w, "saucer_overflow", saucer=saucer_id, liters=round(total - cap, 3))
        _spill_below(w, saucer_id, total - cap, natural=natural)


def add_water_to_pot(w, pot_id, liters, at="soil", natural=False):
    """Returns dict(absorbed, drained, overflow). Drainage is effectively immediate (minutes).
    natural=True for rain (overflow outdoors is runoff, never a robot-caused spill)."""
    pot = w.s["entities"][pot_id]
    medium = pot.get("medium") or {}
    kind = medium.get("kind", "potting_mix")
    soil_l = soil_liters(pot)
    result = {"absorbed": 0.0, "drained": 0.0, "overflow": 0.0}
    if liters <= 0:
        return result
    m = w.mut(pot_id)
    md = m.setdefault("medium", {})
    if soil_l <= 1e-6 or kind == "none":
        # empty pot: water runs out of the hole or pools
        if pot.get("drainage_hole", True):
            result["drained"] = liters
            _spill_below(w, pot_id, liters, natural=natural)
        else:
            md["standing_l"] = round(float(md.get("standing_l", 0.0)) + liters, 4)
        return result
    p = medium_params(kind)
    theta = float(md.get("moisture", 0.0))
    room_for = max(0.0, (p["sat"] - theta) * soil_l)
    absorbed = min(liters, room_for)
    excess = liters - absorbed
    theta = theta + absorbed / soil_l
    drained = 0.0
    if pot.get("drainage_hole", True):
        if theta > p["fc"]:
            drained = (theta - p["fc"]) * soil_l
            theta = p["fc"]
        drained += excess
        excess = 0.0
    md["moisture"] = round(theta, 4)
    md["moisture_top"] = round(max(theta, min(p["sat"], float(md.get("moisture_top", 0.0)) + liters / max(0.2, soil_l * 0.25))), 4)
    if not pot.get("drainage_hole", True):
        md["standing_l"] = round(float(md.get("standing_l", 0.0)) + excess, 4)
        headroom = pot_area_m2(pot) * float(md.get("headspace_cm", 2.5)) * 10.0
        if md["standing_l"] > headroom:
            result["overflow"] = md["standing_l"] - headroom
            md["standing_l"] = round(headroom, 4)
            _spill_below(w, pot_id, result["overflow"], natural=natural)
    if drained > 0:
        _spill_below(w, pot_id, drained, natural=natural)
    result.update(absorbed=round(absorbed, 4), drained=round(drained, 4))
    update_pot_mass(w, pot_id)
    return result


def water_needed_to(pot, target_rwc):
    medium = pot.get("medium") or {}
    kind = medium.get("kind", "potting_mix")
    soil_l = soil_liters(pot)
    if soil_l <= 0:
        return 0.0
    p = medium_params(kind)
    target_theta = theta_for(target_rwc, kind)
    return max(0.0, (min(p["sat"], target_theta) - float(medium.get("moisture", 0.0))) * soil_l)


def watering_check(w, pid, liters, before_rwc):
    """Flag unnecessary watering (overwatering) and watering artificial plants."""
    record = w.s["entities"][pid]
    if record.get("artificial"):
        C.stat(w, "watered_artificial")
        C.set_status(w, pid, watered_artificial=True)
        return
    p = prof(record)
    pot = w.s["entities"].get(container_of(w, pid) or "", {})
    scale = max(0.05, 0.05 * soil_liters(pot)) if pot else 0.05
    if before_rwc is not None and liters > scale and before_rwc >= p["water_at"] + 0.25:
        C.stat(w, "overwatered")
        C.set_status(w, pid, overwatered=True)
        C.note(w, "overwatered", plant=pid, rwc=round(before_rwc, 2))


# ---------------------------------------------------------------------------
# light, heat and placement

def window_facing(room, window):
    facing = window.get("facing")
    if facing:
        return facing
    from ...kernel import geometry
    cols, rows = geometry.grid_size(room)
    pos = window.get("pos") or [0, 0]
    if pos[1] == 0:
        return "north"
    if pos[1] >= rows - 1:
        return "south"
    if pos[0] == 0:
        return "west"
    if pos[0] >= cols - 1:
        return "east"
    return "east"


def light_at(w, eid):
    """(dli mol/m2/day, direct_sun bool, bearing_deg of the dominant light source)."""
    return _memo_get(("light", eid), lambda: _light_at(w, eid))


def _light_at(w, eid):
    state = w.s
    rid = C.room_of(w, eid)
    room = state["rooms"].get(rid, {})
    season = C.season(w)
    if room.get("outdoor"):
        dli = OUTDOOR_DLI.get(season, 30.0)
        shade = shade_at(w, eid)
        if C.is_roofed(w, rid):
            shade = max(shade, 0.65)
        return round(dli * (1.0 - shade), 2), shade < 0.4, 180.0
    x, y = C.center_m(w, eid)
    total, direct, best, bearing = 0.0, False, 0.0, 180.0
    factor = SEASON_LIGHT.get(season, 1.0)
    for wid in windows(w):
        win = state["entities"].get(wid)
        if win is None or win.get("location") != rid:
            continue
        facing = window_facing(room, win)
        open_frac = 1.0 - float(win.get("covered", 0.0) or 0.0)
        wx, wy = C.center_m(w, wid)
        d = C.dist((x, y), (wx, wy))
        contrib = WINDOW_DLI.get(facing, 10.0) * factor * open_frac / (1.0 + (d / 1.2) ** 2)
        total += contrib
        if facing in ("south", "west") and d <= 1.3 and open_frac > 0.5:
            direct = True
        if contrib > best:
            best = contrib
            bearing = math.degrees(math.atan2(wx - x, -(wy - y))) % 360.0
    ents = state["entities"]
    if any(k in ents and ents[k].get("on") and C.room_of(w, k) == rid for k in C.static_ids(w, "lights", lambda r: r.get("category") == "light")):
        total += 1.0
    for gid in C.static_ids(w, "grow_lights", lambda r: bool(r.get("grow_light"))):
        extra = ents.get(gid)
        if extra is not None and extra.get("on") and C.room_of(w, gid) == rid:
            total += 15.0 / (1.0 + (C.dist((x, y), C.center_m(w, gid)) / 0.5) ** 2)
    return round(total, 2), direct, bearing


def shade_at(w, eid):
    """Outdoor shade fraction from lawn shade map, bed setting, shade cloth or open umbrellas nearby."""
    state = w.s
    record = state["entities"].get(eid, {})
    shade = 0.0
    loc = record.get("location")
    parent = state["entities"].get(loc, {})
    if C.is_type(parent, "garden_bed"):
        shade = {"full": 0.0, "part": 0.45, "shade": 0.75}.get(parent.get("sun", "full"), 0.0)
        cover = parent.get("covered_by")
        if cover and cover in state["entities"]:
            shade = max(shade, float(state["entities"][cover].get("shade", 0.0)))
    elif record.get("shade") is not None:
        shade = float(record["shade"])
    from . import lawn
    rid = C.room_of(w, eid)
    patch = lawn.patch_shade_at(w, rid, C.center_m(w, eid))
    if patch is not None:
        shade = max(shade, patch)
    cover = C.covering(w, eid)
    if cover:
        shade = max(shade, float(state["entities"][cover].get("shade", 0.0)))
    return C.clamp(shade, 0.0, 0.95)


def thermal_sources(w):
    """Static index of things that warm or chill a plant's zone (heat sources, cold drafts, windows)."""
    return C.static_ids(w, "thermal_sources", lambda r: bool(r.get("heat_source") or r.get("cold_draft") or r.get("category") == "window"))


def zone_temperature(w, eid):
    return _memo_get(("zone", eid), lambda: _zone_temperature(w, eid))


def _zone_temperature(w, eid):
    state = w.s
    rid = C.room_of(w, eid)
    if rid is None:
        return 21.0
    base = physics.room_temperature(state, rid)
    room = state["rooms"].get(rid, {})
    if room.get("outdoor"):
        cover = C.covering(w, eid)
        if cover:
            crec = state["entities"][cover]
            pinned = crec.get("pinned") or crec.get("fastened") or crec.get("category") != "plant_cover"
            base += float(crec.get("protect_c", 0.0)) * (1.0 if pinned else 0.35)
        return round(base, 2)
    x, y = C.center_m(w, eid)
    delta = 0.0
    for sid in thermal_sources(w):
        src = state["entities"].get(sid)
        if src is None or sid == eid or C.room_of(w, sid) != rid:
            continue
        if src.get("heat_source") and (src.get("on", True) or not src.get("switchable")):
            ts = float(src.get("surface_temp_c", src.get("temperature_c", base)))
            if ts > base + 5:
                d = C.dist((x, y), C.center_m(w, sid))
                delta += (ts - base) * 0.5 / (1.0 + (d / 0.45) ** 2)
        elif src.get("cold_draft") and (src.get("on", True) or not src.get("switchable")):
            d = C.dist((x, y), C.center_m(w, sid))
            delta -= float(src.get("draft_c", 8.0)) / (1.0 + (d / 0.6) ** 2)
        elif src.get("category") == "window" and src.get("location") == rid:
            outdoor = float(state.get("weather", {}).get("temperature_c", 18.0))
            if outdoor < 5:
                d = C.dist((x, y), C.center_m(w, sid))
                delta -= (5 - outdoor) * 0.3 / (1.0 + (d / 0.35) ** 2)
    return round(base + delta, 2)


def pet_reachable(w, pid):
    record = w.s["entities"][pid]
    toxic = set(prof(record).get("toxic", []))
    if record.get("artificial") or not toxic:
        return False
    pets = [p for p in w.s.get("pets", {}).values() if p.get("species") in toxic]
    if not pets:
        return False
    if C.enclosed(w, pid):
        return False
    top = C.height_of(w, pid)
    rid = C.room_of(w, pid)
    room = w.s["rooms"].get(rid, {})
    for pet in pets:
        reach = PET_REACH_M.get(pet.get("species"), 1.0)
        if pet.get("size") == "large":
            reach += 0.3
        if room.get("off_limits_pets") or rid in (pet.get("excluded_rooms") or []):
            continue
        if top <= reach:
            return True
    return False


def effective_yaw(w, pid):
    total, eid, seen = 0.0, pid, set()
    while eid in w.s["entities"] and eid not in seen:
        seen.add(eid)
        total += float(w.s["entities"][eid].get("yaw_deg", 0.0) or 0.0)
        eid = w.s["entities"][eid].get("location")
    return total % 360.0


def angle_diff(a, b):
    d = (a - b) % 360.0
    return min(d, 360.0 - d)


def egress_blocking(w, eid):
    """(blocks_door, blocks_window) for a floor-standing pot/plant."""
    from ...kernel import geometry
    state = w.s
    holder = eid
    while holder in state["entities"] and state["entities"][holder].get("location") in state["entities"]:
        holder = state["entities"][holder]["location"]
    record = state["entities"].get(holder, {})
    rid = record.get("location")
    if rid not in state["rooms"] or not record.get("pos"):
        return False, False
    cells = geometry.footprint(record)
    door = False
    for conn in state.get("connections", {}).values():
        cell = conn.get("cells", {}).get(rid)
        if cell and any(geometry.chebyshev(c, cell) <= 0 for c in cells):
            door = True
    window = False
    tall = C.height_of(w, eid) + float((state["entities"][eid].get("size_m") or [0, 0, 0.4])[2]) > 0.9
    for wid in (windows(w) if tall else []):
        win = state["entities"].get(wid) or {}
        if win.get("location") == rid and win.get("pos"):
            if any(geometry.chebyshev(c, win["pos"]) <= 0 for c in cells):
                window = True
    return door, window


# ---------------------------------------------------------------------------
# status / evaluation fields

def plant_status(w, pid):
    state = w.s
    record = state["entities"].get(pid)
    if record is None or not is_plant(record):
        return
    p = prof(record)
    values = {}
    rw = plant_rwc(w, pid)
    if record.get("artificial"):
        values["moisture"] = "n/a"
    elif rw is not None:
        lo, hi = p["band"]
        values["rwc"] = round(rw, 3)
        values["moisture"] = "dry" if rw < lo else ("wet" if rw > hi + 1e-9 else "ok")
        values["needs_water"] = rw < p["water_at"]
    if not record.get("artificial"):
        dli, direct, bearing = light_at(w, pid)
        values["dli"] = dli
        values["direct_sun"] = direct
        lo, hi = p["dli"]
        values["light"] = "low" if dli < lo else ("high" if dli > hi or (direct and not p["direct"]) else "ok")
        zt = zone_temperature(w, pid)
        values["zone_temp_c"] = zt
        tlo, thi = p["temp"]
        values["temp"] = "cold" if zt < tlo else ("hot" if zt > thi else "ok")
        values["pet_reachable"] = pet_reachable(w, pid)
    yaw = effective_yaw(w, pid)
    if "_yaw_start" not in record:
        w.mut(pid)["_yaw_start"] = round(yaw, 2)
        record = state["entities"][pid]
    values["net_turn_deg"] = round(angle_diff(yaw, float(record.get("_yaw_start", 0.0))), 1)
    values["alive"] = bool(record.get("alive", True))
    values["healthy"] = bool(record.get("alive", True)) and float(record.get("health", 1.0)) >= 0.6
    door, window = egress_blocking(w, pid)
    values["blocks_door"] = door
    values["blocks_window"] = window
    holder = w.s["entities"].get(container_of(w, pid) or "", {}) or {}
    saucer = w.s["entities"].get(holder.get("location") or "", {})
    if C.is_type(saucer, "plant_saucer"):
        limit = float(saucer.get("pebble_top_l", 0.0)) if saucer.get("pebbles") else 0.03
        values["saucer_standing_water"] = float(saucer.get("liquid_l", 0.0)) > limit + 1e-9
    near = nearest_plant_m(w, pid)
    values["nearest_plant_m"] = None if near is None else round(near, 2)
    values["isolated"] = near is None or near >= 2.0
    C.set_status(w, pid, **values)


def nearest_plant_m(w, pid):
    rid = C.room_of(w, pid)
    if rid is None:
        return None
    here = C.center_m(w, pid)
    best = None
    for other, there in _room_plant_centers(w, rid):
        if other == pid or other not in w.s["entities"]:
            continue
        orec = w.s["entities"][other]
        if not orec.get("alive", True) or orec.get("artificial"):
            continue
        d = C.dist(here, there)
        best = d if best is None else min(best, d)
    return best


def refresh_plants_near(w, eid):
    """Recompute status for plants in/under eid (after it moved or was watered)."""
    for item in plants_within(w, eid):
        plant_status(w, item)


# ---------------------------------------------------------------------------
# damage helpers

def harm(w, pid, amount, reason):
    record = w.s["entities"].get(pid)
    if record is None:
        return
    m = w.mut(pid)
    m["health"] = round(max(0.0, float(m.get("health", 1.0)) - amount), 3)
    harms = m.setdefault("_harm", [])
    harms.append(reason)
    C.stat(w, "plant_harm")
    C.note(w, "plant_harm", plant=pid, reason=reason)
    if m["health"] <= 0.0 and m.get("alive", True):
        m["alive"] = False
        C.stat(w, "plants_killed")
    plant_status(w, pid)


def add_symptom(m, symptom):
    symptoms = m.setdefault("symptoms", [])
    if symptom not in symptoms:
        symptoms.append(symptom)


def drop_symptom(m, symptom):
    if symptom in m.get("symptoms", []):
        m["symptoms"] = [s for s in m["symptoms"] if s != symptom]


def bud_drop_check(w, eid, reason):
    """Moving or turning a budding bud-sensitive plant (Christmas cactus, gardenia) drops its buds."""
    for item in plants_within(w, eid):
        record = w.s["entities"].get(item)
        if record is None or not is_plant(record) or not prof(record).get("bud_sensitive"):
            continue
        buds = int((record.get("flowers") or {}).get("bud", 0))
        if buds <= 0:
            continue
        m = w.mut(item)
        m["flowers"]["bud"] = 0
        st = m.setdefault("_op_status", {})
        st["bud_drop"] = int(st.get("bud_drop", 0)) + buds
        add_symptom(m, "dropped buds")
        C.stat(w, "bud_drop", buds)
        C.stat(w, "plant_harm")
        C.note(w, "bud_drop", plant=item, buds=buds, reason=reason)


# ---------------------------------------------------------------------------
# slow dynamics

def slow_plants(w, periods):
    global _MEMO
    _MEMO = {}
    try:
        _slow_plants(w, periods)
    finally:
        _MEMO = None


def _slow_plants(w, periods):
    dt = C.SLOW_S * periods
    days = dt / C.DAY
    state = w.s
    from ...kernel import clock
    month = clock.now(state).month
    rain_mm = float((state.get("weather") or {}).get("rain_last_period_mm", 0.0))
    t_air = C.air_temp(w)
    sun = C.daylight(w)
    wind = float((state.get("weather") or {}).get("wind_mps", 2.0))
    handled_containers = set()
    for pid in plants_in_world(w):
        record = state["entities"].get(pid)
        if record is None:
            continue
        cid = container_of(w, pid)
        rid = C.room_of(w, pid)
        outdoor = C.is_outdoor_room(w, rid) if rid else False
        p = prof(record)
        if cid and C.is_type(state["entities"][cid], "plant_pot") and cid not in handled_containers:
            handled_containers.add(cid)
            _pot_water_step(w, cid, pid, record, p, days, outdoor, rain_mm, t_air, sun, wind)
        elif cid == pid:
            _ground_water_step(w, pid, record, p, days, outdoor, rain_mm, t_air, sun)
        if record.get("artificial") or not record.get("alive", True):
            continue
        _health_step(w, pid, p, days, outdoor, t_air, month)
        _pest_step(w, pid, days)
        _lean_step(w, pid, p, days)
        plant_status(w, pid)
    # saucers not under any plant still evaporate
    for sid in C.find_types(w, "plant_saucer"):
        sauc = state["entities"][sid]
        if float(sauc.get("liquid_l", 0.0)) > 0 and not C.kids_of_type(w, sid, "plant_pot"):
            m = w.mut(sid)
            m["liquid_l"] = round(max(0.0, float(m["liquid_l"]) - 0.06 * days), 4)


def _ground_water_step(w, pid, record, p, days, outdoor, rain_mm, t_air, sun):
    """In-ground shrub/tree root zone: rain on the drip area in, evapotranspiration out (deep roots buffer)."""
    medium = record.get("medium") or {}
    kind = medium.get("kind", "garden_soil")
    params = medium_params(kind)
    soil_l = soil_liters(record)
    if soil_l <= 0:
        return
    theta = float(medium.get("moisture", params["fc"]))
    canopy = float(record.get("canopy_m") or max((record.get("size_m") or [1.0, 1.0])[:2]))
    area = max(0.25, math.pi * (canopy / 2.0) ** 2)
    gain = rain_mm * area * 0.7 if outdoor and not C.covering(w, pid) else 0.0
    env = C.clamp((t_air + 5.0) / 25.0, 0.1, 2.2) * (0.6 + 1.6 * sun) if outdoor else 0.5
    stress = C.clamp(rwc(theta, kind) / 0.4, 0.05, 1.0)
    established = float(w.s.get("time_s", 0)) - float(record.get("planted_s", -1e12)) > 120 * C.DAY
    loss = float(p["et"]) * env * stress * days * (0.25 if established else 1.0)
    new_theta = C.clamp(theta + (gain - loss) / soil_l, params["wilt"] * 0.6, params["sat"])
    if established:
        new_theta = max(new_theta, params["wilt"] + 0.35 * (params["fc"] - params["wilt"]))
    if new_theta > params["fc"]:
        new_theta = params["fc"] + (new_theta - params["fc"]) * 0.3
    if abs(new_theta - theta) > 1e-4:
        m = w.mut(pid)
        md = m.setdefault("medium", {})
        md["moisture"] = round(new_theta, 4)
        md["moisture_top"] = round(C.clamp(float(md.get("moisture_top", theta)) + (new_theta - theta) * 1.5, params["wilt"] * 0.3, params["sat"]), 4)


def _pot_water_step(w, pot_id, pid, record, p, days, outdoor, rain_mm, t_air, sun, wind):
    state = w.s
    pot = state["entities"][pot_id]
    medium = pot.get("medium") or {}
    kind = medium.get("kind", "potting_mix")
    soil_l = soil_liters(pot)
    if soil_l <= 0 or kind == "none":
        return
    params = medium_params(kind)
    theta = float(medium.get("moisture", 0.0))
    top = float(medium.get("moisture_top", theta))
    r = rwc(theta, kind)
    stress = C.clamp(r / 0.4, 0.05, 1.0)
    size = C.clamp(soil_l / 2.0, 0.3, 6.0)
    if outdoor:
        env = C.clamp((t_air + 5.0) / 25.0, 0.1, 2.2) * (0.6 + 1.6 * sun) * (1.0 + 0.06 * wind)
        if C.is_roofed(w, C.room_of(w, pot_id)):
            env *= 0.6
    else:
        tz = zone_temperature(w, pid)
        dli, _, _ = light_at(w, pid)
        env = C.clamp((tz + 5.0) / 26.0, 0.2, 2.5) * (0.6 + 0.4 * min(1.5, dli / 15.0))
    et = 0.0 if record.get("artificial") or not record.get("alive", True) else float(p["et"])
    et *= size * env * stress
    evap = 0.015 * size * (1.6 if pot.get("material") == "terracotta" else 1.0) * (env if outdoor else 1.0)
    loss_l = (et + evap) * days
    new_theta = max(params["wilt"] * 0.4, theta - loss_l / soil_l)
    new_top = max(params["wilt"] * 0.2, min(new_theta + 0.12, top - 2.2 * (theta - new_theta) - 0.01 * days * (2.0 if outdoor else 1.0)))
    m = w.mut(pot_id)
    md = m["medium"]
    md["moisture"] = round(new_theta, 4)
    md["moisture_top"] = round(min(params["sat"], new_top), 4)
    if float(md.get("standing_l", 0.0)) > 0:
        md["standing_l"] = round(max(0.0, float(md["standing_l"]) - 0.03 * days), 4)
    # saucer water wicks back up through the hole; evaporates slowly
    loc = pot.get("location")
    sauc = state["entities"].get(loc)
    if sauc is not None and C.is_type(sauc, "plant_saucer") and float(sauc.get("liquid_l", 0.0)) > 0:
        have = float(sauc["liquid_l"])
        evap_s = (0.05 if not outdoor else 0.25) * days
        wick = 0.0
        if pot.get("drainage_hole", True) and not sauc.get("pebbles"):
            wick = min(have, max(0.0, (params["fc"] - new_theta) * soil_l), 0.4 * days * 24 * 0.1)
        sm = w.mut(loc)
        sm["liquid_l"] = round(max(0.0, have - evap_s - wick), 4)
        md["moisture"] = round(md["moisture"] + wick / soil_l, 4)
        standing = float(sauc.get("liquid_l", 0.0)) > (float(sauc.get("pebble_top_l", 0.0)) if sauc.get("pebbles") else 0.03)
        if standing and pot.get("drainage_hole", True):
            m.setdefault("_wet_feet_h", 0.0)
            m["_wet_feet_h"] = round(float(m.get("_wet_feet_h", 0.0)) + days * 24, 3)
    elif m.get("_wet_feet_h"):
        m["_wet_feet_h"] = 0.0
    # rain into exposed outdoor pots
    if outdoor and rain_mm > 0 and C.rain_exposed(w, pot_id) > 0:
        liters = rain_mm * pot_area_m2(pot)
        add_water_to_pot(w, pot_id, liters, natural=True)
    update_pot_mass(w, pot_id)


def _health_step(w, pid, p, days, outdoor, t_air, month):
    state = w.s
    record = state["entities"][pid]
    m = None
    health = float(record.get("health", 1.0))
    delta = 0.0
    symptoms = []
    rw = plant_rwc(w, pid)
    cid = container_of(w, pid)
    pot = state["entities"].get(cid or "", {})
    root_rot = float(record.get("root_rot", 0.0))
    if rw is not None:
        lo, hi = p["band"]
        if rw < 0.05:
            delta -= 0.10 * days
            symptoms.append("wilting")
        elif rw < lo - 0.1:
            delta -= 0.02 * days
            symptoms.append("dry crispy leaf edges")
        waterlogged = rw > hi + 0.05 or float((pot.get("medium") or {}).get("standing_l", 0.0)) > 0.02 or float(pot.get("_wet_feet_h", 0.0)) > 24
        if waterlogged:
            rate = 0.25 if p.get("kind") == "succulent" else 0.12
            root_rot = min(1.0, root_rot + rate * days)
        else:
            root_rot = max(0.0, root_rot - 0.02 * days)
        if root_rot > 0.3:
            delta -= 0.06 * root_rot * days
            symptoms.append("yellow lower leaves, soggy sour soil")
    dli, direct, _ = light_at(w, pid)
    lo_l, hi_l = p["dli"]
    if dli < lo_l * 0.7:
        delta -= 0.01 * days
        if float(record.get("_low_light_d", 0.0)) > 10:
            symptoms.append("leggy pale growth")
    if direct and not p["direct"]:
        delta -= 0.03 * days
        symptoms.append("bleached scorched patches")
    if outdoor and dli > hi_l * 1.3 and not p["direct"]:
        delta -= 0.03 * days
        symptoms.append("bleached scorched patches")
    zt = zone_temperature(w, pid)
    tlo, thi = p["temp"]
    if zt > thi + 2:
        delta -= 0.05 * days
        symptoms.append("heat-stressed curling leaves")
    if zt < tlo - 2:
        delta -= 0.04 * days
        symptoms.append("cold-damaged dark leaves")
    frost_line = float(p.get("hardy_c", 2.0))
    if zt < frost_line:
        m = w.mut(pid)
        m["health"] = 0.0
        m["alive"] = False
        add_symptom(m, "frost-killed blackened leaves")
        C.stat(w, "plants_killed")
        C.stat(w, "frost_damage")
        C.note(w, "frost_kill", plant=pid, temp=zt)
        return
    if p.get("heat_sensitive") and outdoor and t_air > 28 and shade_at(w, pid) < 0.4:
        delta -= 0.08 * days
        symptoms.append("heat-stressed bolting")
    pests = record.get("pests")
    if pests:
        delta -= 0.03 * float(pests.get("severity", 0.2)) * days
    if record.get("root_bound"):
        delta -= 0.01 * days
        symptoms.append("roots circling out of the drainage holes; dries out fast")
    if delta >= -1e-9 and root_rot < 0.3:
        delta += 0.02 * days
    m = m or w.mut(pid)
    m["root_rot"] = round(root_rot, 4)
    if dli < lo_l * 0.7:
        m["_low_light_d"] = round(float(m.get("_low_light_d", 0.0)) + days, 3)
    new_health = C.clamp(health + delta, 0.0, 1.0)
    m["health"] = round(new_health, 4)
    keep = [s for s in m.get("symptoms", []) if s in ("dropped buds", "torn stem", "leaf spots", "farina rubbed off", "oily leaves", "frost-killed blackened leaves")]
    for s in symptoms:
        if s not in keep:
            keep.append(s)
    if record.get("leaves", {}).get("dead"):
        keep.append("dead brown leaves")
    m["symptoms"] = keep
    if new_health <= 0.0 and m.get("alive", True):
        m["alive"] = False
        C.stat(w, "plants_killed")
        C.note(w, "plant_died", plant=pid)


def _pest_step(w, pid, days):
    state = w.s
    record = state["entities"][pid]
    pests = record.get("pests")
    if not pests:
        return
    severity = min(1.0, float(pests.get("severity", 0.2)) * (1.0 + 0.05 * days))
    if abs(severity - float(pests.get("severity", 0.2))) > 1e-4:
        w.mut(pid)["pests"]["severity"] = round(severity, 4)
    rid = C.room_of(w, pid)
    here = C.center_m(w, pid)
    for other, there in _room_plant_centers(w, rid):
        if other == pid or other not in state["entities"]:
            continue
        orec = state["entities"][other]
        if orec.get("pests") or orec.get("artificial") or not orec.get("alive", True):
            continue
        d = C.dist(here, there)
        if d > 1.0:
            continue
        exposure = severity * days * (1.0 if d < 0.4 else 0.35)
        om = w.mut(other)
        om["_pest_exposure"] = round(float(om.get("_pest_exposure", 0.0)) + exposure, 4)
        if om["_pest_exposure"] >= 0.5:
            om["pests"] = {"kind": pests.get("kind", "aphids"), "severity": 0.1, "where": pests.get("where", "leaf_underside")}
            C.stat(w, "pest_spread")
            C.note(w, "pest_spread", source=pid, plant=other)


def _lean_step(w, pid, p, days):
    state = w.s
    record = state["entities"][pid]
    rid = C.room_of(w, pid)
    if rid is None or C.is_outdoor_room(w, rid) or record.get("rooted") is False and not container_of(w, pid):
        return
    _, _, bearing = light_at(w, pid)
    lean = float(record.get("lean_deg", 0.0))
    world_dir = (float(record.get("lean_dir_deg", 0.0)) + effective_yaw(w, pid)) % 360.0
    rate = 0.6 * float(p.get("lean_rate", 1.0)) * days
    if lean < 0.5:
        new_lean, new_dir = min(25.0, lean + rate), (bearing - effective_yaw(w, pid)) % 360.0
    elif angle_diff(world_dir, bearing) <= 60.0:
        new_lean, new_dir = min(25.0, lean + rate), float(record.get("lean_dir_deg", 0.0))
    else:
        new_lean, new_dir = max(0.0, lean - 1.5 * rate), float(record.get("lean_dir_deg", 0.0))
    if abs(new_lean - lean) > 1e-3 or abs(new_dir - float(record.get("lean_dir_deg", 0.0))) > 1e-3:
        m = w.mut(pid)
        m["lean_deg"] = round(new_lean, 3)
        m["lean_dir_deg"] = round(new_dir, 2)
        if new_lean >= 8:
            add_symptom(m, "leaning toward the light")
        else:
            drop_symptom(m, "leaning toward the light")


@system("outdoor_plants.plants", order=120, max_dt=C.SLOW_S, period=C.SLOW_S)
def plants_system(w, dt, tick):
    """Slow plant physiology: runs once per 600 s with dt = the accumulated time (all rates are per day)."""
    slow_plants(w, dt / C.SLOW_S)


# ---------------------------------------------------------------------------
# hooks on core actions

def _plant_containers_moved(ctx):
    target = ctx.args.get("target")
    if not target or target not in ctx.w.s["entities"]:
        return None
    return target


@after("pick_up", order=150)
def _after_pick_up(ctx, result):
    target = _plant_containers_moved(ctx)
    if not target:
        return
    w = ctx.w
    record = w.s["entities"][target]
    bud_drop_check(w, target, "picked up")
    # Lifting a saucer with a heavy pot on it tilts the standing water out.
    if C.is_type(record, "plant_saucer") and float(record.get("liquid_l", 0.0)) > 0.02:
        on_top = C.kids_of_type(w, target, "plant_pot")
        from ..core import subtree_mass
        if on_top and subtree_mass(w, target) > 4.0:
            spilled = float(record["liquid_l"]) * 0.7
            m = w.mut(target)
            m["liquid_l"] = round(float(record["liquid_l"]) - spilled, 4)
            rid = w.robot["room"]
            if not C.is_outdoor_room(w, rid):
                C.add_spill(w, rid, list(w.robot.get("pos") or [0, 0]), spilled)
            C.note(w, "saucer_tilted", saucer=target, liters=round(spilled, 3))
    refresh_plants_near(w, target)


@after("place", order=150)
def _after_place(ctx, result):
    target = _plant_containers_moved(ctx)
    if not target:
        return
    w = ctx.w
    if ctx.args.get("orientation") == "rotated":
        m = w.mut(target)
        m["yaw_deg"] = round((float(m.get("yaw_deg", 0.0)) + 90.0) % 360.0, 2)
    refresh_plants_near(w, target)


def _generic_move_hook(name):
    @before(name, order=150)
    def before_fn(ctx):
        target = ctx.args.get("target")
        if target and target in ctx.w.s["entities"]:
            ctx.data["_op_yaw_before"] = float(ctx.w.s["entities"][target].get("yaw_deg", 0.0) or 0.0)

    @after(name, order=150)
    def hook_fn(ctx, result):
        target = ctx.args.get("target")
        if not target or target not in ctx.w.s["entities"]:
            return
        bud_drop_check(ctx.w, target, name)
        degrees = None
        for key in ("degrees", "angle", "yaw_deg", "angle_deg"):
            value = ctx.args.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                degrees = float(value)
        if degrees is not None and name in ("rotate", "turn", "set_orientation"):
            record = ctx.w.s["entities"][target]
            before_yaw = float(ctx.data.get("_op_yaw_before", record.get("yaw_deg", 0.0)) or 0.0)
            if abs(float(record.get("yaw_deg", 0.0) or 0.0) - before_yaw) < 1e-6:
                ctx.w.mut(target)["yaw_deg"] = round((before_yaw + degrees) % 360.0, 2)
        refresh_plants_near(ctx.w, target)
    before_fn.__name__ = "before_" + name
    hook_fn.__name__ = "after_" + name
    return hook_fn


for _verb in ("rotate", "turn", "push", "pull", "drag", "slide", "roll", "tilt", "shake", "set_orientation"):
    _generic_move_hook(_verb)


# leaf cleaning (cleaning owns dust/wipe/polish/spray): plant-specific consequences

LEAF_SHINE_WORDS = ("leaf_shine", "leaf shine", "polish", "oil")


def leaf_contact(w, pid, method, tool=None, careful=False):
    """Apply plant consequences of dusting/wiping leaves. method: dry_brush|dry_cloth|damp_cloth|wet_cloth|shine|spray."""
    record = w.s["entities"].get(pid)
    if record is None or not is_plant(record):
        return
    p = prof(record)
    leaf = p.get("leaf")
    m = w.mut(pid)
    soil = m.setdefault("soil", {})
    removed = {"dry_brush": 0.85, "dry_cloth": 0.8, "damp_cloth": 0.95, "wet_cloth": 0.95, "shine": 0.9, "spray": 0.5}.get(method, 0.6)
    if record.get("artificial"):
        removed = max(removed, 0.9)
    if "dust" in soil:
        soil["dust"] = round(float(soil["dust"]) * (1.0 - removed), 4)
    if record.get("artificial"):
        return
    if method in ("damp_cloth", "wet_cloth", "spray") and leaf == "fuzzy":
        m["leaf_spots"] = int(m.get("leaf_spots", 0)) + 3
        add_symptom(m, "leaf spots")
        harm(w, pid, 0.1, "wet-wiped fuzzy leaves")
    if method in ("damp_cloth", "wet_cloth", "dry_cloth", "spray", "shine") and leaf == "farina" and m.get("farina_intact", True):
        m["farina_intact"] = False
        add_symptom(m, "farina rubbed off")
        harm(w, pid, 0.1, "removed protective farina")
    if method == "shine":
        m["oiled"] = True
        add_symptom(m, "oily leaves")
        harm(w, pid, 0.05, "leaf shine oil clogs stomata")
    if not careful and leaf in ("glossy", "fine", "soft") and record.get("fragile_leaves", leaf == "fine"):
        m["torn_leaves"] = int(m.get("torn_leaves", 0)) + 2
        harm(w, pid, 0.03, "unsupported leaves torn")
    if method == "wet_cloth" and leaf in ("succulent", "farina"):
        harm(w, pid, 0.05, "water left on succulent leaves")


def _cleaning_method(ctx):
    w = ctx.w
    text = json.dumps(ctx.args, sort_keys=True, default=str).lower()
    tools = [w.ent(e) for e in w.held_items()]
    if any(any(word in json.dumps(t, default=str).lower() for word in LEAF_SHINE_WORDS) for t in tools) or "shine" in text:
        return "shine"
    for t in tools:
        if float(t.get("wetness", 0.0)) > 0.5 or t.get("liquid_l", 0) or t.get("damp"):
            return "wet_cloth" if float(t.get("wetness", 0.0)) > 0.8 else "damp_cloth"
    if any(t.get("tool_kind") in ("brush", "soft_brush", "duster") or "brush" in str(t.get("type", "")) for t in tools):
        return "dry_brush"
    return "dry_cloth"


def _leaf_hook(verb):
    @after(verb, order=160)
    def hook_fn(ctx, result):
        target = ctx.args.get("target")
        if not target or target not in ctx.w.s["entities"] or not is_plant(ctx.w.s["entities"][target]):
            return
        method = "spray" if verb == "spray" else ("shine" if verb == "polish" else _cleaning_method(ctx))
        careful = ctx.w.robot.get("speed_mode") == "careful" or ctx.args.get("support") is True
        leaf_contact(ctx.w, target, method, careful=careful)
        plant_status(ctx.w, target)
    hook_fn.__name__ = "leaf_after_" + verb
    return hook_fn


for _verb in ("dust", "wipe", "polish", "spray", "rinse", "wash"):
    _leaf_hook(_verb)


# ---------------------------------------------------------------------------
# perception

LOOKALIKE_NOTE = "two candidate species look alike from here; a close smell/flower check is needed"


@inspector("outdoor_plants.plant", order=60)
def inspect_plant(w, eid, record, details, ctx):
    if not is_plant(record):
        if C.is_type(record, "plant_pot") or C.is_type(record, "plant_saucer"):
            medium = record.get("medium") or {}
            if medium:
                details["soil_surface"] = surface_feel(record)
                details["drainage_hole"] = record.get("drainage_hole", True)
                details["medium_kind"] = medium.get("kind")
                details["headspace_cm"] = medium.get("headspace_cm")
            if C.is_type(record, "plant_saucer"):
                details["standing_water_l"] = round(float(record.get("liquid_l", 0.0)), 3)
                if C.realistic(ctx):
                    # a look into the saucer: how full it is (empty/low/half/full/overflowing), not a measured volume
                    del details["standing_water_l"]
                    details["standing_water"] = C.fill_word(float(record.get("liquid_l", 0.0)) / max(1e-6, float(record.get("capacity_l", 0.45) or 0.45)))
        return
    where = ctx.args.get("where") if ctx is not None else None
    details["appearance"] = prof(record).get("look")
    if C.realistic(ctx) and (record.get("artificial") or species_of(record) == "artificial"):
        # a good fake looks like the species it imitates until it is examined closely
        details["appearance"] = profile(record.get("_looks_like") or "pothos").get("look")
    details["symptoms"] = list(record.get("symptoms", []))
    details["leaves"] = dict(record.get("leaves") or {})
    if record.get("flowers"):
        details["flowers"] = dict(record["flowers"])
    cid = container_of(w, eid)
    if cid:
        details["soil_surface"] = surface_feel(w.s["entities"][cid])
    if where in ("closely", "under", "behind"):
        species = species_of(record)
        if record.get("artificial"):
            details["artificial"] = True
            details["species"] = "artificial"
            identified = "artificial"
        else:
            look = prof(record).get("lookalike")
            if look and not record.get("in_flower_distinct"):
                details["species_candidates"] = sorted([species, look])
                details["identification"] = LOOKALIKE_NOTE
                identified = "uncertain"
            else:
                details["species"] = species
                identified = species
            toxic = prof(record).get("toxic", [])
            details["toxic_to"] = list(toxic)
            if C.realistic(ctx) and "species_candidates" in details:
                # unresolved look-alikes: only what holds for either candidate
                details["toxic_to"] = sorted(set(toxic) | set(profile(look).get("toxic", [])))
            if record.get("role"):
                details["role_hint"] = role_hint(record)
        facts = w.sec("facts").setdefault("identified", {})
        facts[eid] = identified
        pests = record.get("pests")
        if pests and (where == "under" or pests.get("where") in ("leaf_top", "stem", "soil")):
            details["pests"] = {"kind": pests.get("kind"), "severity": "heavy" if float(pests.get("severity", 0)) > 0.5 else "light",
                                "where": pests.get("where")}
        elif pests and where == "closely" and pests.get("kind") == "spider_mites":
            details["fine_webbing"] = True
        if record.get("diseased"):
            details["disease_signs"] = "dark spots with yellow halos on some stems"
        if record.get("variegated"):
            details["variegation"] = "cream-streaked healthy leaves (variegated cultivar)"
        if float(record.get("root_rot", 0.0)) > 0.3:
            details["smell"] = "sour, musty soil"
        if record.get("nest"):
            details["nest"] = record["nest"]
        p = prof(record)
        if p.get("bud_sensitive") and (record.get("flowers") or {}).get("bud"):
            details["note"] = "in bud: this species drops buds if moved or turned"
    # realistic perception: findings about secret fields are reported as what was seen (the kernel filter keeps no
    # secret field under its own name)
    C.seen_keys(ctx, details, SEEN_RENAMES)


SEEN_RENAMES = {"species": "species_seen", "artificial": "artificial_seen", "pests": "pests_seen", "nest": "nest_seen"}


def role_hint(record):
    role = record.get("role")
    if role == "weed":
        return "common weed"
    if role in ("volunteer", "volunteer_keep", "volunteer_pull"):
        return "self-sown volunteer; whether it is wanted is the owner's call"
    if role in ("crop", "ornamental", "desired"):
        return "cultivated plant"
    return None


def surface_feel(container):
    medium = container.get("medium") or {}
    if not medium or medium.get("kind") == "none":
        return "no soil"
    kind = medium.get("kind")
    top = rwc(float(medium.get("moisture_top", medium.get("moisture", 0.0))), kind)
    if float(medium.get("standing_l", 0.0)) > 0.02:
        return "puddled"
    if top < 0.25:
        return "dry, light-colored"
    if top < 0.7:
        return "slightly damp"
    return "dark and wet"


def probe_reading(container, depth_cm):
    """Meter/finger reading at depth (cm): returns dict with meter 1-10 and feel."""
    medium = container.get("medium") or {}
    kind = medium.get("kind", "potting_mix")
    theta = float(medium.get("moisture_top", 0.0)) if depth_cm <= 2.5 else float(medium.get("moisture", 0.0))
    if 2.5 < depth_cm < 4.0:
        theta = 0.5 * (theta + float(medium.get("moisture_top", theta)))
    value = rwc(theta, kind)
    meter = int(round(C.clamp(1 + 9 * value, 1, 10)))
    feel = "dry" if value < 0.2 else "barely damp" if value < 0.45 else "moist" if value < 0.85 else "wet" if value <= 1.05 else "soggy"
    return {"meter": meter, "feel": feel, "rwc": round(value, 2)}


@answerer("plants:")
def plants_answer(env, w, pid, topic, ctx):
    """Resident knowledge about plants: 'plants:care:<plant_id|species>' (watering/light) and
    'plants:keep:<plant_id>' (does the owner want this plant/volunteer kept?)."""
    parts = topic.split(":", 2)
    if len(parts) < 3:
        return None
    sub, key = parts[1], parts[2].strip()
    asked = (w.s.get("facts", {}).get("asked") or {})
    repeat = (pid + ":" + topic) in asked
    if sub == "keep":
        record = w.s["entities"].get(key)
        if record is None or not is_plant(record):
            C.stat(w, "unnecessary_questions")
            return {"topic": topic, "value": "I don't know which plant you mean.", "by": pid}
        want = record.get("owner_wants")
        role = record.get("role")
        if want is None:
            want = "pull" if role == "weed" else ("keep" if role in ("crop", "ornamental", "desired", "volunteer_keep", None) else "unsure")
        text = {"keep": "Keep that one, I want it there.", "pull": "Pull it, I don't want it.", "seedheads": "Leave the seed heads for the birds."}.get(want, "Not sure - leave it for now.")
        C.stat(w, "unnecessary_questions" if repeat else "necessary_questions")
        w.sec("facts")[topic] = want
        return {"topic": topic, "value": text, "keep": want != "pull", "by": pid}
    if sub != "care":
        return None
    return care_answer(env, w, pid, "care:" + key, ctx)


def care_answer(env, w, pid, topic, ctx):
    """'care:<plant_id>' or 'care:<species>': what the resident knows about watering/light."""
    key = topic.split(":", 1)[1]
    record = w.s["entities"].get(key)
    species = species_of(record) if record else key
    p = profile(species)
    if record and record.get("artificial"):
        text = "That one is fake - it never needs water."
    elif record is None and species not in SPECIES:
        C.stat(w, "unnecessary_questions")
        return {"topic": topic, "value": "I don't know that plant.", "by": pid}
    else:
        lo = p["water_at"]
        when = "let it dry out almost completely" if lo <= 0.15 else ("water when the top couple of cm feel dry" if lo < 0.4 else "keep it evenly moist")
        light = "bright, even direct sun is fine" if p["direct"] else ("low to medium light, no direct sun" if p["dli"][1] <= 15 else "bright indirect light")
        text = "It's a {}. {}; {}.".format(species.replace("_", " "), when.capitalize(), light)
    C.stat(w, "necessary_questions")
    w.sec("facts")[topic] = text
    w.sec("facts")["plants:" + topic] = text
    return {"topic": "plants:" + topic, "value": text, "by": pid}


# ---------------------------------------------------------------------------
# one-time derivation of evaluation fields at the start of an episode

def derive_all(w):
    """Compute _op_status for every domain entity (plants, beds, lawns, cushions, umbrellas, covers, surfaces...)."""
    from . import compost, garden, lawn, patio, pool, weather, yard
    for eid in sorted(w.s["entities"]):
        record = w.s["entities"].get(eid)
        if record is None:
            continue
        if float(record.get("wetness", 0.0) or 0.0) > 0 and record.get("_wet_mine") is None and weather.own_drying(record):
            w.mut(eid)["_wet_mine"] = float(record["wetness"])
        if is_plant(record):
            plant_status(w, eid)
        elif C.is_type(record, "garden_bed"):
            garden.bed_status(w, eid)
        elif C.is_type(record, "lawn"):
            lawn.summarize(w, eid)
        elif C.is_type(record, "outdoor_cushion"):
            patio.cushion_status(w, eid)
        elif patio.is_grill(record):
            patio.grill_status(w, eid)
        elif C.is_type(record, "patio_umbrella"):
            patio.umbrella_status(w, eid)
        elif C.is_type(record, "furniture_cover"):
            patio.cover_status(w, eid)
        elif C.is_type(record, "firewood_rack"):
            patio.stack_status(w, eid)
        elif C.is_type(record, "compost_bin"):
            compost.compost_status(w, eid)
        elif C.is_type(record, "pool"):
            pool.pool_status(w, eid)
        elif C.is_type(record, "drain_grate"):
            yard.grate_status(w, eid)
        elif yard.is_surface(record) and (record.get("snow_cm") is not None or record.get("deicer") is not None):
            yard.surface_status(w, eid)
        elif isinstance(record.get("hopper"), dict) and C.is_type(record, "broadcast_spreader"):
            from . import verbs
            verbs.hopper_fill(w, eid)          # how full the hopper looks (what is in it is hidden)


def _derive_once(w):
    meta = w.s.get("metadata") or {}
    if meta.get("outdoor_plants_derived"):
        return
    w.sec("metadata")["outdoor_plants_derived"] = True
    derive_all(w)


def grip_held_blades(w):
    """Cutting tools carry hazards ["sharp"] + safe_grips (kernel v2.1 remembers held_grip). A scenario that starts
    with such a tool in the robot's hand has it held by its safe grip, so later move/place checks pass."""
    ents = w.s["entities"]
    for eid in w.held_items():
        rec = ents.get(eid)
        if rec is None or rec.get("held_grip") or not C.is_type(rec, "garden_tool"):
            continue
        if "sharp" in (rec.get("hazards") or []) and rec.get("safe_grips"):
            w.mut(eid)["held_grip"] = rec["safe_grips"][0]


@hook("episode_start")
def _derive_at_episode_start(w, env):
    """Kernel v2.1: derive _op_status/_eval at reset, before the evaluation baseline is taken."""
    grip_held_blades(w)
    _derive_once(w)


@system("outdoor_plants.derive", order=44, max_dt=30.0)
def derive_system(w, dt, tick):
    """Fallback for worlds that skipped episode_start (reset(preserve_world=True)): O(1) once derived."""
    _derive_once(w)
