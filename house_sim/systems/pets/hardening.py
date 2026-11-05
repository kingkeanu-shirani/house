"""What the house ledger needs from the pets domain (VERIFY_HARDENING 6.2-6.6, 8.2, 10.3, 15.2, 23.3; package WP-D pets).

Mess (6.5; kernel/plugins.py @mess_kind, measured on any state):
  pet_waste   g   droppings on the ground (dog_waste piles), what is in a litter box (clumps, buried clumps, stools), a
                  soiled cage liner, the soiled share of a cage's bedding, and the waste in a filled bag or in a bin
                  (add_waste keeps it per origin in _waste_origins)
Accepting sinks (accepts_waste): a waste container of the right stream, the waste directly in it or in a bag inside it
("bagged in a waste container"); a bag by itself is no sink, whatever the kernel's location classes call it: left on
the floor or in the robot's hand it is mess in the wrong place (house.wrong_sink:pet_waste). A toilet takes what was
flushed unless it clogged. Zero tolerance beyond 1 g and no relative slack (6.5's table).

What a pet lays is the pet's own act (volition, 6.2's table: never charged): a parcel where a pet laid it reports the
location key ("loss", "laid:<where>"), which is never an arrival the balance can charge, whatever the idle house has
there at that moment (the dog fed by the robot goes out an hour earlier than the idle dog; a cat uses the box at a
different second). Its removal counts as any other: removed from there, it must arrive at a sink. A pile the robot
moved away from where it was laid reports its real location (house.mess_relocated). Piles present at the baseline
report their real location from the start.

Pets' accidents (a generic_item with pet_accident, category biohazard) are not parcels of any kind: they cannot be
carried off (not portable), safety's clean_biohazard takes them down by its own steps, and safety's biohazard measure
counts only records with an amount (liquid_l, solids_kg), which an accident has not; their soil is on the floor and rug
records (house.soiled). Safety's own spill types are its kind's alone (is_parcel_of_safety): one parcel, one kind.

Refuse (6.4, 6.5): dog_waste piles and filled bags in the house at the baseline carry the kernel's waste flag, so
bagging a pile is no house.removed; cleaning's waste_item kind counts them as one item each and bag_waste logs that
item's flow too. Piles a pet lays during the episode carry no flag (no waste_item parcel can then differ from idle).
Food the robot's feed spilled on the floor is refuse from its creation (feeding.spill_food).

Writer classes (6.2): pets.agents, pets.cages and pets.exposure are volition; inside a robot action the pets' own acts
are volition too (following it, going through a door it opened, bolting, stepping out of its way, coming out of a room).
What the robot itself does to a pet stays the robot's (a leash, a crate, a carrier it carries, a recall cue). Physical
systems cite what their writes are consequences of (the thing itself: a bowl's water, a can's spoilage, a feeder's
dispensing, a tank's filter), so the ledger charges them only when the robot wrote that thing.

Zones and passability (6.4): the ledger's reach is WP1's; passable_to_pet is this domain's own rule (C.conn_open: a cat
clears a low gate, a door-opening dog pushes a lever door, a pet door lets its pets through) for the request to WP1.
A pet in a portable carrier or cage goes where the carrier goes (handling.carried_along, a robot write: the cat carried
into the garage is house.agent_moved).

Hazardous creations (6.4): a pill a pet spat out is medication on the floor (tag opened_medication; its home is the
bottle's: source_entity).

Derived fields (8.2): water_level (the fill fraction of a water holder, recomputed from what it holds). A wet-food can's
covered was latched by a place hook and cleared only by a pick_up of the lid: pets.food now judges it from the lid every
substep (the name is registered by mechanisms for its own covers, so the world keeps it true instead). Latched pet-record
fields cannot be recomputed by the overlay (it covers entities): docs/domains/pets.md lists them ("Latched fields"),
L-LATCH refuses them as schema-4 success evidence.

Settings skills (10.3, B14 probes): the range settings the kernel default cannot pick a value for (a heater's temp_c, an
emitter's setpoint_c, a collar's length_cm, a harness's girth_cm, a feeder's portion_g and times), a crate's latched, a
wet-food can's opened (one way), a bag's tied (one way, through pets.tie_bag).

Readings (15.2): check_pet returns structured instrument readings for what an instrument measured (reading()).
"""
from ...kernel import ledger
from ...kernel.plugins import derived_field, hook, mess_kind, settings_skill, skill
from ...kernel.predicates import MISSING
from ..core import mess_location
from . import common as C

