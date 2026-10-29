"""Consumable restocking: taking units from counted stock (sheets from a ream, napkins, liners,
staple strips), mounting paper rolls on holders in the household's orientation, refilling
dispensers and bulk jars from refills (liquid or granular) with spill/overflow, product-mismatch,
foaming-clog, allergen and pest-transfer consequences and FIFO layering, napkin holder
overstuffing, tissue boxes and bar soap on wet dishes.
"""
from ...kernel import geometry, physics
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, hook
from ..core import add_soil, release_from_hands
from .common import admin_facts, drop_cell, held_with, indexed, reads, slug
from .perceive import approx_litres, count_seen, dumps, name_of, render, spill_words

VISCOUS = {"hand_soap": 0.04, "foaming_hand_soap": 0.08, "dish_soap": 0.04, "lotion": 0.02, "shampoo": 0.04,
           "body_wash": 0.04, "laundry_detergent": 0.06, "hand_sanitizer": 0.05}
DRY_GOODS_KG_L = 0.8        # bulk density of spilled dry goods (rice 0.75-0.85, sugar 0.85, flour 0.6, kibble 0.5 kg/L)
CHEM_MIX_GAS = {("chlorine_bleach", "ammonia"), ("chlorine_bleach", "acid"), ("ammonia", "chlorine_bleach"), ("acid", "chlorine_bleach")}


# ----------------------------------------------------------------------
# counted stock

@action("take_unit", params={"target": "entity", "count": {"type": "int", "default": 1, "min": 1, "max": 500}, "hand": "?str:left|right"},
        duration=lambda ctx: 2.0 + 0.01 * ctx.args.get("count", 1), energy=0.001, category="physical",
        doc="Take units from an open counted-stock package (sheets from a ream, napkins from a pack, a liner from a box, a staple strip) into a free hand.")
