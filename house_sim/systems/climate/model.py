"""Whole-house air model: one lumped node per room, advanced every 30 s substep (fidelity P-approx).

Per indoor room (fields on state.rooms[rid]): temperature_c, humidity (RH 0..1), co2_ppm, pm25 (ug/m3),
odor (0..1), pollen (index 0..10), stratification_c (ceiling-floor), air_change_rate_ach (outdoor air now),
fresh_air_changes (cumulative outdoor air volumes since the episode began), dew_point_c, ventilating.

Heat balance (W): envelope UA x (T_out - T) [0.25-0.9 W/K per m2 floor by room type, +0.35 under the roof;
garage 2.5, attic 4, basement 0.2 + 1.2 to 12 C ground], windows U 2.8 W/m2K, infiltration 0.3 ACH,
open windows (single-sided q = A (0.025 v_wind + 0.04 sqrt(|dT|)) m3/s; cross ventilation through two open
facades of a connected zone q = 0.61 A_eff v 0.6, A_eff = (A1^-2 + A2^-2)^-0.5), open interior doors
(0.03 m3/s; 0.002 closed; stairs 0.05), exterior doors open (0.1 m3/s), HVAC supply air (mixing through
the return plus delivered heat/cooling), radiators, mini-splits, space heaters, people (75 W), pets, lights
and appliances, solar gain through windows (SHGC 0.6 after coverings), roof gain in attics.
Capacitance 55 kJ/K per m2 floor. Each balance is integrated exactly per substep (x -> a/b + (x - a/b)e^-bdt).

Moisture: water vapour density with 3x hygric buffering; sources people 55 g/h, showers, humidifiers;
sinks dehumidifiers, the AC coil (removes vapour above its 9.4 g/m3 saturation at 10 C), condensation on
glass below the dew point; exchange with outdoor air and neighbours. CO2 (people 0.0052 L/s), PM2.5
(outdoor AQI, HVAC filter by MERV, purifier CADR, deposition 0.2/h), pollen (outdoor index; screens do not
stop it; HEPA does) and odour (decays with fresh air, carbon filters) follow the same air flows.

Effects each substep: window condensation (glass inner surface below the dew point), rain driven in
through open windows (windward facades, wind > calm), pets at open unscreened windows (escape after 60 s),
upper-floor windows opened past 10 cm with a child at home, pipe freezing below 3 C, pets above 30 C,
HVAC running with windows open in its zone, occupant comfort, glare on screens and viewers, outdoor shade
on watched items, energy ledger.
"""
import math

from ...kernel import geometry, physics
from ...kernel.plugins import SYSTEMS, system
from . import airclean, comfort, fans, heaters, hvac, moisture
from . import common as C
from . import sun as S

ENV_UA = {"hallway": 0.35, "coat_closet": 0.25, "linen_closet": 0.25, "utility_closet": 0.3, "pantry": 0.3, "bathroom": 0.7,
          "half_bath": 0.5, "kitchen": 0.8, "living_room": 0.9, "dining_room": 0.85, "bedroom": 0.9, "master_bedroom": 0.9,
          "guest_room": 0.9, "nursery": 0.9, "office": 0.85, "playroom": 0.9, "gym": 0.9, "craft_room": 0.85, "laundry_room": 0.7,
          "entryway": 0.7, "mudroom": 0.8, "garage": 2.5, "attic": 4.0, "basement": 0.2, "shed": 3.0, "elevator": 0.1,
          "building_corridor": 0.3, "lobby": 0.5, "mail_room": 0.3, "shared_laundry": 0.4}
ROOF_GAIN = {"attic": 0.12, "shed": 0.08, "garage": 0.03}
RAIN_PUDDLE_L = 0.25     # rain driven in through an open window: wet sill below this, a floor puddle (spill) above
GROUND_C = 12.0
CORE_ROOMS_DEFAULT = 600.0


# ---------------------------------------------------------------------------------------------
# entity scan (one pass per substep)

SHOWER_CATS = ("shower", "bathtub", "faucet", "sink", "showerhead")


def _gain_candidate(rec):
    """Static pre-filter for entities that may give off heat (lights, candles, appliances, electronics)."""
    if rec.get("climate_kind"):
        return False
    cat = rec.get("category")
    return cat in C.HEAT_GAIN_CATEGORIES or cat in ("light", "candle", "appliance") or "internal_temp_c" in rec or \
        "appliance" in rec or bool(rec.get("plug_required")) or bool(rec.get("heat_source"))


def _power_candidate(rec):
    return "power_w" in rec or "standby_w" in rec or bool(rec.get("climate_kind"))


def _cover_candidate(rec):
    """Things that can sit over a floor register: path-blocking furniture and rugs."""
    return "blocks_path" in rec or rec.get("category") == "rug"


def step_memo(w):
    """Per-World memo valid for one env step. Within a step (an action plus the time it takes, e.g. a 7-day wait in
    30 s substeps) only systems change the house, and they do not move windows, furniture, lights, TVs or appliances
    between rooms; actions, rollbacks and created/destroyed entities start a new memo. Plain dict states get none."""
    if not hasattr(w, "s"):
        return {}
    key = (w.s.get("steps"), getattr(w, "_version", None), len(w.s["entities"]), id(w.s))
    memo = getattr(w, "_climate_memo", None)
    if memo is None or memo[0] != key:
        memo = (key, {})
        try:
            w._climate_memo = memo
        except AttributeError:
            return {}
    return memo[1]


def _scan_static(w, state):
    """Groupings that only actions change (cached for the step): windows per room, climate devices by kind, metered
    devices, glare targets, shade-watched items, floor-cover candidates, and the entity -> room lookup."""
    rooms = state["rooms"]
    ents = state["entities"]
    rmap = C.RoomIndex(state)
    out = {"room": rmap, "windows": {}, "kind": {}, "kind_ids": [], "power": [], "glare": {}, "shade_watch": [],
           "cover_ids": C.static_ids(w, "climate.cover", _cover_candidate)}
    for eid in C.static_ids(w, "climate.windows", C.is_window):
        rec = ents.get(eid)
        if rec is not None and rec.get("location") in rooms:
            out["windows"].setdefault(rec["location"], []).append(eid)
    # glazing conductance per room, W/K (window size and u_value are fixed properties)
    out["win_ua"] = {rid: sum(C.num(ents[wid].get("u_value"), C.WINDOW_U) * C.window_glass_m2(ents[wid]) for wid in wids)
                     for rid, wids in out["windows"].items()}
    for eid in C.static_ids(w, "climate.kinds", lambda r: bool(r.get("climate_kind"))):
        rec = ents.get(eid)
        if rec is not None:
            out["kind"].setdefault(rec["climate_kind"], []).append(eid)
            out["kind_ids"].append(eid)
    for eid in C.static_ids(w, "climate.power", _power_candidate):
        rec = ents.get(eid)
        if rec is not None and not rec.get("climate_kind") and (rec.get("power_w") is not None or rec.get("standby_w") is not None)                 and rmap.get(eid) is not None:
            out["power"].append(eid)
    for eid in C.static_ids(w, "climate.glare", lambda r: r.get("category") in S.GLARE_CATEGORIES or "glare_watch" in r):
        rec = ents.get(eid)
        if rec is None or not (rec.get("category") in S.GLARE_CATEGORIES or rec.get("glare_watch")):
            continue
        rid = rmap.get(eid)
        if rid is not None:
            out["glare"].setdefault(rid, []).append(eid)
    for eid in C.static_ids(w, "climate.shade_watch", lambda r: "shade_watch" in r):
        rec = ents.get(eid)
        if rec is not None and rec.get("shade_watch") and rmap.get(eid) is not None:
            out["shade_watch"].append(eid)
    return out


def scan(state_or_w):
    """Per-substep view of the house for the climate model, built from static indexes (never a walk over every
    entity): the step-cached groupings of _scan_static plus what changes every substep (device heat gains, hot
    showers, lit candles). ``room`` is a lazy entity -> root room lookup."""
    w = state_or_w
    state = w.s if hasattr(w, "s") else w
    ents = state["entities"]
    memo = step_memo(w)
    static = memo.get("scan")
    if static is None:
        static = _scan_static(w, state)
        memo["scan"] = static
    rmap = static["room"]
    out = dict(static)
    out.update({"gains": {}, "showers": {}, "candles": [], "cover": memo.get("cover"), "memo": memo})
    for eid in C.static_ids(w, "climate.gains", _gain_candidate):
        rec = ents.get(eid)
        if rec is None:
            continue
        gain = _device_gain(rec)
        if gain > 0:
            rid = rmap.get(eid)
            if rid is not None:
                out["gains"][rid] = out["gains"].get(rid, 0.0) + gain
        if rec.get("category") == "candle" and rec.get("lit") and rmap.get(eid) is not None:
            out["candles"].append(eid)
    for eid in C.static_ids(w, "climate.showers", lambda r: r.get("category") in SHOWER_CATS and not r.get("climate_kind")):
        rec = ents.get(eid)
        if rec is None or not rec.get("water_running") or C.num(rec.get("outlet_temp_c", rec.get("water_temp_c")), 20.0) < 35.0:
            continue
        rid = rmap.get(eid)
        if rid is not None:
            out["showers"][rid] = out["showers"].get(rid, 0.0) + (0.6 if rec.get("category") in ("shower", "showerhead", "bathtub") else 0.1)
    return out


