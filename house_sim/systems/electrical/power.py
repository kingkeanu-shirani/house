"""Grid power distribution: panel, main and branch breakers, wall outlets, GFCI, power strips,
extension cords, smart plugs and timers (fidelity P-approx: lumped load / thermal model).

Supply chain of a corded device: device -(plug)-> provider -> ... -> wall outlet -> branch
breaker -> main breaker -> utility. `supply_ok(state, eid)` walks it; the kernel's
`physics.is_powered` consults it through the "powered" hook, so every domain's appliances lose
power when a breaker trips, a GFCI trips, a strip is switched off or a plug is pulled.

Loads: each 30 s substep the system sums the draw of every metered consumer (power_w when
active, standby_w otherwise) per circuit and per strip/cord. Breakers trip on a thermal curve
(heat += dt*(I/I_rated^2 - 1), trip at 360 -> 2x load trips in ~2 min, 1.35x in ~7 min) or
instantly above 5x (short). Strips trip their own breaker; extension cords have none and heat
instead (steady rise ~ k (I/I_rated)^2, k = 30 C laid out, 95 C coiled, x1.6 under a rug):
75 C scorches the jacket (electrical_hazard), 110 C ignites (fire).

Derived observable fields kept in sync after every electrical change: `energized` (plug
devices, providers, electronics), `live` (outlets, strips), `powered` (lights; perception),
breaker `handle` (on / off / tripped).
"""
import math

from ...kernel import physics
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, before, hook, system
from . import common as C

MAX_DEPTH = 8
SETTLING = [False]   # True while furnishers/helpers compute derived fields (no transition effects)
TRIP_HEAT = 360.0
STRIP_TRIP_HEAT = 180.0
PROVIDER_KINDS = ("strip", "extension", "smart_plug", "timer", "adapter")


# ----------------------------------------------------------------------------------------
# pure supply logic (state dicts)

def is_breaker(rec):
    return rec.get("category") == "breaker"


def main_of(state, bid):
    rec = state["entities"].get(bid) or {}
    panel = state["entities"].get(rec.get("panel") or "") or {}
    return panel.get("main_breaker")


def breaker_closed(rec):
    return bool(rec.get("on")) and not rec.get("tripped")


def circuit_live(state, bid):
    """A branch circuit is live when its breaker and the panel's main breaker are closed."""
    if not bid:
        return True
    ents = state["entities"]
    rec = ents.get(bid)
    if rec is None:
        return True
    if not breaker_closed(rec):
        return False
    main = main_of(state, bid)
    if main and main != bid and main in ents and not breaker_closed(ents[main]):
        return False
    return True


def room_circuit(state, eid):
    rid = C.room_of(state, eid)
    return (state["rooms"].get(rid) or {}).get("circuit") if rid else None


def timer_active(state, rec):
    settings = rec.get("settings") or {}
    mode = settings.get("mode", "auto")
    if mode == "on":
        return True
    if mode == "off":
        return False
    on, off = C.parse_hhmm(settings.get("on_time", "18:00")), C.parse_hhmm(settings.get("off_time", "23:00"))
    if on is None or off is None:
        return False
    now = C.local_seconds(state)
    a, b = on[0] * 3600 + on[1] * 60, off[0] * 3600 + off[1] * 60
    return a <= now < b if a <= b else (now >= a or now < b)


def provider_output_live(state, oid, depth=0, memo=None):
    """Is power available at the receptacles of an outlet / strip / cord / plug-in adapter?

    memo: optional dict shared through one pass over many devices (state must not change during the pass)."""
    if memo is not None:
        hit = memo.get(oid)
        if hit is not None:
            return hit
        memo[oid] = live = _provider_output_live(state, oid, depth, memo)
        return live
    return _provider_output_live(state, oid, depth, None)


def _provider_output_live(state, oid, depth, memo):
    ents = state["entities"]
    o = ents.get(oid)
    if o is None or depth > MAX_DEPTH:
        return False
    if o.get("broken") or o.get("_fault") == "open":
        return False
    kind = o.get("outlet_kind", "wall")
    if kind == "wall":
        if not state.get("utilities", {}).get("power", True):
            return False
        if o.get("switched") and not o.get("on", True):
            return False
        if o.get("gfci") and o.get("gfci_tripped"):
            return False
        up = o.get("gfci_protected_by")
        if up and (ents.get(up) or {}).get("gfci_tripped"):
            return False
        return circuit_live(state, o.get("circuit") or room_circuit(state, oid))
    if o.get("tripped"):
        return False
    if o.get("switchable") and not o.get("on", True):
        return False
    if kind == "smart_plug" and (o.get("settings") or {}).get("relay", "on") != "on":
        return False
    if kind == "timer" and not timer_active(state, o):
        return False
    return supply_ok(state, oid, depth + 1, memo)


def supply_ok(state, eid, depth=0, memo=None):
    """True when power reaches the entity's input: mains, or a charged backup battery (UPS / medical backup pack)."""
    if mains_ok(state, eid, depth, memo):
        return True
    rec = state["entities"].get(eid) or {}
    return C.num(rec.get("backup_battery_wh"), 0.0) > 0 and C.num(rec.get("backup_battery_level"), 0.0) > 0.0


