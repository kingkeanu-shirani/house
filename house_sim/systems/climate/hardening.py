"""Verification-hardening prerequisites of the climate domain (docs/design/VERIFY_HARDENING.md section 25 "WP-D climate",
23.3; docs/domains/climate.md section 19).

  identify first  refill_reservoir, empty_reservoir and reverse_fan answer an unfamiliar thing in any entity parameter
                  with the kernel's one identify-first refusal (15.3; the phase-1 unfamiliar-thing rows), and the held
                  jug and water source a refill reads are passed over the same way; core install refuses an unfamiliar
                  part for one of this domain's slots before the slot is matched by the part's true type, and an
                  unfamiliar device before its true slots are read.
  derived fields  what this domain's hooks keep (8.2), recomputed from the state as it is now: a filter's `reversed` and
                  `bypass_gap_mm` (only while it sits in a slot), a purifier's `layer_order_ok`, a humidifier's
                  `tank_seated`, a corded device's `placement_issues`, `cord_crosses_walkway`, `cord_under_rug`.
  mess flows      reservoir_flow: empty_reservoir pours a vessel out where the measures cannot see the liquid (a drain,
                  a toilet, the ground): what was mess in the vessel (mop water, a used chemical) or becomes mess there
                  (a product on the ground) is logged with its origin, by plumbing_liquids' classes (6.5). Climate owns
                  no mess kind: clean water and condensate are content, spills go through the kernel's spill (measured
                  there), wet cells and soil are judged by house.wet_room and house.soiled.
  hazard zones    what a warning about this domain's hazards is about (13.5): a burst pipe's room, a running heater with
                  something scorched or draped on it, a warm-mist humidifier running with a child at home.
  settings        settings skills (10.3: B14's changed / damaged probes and the inverses of setting changes) for what
                  the kernel default (a list of options through `set`) does not reach: numeric settings, a pull-chain or
                  remote fan, a mini-split's remote, a filter's wrap, and the damaging verbs of this domain -- a ceiling
                  fan reversed while its blades coast (motor_damaged, wobble), a filter fitted backwards (reversed) --
                  through the skill climate.set_field.

Causes (6.2) live where the writes are (attribution.room_causes / hvac_causes and the systems' mut(cause=)); every
climate system is writer="physical": none carries out a person's or a pet's decision.
"""
import sys

from ...kernel.plugins import MESS_KINDS, before, derived_field, hook, settings_skill, skill
from ...kernel.predicates import MISSING
from . import common as C

VERBS = ("refill_reservoir", "empty_reservoir", "reverse_fan")
PLUMBING = __name__.rsplit(".climate.", 1)[0] + ".plumbing_liquids.hardening"


# ------------------------------------------------------------------------------------------- identify first (15.3)

def unfamiliar_first(ctx):
    """An unfamiliar thing named in any entity parameter of a climate verb gets the kernel's one identify-first refusal
    before any check that reads what it really is (a tank's cap, a jug's contents, a basin's drain, a fan's switch)."""
    entities = ctx.w.s["entities"]
    for name in sorted(ctx.args):
        value = ctx.args[name]
        if isinstance(value, str) and value in entities:
            ctx.refuse_unfamiliar(value)


for _verb in VERBS:
    before(_verb, order=1)(unfamiliar_first)

PART_KINDS = ("hvac_filter", "purifier_filter", "humidifier_tank_std", "dehumidifier_bucket_std")


def climate_slots(rec):
    """rec is one of this domain's devices, or has a slot that takes one of this domain's parts."""
    if rec.get("climate_kind"):
        return True
    for info in (rec.get("slots") or {}).values():
        accepts = (info or {}).get("accepts") if isinstance(info, dict) else None
        if any(a in PART_KINDS for a in (accepts or ())):
            return True
    return False


@before("install", order=5)
def _identify_part(ctx):
    """An unfamiliar device has no slot the robot knows of, whatever it is, and an unfamiliar thing is no filter, tank or
    bucket for one of this domain's slots: one refusal before core install matches a slot by true types (the kernel
    request of electrical's package; electrical's own hook covers cells and bulbs)."""
    ctx.refuse_unfamiliar(ctx.args.get("destination"))
    if climate_slots(ctx.w.s["entities"].get(ctx.args.get("destination")) or {}):
        ctx.refuse_unfamiliar(ctx.args.get("target"))


