"""What the house ledger and the battery need from the safety domain (VERIFY_HARDENING 6.2-6.5, 8.2, 10.3; WP-D safety).

Mess kind (6.5; kernel/plugins.py @mess_kind, measured on any state):
  biohazard   g   bodily-fluid mess: every unresolved biohazard spill (vomit, urine, feces, blood, rodent droppings) at
                  its place (a room's floor, outdoors, or the thing it lies on), its liquid and solids in grams; and
                  what carriers hold of it (biohazard_waste_kg: the sealable bag a scoop fills, the paper towels a wipe
                  uses, the vacuum a vacuum step fills). Accepting sinks: a waste container (a sealed bag is one,
                  location_classes.json) or the hazardous-waste stream; eps 1 g, rho 0 (pathogens: no slack, as
                  pet_waste). Flows: clean_biohazard's scoop (into the bag), wipe (into the towels) and vacuum (into
                  the vacuum), logged with world.mess_flow; a bag or towel put in a bin moves its parcel by its
                  container chain (no flow needed). Absorbing keeps the mass (the liquid is held in the clumps).
  Chemical leaks are logged as liquid:chemical flows (chemicals.leaks, origin chemical:<container>); the kind's measure
  belongs to its owner (handoff requests). Glass shards are hazard_created things (location_classes.json), not mess.

Derived field (8.2; overlaid by schema-4 evaluation):
  resolved    a biohazard spill is cleaned up while its liquid (<= 0.02 l), solids (<= 5 g), pathogen (<= 0.05) and
              odor marker (<= 0.1) are down and no stain is set (bio._resolve_if_done's rule, recomputed from the
              record); a puddle while it holds less than 0.02 l (water._resolve's rule)

Settings skills (10.3, B14 probes, inverses): an extinguisher's pin_in (pull it: one way), a trap's armed (re-arm by
check_trap reset), a gas shutoff's gas_valve_open (shut it: one way, the utility restores), a chemical cabinet's locked,
a safety gate's latched, a sealable bag's sealed. The step is the safety.set_field skill (or the core use skill).

Unfamiliar things and unresolved look-alikes (realistic perception; closeout_REV finding 6, the twin sweep of every
safety verb): an order-1 before hook on every safety verb gives a thing named in an entity parameter the kernel's
identify-first refusal (ctx.refuse_unfamiliar for an unfamiliar thing, ctx.refuse_unidentified for an unresolved
look-alike) before any guard reads what it really is, except that a verb built to act on an unread look-alike alike
for every member keeps doing so (IDENTIFY_EXEMPT: clean_biohazard's unread product and smother's unread cover or
powder, used as they are with their true physics and one message; an unfamiliar thing is refused there too). No-op in
oracle mode.

Gas meter (6.8): the gas a leak lets out of the supply is metered by air.apply_sources in safety.meter (gas_m3,
gas_kwh at 10.55 kWh per m3), cumulative; the robot-attributable share is what diverges from the idle house
(resources.json reads climate's appliance gas; adding the leak meter is a request to the ledger's owner).
"""
from ...kernel.plugins import ACTIONS, before, derived_field, mess_kind, settings_skill, skill
from ...kernel.predicates import MISSING
from . import common as C

BIO_TYPES = ("biohazard_spill", "vomit_spill", "urine_puddle", "feces_accident", "blood_spill", "rodent_droppings")
PUDDLE_TYPES = ("floor_puddle",)
EXTINGUISHER_TYPES = ("fire_extinguisher", "fire_extinguisher_compact", "co2_extinguisher", "water_extinguisher",
                      "kitchen_k_extinguisher")
TRAP_TYPES = ("pest_trap", "snap_trap", "glue_board", "live_catch_trap", "ant_bait_station", "pantry_moth_trap",
              "fruit_fly_trap")
SHUTOFF_TYPES = ("gas_meter", "appliance_gas_valve")
CABINET_TYPES = ("chemical_cabinet", "flammables_cabinet")
GATE_TYPES = ("safety_gate", "hardware_safety_gate")
BAG_TYPES = ("waste_bag_sealable",)
BIO_DONE = {"liquid_l": 0.02, "solids_kg": 0.005, "pathogen": 0.05, "odor_marker": 0.1}
PUDDLE_DRY_L = 0.02


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _num(value):
    return float(value) if _number(value) else 0.0