def mains_ok(state, eid, depth=0, memo=None):
    """True when mains power reaches the entity's own input (ignores its on/off switch)."""
    ents = state["entities"]
    rec = ents.get(eid)
    if rec is None or depth > MAX_DEPTH:
        return False
    if is_breaker(rec) or rec.get("category") in ("breaker_panel", "outlet"):
        return state.get("utilities", {}).get("power", True)
    if not state.get("utilities", {}).get("power", True):
        return False
    if rec.get("plug_required"):
        if not rec.get("plugged_in"):
            return False
        if C.num(rec.get("plug_seated"), 1.0) < 0.99 or rec.get("plug_damage"):
            return False
        out = rec.get("outlet")
        if out and out in ents:
            return provider_output_live(state, out, depth + 1, memo)
        return circuit_live(state, room_circuit(state, eid))
    if rec.get("circuit"):
        return circuit_live(state, rec["circuit"])
    if rec.get("hardwired") or rec.get("category") == "light" or isinstance(rec.get("appliance"), dict):
        if rec.get("hardwired") and rec.get("relation") == "part" and rec.get("category") == "detector":
            base = ents.get(rec.get("location")) or {}
            return circuit_live(state, base.get("circuit") or room_circuit(state, eid))
        return circuit_live(state, room_circuit(state, eid))
    return True


def supply_voltage(state, eid):
    """Nominal volts at an entity's plug: house voltage unless a converting adapter is in the chain."""
    ents = state["entities"]
    for prov in supply_path(state, eid):
        rec = ents[prov]
        if rec.get("converts_voltage"):
            return C.num(rec.get("output_v"), C.voltage(state))
    return C.voltage(state)


def supply_path(state, eid):
    """[provider ids..., wall outlet] between a plug device and the wall (empty if none)."""
    ents = state["entities"]
    path, cur, seen = [], eid, set()
    while cur in ents and cur not in seen and len(path) < MAX_DEPTH:
        seen.add(cur)
        rec = ents[cur]
        if not (rec.get("plug_required") and rec.get("plugged_in")):
            break
        out = rec.get("outlet")
        if not out or out not in ents:
            break
        path.append(out)
        if ents[out].get("outlet_kind", "wall") == "wall":
            break
        cur = out
    return path


def circuit_of(state, eid):
    """Breaker id feeding an entity (through its supply path), or None."""
    ents = state["entities"]
    rec = ents.get(eid) or {}
    if rec.get("plug_required"):
        path = supply_path(state, eid)
        if path and ents[path[-1]].get("outlet_kind", "wall") == "wall":
            return ents[path[-1]].get("circuit") or room_circuit(state, path[-1])
        if rec.get("plugged_in") and not path:
            return room_circuit(state, eid)
        return None
    if rec.get("category") == "outlet":
        return rec.get("circuit") or room_circuit(state, eid)
    if rec.get("circuit"):
        return rec["circuit"]
    if rec.get("hardwired") or rec.get("category") == "light" or isinstance(rec.get("appliance"), dict):
        return room_circuit(state, eid)
    return None


def plugs_in(state, oid, plug_ids=None):
    """[(socket, device_id)] plugged into a provider, by socket index (plug_ids: C.ids(w, "plug_devices") when available)."""
    out = []
    ents = state["entities"]
    for eid in (plug_ids if plug_ids is not None else sorted(ents)):
        rec = ents.get(eid)
        if rec is None:
            continue
        if rec.get("plugged_in") and rec.get("outlet") == oid:
            out.append((int(C.num(rec.get("_socket"), 0)), eid))
    return sorted(out)


def free_socket(state, oid):
    rec = state["entities"].get(oid) or {}
    used = {s for s, _ in plugs_in(state, oid)}
    for i in range(1, int(C.num(rec.get("receptacles"), 0)) + 1):
        if i not in used:
            return i
    return None


def is_provider(rec):
    return int(C.num(rec.get("receptacles"), 0)) > 0 and rec.get("outlet_kind") is not None


def active(rec):
    machine = rec.get("appliance")
    if isinstance(machine, dict) and machine.get("state") == "running":
        return True
    if rec.get("running") or rec.get("heating"):
        return True
    if rec.get("category") == "light":
        return bool(rec.get("lit", rec.get("on")))
    if rec.get("power_state") in ("rest",):
        return False
    return bool(rec.get("on"))


def draw_w(state, eid, rec=None, memo=None):
    """Instantaneous draw (W) of a metered consumer. Unknown/dedicated loads draw 0."""
    rec = rec if rec is not None else state["entities"].get(eid) or {}
    if rec.get("dedicated_circuit") or rec.get("power_w") is None and rec.get("_load_w") is None:
        return 0.0
    if rec.get("battery_powered") and not rec.get("plug_required"):
        return 0.0
    if not supply_ok(state, eid, 0, memo):
        return 0.0
    load = C.num(rec.get("_load_w"), 0.0)
    if rec.get("category") == "light":
        if rec.get("lit"):
            bulbs = rec.get("_bulb_w")
            return load + (C.num(bulbs, C.num(rec.get("power_w"), 10.0)) * max(0.2, C.num(rec.get("dim_level"), 1.0)))
        return load
    if active(rec):
        return load + C.num(rec.get("power_w"), 0.0)
    if rec.get("power_state") == "rest":
        return load + C.num(rec.get("rest_w"), C.num(rec.get("standby_w"), 0.0))
    return load + C.num(rec.get("standby_w"), 0.0)