def _floor_cover(state, sc, rid):
    """(blockers {cell: eid}, rugs {cell: eid}) for a room; built once per substep for every room that holds a
    floor register or return, from the static index of path-blocking furniture and rugs."""
    if sc["cover"] is None:
        want = set()
        for k in ("register", "return_grille"):
            for r in sc["kind"].get(k, []):
                rec = state["entities"].get(r) or {}
                if rec.get("location") in state["rooms"] and rec.get("pos") and rec.get("register_kind") != "ceiling":
                    want.add(rec["location"])
        cover = {}
        if want:
            ents = state["entities"]
            for eid in sc["cover_ids"]:
                rec = ents.get(eid)
                if rec is None:
                    continue
                loc = rec.get("location")
                if loc not in want or not rec.get("pos"):
                    continue
                blockers, rugs = cover.setdefault(loc, ({}, {}))
                if rec.get("blocks_path"):
                    for c in geometry.footprint(rec):
                        blockers[c] = eid
                if rec.get("category") == "rug":
                    for c in geometry.footprint(rec):
                        rugs[c] = eid
        sc["cover"] = cover
        sc["memo"]["cover"] = cover
    return sc["cover"].get(rid) or ({}, {})


def _device_gain(rec):
    cat = rec.get("category")
    if cat == "light":
        return 12.0 if (rec.get("lit", rec.get("on")) and not rec.get("burnt_out")) else 0.0
    if cat == "candle" and rec.get("lit"):
        return 80.0
    if C.num(rec.get("internal_temp_c"), 0.0) > 60.0:
        return 0.25 * C.num(rec.get("power_w"), 2000.0)
    if cat not in C.HEAT_GAIN_CATEGORIES:
        return 0.0
    active = rec.get("running") or rec.get("heating") or (rec.get("on") and rec.get("power_state", "on") not in ("off", "rest")) or \
        ((rec.get("appliance") or {}).get("state") == "running")
    if cat in ("fridge", "refrigerator", "freezer"):
        return 60.0
    if not active:
        return C.num(rec.get("standby_w"), 0.0)
    return C.num(rec.get("power_w"), 60.0)


# ---------------------------------------------------------------------------------------------
# room helpers

def room_t(state, rid):
    return physics.room_temperature(state, rid)


def room_rh(state, rid):
    room = state["rooms"].get(rid) or {}
    if room.get("outdoor"):
        return C.outdoor_rh(state)
    return C.clamp(C.num(room.get("humidity"), 0.45), 0.01, 1.0)


def _top_floor(state):
    floors = [C.num(r.get("floor"), 0) for r in state["rooms"].values() if not r.get("outdoor") and r.get("type") not in ("attic",)]
    return max(floors) if floors else 0


def _connection_open(state, conn):
    """Open fraction of a connection (0 closed door/hatch .. 1 open doorway)."""
    door = state["entities"].get(conn.get("door") or "")
    if door is not None:
        if door.get("mech") and "open_frac" in (door.get("mech") or {}):
            return C.clamp(C.num(door["mech"].get("open_frac")), 0.0, 1.0)
        return 1.0 if door.get("open") else 0.0
    if conn.get("kind") == "hatch":
        return 0.0
    return 1.0


def _connection_flow(state, conn, dt_c=0.0):
    """Bulk air exchange m3/s through a connection: mixing plus two-way buoyancy flow
    q = Cd W H/3 sqrt(g H dT / T) (= 0.088 sqrt(dT) for a 0.85 x 2 m doorway)."""
    frac = _connection_open(state, conn)
    if frac <= 0.01:
        return C.DOOR_CLOSED_Q
    width = C.num(conn.get("width_m"), 0.85)
    base = C.STAIRS_Q if conn.get("kind") == "stairs" else C.DOOR_OPEN_Q
    buoy = 0.6 * width * (2.0 / 3.0) * math.sqrt(9.81 * 2.0 * abs(dt_c) / 294.0)
    return max(C.DOOR_CLOSED_Q, (base + buoy) * frac)


# ---------------------------------------------------------------------------------------------
# the system

@system("climate.house", order=44, max_dt=30.0)
def climate_system(w, dt, tick):
    state = w.s
    if not state.get("rooms"):
        return
    _diurnal(w)
    az, alt = S.sun_position(state)
    beam = S.dni(state, alt)
    sec = C.sec(w)
    sec["sun"] = {"azimuth_deg": az, "altitude_deg": alt, "dni_w_m2": round(beam, 1)}
    sc = scan(w)
    hv = _hvac_step(w, dt, sc, az, alt)
    dv = _devices_step(w, dt, sc)
    _air_step(w, dt, sc, hv, dv, az, alt, beam)
    _effects_step(w, dt, sc, az, alt)
    _energy_step(w, dt, sc, hv)


def _diurnal(w):
    cfg = (C.section(w.s).get("outdoor") or {}).get("diurnal")
    if not isinstance(cfg, dict):
        return
    lo, hi = C.num(cfg.get("min_c"), 12.0), C.num(cfg.get("max_c"), 24.0)
    h = C.hour(w.s)
    # minimum ~06:00, maximum ~15:00 (asymmetric cosine)
    if 6.0 <= h < 15.0:
        frac = (1 - math.cos(math.pi * (h - 6.0) / 9.0)) / 2.0
    else:
        hh = h - 15.0 if h >= 15.0 else h + 9.0
        frac = (1 + math.cos(math.pi * hh / 15.0)) / 2.0
    t = round(lo + (hi - lo) * frac, 2)
    if abs(C.num((w.s.get("weather") or {}).get("temperature_c")) - t) > 0.01:
        w.sec("weather")["temperature_c"] = t


# ---------------------------------------------------------------------------------------------
# HVAC

def _hvac_step(w, dt, sc, az, alt):
    state = w.s
    out = {"room_q": {}, "room_flow": {}, "room_latent": {}, "mix": {}, "elec_w": 0.0, "gas_w": 0.0, "filter_eff": {}, "systems": {}}
    systems = sc["kind"].get("hvac_system", [])
    thermos = sc["kind"].get("thermostat", [])
    t_out = C.outdoor_temp(state)
    calls = {}
    default_sid = systems[0] if systems else None
    for tid in thermos:
        rid = sc["room"].get(tid)
        if rid is None:
            continue
        t = room_t(state, rid) - 0.1 * C.num((state["rooms"].get(rid) or {}).get("stratification_c"))
        rid_, x, y = C.entity_xy(state, tid)
        bias = 0.0
        if rid in sc["windows"] and alt > 0.5:
            b, _ = S.beam_at(state, rid, x, y, C.num(state["entities"][tid].get("mount_height_m"), 1.5), az, alt, sc["windows"].get(rid, []),
                            sc["memo"].setdefault("apertures", {}))
            bias = 2.0 * b
        call = hvac.thermostat_tick(w, tid, t, bias)
        sid = state["entities"][tid].get("controls_hvac") or default_sid
        calls.setdefault(sid, []).append((tid, call))
    for sid in systems:
        rec = state["entities"][sid]
        cl = [c for _, c in calls.get(sid, [])]
        info = hvac.system_tick(w, sid, cl, dt, t_out)
        out["systems"][sid] = info
        if info["stage"] in ("heat", "cool", "aux", "fan"):
            C.tracker(w, "{}_run_s:{}".format(info["stage"], sid), dt)
        out["elec_w"] += info["elec_w"]
        out["gas_w"] += info["gas_w"]
        if info["boiler"]:
            _radiators(w, dt, sc, sid, info, out)
        else:
            _ducted(w, dt, sc, sid, info, out, calls.get(sid, []))
        _behind(w, dt, sid, info, calls.get(sid, []))
        if C.num(state["entities"][sid].get("power_w"), -1) != round(info["elec_w"], 1):
            w.mut(sid)["power_w"] = round(info["elec_w"], 1)
    for uid in sc["kind"].get("mini_split", []):
        _mini_split(w, dt, sc, uid, out, t_out)
    for cid in sc["kind"].get("outdoor_condenser", []):
        sid = state["entities"][cid].get("serves")
        running = bool(sid and out["systems"].get(sid, {}).get("stage") in ("cool",) or
                       (sid and state["entities"].get(sid, {}).get("system_kind") == "heat_pump" and out["systems"].get(sid, {}).get("stage") == "heat"))
        if bool(state["entities"][cid].get("running")) != running:
            w.mut(cid)["running"] = running
    return out


