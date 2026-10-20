"""Well-mixed multi-zone air model (fidelity P-approx): smoke, carbon monoxide, natural gas, odors and
toxic vapours per room, with sources, door/stair exchange, ventilation to outdoors and exposure of people,
pets and the robot.

Per room (hidden, never observed): rooms.<rid>._air = {
    "smoke": extinction coefficient K in 1/m (visibility ~ 3/K m; detectors alarm near K 0.05-0.08),
    "co":    carbon monoxide, ppm,
    "gas":   natural gas (methane), % by volume (LEL 5 %, UEL 15 %),
    "odor":  {class: level}  arbitrary odor units (1 = clearly noticeable),
    "toxic": {name: ppm}     chloramine / chlorine from mixed cleaners,
    "dT":    hot-layer temperature rise above ambient (K) from fires}

Sources (entity fields, hidden):
  _gas_leak   {"m3h": 0.25, "valve": <shutoff id or null>}      natural gas escaping (stove burner left on unlit
                                                                ~0.25 m3/h; loose connector 0.02-0.5 m3/h)
  _co_source  {"g_min": 6.0, "needs": "running"|"on"|null}       CO emission (idling car in a closed garage
                                                                3-10 g/min; cracked furnace 0.5-2; generator 25-60)
  _odor       {"class": "spoiled_food", "rate": 0.02}            odor units per second into the room
Fires add smoke, CO, heat and a "smoke" odor (see fire.py).

Exchange per connection (m3/s): open doorway/door 0.12 (+0.002 per K of hot layer), stairs/hatch 0.15,
closed door 0.004 (gap under the door). To outdoors: open window 0.10 x (0.5 + wind/4), open exterior door 0.3,
infiltration 0.35 air changes per hour, running exhaust fans (`exhaust_m3s`). Outdoor rooms clear within ~1 min.
"""
import math

from ...kernel import body_faults, camera, clock, geometry
from ...kernel.plugins import system
from . import common as C

KEYS = ("smoke", "co", "gas")
EPS = {"smoke": 1e-4, "co": 0.05, "gas": 1e-4}
LEL_PCT = 5.0
CO_PPM_PER_G_M3 = 873.0          # 1 g/m3 CO = 873 ppm at 25 C
SMOKE_DECAY_S = 3600.0           # deposition on surfaces
ODOR_DECAY_S = 1800.0
TOXIC_DECAY_S = 900.0
DT_DECAY_S = 240.0               # hot layer cools into walls/ceiling
OUTDOOR_CLEAR_S = 40.0


def air(state, rid):
    room = state["rooms"].get(rid) or {}
    return room.get("_air") or {}


def _blank():
    return {"smoke": 0.0, "co": 0.0, "gas": 0.0, "odor": {}, "toxic": {}, "dT": 0.0}


def air_m(w, rid):
    room = w.room_m(rid)
    if not isinstance(room.get("_air"), dict):
        room["_air"] = _blank()
    rec = room["_air"]
    for key, value in _blank().items():
        rec.setdefault(key, value if not isinstance(value, dict) else {})
    return rec


def add(w, rid, key, amount):
    if rid not in w.s["rooms"] or not amount:
        return
    rec = air_m(w, rid)
    rec[key] = round(max(0.0, float(rec.get(key, 0.0)) + float(amount)), 6)


def add_odor(w, rid, cls, amount):
    if rid not in w.s["rooms"] or not amount:
        return
    rec = air_m(w, rid)
    rec["odor"][cls] = round(max(0.0, float(rec["odor"].get(cls, 0.0)) + float(amount)), 6)


def add_toxic(w, rid, name, ppm):
    if rid not in w.s["rooms"] or not ppm:
        return
    rec = air_m(w, rid)
    rec["toxic"][name] = round(max(0.0, float(rec["toxic"].get(name, 0.0)) + float(ppm)), 4)


def gas_fraction_lel(state, rid):
    return float(air(state, rid).get("gas", 0.0)) / LEL_PCT