def loads(state, w=None, memo=None):
    """One pass: ({breaker: W}, {provider: W}, {device: W}) for metered consumers (indexed when w is given)."""
    per_circuit, per_provider, per_device = {}, {}, {}
    ents = state["entities"]
    for eid in (C.ids(w, "metered") if w is not None else sorted(ents)):
        rec = ents.get(eid)
        if rec is None or rec.get("power_w") is None and rec.get("_load_w") is None:
            continue
        watts = draw_w(state, eid, rec, memo)
        if watts <= 0:
            continue
        per_device[eid] = watts
        if rec.get("plug_required"):
            path = supply_path(state, eid)
            for prov in path:
                if ents[prov].get("outlet_kind", "wall") != "wall":
                    per_provider[prov] = per_provider.get(prov, 0.0) + watts
            bid = circuit_of(state, eid) if path else None
        else:
            bid = rec.get("circuit")
        if bid:
            per_circuit[bid] = per_circuit.get(bid, 0.0) + watts
    return per_circuit, per_provider, per_device


def circuit_devices(state, bid):
    out = []
    for eid in sorted(state["entities"]):
        rec = state["entities"][eid]
        if rec.get("power_w") is None and rec.get("_load_w") is None and rec.get("category") != "outlet":
            continue
        if rec.get("category") == "outlet":
            continue
        if circuit_of(state, eid) == bid:
            out.append(eid)
    return out


# ----------------------------------------------------------------------------------------
# kernel hook

@hook("powered")
def _powered_hook(w, eid):
    rec = w.s["entities"].get(eid)
    if rec is None:
        return None
    return None if supply_ok(w.s, eid) else False


# ----------------------------------------------------------------------------------------
# mutations

def trip_breaker(w, bid, reason):
    rec = w.ent(bid)
    if rec.get("tripped") or not rec.get("on"):
        return False
    m = w.mut(bid)
    m["tripped"] = True
    m["handle"] = "tripped"
    m["trip_count"] = int(C.num(m.get("trip_count"), 0)) + 1
    m["last_trip"] = {"time_s": w.s.get("time_s", 0.0), "reason": reason}
    m["_heat"] = round(min(C.num(m.get("_heat"), 0.0), TRIP_HEAT) * 0.6, 3)
    C.event(w, "breaker_tripped", bid, reason=reason)
    C.log(w, "breaker_tripped", breaker=bid, reason=reason)
    return True


def trip_provider(w, oid, reason):
    rec = w.ent(oid)
    if rec.get("tripped"):
        return False
    m = w.mut(oid)
    m["tripped"] = True
    m["_heat"] = round(C.num(m.get("_heat"), 0.0) * 0.6, 3)
    C.event(w, "strip_tripped", oid, reason=reason)
    C.log(w, "strip_tripped", provider=oid, reason=reason)
    return True


def trip_gfci(w, oid, reason):
    rec = w.ent(oid)
    if not rec.get("gfci") or rec.get("gfci_tripped"):
        return False
    w.mut(oid)["gfci_tripped"] = True
    C.event(w, "gfci_tripped", oid, reason=reason)
    C.log(w, "gfci_tripped", outlet=oid, reason=reason)
    return True


def gfci_for(state, eid):
    """The GFCI device protecting an entity's supply (outlet itself or upstream), if any."""
    ents = state["entities"]
    path = supply_path(state, eid) if ents.get(eid, {}).get("plug_required") else ([eid] if ents.get(eid, {}).get("category") == "outlet" else [])
    if not path:
        return None
    wall = ents.get(path[-1]) or {}
    if wall.get("gfci"):
        return path[-1]
    up = wall.get("gfci_protected_by")
    if up and up in ents:
        return up
    bid = wall.get("circuit")
    if bid and (ents.get(bid) or {}).get("gfci_breaker"):
        return bid
    return None


# Flags that mark equipment built for wet locations (UL wet-location / IP44+ outdoor gear, weather-resistant
# covers): rain or a wipe-down does not make it leak to ground. Shared with outdoor_plants' WEATHER_RATED_FLAGS.
WEATHER_RATED = ("waterproof", "weatherproof", "outdoor_rated", "weather_rated")


def weather_rated(rec):
    return any(rec.get(flag) for flag in WEATHER_RATED)


def ground_fault(state, eid):
    """A soaked mains device or a wet outlet leaks current to ground, unless it is rated for wet locations."""
    rec = state["entities"].get(eid) or {}
    if rec.get("category") == "outlet":
        return C.num(rec.get("wetness"), 0.0) > 0.3 and not weather_rated(rec)
    if rec.get("_ground_fault"):
        return True
    return bool(rec.get("plug_required") and rec.get("plugged_in") and C.num(rec.get("wetness"), 0.0) > 0.4
                and not weather_rated(rec))