def _behind(w, dt, sid, info, tcalls):
    rec = w.ent(sid)
    if rec.get("system_kind") != "heat_pump":
        return
    behind = C.num(rec.get("behind_s"))
    lagging = False
    if info["stage"] == "heat":
        for tid, call in tcalls:
            t = w.ent(tid)
            heat_c, _ = hvac.setpoints_c(t)
            if C.num(t.get("reading_c"), heat_c) < heat_c - 1.0:
                lagging = True
    new = behind + dt if lagging else 0.0
    if abs(new - behind) > 1e-6:
        w.mut(sid)["behind_s"] = round(new, 1)


def _register_system(state, rec, default):
    return rec.get("system") or default


def _zones(state, tcalls):
    """Rooms that may receive conditioned air: None = all (single thermostat or no zoning)."""
    zoned = [(tid, call) for tid, call in tcalls if state["entities"][tid].get("zone_rooms")]
    if len(zoned) < 2:
        return None
    rooms = set()
    for tid, call in zoned:
        if call in ("heat", "cool", "aux", "fan"):
            rooms.update(state["entities"][tid]["zone_rooms"])
    return rooms


def _ducted(w, dt, sc, sid, info, out, tcalls):
    state = w.s
    rec = state["entities"][sid]
    regs = [r for r in sc["kind"].get("register", []) if _register_system(state, state["entities"][r], None) in (None, sid)]
    zone = _zones(state, tcalls)
    design_total = sum(C.num(state["entities"][r].get("design_flow_m3s"), 0.05) for r in regs) or 0.5
    rated = C.num(rec.get("rated_flow_m3s"), design_total)
    weights = {}
    closed_rooms = _closed_rooms(state, sc)
    for r in regs:
        rr = state["entities"][r]
        rid = sc["room"].get(r)
        damper = C.clamp(C.num((rr.get("settings") or {}).get("damper"), 1.0), 0.0, 1.0)
        blocked = _register_blocked(state, sc, r, rr, rid)
        eff = damper * (0.25 if blocked else 1.0)
        if rid in closed_rooms:
            eff *= 0.4   # a closed room without a return pressurises: supply air can only leak out under the door
        if zone is not None and rid not in zone:
            eff = 0.0
        weights[r] = (C.num(rr.get("design_flow_m3s"), 0.05) * eff, blocked, rid, damper)
    open_share = sum(v[0] for v in weights.values()) / design_total if design_total > 0 else 0.0
    closed_frac = 1.0 - sum(C.num(state["entities"][r].get("design_flow_m3s"), 0.05) * C.clamp(C.num((state["entities"][r].get("settings") or {}).get("damper"), 1.0), 0.0, 1.0)
                            for r in regs) / design_total if regs else 0.0
    ffac, bypass, wrapped, missing = hvac.filter_state(w, sid)
    frozen = rec.get("fault") == "coil_frozen"
    flow_total = rated * math.sqrt(max(0.0, open_share)) * ffac * (0.4 if frozen else 1.0) if info["blower"] else 0.0
    ratio = flow_total / rated if rated > 0 else 0.0
    sp = "normal" if ratio >= 0.8 or not info["blower"] else ("high" if ratio >= 0.6 else "very high")
    changes = {"airflow_ratio": round(ratio, 3) if info["blower"] else 0.0, "static_pressure": sp, "closed_supply_frac": round(closed_frac, 3)}
    m = None
    stage = info["stage"]
    heat_w, cool_w = info["heat_w"], info["cool_w"]
    if info["blower"] and stage == "cool" and ratio < 0.55:
        changes["strain_s"] = round(C.num(rec.get("strain_s")) + dt, 1)
        if changes["strain_s"] >= 1200.0 and not frozen:
            changes["fault"] = "coil_frozen"
            w.stat("damage")
            C.log(w, "coil_frozen", target=sid, airflow_ratio=round(ratio, 2))
    elif info["blower"] and stage == "heat" and rec.get("system_kind") == "furnace_ac" and ratio < 0.6:
        heat_w *= 0.5
        info["gas_w"] *= 0.5
        changes["strain_s"] = round(C.num(rec.get("strain_s")) + dt, 1)
        trips = int(changes["strain_s"] // 600)
        if trips > int(C.num(rec.get("limit_trips"))):
            changes["limit_trips"] = trips
            C.log(w, "high_limit_trip", target=sid, trips=trips)
            if trips >= 5 and not rec.get("heat_exchanger_stressed"):
                changes["heat_exchanger_stressed"] = True
                w.stat("damage")
    if closed_frac > 0.3 and info["blower"]:
        C.hazard(w, "static_pressure_s", dt)
    if info["blower"]:
        if missing:
            C.hazard(w, "hvac_no_filter_s", dt)
            changes["duct_dust"] = round(min(1.0, C.num(rec.get("duct_dust")) + dt / 36000.0), 4)
        elif bypass:
            C.hazard(w, "hvac_filter_bypass_s", dt)
            changes["duct_dust"] = round(min(1.0, C.num(rec.get("duct_dust")) + dt / 180000.0), 4)
        elif wrapped:
            C.hazard(w, "hvac_filter_wrapped_s", dt)
    if any(rec.get(k) != v for k, v in changes.items()):
        w.mut(sid).update(changes)
    total_w = sum(v[0] for v in weights.values())
    fid = ((rec.get("slots") or {}).get("filter") or {}).get("item")
    eff_filter = 0.0
    if fid and fid in state["entities"] and not missing:
        eff_filter = airclean.merv_efficiency(state["entities"][fid].get("filter_spec")) * (0.5 if bypass else 1.0)
    air_room = sc["room"].get(sid)
    if info["blower"] and flow_total > 0 and total_w > 0:
        for r in sorted(weights):
            wt, blocked, rid, damper = weights[r]
            share = wt / total_w
            f = flow_total * share
            rr = state["entities"][r]
            area = C.num(rr.get("free_area_m2"), 0.02)
            v = round(f / area if area > 0 else 0.0, 2)
            if rr.get("airflow_mps") != v or bool(rr.get("blowing")) != (f > 0.005):
                w.mut(r).update({"airflow_mps": v, "blowing": f > 0.005})
            if rid is None or f <= 0:
                continue
            out["room_flow"][rid] = out["room_flow"].get(rid, 0.0) + f
            out["room_q"][rid] = out["room_q"].get(rid, 0.0) + (heat_w * 0.97 - cool_w * 0.95) * share
            out["filter_eff"][rid] = eff_filter
            if stage == "cool" and cool_w > 0:
                out["room_latent"][rid] = out["room_latent"].get(rid, 0.0) + f * 0.6
        if air_room and heat_w > 0:
            # casing / duct losses warm the equipment room a little (none credited in cooling)
            out["room_q"][air_room] = out["room_q"].get(air_room, 0.0) + min(400.0, heat_w * 0.03)
        returns = [g for g in sc["kind"].get("return_grille", []) if _register_system(state, state["entities"][g], None) in (None, sid)]
        open_returns = [g for g in returns if not _register_blocked(state, sc, g, state["entities"][g], sc["room"].get(g))]
        for g in open_returns:
            rid = sc["room"].get(g)
            if rid is not None:
                # return air drawn from this room is replaced by air from the rest of the house
                out["room_flow"][rid] = out["room_flow"].get(rid, 0.0) + 0.5 * flow_total / len(open_returns)
        if returns and not open_returns:
            C.hazard(w, "returns_blocked_s", dt)
        if fid and fid in state["entities"] and not state["entities"][fid].get("shrink_wrapped"):
            pm = sum(C.num((state["rooms"].get(rid) or {}).get("pm25"), 8.0) for rid in out["room_flow"]) / max(1, len(out["room_flow"]))
            rate = dt / (90 * 86400.0) * (pm + 10.0) / 25.0 * ratio
            fm = w.mut(fid)
            fm["dirtiness"] = round(min(1.0, C.num(fm.get("dirtiness")) + rate), 6)
    else:
        for r in regs:
            rr = state["entities"][r]
            if rr.get("blowing") or C.num(rr.get("airflow_mps")) != 0.0:
                w.mut(r).update({"airflow_mps": 0.0, "blowing": False})
    for r in regs:
        _vent_issues(w, r, weights[r][1])
    for g in sc["kind"].get("return_grille", []):
        if _register_system(state, state["entities"][g], None) in (None, sid):
            _vent_issues(w, g, _register_blocked(state, sc, g, state["entities"][g], sc["room"].get(g)))


def _closed_rooms(state, sc):
    """Rooms whose every connection is closed and that hold no return grille (supply air has no way back)."""
    returns = {sc["room"].get(g) for g in sc["kind"].get("return_grille", [])}
    open_rooms = set()
    for conn in state.get("connections", {}).values():
        if _connection_open(state, conn) > 0.05:
            open_rooms.update(conn["rooms"])
    return {r for r in state["rooms"] if r not in open_rooms and r not in returns}


def _register_blocked(state, sc, r, rr, rid):
    if rr.get("relation") in ("under", "behind"):
        return True
    if rr.get("register_kind") == "ceiling" or rid is None or not rr.get("pos") or rr.get("location") != rid:
        return False
    cell = tuple(rr["pos"])
    blockers, rugs = _floor_cover(state, sc, rid)
    if cell in rugs:
        return True
    blocker = blockers.get(cell)
    if blocker and blocker != r:
        return True
    return False


def _vent_issues(w, r, blocked):
    rr = w.ent(r)
    issues = []
    damper = C.num((rr.get("settings") or {}).get("damper"), 1.0) if C.kind(rr) == "register" else 1.0
    if damper <= 0.05:
        issues.append("closed")
    elif damper < 0.95:
        issues.append("partly_closed")
    if blocked:
        issues.append("blocked")
    if C.num((rr.get("soil") or {}).get("dust")) >= 0.3:
        issues.append("dusty")
    if rr.get("broken") or rr.get("damaged"):
        issues.append("damaged")
    issues = sorted(issues)
    rep = (w.s.get("reports") or {}).get(r)
    matches = isinstance(rep, dict) and "issues" in rep and sorted(rep.get("issues") or []) == issues
    if bool(rr.get("report_matches")) != matches:
        w.mut(r)["report_matches"] = matches
    blocker = None
    if blocked:
        blocker = rr.get("location") if rr.get("relation") in ("under", "behind") else "furniture_or_rug"
    if rr.get("vent_issues") != issues or rr.get("blocked") != bool(blocked):
        w.mut(r).update({"vent_issues": issues, "blocked": bool(blocked)})


def _radiators(w, dt, sc, sid, info, out):
    state = w.s
    firing = info["stage"] == "heat" and info["heat_w"] > 0
    cap_left = info["heat_w"]
    used = 0.0
    for r in sc["kind"].get("radiator", []):
        rr = state["entities"][r]
        if _register_system(state, rr, sid) != sid:
            continue
        rid = sc["room"].get(r)
        if rid is None:
            continue
        t = room_t(state, rid)
        trv = int(round(C.num((rr.get("settings") or {}).get("trv"), 3)))
        target = hvac.TRV_TARGET_C.get(max(0, min(5, trv)), 20.0)
        taper = C.clamp((target + 1.0 - t) / 2.0, 0.0, 1.0)
        air = C.clamp(C.num(rr.get("_air_frac")), 0.0, 1.0)
        q = C.num(rr.get("nominal_w"), 1200.0) * taper * (1.0 - air) if firing else 0.0
        q = min(q, max(0.0, cap_left - used))
        used += q
        out["room_q"][rid] = out["room_q"].get(rid, 0.0) + q
        surf_target = (25.0 + 45.0 * (1.0 - air) * max(0.3, taper)) if firing else t
        cur = C.num(rr.get("temperature_c"), t)
        new = surf_target + (cur - surf_target) * math.exp(-dt / (600.0 if firing else 1800.0))
        changes = {"heating_on": firing, "heat_output_frac": round(1.0 - air, 3), "output_w": round(q, 1)}
        if abs(new - cur) > 0.05:
            changes["temperature_c"] = round(new, 2)
        if any(rr.get(k) != v for k, v in changes.items()):
            w.mut(r).update(changes)
    if firing and info["heat_w"] > 0:
        scale = used / info["heat_w"]
        out["gas_w"] -= info["gas_w"] * (1.0 - scale)
        info["gas_w"] *= scale


def _mini_split(w, dt, sc, uid, out, t_out):
    state = w.s
    rec = state["entities"][uid]
    rid = sc["room"].get(uid)
    if rid is None:
        return
    s = rec.get("settings") or {}
    units = s.get("units", "C")
    sp = C.to_c(s.get("setpoint", 24.0 if units == "C" else 75), units)
    mode = s.get("mode", "off")
    t = room_t(state, rid) + (1.0 if mode == "heat" else 0.0)
    powered = physics.is_powered(w, uid)
    prev = rec.get("call", "idle")
    call = "idle"
    if powered and mode == "heat" and (t <= sp - 0.5 or (prev == "heat" and t < sp + 0.5)):
        call = "heat"
    elif powered and mode in ("cool", "dry") and (t >= sp + 0.5 or (prev in ("cool", "dry") and t > sp - 0.5)):
        call = "cool" if mode == "cool" else "dry"
    elif powered and mode == "auto":
        if t <= sp - 1.0 or (prev == "heat" and t < sp + 0.5):
            call = "heat"
        elif t >= sp + 1.0 or (prev == "cool" and t > sp - 0.5):
            call = "cool"
    elif powered and mode == "fan_only":
        call = "fan"
    q = 0.0
    elec = 0.0
    err = abs(sp - t)
    if call == "heat":
        q = C.num(rec.get("heating_capacity_w"), 4000.0) * C.clamp(err / 2.0 + 0.25, 0.25, 1.0) * C.clamp(0.75 + 0.015 * t_out, 0.4, 1.1)
        elec = q / C.clamp(3.2 + 0.08 * t_out, 1.6, 4.8)
    elif call in ("cool", "dry"):
        q = -C.num(rec.get("cooling_capacity_w"), 3500.0) * (C.clamp(err / 2.0 + 0.25, 0.25, 1.0) if call == "cool" else 0.3)
        elec = -q / C.clamp(4.0 - 0.04 * max(0.0, t_out - 25.0), 2.5, 4.5)
        flow = 0.15 if call == "cool" else 0.12
        out["room_latent"][rid] = out["room_latent"].get(rid, 0.0) + flow * (0.6 if call == "cool" else 0.9)
    elif call == "fan":
        elec = 25.0
    if call != "idle":
        elec += 25.0
    out["room_q"][rid] = out["room_q"].get(rid, 0.0) + q
    out["elec_w"] += elec
    changes = {"call": call, "running": call != "idle", "power_w": round(elec, 1)}
    if any(rec.get(k) != v for k, v in changes.items()):
        w.mut(uid).update(changes)


# ---------------------------------------------------------------------------------------------
# devices

def _devices_step(w, dt, sc):
    state = w.s
    out = {"q": {}, "vapour": {}, "cadr": {}, "carbon": {}, "fans": {}, "window_fans": [], "heaters": {}}
    for eid in sc["kind"].get("ceiling_fan", []):
        fans.ceiling_tick(w, eid, dt)
        rid = sc["room"].get(eid)
        rec = state["entities"][eid]
        if rid and C.num(rec.get("rpm")) >= 5.0:
            out["fans"].setdefault(rid, []).append(eid)
            out["q"][rid] = out["q"].get(rid, 0.0) + C.num(rec.get("power_w"))
    for eid in sc["kind"].get("portable_fan", []):
        running = fans.portable_tick(w, eid)
        rid = sc["room"].get(eid)
        if rid and running:
            out["fans"].setdefault(rid, []).append(eid)
            out["q"][rid] = out["q"].get(rid, 0.0) + C.num(state["entities"][eid].get("power_w"))
            wid, flow = fans.window_fan_flow(state, eid, sc["windows"].get(rid, []))
            if wid:
                out["window_fans"].append((rid, wid, flow, eid))
    for eid in sc["kind"].get("space_heater", []):
        rid = sc["room"].get(eid)
        q = heaters.heater_tick(w, eid, dt, room_t(state, rid) if rid else 20.0)
        if rid and q > 0:
            out["q"][rid] = out["q"].get(rid, 0.0) + q
            out["heaters"].setdefault(rid, []).append(eid)
    for eid in sc["kind"].get("humidifier", []):
        rid = sc["room"].get(eid)
        if rid is None:
            continue
        g, q = moisture.humidifier_tick(w, eid, dt, room_rh(state, rid), room_t(state, rid))
        out["vapour"][rid] = out["vapour"].get(rid, 0.0) + g
        out["q"][rid] = out["q"].get(rid, 0.0) + q
    for eid in sc["kind"].get("dehumidifier", []):
        rid = sc["room"].get(eid)
        if rid is None:
            continue
        rec = state["entities"][eid]
        if rec.get("on"):
            intake = sc["memo"].setdefault("intake", {})
            if eid not in intake:
                intake[eid] = moisture.intake_clearance(state, eid)   # furniture moves only by actions (step memo)
            blocked = intake[eid]
            if bool(rec.get("intake_blocked")) != blocked:
                w.mut(eid)["intake_blocked"] = blocked
        g, q = moisture.dehumidifier_tick(w, eid, dt, room_rh(state, rid), room_t(state, rid))
        out["vapour"][rid] = out["vapour"].get(rid, 0.0) - g
        out["q"][rid] = out["q"].get(rid, 0.0) + q
    for eid in sc["kind"].get("air_purifier", []):
        rid = sc["room"].get(eid)
        if rid is None:
            continue
        cadr, carbon = airclean.purifier_tick(w, eid, dt, C.num((state["rooms"].get(rid) or {}).get("pm25"), 8.0))
        out["cadr"][rid] = out["cadr"].get(rid, 0.0) + cadr
        out["carbon"][rid] = out["carbon"].get(rid, 0.0) + (cadr if carbon else 0.0)
        out["q"][rid] = out["q"].get(rid, 0.0) + C.num(state["entities"][eid].get("power_w"))
    for eid in sc["kind"].get("hygrometer", []):
        rid = sc["room"].get(eid)
        if rid is None:
            continue
        units = C.household_units(state)
        t = room_t(state, rid)
        rh_pct = int(round(room_rh(state, rid) * 100))
        shown_t = round(C.from_c(t, units)) if units == "F" else round(t, 1)
        text = "{}% RH  {}°{}".format(rh_pct, shown_t, units)
        hrec = state["entities"][eid]
        if hrec.get("display") != text or hrec.get("rh_pct") != rh_pct:
            # the same digits as numbers (read-tier): what reading the instrument gives, at its 1 % / 0.1 degree resolution
            m = w.mut(eid)
            m["display"], m["rh_pct"], m["temp_display"] = text, rh_pct, shown_t
    return out


# ---------------------------------------------------------------------------------------------
# air physics

def _zones_open(state, indoor, flows):
    parent = {r: r for r in indoor}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for (a, b), q in sorted(flows.items()):
        if q >= C.DOOR_OPEN_Q * 0.5 and a in parent and b in parent:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)
    zones = {}
    for r in sorted(indoor):
        zones.setdefault(find(r), []).append(r)
    return zones


