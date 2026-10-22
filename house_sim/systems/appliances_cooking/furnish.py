"""Furnishers: kitchen appliances (order 45), outdoor grills (46) and garage/basement freezers (47).

Regional variety (metadata.region): US mostly electric coil/radiant or gas ranges with F ovens, UK/EU induction or
radiant hobs with C ovens and kettles almost everywhere, JP gas hobs, rice cookers and fewer dishwashers. Every
kitchen gets a cooktop + oven, a hood (freestanding ranges), a refrigerator with a freezer, and usually a dishwasher
(built in under a counter), plus a modest set of small appliances on counters or stored in base cabinets, oven
mitts in the towel drawer and a probe thermometer in the utensil drawer. Floor appliances are placed with the kernel
v2.3 access rule (build.place_floor_appliance: nothing that was accessible - furniture, fixtures, outlets, radiators,
windows, wall cabinets - becomes inaccessible, and nothing tall stands in front of a window or radiator); appliance parts
(freezer compartment, propane cylinder, racks, burners) are children of their appliance; small appliances put away in
cabinets are unplugged; loose floor items go through Placer.floor_item (keep_access). A full kitchen gets built-in
variants and missing optional appliances rather than failing.

Generation 2 (opts["gen"] >= 2, docs/GENERATOR.md): the residents' usual hot drinks (household profiles,
people.<pid>.profile.drinks.hot) choose the drink makers: a household where someone drinks tea has an electric kettle,
one where someone drinks coffee has a coffee machine (drip, pod or espresso by the regional shares; the small pod
machine when the drawn one does not fit; none only on a counter with no room left), and one where nobody drinks coffee
mostly has none. The same random draws are taken in both generations, so generation-1 houses are
unchanged; a generation-1 kitchen also keeps the field values the adoption round changed in the type data (GEN1_PROPS:
burners and the electric griddle own their temperature through the older fixed_temperature alias, a pressure cooker
is put away lid open), because generation-1 houses stay byte-identical (TIER1_INTEGRATION 3.2).
"""
from ...generator.place import Placer
from ...kernel import geometry
from ...kernel.plugins import furnisher
from . import build as B
from . import common as C

# type-data changes made after generation 1 was frozen: what a generation-1 kitchen keeps instead (_generation_one)
GEN1_PROPS = {"burner": {"fixed_temperature": True}, "electric_griddle": {"fixed_temperature": True},
              "electric_pressure_cooker": {"open": True}}

STOVES = {
    "us": [("electric_coil", 0.4), ("radiant", 0.25), ("gas", 0.3), ("induction", 0.05)],
    "ca": [("gas", 0.4), ("electric_coil", 0.25), ("radiant", 0.2), ("induction", 0.15)],
    "uk": [("induction", 0.45), ("radiant", 0.3), ("gas", 0.25)],
    "eu": [("induction", 0.6), ("radiant", 0.25), ("gas", 0.15)],
    "jp": [("gas", 0.75), ("induction", 0.25)],
}


def _pick(rng, options):
    x = rng.random()
    acc = 0.0
    for value, prob in options:
        acc += prob
        if x <= acc:
            return value
    return options[-1][0]


def _counters(p, room):
    ents = p.c["entities"]
    return [c for c in p.find("counter", room=room) if ents[c].get("location") == room]


def _lower_cabinets(p, room):
    ents = p.c["entities"]
    return [c for c in p.find("cabinet", room=room) if ents[c].get("type") == "cabinet" and ents[c].get("location") == room]


def _drawer(p, room, word):
    """The labelled drawer when the robot can get to it, else the first reachable drawer (a drawer mounted deep inside a
    counter run cannot be reached), else the labelled one."""
    ents = p.c["entities"]
    state = p.state()
    drawers = p.find("drawer", room=room)
    named = [d for d in drawers if word in str(ents[d].get("label") or "")]
    for d in named + drawers:
        if C.approachable(state, d):
            return d
    return named[0] if named else (drawers[0] if drawers else None)


def _on_some_counter(p, counters, type_name, rng, **props):
    order = list(counters)
    rng.shuffle(order)
    for c in order:
        eid = p.on(c, type_name, **props)
        if eid:
            return eid
    return None