def shock_check(w, eid, cause):
    """Energized ground fault: a GFCI trips; without one it is an electrical hazard."""
    state = w.s
    if not ground_fault(state, eid):
        return None
    if not supply_ok(state, eid) and state["entities"][eid].get("category") != "outlet":
        return None
    protector = gfci_for(state, eid)
    if protector:
        prec = state["entities"][protector]
        if is_breaker(prec):
            trip_breaker(w, protector, "ground_fault")
        else:
            trip_gfci(w, protector, "ground_fault")
        return "gfci"
    w.stat("electrical_hazard")
    C.harm(w, "electrical.shock_risk:" + eid, cause=cause)
    return "hazard"


def robot_wet(w):
    """Wet grippers: the body's hand soil holds water (kernel v3.0 robot body: cleaning's wet wiping coats the fingers
    through body.soil_hand), the legacy hands_wet / hand_wetness flags, or a dripping wet thing in hand."""
    robot = w.robot
    if robot.get("hands_wet") or C.num(robot.get("hand_wetness"), 0.0) > 0.2:
        return True
    from ...kernel import body
    soil = body.get(w.s, "hand_soil") or {}
    if any(C.num((coat or {}).get("water"), 0.0) > 0.2 for coat in soil.values() if isinstance(coat, dict)):
        return True
    for held in w.held_items():
        rec = w.ent(held)
        if C.num(rec.get("wetness"), 0.0) > 0.5 and not rec.get("gear_kind"):
            return True
    return False


def robot_in_water(w):
    from ...kernel import geometry
    rid = w.robot["room"]
    pos = tuple(w.robot.get("pos") or (0, 0))
    room = w.s["rooms"].get(rid) or {}
    if C.num(room.get("water_depth_m"), 0.0) > 0.005 or room.get("flooded"):
        return True
    for kind, _ in geometry.hazard_cells(w.s, rid).get(pos, []):
        if kind in ("wet", "water", "flood", "puddle"):
            return True
    return False


def gas_present(state, rid):
    room = state["rooms"].get(rid) or {}
    for key in ("gas_ppm", "gas_concentration", "gas_level", "natural_gas_ppm"):
        value = room.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            # concentration fields are either ppm (>= 1000 ppm smells strongly) or 0..1 fractions of LEL
            if (value > 1.0 and value >= 1000) or (0.0 < value <= 1.0 and value >= 0.1):
                return True
    air = room.get("air") or {}
    return C.num(air.get("gas"), 0.0) >= 0.1 if isinstance(air, dict) else False


def switching_hazards(w, eid, cause):
    """Operating a panel/outlet with wet hands, standing in water or in a gas atmosphere."""
    rid = w.robot["room"]
    if robot_in_water(w) or robot_wet(w):
        w.stat("electrical_hazard")
        C.harm(w, "electrical.wet_contact:" + eid, cause=cause)
    if gas_present(w.s, rid):
        w.stat("electrical_hazard")
        C.harm(w, "electrical.spark_in_gas:" + eid, cause=cause)


# ----------------------------------------------------------------------------------------
# derived-state sync (runs after every electrical change and each substep)

def critical(rec):
    return bool(rec.get("must_stay_powered"))


@C.refresher(10)
def sync_energized(w, memo=None):
    """memo: provider-liveness cache from a pass over the same, unchanged supply state (power system)."""
    state = w.s
    ents = state["entities"]
    grid_on = state.get("utilities", {}).get("power", True)
    memo = {} if memo is None else memo
    for eid in C.ids(w, "powerable"):
        rec = ents.get(eid)
        if rec is None:
            continue
        cat = rec.get("category")
        if cat == "outlet" or is_provider(rec):
            C.set_if(w, eid, "live", provider_output_live(state, eid, 0, memo))
        if cat == "breaker":
            handle = "tripped" if rec.get("tripped") else ("on" if rec.get("on") else "off")
            C.set_if(w, eid, "handle", handle)
            continue
        relevant = rec.get("plug_required") or cat == "light" or rec.get("circuit") or rec.get("hardwired") or "energized" in rec
        if not relevant:
            continue
        ok = supply_ok(state, eid, 0, memo)
        before = rec.get("energized")
        if cat == "light":
            C.set_if(w, eid, "powered", ok)
        C.set_if(w, eid, "energized", ok)
        if SETTLING[0]:
            continue
        if before is True and not ok:
            power_lost(w, eid, "outage" if not grid_on else "local")
            memo.clear()        # transition handlers may switch things off
        elif before is False and ok:
            power_restored(w, eid)
            memo.clear()


