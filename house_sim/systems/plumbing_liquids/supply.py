"""Water supply: municipal service, the house main shutoff, fixture stop valves, the water heater, the meter.

Valves (types shutoff_valve, main_shutoff_valve, and the supply valve built into toilets/spigots):
  settings.position 0..1 (1 = fully open), control_kinds position "valve" (core `set` cannot turn it: use
  shut_off / open_valve or mechanisms' turn), valve_open (bool mirror for other domains), valve_kind
  gate | ball | multi_turn_stop | quarter_turn_stop, turns_to_close (gate ~6-8, multi-turn stop ~2.5, ball 0.25),
  feeds [fixture ids] + line hot|cold|both (fixture valves), main true (house main), feeds_hot true (water-heater
  cold inlet: closing it stops hot water everywhere but not a burst cold pipe), hidden _seized (corroded stem),
  max_torque_nm (stem snaps above it: ~20 N m for a 1/2 in stop, 60 N m for a 3/4 in gate).
Main closure sets utilities.main_water_open False (kernel.physics.water_available honours it); gate valves
pass most of the flow until nearly closed (flow factor min(1, 2.2 x position)).

Water heater (type water_heater): tank_l 150-190 (40-50 gal), settings.temp_c (49 C default), tank_temp_c,
recovery_kw (electric 4.5 kW, gas ~9 kW effective). Drawing hot water mixes cold inflow into the tank; the tank
reheats at recovery_kw. Hot water reaches a tap after warm_up_s of continuous hot flow (pipe run; 10-40 s).
Tankless units (`tankless` true, wall-hung; gas ~44 kW / 150,000 BTU/h, electric 24 kW) heat on demand: hot water
leaves at the setpoint while the unit has gas/power (44 kW lifts 13 L/min by 45 C, more than any one fixture draws;
throttling under several simultaneous draws is not modeled) and there is no stored hot water to run out of or to keep
hot after a gas/power cut (the exchanger cools to supply temperature within a few minutes).

Meter (type water_meter): display shows cumulative liters; flow_indicator spins whenever any water moves
(including pressurized leaks and a running toilet) - the classic leak test with every fixture off.
"""
from ...kernel import physics
from ...kernel.errors import require
from ...kernel.plugins import hook
from . import common as C

LINES = ("hot", "cold")


def valve_position(rec):
    settings = rec.get("settings") or {}
    if "position" in settings:
        return C.clamp(C.num(settings.get("position"), 1.0), 0.0, 1.0)
    if "supply_valve" in settings:
        return 1.0 if settings.get("supply_valve") == "open" else 0.0
    return 1.0 if rec.get("valve_open", True) else 0.0


def flow_factor(rec):
    pos = valve_position(rec)
    if pos <= 0.001:
        return 0.0
    kind = rec.get("valve_kind", "gate")
    if kind in ("ball", "quarter_turn_stop"):
        return C.clamp(pos * 1.4, 0.0, 1.0)
    return C.clamp(pos * 2.2, 0.0, 1.0)


def _is_valve(rec):
    return bool(rec.get("valve_kind"))


def _is_main_valve(rec):
    return bool(rec.get("valve_kind")) and bool(rec.get("main"))


def valves(w):
    """All valve entities, sorted (static index: valve_kind is a type prop)."""
    return C.live(w, C.index(w, "valves", _is_valve))


def main_valves(w):
    return C.live(w, C.index(w, "main_valves", _is_main_valve))


def main_factor(w):
    if not C.supply_ok(w):
        return 0.0
    factor = 1.0
    for eid in main_valves(w):
        factor = min(factor, flow_factor(w.ent(eid)))
    return factor


def line_factor(w, fixture, line, cache=None):
    """0..1 pressure/flow factor of a fixture's hot or cold line (main, heater inlet and fixture stops)."""
    factor = main_factor(w) if cache is None else cache.get("main", 1.0)
    if factor <= 0:
        return 0.0
    vals = cache.get("valves") if cache is not None else valves(w)
    for eid in vals:
        rec = w.ent(eid)
        if rec.get("main"):
            continue
        if line == "hot" and rec.get("feeds_hot"):
            factor = min(factor, flow_factor(rec))
            continue
        feeds = rec.get("feeds") or []
        if fixture in feeds and rec.get("line", "both") in (line, "both"):
            factor = min(factor, flow_factor(rec))
    frec = w.s["entities"].get(fixture) or {}
    own = (frec.get("settings") or {}).get("supply_valve")
    if own is not None and own != "open":
        return 0.0
    if frec.get("broken_supply"):
        return 0.0
    return factor


