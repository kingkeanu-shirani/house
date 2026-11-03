"""What the house ledger needs from the plumbing_liquids domain (VERIFY_HARDENING 6.2-6.11, 8.2, 10.3, 23.3; package
WP-D plumbing_liquids).

Liquid mess (6.5; kernel/plugins.py @mess_kind, measured on any state, unit litres, origin None: one group per kind,
so the flows other domains log -- cleaning's dump_liquid, navigation's slops and footprints, safety's chemical leaks
-- join the same balance whatever origin ids they carry). Every liquid parcel belongs to exactly one kind, by the
class of what it is (liquid_class):

  liquid:chemical    a chemical product itself (bleach, drain cleaner, ammonia; the kernel's toxic or corrosive
                     liquids: motor oil, gasoline, antifreeze, lighter fluid) out of its own container: spilled,
                     decanted into a vessel that is not its product container, or leaked onto what held it (safety's
                     _chem_spill residue). Accepting sink: a hazardous-waste container (the kernel default).
  liquid:grease      cooking oil and grease spilled, and used grease in a vessel (a can of bacon fat; fresh oil in
                     a pan or a bottle is food)
  liquid:mop_water   used cleaning water anywhere, inside vessels included: dirty, muddy and mop water, water with
                     more than 2 g/l of dirt (cleaning's turbidity rule). A fresh cleaning solution (water and a cleaner
                     at its use dilution, nothing dirty in it yet: a bucket made up for mopping) is content until it
                     is used; spilled it is mop water. Sinks: toilet, utility sink, floor drain. Zero relative slack
                     (rho 0): it carries chemicals.
  liquid:dirty_water sewage, toilet water, urine and vomit anywhere (an overflowing toilet, a sewer backup, a kernel
                     puddle of urine; the biohazard spills safety's own kind counts are not spills). Sinks as mop
                     water; rho 0 (biohazard).
  liquid:spill       any other liquid outside a vessel: water, drinks and food liquids, soapy water, condensate,
                     hose and rain water on floors, the ground and surfaces (spill and puddle entities: this domain's,
                     cleaning's spill type, the kernel's puddles, safety's floor puddles). Inside a vessel it is
                     content, never mess (filling a kettle, a vase or a pet's bowl makes no parcel).

Parcels are read from the state only: spill and puddle entities (and ice dropped out of a tray, as the water it melts
into) at the floor (floor:<room>, outdoors outside:<room>) or the surface they lie on (entity:<surface>), dirty /
chemical / grease liquid in a vessel at entity:<vessel> (not in
basins, toilets or hoses, whose own water is the fixture's; not in a product's own container, whose contents are
goods the content guard judges), safety's chemical residue at entity:<holder>. Flows (World.mess_flow) are logged by
this domain's transfers (pour, empty, wring, fill overflow, drain spigots) where the measures cannot see the
destination: a toilet, a basin, a floor or storm drain, a bin, a plant, soaking ground (delivery_flow). Floors,
surfaces and vessels are measured, so a liquid landing there needs no flow (and none is logged: a flow into a floor
would outlive the mop-up another domain does without logging). The time systems log no flows: what an exogenous
leak or a scenario's running tap puts on a floor is the idle house's too, and what the robot's own doing puts there
is an end-state increase the counterfactual rule charges (6.2).

Causes (6.2): the time systems' writes cite the thing whose water it is (common.caused: a leak its host, an overflow
its basin, a drip what drips), so the ledger charges a physical consequence only when the robot wrote that thing.

Derived fields (8.2): coiled (a garden hose is coiled while it rests on a hose hanger or reel, or still lies where it
was coiled), drained (a standard spigot is drained while its interior valve stays closed). Both were latched by
action hooks (outdoor.hose_stored on place, outdoor.spigot_drained on the valve verbs).

Settings skills (10.3, B14 probes): a faucet's handles (settings.flow / left / right / valve), a valve's
settings.position, a toilet's settings.supply_valve, a basin's plug, a hose nozzle's settings.trigger.
"""
from ...kernel.phys import props as PP
from ...kernel.plugins import MESS_KINDS, before, derived_field, mess_kind, settings_skill
from . import common as C