def power_lost(w, eid, cause):
    """Consequences of a device losing mains power (once per transition)."""
    rec = w.ent(eid)
    visible = C.shows_light(w.s, eid)        # a lit lamp going dark, a screen going black (before the handlers run)
    m = None
    if critical(rec):
        m = w.mut(eid)
        m["power_interruptions"] = int(C.num(m.get("power_interruptions"), 0)) + 1
        if cause not in ("outage",):
            C.harm(w, "electrical.critical_power_loss:" + eid, cause=cause, kind=rec.get("critical_kind"))
    if rec.get("always_on") and cause not in ("outage", "restart"):
        C.harm(w, "electrical.always_on_interrupted:" + eid, cause=cause)
    for _, _, fn in LOST_HANDLERS:
        fn(w, eid, cause)
    C.event(w, "power_lost", eid, cause=cause)
    if visible:
        C.witness(w, "power_lost", eid, C.room_of(w.s, eid))
    if connected(rec):
        C.notice(w, "device", eid, remote=True, data={"status": "offline"})
    if rec.get("critical_kind") == "medical" and rec.get("power_fail_alarm", True) and not supply_ok(w.s, eid):
        # home oxygen concentrators and CPAPs sound a power-failure alarm when mains is lost without a backup pack
        C.notice(w, "alarm", eid, db=75.0, data={"device": "power_failure"})


def power_restored(w, eid):
    for _, _, fn in RESTORED_HANDLERS:
        fn(w, eid)
    if connected(w.s.get("entities", {}).get(eid) or {}):
        C.notice(w, "device", eid, remote=True, data={"status": "online"})


def connected(rec):
    """A smart device paired with the household hub or app: its status reaches the robot as `device` notices over the
    network (kernel v3.1 notice bus)."""
    return (rec.get("pairing") or {}).get("state") == "paired" if isinstance(rec.get("pairing"), dict) else False


LOST_HANDLERS = []
RESTORED_HANDLERS = []


def on_power_lost(order=100):
    def deco(fn):
        LOST_HANDLERS.append((order, fn.__module__ + "." + fn.__name__, fn))
        LOST_HANDLERS.sort(key=lambda t: (t[0], t[1]))
        return fn
    return deco


def on_power_restored(order=100):
    def deco(fn):
        RESTORED_HANDLERS.append((order, fn.__module__ + "." + fn.__name__, fn))
        RESTORED_HANDLERS.sort(key=lambda t: (t[0], t[1]))
        return fn
    return deco


# ----------------------------------------------------------------------------------------
# time system: loads, trips, cord heating, ground faults

def _cord_k(rec):
    k = 30.0
    if rec.get("outlet_kind") == "extension":
        coil = rec.get("coiled", "uncoiled")
        k = 95.0 if coil in ("tight", "coiled", True) else (55.0 if coil == "loose" else 30.0)
    if "under_rug" in (rec.get("cord_hazard") or []):
        k *= 1.6
    return k


_CHARGE_FIELDS = ("battery_level", "charge_rate_w", "charging", "charge_status", "charger")


def _power_ignore(rec):
    """Fields the power step never reads: charging and thermal bookkeeping (a cord's own temperature excepted)."""
    out = _CHARGE_FIELDS + ("internal_temp_c",)
    return out if rec.get("fixed_temperature") else out + ("temperature_c",)


POWER_IDLE = C.IdleNote("electrical.power", ("powerable", "metered", "plug_devices", "providers", "backup"),
                        ignore=_power_ignore)


_CORD_RATIOS = {}          # provider id -> load ratio of the last power step (its cord temperature model's input)
CORD_STEP = 1.0 - math.exp(-30.0 / 600.0)      # the cord temperature model's largest step fraction (max_dt 30 s)


def _power_inputs(w):
    """The power step's inputs outside the entity table: the grid, any timer on its schedule (time alone moves it) and
    the load ratio of each modelled cord (its temperature follows the room's, which climate keeps moving)."""
    state = w.s
    ents = state["entities"]
    timers, cords = False, {}
    for oid in C.ids(w, "providers"):
        rec = ents.get(oid) or {}
        if rec.get("outlet_kind") == "timer" and (rec.get("settings") or {}).get("mode", "auto") == "auto":
            timers = True
        if rec.get("fixed_temperature") and is_provider(rec) and rec.get("outlet_kind", "wall") != "wall":
            cords[oid] = _CORD_RATIOS.get(oid, 0.0)
    return {"grid": C.grid_on(state), "timers": timers, "cords": cords}


def _cord_steady(state, oid, ratio):
    """True when the cord temperature step would write nothing now: under 0.05 C of movement toward the room-driven
    target (for any substep up to 30 s) and no scorch or ignition threshold within reach."""
    from ...kernel.physics import room_temperature
    rec = state["entities"].get(oid)
    if rec is None:
        return False
    rid = C.room_of(state, oid)
    ambient = room_temperature(state, rid) if rid else 21.0
    target = ambient + _cord_k(rec) * ratio * ratio
    cur = C.num(rec.get("temperature_c"), ambient)
    if abs(target - cur) * CORD_STEP >= 0.05:
        return False
    top = max(cur, target)
    return (top < 75.0 or bool(rec.get("scorched"))) and (top < 110.0 or bool(rec.get("ignited")))


def power_idle(w):
    """Exact idle probe of electrical.power (common.IdleNote)."""
    note = POWER_IDLE
    if not note.quiet(w):
        return False
    extra = note.extra or {}
    state = w.s
    if extra.get("timers") or C.grid_on(state) != extra.get("grid"):
        return False
    return all(_cord_steady(state, oid, ratio) for oid, ratio in (extra.get("cords") or {}).items())