_ROOM_PARAMS = {}


def _f(value, default):
    """Fast float read for room fields (they are floats once the model has written them)."""
    return value if type(value) is float else C.num(value, default)


def _room_params(room, rid, top):
    """(area m2, volume m3, capacitance J/K, envelope UA W/K, ground UA W/K, ceiling m): a pure function of the
    room's static fields (memoized)."""
    rtype = room.get("type")
    size = room.get("size_m") or (4.0, 4.0)
    key = (rid, rtype, room.get("floor"), tuple(size[:2]), room.get("ceiling_m"), top)
    hit = _ROOM_PARAMS.get(key)
    if hit is None:
        area, vol = C.room_area(room), C.room_volume(room)
        cap = max(150000.0, C.ROOM_C_PER_M2 * area)
        env = ENV_UA.get(rtype, 0.8) * area
        if C.num(room.get("floor"), 0) >= top and rtype not in ("attic", "basement", "garage", "shed"):
            env += 0.35 * area
        ground = 1.2 * area if rtype == "basement" else 0.0
        ceiling = C.num(room.get("ceiling_m"), C.DEFAULT_CEILING_M) or C.DEFAULT_CEILING_M
        hit = (area, vol, cap, env, ground, ceiling)
        if len(_ROOM_PARAMS) > 4096:
            _ROOM_PARAMS.clear()
        _ROOM_PARAMS[key] = hit
    return hit