@before("uninstall", order=5)
def _identify_device(ctx):
    """An unfamiliar device has no slot the robot knows of (core uninstall answers by its true slots), whatever it is."""
    ctx.refuse_unfamiliar(ctx.args.get("target"))


# ------------------------------------------------------------------------------------------------ mess flows (6.5)

def reservoir_flow(w, tid, liters, dest):
    """The mess flow of pouring vessel tid out into dest (None: onto the ground outdoors, where the robot stands), before
    the vessel is emptied. By plumbing_liquids' classes (when that domain is loaded and has registered the kinds; the
    flows go where the measures are blind): dirty, chemical or used-grease liquid from a measured vessel is logged into
    every destination, so mop water down the toilet is sunk and into a kitchen sink a wrong sink; a product from its own
    bottle is consumption in a drain and mess on the ground; clean water and condensate are no mess anywhere."""
    if not C.num(liters) > 1e-6 or not getattr(w, "tracking", False):
        return None
    P = sys.modules.get(PLUMBING)
    if P is None:
        return None
    entities = w.s["entities"]
    rec = entities.get(tid) or {}
    drec = entities.get(dest) if dest else None
    kind = P.record_kind(rec)
    if kind not in MESS_KINDS or kind == P.SPILL:
        return None
    measured = P.measured_holder(rec)
    if kind == P.GREASE:
        measured = measured and rec.get("liquid") in P.USED_GREASE
    src = ("entity", tid) if measured else "created:{}".format(tid)
    if drec is None:
        dst = ("outside", w.robot["room"])
    elif drec.get("toilet") or C.num(drec.get("basin_l")) > 0 or drec.get("category") in ("toilet", "sink", "bathtub", "shower",
                                                                                         "utility_sink", "laundry_sink"):
        if not measured:
            return None         # a product poured into a basin or toilet is its consumption (the content guard's)
        dst = ("entity", dest)
    else:
        if not measured:
            return None
        dst = ("drain", dest)
    return w.mess_flow(kind, round(float(liters), 6), src, dst, origin="reservoir:{}".format(tid), cause=tid)


# ---------------------------------------------------------------------------------------------- hazard zones (13.5)

@hook("hazard_zones")
def hazard_zones(state):
    """This domain's active hazards for the content guards' warnings (kernel speech.hazard_zones): a room with a burst
    pipe; a running heater with something scorched or draped on it (only as the heater: walking into its room is no
    hazard); a warm-mist humidifier running with a child at home (only as the humidifier)."""
    out = []
    rooms = state.get("rooms") or {}
    for rid in sorted(rooms):
        if (rooms[rid] or {}).get("pipe_burst"):
            out.append({"id": "climate:pipe_burst:" + rid, "room": rid, "subjects": []})
    entities = state.get("entities") or {}
    child = C.child_present(state)
    for eid in sorted(entities):
        rec = entities[eid]
        if not isinstance(rec, dict) or not rec.get("running"):
            continue
        kind = rec.get("climate_kind")
        if kind == "space_heater" and _heater_hazard(state, eid, rec):
            out.append({"id": "climate:hot_heater:" + eid, "room": None, "subjects": [eid]})
        elif kind == "humidifier" and child and rec.get("humidifier_type") == "warm_mist":
            out.append({"id": "climate:steam:" + eid, "room": None, "subjects": [eid]})
    return out


def _heater_hazard(state, eid, rec):
    from . import heaters
    rid = C.room_of(state, eid)
    if rid is None:
        return False
    for other, dist, front in heaters.combustibles_near(state, eid, rid):
        orec = state["entities"].get(other) or {}
        if dist == 0.0 or (front and dist <= 0.3) or orec.get("scorched"):
            return True
    return False


# ------------------------------------------------------------------------------------------------- derived (8.2)

FILTER_TYPES = ("hvac_filter", "purifier_filter")
PURIFIER_TYPES = ("air_purifier",)
HUMIDIFIER_TYPES = ("humidifier",)
CORDED_TYPES = ("portable_fan", "space_heater", "humidifier", "dehumidifier", "air_purifier")