def tick_cache(w):
    """Per-substep values every fixture needs (supply factors, heater, supply temperatures)."""
    return {"main": main_factor(w), "valves": valves(w), "heater": heater(w)}


@hook("water_available")
def water_available_hook(w, eid):
    """physics.water_available(w, fixture): False when every supply line of a plumbing fixture is shut."""
    rec = w.s["entities"].get(eid) or {}
    if not C.is_mine(rec):
        feeds = [v for v in valves(w) if eid in (w.ent(v).get("feeds") or [])]
        if not feeds:
            return None
        return bool(any(flow_factor(w.ent(v)) > 0 for v in feeds))
    if rec.get("hose"):
        return hose_has_water(w, eid)
    kind = C.kind(rec)
    if kind in ("sink", "tub", "shower", "spigot", "dispenser", "ice_maker", "toilet"):
        lines = rec.get("lines") or ["cold"]
        return any(line_factor(w, eid, line) > 0 for line in lines)
    return None


def hose_has_water(w, hose):
    rec = w.ent(hose)
    spigot = rec.get("attached_to")
    if not spigot or spigot not in w.s["entities"]:
        return False
    srec = w.ent(spigot)
    if C.num((srec.get("settings") or {}).get("valve"), 0.0) <= 0.0:
        return False
    return line_factor(w, spigot, "cold") > 0


# ----------------------------------------------------------------------------------
# water heater

def heater(w, cache=None):
    if cache is not None and "heater" in cache:
        return cache["heater"]
    for eid in C.kind_ids(w, "water_heater"):
        if eid in w.s["entities"]:
            return eid
    return None


def hot_supply_c(w, cache=None):
    """Tank temperature right now (read live: drawing hot water cools the tank within a tick)."""
    hid = heater(w, cache)
    if hid is None:
        return C.DEFAULT_HEATER_C
    rec = w.ent(hid)
    if rec.get("broken"):
        return C.COLD_SUPPLY_C
    return C.num(rec.get("tank_temp_c"), C.num((rec.get("settings") or {}).get("temp_c"), C.DEFAULT_HEATER_C))


def cold_supply_c(w):
    u = w.s.get("utilities") or {}
    return C.num(u.get("cold_water_c"), C.COLD_SUPPLY_C)


def draw_hot(w, liters, cache=None):
    """Hot water drawn from the tank is replaced by cold supply water (well-mixed tank); a tankless unit heats it on
    the way through instead."""
    hid = heater(w, cache)
    if hid is None or liters <= 0:
        return
    rec = w.ent(hid)
    if rec.get("tankless"):
        return
    tank = C.num(rec.get("tank_l"), 180.0)
    t = C.num(rec.get("tank_temp_c"), C.num((rec.get("settings") or {}).get("temp_c"), C.DEFAULT_HEATER_C))
    tc = cold_supply_c(w)
    frac = min(1.0, liters / tank)
    m = w.mut(hid)
    m["tank_temp_c"] = C.rnd(t - (t - tc) * frac, 2)


def heater_step(w, dt, cache=None):
    hid = heater(w, cache)
    if hid is None:
        return
    rec = w.ent(hid)
    target = C.num((rec.get("settings") or {}).get("temp_c"), C.DEFAULT_HEATER_C)
    t = C.num(rec.get("tank_temp_c"), target)
    if rec.get("broken"):
        return
    powered = physics.is_powered(w, hid) if rec.get("fuel", "gas") == "electric" else bool((w.s.get("utilities") or {}).get("gas", True))
    if rec.get("tankless"):
        # on demand: at the setpoint while it can fire; a cut leaves the small exchanger to cool (~5 min)
        tc = cold_supply_c(w)
        new = target if powered else max(tc, t - (t - tc) * min(1.0, dt / 300.0))
        if abs(new - t) > 0.01:
            w.mut(hid)["tank_temp_c"] = C.rnd(new, 2)
        return
    if t < target - 0.05 and powered:
        tank = C.num(rec.get("tank_l"), 180.0)
        rise = C.num(rec.get("recovery_kw"), 9.0) * 1000.0 * dt / (4186.0 * tank)
        w.mut(hid)["tank_temp_c"] = C.rnd(min(target, t + rise), 2)
    elif t > target + 0.05:
        # standby loss toward the setpoint after it was turned down (~1 C/h insulated tank)
        w.mut(hid)["tank_temp_c"] = C.rnd(max(target, t - dt / 3600.0), 2)