SPILL = "liquid:spill"
MOP = "liquid:mop_water"
DIRTY = "liquid:dirty_water"
CHEMICAL = "liquid:chemical"
GREASE = "liquid:grease"
KINDS = (SPILL, MOP, DIRTY, CHEMICAL, GREASE)
KIND_OF_CLASS = {"chemical": CHEMICAL, "grease": GREASE, "mop_water": MOP, "dirty_water": DIRTY, "food": SPILL,
                 "water": SPILL}
STRICTNESS = (CHEMICAL, DIRTY, GREASE, MOP, SPILL)     # kernel mess_classes.json order of the kinds' classes

CHEMICAL_PRODUCTS = ("bleach", "chlorine_bleach", "drain_cleaner", "ammonia", "muriatic_acid", "lye", "oven_cleaner",
                     "toilet_bowl_cleaner", "paint_thinner", "mineral_spirits", "turpentine", "acetone", "pesticide",
                     "herbicide", "pool_chlorine")
MOP_NAMES = ("mop_water", "dirty_water", "garbage_juice", "mud_water")
SEWAGE_NAMES = ("sewage", "toilet_water")
GREASE_NAMES = ("cooking_oil", "grease", "oil", "vegetable_oil", "canola_oil", "olive_oil", "bacon_grease", "frying_oil")
# grease in a vessel is mess only once used (fresh oil in a pan or a bottle is food); spilled, any oil is grease
USED_GREASE = ("grease", "bacon_grease", "frying_oil", "used_oil", "used_cooking_oil")
TURBID_G_PER_L = 2.0            # cleaning's dirty rule (state.turbidity > 2 g/l)
DIRTY_SHARE = 0.05              # a mix with this share of dirty liquid is used water (liquids.derive_name's own rule)
SPILL_CATEGORIES = ("spill", "puddle")
PRODUCT_CATEGORIES = ("cleaning_product", "cleaner", "chemical", "chemicals", "detergent", "solvent", "fuel",
                      "fuel_can", "automotive_fluid", "paint", "pesticide", "pool_chemical", "product")


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# ----------------------------------------------------------------------
# liquid classes and kinds

def _kernel_row(name):
    row = PP.liquid_props(name or "water")
    return row if row.get("name") != "default" else {}


def liquid_class(name, chem=None, liters=None, soil_g=None, mix=None):
    """The mess class of a liquid (6.5 strictness classes): chemical, grease, dirty_water (sewage, toilet water,
    urine, vomit), mop_water, food or water. liters / soil_g / mix: how much of it there is, the grams of dirt in it and
    its composition {liquid: liters} when known (a vessel's record, a parcel); without a composition a liquid named mop
    water is used water. chem (dissolved products) is accepted for the callers' convenience: a cleaner at its use
    dilution does not make water mess (a fresh mop bucket), dirt does."""
    name = str(name or "water")
    info = C.info(name) if name in C.LIQUIDS else {}
    row = _kernel_row(name)
    if name in CHEMICAL_PRODUCTS or (info.get("chemical") and info.get("product")) or info.get("caustic") or \
            row.get("toxic") or row.get("corrosive"):
        return "chemical"
    perishable = bool(row.get("perishable") or info.get("perishable"))
    if name in GREASE_NAMES or info.get("grease") or (row.get("residue") in ("grease", "stain_oil") and not perishable):
        return "grease"             # (broth leaves a greasy ring but is food: perishable)
    if name in SEWAGE_NAMES or info.get("biohazard") or row.get("residue") in ("urine", "biohazard"):
        return "dirty_water"
    volume = C.num(liters)
    if name in MOP_NAMES and name != "mop_water":
        return "mop_water"
    if volume > 1e-6 and C.num(soil_g) / volume > TURBID_G_PER_L:
        return "mop_water"
    if name == "mop_water":
        # this domain names any water with a cleaner in it "mop_water" (liquids.derive_name): used water when its
        # composition holds dirty liquid, a fresh cleaning solution (content) when it is clean water and a cleaner
        if not isinstance(mix, dict) or not mix:
            return "mop_water"
        total = sum(C.num(v) for v in mix.values()) or volume
        dirty = sum(C.num(v) for k, v in mix.items() if k in MOP_NAMES or k in SEWAGE_NAMES)
        return "mop_water" if total > 0 and dirty / total >= DIRTY_SHARE else "water"
    if perishable or (info.get("drink") and name not in C.WATERLIKE) or row.get("residue") in (
            "protein", "sticky", "food", "stain_coffee", "stain_wine"):
        return "food"
    return "water"