COUNTER_SHARE = 0.35     # small appliances left out take at most ~a third of the counter; the rest stays work space


def _area(p, type_name):
    size = (p.reg.resolve(type_name) if p.reg.known(type_name) else {}).get("size_m") or [0.3, 0.3, 0.3]
    return float(size[0]) * float(size[1])


def _counter_room(p, counters, type_name):
    """True while this domain's countertop appliances stay within COUNTER_SHARE of the counters' area."""
    ents = p.c["entities"]
    total = 0.0
    used = 0.0
    for c in counters:
        rec = ents[c]
        dims = rec.get("surface_size_m") or (rec.get("size_m") or [1.0, 0.6])[:2]
        total += float(dims[0]) * float(dims[1])
        for k in C.children(p.state(), c, "on"):
            krec = ents[k]
            if krec.get("ac_kind") or krec.get("category") == "appliance":
                fp = geometry.footprint_m(krec)
                used += fp[0] * fp[1]
    return used + _area(p, type_name) <= COUNTER_SHARE * total


def _store(p, cabinets, type_name, rng, extra_kg=0.0, **props):
    """Store an item (and `extra_kg` of parts that go with it) in a base cabinet: reachable cabinets first (not the
    sink base when another has room), on the lowest shelf that stays within B.STORE_SHARE of its load rating."""
    order = list(cabinets)
    rng.shuffle(order)
    state = p.state()
    ents = p.c["entities"]
    order.sort(key=lambda c: (not C.approachable(state, c), bool(ents[c].get("sink_base"))))   # stable: shuffled within
    return B.store_in_cabinets(p, order, type_name, extra_kg=extra_kg, **props)


def _hot_drinks(config):
    """What a generation-2 household drinks hot: {"coffee", "tea"} words from the residents' profiles."""
    out = set()
    for person in (config.get("people") or {}).values():
        hot = str(((person.get("profile") or {}).get("drinks") or {}).get("hot") or "").lower()
        if "coffee" in hot:
            out.add("coffee")
        if "tea" in hot:
            out.add("tea")
    return out


def _generation_one(ents, before):
    """The GEN1_PROPS values on what this furnisher placed in a generation-1 house (thermal_owner gives way to the
    fixed_temperature alias: the kernel reads both as this domain's ownership)."""
    for eid, rec in ents.items():
        old = GEN1_PROPS.get(rec.get("type"))
        if old and eid not in before:
            if "fixed_temperature" in old:
                rec.pop("thermal_owner", None)
            rec.update(old)


def _pod_machine(p, counters, rng):
    """A pod coffee machine with its pod holder on some counter; its id, or None."""
    pm = _on_some_counter(p, counters, "pod_coffee_machine", rng)
    if pm:
        holder = p.make("pod_holder", pm, eid=pm + "_holder")
        p.c["entities"][holder]["relation"] = "part"
    return pm


def _kg(p, *type_names):
    return sum(C.num((p.reg.resolve(t) if p.reg.known(t) else {}).get("mass_kg"), 0.5) for t in type_names)


