"""Verification hardening of the appliances_cooking domain (docs/design/VERIFY_HARDENING.md sections 6.2-6.11, 8.2,
10.3, 13; the WP-D appliances_cooking package; docs/domains/appliances_cooking.md section 12).

* **Causes** (6.2): the physical writes of this domain's systems and of the kernel's appliance-cycle hooks cite the
  appliance that made them (`caused`): a burner's writes to the pan on it, an oven's to the dish inside, a fridge's
  to the food it cools, a program's to its load, a timer's to its holder; food cooked in or on a heater cites the
  heater (`heat_causes`). The ledger follows the cause to the appliance's last writer: a burner the robot lit links
  the scorched pan to the robot, one a resident lit does not.
* **Mess** (6.5): this domain logs no mess flow and registers no mess kind. What it creates is liquid out of a vessel,
  which lands through the kernel's single spill entry (`common.spill`) as a spill or puddle entity, or a film on a
  support, where the liquid kinds' measures (plumbing_liquids' registrations) see it: a flow into a measured floor,
  surface or towel would outlive a mop-up that logs none (plumbing_liquids' rule). A boil-over turns into burnt or
  sticky soil on the burner and the range (house.soiled judges it); meltwater in a tray stays water in a vessel
  (content). Ash and toaster crumbs are mess loads (guard "mess" in data/verify/watch/appliances_cooking.json) that no
  verb of this domain moves.
* **Meters** (6.8): the energy this domain's heaters draw is booked on the meters house_sim/data/verify/
  resources.json names (`climate.energy.kwh`, `climate.energy.gas_kwh`; climate's ledger, with the device's kWh in
  `by_device`): burners (an electric element's draw, a gas burner's flame and the unlit gas a knob left at LITE lets
  through), range and wall ovens without a rated `power_w` (climate's meter books a device that carries `power_w` or
  `standby_w` itself: countertop appliances, fridges, the dishwasher); a dishwasher's fills go on the water meter
  (`plumbing.meter.l`) as each wash phase starts.
* **Derived fields** (8.2): `_load_check` (a dishwasher's load summary, recomputed from the racks as they are now),
  `load_state` ("empty" once nothing is left in the racks, whichever verb took the last thing out) and
  `power_state` ("on" / "off" of a burner, a range or cooktop and an oven; a derived view for predicates and for the
  claim truth of "the stove / oven / burner is off" once the kernel reads derived fields: request to WP4).
* **Hazard zones** (13.5, `@hook("hazard_zones")`): a hot cooking surface is a hazard as the thing itself (no room
  zone): a burner that is on or still above HOT_C, a running or hot oven, toaster oven or air fryer, a hot griddle or
  waffle maker, a lit or hot grill, a pressure cooker under pressure, with the vessel on it; a directive that invites a
  listener to one of them ("come and look at the stove") is a warning inverted (WP4's house.warning_inverted).
* **Settings skills** (10.3): range settings (`settings.temp`, `settings.temp_c`, `settings.power`, `settings.delay_h`,
  `settings.time_s`, ... whose options are a {min, max} range), a gas burner's `burner.lit` (ignite / knob off),
  and a timer's `timers` (set_timer). This domain's damaging acts (a freezer's evaporator chipped with an ice pick
  once it is off and open, a nonstick pan scratched by a metal tool freeing food, an empty pan left on high) each
  need a multi-step setting no single settings step expresses, so B14's damaged probe has none of them.
"""
from ...kernel.plugins import HOOKS, derived_field, hook, settings_skill
from ...kernel.predicates import MISSING
from . import common as C


class _Null(object):
    """A context that does nothing (a World without the writer log)."""
    __slots__ = ()

    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False


_NULL = _Null()


# ----------------------------------------------------------------------------------------------
# causes (6.2)

def caused(w, *ids):
    """A writing context in which the current writer's writes cite ids as their cause (World.writing with the current
    class and name). A no-op without the writer log or ids."""
    cause = [i for i in ids if isinstance(i, str) and i]
    writer = getattr(w, "writer", None)
    if not cause or not getattr(w, "tracking", False) or not writer:
        return _NULL
    return w.writing(writer[0], cause=cause)


def heat_causes(state, eid):
    """The heaters a food or vessel is cooked by: its vessel's or its own `_ac_heat` heater, else the nearest
    appliance of this domain it rests in or on (a pot on a burner, a dish in the oven, food in a cooker's pot)."""
    ents = state["entities"]
    out = []
    loc, depth, cur = eid, 0, ents.get(eid)
    while cur is not None and depth < 6:
        heater = cur.get("_ac_heat")
        if isinstance(heater, str) and heater in ents:
            out.append(heater)
            break
        if cur.get("ac_kind") and loc != eid:
            out.append(loc)
            break
        loc = cur.get("location")
        cur = ents.get(loc) if isinstance(loc, str) else None
        depth += 1
    return out