def liquid_kind(name, chem=None, liters=None, soil_g=None, mix=None):
    """The registered mess kind of a liquid (liquid_class's kind)."""
    return KIND_OF_CLASS[liquid_class(name, chem, liters, soil_g, mix)]


def stricter(a, b):
    """The stricter of two kinds (None loses)."""
    if a not in STRICTNESS:
        return b
    if b not in STRICTNESS:
        return a
    return a if STRICTNESS.index(a) <= STRICTNESS.index(b) else b


def record_kind(rec):
    """The mess kind of what a vessel holds (its liquid name, composition and dirt)."""
    from .liquids import mix_of
    return liquid_kind(rec.get("liquid"), rec.get("cl_chem"), rec.get("liquid_l"), rec.get("cl_soil_g"), mix_of(rec))


def parcel_kind(parcel):
    """The mess kind of a parcel of this domain's transfers (liquids.take / make_parcel), by the rule of record_kind:
    its composition's name, its composition and its dirt (a parcel is a share of its source, so both agree)."""
    from .liquids import parcel_name
    chem = parcel.get("chem") or parcel.get("named_chem") or {}
    return liquid_kind(parcel_name(parcel), chem, C.num(parcel.get("liters")), parcel.get("soil_g"),
                       parcel.get("mix") or None)


def is_spill_like(rec):
    """A spill or puddle entity: liquid outside any vessel."""
    return rec.get("category") in SPILL_CATEGORIES or bool(rec.get("_phys_spill"))


def product_container(rec):
    """A product's own container (a bleach jug, a soap dispenser, an oil bottle, a food carton): what it holds is
    goods, judged by the content guard (envelope.consume), not mess."""
    if rec.get("product") or rec.get("product_tag") or "amount_ml" in rec or rec.get("sealed") is True:
        return True
    if isinstance(rec.get("food"), dict):
        return True
    return rec.get("category") in PRODUCT_CATEGORIES


def measured_holder(rec):
    """A vessel whose dirty, chemical or grease liquid is a parcel at entity:<vessel>: not a basin, toilet, hose or
    spill, not a product's own container, not a person's or pet's body."""
    if not isinstance(rec, dict) or is_spill_like(rec):
        return False
    if C.num(rec.get("basin_l")) > 0 or rec.get("toilet") or rec.get("hose") or rec.get("water_source"):
        return False
    return not product_container(rec)


def _loc(state, location, relation=None):
    if location is None:
        return None
    rooms = state.get("rooms") or {}
    if location in rooms:
        return ("outside" if (rooms[location] or {}).get("outdoor") else "floor", location)
    if relation == "drain":
        return ("drain", location)
    return ("entity", location)