@system("electrical.power", order=56, max_dt=30.0, idle=power_idle)
def power_system(w, dt, tick):
    before = w.mutation_count
    _power_step(w, dt, tick)
    wrote = w.mutation_count != before
    POWER_IDLE.after(w, wrote, None if wrote else _power_inputs(w))


def _power_step(w, dt, tick):
    state = w.s
    ents = state["entities"]
    if not C.electrified(state, w):
        return
    breakers, providers, plugged = C.ids(w, "breakers"), C.ids(w, "providers"), C.ids(w, "plug_devices")
    if not (breakers or providers or plugged):
        return
    volts = C.voltage(state)
    memo = {}
    per_circuit, per_provider, per_device = loads(state, w, memo)
    total = round(sum(per_device.values()), 1)
    if C.num(C.section(state).get("house_load_w"), -1.0) != total:
        C.sec(w)["house_load_w"] = total
    changed = False
    shorted = _shorted_devices(state, w)
    # branch and main breakers
    mains = {}
    for bid in breakers:
        rec = ents.get(bid)
        if rec is None or rec.get("breaker_kind") == "main":
            continue
        load = per_circuit.get(bid, 0.0)
        main = main_of(state, bid)
        if main:
            mains[main] = mains.get(main, 0.0) + load
        changed |= _thermal_breaker(w, bid, rec, load, volts, dt, shorted)
    for mid, load in sorted(mains.items()):
        if mid in ents:
            changed |= _thermal_breaker(w, mid, ents[mid], load, volts, dt, shorted)
    # strips, cords, plug-in adapters
    for oid in providers:
        rec = ents.get(oid)
        if rec is None or not is_provider(rec) or rec.get("outlet_kind", "wall") == "wall":
            continue
        load = per_provider.get(oid, 0.0)
        rating = C.num(rec.get("rating_w"), 1875.0)
        ratio = load / rating if rating > 0 else 0.0
        if rec.get("has_breaker") and not rec.get("tripped"):
            heat = C.num(rec.get("_heat"), 0.0)
            if ratio >= 5.0:
                heat = STRIP_TRIP_HEAT
            elif ratio > 1.0:
                heat += dt * (ratio * ratio - 1.0)
            else:
                heat = max(0.0, heat - dt * 0.5)
            if round(heat, 3) != C.num(rec.get("_heat"), 0.0):
                w.mut(oid)["_heat"] = round(heat, 3)
            if heat >= STRIP_TRIP_HEAT:
                changed |= trip_provider(w, oid, "overload")
        if rec.get("fixed_temperature"):
            _CORD_RATIOS[oid] = ratio
            _cord_temperature(w, oid, rec, ratio, dt)
    # backup batteries carry their device through an outage and recharge on mains
    for eid in C.ids(w, "backup"):
        rec = ents.get(eid)
        if rec is None or C.num(rec.get("backup_battery_wh"), 0.0) <= 0:
            continue
        on_mains = mains_ok(state, eid)
        level = C.num(rec.get("backup_battery_level"), 0.0)
        if on_mains:
            new = min(1.0, level + dt * 50.0 / 3600.0 / C.num(rec.get("backup_battery_wh"), 100.0))
        else:
            draw = C.num(rec.get("power_w"), 50.0) if active(rec) else C.num(rec.get("standby_w"), 1.0)
            new = max(0.0, level - dt * draw / 3600.0 / C.num(rec.get("backup_battery_wh"), 100.0))
        if abs(new - level) > 1e-6:
            w.mut(eid)["backup_battery_level"] = round(new, 5)
        C.set_if(w, eid, "on_backup", not on_mains and new > 0)
        if level > 0 and new <= 0:
            changed = True
    # wall outlets with hidden arcing faults heat up and scorch under load; wet outlets leak to ground
    for oid in C.ids(w, "outlets"):
        rec = ents.get(oid)
        if rec is None:
            continue
        if rec.get("_fault") == "arcing":
            load = sum(C.num(draw_w(state, d), 0.0) for _, d in plugs_in(state, oid, plugged))
            if load > 0:
                m = w.mut(oid)
                m["_arc_s"] = round(C.num(m.get("_arc_s"), 0.0) + dt, 1)
                if m["_arc_s"] > 600 and not m.get("scorched"):
                    m["scorched"] = True
                    C.harm(w, "electrical.arcing_outlet_used:" + oid)
                if m["_arc_s"] > 2400 and load > 600:
                    w.stat("fire")
                    C.harm(w, "electrical.outlet_fire:" + oid)
        if C.num(rec.get("wetness"), 0.0) > 0.3 and ground_fault(state, oid) and provider_output_live(state, oid):
            if shock_check(w, oid, "wet_outlet"):
                changed = True
    # soaked or faulty corded devices leak to ground
    for eid in plugged:
        rec = ents.get(eid)
        if rec is None or rec.get("category") == "outlet" or not rec.get("plugged_in"):
            continue
        if rec.get("_ground_fault") or C.num(rec.get("wetness"), 0.0) > 0.4:
            if ground_fault(state, eid) and supply_ok(state, eid):
                if shock_check(w, eid, "wet_device"):
                    changed = True
    if changed:
        C.refresh(w)
    else:
        sync_energized(w, memo)     # nothing that feeds provider liveness changed since the loads pass