def is_kind(record, kinds):
    return record.get("type") in kinds or any(t in kinds for t in record.get("is_a") or ())


# ----------------------------------------------------------------------
# mess kind (6.5)

def place_key(state, rec):
    """The mess location key of where a thing lies: a room's floor (outdoors: outside), the robot's hands, or the
    entity it is on or in."""
    loc = rec.get("location")
    rooms = state.get("rooms") or {}
    if loc in rooms:
        return ("outside" if (rooms[loc] or {}).get("outdoor") else "floor", loc)
    if loc == "robot":
        return ("entity", "robot")
    if isinstance(loc, str) and loc:
        return ("entity", loc)
    return None


def bio_grams(rec):
    """Grams of bodily-fluid mess a spill record still holds (1 l of liquid ~ 1 kg)."""
    return 1000.0 * (max(0.0, _num(rec.get("liquid_l"))) + max(0.0, _num(rec.get("solids_kg"))))


@mess_kind("biohazard", unit="g", sinks=["waste", "hazardous_waste"], eps=1.0, rho=0.0)
def biohazard_parcels(state):
    """(location key, origin, grams) of every biohazard parcel in a state (origin None: one group for the kind, so a
    bag's or towel's load joins the spills it came from)."""
    out = []
    entities = state.get("entities") or {}
    for eid in sorted(entities):
        rec = entities[eid]
        if not isinstance(rec, dict):
            continue
        if is_kind(rec, BIO_TYPES) or rec.get("category") == "biohazard":
            if not rec.get("resolved"):
                grams = bio_grams(rec)
                key = place_key(state, rec)
                if grams > 0 and key is not None:
                    out.append((key, None, grams))
        load = rec.get("biohazard_waste_kg")
        if _number(load) and load > 0:
            out.append((("entity", eid), None, 1000.0 * float(load)))
    return out


def log_flow(w, spill, grams, dst_entity):
    """A clean-up step moved `grams` of a spill's mess into a carrier (world.mess_flow; nothing without it)."""
    flow = getattr(w, "mess_flow", None)
    rec = w.s["entities"].get(spill) or {}
    src = place_key(w.s, rec)
    if not callable(flow) or src is None or grams <= 0 or not isinstance(dst_entity, str):
        return
    dst = ("entity", dst_entity)
    if dst != src:
        flow("biohazard", round(float(grams), 3), list(src), list(dst))


def carry(w, spill, carrier, kg):
    """kg of a spill's mess now held by carrier (its biohazard_waste_kg) and the flow there."""
    if not isinstance(carrier, str) or carrier not in w.s["entities"] or kg <= 0:
        return
    m = w.mut(carrier)
    m["biohazard_waste_kg"] = round(_num(m.get("biohazard_waste_kg")) + kg, 4)
    log_flow(w, spill, 1000.0 * kg, carrier)


# ----------------------------------------------------------------------
# derived field (8.2)

@derived_field("resolved", types=BIO_TYPES + PUDDLE_TYPES)
def derived_resolved(state, eid):
    """resolved from the spill's or puddle's own record now (module docstring)."""
    rec = (state.get("entities") or {}).get(eid)
    if not isinstance(rec, dict):
        return MISSING
    if is_kind(rec, PUDDLE_TYPES):
        return _num(rec.get("liquid_l")) < PUDDLE_DRY_L
    if not is_kind(rec, BIO_TYPES):
        return MISSING
    return all(_num(rec.get(field)) <= limit for field, limit in sorted(BIO_DONE.items())) and not rec.get("set_stain")


# ----------------------------------------------------------------------
# settings skills (10.3)

def _record(state, eid):
    return (state.get("entities") or {}).get(eid) or {}


def _field_step(eid, field, value):
    return {"skill": "safety.set_field", "target": eid, "field": field, "value": value}


@settings_skill("pin_in", types=EXTINGUISHER_TYPES)
def pin_steps(state, eid):
    """Pull the pin (it cannot be put back: tamper seal)."""
    return [(False, _field_step(eid, "pin_in", False))] if _record(state, eid).get("pin_in", True) else []


@settings_skill("armed", types=TRAP_TYPES)
def trap_steps(state, eid):
    """Re-arm a sprung or unset trap (check_trap reset); there is no disarming verb."""
    return [] if _record(state, eid).get("armed") else [(True, _field_step(eid, "armed", True))]


