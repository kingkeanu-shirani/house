"""The domain's time steps (kernel v2.1: no system scans every entity each tick).

plumbing_liquids.water (order 100, every <= 30 s substep; fast, event-sensitive water movement):
  supply cache -> water heater recovery -> taps (flow, warm-up, auto-off, where the water goes) -> basins (drain,
  overflow outlet, displacement overflow, opacity, attended-fill auto-off) -> toilets (refill, stuck fill valve,
  slow bowl drain) -> spigots and hoses -> irrigation controllers -> leaks -> filter pitchers, foam, ice
  (freeze/melt), fizz, hot-liquid hazards -> drips onto catchers -> meter.
  Iterates static indexes only (fixtures by plumb.kind, valves, leak hosts, liquid holders, drippers).
plumbing_liquids.slow (order 101, period 120 s, dt = the accumulated time): drinking-water staleness/biofilm, ice
  makers (90 min harvest cycle), freeze damage (<= -2 C for an hour), condensation, water rings, closed-wet mildew,
  fallback drying (only without the cleaning domain).
plumbing_liquids.census (order 102, period 600 s): finds liquid that other domains put into things whose type does
  not mark them as liquid holders (runtime extras for the holder index).

The water system has an exact idle probe (kernel v3.1, TIER1_SWEEP 6.7): water_idle(w) is True when no step would
change anything (the heater at its setpoint, every tap shut and not dripping, basins empty, toilets full and calm,
spigots and hoses slack, controllers not watering, no leak running, nothing freezing, melting, foaming, fizzing or
cooling past the hot-hazard line, nothing dripping, no sprinkler on, the meter current), so the engine skips the call;
tests/test_plumbing_liquids.py checks that states are identical with probes on and off.
"""
from ...kernel.plugins import hook, system
from . import common as C
from . import fixtures as F
from . import ice as I
from . import leaks as K
from . import liquids as L
from . import outdoor as O
from . import supply as S
from . import toilets as T


def _attended_fill(w, eid):
    rec = w.ent(eid)
    target = rec.get("_fill_target_l")
    if target is None:
        return
    reached = L.liters(rec) >= C.num(target) - 1e-3
    late = C.now(w) >= C.num(rec.get("_fill_deadline_s"), 0.0)
    if not (reached or late):
        return
    m = w.mut(eid)
    m.pop("_fill_target_l", None)
    m.pop("_fill_deadline_s", None)
    settings = m.get("settings") or {}
    kind = F.fkind(rec)
    if kind in ("single_lever", "pull_down", "thermostatic"):
        settings["flow"] = 0.0
    elif kind == "two_handle":
        settings["left"] = 0.0
        settings["right"] = 0.0
    elif kind == "touchless":
        settings["sensor"] = "off"
    elif kind == "spigot":
        settings["valve"] = 0.0
    C.mark_robot_water(w, eid, "flow", False)       # the attended fill shut the tap the robot opened
    F.refresh(w, eid)
    C.plog(w, "attended_fill_done", basin=eid, liters=C.rnd(L.liters(w.ent(eid)), 2), reached=bool(reached))


def _freshness(w, eid, dt):
    """Water or sugary drinks left standing in an open container grow a film after a day (faster when sweet)."""
    rec = w.ent(eid)
    if L.liters(rec) <= 0 or rec.get("filled_s") is None:
        return
    age = C.now(w) - C.num(rec.get("filled_s"))
    sugary = C.info(rec.get("liquid")).get("sugary") or rec.get("liquid") in ("milk", "juice", "orange_juice")
    onset = 8 * 3600.0 if sugary else 24 * 3600.0
    if age < onset or C.num(rec.get("temperature_c"), 21.0) < 8.0:
        return
    rate = (dt / 86400.0) * (0.6 if sugary else 0.25)
    soil = rec.get("soil") or {}
    level = C.num(soil.get("mildew") if isinstance(soil, dict) else 0.0)
    if level < 0.6:
        C.add_soil(w, eid, "mildew", rate)
    if not rec.get("stale"):
        w.mut(eid)["stale"] = True