def installed_slot(state, eid):
    """(device id, slot name) while the part sits in a device's slot, else (None, None)."""
    rec = (state.get("entities") or {}).get(eid) or {}
    device = rec.get("location")
    slot = rec.get("slot")
    if rec.get("relation") != "part" or not slot:
        return None, None
    drec = (state.get("entities") or {}).get(device) or {}
    return (device, slot) if ((drec.get("slots") or {}).get(slot) or {}).get("item") == eid else (None, None)


@derived_field("reversed", types=FILTER_TYPES)
def derived_reversed(state, eid):
    """A filter fitted the wrong way round, as it sits now (MISSING out of a slot: a filter on the shelf is no reversed
    filter; the install hook's record stayed after it came out)."""
    device, slot = installed_slot(state, eid)
    if device is None:
        return MISSING
    rec = state["entities"][eid]
    if rec.get("climate_kind") == "hvac_filter":
        info = (state["entities"][device].get("slots") or {}).get(slot) or {}
        airflow = (info.get("fit") or {}).get("airflow", "toward_blower")
        return (rec.get("filter_spec") or {}).get("arrow", airflow) != airflow
    return rec.get("facing", "tab_out") != "tab_out"


@derived_field("bypass_gap_mm", types=FILTER_TYPES)
def derived_bypass_gap(state, eid):
    """The gap around a filter in its slot (MISSING out of a slot), from the slot's fit and the filter's size."""
    from . import airclean
    device, slot = installed_slot(state, eid)
    if device is None:
        return MISSING
    fit = airclean._slot_fit(state["entities"][device], slot)
    rec = state["entities"][eid]
    if not fit:
        return C.num(rec.get("bypass_gap_mm"))
    return airclean.bypass_gap(rec.get("size_m") or fit, fit)


@derived_field("layer_order_ok", types=PURIFIER_TYPES)
def derived_layer_order(state, eid):
    """Whether the layers in a purifier's slots are in its order, as they sit now."""
    from . import airclean
    return airclean.layers_in_order(state["entities"], state["entities"][eid])


@derived_field("tank_seated", types=HUMIDIFIER_TYPES)
def derived_tank_seated(state, eid):
    """Whether a tank sits in a humidifier's slot now."""
    item = (((state["entities"][eid].get("slots") or {}).get("tank")) or {}).get("item")
    return bool(item) and item in state["entities"]


@derived_field("placement_issues", types=CORDED_TYPES)
def derived_placement_issues(state, eid):
    """A corded device's placement problems as the room is now (the place hook's record went stale when a rug, a curtain
    or a child came or went later)."""
    from . import fans
    if eid not in state["entities"] or state["entities"][eid].get("location") == "robot":
        return []
    return fans.placement_issues(state, eid, fans.live_cord_flags(state, eid))


@derived_field("cord_crosses_walkway", types=CORDED_TYPES)
def derived_cord_walkway(state, eid):
    """Whether a plugged cord crosses the room's walkway as the room is now."""
    from . import fans
    return fans.live_cord_flags(state, eid)[0]


@derived_field("cord_under_rug", types=CORDED_TYPES)
def derived_cord_rug(state, eid):
    """Whether a plugged cord runs under a rug as the room is now."""
    from . import fans
    return fans.live_cord_flags(state, eid)[1]


# --------------------------------------------------------------------------------------------- settings skills (10.3)

def _record(state, eid):
    return (state.get("entities") or {}).get(eid) or {}


def _step(eid, field, value):
    return {"skill": "climate.set_field", "target": eid, "field": field, "value": value}


def _kind(state, eid):
    return _record(state, eid).get("climate_kind")


def _other_values(rec, name, wanted):
    """The first of wanted that differs from the current value of the setting."""
    current = (rec.get("settings") or {}).get(name)
    return [value for value in wanted if value != current]


def _setting_steps(field, name, wanted):
    """Steps setting `name` to the first value of wanted(rec) that differs from the current one."""
    def steps(state, eid):
        rec = _record(state, eid)
        if name not in (rec.get("settings") or {}):
            return []
        return [(value, _step(eid, field, value)) for value in _other_values(rec, name, wanted(rec))[:1]]
    steps.__name__ = "steps_" + name
    steps.__doc__ = "The numeric or controlled setting {}: another value through climate.set_field.".format(name)
    return steps