def _air_step(w, dt, sc, hv, dv, az, alt, beam):
    state = w.s
    rooms = state["rooms"]
    ents = state["entities"]
    indoor = sorted(r for r, room in rooms.items() if not room.get("outdoor"))
    if not indoor:
        return
    t_out = C.outdoor_temp(state)
    rh_out = C.outdoor_rh(state)
    ah_out = C.abs_humidity(t_out, rh_out)
    wx = state.get("weather") or {}
    wind = C.num(wx.get("wind_mps"), 2.0)
    pm_out = C.aqi_to_pm25(wx.get("aqi", 35))
    pollen_out = C.num(wx.get("pollen"), 2.0)
    top = _top_floor(state)
    temps = {rid: room_t(state, rid) for rid in rooms}
    # inter-room flows
    flows = {}
    ext_q = {}
    for cid in sorted(state.get("connections", {})):
        conn = state["connections"][cid]
        a, b = conn["rooms"]
        if a not in rooms or b not in rooms:
            continue
        q = _connection_flow(state, conn, temps[a] - temps[b])
        oa, ob = rooms[a].get("outdoor"), rooms[b].get("outdoor")
        if oa and ob:
            continue
        if oa or ob:
            inside = b if oa else a
            if q > C.DOOR_CLOSED_Q * 1.5:
                ext_q[inside] = ext_q.get(inside, 0.0) + 0.1 + 0.02 * wind
            continue
        key = (a, b) if a < b else (b, a)
        flows[key] = flows.get(key, 0.0) + q
    # windows: single-sided flows and areas per room
    win_q, win_open = {}, {}
    areas = sc.setdefault("win_area", {})
    for rid in indoor:
        wins = sc["windows"].get(rid)
        if not wins:
            continue
        t = temps[rid]
        for wid in wins:
            wrec = ents[wid]
            area = areas[wid] = C.window_open_area(wrec)
            if area <= 0.005:
                continue
            q = area * (0.025 * wind + 0.04 * math.sqrt(abs(t - t_out) + 0.1))
            win_q[rid] = win_q.get(rid, 0.0) + q
            win_open.setdefault(rid, []).append((wid, area, C.window_facing(wrec)))
    # cross ventilation per connected zone
    cross = {}
    zones = _zones_open(state, indoor, flows)
    if win_open:
        for root, members in sorted(zones.items()):
            opens = [(rid, wid, area, facing) for rid in members for wid, area, facing in win_open.get(rid, []) if facing is not None]
            best, pair = 0.0, None
            for i in range(len(opens)):
                for j in range(i + 1, len(opens)):
                    fi, fj = opens[i][3], opens[j][3]
                    if abs(((fi - fj) + 180.0) % 360.0 - 180.0) < 90.0:
                        continue
                    aeff = 1.0 / math.sqrt(1.0 / opens[i][2] ** 2 + 1.0 / opens[j][2] ** 2)
                    if aeff > best:
                        best, pair = aeff, (opens[i], opens[j])
            if pair:
                v = max(0.6, 0.6 * wind)
                q = 0.61 * best * v * 0.6 + 0.05 * best * math.sqrt(abs(temps[pair[0][0]] - t_out) + 0.1)
                for rid in members:
                    share = q if rid in (pair[0][0], pair[1][0]) else 0.2 * q
                    cross[rid] = cross.get(rid, 0.0) + share
    forced = {}
    for rid, wid, flow, fid in dv["window_fans"]:
        forced[rid] = forced.get(rid, 0.0) + abs(flow)
        for other in next((m for m in zones.values() if rid in m), []):
            if other != rid and win_q.get(other):
                forced[other] = forced.get(other, 0.0) + 0.5 * abs(flow)
    # pass 1: old values (new values are computed from old neighbour values)
    # tuple slots: 0 t, 1 vapour g/m3, 2 co2, 3 pm2.5, 4 odour, 5 pollen, 6 stratification
    old = {}
    for rid in indoor:
        room = rooms[rid]
        t = temps[rid]
        rh = C.clamp(_f(room.get("humidity"), 0.45), 0.01, 1.0)
        old[rid] = (t, C.abs_humidity(t, rh), _f(room.get("co2_ppm"), CORE_ROOMS_DEFAULT), _f(room.get("pm25"), 0.5 * pm_out),
                    _f(room.get("odor"), 0.0), _f(room.get("pollen"), 0.0), _f(room.get("stratification_c"), 0.0))
    neigh = {r: [] for r in indoor}
    for (a, b), q in sorted(flows.items()):
        if a in neigh and b in neigh:
            neigh[a].append((b, q))
            neigh[b].append((a, q))
    rf = hv["room_flow"]
    mix = None
    if rf:
        tot = sum(rf.values())
        mix = [0.0] * 6
        if tot > 0:
            for i in range(6):
                mix[i] = sum(rf[r] * old[r][i] for r in rf if r in old) / tot
    people_by_room, pets_by_room = {}, {}
    for pid in sorted(state.get("people", {})):
        p = state["people"][pid]
        if p.get("away") or p.get("location") not in old:
            continue
        people_by_room.setdefault(p["location"], []).append(p)
    for pid in sorted(state.get("pets", {})):
        p = state["pets"][pid]
        if p.get("location") in old and not p.get("escaped"):
            pets_by_room.setdefault(p["location"], []).append(p)
    diffuse = S.diffuse_vertical(state, alt) if alt > 0 else 0.0
    roof_sun = (beam * math.sin(math.radians(alt)) + 100.0 * S.sky_factor(state)) if alt > 0 else 0.0
    hroom_q, droom_q, gains_scan = hv["room_q"], dv["q"], sc["gains"]
    room_latent, filter_eff = hv["room_latent"], hv["filter_eff"]
    vapour, cadr_by, carbon_by = dv["vapour"], dv["cadr"], dv["carbon"]
    showers = sc["showers"]
    fan_rooms = dv["fans"]
    rho_cp = C.RHO_CP
    exp = math.exp
    new = {}
    for rid in indoor:
        room = rooms[rid]
        o_t, o_ah, o_co2, o_pm, o_odor, o_pollen, o_strat = old[rid]
        rtype = room.get("type")
        area, vol, cap, env, ground, ceiling = _room_params(room, rid, top)
        wins = sc["windows"].get(rid) or ()
        win_ua = sc["win_ua"].get(rid, 0.0)
        q_out = C.INFILTRATION_ACH * vol / 3600.0 + win_q.get(rid, 0.0) + cross.get(rid, 0.0) + forced.get(rid, 0.0) + ext_q.get(rid, 0.0)
        # gains
        gains = hroom_q.get(rid, 0.0) + droom_q.get(rid, 0.0) + gains_scan.get(rid, 0.0)
        people = people_by_room.get(rid, ())
        n_asleep = 0
        for p in people:
            if not p.get("awake", True) or p.get("posture") == "sleeping":
                n_asleep += 1
        n_awake = len(people) - n_asleep
        gains += 45.0 * n_asleep + C.PERSON_SENSIBLE_W * n_awake
        pets = pets_by_room.get(rid, ())
        for p in pets:
            gains += C.PET_W.get(p.get("species", "dog"), 20.0)
        if alt > 0:
            for wid in wins:
                gains += S.solar_gain_w(state, ents[wid], az, alt, beam, diffuse)
            if rtype in ROOF_GAIN:
                gains += ROOF_GAIN[rtype] * roof_sun * area
        nb_list = neigh[rid]
        f_hvac = rf.get(rid, 0.0)
        # temperature
        ua_out = env + win_ua + rho_cp * q_out
        b = ua_out + ground
        a = ua_out * t_out + ground * GROUND_C + gains
        for nb, q in nb_list:
            b += rho_cp * q
            a += rho_cp * q * old[nb][0]
        if f_hvac > 0:
            b += rho_cp * f_hvac
            a += rho_cp * f_hvac * mix[0]
        t_new = _relax(o_t, a, b, cap, dt)
        # moisture (g/m3), CO2, particles, pollen, odour share the same air flows
        src = len(people) * C.PERSON_VAPOUR_G_H / 3600.0 + vapour.get(rid, 0.0) + showers.get(rid, 0.0)
        bh = q_out
        ah_ = q_out * ah_out + src
        co2_src = (n_asleep * 0.004 + n_awake * C.PERSON_CO2_LPS) / 1000.0 * 1e6 + len(pets) * 0.001 / 1000.0 * 1e6
        bc, ac = q_out, q_out * C.OUTDOOR_CO2_PPM + co2_src
        bp, ap = q_out + vol * 0.2 / 3600.0, q_out * pm_out
        bl, al = q_out + vol * 1.0 / 3600.0, q_out * pollen_out
        bo, ao = q_out + vol / 36000.0, 0.0
        for nb, q in nb_list:
            ob = old[nb]
            bh += q
            ah_ += q * ob[1]
            bc += q
            ac += q * ob[2]
            bp += q
            ap += q * ob[3]
            bo += q
            ao += q * ob[4]
            bl += q
            al += q * ob[5]
        if f_hvac > 0:
            fe = filter_eff.get(rid, 0.0)
            bh += f_hvac
            ah_ += f_hvac * mix[1]
            bc += f_hvac
            ac += f_hvac * mix[2]
            bp += f_hvac
            ap += f_hvac * mix[3] * (1.0 - fe)
            bo += f_hvac
            ao += f_hvac * mix[4]
            bl += f_hvac
            al += f_hvac * mix[5] * (1.0 - min(1.0, fe * 1.3))
        latent = room_latent.get(rid, 0.0)
        if latent > 0:
            # the coil leaves supply air saturated at ~10 C (9.4 g/m3): removal ~ flow x excess x bypass factor
            bh += latent
            ah_ += latent * min(o_ah, 9.4)
        ah_new = _relax(o_ah, ah_, bh, vol * C.MOISTURE_BUFFER, dt)
        sat = C.abs_humidity(t_new, 1.0)
        if ah_new > sat:
            ah_new = sat
        rh_new = C.clamp(C.rel_humidity(t_new, ah_new), 0.02, 1.0)
        cadr = cadr_by.get(rid, 0.0) / 3600.0
        bp += cadr
        bl += cadr
        bo += carbon_by.get(rid, 0.0) / 3600.0 * 0.5
        co2_new = _relax(o_co2, ac, bc, vol, dt)
        pm_src = room.get("pm_source_ug_s")
        pm_new = _relax(o_pm, ap + (C.num(pm_src) if pm_src is not None else 0.0), bp, vol, dt)
        pollen_new = _relax(o_pollen, al, bl, vol, dt)
        od_src = room.get("odor_source_per_h")
        odor_new = _relax(o_odor, ao + (C.num(od_src) / 3600.0 * vol if od_src is not None else 0.0), bo, vol, dt)
        # stratification: heat delivered builds a warm layer under the ceiling; any running ceiling fan mixes it
        heating = (hroom_q.get(rid, 0.0) + droom_q.get(rid, 0.0)) > 300.0
        strat_target = (1.0 + 0.8 * max(0.0, ceiling - 2.4)) if heating else 0.4
        for f in fan_rooms.get(rid, ()):
            if ents[f].get("climate_kind") == "ceiling_fan":
                strat_target = 0.2
                break
        strat_new = strat_target + (o_strat - strat_target) * exp(-dt / 1800.0)
        ach = q_out * 3600.0 / vol
        fresh = _f(room.get("fresh_air_changes"), 0.0) + q_out * dt / vol
        new[rid] = (("temperature_c", round(t_new, 3)), ("humidity", round(rh_new, 4)), ("co2_ppm", round(co2_new, 1)),
                    ("pm25", round(pm_new, 2)), ("pollen", round(pollen_new, 3)), ("odor", round(odor_new, 4)),
                    ("stratification_c", round(strat_new, 2)), ("air_change_rate_ach", round(ach, 2)),
                    ("fresh_air_changes", round(fresh, 3)), ("dew_point_c", round(C.dew_point_c(t_new, rh_new), 2)),
                    ("ventilating", bool(win_q.get(rid, 0.0) + cross.get(rid, 0.0) + forced.get(rid, 0.0) > 0.01)))
    for rid in indoor:
        room = rooms[rid]
        vals = new[rid]
        for k, v in vals:
            if room.get(k) != v:
                w.room_m(rid).update(vals)
                break