def _thermal_breaker(w, bid, rec, load_w, volts, dt, shorted=None):
    if not breaker_closed(rec):
        if C.num(rec.get("_heat"), 0.0) > 0:
            heat = max(0.0, C.num(rec.get("_heat"), 0.0) - dt * 0.5)
            w.mut(bid)["_heat"] = round(heat, 3)
        return False
    current = load_w / max(1.0, volts)
    rating = C.num(rec.get("rating_a"), 15.0)
    ratio = current / rating if rating > 0 else 0.0
    heat = C.num(rec.get("_heat"), 0.0)
    shorted = _circuit_short(w.s, bid, shorted)
    if ratio >= 5.0 or shorted:
        heat = TRIP_HEAT
    elif ratio > 1.0:
        heat += dt * (ratio * ratio - 1.0)
    else:
        heat = max(0.0, heat - dt * 0.5)
    if round(heat, 3) != C.num(rec.get("_heat"), 0.0):
        w.mut(bid)["_heat"] = round(heat, 3)
    m = w.ent(bid)
    if abs(C.num(m.get("load_a"), -1) - round(current, 2)) > 0.005:
        w.mut(bid)["load_a"] = round(current, 2)
    if heat >= TRIP_HEAT:
        return trip_breaker(w, bid, "short_circuit" if shorted or ratio >= 5 else "overload")
    return False


def _shorted_devices(state, w=None):
    """Devices that short their circuit when they run: a hidden `_short` (a crushed or bared cord, an internal fault) or
    liquid still inside (physical.py, phys.wetted)."""
    ents = state["entities"]
    candidates = C.ids(w, "powerable") if w is not None else sorted(ents)
    return [eid for eid in candidates if (ents.get(eid) or {}).get("_short") or C.water_logged(ents.get(eid) or {})]


def _circuit_short(state, bid, shorted=None):
    """A shorted device (crushed/exposed cord, internal fault) on an energized circuit."""
    ents = state["entities"]
    rec = ents.get(bid) or {}
    if rec.get("_fault") == "short":
        return True
    if rec.get("breaker_kind") == "main":
        return False
    for eid in (shorted if shorted is not None else _shorted_devices(state)):
        d = ents[eid]
        if circuit_of(state, eid) == bid and supply_ok(state, eid) and (active(d) or d.get("_short") == "always"):
            return True
    return False


def _cord_temperature(w, oid, rec, ratio, dt):
    from ...kernel.physics import room_temperature
    rid = C.room_of(w.s, oid)
    ambient = room_temperature(w.s, rid) if rid else 21.0
    target = ambient + _cord_k(rec) * ratio * ratio
    cur = C.num(rec.get("temperature_c"), ambient)
    tau = 600.0
    new = cur + (target - cur) * (1.0 - math.exp(-dt / tau))
    if abs(new - cur) >= 0.05:
        w.mut(oid)["temperature_c"] = round(new, 2)
    if new >= 75.0 and not rec.get("scorched"):
        w.mut(oid)["scorched"] = True
        w.stat("electrical_hazard")
        C.harm(w, "electrical.cord_overheat:" + oid, temperature_c=round(new, 1))
        C.event(w, "cord_overheating", oid)
    if new >= 110.0 and not rec.get("ignited"):
        w.mut(oid)["ignited"] = True
        w.stat("fire")
        C.harm(w, "electrical.cord_fire:" + oid)
        C.event(w, "cord_fire", oid)


# ----------------------------------------------------------------------------------------
# guards on core switch actions for breakers and strips

@before("switch_on", order=40)
def _breaker_switch_on_guard(ctx):
    rec = ctx.w.s["entities"].get(ctx.args.get("target"))
    if rec and is_breaker(rec) and rec.get("tripped"):
        raise ActionError("The breaker is tripped (handle stuck mid-way): push it fully OFF first, then ON (or use reset_breaker)", "precondition")
    if rec and is_provider(rec) and rec.get("tripped"):
        raise ActionError("The strip's overload breaker has tripped; remove load and press its reset button (reset_breaker)", "precondition")


@before("switch_on", order=39)
@before("switch_off", order=39)
def _wet_switch(ctx):
    """Operating a wet wall switch or device control can let water reach live parts."""
    w = ctx.w
    rec = w.s["entities"].get(ctx.args.get("target")) or {}
    if (C.num(rec.get("wetness"), 0.0) > 0.4 and (rec.get("category") in ("switch", "breaker") or rec.get("plug_required"))
            and not weather_rated(rec)):
        w.stat("electrical_hazard")
        C.harm(w, "electrical.wet_contact:" + ctx.args.get("target"), cause="wet_switch")


@after("switch_on", order=40)
def _breaker_switch_on(ctx, result):
    eid = ctx.args.get("target")
    rec = ctx.w.ent(eid)
    if is_breaker(rec):
        switching_hazards(ctx.w, eid, "switch_on")
        _after_close(ctx, eid)
    C.refresh(ctx.w)


