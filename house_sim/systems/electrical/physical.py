"""Physics adoption (kernel v3.0, docs/PHYSICS.md; TIER1_PHYSICS 10.3 and 13, TIER1_INTEGRATION 6 "electrical").

The kernel owns falls, breaks, spills and wetting; this module adds what those consequences mean for electrical gear,
through the kernel's hooks:

  phys.wetted   liquid reaching electrical gear (a spill, an overflow, rain, a wet item set on it). Gear rated for
                water (weather_rated flags, an IP rating with a second digit of 4 or more: phones, watches, outdoor
                outlets) and a few drops (under INGRESS_L) are unaffected. Energized mains gear leaks to ground: its
                GFCI (or GFCI breaker) trips, otherwise the event is an electrical hazard (shock risk); while still
                wet it shorts its circuit whenever it runs (power.py: the breaker trips under load). A battery device
                that is on shorts inside and dies (the kernel breaks it). Mains gear and anything unpowered take
                kernel `water_damage` and stay dead (`water_ingress`: no power-on, no light, no charge) until dry
                again (wetness at most DRY: cleaning's drying, a towel).
  phys.broke    a device the kernel broke (a fall onto a hard floor, a crush, a short): it is dead (power off, not
                functional, no charging) and a screen shows a crack (`screen_cracked`, glance tier).
  phys.props    a computer monitor's drop tolerance is its LCD panel's (0.45 m), not the electronics class's 1.2 m.

Electrical has no copy of a deferred law (TIER1_PHYSICS 10.2 lists it only for thermal_tau, the bulb and cord cooling
times): bulbs heat through their fixture's `internal_temp_c` (the kernel's heat-source parent) and cool with the kernel
thermal system, so the thermal_tau flip changes their cooling time and nothing else (tests/test_electrical.py
ThermalTauTests). Burns of people from a hot bulb are the kernel's burn_seconds rule (core hand-over checks).
"""
from ...kernel.plugins import hook
from . import common as C
from . import power as P

INGRESS_L = 0.02        # litres reaching a device before liquid gets inside (a mouthful from a tipped cup; drops do not)
DRY = C.DRY_WETNESS     # wetness at or below which a water-logged device works again
SCREEN_CATEGORIES = ("tv", "monitor", "phone", "tablet", "computer", "watch", "smartwatch")
GEAR_CATEGORIES = ("outlet", "breaker", "breaker_panel", "switch", "light", "light_bulb", "detector", "charger", "tv",
                   "monitor", "audio", "media_source", "computer", "network", "phone", "tablet", "watch", "smartwatch",
                   "remote", "clock")      # not cables: a wet cable jacket is no harm (its plugs dry with the device)


def electrical_gear(rec):
    """Gear whose electronics liquid can reach: corded and battery devices, receptacles, switches, fixtures, alarms."""
    return bool(rec.get("plug_required") or rec.get("category") in GEAR_CATEGORIES or "power_state" in rec
                or rec.get("rechargeable") or rec.get("battery_powered") or rec.get("outlet_kind")
                or rec.get("hardwired") or rec.get("captive_connector"))


def splash_proof(rec):
    """Rated for water: weather-rated gear, or an IP rating whose second digit is 4 or more (IPX4 splashing water;
    phones are IP67/IP68)."""
    if P.weather_rated(rec):
        return True
    rating = str(rec.get("ip_rating") or "")
    return len(rating) >= 4 and rating[:2].upper() == "IP" and rating[3].isdigit() and int(rating[3]) >= 4


def energized(state, eid, rec):
    """Live parts inside right now: mains reaching a corded, hardwired or wired device (or a live receptacle), or a
    battery device that is switched on."""
    if rec.get("category") in ("outlet", "breaker") or P.is_provider(rec):
        return bool(rec.get("live", P.supply_ok(state, eid))) if rec.get("category") != "breaker" else P.breaker_closed(rec)
    if rec.get("plug_required") or rec.get("hardwired") or rec.get("category") in ("light", "switch") or rec.get("circuit"):
        return bool(P.mains_ok(state, eid))
    if rec.get("battery_powered") or rec.get("rechargeable"):
        on = rec.get("power_state") in ("on", "booting") or (rec.get("power_state") is None and rec.get("on"))
        return bool(on and C.num(rec.get("battery_level"), 1.0) > 0)
    return False


