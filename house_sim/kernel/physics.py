"""Shared lumped-parameter physics used by every domain.

* Power: is_powered(w, eid) combines grid power, plugs and circuit hooks.
* Water: water_available(w, fixture) combines utilities, main shutoff and valves.
* Thermal: object temperatures relax toward their ambient (Newton cooling). The
  ambient of an entity is its parent's internal_temp_c (ovens, fridges, burners),
  else its parent's temperature when the parent conducts_heat (pots, pans), else
  the room temperature (outdoors: weather temperature).
* Appliance cycles: generic programmable state machines (see appliance_* helpers);
  domains add per-type behavior with hooks "cycle_tick:<type>" and
  "cycle_complete:<type>" (also tried for each ancestor type in is_a).

These are approximate physical models (fidelity label "P-approx"), not rigid-body,
fluid or deformable simulation.

Kernel v3.0 (tier-1 physics, docs/PHYSICS.md): this module is the facade and the one place the physics layer
(house_sim/kernel/phys/) is wired into the engine. It registers the systems physics.environment (47, wind and rain on
objects) and physics.processes (48, timed checks and continuous processes), runs the kernel thermal system through
phys.thermal.thermal_step (the active-set optimisation; temperature writes through World.set_quiet), the physics.*
perturbation event effects (phys/events.py), the observer physics.sounds and the inspector physics (phys/observe.py),
and registers the settle laws with the lawbook (register_laws, on the first settle). It re-exports the layer's public
functions (physics.fall, physics.spill, physics.lift_plan, ...). Owners: thermal_owner (or the older
fixed_temperature) makes the kernel thermal system leave an entity to its domain; tau_s follows the deferred
thermal_tau law.
"""
import hashlib
import math
from pathlib import Path

from .errors import ActionError, require
from .plugins import hook, inspector, observer, run_hooks, system


def room_temperature(state, rid):
    room = state["rooms"].get(rid, {})
    if room.get("outdoor"):
        return float(state.get("weather", {}).get("temperature_c", 18.0))
    return float(room.get("temperature_c", state.get("utilities", {}).get("indoor_temp_c", 21.0)))


def is_powered(w, eid):
    state = w.s
    entity = state["entities"].get(eid, {})
    if entity.get("battery_powered"):
        return float(entity.get("battery_level", 1.0)) > 0
    if not state.get("utilities", {}).get("power", True):
        # UPS / medical-device backup keeps a device powered through a grid outage
        return float(entity.get("backup_battery_level", 0.0) or 0.0) > 0
    if entity.get("plug_required") and not entity.get("plugged_in"):
        return False
    for verdict in run_hooks("powered", w, eid):
        if verdict is False:
            return False
    return True


def water_available(w, eid=None):
    utilities = w.s.get("utilities", {})
    if not utilities.get("water", True) or not utilities.get("main_water_open", True):
        return False
    if float(utilities.get("water_l", 1e9)) <= 0:
        return False
    if eid is not None:
        entity = w.s["entities"].get(eid, {})
        if entity.get("valve_open") is False:
            return False
        for verdict in run_hooks("water_available", w, eid):
            if verdict is False:
                return False
    return True


def _source(entities, entity):
    """Walk up through plain (non-insulated) containers to the nearest heat/cold source.

    Returns (temperature, coupling) or None when the entity sits in room air.
    A lidded box in a fridge is in fridge air; food in a pot on a burner follows the pot.
    """
    parent = entities.get(entity.get("location"))
    depth = 0
    while parent is not None and depth < 8:
        if parent.get("internal_temp_c") is not None:
            return float(parent["internal_temp_c"]), float(parent.get("heat_coupling", 1.0))
        if parent.get("conducts_heat"):
            return float(parent.get("temperature_c", 21.0)), float(parent.get("heat_coupling", 1.5))
        if parent.get("insulated"):
            return float(parent.get("temperature_c", 21.0)), 0.15
        if not (parent.get("container") or parent.get("accepts_items")):
            return None
        parent = entities.get(parent.get("location"))
        depth += 1
    return None


def ambient(state, eid):
    entities = state["entities"]
    found = _source(entities, entities.get(eid, {}))
    if found is not None:
        return found
    from .predicates import root_room
    rid = root_room(state, eid)
    if rid is None:
        return float(state.get("utilities", {}).get("indoor_temp_c", 21.0)), 1.0
    return room_temperature(state, rid), 1.0


def legacy_tau_s(entity):
    """The kernel's pre-v3.0 time constant: an explicit thermal_tau_s, else 300 sqrt(mass + liquid) + 60 s."""
    if entity.get("thermal_tau_s"):
        return float(entity["thermal_tau_s"])
    mass = max(0.01, float(entity.get("mass_kg", 0.4)) + float(entity.get("liquid_l", 0.0)))
    return 300.0 * math.sqrt(mass) + 60.0