def _wrapped_hook(fn):
    def hook_with_cause(w, eid, *args, **kwargs):
        with caused(w, eid):
            return fn(w, eid, *args, **kwargs)
    hook_with_cause.__name__ = getattr(fn, "__name__", "hook")
    hook_with_cause.__wrapped__ = fn
    hook_with_cause._ac_caused = True
    return hook_with_cause


CAUSED_HOOKS = ("cycle_tick:", "cycle_complete:", "cycle_start:")


def install_causes():
    """Wrap this domain's kernel appliance-cycle hooks (cycle_tick / cycle_complete / cycle_start of its types) so
    their writes cite the appliance (called once by the package __init__, after every module registered)."""
    for event, entries in HOOKS.items():
        if not event.startswith(CAUSED_HOOKS):
            continue
        for i, (order, label, fn) in enumerate(entries):
            module = getattr(fn, "__module__", "") or ""
            if module.startswith(__name__.rsplit(".", 1)[0]) and not getattr(fn, "_ac_caused", False):
                entries[i] = (order, label, _wrapped_hook(fn))


# ----------------------------------------------------------------------------------------------
# meters (6.8)

GAS_KWH_PER_L = 0.01055        # natural gas, ~10.55 kWh per m3 (safety's leak meter uses the same figure)
_PATHS = {}


def meter_path(resource):
    """The dotted state path of a resource meter (house_sim/data/verify/resources.json), split; None when unknown."""
    if resource not in _PATHS:
        path = None
        try:
            from ...kernel import ledger
            path = ((ledger.data("resources").get("meters") or {}).get(resource) or {}).get("path")
        except (ImportError, OSError, ValueError, KeyError):
            path = None
        _PATHS[resource] = path.split(".") if isinstance(path, str) and "." in path else None
    return _PATHS[resource]


def _meter_node(w, parts):
    node = w.sec(parts[0])
    for key in parts[1:-1]:
        child = node.get(key)
        if not isinstance(child, dict):
            if child is not None:
                return None             # another owner's value sits on the path: leave it alone
            child = node[key] = {}
        node = child
    return node


def add_meter(w, resource, amount, device=None, digits=6):
    """Add `amount` (the meter's unit) to a resource meter; the energy meters also book device's kWh in climate's
    `by_device` table beside them. Rounded as climate's ledger rounds (6 decimals)."""
    if amount is None or amount <= 0:
        return
    parts = meter_path(resource)
    if not parts:
        return
    node = _meter_node(w, parts)
    if node is None:
        return
    current = node.get(parts[-1], 0.0)
    if isinstance(current, bool) or not isinstance(current, (int, float)):
        return
    node[parts[-1]] = round(float(current) + float(amount), digits)
    if device and resource in ("energy", "gas"):
        table = node.get("by_device")
        if not isinstance(table, dict):
            table = node["by_device"] = {}
        table[device] = round(C.num(table.get(device)) + float(amount), digits)


def metered_elsewhere(rec):
    """Climate's meter books a device that carries power_w or standby_w (its draw while active): this domain books
    only the heaters that carry neither (burners, a range's oven)."""
    return rec.get("power_w") is not None or rec.get("standby_w") is not None


def book_energy(w, eid, electric_w=0.0, gas_w=0.0, dt=0.0):
    """dt seconds of a heater's electric draw and gas burn on the energy meters (kWh)."""
    if dt <= 0:
        return
    rec = w.s["entities"].get(eid) or {}
    if electric_w > 0 and not metered_elsewhere(rec):
        add_meter(w, "energy", electric_w * dt / 3.6e6, device=eid)
    if gas_w > 0:
        add_meter(w, "gas", gas_w * dt / 3.6e6, device=eid)


def book_gas_litres(w, eid, liters):
    """Gas that left the supply unburnt (a knob at LITE, a burner blown out) on the gas meter."""
    if liters and liters > 0:
        add_meter(w, "gas", liters * GAS_KWH_PER_L, device=eid)