def take_unit(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = ctx.obj(target, touch=True)
    if rec.get("paper_stack"):
        return _split_paper(ctx, target, rec)
    require(rec.get("units_count") is not None and rec.get("unit_type"), "Units are not taken out of that one at a time", "argument")
    require(not rec.get("openable") or rec.get("open"), "Open it first", "precondition")
    n = ctx.args["count"]
    have = int(rec.get("units_count", 0))
    require(have > 0, "It is empty", "resource")
    require(n <= have, "Only {} {} left".format(count_seen(ctx.env, target, have), rec.get("unit_name", "units")), "resource")
    hand = ctx.free_hand(ctx.args.get("hand"))
    require(hand is not None, "No free hand", "capability")
    m = w.mut(target)
    m.setdefault("pack_count", have if not rec.get("opened_before") else rec.get("pack_count", have))
    m["units_count"] = have - n
    m["opened_before"] = True
    if m["units_count"] <= 0:
        m["empty"] = True
    unit_type = rec["unit_type"]
    props = dict(rec.get("unit_props") or {})
    if unit_type == "paper_stack":
        props.update(sheets_count=n, paper_size=rec.get("paper_size", "letter"), sku=rec.get("sku"),
                     mass_kg=round(0.0045 * n, 4), volume_l=round(0.0062 * n, 4),
                     size_m=[rec.get("size_m", [0.216, 0.279])[0], rec.get("size_m", [0.216, 0.279])[1], round(0.0001 * n, 4)],
                     label="stack of {} sheets of {} paper".format(n, rec.get("paper_size", "letter")))
    elif unit_type == "napkin_stack":
        props.update(napkins=n, mass_kg=round(0.003 * n, 4), size_m=[0.165, 0.165, round(0.0007 * n, 4)], volume_l=round(0.02 * n, 3))
    elif unit_type == "staple_strip" and n > 1:
        props.update(count=210 * n)
    elif n > 1:
        props.update(units=n)
    if n > 1 and unit_type not in ("paper_stack", "napkin_stack"):
        # one entity stands for the n units taken: it weighs and fills what n of them do (the kernel's subtree mass and
        # the body's carry read mass_kg)
        unit = w.reg.make(unit_type, "robot") if w.reg is not None and w.reg.known(unit_type) else {}
        for key in ("mass_kg", "volume_l"):
            base = props.get(key, unit.get(key))
            if isinstance(base, (int, float)) and not isinstance(base, bool):
                props[key] = round(float(base) * n, 4)
    if rec.get("sku") and "sku" not in props and unit_type not in ("staple_strip",):
        props["sku"] = rec.get("sku")
    new = w.spawn(unit_type, "robot", **props)
    ctx.check_lift(new, hand)           # the body's derated hand capacity and total carry (TIER1_INTEGRATION 1.5)
    nm = w.mut(new)
    nm["relation"], nm["held_hand"] = "held", hand
    nm["derived_from"] = target          # a portion looks like its package until that package is identified
    w.robot_m()["hands"][hand] = new
    w.log_move(new, target, "robot")
    ctx.grant(new, "measure", ["sheets_count", "napkins", "count", "units"])   # the robot counted out what it took
    return dumps(ctx.env, {"took": new, "count": n, "left": count_seen(ctx.env, target, m["units_count"])})


def _split_paper(ctx, target, rec):
    """Lift some sheets off a loose paper stack into a free hand."""
    w = ctx.w
    n = ctx.args["count"]
    have = int(rec.get("sheets_count", 0))
    require(n < have, "The stack only has {} sheets; pick up the whole stack instead".format(count_seen(ctx.env, target, have)), "precondition")
    hand = ctx.free_hand(ctx.args.get("hand"))
    require(hand is not None, "No free hand", "capability")
    m = w.mut(target)
    m["sheets_count"] = have - n
    for stack, count in ((m, have - n),):
        stack["mass_kg"] = round(0.0045 * count, 4)
        stack["volume_l"] = round(0.0062 * count, 4)
        stack["size_m"] = [rec["size_m"][0], rec["size_m"][1], round(0.0001 * count, 4)]
    new = w.spawn("paper_stack", "robot", sheets_count=n, paper_size=rec.get("paper_size", "letter"), sku=rec.get("sku"),
                  mass_kg=round(0.0045 * n, 4), volume_l=round(0.0062 * n, 4),
                  size_m=[rec["size_m"][0], rec["size_m"][1], round(0.0001 * n, 4)])
    ctx.check_lift(new, hand)
    nm = w.mut(new)
    nm["relation"], nm["held_hand"] = "held", hand
    nm["derived_from"] = target
    w.robot_m()["hands"][hand] = new
    w.log_move(new, target, "robot")
    ctx.grant(new, "measure", ["sheets_count"])
    return dumps(ctx.env, {"took": new, "count": n, "left": count_seen(ctx.env, target, have - n)})


# ----------------------------------------------------------------------
# rolls on holders

def _roll_empty(rec):
    return float(rec.get("remaining", 1.0)) <= 0.0


@action("mount_roll", params={"target": "entity", "destination": "entity", "orientation": "?str:over|under"},
        duration=8.0, energy=0.004, category="physical",
        doc="Mount a held paper roll on a holder (toilet paper / paper towel) with the sheet coming over or under the roll. The holder's slot must be empty (take off an empty core first).")
def mount_roll(ctx):
    w = ctx.w
    roll, holder = ctx.args["target"], ctx.args["destination"]
    require(ctx.holding(roll), "Hold the roll first", "precondition")
    h = ctx.obj(holder, touch=True, action="install")
    slots = h.get("slots") or {}
    require("roll" in slots, "That is not a roll holder", "argument")
    current = slots["roll"].get("item")
    if current:
        cur = w.ent(current)
        if _roll_empty(cur):
            raise ActionError("Take the empty core off the holder first", "precondition")
        left = float(cur.get("remaining", 1.0))
        if ctx.realistic:
            # how much is left on a roll is judged by eye (the kernel's inspect estimate), never the exact fraction
            seen = render(ctx.env, current, "remaining", left, "inspect")
            raise ActionError("The holder still has a usable roll{}".format(
                " (about {:.0%} left)".format(seen) if isinstance(seen, float) else ""), "precondition")
        raise ActionError("The holder still has a usable roll ({:.0%} left)".format(left), "precondition")
    ctx.env.perform({"action": "install", "target": roll, "destination": holder, "slot": "roll"}, ctx)
    orientation = ctx.args.get("orientation") or "over"
    w.mut(holder)["roll_orientation"] = orientation
    m = w.mut(roll)
    m["orientation_on_holder"] = orientation
    if float(m.get("wetness", 0)) > 0.3:
        m["ruined"] = True
    return "Mounted {} on {} ({})".format(roll, holder, orientation)


@after("install", order=60)
def _default_orientation(ctx, result):
    w = ctx.w
    holder = ctx.args.get("destination")
    rec = w.s["entities"].get(holder)
    if rec is not None and "roll_orientation" in rec and (rec.get("slots") or {}).get("roll", {}).get("item") == ctx.args.get("target"):
        # kernel v2.1: core install accepts orientation; a roll goes on "over" unless told "under"
        wanted = str(ctx.args.get("orientation") or "").lower()
        w.mut(holder)["roll_orientation"] = wanted if wanted in ("over", "under") else "over"


# ----------------------------------------------------------------------
# refills

def _contents(rec):
    """(product, liters) of a liquid or bulk container (top layer product for bulk)."""
    if rec.get("liquid_container"):
        return rec.get("liquid"), float(rec.get("liquid_l", 0.0))
    if rec.get("bulk_store"):
        layers = rec.get("layers") or []
        total = sum(float(l.get("l", 0.0)) for l in layers)
        return (layers[-1].get("product") if layers else None), total
    return None, 0.0


def _capacity(rec):
    if rec.get("liquid_container"):
        return float(rec.get("fill_line_l") or rec.get("capacity_l", 0.0)), float(rec.get("capacity_l", 0.0))
    return 0.95 * float(rec.get("capacity_l", 0.0)), float(rec.get("capacity_l", 0.0))


def _take_layers(rec, amount):
    """Remove `amount` liters from the top of a layered bulk container; returns removed layers (top first)."""
    layers = [dict(l) for l in rec.get("layers") or []]
    taken = []
    left = amount
    while left > 1e-9 and layers:
        top = layers[-1]
        part = min(left, float(top["l"]))
        taken.append(dict(top, l=round(part, 4)))
        top["l"] = round(float(top["l"]) - part, 4)
        left -= part
        if top["l"] <= 1e-6:
            layers.pop()
    rec["layers"] = layers
    return taken


def _add_layers(rec, layers):
    current = [dict(l) for l in rec.get("layers") or []]
    for layer in layers:
        if current and current[-1].get("product") == layer.get("product") and current[-1].get("lot") == layer.get("lot"):
            current[-1]["l"] = round(float(current[-1]["l"]) + float(layer["l"]), 4)
        else:
            current.append(dict(layer))
    rec["layers"] = current


def _spill(w, near, product, liters, kind="liquid", generic=False, cause="spill"):
    """Spilled product lands on the surface the container stands on, or the floor. A liquid is the kernel's spill
    (phys.liquids.spill, TIER1_PHYSICS 5.7, attributed to the robot: the surface takes a film and keeps what it can,
    the rest runs down to the floor as a puddle -- the cleaning domain's `spill` entity through its phys.spill_sink
    when it is loaded -- counted in spills / phys_spills); soap leaves the surface sticky. Dry goods (rice, flour,
    kibble) are a debris pile this domain makes. generic (realistic perception): a spill is labeled by what it looks
    like (what it is shows up close: the spill's inspect-tier liquid). cause "overflow" logs the kernel's
    phys.overflow (liquid over the brim), every other one phys.spill. Returns the floor spill's id (or None)."""
    rec = w.ent(near)
    parent = rec.get("location")
    if parent == "robot":
        parent = w.robot["room"]
    target_surface = None
    if parent in w.s["entities"] and w.ent(parent).get("accepts_items"):
        target_surface = parent
        add_soil(w, parent, "sticky" if kind == "liquid" else "food", min(1.0, 0.2 + liters * 2.0))
        room = None
    else:
        room = parent if parent in w.s["rooms"] else w.robot["room"]
    if kind == "liquid":
        if target_surface:
            spill = physics.spill(w, liters, liquid=product or "water", onto=target_surface, source=near, by="robot",
                                  cause=cause)
        else:
            spill = physics.spill(w, liters, liquid=product or "water", at=room, source=near, by="robot", cause=cause)
        if spill in w.s["entities"] and generic:
            w.mut(spill)["label"] = "spilled liquid"
        return spill
    label = "spilled dry goods" if generic else "{} spill".format(str(product or "product").replace("_", " "))
    pile = {"label": label, "category": "debris", "portable": False, "product": product, "amount_l": round(liters, 3),
            "mass_kg": round(liters * DRY_GOODS_KG_L, 3), "volume_l": round(liters, 3), "soil": {"food": 0.5},
            "waste_stream": "trash"}
    if target_surface:
        spill = w.spawn("generic_item", target_surface, size_m=[0.15, 0.15, 0.002], **pile)
        sm = w.mut(spill)
        sm["relation"] = "on"
        spot = geometry.surface_spot(w.s, target_surface, spill)
        if spot is not None:
            sm["spos"] = spot
    else:
        spill = w.spawn("generic_item", room, size_m=[0.3, 0.3, 0.002], floor_hazard="slip", **pile)
        w.mut(spill)["pos"] = drop_cell(w, room, w.robot.get("pos"), spill)
    w.stat("spills")
    w.emit("spill", target=spill, product=product, liters=round(liters, 3))
    return spill


def _refill_seconds(ctx):
    return ctx.data.get("seconds", 8.0)


@action("refill", params={"target": "entity", "source": "entity", "amount_l": "?number", "funnel": "?entity"},
        duration=_refill_seconds, energy=0.003, category="physical",
        doc="Pour from a held, open source (refill bottle, bag, jar) into an open dispenser/jar (target). Without amount_l it fills to the fill line (liquids) or 95% (bulk). Narrow necks spill without a held funnel; overfilling overflows; the wrong product (or undiluted soap in a foaming pump) is a contamination you cannot undo.")
def refill(ctx):
    w = ctx.w
    dest_id, src_id = ctx.args["target"], ctx.args["source"]
    require(dest_id != src_id, "Pour into a different container", "argument")
    require(ctx.holding(src_id), "Hold the source container", "precondition")
    src = w.ent(src_id)
    dest = ctx.obj(dest_id, touch=True)
    for rec, eid in ((src, src_id), (dest, dest_id)):
        require(rec.get("liquid_container") or rec.get("bulk_store"), "{} cannot hold poured product".format(name_of(ctx.env, eid)), "argument")
        require(not rec.get("openable") or rec.get("open"), "Open the {} first".format(name_of(ctx.env, eid)), "precondition")
    liquid = bool(src.get("liquid_container"))
    require(liquid == bool(dest.get("liquid_container")), "Pour liquids into liquid containers and dry goods into bulk jars", "argument")
    product, have = _contents(src)
    require(have > 1e-6, "The source is empty", "resource")
    fill_to, capacity = _capacity(dest)
    _, current = _contents(dest)
    wanted = ctx.args.get("amount_l")
    if wanted is None:
        wanted = max(0.0, fill_to - current)
    require(wanted > 1e-6, "It is already full", "precondition")
    poured = min(have, float(wanted))
    funnel = ctx.args.get("funnel") or held_with(ctx, "funnel")
    if funnel and not ctx.holding(funnel):
        frec = ctx.obj(funnel, touch=True)
        require("funnel" in frec.get("capabilities", []) or frec.get("funnel"), "That is not a funnel", "argument")
    neck = float(dest.get("neck_mm", 60 if not liquid else 40))
    narrow = neck < (25 if liquid else 60)
    spill_frac = 0.0
    if narrow and not funnel:
        spill_frac = 0.12 if liquid else 0.06
    elif funnel:
        spill_frac = 0.005
    if dest.get("dusty_product") or src.get("dusty_product"):
        spill_frac = max(spill_frac, 0.01 if funnel else 0.03)
    spilled = poured * spill_frac
    into = poured - spilled
    overflow = max(0.0, current + into - capacity)
    into -= overflow
    sm = w.mut(src_id)
    dm = w.mut(dest_id)
    report = {"poured_l": round(poured, 3), "into_l": round(into, 3), "spilled_l": round(spilled + overflow, 3)}
    if liquid:
        sm["liquid_l"] = round(have - poured, 4)
        prior_product, prior = dest.get("liquid"), current
        dm["liquid_l"] = round(current + into, 4)
        tag = dest.get("product_tag")
        if prior > 0.005 and prior_product and prior_product != product:
            dm["liquid"] = "mixed"
            dm["liquid_mix"] = sorted({prior_product, product})
            pair = (src.get("chem_class"), dest.get("chem_class"))
            if pair in CHEM_MIX_GAS:
                w.stat("gas_exposure")
                w.emit("toxic_gas", target=dest_id, mix=list(pair))
        else:
            dm["liquid"] = product
        if tag and product != tag:
            dm["product_mismatch"] = True
            if dest.get("foaming") and product != "foaming_hand_soap" and not src.get("diluted"):
                dm["clogged"] = True
        if sm["liquid_l"] <= 0.0005:
            sm["liquid_l"] = 0.0
            sm["empty"] = True
            sm["waste_stream"] = sm.get("waste_stream") or "recycling"
        if src.get("chem_class"):
            dm["chem_class"] = src["chem_class"] if not dest.get("chem_class") else dest["chem_class"]
    else:
        taken = _take_layers(sm, poured)
        scale = into / poured if poured > 0 else 0.0
        moved = [dict(l, l=round(float(l["l"]) * scale, 4)) for l in taken]
        # the first portion poured lands first: pouring inverts the source's layer order on top of the jar
        _add_layers(dm, moved)
        tag = dest.get("product_tag")
        products = {l.get("product") for l in dm.get("layers") or []}
        if len(products) > 1:
            dm["mixed_products"] = sorted(p for p in products if p)
        if tag and product != tag:
            dm["product_mismatch"] = True
        if src.get("contains_gluten") and dest.get("gluten_free"):
            dm["allergen_contaminated"] = True
        if src.get("_infested"):
            dm["_infested"] = True
        if sm.get("layers") == []:
            sm["empty"] = True
    if src.get("contains_gluten") and dest.get("gluten_free"):
        dm["allergen_contaminated"] = True
    if funnel:
        add_soil(w, funnel, "sticky" if liquid else "food", 0.2)
    total_spill = spilled + overflow
    if liquid:
        # the kernel's spill (5 ml or more counts): what missed the neck, then what ran over the brim
        if spilled > 0:
            _spill(w, dest_id, product, spilled, "liquid", generic=ctx.realistic)
        if overflow > 0:
            _spill(w, dest_id, product, overflow, "liquid", generic=ctx.realistic, cause="overflow")
    elif total_spill > 0.002:
        _spill(w, dest_id, product, total_spill, "granular", generic=ctx.realistic)
    if overflow > 0:
        dm["overflowed"] = True
        add_soil(w, dest_id, "sticky" if liquid else "food", 0.3)
    rate = VISCOUS.get(product, 0.25 if liquid else 0.4)
    # pouring time from the amount to the eye's 50 ml (what the message reports), so the clock is no finer a
    # measure of a hidden level than the message; the flow rate is the product's viscosity, which pouring shows
    ctx.data["seconds"] = 3.0 + approx_litres(poured) / rate + (2.0 if funnel else 0.0)
    w.emit("refilled", target=dest_id, source=src_id, product=product, liters=round(into, 3))
    if ctx.realistic:
        # pouring is a close look at both containers (the stream's colour and thickness, the level rising)
        ctx.grant(src_id, "inspect")
        ctx.grant(dest_id, "inspect")
        # pouring is judged by eye: amounts to about 50 ml, a spill as what it looks like
        report = {"poured_l": approx_litres(poured), "into_l": approx_litres(into), "spilled": spill_words(spilled + overflow)}
    return dumps(ctx.env, report)


# ----------------------------------------------------------------------
# small point-of-use behaviours

@after("open", order=60)
def _tissue_box_opened(ctx, result):
    rec = ctx.w.ent(ctx.args["target"])
    if "tissues" in rec and int(rec.get("tissues", 0)) > 0:
        ctx.w.mut(ctx.args["target"])["tissue_up"] = True


@after("place", order=65)
def _point_of_use(ctx, result):
    w = ctx.w
    eid = ctx.args["target"]
    rec = w.ent(eid)
    parent_id = rec.get("location")
    parent = w.s["entities"].get(parent_id)
    if parent is None:
        if parent_id in w.s["rooms"]:
            _wet_floor(w, eid, parent_id)
        return
    if "napkin_capacity" in parent:
        total = sum(int(w.ent(c).get("napkins", 0)) for c in w.children(parent_id) if w.ent(c).get("relation") == "in")
        pm = w.mut(parent_id)
        pm["napkins"] = total
        pm["overstuffed"] = total > int(parent.get("napkin_capacity", 80))
        pm["dispensable"] = 0 < total <= int(parent.get("napkin_capacity", 80))
        pm["fill"] = round(min(1.5, total / float(parent.get("napkin_capacity", 80))), 3)
    if parent.get("drains") is not None and rec.get("soap_kind"):
        if parent.get("pooled_water") or float((parent.get("soil") or {}).get("soap_scum", 0)) > 0.3:
            w.mut(eid)["soggy"] = True


def _wet_floor(w, eid, room):
    rec = w.ent(eid)
    cell = rec.get("pos")
    wet = [list(c) for c in w.s["rooms"][room].get("wet_cells", [])]
    if cell and list(cell) in wet and rec.get("material") in ("paper", "cardboard", "fabric"):
        m = w.mut(eid)
        m["wetness"] = 1.0
        m["ruined"] = True
        for child in w.descendants(eid):
            c = w.ent(child)
            if c.get("material") in ("paper", "cardboard", "fabric"):
                cm = w.mut(child)
                cm["wetness"] = max(float(cm.get("wetness", 0)), 0.8)
                cm["ruined"] = True


@after("pick_up", order=65)
def _napkins_taken(ctx, result):
    w = ctx.w
    prev = ctx.data.get("admin_prev_loc")
    parent = w.s["entities"].get(prev)
    if parent is not None and "napkin_capacity" in parent:
        total = sum(int(w.ent(c).get("napkins", 0)) for c in w.children(prev) if w.ent(c).get("relation") == "in")
        pm = w.mut(prev)
        pm["napkins"] = total
        pm["overstuffed"] = total > int(parent.get("napkin_capacity", 80))
        pm["dispensable"] = 0 < total <= int(parent.get("napkin_capacity", 80))
        pm["fill"] = round(min(1.5, total / float(parent.get("napkin_capacity", 80))), 3)


def normalize_consumables(w, eid, rec):
    """Empty rolls become recyclable cardboard cores (also used by setup helpers)."""
    if rec.get("part_type") in ("toilet_paper_roll", "paper_towel_roll") and float(rec.get("remaining", 1.0)) <= 0.0 \
            and rec.get("waste_stream") != "recycling":
        m = w.mut(eid)
        m["waste_stream"] = "recycling"
        m["label"] = "empty cardboard core"
        m["material"] = "cardboard"
        m["mass_kg"] = 0.01


# Residents' consumption (opt-in: household.consumption.enabled). Typical US use: ~0.4 rolls of
# toilet paper per person per day across all bathrooms, ~1 paper towel roll per household per week,
# ~5 ml hand soap per person per day, ~8 tissues per day per household.
CONSUMPTION = {"toilet_paper_roll": 0.4, "paper_towel_roll": 0.15, "hand_soap_l": 0.005, "tissues": 8.0}


ROLL_PARTS = ("toilet_paper_roll", "paper_towel_roll")


@reads("part_type")
def _is_roll(record):
    return record.get("part_type") in ROLL_PARTS


@reads("part_type", "dispenser", "liquid_container", "tissues")
def _is_consumable(record):
    """Static predicate: rolls, dispensers and tissue boxes (their levels and placement are dynamic)."""
    return record.get("part_type") in ROLL_PARTS or bool(record.get("dispenser") and record.get("liquid_container")) or \
        "tissues" in record


def normalize_rolls(w):
    """Spent rolls (remaining 0) become recyclable cores; indexed, so cheap to call from a system."""
    ents = w.s["entities"]
    for eid in indexed(w, "rolls", _is_roll):
        rec = ents[eid]
        if float(rec.get("remaining", 1.0)) <= 0.0 and rec.get("waste_stream") != "recycling":
            normalize_consumables(w, eid, rec)


def consumption_tick(w, dt):
    """Residents' use of installed rolls, pumps and tissues; linear in dt, so exact for any period."""
    cfg = w.s.get("household", {}).get("consumption") or {}
    if not cfg.get("enabled"):
        return
    people = max(1, len(w.s.get("people", {})))
    rates = dict(CONSUMPTION)
    rates.update(cfg.get("rates", {}))
    day = dt / 86400.0
    ents = w.s["entities"]
    candidates = indexed(w, "consumables", _is_consumable)
    holders = max(1, sum(1 for e in candidates if ents[e].get("part_type") == "toilet_paper_roll" and ents[e].get("relation") == "part"))
    for eid in candidates:
        rec = ents[eid]
        if rec.get("relation") == "part" and rec.get("part_type") == "toilet_paper_roll" and float(rec.get("remaining", 0)) > 0:
            m = w.mut(eid)
            m["remaining"] = round(max(0.0, float(rec["remaining"]) - rates["toilet_paper_roll"] * people * day / holders), 5)
            normalize_consumables(w, eid, m)
        elif rec.get("relation") == "part" and rec.get("part_type") == "paper_towel_roll" and float(rec.get("remaining", 0)) > 0:
            m = w.mut(eid)
            m["remaining"] = round(max(0.0, float(rec["remaining"]) - rates["paper_towel_roll"] * day), 5)
            normalize_consumables(w, eid, m)
        elif rec.get("dispenser") and rec.get("liquid_container") and float(rec.get("liquid_l", 0)) > 0 and rec.get("in_service", True):
            m = w.mut(eid)
            m["liquid_l"] = round(max(0.0, float(rec["liquid_l"]) - rates["hand_soap_l"] * people * day / 2.0), 5)
        elif rec.get("relation") == "part" and "tissues" in rec and int(rec.get("tissues", 0)) > 0:
            m = w.mut(eid)
            used = rates["tissues"] * day
            m["_tissue_frac"] = float(rec.get("_tissue_frac", 0.0)) + used
            whole = int(m["_tissue_frac"])
            if whole:
                m["tissues"] = max(0, int(rec["tissues"]) - whole)
                m["_tissue_frac"] -= whole