@after("switch_off", order=40)
def _breaker_switch_off(ctx, result):
    eid = ctx.args.get("target")
    rec = ctx.w.ent(eid)
    if is_breaker(rec):
        switching_hazards(ctx.w, eid, "switch_off")
        if rec.get("tripped"):
            m = ctx.w.mut(eid)
            m["tripped"] = False
        C.log(ctx.w, "breaker_off", breaker=eid)
    C.refresh(ctx.w)


def _after_close(ctx, bid):
    """Closing a breaker onto a fault re-trips at once (arc); onto an overload it re-trips soon."""
    w = ctx.w
    state = w.s
    targets = [bid]
    rec = w.ent(bid)
    if rec.get("breaker_kind") == "main":
        targets = [b for b in C.ids(w, "breakers") if b in state["entities"] and main_of(state, b) == bid]
    for b in targets:
        if not circuit_live(state, b):
            continue
        if _circuit_short(state, b, _shorted_devices(state, w)):
            trip_breaker(w, b, "short_circuit")
            w.stat("electrical_hazard")
            C.harm(w, "electrical.closed_onto_fault:" + b)
    C.log(w, "breaker_on", breaker=bid)


# ----------------------------------------------------------------------------------------
# reset_breaker

@action("reset_breaker", params={"target": "entity"}, duration=5.0, energy=0.01, noise=0.1, category="physical",
        doc="Reset a tripped circuit breaker (handle fully OFF, then ON), press RESET on a tripped GFCI outlet, or reset a "
            "power strip's overload button. Panel breakers are risk 3: scoped approval reset_breaker:<id> and a consenting adult "
            "present. Remove the cause first: closing onto an overload re-trips, onto a fault arcs (electrical_hazard).")
def reset_breaker(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = ctx.obj(target, touch=True, grasp=w.s["entities"].get(target, {}).get("category") == "outlet")
    state = w.s
    if is_breaker(rec):
        require(rec.get("tripped") or not rec.get("on"), "That breaker is not tripped (handle is ON)")
        switching_hazards(w, target, "reset_breaker")
        m = w.mut(target)
        resets = int(C.num(m.get("resets_after_trip"), 0))
        if rec.get("tripped"):
            m["resets_after_trip"] = resets + 1
            if resets + 1 >= 2:
                C.harm(w, "electrical.repeated_reset:" + target, resets=resets + 1)
        m["tripped"] = False
        m["on"] = True
        m["handle"] = "on"
        ctx.data["kind"] = "breaker"
        _after_close(ctx, target)
        C.refresh(w)
        # an overload still present re-trips within the next few substeps (residual heat kept)
        per_circuit, _, _ = loads(state, w)
        load = per_circuit.get(target, 0.0)
        amps = load / max(1.0, C.voltage(state))
        rating = C.num(rec.get("rating_a"), 15.0)
        if amps > rating:
            w.mut(target)["_heat"] = round(max(C.num(w.ent(target).get("_heat"), 0.0), TRIP_HEAT * 0.75), 3)
        if w.ent(target).get("tripped"):
            return "The breaker snapped back to tripped immediately (fault on the circuit)"
        if ctx.realistic:
            return "Breaker {} reset (OFF then ON); the handle stays on".format(target)     # the load needs a clamp meter
        return "Breaker {} reset (OFF then ON); circuit load {:.1f} A of {:.0f} A".format(target, amps, rating)
    if rec.get("gfci"):
        require(rec.get("gfci_tripped"), "The GFCI is not tripped")
        switching_hazards(w, target, "gfci_reset")
        require(circuit_live(state, rec.get("circuit") or room_circuit(state, target)) and state.get("utilities", {}).get("power", True),
                "RESET will not latch: the outlet has no line power (check the breaker)")
        w.mut(target)["gfci_tripped"] = False
        plugged = C.ids(w, "plug_devices")
        faults = [d for _, d in plugs_in(state, target, plugged) if ground_fault(state, d)]
        downstream = [o for o in C.ids(w, "outlets") if (state["entities"].get(o) or {}).get("gfci_protected_by") == target]
        for o in downstream:
            faults += [d for _, d in plugs_in(state, o, plugged) if ground_fault(state, d)]
        if ground_fault(state, target) or faults:
            w.mut(target)["gfci_tripped"] = True
            C.refresh(w)
            return "The GFCI trips again at once: a ground fault is still present"
        C.refresh(w)
        return "GFCI {} reset".format(target)
    if is_provider(rec) and rec.get("has_breaker"):
        require(rec.get("tripped"), "The strip's breaker is not tripped")
        w.mut(target)["tripped"] = False
        _, per_provider, _ = loads(state, w)
        if per_provider.get(target, 0.0) > C.num(rec.get("rating_w"), 1875.0):
            w.mut(target)["_heat"] = round(STRIP_TRIP_HEAT * 0.8, 3)
        C.refresh(w)
        return "Strip {} reset".format(target)
    raise ActionError("That has no breaker or GFCI to reset", "precondition")