def odor_levels(state, rid):
    """{class: level} including the mercaptan odorant carried by natural gas."""
    a = air(state, rid)
    levels = dict(a.get("odor") or {})
    gas = float(a.get("gas", 0.0))
    if gas > 0.0:
        # odorant is detectable well below 1/5 LEL (regulation): faint ~0.05 %, strong >= 0.5 %
        levels["gas"] = levels.get("gas", 0.0) + gas / 0.1
    smoke = float(a.get("smoke", 0.0))
    if smoke > 0.0:
        levels["smoke"] = levels.get("smoke", 0.0) + smoke / 0.05
    for name, ppm in (a.get("toxic") or {}).items():
        levels["chlorine_bleach"] = levels.get("chlorine_bleach", 0.0) + float(ppm) / 0.5
    return levels


def visibility_m(state, rid):
    k = float(air(state, rid).get("smoke", 0.0))
    return 30.0 if k <= 0.1 else round(3.0 / k, 2)


# ---------------------------------------------------------------------------------------------
# sources


def _source_room(state, eid):
    return C.root_room(state, eid)


def _gas_open(state, rec):
    util = state.get("utilities", {})
    if not util.get("gas", True) or not util.get("main_gas_open", True):
        return False
    leak = rec.get("_gas_leak") if isinstance(rec.get("_gas_leak"), dict) else {}
    valve = leak.get("valve")
    if valve and valve in state["entities"] and state["entities"][valve].get("gas_valve_open") is False:
        return False
    return True


def _co_active(rec):
    src = rec.get("_co_source") if isinstance(rec.get("_co_source"), dict) else {}
    need = src.get("needs")
    if need == "running":
        return bool(rec.get("running") or rec.get("engine_running"))
    if need == "on":
        return bool(rec.get("on"))
    return True


def apply_sources(w, dt, sources):
    state = w.s
    for eid in sources:
        rec = state["entities"].get(eid)
        if rec is None:
            continue
        rid = _source_room(state, eid)
        if rid is None:
            continue
        vol = C.room_volume_m3(state, rid)
        leak = rec.get("_gas_leak") if isinstance(rec.get("_gas_leak"), dict) else None
        if leak and float(leak.get("m3h", 0.0)) > 0 and _gas_open(state, rec):
            add(w, rid, "gas", float(leak["m3h"]) / 3600.0 * dt / vol * 100.0)
        src = rec.get("_co_source") if isinstance(rec.get("_co_source"), dict) else None
        if src and float(src.get("g_min", 0.0)) > 0 and _co_active(rec):
            add(w, rid, "co", float(src["g_min"]) / 60.0 * dt / vol * CO_PPM_PER_G_M3)
        odor = rec.get("_odor")
        if isinstance(odor, dict) and float(odor.get("rate", 0.0)) > 0 and odor.get("class") and not odor.get("contained"):
            # sealed containers (closed lid, bagged) hold most odor in
            factor = 1.0
            parent = state["entities"].get(rec.get("location"))
            if parent and parent.get("openable") and not parent.get("open") and parent.get("airtight", False):
                factor = 0.05
            if rec.get("sealed") or rec.get("bagged"):
                factor = min(factor, 0.1)
            add_odor(w, rid, odor["class"], float(odor["rate"]) * dt / max(1.0, vol / 40.0) * factor)


def _maybe_source(rec):
    """Static superset of air sources: entities with a leak/CO/odor marker when indexed, and anything that can gain one
    at runtime (burners and fuel-burning appliances: appliances_cooking marks unlit gas flow / sooty flames on burners)."""
    return bool("_gas_leak" in rec or "_co_source" in rec or "_odor" in rec or rec.get("type") == "burner"
                or "burner" in (rec.get("is_a") or []) or rec.get("ac_kind") == "burner" or rec.get("category") == "burner"
                or rec.get("safety_burner") or rec.get("fuel") or rec.get("gas_appliance"))


def source_ids(state):
    out = []
    for eid in C.sindex(state, "air_sources", _maybe_source):
        rec = state["entities"][eid]
        if "_gas_leak" in rec or "_co_source" in rec or "_odor" in rec:
            out.append(eid)
    return out


# ---------------------------------------------------------------------------------------------
# exchange