@furnisher("appliances_cooking.kitchen", order=45)
def furnish_kitchen(config, rng, opts, registry):
    p = Placer(config, registry, rng)
    meta = config.get("metadata", {})
    region = opts.get("region") or meta.get("region") or "us"
    dwelling = meta.get("dwelling_type", "house")
    gas_service = config.get("utilities", {}).get("gas", True)
    ents = config["entities"]
    gen2 = int(opts.get("gen") or 1) >= 2
    drinks = _hot_drinks(config) if gen2 else set()
    before = set(ents)
    for kitchen in p.rooms("kitchen"):
        counters = _counters(p, kitchen)
        kind = _pick(rng, STOVES.get(region, STOVES["us"]))
        if kind == "gas" and not gas_service:
            kind = "induction" if region in ("uk", "eu", "jp") else "electric_coil"
        unit = "F" if region == "us" else "C"
        near = ents[counters[0]]["pos"] if counters else None
        rng_out = B.build_range(p, kitchen, kind=kind, unit=unit, near=near)
        if rng_out and rng_out.get("oven"):
            ents[rng_out["oven"]]["oven"]["convection"] = rng.random() < 0.5
            ents[rng_out["oven"]]["oven"]["self_clean"] = rng.random() < 0.75
            ents[rng_out["oven"]]["oven"]["panel"] = "knob" if rng.random() < 0.3 else "touch"
        style = rng.choice(["bottom_freezer", "bottom_freezer", "top_freezer"])
        B.build_fridge(p, kitchen, style=style)
        counters = _counters(p, kitchen)
        dw_prob = {"house": 0.92, "townhouse": 0.88, "apartment": 0.7}.get(dwelling, 0.85) * (0.4 if region == "jp" else 1.0)
        dw = None
        if counters and rng.random() < dw_prob:
            brand = rng.choice(["generic", "generic", "brand_a", "brand_b", "brand_c"])
            dw = B.build_dishwasher(p, kitchen, brand=brand, rinse_aid_l=round(rng.uniform(0.0, 0.12), 3))
        cabinets = _lower_cabinets(p, kitchen)
        if dw and cabinets:
            tub = _store(p, cabinets, "dishwasher_pod_tub", rng, extra_kg=0.16)
            if tub:
                for _ in range(rng.randint(5, 10)):
                    p.inside(tub, "dishwasher_pod")
            if rng.random() < 0.6:
                _store(p, cabinets, "rinse_aid_bottle", rng)
        if counters:
            fits = lambda t: _counter_room(p, counters, t)  # noqa: E731
            if rng.random() < 0.85 and fits("microwave"):
                _on_some_counter(p, counters, "microwave", rng)
            p_kettle = 0.95 if region in ("uk", "eu", "jp") or "tea" in drinks else 0.45
            if rng.random() < p_kettle and fits("kettle_base"):
                c0 = rng.choice(counters)
                B.build_kettle(p, c0) or B.build_kettle(p, counters[0])
            r = rng.random()
            if gen2 and "coffee" in drinks and r >= 0.92:
                r = 0.0                                      # someone drinks coffee every day: there is a machine
            elif gen2 and "coffee" not in drinks and r < 0.8:
                r = 1.0                                      # most coffee-free households own none
            machine = None
            if r < (0.35 if region == "eu" else 0.6):
                if fits("drip_coffee_maker"):
                    machine = B.build_coffee_maker(p, rng.choice(counters))
            elif r < 0.8:
                machine = _pod_machine(p, counters, rng) if fits("pod_coffee_machine") else None
            elif r < 0.92 and fits("espresso_machine"):
                machine = B.build_espresso(p, rng.choice(counters), water_l=round(rng.uniform(0.5, 1.8), 2))
            if gen2 and "coffee" in drinks and not machine and fits("pod_coffee_machine"):
                _pod_machine(p, counters, rng)               # a crowded counter still finds room for the smallest one
            if rng.random() < (0.35 if region == "jp" else 0.75) and fits("toaster"):
                _on_some_counter(p, counters, "toaster", rng)
            if rng.random() < (0.85 if region == "jp" else 0.15) and fits("rice_cooker"):
                B.build_rice_cooker(p, rng.choice(counters))
            if rng.random() < 0.3 and fits("air_fryer"):
                B.build_air_fryer(p, rng.choice(counters))
            if rng.random() < 0.2 and fits("stand_mixer"):
                B.build_stand_mixer(p, rng.choice(counters))
        if cabinets:
            if rng.random() < 0.45:
                base = _store(p, cabinets, "blender", rng, extra_kg=_kg(p, "blender_jar"))
                if base:
                    p.inside(ents[base]["location"], "blender_jar", shelf=ents[base].get("shelf", 0))
            if rng.random() < 0.3:
                sc = _store(p, cabinets, "slow_cooker", rng, extra_kg=_kg(p, "slow_cooker_insert"))
                if sc:
                    p.inside(ents[sc]["location"], "slow_cooker_insert", shelf=ents[sc].get("shelf", 0))
            if rng.random() < 0.25:
                kg = _kg(p, "food_processor", "processor_bowl", "processor_s_blade", "processor_lid")
                first = rng.choice(cabinets)
                for cab in [first] + [c for c in cabinets if c != first]:
                    shelf = B.shelf_with_room(p, cab, kg)
                    if shelf is not None and B.build_food_processor(p, cab, shelf=shelf):
                        break
            if rng.random() < 0.2:
                pc = _store(p, cabinets, "electric_pressure_cooker", rng, extra_kg=_kg(p, "pressure_cooker_pot", "sealing_ring"))
                if pc:
                    pot = p.make("pressure_cooker_pot", pc, eid=pc + "_pot")
                    ents[pot]["relation"] = "in"
                    ring = p.make("sealing_ring", pc, eid=pc + "_ring")
                    ents[ring].update(relation="part", slot="seal")
                    ents[pc]["slots"] = {"seal": {"accepts": ["sealing_ring"], "item": ring, "access": "open"}}
            if rng.random() < 0.15:
                _store(p, cabinets, "waffle_maker", rng)
            if rng.random() < 0.12:
                _store(p, cabinets, "electric_griddle", rng)
        towel = _drawer(p, kitchen, "towel")
        utensil = _drawer(p, kitchen, "utensil")
        if towel:
            p.inside(towel, "oven_mitts", home=towel)
            if rng.random() < 0.6:
                p.inside(towel, "pot_holder", home=towel)
        elif counters:
            _on_some_counter(p, counters, "oven_mitts", rng)
        if utensil:
            if rng.random() < 0.6:
                p.inside(utensil, "instant_read_thermometer", home=utensil)
            if rng.random() < 0.3:
                p.inside(utensil, "cake_tester", home=utensil)
        if counters and rng.random() < 0.5:
            _on_some_counter(p, counters, "kitchen_timer", rng)
    if not gen2:
        _generation_one(ents, before)