def _hot_hazard_current(rec):
    """True when _hot_hazard would write nothing. Pure."""
    if not isinstance(rec.get("_mix"), dict) or L.liters(rec) <= 0.01:
        return True
    temp = C.num(rec.get("temperature_c"), 21.0)
    hazards = rec.get("hazards") or []
    if temp > 50.0:
        return "hot" in hazards
    return not ("hot" in hazards and rec.get("_hot_by_plumbing") and temp < 45.0)


def _holder_active(rec):
    """A holder the water system steps: liquid, ice or a pitcher reservoir in it, or a freezing plateau to hand back."""
    return L.holding(rec) or rec.get("thermal_owner") == I.OWNER


def _holder_idle(w, eid, rec):
    if C.num(rec.get("reservoir_l")) > 1e-6 or C.num(rec.get("foam_l")) > 0 or C.num(rec.get("fizz")) > 0.01:
        return False
    if not rec.get("food") and not I.freeze_idle(w, eid):
        return False
    return _hot_hazard_current(rec)


def water_idle(w):
    """The water system's idle probe: True when _water would change nothing this substep (every step's own pure
    check; one step writing nothing leaves the next one's input unchanged). Writes nothing."""
    entities = w.s["entities"]
    cache = S.tick_cache(w)
    if not S.heater_idle(w, cache):
        return False
    for eid in C.index(w, "active", _active_kind):
        rec = entities.get(eid)
        if rec is None:
            continue
        kind = C.kind(rec)
        if kind in ("sink", "tub", "shower", "dispenser"):
            if not F.outlet_idle(w, eid, cache) or (L.is_basin(rec) and not F.basin_idle(w, eid)):
                return False
        elif kind == "toilet":
            if not T.toilet_idle(w, eid, cache):
                return False
        elif kind == "spigot":
            if not O.spigot_idle(w, eid, cache):
                return False
        elif kind == "controller":
            if not O.controller_idle(w, eid):
                return False
    if not K.leaks_idle(w, cache):
        return False
    for eid in L.holder_ids(w):
        rec = entities.get(eid)
        if rec is not None and _holder_active(rec) and not _holder_idle(w, eid, rec):
            return False
    return K.drips_idle(w) and O.sprinklers_idle(w) and S.meter_idle(w)


def _hot_hazard(w, eid):
    """A container of hot liquid (> 50 C) is a burn hazard (core hand-over refuses it for children)."""
    if eid not in w.s["entities"]:
        return
    rec = w.ent(eid)
    if not isinstance(rec.get("_mix"), dict) or L.liters(rec) <= 0.01:
        return
    hot = C.num(rec.get("temperature_c"), 21.0) > 50.0
    hazards = list(rec.get("hazards") or [])
    if hot and "hot" not in hazards:
        m = w.mut(eid)
        m["hazards"] = hazards + ["hot"]
        m["_hot_by_plumbing"] = True
    elif not hot and "hot" in hazards and rec.get("_hot_by_plumbing") and C.num(rec.get("temperature_c"), 21.0) < 45.0:
        m = w.mut(eid)
        m["hazards"] = [h for h in hazards if h != "hot"]
        m.pop("_hot_by_plumbing", None)


def _active_kind(rec):
    return C.kind(rec) in ACTIVE_KINDS


ACTIVE_KINDS = ("sink", "tub", "shower", "dispenser", "toilet", "spigot", "controller")


@system("plumbing_liquids.water", order=100, max_dt=30.0, idle=water_idle)
def water_system(w, dt, tick):
    with C.system_context():
        _water(w, dt)