def _is_vent(rec):
    return rec.get("category") == "window" or "exhaust_m3s" in rec


def vent_map(state):
    """room -> m3/s exhausted to outdoors by open windows and running exhaust fans (one entity scan)."""
    wind = float(state.get("weather", {}).get("wind_mps", 2.0))
    flows = {}
    for eid in C.sindex(state, "vents", _is_vent):
        rec = state["entities"][eid]
        rid = rec.get("location")
        if rid not in state["rooms"]:
            continue
        if rec.get("category") == "window" and rec.get("open"):
            flows[rid] = flows.get(rid, 0.0) + 0.10 * (0.5 + wind / 4.0) * float(rec.get("open_fraction", 1.0))
        if rec.get("exhaust_m3s") and rec.get("on"):
            flows[rid] = flows.get(rid, 0.0) + float(rec["exhaust_m3s"])
    return flows


def _exterior_flow(state, rid):
    flow = 0.0
    for other, cid, conn in C.neighbors(state, rid):
        if C.is_outdoor(state, other) and not C.is_outdoor(state, rid):
            flow += 0.3 * C.connection_open(state, conn) + 0.004
    return flow


def _pair_flow(state, conn, dT):
    kind = conn.get("kind", "doorway")
    opening = C.connection_open(state, conn)
    if kind in ("elevator_door",):
        return 0.002
    if kind in ("stairs", "hatch"):
        base = 0.15
    else:
        base = 0.12
    if opening < 0.5:
        return 0.004
    return base + 0.002 * max(0.0, dT)


def _active_rooms(state):
    active = set()
    for rid in state["rooms"]:
        a = state["rooms"][rid].get("_air")
        if not a:
            continue
        if any(float(a.get(k, 0.0)) > EPS[k] for k in KEYS) or any(v > 1e-4 for v in (a.get("odor") or {}).values()) \
                or any(v > 1e-3 for v in (a.get("toxic") or {}).values()) or float(a.get("dT", 0.0)) > 0.05:
            active.add(rid)
    return active