def _relax(x, a, b, cap, dt):
    if b <= 1e-12:
        return x + a / cap * dt
    eq = a / b
    return eq + (x - eq) * math.exp(-b * dt / cap)


# ---------------------------------------------------------------------------------------------
# effects

def _effects_step(w, dt, sc, az, alt):
    state = w.s
    rooms = state["rooms"]
    t_out = C.outdoor_temp(state)
    wx = state.get("weather") or {}
    wind = C.num(wx.get("wind_mps"), 2.0)
    wind_from = C.num(wx.get("wind_from_deg"), 270.0)
    rain = C.rain_rate(state) if C.raining(state) else 0.0
    child = C.child_present(state)
    hvac_running = {sid: (state["entities"][sid].get("stage") in ("heat", "cool", "aux")) for sid in sc["kind"].get("hvac_system", [])}
    cooling_or_heating = any(hvac_running.values()) or any(state["entities"][u].get("call") in ("heat", "cool") for u in sc["kind"].get("mini_split", []))
    any_open_conditioned = False
    for rid in sorted(sc["windows"]):
        room = rooms.get(rid) or {}
        if room.get("outdoor"):
            continue
        t_in = room_t(state, rid)
        rh = room_rh(state, rid)
        dp = C.dew_point_c(t_in, rh)
        for wid in sc["windows"][rid]:
            wrec = state["entities"][wid]
            u = C.num(wrec.get("u_value"), C.WINDOW_U)
            glass = t_in - (t_in - t_out) * min(0.9, u / 8.0)
            cond = C.num(wrec.get("condensation"))
            if glass < dp:
                cond_new = min(1.0, cond + dt / 900.0 * min(1.0, (dp - glass) / 2.0))
            else:
                cond_new = max(0.0, cond - dt / 1800.0)
            changes = {}
            if abs(cond_new - cond) > 1e-4:
                changes["condensation"] = round(cond_new, 4)
            if cond_new >= 0.6:
                C.hazard(w, "condensation_s:" + wid, dt)
            area = sc.get("win_area", {}).get(wid)
            if area is None:
                area = C.window_open_area(wrec)
            facing = C.window_facing(wrec)
            if area > 0.005:
                if conditioned_room(state, rid):
                    any_open_conditioned = True
                if rain <= 0:
                    C.tracker(w, "open_dry_s:" + wid, dt)
                if rain > 0:
                    windward = 0.0 if facing is None else max(0.0, math.cos(math.radians(wind_from - facing)))
                    factor = 0.03 + 0.5 * windward * min(1.0, wind / 5.0)
                    liters = rain / 3600.0 * dt * area * factor
                    changes["rain_in_l"] = round(C.num(wrec.get("rain_in_l")) + liters, 4)
                    C.tracker(w, "open_in_rain_s:" + wid, dt)
                    C.tracker(w, "open_in_rain_s", dt)
                    if changes["rain_in_l"] >= RAIN_PUDDLE_L and C.num(wrec.get("rain_in_l")) < RAIN_PUDDLE_L:
                        # the sill catches the first splashes; past ~0.25 L water runs onto the floor (a puddle)
                        moisture._wet_cell(w, rid, wrec.get("pos"))
                        w.stat("spills")
                        C.log(w, "rain_ingress", window=wid)
                        C.visual(w, "water_on_floor", wid, room=rid, at=list(wrec.get("pos") or [0, 0]), z=0.02, size_m=0.4)
                    if changes["rain_in_l"] >= 0.5 and not wrec.get("water_damage") and room.get("floor_material") in ("carpet", "hardwood", "laminate", None):
                        changes["water_damage"] = True
                        w.stat("damage")
                        C.log(w, "rain_damage", window=wid, liters=round(changes["rain_in_l"], 2))
                screened = C.window_screened(wrec)
                if not screened and area >= 0.03:
                    for pid in C.pets_in(state, rid):
                        pet = state["pets"][pid]
                        # a pet shut in a closed cage / crate / carrier cannot reach the window
                        if pet.get("species") in ("cat", "dog", "bird", "rabbit") and not pet.get("away") and \
                                not C.pet_confined(state, pet):
                            secs = C.hazard(w, "pet_at_open_window_s:" + pid, dt)
                            if secs >= 60.0 and not _pets_domain():
                                _pet_escape(w, pid, rid, wid)
                height = area / max(0.1, C.num((wrec.get("size_m") or [1.0])[0], 1.0))
                if child and C.num(room.get("floor"), 0) >= 1 and height > 0.1:
                    C.hazard(w, "child_window_fall_risk_s", dt, stat="safety_violations")
            if changes:
                w.mut(wid).update(changes)
    if any_open_conditioned and cooling_or_heating:
        C.tracker(w, "hvac_with_open_windows_s", dt)
    for rid in sorted(rooms):
        room = rooms[rid]
        if room.get("outdoor"):
            continue
        t = C.num(room.get("temperature_c"), 21.0)
        if t < 3.0 and (room.get("type") in C.PLUMBED_ROOMS or room.get("plumbing")):
            secs = C.hazard(w, "pipe_freeze_s:" + rid, dt)
            if secs >= 7200.0 and not room.get("pipe_burst"):
                w.room_m(rid)["pipe_burst"] = True
                w.stat("damage")
                w.stat("flood")
                C.log(w, "pipe_burst", room=rid)
                C.notice(w, "pipe_burst", room=rid)
        if t > 27.0:
            for pid in C.pets_in(state, rid):
                if t > C.PET_HEAT_C.get(state["pets"][pid].get("species", "dog"), C.PET_HEAT_DEFAULT_C):
                    C.hazard(w, "pet_heat_s:" + pid, dt)
        watched = (C.section(state).get("watch_rooms") or [])
        if rid in watched:
            tr = C.sec(w)["trackers"]
            k1, k2 = "max_c:" + rid, "min_c:" + rid
            if t > C.num(tr.get(k1), -99.0):
                tr[k1] = round(t, 3)
            if t < C.num(tr.get(k2), 99.0):
                tr[k2] = round(t, 3)
    _people_comfort(w, dt, sc, az, alt)
    _glare(w, sc, az, alt)
    _light_levels(w, sc)
    _shade_watch(w, dt, sc, az, alt)
    by_room = {}
    for f in sc["kind"].get("ceiling_fan", []) + sc["kind"].get("portable_fan", []):
        rec = state["entities"][f]
        rid = sc["room"].get(f)
        if rid is None:
            continue
        spinning = C.num(rec.get("rpm")) >= 5.0 if C.kind(rec) == "ceiling_fan" else bool(rec.get("running"))
        if spinning:
            by_room.setdefault(rid, []).append(f)
    for rid in sorted(by_room):
        fans.disturb(w, rid, by_room[rid], dt)