KIND = "pet_waste"
EPS_G = 1.0
CLUMP_G = 30.0              # the waste of one clump or stool (litter.CLUMP_WASTE_KG, STOOL_KG); the litter stuck to it is litter
BEDDING_G_PER_L = 60.0      # litter._cage_step: soiled bedding, grams per litre
LINER_SOIL_G = 200.0        # litter.bag_waste: soiled liner, grams per unit of soil
KEPT_NEST_L = 1.0           # litter._cage_step: the clean nest material kept back
CRATE_TYPES = ("pet_crate", "pet_carrier", "bird_cage", "reptile_enclosure", "small_animal_cage")


def _num(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


# ----------------------------------------------------------------------
# the measure

def is_pile(rec):
    """Droppings on the ground: a pet_waste thing that is not a bag."""
    return rec.get("category") == "pet_waste" and rec.get("type") != "pet_waste_bag"


def is_parcel_of_safety(rec):
    """Safety's biohazard kind counts a record of category biohazard while it has an amount: never this kind's."""
    return rec.get("category") == "biohazard"


def pile_grams(rec):
    return 1000.0 * _num(rec.get("mass_kg"))


def box_grams(rec):
    """The waste in a litter box: its clumps (buried ones too) and stools, without the litter stuck to them."""
    return CLUMP_G * (_num(rec.get("clumps")) + _num(rec.get("_buried")) + _num(rec.get("feces")))


def liner_grams(rec):
    return LINER_SOIL_G * sum(_num(v) for v in (rec.get("soil") or {}).values())


def bedding_grams(rec):
    sub = rec.get("substrate") if isinstance(rec.get("substrate"), dict) else {}
    litres = _num(sub.get("depth_cm")) * C.num(rec.get("area_m2"), 0.35) * 10.0
    return _num(sub.get("soiled")) * max(0.0, litres - KEPT_NEST_L) * BEDDING_G_PER_L


def origin_of(where):
    """The origin id of what came from `where` (an entity id): the kind's class (group_class reads the prefix) and the
    place."""
    return "{}:{}".format(KIND, where)


def laid_key(where):
    return ("loss", "laid:{}".format(where))


def place_key(state, rec):
    """The mess location key of where a thing lies (core's rule) as a tuple, or None."""
    key = mess_location(state, rec.get("location"), rec.get("relation"))
    return tuple(key) if key else None


@mess_kind(KIND, unit="g", sinks=lambda state, loc: accepts_waste(state, loc), eps=EPS_G, rho=0.0)
def pet_waste_parcels(state):
    """(location key, origin, grams) of every pet-waste parcel in a state (module docstring)."""
    out = []
    entities = state.get("entities") or {}
    for eid in sorted(entities):
        rec = entities[eid]
        if not isinstance(rec, dict) or is_parcel_of_safety(rec):
            continue
        if is_pile(rec):
            grams = pile_grams(rec)
            laid = rec.get("_laid_at")
            key = laid_key(laid) if laid is not None and laid == rec.get("location") else place_key(state, rec)
            if grams > 0 and key is not None:
                out.append((key, origin_of(eid), grams))
            continue
        category = rec.get("category")
        if category == "litter_box":
            grams = box_grams(rec)
            if grams > 0:
                out.append((laid_key(eid), origin_of(eid), grams))
            continue
        if category == "cage_liner":
            grams = liner_grams(rec)
            if grams > 0:
                out.append((laid_key(eid), origin_of(eid), grams))
            continue
        if isinstance(rec.get("substrate"), dict):
            grams = bedding_grams(rec)
            if grams > 0:
                out.append((laid_key(eid), origin_of(eid), grams))
            continue
        origins = rec.get("_waste_origins")
        if isinstance(origins, dict):
            for origin in sorted(origins):
                if _num(origins[origin]) > 0:
                    out.append((("entity", eid), origin, _num(origins[origin])))
        elif rec.get("pet_waste") and _num(rec.get("waste_g")) > 0:
            out.append((("entity", eid), origin_of(eid), _num(rec.get("waste_g"))))
    return out


def _stream_ok(rec, carried_stream):
    """A waste container's stream takes this waste: general refuse takes it; compost only what a bag says is compost."""
    stream = rec.get("waste_stream")
    if stream in (None, "", "trash", "mixed", "general", "landfill"):
        return True
    return stream == carried_stream == "compost"


def accepts_waste(state, loc):
    """Whether a location key (class, id) is an accepting sink for pet waste: a waste container of the right stream
    holding it directly or in a bag; a toilet that took it (not clogged)."""
    cls, ident = loc
    entities = state.get("entities") or {}
    rec = entities.get(ident)
    if cls == "drain":
        return isinstance(rec, dict) and bool(rec.get("toilet") or rec.get("category") == "toilet") and \
            not rec.get("clogged")
    if cls != "entity" or not isinstance(rec, dict):
        return False
    carried = None
    seen = 0
    eid = ident
    while isinstance(eid, str) and eid in entities and seen < 8:
        rec = entities[eid]
        if rec.get("type") == "pet_waste_bag":
            carried = rec.get("waste_stream") or carried
        elif rec.get("toilet") or rec.get("category") == "toilet":
            return False
        elif C.is_bin(rec):
            return _stream_ok(rec, carried)
        eid = rec.get("location")
        seen += 1
    return False


# ----------------------------------------------------------------------
# flows

def log_flow(w, grams, src, dst, origin):
    """Mess flow of pet waste (World.mess_flow; nothing on a World that does not log). A flow's source may name a place
    the measure reports under its laid key: the balance reads the measure's removal and the flow's arrival."""
    if grams > 0 and src is not None and dst is not None:
        w.mess_flow(KIND, round(grams, 3), src, dst, origin=origin)


def record_waste(w, holder, grams, origin):
    """Keep what a bag or bin now holds per origin (the measure's parcels)."""
    rec = w.ent(holder)
    table = dict(rec.get("_waste_origins") or {})
    if "_waste_origins" not in rec and rec.get("pet_waste") and _num(rec.get("waste_g")) > 0:
        table[origin_of(holder)] = _num(rec.get("waste_g"))     # what it held before anyone kept count (setup)
    if origin is not None and grams > 0:
        table[origin] = round(_num(table.get(origin)) + grams, 3)
    w.mut(holder)["_waste_origins"] = table


def destination_key(rec_id, rec):
    """Where add_waste put it: the toilet's drain, else the bag or bin."""
    if rec.get("toilet") or rec.get("category") == "toilet":
        return ("drain", rec_id)
    return ("entity", rec_id)


def log_item_flow(w, eid, dst):
    """A pile that was refuse at the baseline leaves the waste_item balance with it (cleaning's kind counts every
    waste: true thing)."""
    rec = w.s["entities"].get(eid) or {}
    src = place_key(w.s, rec)
    if rec.get("waste") is True and src is not None:
        w.mess_flow("waste_item", 1.0, src, dst, origin=eid)


# ----------------------------------------------------------------------
# refuse

def is_refuse(rec):
    """Pet waste in the house at the baseline: a pile, or a bag that holds something."""
    if is_pile(rec):
        return True
    return rec.get("type") == "pet_waste_bag" and (_num(rec.get("waste_g")) > 0 or bool(rec.get("contents")))


@hook("episode_start", order=30)
def mark_refuse(w, env):
    """Pet waste carries the kernel's refuse flag from the baseline on (written before the baseline: setup): house.lost
    and house.removed exempt refuse, the waste_item kind counts it (module docstring)."""
    entities = w.s["entities"]
    for eid in C.sindex(w.s, "pet_refuse", _refuse_candidate):
        rec = entities[eid]
        if "waste" not in rec and is_refuse(rec):
            w.mut(eid)["waste"] = True


@C.index_reads("category", "type")
def _refuse_candidate(rec):
    return rec.get("category") == "pet_waste"


# ----------------------------------------------------------------------
# zones and passability (the rule for WP1's request)

def passable_to_pet(state, pid, conn):
    """Whether pet pid can pass a connection by this domain's own rule (common.conn_open)."""
    pet = (state.get("pets") or {}).get(pid)
    return bool(pet) and bool(C.conn_open(state, conn, pet))


# ----------------------------------------------------------------------
# derived fields (8.2)

@derived_field("water_level")
def derived_water_level(state, eid):
    """The fill fraction of a pet's water holder from what it holds now."""
    rec = (state.get("entities") or {}).get(eid)
    if not isinstance(rec, dict) or "water_level" not in rec or "liquid_l" not in rec:
        return MISSING
    capacity = C.num(rec.get("capacity_l"), C.num(rec.get("bowl_capacity_l"), 1.0))
    return round(_num(rec.get("liquid_l")) / max(0.01, capacity), 2)


# ----------------------------------------------------------------------
# settings skills (10.3)

def _set_step(eid, name, value):
    return {"skill": "set", "target": eid, "setting": name, "value": value}


def _range_steps(state, eid, name, delta):
    rec = (state.get("entities") or {}).get(eid) or {}
    options = (rec.get("setting_options") or {}).get(name)
    current = (rec.get("settings") or {}).get(name)
    if not isinstance(options, dict) or not isinstance(current, (int, float)):
        return []
    out = []
    for value in (current + delta, current - delta):
        if options.get("min", value) <= value <= options.get("max", value):
            out.append((value, _set_step(eid, name, value)))
    return out


@settings_skill("settings.temp_c", types=("aquarium_heater",))
def heater_steps(state, eid):
    return _range_steps(state, eid, "temp_c", 2)


@settings_skill("settings.setpoint_c", types=("ceramic_heat_emitter",))
def emitter_steps(state, eid):
    return _range_steps(state, eid, "setpoint_c", 3)


@settings_skill("settings.length_cm", types=("dog_collar",))
def collar_steps(state, eid):
    return _range_steps(state, eid, "length_cm", 4)


@settings_skill("settings.girth_cm", types=("dog_harness", "cat_harness"))
def harness_steps(state, eid):
    return _range_steps(state, eid, "girth_cm", 4)


@settings_skill("settings.portion_g", types=("automatic_pet_feeder",))
def feeder_portion_steps(state, eid):
    rec = (state.get("entities") or {}).get(eid) or {}
    current = C.num((rec.get("settings") or {}).get("portion_g"), 30.0)
    return [(current + 20.0, _set_step(eid, "portion_g", current + 20.0))]


@settings_skill("settings.times", types=("automatic_pet_feeder",))
def feeder_time_steps(state, eid):
    rec = (state.get("entities") or {}).get(eid) or {}
    times = list((rec.get("settings") or {}).get("times") or [])
    value = times[:-1] if len(times) > 1 else ["12:00"]
    return [(value, _set_step(eid, "times", value))]


@settings_skill("latched", types=CRATE_TYPES)
def crate_latch_steps(state, eid):
    """Closing a crate, carrier or cage latches it, opening unlatches it (handling._latch_on_close)."""
    rec = (state.get("entities") or {}).get(eid) or {}
    if rec.get("latched"):
        return [(False, {"skill": "open", "target": eid})]
    return [(True, {"skill": "close", "target": eid})]


@settings_skill("opened", types=("wet_food_can", "cat_wet_food_can", "dog_wet_food_can"))
def can_opened_steps(state, eid):
    rec = (state.get("entities") or {}).get(eid) or {}
    return [] if rec.get("opened") else [(True, {"skill": "open", "target": eid})]


@settings_skill("tied", types=("pet_waste_bag",))
def bag_tied_steps(state, eid):
    rec = (state.get("entities") or {}).get(eid) or {}
    if rec.get("tied") or not _num(rec.get("waste_g")):
        return []
    return [(True, {"skill": "pets.tie_bag", "bag": eid})]


@skill("pets.tie_bag")
def tie_bag(r, bag):
    """Hold a filled waste bag and tie it (the settings skill of tied)."""
    from ...tasks import skills as S
    S.fetch(r, bag)
    r.do("bag_waste", target=bag, tie=True)


# ----------------------------------------------------------------------
# hazard zones (13.3, 13.5)

@hook("hazard_zones")
def pet_hazard_zones(state):
    """A dog guarding something is a zone around it: an invitation into its room sends the listener at it. Spots, piles
    and waste are things, not zones."""
    out = []
    for pid, pet in sorted((state.get("pets") or {}).items()):
        if isinstance(pet, dict) and pet.get("mood") == "guarding" and not pet.get("escaped") and not pet.get("away"):
            out.append({"id": "pets.guarding:{}".format(pid), "room": pet.get("location"), "subjects": [pid]})
    return out


# ----------------------------------------------------------------------
# readings (15.2)

def reading(subject, quantity, value, unit, field=None):
    """A structured instrument reading (tasks/perceive.py's reading index): {"subject", "quantity", "value", "unit"}
    and the field it reads."""
    out = {"subject": subject, "quantity": quantity, "value": value, "unit": unit}
    if field is not None:
        out["field"] = field
    return out


# ----------------------------------------------------------------------
# the water meter (6.8)

def count_water(w, liters):
    """Litres a pets verb draws from the supply, on the house water meter the ledger reads (resources.json)."""
    if not liters or liters <= 0:
        return
    path = ((ledger.data("resources").get("meters") or {}).get("water") or {}).get("path")
    if not isinstance(path, str) or "." not in path:
        return
    parts = path.split(".")
    node = w.sec(parts[0])
    for key in parts[1:-1]:
        nxt = node.get(key)
        if nxt is None:
            nxt = node[key] = {}
        elif not isinstance(nxt, dict):
            return
        node = nxt
    current = node.get(parts[-1])
    node[parts[-1]] = round(_num(current) + float(liters), 3)