def exchange(w, dt, active, vent=None):
    state = w.s
    rooms = state["rooms"]
    vent = vent_map(state) if vent is None else vent
    involved = set(active)
    for rid in sorted(active):
        for other, _, _ in C.neighbors(state, rid):
            involved.add(other)
    # pairwise exchange through connections touching an active room
    done = set()
    for rid in sorted(active):
        for other, cid, conn in C.neighbors(state, rid):
            if cid in done:
                continue
            done.add(cid)
            a, b = conn["rooms"]
            ra, rb = rooms[a], rooms[b]
            if ra.get("outdoor") and rb.get("outdoor"):
                continue
            aa, ab = ra.get("_air") or {}, rb.get("_air") or {}
            va, vb = C.room_volume_m3(state, a), C.room_volume_m3(state, b)
            if ra.get("outdoor") or rb.get("outdoor"):
                continue  # handled by the exterior flow below
            dT = max(float(aa.get("dT", 0.0)), float(ab.get("dT", 0.0)))
            q = _pair_flow(state, conn, dT)
            rate = q * (1.0 / va + 1.0 / vb)
            frac = (1.0 - math.exp(-rate * dt)) / (1.0 / va + 1.0 / vb)
            changes_a, changes_b = {}, {}
            for key in KEYS:
                ca, cb = float(aa.get(key, 0.0)), float(ab.get(key, 0.0))
                if abs(ca - cb) <= EPS[key] * 0.1:
                    continue
                moved = (ca - cb) * frac
                changes_a[key] = ca - moved / va
                changes_b[key] = cb + moved / vb
            odor_a, odor_b = dict(aa.get("odor") or {}), dict(ab.get("odor") or {})
            odor_changed = False
            for cls in sorted(set(odor_a) | set(odor_b)):
                ca, cb = float(odor_a.get(cls, 0.0)), float(odor_b.get(cls, 0.0))
                if abs(ca - cb) < 1e-6:
                    continue
                moved = (ca - cb) * frac
                odor_a[cls], odor_b[cls] = ca - moved / va, cb + moved / vb
                odor_changed = True
            tox_a, tox_b = dict(aa.get("toxic") or {}), dict(ab.get("toxic") or {})
            tox_changed = False
            for name in sorted(set(tox_a) | set(tox_b)):
                ca, cb = float(tox_a.get(name, 0.0)), float(tox_b.get(name, 0.0))
                if abs(ca - cb) < 1e-4:
                    continue
                moved = (ca - cb) * frac
                tox_a[name], tox_b[name] = ca - moved / va, cb + moved / vb
                tox_changed = True
            if changes_a or odor_changed or tox_changed:
                ma, mb = air_m(w, a), air_m(w, b)
                for key, value in changes_a.items():
                    ma[key] = round(max(0.0, value), 6)
                for key, value in changes_b.items():
                    mb[key] = round(max(0.0, value), 6)
                if odor_changed:
                    ma["odor"] = {k: round(v, 6) for k, v in sorted(odor_a.items()) if v > 1e-6}
                    mb["odor"] = {k: round(v, 6) for k, v in sorted(odor_b.items()) if v > 1e-6}
                if tox_changed:
                    ma["toxic"] = {k: round(v, 4) for k, v in sorted(tox_a.items()) if v > 1e-4}
                    mb["toxic"] = {k: round(v, 4) for k, v in sorted(tox_b.items()) if v > 1e-4}
    # ventilation to outdoors, deposition/decay, outdoor clearing
    for rid in sorted(involved):
        room = rooms[rid]
        a = room.get("_air")
        if not a:
            continue
        vol = C.room_volume_m3(state, rid)
        if room.get("outdoor"):
            k = math.exp(-dt / OUTDOOR_CLEAR_S)
            out_q = None
        else:
            out_q = vol * 0.35 / 3600.0 + vent.get(rid, 0.0) + _exterior_flow(state, rid)
            k = math.exp(-out_q * dt / vol)
        if not _has_any(a):
            continue
        m = air_m(w, rid)
        for key in KEYS:
            value = float(m.get(key, 0.0)) * k
            if key == "smoke":
                value *= math.exp(-dt / SMOKE_DECAY_S)
            m[key] = round(value, 6) if value > EPS[key] * 0.01 else 0.0
        m["odor"] = {cls: round(v * k * math.exp(-dt / ODOR_DECAY_S), 6) for cls, v in sorted((m.get("odor") or {}).items())
                     if v * k > 1e-5}
        m["toxic"] = {n: round(v * k * math.exp(-dt / TOXIC_DECAY_S), 4) for n, v in sorted((m.get("toxic") or {}).items())
                      if v * k > 1e-3}
        dTv = float(m.get("dT", 0.0)) * math.exp(-dt / DT_DECAY_S) * (k if out_q is not None else 0.2)
        m["dT"] = round(dTv, 3) if dTv > 0.01 else 0.0


def _has_any(a):
    return any(float(a.get(k, 0.0)) > 0 for k in KEYS) or bool(a.get("odor")) or bool(a.get("toxic")) or float(a.get("dT", 0.0)) > 0


# ---------------------------------------------------------------------------------------------
# exposure (people, pets): CO uptake (COHb), smoke, toxic vapours, heat

COHB_BASE = 0.8
COHB_SYMPTOMS = 10.0      # headache, nausea (CPSC); counts as gas exposure harm
COHB_SERIOUS = 25.0       # confusion, collapse -> injury


def cohb_tau_s(agent, pet=False):
    if pet:
        return 3600.0
    age = C.age_of(agent)
    if age < 13:
        return 7200.0
    return 10800.0


