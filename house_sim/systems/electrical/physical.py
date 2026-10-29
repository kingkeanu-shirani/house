"""Physics adoption (kernel v3.0, docs/PHYSICS.md; TIER1_PHYSICS 10.3 and 13, TIER1_INTEGRATION 6 "electrical").

The kernel owns falls, breaks, spills and wetting; this module adds what those consequences mean for electrical gear,
through the kernel's hooks:

  phys.wetted   liquid reaching electrical gear (a spill, an overflow, rain, a wet item set on it). Gear rated for
                water (weather_rated flags, an IP rating with a second digit of 4 or more: phones, watches, outdoor
                outlets) and a few drops (under INGRESS_L) are unaffected. The litres reaching a device add up while
                it stays wet (section `gathering`: drips arrive in 5 ml portions, the kernel's droplet limit, and a
                dripping pot's 20 ml get in as a tipped cup's do); once its wetness is down to GATHER_DRY (no film
                left: dried, wiped) what reached it before is gone (electrical.wet_gear, after the drying systems, and
                the dry-device refresher), and liquid that leaves no film on it (a seep into dry gear) does not
                gather. Energized mains gear leaks to ground: its GFCI (or GFCI breaker) trips, otherwise the event
                is an electrical hazard (shock risk); while still wet it shorts its circuit whenever it runs
                (power.py: the breaker trips under load). A battery device that is on shorts inside and dies (the
                kernel breaks it). Mains gear and anything unpowered take kernel `water_damage` and stay dead
                (`water_ingress`: no power-on, no light, no charge) until dry again (wetness at most DRY: cleaning's
                drying, a towel).
  phys.broke    a device the kernel broke (a fall onto a hard floor, a crush, a short): it is dead (power off, not
                functional, no charging) and a screen shows a crack (`screen_cracked`, glance tier).
  phys.props    a computer monitor's drop tolerance is its LCD panel's (0.45 m), not the electronics class's 1.2 m.

Electrical has no copy of a deferred law (TIER1_PHYSICS 10.2 lists it only for thermal_tau, the bulb and cord cooling
times): bulbs heat through their fixture's `internal_temp_c` (the kernel's heat-source parent) and cool with the kernel
thermal system, so the thermal_tau flip changes their cooling time and nothing else (tests/test_electrical.py
ThermalTauTests). Burns of people from a hot bulb are the kernel's burn_seconds rule (core hand-over checks).
"""
from ...kernel.plugins import hook, system
from . import common as C
from . import power as P

INGRESS_L = 0.02        # litres reaching a device before liquid gets inside (a mouthful from a tipped cup; drops do not)
GATHER_DRY = 0.001      # wetness at or below which no film is left on a device: the litres that reached it are gone
WET_BY_ROBOT = C.WET_BY_ROBOT
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


def gathered_l(state, eid):
    """Litres of liquid that reached a device since it was last dry, short of INGRESS_L (0 when none)."""
    return C.num((C.section(state).get("gathering") or {}).get(eid), 0.0)


def _gathers(w, eid, rec, liters):
    """Liquid reaching a device adds to what reached it since it was last dry: True once the sum is INGRESS_L or more
    (the gathering is spent: liquid gets in), else the sum is kept while the device stays wet, so drips add up as a
    single splash does. A device left without a film (wetness at most GATHER_DRY: what reached it ran off or seeped
    into dry gear) keeps nothing: only this arrival counts."""
    film = C.num(rec.get("wetness"), 0.0) > GATHER_DRY
    gathering = C.section(w.s).get("gathering") or {}
    total = round(C.num(liters) + (C.num(gathering.get(eid), 0.0) if film else 0.0), 4)
    keep = film and total < INGRESS_L
    if eid in gathering or keep:
        s = C.sec(w)
        mine = s.setdefault("gathering", {})
        if keep:
            mine[eid] = total
        else:
            mine.pop(eid, None)
            if not mine:
                s.pop("gathering", None)
    return total >= INGRESS_L


def _dried_gatherings(w):
    """Ids in the gathering whose device is gone or dry again (wetness at most GATHER_DRY), sorted."""
    gathering = C.section(w.s).get("gathering")
    if not gathering:
        return []
    ents = w.s["entities"]
    return sorted(eid for eid in gathering if eid not in ents or C.num(ents[eid].get("wetness"), 0.0) <= GATHER_DRY)


def forget_dried(w):
    """Drop the gathered litres of every device that is dry again or gone. Returns True when it wrote."""
    gone = _dried_gatherings(w)
    if not gone:
        return False
    s = C.sec(w)
    mine = s["gathering"]
    for eid in gone:
        mine.pop(eid, None)
    if not mine:
        s.pop("gathering", None)
    return True


def wet_gear_idle(w):
    """Exact idle probe: no gathering device is dry again or gone (an empty gathering: nothing to look at)."""
    return not _dried_gatherings(w)


@system("electrical.wet_gear", order=77, max_dt=30.0, idle=wet_gear_idle)
def wet_gear_system(w, dt, tick):
    """After the drying systems (cleaning.drying 75; appliances_cooking.drying 76 when cleaning is not loaded): a device
    they dried keeps no gathered litres for the next drip, also in a wait in which no electrical refresh runs."""
    forget_dried(w)


@hook("phys.wetted")
def _liquid_ingress(w, eid, liters, liquid, source):
    state = w.s
    rec = state["entities"].get(eid)
    if rec is None or not electrical_gear(rec):
        return
    if not rec.get(WET_BY_ROBOT) and (C.robot_acting(w) or C.robot_acted_on(w, [source])):
        # the robot wetted it (its action spilled, poured or wiped, or it arranged the source): a ground fault this
        # water causes later (power.shock_check in a time step, a GFCI breaker cutting the circuit) is the robot's
        # (common.robot_acted_on reads the marker; sync_dry_devices clears it once the gear is dry)
        w.mut(eid)[WET_BY_ROBOT] = True
    if splash_proof(rec) or rec.get("broken") or not _gathers(w, eid, rec, liters):
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
            # the robot's when its action spilled it, or it arranged the source or acted on the device or its supply;
            # rain or a resident's spill on gear the robot never touched is exogenous (common.consequence)
            C.consequence(w, "electrical.shock_risk:" + eid, P.causal_ids(state, eid) | {source},
                          stat="electrical_hazard", cause="liquid_ingress")
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


@C.declare_index("water_logged", ("plug_required", "battery_powered", "rechargeable", "power_state", "category"))
def _may_take_water(r):
    """Static index predicate: things whose liquid ingress this module tracks (mains, battery and rechargeable gear)."""
    return bool(r.get("plug_required") or r.get("battery_powered") or r.get("rechargeable") or "power_state" in r
                or r.get("category") in GEAR_CATEGORIES)


@C.refresher(5)
def sync_dry_devices(w):
    """A water-logged device works again once it is dry (cleaning's drying, a towel): clear water_ingress, and the
    robot-wetting marker of any dry gear; a device without a film keeps no gathered litres."""
    state = w.s
    forget_dried(w)
    for eid in C.ids(w, "water_logged", _may_take_water):
        rec = state["entities"].get(eid)
        if rec is None or C.num(rec.get("wetness"), 0.0) > DRY:
            continue
        if rec.get("water_ingress"):
            w.mut(eid)["water_ingress"] = False
        if rec.get(WET_BY_ROBOT):
            w.mut(eid).pop(WET_BY_ROBOT, None)