def tau_s(entity):
    """Thermal time constant (s) the kernel uses: the lumped m c / (h A) of the thermal_tau law once it is active
    (phys.thermal.tau_s), until then legacy_tau_s."""
    from .phys import thermal
    return thermal.kernel_tau_s(entity)


def _room_map(state):
    """entity id -> root room for every entity (one pass, memoized)."""
    entities, rooms = state["entities"], state["rooms"]
    people, pets, robot_room = state.get("people", {}), state.get("pets", {}), state["robot"].get("room")
    memo = {}
    for eid in entities:
        chain, current = [], eid
        while True:
            if current in memo:
                result = memo[current]
                break
            if current in rooms:
                result = current
                break
            if current == "robot":
                result = robot_room
                break
            if current in people:
                result = people[current].get("location")
                break
            if current in pets:
                result = pets[current].get("location")
                break
            record = entities.get(current)
            if record is None or current in chain:
                result = None
                break
            chain.append(current)
            current = record.get("location")
        for item in chain:
            memo[item] = result
    return memo


def thermal_system(w, dt, tick):
    """The pre-v3.0 kernel thermal step (kept as the reference phys.thermal.thermal_step is proven identical to; the
    registered system is thermal_kernel below)."""
    state = w.s
    entities = state["entities"]
    temps = {rid: room_temperature(state, rid) for rid in state["rooms"]}
    indoor = float(state.get("utilities", {}).get("indoor_temp_c", 21.0))
    rooms_of = None
    for eid, entity in entities.items():
        if "temperature_c" not in entity or entity.get("fixed_temperature"):
            continue
        found = _source(entities, entity)
        if found is not None:
            target, coupling = found
        else:
            if rooms_of is None:
                rooms_of = _room_map(state)
            target, coupling = temps.get(rooms_of.get(eid), indoor), 1.0
        current = float(entity["temperature_c"])
        if abs(target - current) < 0.05:
            continue
        k = 1.0 - math.exp(-dt * coupling / legacy_tau_s(entity))
        w.mut(eid)["temperature_c"] = round(current + (target - current) * k, 3)


# ----- programmable appliances ------------------------------------------

def appliance(entity):
    return entity.get("appliance")


def appliance_types(entity):
    return [entity.get("type")] + list(entity.get("is_a", []))


def appliance_start(w, eid, program=None):
    entity = w.mut(eid)
    machine = entity.get("appliance")
    require(isinstance(machine, dict), "This has no programmable cycle", "capability")
    require(machine.get("state", "idle") in ("idle", "done", "paused", "off"), "It is already running")
    if machine.get("requires_power", True):
        require(is_powered(w, eid), "No power to the appliance", "resource")
    if machine.get("requires_closed", True) and entity.get("openable"):
        require(not entity.get("open"), "Close the door/lid first")
    if machine.get("state") == "paused" and program is None:
        machine["state"] = "running"
        return machine
    if program:
        require(program in machine.get("programs", {}), "Unknown program: " + program, "argument")
        machine["program"] = program
    name = machine.get("program") or next(iter(machine.get("programs", {"default": {}})), "default")
    machine["program"] = name
    spec = machine.get("programs", {}).get(name, {})
    if spec.get("water_l"):
        require(water_available(w, eid), "No water supply to the appliance", "resource")
    for verdict in run_hooks_typed("cycle_check", w, eid, entity):
        if isinstance(verdict, str):
            raise ActionError(verdict, "precondition")
        if isinstance(verdict, tuple):
            raise ActionError(verdict[0], verdict[1])
    machine["remaining_s"] = float(spec.get("duration_s", machine.get("duration_s", 60)))
    machine["elapsed_s"] = 0.0
    machine["state"] = "running"
    machine["error"] = None
    run_hooks_typed("cycle_start", w, eid, entity)
    w.emit("cycle_started", target=eid, program=name)
    return machine


def run_hooks_typed(prefix, w, eid, entity, *args):
    results = []
    for type_name in appliance_types(entity):
        results.extend(run_hooks(prefix + ":" + type_name, w, eid, *args))
    return results


@system("appliances", order=60, max_dt=30.0)
def appliance_system(w, dt, tick):
    state = w.s
    for eid in w.static_index("kernel.appliances", lambda r: isinstance(r.get("appliance"), dict)):
        entity = state["entities"].get(eid)
        if entity is None:
            continue
        machine = entity.get("appliance")
        if not isinstance(machine, dict) or machine.get("state") != "running":
            continue
        if machine.get("requires_power", True) and not is_powered(w, eid):
            m = w.mut(eid)["appliance"]
            m["state"], m["error"] = "paused", "power_loss"
            w.emit("cycle_interrupted", target=eid, reason="power_loss")
            continue
        m = w.mut(eid)["appliance"]
        step = min(dt, m.get("remaining_s", 0.0))
        m["remaining_s"] = round(max(0.0, m.get("remaining_s", 0.0) - dt), 3)
        m["elapsed_s"] = round(m.get("elapsed_s", 0.0) + step, 3)
        run_hooks_typed("cycle_tick", w, eid, w.ent(eid), step)
        if w.ent(eid)["appliance"].get("state") == "running" and m["remaining_s"] <= 0:
            m["state"] = "done"
            run_hooks_typed("cycle_complete", w, eid, w.ent(eid))
            w.emit("cycle_complete", target=eid)