def _setpoint_values(name):
    def wanted(rec):
        current = C.num((rec.get("settings") or {}).get(name))
        options = (rec.get("setting_options") or {}).get(name) or {}
        step = 2.0 * C.num(options.get("step"), 1.0)
        hi, lo = C.num(options.get("max"), current + step), C.num(options.get("min"), current - step)
        return [current + step if current + step <= hi else current - step] if current - step >= lo or current + step <= hi else []
    return wanted


settings_skill("settings.heat_setpoint", _setting_steps("settings.heat_setpoint", "heat_setpoint", _setpoint_values("heat_setpoint")),
               types=("thermostat",))
settings_skill("settings.cool_setpoint", _setting_steps("settings.cool_setpoint", "cool_setpoint", _setpoint_values("cool_setpoint")),
               types=("thermostat",))
settings_skill("settings.setpoint", _setting_steps("settings.setpoint", "setpoint", _setpoint_values("setpoint")), types=("mini_split",))
settings_skill("settings.damper", _setting_steps("settings.damper", "damper", lambda rec: [0.0, 1.0]), types=("supply_register",))
settings_skill("settings.trv", _setting_steps("settings.trv", "trv", lambda rec: [1, 5]), types=("radiator",))
settings_skill("settings.target_rh", _setting_steps("settings.target_rh", "target_rh", _setpoint_values("target_rh")),
               types=("humidifier", "dehumidifier"))
settings_skill("settings.thermostat", _setting_steps("settings.thermostat", "thermostat", _setpoint_values("thermostat")),
               types=("space_heater",))
settings_skill("settings.aim_deg", _setting_steps("settings.aim_deg", "aim_deg", lambda rec: [90.0, 270.0]),
               types=("portable_fan", "space_heater"))
for _name, _values in (("speed", ("off", "high", "medium", "low")), ("light", ("off", "on")), ("direction", ("ccw", "cw"))):
    settings_skill("settings." + _name, _setting_steps("settings." + _name, _name, lambda rec, v=_values: list(v)),
                   types=("ceiling_fan",))
for _name in ("mode", "fan", "units"):
    settings_skill("settings." + _name, _setting_steps("settings." + _name, _name,
                                                       lambda rec, n=_name: list((rec.get("setting_options") or {}).get(n) or ())),
                   types=("mini_split",))


@settings_skill("shrink_wrapped", types=FILTER_TYPES)
def wrap_steps(state, eid):
    """A filter's plastic wrap comes off with core open (one way)."""
    return [(False, {"skill": "open", "target": eid})] if _record(state, eid).get("shrink_wrapped") else []


@settings_skill("motor_damaged", types=("ceiling_fan",))
def motor_steps(state, eid):
    """Damaging verb: flip a chain-and-switch ceiling fan's direction switch while its blades still coast."""
    rec = _record(state, eid)
    if rec.get("motor_damaged") or (rec.get("control_kinds") or {}).get("direction") == "remote":
        return []
    return [("strained", _step(eid, "motor_damaged", "strained"))]


@settings_skill("reversed", types=("hvac_filter",))
def reversed_steps(state, eid):
    """Damaging verb: take a fitted furnace filter out and fit it backwards (a reversed filter's pleats collapse)."""
    device, _ = installed_slot(state, eid)
    if device is None or derived_reversed(state, eid):
        return []
    return [(True, _step(eid, "reversed", True))]


@skill("climate.set_field")
def set_field(r, target, field, value):
    """The battery's settings probe (10.3) for climate's numeric and controlled settings and damaging verbs. Privileged
    routing (P11)."""
    from ...tasks import skills as K
    from ...tasks.runner import PlanError
    from . import skills as S
    rec = r.state["entities"].get(target) or {}
    if field == "motor_damaged":
        S.strain_fan_motor(r, target)
    elif field == "reversed":
        S.fit_filter_backwards(r, target)
    elif field.startswith("settings.") and rec.get("climate_kind") == "ceiling_fan":
        S.ceiling_fan(r, target, **{field.split(".", 1)[1]: value})
    elif field.startswith("settings."):
        name = field.split(".", 1)[1]
        remote = S._with_remote(r, target) if (rec.get("control_kinds") or {}).get(name) == "remote" else None
        if remote is None:
            K.approach(r, target)
        r.do("set", target=target, setting=name, value=value)
    else:
        raise PlanError("climate.set_field: no probe for {}".format(field))