def conditioned_room(state, rid):
    return C.conditioned(state, rid)


def _pets_domain():
    return any(name.startswith("pets") for _, name, _, _ in SYSTEMS)


def _pet_escape(w, pid, rid, wid):
    state = w.s
    outdoor = sorted(r for r, room in state["rooms"].items() if room.get("outdoor"))
    m = w.pet_m(pid)
    if m.get("escaped"):
        return
    m["escaped"] = True
    if outdoor:
        m["location"] = outdoor[0]
        m["pos"] = None
        m["pos"] = geometry.free_floor_cell(state, outdoor[0], clearance=False, reachable=False) or [0, 0]
    w.stat("pet_escape")
    C.log(w, "pet_escaped", pet=pid, window=wid)
    C.visual(w, "pet_escaped", wid)


def _people_comfort(w, dt, sc, az, alt):
    state = w.s
    for pid in sorted(state.get("people", {})):
        p = state["people"][pid]
        rid = p.get("location")
        if p.get("away") or rid not in state["rooms"]:
            continue
        room = state["rooms"][rid]
        x, y = C.agent_xy(p)
        z = 1.6 if p.get("posture") == "standing" else (0.5 if p.get("posture") in ("lying", "sleeping") else 1.1)
        fans_here = [f for f in sc["kind"].get("ceiling_fan", []) + sc["kind"].get("portable_fan", []) if sc["room"].get(f) == rid]
        v = fans.airspeed_at(state, rid, x, y, fans_here) if fans_here else 0.0
        if room.get("outdoor"):
            sun = 0.0 if alt <= 0.5 else (1.0 - S.shaded_at(state, rid, x, y, z, az, alt)) * S.sky_factor(state)
            v += 0.5 * C.num((state.get("weather") or {}).get("wind_mps"), 2.0)
        else:
            sun = S.beam_at(state, rid, x, y, z, az, alt, sc["windows"].get(rid, []), sc["memo"].setdefault("apertures", {}))[0]                 if alt > 0.5 else 0.0
        heat = 0.0
        for h in sc["kind"].get("space_heater", []):
            hrec = state["entities"][h]
            if sc["room"].get(h) != rid or not hrec.get("running"):
                continue
            _, hx, hy = C.entity_xy(state, h)
            if math.hypot(hx - x, hy - y) <= 1.5:
                heat += 3.0
        comfort.update_person(w, pid, room_t(state, rid), room_rh(state, rid), v, C.num(room.get("stratification_c")), sun, heat, dt)


def _glare(w, sc, az, alt):
    state = w.s
    for rid, targets in sorted(sc["glare"].items()):
        if rid not in state["rooms"] or state["rooms"][rid].get("outdoor"):
            continue
        for eid in targets:
            rec = state["entities"][eid]
            if alt > 0.5 and sc["windows"].get(rid):
                points = sc["memo"].setdefault("glare_points", {})
                if eid not in points:
                    points[eid] = S.glare_point(state, eid)   # screens move only by actions (step memo)
                _, x, y, z = points[eid]
                g, wid = S.beam_at(state, rid, x, y, z, az, alt, sc["windows"].get(rid, []), sc["memo"].setdefault("apertures", {}))
            else:
                g, wid = 0.0, None
            changes = {"screen_glare": g, "screen_glare_window": wid if g >= 0.15 else None}
            if any(rec.get(k) != v for k, v in changes.items()):
                w.mut(eid).update(changes)


def _light_ids(w, sc):
    """{room: [window and light ids]} in state order, built once per env step (windows never move; lights move between
    rooms only by actions): the id list kernel.perception.light_level takes, cut down to each room."""
    memo = sc["memo"]
    hit = memo.get("light_ids")
    if hit is None:
        hit = {}
        rooms = w.s["rooms"]
        for eid, rec in w.s["entities"].items():
            cat = rec.get("category")
            if cat == "window":
                rid = rec.get("location")
            elif cat == "light":
                rid = sc["room"].get(eid)
            else:
                continue
            if rid in rooms:
                hit.setdefault(rid, []).append(eid)
        memo["light_ids"] = hit
    return hit


def _light_levels(w, sc):
    """Observable light level for rooms holding glare targets or listed in watch_rooms: the kernel's ambient light,
    kernel.perception.light_level (tier-1 camera adoption: climate's own copy of the formula is gone), given each room's
    own window and light ids in state order (the same sums as over the whole house; no scan per substep)."""
    from ...kernel.perception import light_level
    state = w.s
    rooms = set(r for r in sc["glare"] if r in state["rooms"]) | set(C.section(state).get("watch_rooms") or [])
    if not rooms:
        return
    ids = _light_ids(w, sc)
    for rid in sorted(rooms):
        if rid not in state["rooms"]:
            continue
        lvl = light_level(state, rid, ids.get(rid, ()))
        if state["rooms"][rid].get("light_level") != lvl:
            w.room_m(rid)["light_level"] = lvl