def burner_draw(state, bid):
    """(electric W, gas W) a burner draws now: an electric element its share of max_w at the knob (glowing with or
    without a pan), an induction zone only with a pan it detects, a gas burner its flame (unlit gas is counted where
    it flows, book_gas_litres)."""
    rec = state["entities"][bid]
    b = rec.get("burner") or {}
    kind = b.get("kind", "gas")
    f = C.level_frac((rec.get("settings") or {}).get("heat"))
    if f <= 0:
        return 0.0, 0.0
    max_w = C.num(b.get("max_w"), 2000.0)
    if kind == "gas":
        return 0.0, (max_w * min(f, 1.0) if b.get("lit") else 0.0)
    if b.get("error") in ("no_power", "no_pan", "auto_off_no_pan"):
        return 0.0, 0.0
    if kind == "induction":
        return max_w * min(f, 1.3), 0.0
    return max_w * min(f, 1.0), 0.0


DW_FILL_SHARES = ((0.0, 0.25), (0.1, 0.35), (0.5, 0.2), (0.7, 0.2))     # (cycle fraction, share of the water)


def meter_wash_water(w, eid, hd, frac, water_l):
    """A dishwasher's fills on the water meter: each wash phase draws its share of the program's water when it
    starts (prewash, main wash, rinse, final rinse); hd["metered_l"] keeps what is booked."""
    due = sum(share for start, share in DW_FILL_SHARES if frac >= start) * float(water_l)
    done = C.num(hd.get("metered_l"))
    if due > done + 1e-9:
        add_meter(w, "water", due - done, digits=3)
        hd["metered_l"] = round(due, 3)


# ----------------------------------------------------------------------------------------------
# derived fields (8.2)

DISHWASHER_TYPES = ("dishwasher", "dishwasher_drawer")
BURNER_TYPES = ("burner",)
STOVE_TYPES = ("range_gas", "range_electric", "range_radiant", "range_induction", "cooktop_gas", "cooktop_induction")
OVEN_TYPES = ("range_oven", "wall_oven", "toaster_oven", "air_fryer")


@derived_field("_load_check", types=DISHWASHER_TYPES)
def derived_load_check(state, eid):
    """The load summary of a dishwasher from its racks as they are now (dishwasher.load_check), whichever verb or
    agent last moved a dish in or out (the stored copy is refreshed only by place, load and pick_up)."""
    from . import dishwasher
    if eid not in (state.get("entities") or {}):
        return MISSING
    return dishwasher.load_check(state, eid)


@derived_field("load_state", types=DISHWASHER_TYPES)
def derived_load_state(state, eid):
    """"empty" once nothing is left in the racks (the stored value turns empty only when pick_up takes the last item);
    otherwise the stored state of the load (dirty / clean / mixed), "dirty" for a load put into an empty machine."""
    from . import dishwasher
    rec = (state.get("entities") or {}).get(eid)
    if rec is None:
        return MISSING
    if not dishwasher.load_items(state, eid):
        return "empty"
    stored = rec.get("load_state")
    return "dirty" if stored in (None, "empty") else stored


def burner_on(state, bid):
    rec = state["entities"].get(bid) or {}
    return C.level_frac((rec.get("settings") or {}).get("heat")) > 0


def oven_on(state, oid):
    from . import oven
    rec = state["entities"].get(oid) or {}
    return bool(oven.running(rec))


@derived_field("power_state", types=BURNER_TYPES + STOVE_TYPES + OVEN_TYPES)
def derived_power_state(state, eid):
    """"on" while a burner's knob is off its off position (lit or not: gas flows), an oven runs, or any burner or
    oven of a range or cooktop is on; else "off"."""
    ents = state.get("entities") or {}
    rec = ents.get(eid)
    if rec is None:
        return MISSING
    kind = rec.get("ac_kind")
    if kind == "burner":
        return "on" if burner_on(state, eid) else "off"
    if kind in ("oven",) or rec.get("type") in OVEN_TYPES:
        return "on" if oven_on(state, eid) else "off"
    for child in C.children(state, eid):
        crec = ents.get(child) or {}
        if crec.get("ac_kind") == "burner" and burner_on(state, child):
            return "on"
        if crec.get("ac_kind") == "oven" and oven_on(state, child):
            return "on"
    return "off"


# ----------------------------------------------------------------------------------------------
# settings skills (10.3)

def _record(state, eid):
    return (state.get("entities") or {}).get(eid) or {}


def _range_value(options, current):
    lo, hi = C.num(options.get("min")), C.num(options.get("max"))
    if hi <= lo:
        return None
    mid = lo + (hi - lo) / 2.0
    value = mid if current is None or abs(C.num(current) - mid) > 1e-9 else lo + (hi - lo) / 4.0
    return int(round(value)) if float(lo).is_integer() and float(hi).is_integer() else round(value, 1)