# ----- the tier-1 physics layer (kernel v3.0): registrations and the facade --------------------------------------------
# Wired here and nowhere else (TIER1_INTEGRATION 1.16): the library modules under kernel/phys/ register nothing.

PHYSICS_DATA = Path(__file__).resolve().parent.parent / "data" / "physics"
_VERSION = []


def physics_version():
    """Digest of the physics data (data/physics/*.json, line endings normalized), recorded in replays
    (metadata.physics_version): a replay recorded under other physics data may diverge."""
    if not _VERSION:
        sha = hashlib.sha256()
        for path in sorted(PHYSICS_DATA.glob("*.json")):
            sha.update(path.name.encode("utf-8") + b"\0")
            sha.update(path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
        _VERSION.append(sha.hexdigest()[:16])
    return _VERSION[0]


def register_laws():
    """Register the kernel's settle laws (phys impact, liquids and thermal LAWS) with the lawbook. Called by the
    settle pass before it reads lawbook.laws(); idempotent (the lawbook ignores a function registered again), so the
    lawbook's own table stays empty in processes that never settle."""
    from .phys import impact as _impact_laws, lawbook as _lawbook, liquids as _liquid_laws, thermal as _thermal_laws
    if len(_lawbook.laws()) >= _LAW_COUNT[0]:
        return
    tables = _impact_laws.LAWS + _liquid_laws.LAWS + _thermal_laws.LAWS
    for law_id, order, fn in tables:
        _lawbook.law(law_id, order)(fn)
    _LAW_COUNT[0] = len(tables)


_LAW_COUNT = [1 << 30]       # set to the number of kernel laws once registered


from .phys import environment as _environment, events as _events, observe as _observe  # noqa: E402
from .phys import processes as _processes, thermal as _thermal  # noqa: E402


@system("physics.environment", order=47, max_dt=30.0)
def physics_environment(w, dt, tick):
    """Wind and rain on objects while the wind / rain laws are active (phys/environment.py)."""
    _environment.run(w, dt, tick)


@system("physics.processes", order=48, max_dt=30.0)
def physics_processes(w, dt, tick):
    """Timed physics checks and continuous processes (phys/processes.py): creep, rolling, drips, leaks, contact heat,
    freezing and melting."""
    _processes.run(w, dt, tick)


@system("thermal", order=50, max_dt=120.0, period=120.0)
def thermal_kernel(w, dt, tick):
    """The kernel thermal system: phys.thermal.thermal_step (Newton relaxation toward each entity's ambient with the
    active-set optimisation; thermal_owner entities and phase plateaus are left to their owners). Temperature writes go
    through World.set_quiet (journaled per field, no record copy), so they are no touches for the settle pass."""
    _thermal.thermal_step(w, dt, tick, setter=w.set_quiet)


observer("physics.sounds", order=125)(_observe.sounds)
inspector("physics", order=150)(_observe.inspect)
for _name, _fn in _events.EFFECTS:
    hook("event:" + _name)(_fn)


# public names of the layer (from house_sim.kernel import physics; physics.fall(...))
from .phys.contain import choking_hazard, fits, opening_ok  # noqa: E402,F401
from .phys.environment import exposed, rain_wet, wind_effect, wind_force_n  # noqa: E402,F401
from .phys.forces import friction, lift_plan, max_push_n, push_needed_n, robot_caps, scratch, tips_when_pushed  # noqa: E402,F401
from .phys.impact import (break_entity, collapse, crush, damage, fall, knock, landing_cell, robot_caused,  # noqa: E402,F401
                          setdown, slide, tear, tip_over)
from .phys.lawbook import active as law_active  # noqa: E402,F401
from .phys.liquids import absorb, drip_start, drip_stop, evaporation_mm_s, overflow, spill, transfer, wet  # noqa: E402,F401
from .phys.observe import emit_consequence  # noqa: E402,F401
from .phys.props import floor_props, liquid_props, material_class, props, vary  # noqa: E402,F401
from .phys.settle import settle, travel_of  # noqa: E402,F401
from .phys.statics import edge_margin_m, stability, tip_accel_g, tip_margin_nm  # noqa: E402,F401
from .phys.support import children_map, held_envelope, resting_size, subtree_mass, support_of, top_support  # noqa: E402,F401
from .phys.thermal import (burn_seconds, contact_check, dew_point_c, freeze_burst_check, heat_content_j,  # noqa: E402,F401
                           heat_limit_c, shock_check)