def parcels(state):
    """{kind: [(location key, None, litres)]} of every liquid parcel in a state."""
    out = dict((k, []) for k in KINDS)
    entities = state.get("entities") or {}
    for eid in sorted(entities):
        rec = entities[eid]
        if not isinstance(rec, dict):
            continue
        liters = rec.get("liquid_l")
        if is_spill_like(rec):
            if _number(liters) and liters > 0:
                kind = stricter(liquid_kind(rec.get("liquid")), rec.get(C.MESS_KIND))
                loc = _loc(state, rec.get("location"), rec.get("relation"))
                if loc is not None:
                    out[kind].append((loc, None, float(liters)))
        elif rec.get("category") == "ice_cubes" and _number(rec.get("ice_g")) and rec["ice_g"] > 0:
            # ice dropped out of a tray (ice.drop_cubes): water outside a vessel that has not melted yet
            loc = _loc(state, rec.get("location"), rec.get("relation"))
            if loc is not None:
                out[SPILL].append((loc, None, float(rec["ice_g"]) / 1000.0))
        elif _number(liters) and liters > 0 and measured_holder(rec):
            kind = record_kind(rec)
            if kind == GREASE and rec.get("liquid") not in USED_GREASE:
                kind = SPILL
            if kind != SPILL:
                out[kind].append((("entity", eid), None, float(liters)))
        residue = rec.get("_chem_spill")
        if isinstance(residue, dict) and residue:
            ml = max([C.num(v) for v in residue.values()] or [0.0])
            if ml > 0:
                out[CHEMICAL].append((("entity", eid), None, ml / 1000.0))
    return out


def _measure(kind):
    def measure(state):
        return parcels(state)[kind]
    measure.__name__ = "measure_" + kind.split(":", 1)[1]
    measure.__doc__ = "(location key, None, litres) of every {} parcel in a state (hardening.parcels).".format(kind)
    return measure


mess_kind(SPILL, "l", _measure(SPILL))
mess_kind(MOP, "l", _measure(MOP), rho=0.0)
mess_kind(DIRTY, "l", _measure(DIRTY), rho=0.0)
mess_kind(CHEMICAL, "l", _measure(CHEMICAL))
mess_kind(GREASE, "l", _measure(GREASE))


# ----------------------------------------------------------------------
# flows (World.mess_flow, WP2)

def log_flow(w, kind, liters, src, dst):
    """Log a liquid mess flow of a registered kind (no-op for an untracked World, an unregistered kind, a zero amount
    or the same place). src / dst: location keys or "created:<id>". Returns the log entry or None."""
    flow = getattr(w, "mess_flow", None)
    if not callable(flow) or kind not in MESS_KINDS or src is None or dst is None:
        return None
    if src == dst or not C.num(liters) > 1e-6:
        return None
    return flow(kind, round(float(liters), 6), src, dst)


SINK_DESTS = ("basin", "toilet", "floor_drain")


def destination_key(w, dest, dest_kind):
    """The location key where a parcel delivered to dest goes when the measures cannot see it there, else None (a
    vessel, a floor, a surface or hardscape: the spill or the vessel's contents are measured)."""
    if dest_kind in ("basin", "toilet", "plant", "bin"):
        return ("entity", dest)
    if dest_kind in ("floor_drain", "storm_drain"):
        return ("drain", dest)
    if dest_kind == "ground":
        rid = C.room_of(w, dest)
        if rid in w.s["rooms"] and C.floor_material(w, rid) in ("grass", "soil", "mulch"):
            return ("outside", rid)
    return None


def delivery_flow(w, dest, dest_kind, parcel, source):
    """The mess flow of a parcel this domain's pour, empty, wring, fill or drain spigot delivers to dest (transfer.
    deliver, before the destination takes it). Dirty, chemical or grease liquid from a measured vessel is logged into
    every unmeasured destination (so mop water down the toilet is sunk, into the kitchen sink or the trash a wrong
    sink, onto the lawn relocated). Liquid that was content where it came from (clean water, a drink, a product from
    its own bottle) becomes mess only where it is out of place: a bin, and -- for anything but clean liquids -- a
    plant, soaking ground or a storm drain; a product poured where it is used (drain cleaner into a drain, bleach
    into a toilet) is its consumption, the content guard's, never a mess flow."""
    if not parcel or not C.num(parcel.get("liters")) > 1e-6:
        return None
    dst = destination_key(w, dest, dest_kind)
    if dst is None:
        return None
    kind = parcel_kind(parcel)
    srec = w.s["entities"].get(source) if isinstance(source, str) else None
    measured = kind != SPILL and srec is not None and measured_holder(srec)
    if kind == GREASE:
        from .liquids import parcel_name
        measured = measured and parcel_name(parcel) in USED_GREASE        # fresh oil was food where it came from
    if measured:
        src = ("entity", source)
    else:
        if dest_kind in SINK_DESTS:
            return None
        if kind == SPILL and dest_kind != "bin":
            return None
        src = "created:{}".format(source if isinstance(source, str) and source else "plumbing")
    return log_flow(w, kind, parcel["liters"], src, dst)