MONITOR_DROP_M = 0.45   # an LCD monitor's panel cracks when it falls off a desk (the electronics class says 1.2 m)


@hook("phys.props")
def monitor_props(record, phys):
    """Drop tolerance of a computer monitor: its glass panel is what breaks, not the electronics class's case (a phys.props
    refinement, so the generator's records keep their fields; a record's own drop_tolerance_m wins)."""
    if record.get("drop_tolerance_m") is None:
        return {"drop_tol_m": MONITOR_DROP_M}
    return None


monitor_props.applies = lambda record: record.get("type") == "computer_monitor" or record.get("category") == "monitor"


@hook("phys.wetted")
def _liquid_ingress(w, eid, liters, liquid, source):
    state = w.s
    rec = state["entities"].get(eid)
    if rec is None or not electrical_gear(rec) or splash_proof(rec) or C.num(liters) < INGRESS_L or rec.get("broken"):
        return
    from ...kernel.phys import impact
    live = energized(state, eid, rec)
    mains = rec.get("plug_required") or rec.get("hardwired") or rec.get("circuit") or \
        rec.get("category") in ("outlet", "light", "switch") or P.is_provider(rec)
    if not live or mains:
        if not rec.get("water_ingress"):
            w.mut(eid)["water_ingress"] = True
            impact.damage(w, eid, "water_damage", 0.5, detail="liquid got inside", by=None)
        C.log(w, "water_ingress", device=eid, liquid=liquid, powered=live)
        if not live:
            C.refresh(w)
            return
    C.event(w, "water_ingress", eid, liquid=liquid)
    if mains:
        protector = P.gfci_for(state, eid) if rec.get("plug_required") or rec.get("category") == "outlet" else None
        if protector and protector in state["entities"]:
            if P.is_breaker(state["entities"][protector]):
                P.trip_breaker(w, protector, "ground_fault")
            else:
                P.trip_gfci(w, protector, "ground_fault")
        else:
            w.stat("electrical_hazard")
            C.harm(w, "electrical.shock_risk:" + eid, cause="liquid_ingress")
    else:
        impact.break_entity(w, eid, "short_circuit")
    C.refresh(w)


@hook("phys.broke")
def _device_broken(w, eid, info):
    rec = w.s["entities"].get(eid)
    if rec is None or not electrical_gear(rec):
        return
    m = w.mut(eid)
    if "power_state" in rec and rec.get("power_state") != "off":
        m["power_state"] = "off"
        m.pop("boot_remaining_s", None)
    if rec.get("on") and rec.get("category") != "light":
        m["on"] = False
    if "functional" in rec or rec.get("battery_powered") or rec.get("rechargeable"):
        m["functional"] = False
    mechanical = info.get("cause") not in ("short_circuit",)      # a short kills it; only an impact cracks the glass
    if mechanical and (rec.get("category") in SCREEN_CATEGORIES or rec.get("type") in ("laptop", "laptop_barrel", "laptop_computer")):
        if not rec.get("screen_cracked"):
            m["screen_cracked"] = True
            C.event(w, "screen_cracked", eid, cause=info.get("cause"))
    C.log(w, "device_broken", device=eid, cause=info.get("cause"))
    C.refresh(w)


@C.refresher(5)
def sync_dry_devices(w):
    """A water-logged device works again once it is dry (cleaning's drying, a towel): clear water_ingress."""
    state = w.s
    for eid in C.ids(w, "water_logged", lambda r: bool(r.get("plug_required") or r.get("battery_powered")
                                                         or r.get("rechargeable") or "power_state" in r
                                                         or r.get("category") in GEAR_CATEGORIES)):
        rec = state["entities"].get(eid)
        if rec is not None and rec.get("water_ingress") and C.num(rec.get("wetness"), 0.0) <= DRY:
            w.mut(eid)["water_ingress"] = False