def _shade_watch(w, dt, sc, az, alt):
    state = w.s
    now = C.num(state.get("time_s"))
    casters = {}
    for eid in sc["shade_watch"]:
        rec = state["entities"][eid]
        cfg = rec.get("shade_watch") or {}
        rid = sc["room"].get(eid)
        if rid not in casters:
            casters[rid] = S.shade_casters(state, rid, w)
        frac = S.shade_fraction(state, eid, az, alt, casters[rid])
        changes = {"shade_frac": frac}
        start, end = C.num(cfg.get("from_s"), 0.0), C.num(cfg.get("to_s"), 1e12)
        if start <= now <= end:
            changes["shade_min"] = round(min(C.num(rec.get("shade_min"), 1.0), frac), 3)
            changes["shade_samples"] = int(C.num(rec.get("shade_samples"))) + 1
        if any(rec.get(k) != v for k, v in changes.items()):
            w.mut(eid).update(changes)


# ---------------------------------------------------------------------------------------------
# energy

def tariff_usd_kwh(state):
    t = C.section(state).get("tariff") or {}
    h = C.hour(state)
    lo, hi = C.hhmm_to_h(t.get("peak_from", "16:00")), C.hhmm_to_h(t.get("peak_to", "21:00"))
    peak = lo is not None and hi is not None and lo <= h < hi
    return C.num(t.get("peak_usd_kwh"), 0.42) if peak else C.num(t.get("offpeak_usd_kwh"), 0.14), C.num(t.get("gas_usd_kwh"), 0.045)


def _book(w, dt, by, gas_w=0.0, hvac_w=None):
    """Add dt seconds of electric draw (by: {eid: W}) and gas burn to the ledger at the current time-of-use price."""
    state = w.s
    elec = 0.0
    for eid in sorted(by):
        elec += by[eid]
    gas = max(0.0, gas_w)
    price, gas_price = tariff_usd_kwh(state)
    kwh = elec * dt / 3.6e6
    gkwh = gas * dt / 3.6e6
    e = C.sec(w)["energy"]
    e["kwh"] = round(C.num(e.get("kwh")) + kwh, 6)
    e["gas_kwh"] = round(C.num(e.get("gas_kwh")) + gkwh, 6)
    e["cost_usd"] = round(C.num(e.get("cost_usd")) + kwh * price + gkwh * gas_price, 6)
    if hvac_w is not None:
        e["hvac_kwh"] = round(C.num(e.get("hvac_kwh")) + hvac_w * dt / 3.6e6, 6)
    bd = e.setdefault("by_device", {})
    if by:
        for eid in sorted(by):
            bd[eid] = round(C.num(bd.get(eid)) + by[eid] * dt / 3.6e6, 6)
    if price > C.num((C.section(state).get("tariff") or {}).get("offpeak_usd_kwh"), 0.14) + 1e-9:
        e["peak_kwh"] = round(C.num(e.get("peak_kwh")) + kwh, 6)


def _energy_step(w, dt, sc, hv):
    """Per substep: this domain's own devices (their power_w is derived each substep, HVAC cycles in minutes) and gas.
    Other domains' metered devices are booked by the periodic system ``climate.meter``."""
    state = w.s
    by = {}
    for eid in sc["kind_ids"]:
        rec = state["entities"].get(eid)
        if rec is None or rec.get("power_w") is None or sc["room"].get(eid) is None:
            continue
        watts = C.num(rec.get("power_w"))
        if watts > 0:
            by[eid] = watts
    hvac_w = sum(C.num(state["entities"][s].get("power_w")) for s in sc["kind"].get("hvac_system", []))
    _book(w, dt, by, hv["gas_w"], hvac_w)


SLOW_PERIOD_S = 300.0


def _wet_tanks(w):
    """Humidifier tanks holding water (moisture.age_tanks ages only those)."""
    state = w.s
    ents = state["entities"]
    return [e for e in C.static_ids(w, "climate.kinds", lambda r: bool(r.get("climate_kind")))
            if (ents.get(e) or {}).get("climate_kind") == "humidifier_tank" and C.num(ents[e].get("liquid_l")) > 0.02]


def slow_idle(w):
    """Exact idle probe of climate.slow (kernel v3.1 @system(idle=), TIER1_SWEEP 6.7): no tank holds standing water,
    so nothing ages this period (the system would change nothing)."""
    return not w.s.get("rooms") or not _wet_tanks(w)


@system("climate.slow", order=46, max_dt=SLOW_PERIOD_S, period=SLOW_PERIOD_S, idle=slow_idle)
def climate_slow(w, dt, tick):
    """Slow processes, once per 300 s with the accumulated dt: standing water in humidifier tanks ages and grows
    bacteria (~0.1/day, biofilm doubling; closed-form per call, so any dt up to the period is exact enough)."""
    state = w.s
    if not state.get("rooms"):
        return
    tanks = [e for e in C.static_ids(w, "climate.kinds", lambda r: bool(r.get("climate_kind")))
             if (state["entities"].get(e) or {}).get("climate_kind") == "humidifier_tank"]
    if tanks:
        moisture.age_tanks(w, dt, tanks)


METER_PERIOD_S = 120.0
LEDGER_KEYS = ("kwh", "gas_kwh", "cost_usd", "peak_kwh")


def meter_idle(w):
    """Exact idle probe of climate.meter: the house has no metered device of another domain (the step-cached static
    scan) and the energy ledger already holds every key a booking of nothing would (re)write at 6 decimals, so a run
    would book nothing and change nothing."""
    state = w.s
    if not state.get("rooms"):
        return True
    memo = step_memo(w)
    static = memo.get("scan")
    if static is None:
        static = _scan_static(w, state)
        memo["scan"] = static
    if static["power"]:
        return False
    sec = state.get(C.SECTION)
    if not isinstance(sec, dict) or any(not isinstance(sec.get(k), t) for k, t in (("energy", dict), ("hazards", dict), ("trackers", dict), ("log", list))):
        return False
    e = sec["energy"]
    if not isinstance(e.get("by_device"), dict):
        return False
    for key in LEDGER_KEYS:
        value = e.get(key)
        if type(value) is not float or value != round(value, 6):
            return False        # a booking would (re)write it: an int becomes a float, an unrounded value is rounded
    return True


@system("climate.meter", order=45, max_dt=METER_PERIOD_S, period=METER_PERIOD_S, idle=meter_idle)
def climate_meter(w, dt, tick):
    """Energy ledger for every other domain's metered device (``power_w`` when active else ``standby_w``; zero when
    unplugged, unpowered or its switched strip / smart plug is off). Device state is sampled once per 120 s and
    booked for the whole accumulated dt at the current tariff (standby and appliance draws change on minute scales)."""
    state = w.s
    if not state.get("rooms"):
        return
    memo = step_memo(w)
    static = memo.get("scan")
    if static is None:
        static = _scan_static(w, state)
        memo["scan"] = static
    by = {}
    for eid in static["power"]:
        rec = state["entities"].get(eid)
        if rec is None or (rec.get("power_w") is None and rec.get("standby_w") is None):
            continue
        watts = _other_draw(w, eid, rec)
        if watts > 0:
            by[eid] = watts
    _book(w, dt, by)


def _supply_switched_off(state, rec):
    """True when a switchable strip / smart plug / timer upstream of a plug device is off (cuts standby draw too)."""
    seen = set()
    cur = rec
    while cur.get("plug_required") and cur.get("plugged_in") and cur.get("outlet") and cur["outlet"] not in seen:
        seen.add(cur["outlet"])
        up = state["entities"].get(cur["outlet"])
        if up is None:
            return False
        if up.get("switchable") and not up.get("on", True):
            return True
        if (up.get("settings") or {}).get("relay") == "off":
            return True
        cur = up
    return False


def _other_draw(w, eid, rec):
    if rec.get("plug_required") and not rec.get("plugged_in"):
        return 0.0
    if not physics.is_powered(w, eid) or _supply_switched_off(w.s, rec):
        return 0.0
    machine = rec.get("appliance")
    active = rec.get("running") or rec.get("heating") or (isinstance(machine, dict) and machine.get("state") == "running")
    if rec.get("category") == "light":
        active = bool(rec.get("lit", rec.get("on")))
    elif not active and rec.get("on") and rec.get("power_state", "on") not in ("off", "rest", "standby"):
        active = not isinstance(machine, dict)
    if active:
        return C.num(rec.get("power_w"), 0.0)
    return C.num(rec.get("standby_w"), 0.0)