# ----------------------------------------------------------------------
# derived fields (8.2)

HOSE_TYPES = ("garden_hose",)
SPIGOT_TYPES = ("outdoor_spigot",)
COILED_AT = "_coiled_at"        # hidden: where a hose was coiled (location, relation, pos) when it was coiled


def placement_of(rec):
    pos = rec.get("pos")
    return [rec.get("location"), rec.get("relation"), list(pos) if isinstance(pos, (list, tuple)) else None]


def on_hose_storage(state, rec):
    holder = (state.get("entities") or {}).get(rec.get("location") or "")
    return isinstance(holder, dict) and bool(holder.get("hose_storage"))


@derived_field("coiled", types=HOSE_TYPES)
def derived_coiled(state, eid):
    """A hose is coiled while it rests on a hose hanger or reel (outdoor.hose_stored's rule), or still lies where it
    was coiled (a hose put away coiled on a shelf or the floor: _coiled_at). Carried, dragged by the wind or moved by
    anyone, a coil left anywhere else is no longer coiled; the stored flag alone was latched by the place hook."""
    rec = (state.get("entities") or {}).get(eid) or {}
    if not rec.get("coiled"):
        return False
    if isinstance(rec["coiled"], dict):
        return True             # manipulation's coil record (its coil verb coils it where it lies, kept by its rules)
    if on_hose_storage(state, rec):
        return True
    at = rec.get(COILED_AT)
    return isinstance(at, list) and at == placement_of(rec)


@derived_field("drained", types=SPIGOT_TYPES)
def derived_drained(state, eid):
    """A standard spigot is drained while its interior valve stays closed after its stub was opened to drain
    (outdoor.spigot_drain_open); opening the interior valve by any path refills it (the stored flag was cleared only
    by the robot's valve verbs)."""
    entities = state.get("entities") or {}
    rec = entities.get(eid) or {}
    if not rec.get("drained"):
        return False
    valve = entities.get(rec.get("interior_valve") or "")
    if not isinstance(valve, dict):
        return bool(rec.get("frost_free"))
    position = (valve.get("settings") or {}).get("position")
    if not _number(position):
        return not bool(valve.get("valve_open", True))
    return float(position) <= 0.001


def mark_coiled_at(w, eid):
    """Record where a hose was coiled (hidden; the derived coiled reads it). Called wherever this domain coils one."""
    if eid in w.s["entities"]:
        w.mut(eid)[COILED_AT] = placement_of(w.ent(eid))


# ----------------------------------------------------------------------
# settings skills (10.3)

FLOW_ON = 0.6                    # turn_on_water's default opening


def _faucet_kind(rec):
    return (rec.get("faucet") or {}).get("kind") if isinstance(rec.get("faucet"), dict) else None


def _handle_steps(state, eid, field, kinds, handle=None):
    rec = (state.get("entities") or {}).get(eid) or {}
    if _faucet_kind(rec) not in kinds:
        return []
    name = field.split(".", 1)[1]
    current = C.num((rec.get("settings") or {}).get(name))
    if current > 0.001:
        step = {"skill": "use", "action": "turn_off_water", "target": eid}
        if handle:
            step["handle"] = handle
        return [(0.0, step)]
    step = {"skill": "use", "action": "turn_on_water", "target": eid, "flow": FLOW_ON}
    if handle:
        step.update(handle=handle, open=FLOW_ON)
        step.pop("flow")
    elif "single_lever" in kinds:
        step["temp"] = "cold"
    return [(FLOW_ON, step)]


FAUCET_TYPES = ("kitchen_sink", "bathroom_sink", "utility_sink", "bathtub", "shower_stall", "outdoor_spigot")