def heater_idle(w, cache=None):
    """True when heater_step would change nothing: no heater, a broken one, a tank at its setpoint (within 0.05 C) or
    below it without power, a tankless unit at its setpoint while it can fire or at the supply temperature after a
    cut (still cooling is not idle).
    Pure (the water system's idle probe)."""
    hid = heater(w, cache)
    if hid is None:
        return True
    rec = w.s["entities"][hid]
    if rec.get("broken"):
        return True
    target = C.num((rec.get("settings") or {}).get("temp_c"), C.DEFAULT_HEATER_C)
    t = C.num(rec.get("tank_temp_c"), target)
    powered = physics.is_powered(w, hid) if rec.get("fuel", "gas") == "electric" else bool((w.s.get("utilities") or {}).get("gas", True))
    if rec.get("tankless"):
        return abs((target if powered else cold_supply_c(w)) - t) <= 0.01
    if t < target - 0.05 and powered:
        return False
    return not t > target + 0.05


# ----------------------------------------------------------------------------------
# meter

def _meter_view(rec, total, spinning):
    reading = round(C.num(rec.get("base_reading_l"), 482113.0) + total, 1)
    return reading, "{:.1f} L total; flow indicator {}".format(reading, "turning" if spinning else "still")


def meter_idle(w):
    """True when meter_update with no flow would change nothing (every display current and still). Pure."""
    total = C.num(C.pget(w, "meter_l"))
    for eid in C.live(w, C.kind_ids(w, "water_meter")):
        rec = w.s["entities"][eid]
        if rec.get("display") != _meter_view(rec, total, False)[1] or rec.get("flow_indicator") is not False:
            return False
    return True


def meter_update(w, flow_lpm):
    """Refresh every water meter's display (cumulative liters) and flow indicator."""
    total = C.num(C.pget(w, "meter_l"))
    for eid in C.live(w, C.kind_ids(w, "water_meter")):
        rec = w.s["entities"][eid]
        spinning = flow_lpm > 0.005
        reading, display = _meter_view(rec, total, spinning)
        if rec.get("display") != display or rec.get("flow_indicator") != spinning:
            m = w.mut(eid)
            m["display"] = display
            m["flow_indicator"] = spinning
            m["reading_l"] = reading


# ----------------------------------------------------------------------------------
# valve operation (used by shut_off / open_valve and the setting_changed hook)

def set_position(ctx, eid, position):
    from ..core import apply_setting
    w = ctx.w
    rec = w.ent(eid)
    settings = rec.get("settings") or {}
    if "position" in settings:
        apply_setting(ctx, eid, "position", round(C.clamp(position, 0.0, 1.0), 4))
    elif "supply_valve" in settings:
        apply_setting(ctx, eid, "supply_valve", "open" if position > 0.001 else "closed")
    sync_valve(w, eid)


def sync_valve(w, eid):
    rec = w.ent(eid)
    is_open = valve_position(rec) > 0.001
    if rec.get("valve_open") != is_open and "valve_kind" in rec:
        w.mut(eid)["valve_open"] = is_open
    if rec.get("main"):
        u = w.s.get("utilities") or {}
        others_open = all(valve_position(w.ent(v)) > 0.001 for v in main_valves(w))
        if bool(u.get("main_water_open", True)) != others_open:
            w.sec("utilities")["main_water_open"] = others_open


@hook("setting_changed")
def valve_setting_changed(ctx, eid, name, old, new):
    w = ctx.w
    rec = w.s["entities"].get(eid) or {}
    if name in ("position", "supply_valve") and (rec.get("valve_kind") or C.is_mine(rec)):
        sync_valve(w, eid)
        C.plog(w, "valve", target=eid, position=new)


def close_valve_turns(rec):
    return C.num(rec.get("turns_to_close"), 0.25 if rec.get("valve_kind") in ("ball", "quarter_turn_stop") else 2.5)


def require_turnable(ctx, rec, eid, force):
    """Seized stems do not move by hand; forcing past the torque limit snaps them."""
    if not rec.get("_seized"):
        return True
    if force != "firm":
        require(False, "The valve handle will not move by hand: it is seized (corroded stem). Do not force it; "
                       "isolate upstream or get a plumber", "precondition")
    w = ctx.w
    C.damage(w, eid, "stem snapped (forced past its torque limit)", broken=True, kind="stem_snapped")
    m = w.mut(eid)
    m["_leak"] = {"component": "valve_stem", "rate_lpm": 8.0, "when": "pressurized", "line": rec.get("line", "cold"),
                  "fixture": eid, "to": {"room": C.room_of(w, eid), "cell": C.cell_of(w, eid)}, "started_s": C.now(w),
                  "severity": "spray"}
    return False