def _farthest_cell(config, room):
    rec = config["rooms"][room]
    cols, rows = geometry.grid_size(rec)
    doors = [tuple(conn["cells"][room]) for conn in config.get("connections", {}).values()
             if room in conn.get("cells", {}) and not config["rooms"].get(conn["rooms"][0] if conn["rooms"][1] == room else conn["rooms"][1], {}).get("outdoor")]
    if not doors:
        return [cols // 2, rows // 2]
    best, best_d = [0, 0], -1
    for y in range(rows):
        for x in range(cols):
            d = min(geometry.chebyshev((x, y), dcell) for dcell in doors)
            if d > best_d:
                best, best_d = [x, y], d
    return best


@furnisher("appliances_cooking.outdoor", order=46)
def furnish_outdoor(config, rng, opts, registry):
    p = Placer(config, registry, rng)
    rooms = [r for r in p.rooms("patio") + p.rooms("deck") + p.rooms("backyard")]
    if not rooms or rng.random() > 0.6:
        return
    room = rooms[0]
    kind = "gas" if rng.random() < 0.7 else "charcoal"
    out = B.build_grill(p, room, kind=kind, near=_farthest_cell(config, room))
    if out and kind == "charcoal":
        near = config["entities"][out["grill"]]["pos"]
        if registry.known("metal_ash_can"):   # outdoor_plants owns the type; fall back to an explicit generic can
            p.floor_item(room, "metal_ash_can", near=near)
        else:
            p.floor_item(room, "generic_item", near=near, label="metal ash can", category="ash_can", ash_can=True, material="steel",
                         accepts_items=True, container=True, openable=True, open=False, capacity_l=25.0, inner_size_m=[0.3, 0.3, 0.38],
                         size_m=[0.33, 0.33, 0.42], mass_kg=3.0, heat_resistant=True, noncombustible=True)


@furnisher("appliances_cooking.storage", order=47)
def furnish_storage(config, rng, opts, registry):
    p = Placer(config, registry, rng)
    rooms = p.rooms("garage") + p.rooms("basement")
    if not rooms or rng.random() > 0.3:
        return
    room = rng.choice(rooms)
    out = B.build_freezer(p, room, type_name=rng.choice(["chest_freezer", "upright_freezer"]), frost_mm=round(rng.uniform(1.0, 9.0), 1))
    if out:
        config["entities"][out["freezer"]]["internal_temp_c"] = -18.0