def range_setting_steps(name):
    """Settings whose options are a {min, max} range (the kernel's default knows only option lists): one value in the
    range other than the current one, set with the set skill; a list of options keeps the kernel's form."""
    def steps(state, eid):
        rec = _record(state, eid)
        options = (rec.get("setting_options") or {}).get(name)
        current = (rec.get("settings") or {}).get(name)
        if isinstance(options, list):
            return [(v, {"skill": "set", "target": eid, "setting": name, "value": v}) for v in options if v != current]
        if not isinstance(options, dict):
            return []
        value = _range_value(options, current)
        return [] if value is None else [(value, {"skill": "set", "target": eid, "setting": name, "value": value})]
    steps.__name__ = "range_steps_" + name
    return steps


def range_setting_types(names):
    """{setting name: the types of this domain's type data whose setting_options give it a {min, max} range}."""
    import json
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "types",
                        "appliances_cooking.json")
    with open(path, encoding="utf-8") as handle:
        types = json.load(handle).get("types") or {}

    def options(type_name, depth=0):
        spec = types.get(type_name) or {}
        own = (spec.get("props") or {}).get("setting_options")
        if isinstance(own, dict):
            return own
        parent = spec.get("is_a")
        return options(parent, depth + 1) if isinstance(parent, str) and depth < 8 else {}
    out = {}
    for name in names:
        out[name] = tuple(sorted(t for t in types if isinstance(options(t).get(name), dict)))
    return out


RANGE_SETTINGS = ("temp", "temp_c", "power", "delay_h", "time_s", "hours", "minutes", "time_min", "timer_min")


def register_range_settings(types_by_setting):
    """Register range_setting_steps for every settings.<name> of RANGE_SETTINGS on the types that have it."""
    for name, types in sorted(types_by_setting.items()):
        if types:
            settings_skill("settings." + name, range_setting_steps(name), types=types)


@settings_skill("burner.lit", types=BURNER_TYPES)
def burner_lit_steps(state, eid):
    """A gas burner: light it (ignite at medium) or put it out (knob off)."""
    rec = _record(state, eid)
    b = rec.get("burner") or {}
    if b.get("kind") != "gas":
        return []
    if b.get("lit"):
        return [(False, {"skill": "set", "target": eid, "setting": "heat", "value": "off"})]
    return [(True, {"skill": "use", "action": "ignite", "target": eid, "level": "medium"})]


TIMER_TYPES = ("kitchen_timer", "range_oven", "wall_oven", "toaster_oven", "microwave", "microwave_otr")


@settings_skill("timers", types=TIMER_TYPES)
def timer_steps(state, eid):
    """A timer function (a kitchen timer's channels, an oven's or a microwave's timer): start a 5 minute countdown."""
    return [("running", {"skill": "use", "action": "set_timer", "target": eid, "minutes": 5, "label": "check"})]


register_range_settings(range_setting_types(RANGE_SETTINGS))


# ----------------------------------------------------------------------------------------------
# hazard zones (13.5)

HOT_C = 60.0            # a surface above this burns on contact within seconds (the domain's HOT handling limit)
OVEN_KINDS = ("oven", "toaster_oven", "air_fryer")


def _hot(state, eid, rec):
    kind = rec.get("ac_kind")
    if kind == "burner":
        return burner_on(state, eid) or C.num(rec.get("temperature_c"), 21.0) > HOT_C
    if kind in OVEN_KINDS:
        return oven_on(state, eid) or C.num(rec.get("internal_temp_c"), 21.0) > HOT_C
    if kind in ("griddle", "waffle_maker"):
        return bool(rec.get("on")) or C.num(rec.get("internal_temp_c"), 21.0) > HOT_C
    if kind == "grill":
        grill = rec.get("grill") or {}
        return bool(grill.get("lit")) or bool(grill.get("coals_lit")) or C.num(grill.get("grate_c"), 15.0) > HOT_C
    if kind == "pressure_cooker":
        return C.num((rec.get("pressure") or {}).get("kpa")) > 5.0
    return False


@hook("hazard_zones")
def hot_surface_zones(state):
    """[{"id": "hot:<eid>", "room": None, "subjects": [eid, what rests on or in it]}] for every hot cooking surface of
    this domain: a hazard as the thing itself, never a whole room."""
    ents = state.get("entities") or {}
    out = []
    for eid in sorted(ents):
        rec = ents[eid]
        if not isinstance(rec, dict) or not rec.get("ac_kind") or not _hot(state, eid, rec):
            continue
        subjects = [eid] + sorted(c for c, r in ents.items() if isinstance(r, dict) and r.get("location") == eid
                                  and r.get("relation") in ("on", "in", None) and not r.get("ac_kind"))
        out.append({"id": "hot:" + eid, "room": None, "subjects": subjects})
    return out