@settings_skill("settings.flow", types=FAUCET_TYPES)
def faucet_flow_steps(state, eid):
    return _handle_steps(state, eid, "settings.flow", ("single_lever", "pull_down", "thermostatic"))


@settings_skill("settings.left", types=FAUCET_TYPES)
def faucet_left_steps(state, eid):
    return _handle_steps(state, eid, "settings.left", ("two_handle",), handle="left")


@settings_skill("settings.right", types=FAUCET_TYPES)
def faucet_right_steps(state, eid):
    return _handle_steps(state, eid, "settings.right", ("two_handle",), handle="right")


@settings_skill("settings.valve", types=SPIGOT_TYPES)
def spigot_valve_steps(state, eid):
    return _handle_steps(state, eid, "settings.valve", ("spigot",))


@settings_skill("settings.position", types=("shutoff_valve", "main_shutoff_valve"))
def valve_position_steps(state, eid):
    rec = (state.get("entities") or {}).get(eid) or {}
    position = C.num((rec.get("settings") or {}).get("position"), 1.0)
    if position > 0.001:
        return [(0.0, {"skill": "use", "action": "shut_off", "target": eid})]
    return [(1.0, {"skill": "use", "action": "open_valve", "target": eid})]


@settings_skill("settings.supply_valve", types=("toilet",))
def toilet_supply_steps(state, eid):
    rec = (state.get("entities") or {}).get(eid) or {}
    if (rec.get("settings") or {}).get("supply_valve", "open") == "open":
        return [("closed", {"skill": "use", "action": "shut_off", "target": eid})]
    return [("open", {"skill": "use", "action": "open_valve", "target": eid})]


@settings_skill("plug", types=("kitchen_sink", "bathroom_sink", "utility_sink", "bathtub"))
def basin_plug_steps(state, eid):
    """A pop-up or toe-touch stopper closes in place; a basket strainer needs a held stopper (no single step)."""
    rec = (state.get("entities") or {}).get(eid) or {}
    if rec.get("plug", "open") != "open":
        return [("open", {"skill": "use", "action": "drain", "target": eid})]
    if rec.get("plug_kind") in ("popup", "toe_touch"):
        return [("closed", {"skill": "use", "action": "plug_drain", "target": eid})]
    return []


@settings_skill("settings.trigger", types=("hose_nozzle",))
def nozzle_trigger_steps(state, eid):
    rec = (state.get("entities") or {}).get(eid) or {}
    if (rec.get("settings") or {}).get("trigger") == "open":
        return [("closed", {"skill": "use", "action": "turn_off_water", "target": eid})]
    return [("open", {"skill": "use", "action": "turn_on_water", "target": eid})]


# ----------------------------------------------------------------------
# unfamiliar things: one identify-first refusal on every verb of this domain

def unfamiliar_first(ctx):
    """Before hook (order 1) of every plumbing_liquids verb: an unfamiliar (novel) thing in any entity parameter -- the
    target, the destination, the source, the tool -- gets the kernel's identify-first refusal (Ctx.refuse_unfamiliar)
    before any check reads what it really is (a hose's coupling, a nozzle's trigger, a plunger's cup, a tap's handles).
    The twin sweep of every verb x every entity parameter found an unfamiliar hose, nozzle and plunger answered by
    their true types (disconnect_hose, drain, flush, plunge, spray, turn_on_water, turn_off_water). Implicit tools pass
    an unfamiliar held thing over (toilets._plunger, outdoor._held_hose, the basket stopper, seen._held). No-op in
    oracle mode."""
    for pname, param in sorted(ctx.spec.params.items()):
        if param.get("type") not in ("entity", "target"):
            continue
        eid = ctx.args.get(pname)
        if isinstance(eid, str) and eid in ctx.w.s["entities"]:
            ctx.refuse_unfamiliar(eid)


def guard_verbs(names):
    """Register unfamiliar_first before every verb in names (the package's VERBS, after they are all registered)."""
    for name in names:
        before(name, order=1)(unfamiliar_first)