@settings_skill("gas_valve_open", types=SHUTOFF_TYPES)
def shutoff_steps(state, eid):
    """Close the shutoff (only the utility opens a meter again)."""
    return [(False, _field_step(eid, "gas_valve_open", False))] if _record(state, eid).get("gas_valve_open", True) \
        else []


def _toggle(state, eid, field, on_action, off_action, requires=None):
    record = _record(state, eid)
    if requires and not record.get(requires):
        return []
    value = not bool(record.get(field))
    return [(value, {"skill": "use", "action": on_action if value else off_action, "target": eid})]


@settings_skill("locked", types=CABINET_TYPES)
def cabinet_lock_steps(state, eid):
    return _toggle(state, eid, "locked", "lock", "unlock", requires="lockable")


@settings_skill("latched", types=GATE_TYPES)
def gate_latch_steps(state, eid):
    return _toggle(state, eid, "latched", "latch", "unlatch")


@settings_skill("sealed", types=BAG_TYPES)
def bag_seal_steps(state, eid):
    return _toggle(state, eid, "sealed", "seal", "unseal", requires="sealable")


@skill("safety.set_field")
def set_field(r, target, field, value):
    """The battery's settings probe (10.3) for safety's one-way and tool-bound settings: pull an extinguisher's pin,
    re-arm a trap, close a gas shutoff (fetching a wrench when the valve needs one). Privileged routing."""
    from ...tasks import skills as S
    rec = r.state["entities"].get(target) or {}
    if field == "pin_in" and value is False:
        if target not in [v for v in r.state["robot"]["hands"].values() if v]:
            S.fetch(r, target)
        r.do("pull_pin", target=target)
    elif field == "armed" and value is True:
        S.approach(r, target)
        r.do("check_trap", target=target, reset=True)
    elif field == "gas_valve_open" and value is False:
        if rec.get("valve_tool") == "wrench":
            held = [v for v in r.state["robot"]["hands"].values() if v]
            if not any("wrench" in str((r.state["entities"].get(h) or {}).get("tool_kind") or
                                       (r.state["entities"].get(h) or {}).get("type") or "") for h in held):
                wrench = next((eid for eid, e in sorted(r.state["entities"].items())
                               if "wrench" in str(e.get("tool_kind") or e.get("type") or "")), None)
                if wrench is not None:
                    S.fetch(r, wrench)
        S.approach(r, target)
        r.do("shut_off_gas", target=target)
    else:
        from ...tasks.runner import PlanError
        raise PlanError("safety.set_field: no step sets {}.{} to {!r}".format(target, field, value))


# ----------------------------------------------------------------------
# unfamiliar things and unresolved look-alikes: one identify-first refusal on every safety verb

# (verb, parameter) pairs built to act on an unidentified thing alike for every member, with its true physics and one
# message (their own handling is tested): an unread product applied to a spill (bio._held_product), an unread powder
# thrown on a fire (a flour jar flares the fire it was meant to smother: the harm of acting unidentified is the
# world's answer, never a refusal that would spare a careless agent it)
IDENTIFY_EXEMPT = {("clean_biohazard", "product"), ("clean_biohazard", "tool"), ("smother", "tool")}


def identify_first(ctx):
    """Before hook (order 1) of every safety verb (module docstring). The refusal names nothing about the thing, so
    every member of a look-alike group and every unfamiliar twin gets the same words."""
    if not C.realistic_ctx(ctx):
        return
    name = ctx.spec.name
    for pname, param in sorted(ctx.spec.params.items()):
        if param.get("type") not in ("entity", "target"):
            continue
        eid = ctx.args.get(pname)
        if not (isinstance(eid, str) and eid in ctx.w.s["entities"]):
            continue
        # an unfamiliar thing serves as no tool, cover, product or bin the robot knows of (review I4): always refused;
        # an unresolved look-alike member unless the verb uses an unread one alike for every member
        ctx.refuse_unfamiliar(eid)
        if (name, pname) not in IDENTIFY_EXEMPT:
            ctx.refuse_unidentified(eid)


_INSTALLED = []


def install_identify_first():
    """Register identify_first on every safety verb (the package's __init__ calls it once every module is imported,
    so no verb registered after this module escapes it)."""
    for name in sorted(n for n, spec in ACTIONS.items() if spec.fn.__module__.startswith(__package__ + ".")):
        if name not in _INSTALLED:
            before(name, order=1)(identify_first)
            _INSTALLED.append(name)