def _water(w, dt):
    entities = w.s["entities"]
    cache = S.tick_cache(w)
    S.heater_step(w, dt, cache)
    meter = 0.0
    for eid in C.index(w, "active", _active_kind):
        rec = entities.get(eid)
        if rec is None:
            continue
        kind = C.kind(rec)
        if kind in ("sink", "tub", "shower", "dispenser"):
            meter += F.outlet_step(w, eid, dt, cache)
            if L.is_basin(w.ent(eid)):
                F.basin_step(w, eid, dt)
                _attended_fill(w, eid)
                rec = w.ent(eid)
                if isinstance(rec.get("_clog"), dict) and rec.get("standing_l") != C.rnd(L.liters(rec), 3):
                    w.mut(eid)["standing_l"] = C.rnd(L.liters(rec), 3)
        elif kind == "toilet":
            meter += T.toilet_step(w, eid, dt, cache)
        elif kind == "spigot":
            meter += O.spigot_step(w, eid, dt, cache)
        elif kind == "controller":
            O.controller_step(w, eid, dt)
    O.sprinkler_spray(w, dt)
    meter += K.leak_step(w, dt, cache)
    for eid in L.holder_ids(w):
        rec = entities.get(eid)
        if rec is None or not _holder_active(rec):
            continue
        if rec.get("reservoir_l"):
            I.pitcher_step(w, eid, dt)
        if C.num(rec.get("foam_l")) > 0:
            foam = C.num(rec.get("foam_l")) * max(0.0, 1.0 - dt / 40.0)
            w.mut(eid)["foam_l"] = C.rnd(foam, 4) if foam > 1e-4 else 0.0
        if not rec.get("food"):
            I.freeze_step(w, eid, dt)
        _hot_hazard(w, eid)
        if eid in entities and C.num(w.ent(eid).get("fizz")) > 0.01:
            # a shaken, sealed carbonated drink calms down over a couple of minutes
            w.mut(eid)["fizz"] = C.rnd(C.num(w.ent(eid)["fizz"]) * max(0.0, 1.0 - dt / 90.0), 3)
    K.drip_step(w, dt)
    S.meter_update(w, meter)


@system("plumbing_liquids.slow", order=101, max_dt=600.0, period=120)
def slow_system(w, dt, tick):
    """Slow processes, dt-correct for the accumulated period (120 s; up to 600 s on long waits)."""
    with C.system_context():
        entities = w.s["entities"]
        for eid in L.holder_ids(w):
            rec = entities.get(eid)
            if rec is None or not L.holding(rec):
                continue
            _freshness(w, eid, dt)
        for eid in C.live(w, C.kind_ids(w, "ice_maker")):
            I.ice_maker_step(w, eid, dt)
        O.freeze_step(w, dt)
        K.condensation_step(w)
        K.ring_and_mildew_step(w, dt)
        K.fallback_drying(w, dt)


@system("plumbing_liquids.census", order=102, max_dt=3600.0, period=600)
def census_system(w, dt, tick):
    """Liquid other domains poured into things whose type gives no hint (a pet bowl, a vase): track them."""
    known = set(C.index(w, "holders", L.holder_candidate))
    extra = list(C.pget(w, "holders_extra") or [])
    entities = w.s["entities"]
    found = []
    for eid, rec in entities.items():
        if eid in known:
            continue
        liq = rec.get("liquid_l")
        ice = rec.get("ice_g")
        if (liq and C.num(liq) > 0) or (ice and C.num(ice) > 0) or rec.get("reservoir_l"):
            if not L.holder_excluded(rec):
                found.append(eid)
    keep = sorted(set(e for e in extra if e in entities and e not in known) | set(found))
    if keep != extra:
        C.psec(w)["holders_extra"] = keep


@hook("episode_start")
def episode_start(w, env):
    """Before the baseline is taken: derived tap state (water_running, flow_lpm, outlet_temp_c, drips) and drain
    rates agree with the settings a scenario wrote, so the first observation and `preserve` see the real state."""
    cache = S.tick_cache(w)
    for eid in F.faucet_ids(w):
        F.refresh(w, eid, cache)
        if L.is_basin(w.ent(eid)):
            F.refresh_drain(w, eid)