def expose(w, dt):
    state = w.s
    for group in ("people", "pets"):
        for pid in sorted(state.get(group, {})):
            agent = state[group][pid]
            rid = agent.get("location")
            if rid not in state["rooms"]:
                continue
            a = air(state, rid)
            exp = agent.get("_exposure") or {}
            cohb = float(exp.get("cohb", COHB_BASE))
            ppm = float(a.get("co", 0.0))
            eq = max(COHB_BASE, min(80.0, 0.16 * ppm))
            tau = cohb_tau_s(agent, pet=(group == "pets"))
            new = cohb + (eq - cohb) * (1.0 - math.exp(-dt / tau))
            smoke = float(a.get("smoke", 0.0))
            toxic = sum(float(v) for v in (a.get("toxic") or {}).values())
            dT = float(a.get("dT", 0.0))
            if abs(new - cohb) < 0.005 and smoke < 0.3 and toxic < 1.0 and dT < 40:
                continue
            m = (w.person_m(pid) if group == "people" else w.pet_m(pid))
            exp = dict(m.get("_exposure") or {})
            exp["cohb"] = round(new, 3)
            flags = list(exp.get("flags", []))
            if new >= COHB_SYMPTOMS and "co_symptoms" not in flags:
                flags.append("co_symptoms")
                C.harm(w, "co_poisoning", stat="gas_exposure", who=pid, cohb=round(new, 1))
            if new >= COHB_SERIOUS and "co_serious" not in flags:
                flags.append("co_serious")
                C.harm(w, "co_poisoning_serious", stat="injury", who=pid, cohb=round(new, 1))
            if smoke >= 1.0:
                exp["smoke_s"] = round(float(exp.get("smoke_s", 0.0)) + dt, 1)
                if exp["smoke_s"] >= 60.0 and "smoke_inhalation" not in flags:
                    flags.append("smoke_inhalation")
                    C.harm(w, "smoke_inhalation", stat="injury", who=pid)
            if toxic >= 2.0:
                exp["toxic_s"] = round(float(exp.get("toxic_s", 0.0)) + dt, 1)
                if "toxic_vapour" not in flags:
                    flags.append("toxic_vapour")
                    C.harm(w, "toxic_vapour", stat="gas_exposure", who=pid)
            if dT >= 60.0 and "heat" not in flags:
                flags.append("heat")
                C.harm(w, "heat_exposure", stat="burns", who=pid)
            exp["flags"] = flags
            m["_exposure"] = exp


SOOT_MIN_K = 0.2                 # smoke below this (visibility over ~15 m) leaves no noticeable film on a lens
SOOT_PER_K_S = 2e-4              # lens clarity lost per second per 1/m of smoke: thick smoke (K 3) dirties it in ~10 min


def robot_exposure(w, dt):
    state = w.s
    rid = state["robot"].get("room")
    a = air(state, rid)
    k = float(a.get("smoke", 0.0))
    if k >= SOOT_MIN_K:
        # soot settles on the camera covers (body_faults.degrade_camera; the robot notices a dirty camera below the
        # self-check level and clean_camera restores it)
        body_faults.degrade_camera(w, amount=round(SOOT_PER_K_S * k * dt, 4), cause="smoke")
    dT = float(a.get("dT", 0.0))
    if dT >= 150.0:
        sec = C.ledger(w)
        if not sec["status"].get("robot_heat_damage"):
            sec["status"]["robot_heat_damage"] = True
            C.harm(w, "robot_heat_damage", stat="damage")


HAZE_STEP_K = 0.05               # smoke_density written in 0.05 1/m steps (a write only when the step changes)


def haze_level(state, rid):
    """The room's smoke as the geometric camera's haze: its extinction coefficient K (1/m) in HAZE_STEP_K steps, or
    None below one step."""
    k = float(air(state, rid).get("smoke", 0.0))
    level = round(round(k / HAZE_STEP_K) * HAZE_STEP_K, 3)
    return level if level >= HAZE_STEP_K - 1e-9 else None


def feed_haze(w):
    """Geometric camera only (TIER1_INTEGRATION 6, camera: room smoke feeds k_haze): each room's smoke as its
    `smoke_density` field, which sight.k_haze (images fade with distance through smoke) and the realistic uncertainty
    read. Room mode never writes it, so room-mode outputs stay identical; realistic observations never show it (an
    unlisted room field; the robot perceives smoke through obs.air)."""
    state = w.s
    for rid in sorted(state["rooms"]):
        level = haze_level(state, rid)
        if state["rooms"][rid].get("smoke_density") == level:
            continue
        room = w.room_m(rid)
        if level is None:
            room.pop("smoke_density", None)
        else:
            room["smoke_density"] = level


def haze_stale(state):
    """True when feed_haze would change a room (geometric mode only)."""
    return "view" in state["robot"] and any(state["rooms"][rid].get("smoke_density") != haze_level(state, rid)
                                            for rid in state["rooms"])


SMOKE_SEEN_K = 0.1               # smoke becomes a visible haze (visibility ~30 m): a "smoke" notice in that room
SMOKE_GONE_K = 0.05              # ... and the room counts as clear again below this (hysteresis)


def smoke_sightings(w, rooms):
    """Rooms whose smoke just became visible post a "smoke" notice (seen in the robot's lit room with imaging; the
    geometric camera logs whether its current pose saw the haze, camera_log.events / a "visual" notice). The hidden
    `_air.seen` flag keeps one notice per smoky spell."""
    from . import fire as F
    state = w.s
    for rid in sorted(rooms):
        a = air(state, rid)
        k = float(a.get("smoke", 0.0))
        if k >= SMOKE_SEEN_K and not a.get("seen"):
            air_m(w, rid)["seen"] = True
            fires = F.fires(state, room=rid)
            room = state["rooms"][rid]
            cols, rows = geometry.grid_size(room)
            camera.visual_event(w, "smoke_seen", source=fires[0] if fires else None, room=rid,
                                at=None if fires else [cols // 2, rows // 2],
                                z=max(1.5, float(room.get("ceiling_m", 2.5)) - 0.3), size_m=1.0, visual="smoke", emit=False)
            C.post_notice(w, "smoke", rid, source=fires[0] if fires else None, visual=True)
        elif a.get("seen") and k < SMOKE_GONE_K:
            air_m(w, rid)["seen"] = False


def air_idle(w):
    """Exact idle probe (kernel v3.1 @system idle): the air system would write nothing this substep -- no source
    entity (gas leak, CO source, odour), every room's air clean (no smoke, CO, gas, odour, vapour or hot layer to
    exchange or decay), no smoky spell left to close and, with the geometric camera, no haze to update. Writes
    nothing."""
    state = C.bind_world(w).s
    if source_ids(state) or _active_rooms(state):
        return False
    if any(air(state, rid).get("seen") for rid in state["rooms"]):
        return False
    return not haze_stale(state)


@system("safety.air", order=310, max_dt=30.0, idle=air_idle)
def air_system(w, dt, tick):
    state = C.bind_world(w).s
    sources = source_ids(state)
    active = _active_rooms(state)
    if sources:
        apply_sources(w, dt, sources)
        active = _active_rooms(state)
    if active:
        exchange(w, dt, active)
        expose(w, dt)
        robot_exposure(w, dt)
    smoke_sightings(w, [rid for rid in state["rooms"] if rid in active or air(state, rid).get("seen")])
    if "view" in state["robot"]:
        feed_haze(w)


def describe_smoke(k):
    if k < 0.02:
        return "none"
    if k < 0.1:
        return "faint haze"
    if k < 0.5:
        return "haze"
    if k < 1.5:
        return "light smoke"
    return "thick smoke"


def describe_odor(level):
    if level < 0.15:
        return None
    if level < 0.6:
        return "faint"
    if level < 2.0:
        return "noticeable"
    return "strong"


ODOR_TEXT = {
    "gas": "rotten-egg (mercaptan) odor",
    "smoke": "smoke",
    "burning_food": "burnt food smell",
    "hot_plastic": "hot plastic / electrical burning smell",
    "burning_wood": "wood smoke / burning paper smell",
    "musty": "musty, damp smell",
    "spoiled_food": "rotten food smell",
    "sewer": "sewer gas smell",
    "cooking": "cooking smell",
    "incense": "incense / scented smoke",
    "pet_urine": "urine (ammonia-like) smell",
    "vomit": "sour vomit smell",
    "chlorine_bleach": "sharp chlorine / bleach fumes",
    "solvent": "solvent / gasoline fumes",
    "garbage": "garbage smell",
    "dead_animal": "decay smell",
    "fertilizer": "fertilizer smell",
    "candle": "scented candle",
}


def hour_of_day(state):
    return clock.hour(state)
