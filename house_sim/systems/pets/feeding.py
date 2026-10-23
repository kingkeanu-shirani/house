"""Feeding: measured portions into bowls (scale / level or heaped scoop / can fraction / eyeballing), meal-slot
bookkeeping against the household schedule (double feeding, extra meals, free-fed allotments), the pet-care
log, treats (limits, diet rules, toxic 'treats', trading for a held item), wet-food time-out and spoilage,
opened-can storage, automatic feeders, and raiding of reachable food.

Accuracy model (fidelity P-approx): scale +/-1 %, level scoop +/-3 % of scoop_ml x bulk density,
heaped scoop +30 % +/-5 %, can fraction +/-3 %, eyeballing +/-30 %.
"""
import json

from ...kernel import geometry, physics
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, hook, system
from ...kernel.predicates import root_room
from . import behavior as B
from . import common as C
from . import model as M

WET_TIMEOUT_S = 30 * 60       # uneaten wet food should be picked up after ~30 min
WET_SPOIL_S = 4 * 3600        # 4 h at room temperature (FDA two-hour rule for warm rooms; 4 h at ~21 C)
FRIDGE_KEEP_S = 3 * 86400


# ----------------------------------------------------------------------
# helpers

def is_bowl(rec):
    return rec.get("bowl_kind") == "food" or "food_g" in rec and rec.get("pet_item")


def food_props(rec):
    """(kind, wet, density g/ml, kcal/g) of a food source entity."""
    pf = rec.get("pet_food")
    if isinstance(pf, dict):
        return pf.get("kind", "pet_food"), bool(pf.get("wet")), C.num(pf.get("density_g_per_ml"), 0.45), C.num(pf.get("kcal_per_g"), 3.6)
    food = rec.get("food")
    if isinstance(food, dict):
        return str(food.get("kind", rec.get("label", "food"))), bool(food.get("moist") or food.get("phase") == "liquid"), 0.6, 2.5
    return str(rec.get("label", "food")).replace(" ", "_"), False, 0.5, 2.5


def available_g(rec):
    if "remaining_g" in rec:
        return C.num(rec.get("remaining_g"))
    food = rec.get("food")
    if isinstance(food, dict):
        if food.get("qty_g") is not None:
            return C.num(food.get("qty_g"))
        if food.get("unit") == "g":
            return C.num(food.get("quantity"))
    return C.num(rec.get("mass_kg"), 0.1) * 1000.0


def consume_source(w, eid, grams):
    rec = w.ent(eid)
    if "remaining_g" in rec:
        m = w.mut(eid)
        m["remaining_g"] = round(max(0.0, C.num(rec.get("remaining_g")) - grams), 2)
        base = C.num(rec.get("_empty_kg"), 0.1)
        m["mass_kg"] = round(base + m["remaining_g"] / 1000.0, 3)
        return
    w.destroy(eid)


def find_log(w, ctx=None):
    """The household pet-care log the robot can write now: one within reach, or an app log over the network."""
    state = w.s
    for eid in sorted(state["entities"]):
        rec = state["entities"][eid]
        if rec.get("log_kind") not in ("pet_care", "feeding"):
            continue
        if rec.get("app") and state["robot"].get("network", True):
            return eid
        if ctx is not None and root_room(state, eid) == state["robot"]["room"] and ctx.visible(eid):
            return eid
    return None


def write_log(w, log_id, pid, slot, text, kind="meal"):
    rec = w.ent(log_id)
    m = w.mut(log_id)
    entries = list(rec.get("entries") or [])
    stamp = _clock_str(w.s)
    entries.append({"time": stamp, "pet": pid, "slot": slot, "what": text, "by": "robot", "kind": kind})
    m["entries"] = entries
    logged = list(rec.get("logged") or [])
    key = "{}:{}".format(pid, slot)
    if key not in logged:
        logged.append(key)
    m["logged"] = logged
    m["text"] = render_log(w.s, entries)


def render_log(state, entries):
    lines = ["Pet care log:"]
    for e in entries[-30:]:
        # a pet by name, a tank or other entry subject by its label (never an internal id in printed text)
        pet = state.get("pets", {}).get(e.get("pet")) or state.get("entities", {}).get(e.get("pet")) or {}
        lines.append("{} - {} - {}{} ({})".format(e.get("time"), pet.get("label", e.get("pet")), e.get("what"),
                                                    " [{}]".format(e.get("slot")) if e.get("slot") else "", e.get("by", "?")))
    return "\n".join(lines)


def _clock_str(state):
    from ...kernel import clock
    return clock.now(state).strftime("%Y-%m-%d %H:%M")


def scale_near(ctx):
    """The scale a portion is weighed on: held, or in the robot's room where the robot can see it now or saw it earlier
    this episode. Weighing happens at the scale and the portion is carried to the bowl (the 12 s of a weighed serving),
    so the scale need not be in view from the bowl; with the geometric camera a scale on the counter is often hidden
    from a bowl on the floor (realistic mode: one the robot never saw is not used)."""
    w = ctx.w
    state = w.s
    for eid in sorted(state["entities"]):
        rec = state["entities"][eid]
        if rec.get("tool_kind") in ("kitchen_scale", "scale", "postal_scale") or "weigh" in (rec.get("affordances") or []) and rec.get("capacity_g"):
            if ctx.holding(eid):
                return eid
            if root_room(state, eid) == state["robot"]["room"] and (ctx.visible(eid) or (
                    ctx.camera_mode == "geometric" and C.shown_before(ctx, eid) and not C.enclosed(state, eid))):
                return eid
    return None


def scoop_held(ctx):
    for eid in ctx.w.held_items():
        rec = ctx.w.ent(eid)
        if rec.get("scoop_ml"):
            return eid
    return None


def measured_grams(ctx, source_rec, amount, unit, measure, density):
    """Dispensed grams for the requested amount and method (with the method's real error)."""
    rng = ctx.rng
    if unit == "scoop":
        scoop = scoop_held(ctx)
        require(scoop is not None, "Hold a measuring scoop to measure by the scoop", "capability")
        ml = C.num(ctx.w.ent(scoop).get("scoop_ml"), 237.0)
        if measure == "heaped":
            factor = 1.30 + rng.uniform(-0.05, 0.05)
        else:
            factor = 1.0 + rng.uniform(-0.03, 0.03)
        return amount * ml * density * factor, "{} {} scoop(s)".format(amount, "heaped" if measure == "heaped" else "level")
    if unit == "can":
        net = C.num(source_rec.get("net_g"), C.num(source_rec.get("remaining_g")))
        return amount * net * (1.0 + rng.uniform(-0.03, 0.03)), "{} can".format(amount)
    if unit == "g":
        if measure == "scale":
            require(scale_near(ctx) is not None, "No kitchen scale within reach to weigh the food", "capability")
            return amount * (1.0 + rng.uniform(-0.01, 0.01)), "{:.0f} g on the scale".format(amount)
        return amount * (1.0 + rng.uniform(-0.3, 0.3)), "about {:.0f} g by eye".format(amount)
    if unit == "whole":
        return available_g(source_rec), "all of it"
    raise ActionError("unit must be g, scoop, can or whole", "argument")


def bowl_volume_ml(rec):
    total = 0.0
    for kind, grams in (rec.get("food_g") or {}).items():
        meta = (rec.get("food_meta") or {}).get(kind) or {}
        total += C.num(grams) / max(0.2, C.num(meta.get("density"), 0.45))
    total += C.num(rec.get("hull_g")) / 0.3
    return total


def spill_food(w, room, near, kind, grams, wet, owner=None):
    if room not in w.s["rooms"] or grams <= 0.5:
        return None
    eid = w.new_id("spilled_pet_food")
    # named by its look (a renal diet spilled on the floor looks like any kibble); which food it is stays in the hidden
    # food records (per-entity tiers) -- a pile on the floor carries no label
    rec = w.reg.make("generic_item", room, label="spilled {}".format(C.food_look(kind, {"wet": wet})), category="food",
                     food={"kind": kind, "state": "raw", "quantity": round(grams, 1), "unit": "g", "qty_g": round(grams, 1)},
                     pet_food={"kind": kind, "species": "any", "wet": wet}, remaining_g=round(grams, 1), mass_kg=round(grams / 1000.0, 3),
                     size_m=[0.3, 0.2, 0.01], volume_l=0.05, soil={"crumbs": 0.5}, spilled=True, pet_appeal={"dog": 0.9, "cat": 0.3},
                     _perception={"hidden": ["food", "pet_food"]})
    # (no derived_from: the kernel would show the pile as its package -- "bag of dry dog food" -- and the food domain
    # drops derived_from on portions of packages without a food record anyway)
    rec["pos"] = C.free_cell_near(w.s, room, near)
    w.create(eid, rec)
    C.log(w, "food_spilled", item=eid, grams=round(grams, 1))
    return eid


# ----------------------------------------------------------------------
# meal bookkeeping

def account_serving(w, pid, kind, grams, wet, ctx=None):
    """Attribute a serving to a pet's current meal slot; flags double feeding and wrong diets."""
    state = w.s
    M.ensure_pet(w, pid)
    pet = state["pets"][pid]
    m = w.pet_m(pid)
    served = dict(m.get("served_g") or {})
    served[kind] = round(C.num(served.get(kind)) + grams, 2)
    m["served_g"] = served
    diet = pet.get("diet") or {}
    note = None
    if diet.get("mode") == "free":
        m["free_served_g"] = round(C.num(m.get("free_served_g")) + grams, 2)
        allot = C.num((pet.get("_diet") or {}).get("daily_g"), M.portion_g(pet) * 2)
        already = C.num(pet.get("free_fed_today_g"))
        if already + C.num(m["free_served_g"]) > allot * 1.1:
            m["over_allotment"] = True
            C.harm(w, "overfed", pet=pid, why="above the daily allotment")
            note = "that is more than {}'s daily allotment".format(pet.get("label", pid))
    else:
        key, slot, _start = M.current_slot(state, pet)
        last = pet.get("_last_serving") or {}
        same_meal = last.get("key") == key and C.now(state) - C.num(last.get("t"), -1e9) < 900
        if key and not same_meal:
            count = M.slot_count(state, pet, key)
            given = dict(m.get("meals_given") or {})
            given[key] = count + 1
            m["meals_given"] = given
            if count >= 1:
                m["double_fed"] = True
                m["extra_meals"] = int(m.get("extra_meals", 0)) + 1
                C.harm(w, "double_fed", pet=pid, slot=key)
                note = "{} had already been fed for {}".format(pet.get("label", pid), slot)
        m["_last_serving"] = {"key": key, "t": C.now(state)}
    if kind not in M.allowed_foods(pet):
        m["wrong_diet_served"] = int(m.get("wrong_diet_served", 0)) + 1
        C.harm(w, "wrong_diet", pet=pid, kind=kind)
    if C.species(pet) == "cat":
        m["_meal_left_g"] = round(min(400.0, C.num(m.get("_meal_left_g")) + grams * 0.8), 2)
        m["hunger"] = max(C.num(m.get("hunger")), 0.6)
    return note


# ----------------------------------------------------------------------
# feed

def _feed_seconds(ctx):
    return ctx.data.get("seconds", 8.0)


@action("feed", params={"target": "entity", "food": "entity", "amount": "?number", "unit": "?str:g|scoop|can|whole",
                        "measure": "?str:scale|level|heaped|eyeball", "log": {"type": "bool", "default": True}},
        duration=_feed_seconds, energy=0.01, noise=0.05, category="physical",
        doc="Put a measured amount of food from a container (bag/bin/can/seed bag; held or within reach, opened) into a pet bowl, "
            "seed cup or feeder hopper. unit g (measure scale|eyeball), scoop (needs a held scoop; measure level|heaped), can (fraction of "
            "a wet-food can) or whole. Counts toward the bowl owner's current meal slot and writes the pet-care log when one is within "
            "reach (log=false skips it).")
def feed(ctx):
    w = ctx.w
    state = w.s
    bowl_id, food_id = ctx.args["target"], ctx.args["food"]
    bowl = ctx.obj(bowl_id, touch=True)
    require(is_bowl(bowl) or "hopper_g" in bowl, "That is not a pet bowl, seed cup or feeder", "precondition")
    parent = state["entities"].get(bowl.get("location")) or {}
    if parent.get("pet_item") and parent.get("openable"):
        require(parent.get("open"), "Open the {} to reach the {}".format(parent.get("label", "cage"), bowl.get("label", "dish")), "precondition")
    require(food_id != bowl_id, "Choose the food to serve", "argument")
    src = state["entities"].get(food_id)
    require(src is not None and ctx.visible(food_id), "That food is not visible here", "perception")
    if not ctx.holding(food_id):
        ctx.obj(food_id, touch=True)
    require(src.get("pet_food") or src.get("food") or C.is_edible(src), "That is not food", "precondition")
    if src.get("openable") and not src.get("open"):
        raise ActionError("Open the {} first".format(C.known_label(ctx, food_id, "container")), "precondition")
    kind, wet, density, kcal = food_props(src)
    unit = ctx.args.get("unit") or ("can" if wet and "net_g" in src or src.get("type", "").endswith("food_can") else
                                    "scoop" if scoop_held(ctx) else "g")
    measure = ctx.args.get("measure") or ("scale" if unit == "g" and scale_near(ctx) else "level" if unit == "scoop" else "eyeball")
    amount = ctx.args.get("amount")
    if unit != "whole":
        require(amount is not None and amount > 0, "Say how much (amount with unit g, scoop or can)", "argument")
    grams, how = measured_grams(ctx, src, C.num(amount, 1.0), unit, measure, density)
    have = available_g(src)
    require(have > 0.5, "The {} is empty".format(C.known_label(ctx, food_id, "container")), "resource")
    grams = min(grams, have)
    seconds = 6.0 + (4.0 * C.num(amount) if unit == "scoop" else 12.0 if measure == "scale" else 5.0)
    realistic = C.realistic(ctx)
    # realistic perception: the robot knows the grams only from a scale reading; otherwise what it did (scoops, a can,
    # by eye) and what the food looks like (a renal diet looks like any kibble)
    exact = not realistic or (unit == "g" and measure == "scale")
    what = kind.replace("_", " ") if not realistic else C.food_look(kind, {"wet": wet})
    if "hopper_g" in bowl:
        m = w.mut(bowl_id)
        m["hopper_g"] = round(C.num(bowl.get("hopper_g")) + grams, 1)
        m["hopper_kind"] = kind
        consume_source(w, food_id, grams)
        ctx.data["seconds"] = seconds
        if not exact:
            return "Topped up the feeder hopper with {} of {}".format(how, what)
        return "Topped up the feeder hopper with {:.0f} g of {}".format(grams, what)
    capacity_ml = C.num(bowl.get("bowl_capacity_l"), 0.9) * 1000.0
    room_ml = max(0.0, capacity_ml - bowl_volume_ml(bowl))
    fits_g = room_ml * density
    spilled = max(0.0, grams - fits_g)
    put = grams - spilled
    m = w.mut(bowl_id)
    foods = dict(m.get("food_g") or {})
    foods[kind] = round(C.num(foods.get(kind)) + put, 2)
    m["food_g"] = foods
    meta = dict(m.get("food_meta") or {})
    meta[kind] = {"wet": wet, "served_s": C.now(state), "density": density, "kcal_per_g": kcal, "for": bowl.get("owner")}
    m["food_meta"] = meta
    m["last_served"] = {"kind": kind, "g": round(put, 1), "t": C.now(state), "how": how}
    consume_source(w, food_id, grams)
    notes = []
    if spilled > 0.5:
        room, cell = C.cell_of_entity(state, bowl_id)
        spill_food(w, room, cell, kind, spilled, wet)
        w.stat("spills")
        notes.append("{:.0f} g overflowed onto the floor".format(spilled) if exact else "food overflowed onto the floor")
    owner = bowl.get("owner")
    if owner in state.get("pets", {}):
        note = account_serving(w, owner, kind, put, wet, ctx)
        if note and not realistic:
            # realistic perception: earlier meals and the daily allotment are learned from the log or by asking
            notes.append(note)
        if ctx.args.get("log", True):
            log_id = find_log(w, ctx)
            if log_id:
                key, slot, _s = M.current_slot(state, state["pets"][owner])
                # the robot logs what it knows it served
                write_log(w, log_id, owner, slot if (state["pets"][owner].get("diet") or {}).get("mode") != "free" else "top-up",
                          "{:.0f} g {}".format(put, what) if exact else "{} {}".format(how, what))
            else:
                notes.append("no pet-care log within reach to record it")
    ctx.data["seconds"] = seconds
    C.log(w, "served", bowl=bowl_id, kind=kind, g=round(put, 1), how=how, owner=owner)
    if not exact:
        return "Served {} of {} into {}{}".format(how, what, bowl_id, "; " + "; ".join(notes) if notes else "")
    return "Served {:.0f} g of {} ({}) into {}{}".format(put, what, how, bowl_id, "; " + "; ".join(notes) if notes else "")


# ----------------------------------------------------------------------
# treats

def ingest_given(w, pid, item_id, grams=None, what=None):
    """A pet eats (part of) an item the robot gave it. Toxins take effect."""
    state = w.s
    rec = state["entities"][item_id]
    pet = state["pets"][pid]
    tox = C.toxic_to(rec, pet)
    kind, wet, _d, kcal = food_props(rec)
    grams = available_g(rec) if grams is None else grams
    B.record_intake(w, pid, {kind: round(grams, 2)}, source="robot")
    m = w.pet_m(pid)
    ingested = list(m.get("ingested") or [])
    ingested.append({"item": item_id, "label": what or rec.get("label", item_id), "toxin": tox[0] if tox else None, "t": round(C.now(state), 1)})
    m["ingested"] = ingested
    if tox:
        info = C.TOXINS.get(tox[0], {})
        B.add_condition(w, pid, {"kind": "toxin", "toxin": tox[0], "severity": tox[1], "since_s": C.now(state),
                                 "onset_s": C.now(state) + C.num(info.get("onset_s"), 3600), "signs": list(info.get("signs", [])),
                                 "evidence": rec.get("label")})
        m["toxic_ingestions"] = int(m.get("toxic_ingestions", 0)) + 1
        C.harm(w, "poisoned", robot=True, pet=pid, item=item_id, toxin=tox[0])     # the robot handed it over
    return kind, kcal * grams, tox


def held_by_pet(state, pid):
    return sorted(e for e, r in state["entities"].items() if r.get("location") == pid and r.get("relation") in ("held", "mouth"))


@action("give_treat", params={"target": "pet", "treat": "entity", "count": {"type": "int", "default": 1, "min": 1, "max": 20},
                              "trade": {"type": "bool", "default": False}, "log": {"type": "bool", "default": True}},
        duration=lambda ctx: 3.0 + 1.5 * ctx.args.get("count", 1), energy=0.005, category="physical",
        doc="Hand a pet treats from a held, opened treat jar/pouch (count) or a held food item. trade=true offers it in exchange "
            "for something the pet holds (it drops the item unless it is guarding hard). Counts against the pet's daily treat "
            "limit and diet rules; toxic 'treats' poison.")
def give_treat(ctx):
    w = ctx.w
    state = w.s
    pid = ctx.args["target"]
    pet = C.require_pet(ctx, pid)
    M.ensure_pet(w, pid)
    pet = state["pets"][pid]
    treat = ctx.args["treat"]
    require(ctx.holding(treat), "Hold the treats first", "precondition")
    rec = w.ent(treat)
    require(rec.get("pet_food") or rec.get("food") or C.is_edible(rec), "That is not something to feed a pet", "precondition")
    require(C.is_awake(pet), "{} is asleep".format(pet.get("label", pid)), "social")
    require(C.num(pet.get("anxiety")) < 0.9, "{} is too frightened to take food".format(pet.get("label", pid)), "social")
    count = int(ctx.args.get("count", 1))
    notes = []
    # realistic perception: what the jar is for, whether a food is toxic and what the diet allows are learned from the
    # label, general knowledge or the owner -- the action does not announce them (the consequences still happen)
    realistic = C.realistic(ctx)
    what = C.known_label(ctx, treat, "treat")
    if "count" in rec and (rec.get("pet_food") or {}).get("treat"):
        require(not rec.get("openable") or rec.get("open"), "Open the {} first".format(C.known_label(ctx, treat, "jar")), "precondition")
        require(int(rec.get("count", 0)) >= count, "Not enough treats left", "resource")
        species_ok = rec.get("treat_species") in ("any", C.species(pet))
        if not species_ok and not realistic:
            notes.append("{} treats are not made for a {}".format(rec.get("treat_species"), C.species(pet)))
        grams = C.num(rec.get("g_per_treat"), 4.0) * count
        kcal = C.num(rec.get("kcal_per_treat"), 10.0) * count
        kind = rec.get("treat_kind", "treat")
        jar = w.mut(treat)
        jar["count"] = int(rec.get("count", 0)) - count
        B.record_intake(w, pid, {kind: round(grams, 2)}, source="treat")
        tox = None
    else:
        require(count == 1, "Give a single food item at a time", "argument")
        release_hand = ctx.hand_of(treat)
        kind, kcal, tox = ingest_given(w, pid, treat)
        from ..core import release_from_hands
        release_from_hands(w, treat)
        w.destroy(treat)
        if tox and not realistic:
            notes.append("{} is toxic to {}s".format(rec.get("label", "that"), C.species(pet)))
    m = w.pet_m(pid)
    m["treats_today"] = int(m.get("treats_today", 0)) + count
    m["treat_kcal"] = round(C.num(m.get("treat_kcal")) + kcal, 1)
    log = list(m.get("treat_log") or [])
    log.append({"t": round(C.now(state), 1), "kind": kind, "count": count})
    m["treat_log"] = log
    diet = pet.get("diet") or {}
    if diet.get("no_treats"):
        C.harm(w, "diet_violation", pet=pid, why="treats are not allowed on this diet")
        if not realistic:
            notes.append("{}'s diet does not allow treats".format(pet.get("label", pid)))
    limit = int(diet.get("treat_limit", 99))
    if int(m["treats_today"]) > limit:
        m["treat_over_limit"] = True
        C.harm(w, "treat_over_limit", pet=pid, count=m["treats_today"], limit=limit)
    m["activity"] = "eating"
    m["excitement"] = round(min(1.0, C.num(m.get("excitement")) + 0.1), 3)
    dropped = None
    if ctx.args.get("trade"):
        holding = held_by_pet(state, pid)
        if holding:
            item = holding[0]
            guard = C.num(pet.get("_guard_intensity"), C.behavior(pet, "resource_guarding"))
            if guard >= 0.8:
                B.spike(w, pid, 0.2, "guarding")
                w.pet_m(pid)["mood"] = "guarding"
                notes.append("{} ate the treat but stiffened and kept the {} (guarding)".format(pet.get("label", pid), C.known_label(ctx, item, "item")))
            else:
                from . import play
                play.pet_drops(w, pid, item)
                dropped = item
                notes.append("{} dropped the {}".format(pet.get("label", pid), C.known_label(ctx, item, "item")))
    # the treat is named by what the robot can tell it is (a jar's kind only from its read label)
    named = kind.replace("_", " ")
    if realistic:
        jar = "count" in rec and (rec.get("pet_food") or {}).get("treat")
        if not jar:
            named = what
        elif C.realism_of(ctx).knowledge.level(treat, rec, "treat_kind") < 3:      # below READ
            named = "treat(s)"
    if ctx.args.get("log", True):
        log_id = find_log(w, ctx)
        if log_id:
            write_log(w, log_id, pid, "treat", "{} x {}".format(count, named), kind="treat")
    C.log(w, "treat", pet=pid, kind=kind, count=count, traded=dropped)
    return "Gave {} {} {}{}".format(pet.get("label", pid), count, named, "; " + "; ".join(notes) if notes else "")


# ----------------------------------------------------------------------
# wet food, opened cans, spoilage, automatic feeders

@after("open", order=70)
def _can_opened(ctx, result):
    w = ctx.w
    eid = ctx.args.get("target")
    rec = w.s["entities"].get(eid) or {}
    if (rec.get("pet_food") or {}).get("wet") and not rec.get("opened"):
        m = w.mut(eid)
        m["opened"] = True
        m["opened_s"] = C.now(w.s)
        m.setdefault("net_g", C.num(rec.get("remaining_g")))
        m["perishable"] = True
        m["storage"] = "fridge"


@after("place", order=70)
def _covering(ctx, result):
    """A can lid placed on an opened can covers it; taking it off uncovers."""
    w = ctx.w
    state = w.s
    eid = ctx.args.get("target")
    rec = state["entities"].get(eid) or {}
    if rec.get("can_cover"):
        parent = state["entities"].get(rec.get("location")) or {}
        if (parent.get("pet_food") or {}).get("wet"):
            w.mut(rec["location"])["covered"] = True


@after("pick_up", order=70)
def _uncovering(ctx, result):
    w = ctx.w
    state = w.s
    eid = ctx.args.get("target")
    rec = state["entities"].get(eid) or {}
    if rec.get("can_cover"):
        lids = C.sindex(state, "can_lids", lambda r: bool(r.get("can_cover")))
        for can in C.sindex(state, "food", _food_candidate):
            crec = state["entities"][can]
            if crec.get("covered") and (crec.get("pet_food") or {}).get("wet") and not C.located_in(state, lids, can):
                w.mut(can)["covered"] = False


def _cold(state, eid):
    rec = state["entities"][eid]
    parent = state["entities"].get(rec.get("location")) or {}
    if parent.get("internal_temp_c") is not None and C.num(parent.get("internal_temp_c")) <= 7.0:
        return True
    return C.num(rec.get("temperature_c"), 21.0) <= 7.0


def _food_candidate(rec):
    """Static: wet pet food (cans/pouches), pet bowls and automatic feeders."""
    pf = rec.get("pet_food")
    return bool(isinstance(pf, dict) and pf.get("wet") or rec.get("bowl_kind") or rec.get("pet_item") or "hopper_g" in rec)


def _feeder_due(state, rec):
    """True when an automatic feeder has a dispensing time due now that it has not served yet (as _feeder decides)."""
    done = list(rec.get("_dispensed") or [])
    today = C.date_key(state)
    for t in (rec.get("settings") or {}).get("times") or []:
        key = "{}:{}".format(today, t)
        if key in done or C.hm_to_h(t) > C.hour(state) or C.hour(state) - C.hm_to_h(t) > 0.25:
            continue
        return True
    return False


def food_idle(w):
    """Exact idle probe of pets.food (kernel v3.1): no opened wet food spoiling, every bowl's stale-wet list current
    and no ants due, no powered feeder with a dispensing time due."""
    state = w.s
    now = C.now(state)
    for eid in C.sindex(state, "food", _food_candidate):
        rec = state["entities"][eid]
        pf = rec.get("pet_food")
        if isinstance(pf, dict) and pf.get("wet") and rec.get("opened") and not rec.get("spoiled"):
            return False
        if is_bowl(rec) and rec.get("food_g"):
            meta = rec.get("food_meta") or {}
            stale = sorted(k for k, v in meta.items() if v.get("wet") and now - C.num(v.get("served_s"), now) >= WET_TIMEOUT_S)
            if stale != list(rec.get("stale_wet") or []):
                return False
            if stale and not rec.get("ants") and now - min(C.num(meta[k].get("served_s"), now) for k in stale) >= 2 * 3600:
                return False
        if "hopper_g" in rec and rec.get("on") and physics.is_powered(w, eid) and _feeder_due(state, rec):
            return False
    return True


@system("pets.food", order=135, max_dt=30.0, idle=food_idle)
def food_tick(w, dt, tick):
    state = w.s
    now = C.now(state)
    C.bind_world(w)
    for eid in C.sindex(state, "food", _food_candidate):
        rec = state["entities"][eid]
        pf = rec.get("pet_food")
        if isinstance(pf, dict) and pf.get("wet") and rec.get("opened") and not rec.get("spoiled"):
            cold = _cold(state, eid)
            key = "_cold_s" if cold else "_warm_s"
            m = w.mut(eid)
            m[key] = round(C.num(rec.get(key)) + dt, 1)
            if C.num(m.get("_warm_s")) >= WET_SPOIL_S or C.num(m.get("_cold_s")) >= FRIDGE_KEEP_S or \
                    (not rec.get("covered") and C.num(m.get("_cold_s")) >= 86400):
                m["spoiled"] = True
                m["pet_food"] = dict(pf, spoiled=True)
                C.log(w, "wet_food_spoiled", item=eid)
        if is_bowl(rec) and rec.get("food_g"):
            meta = rec.get("food_meta") or {}
            stale = sorted(k for k, v in meta.items() if v.get("wet") and now - C.num(v.get("served_s"), now) >= WET_TIMEOUT_S)
            if stale != list(rec.get("stale_wet") or []):
                w.mut(eid)["stale_wet"] = stale
            if stale and not rec.get("ants") and now - min(C.num(meta[k].get("served_s"), now) for k in stale) >= 2 * 3600:
                w.mut(eid)["ants"] = True
                C.log(w, "ants_at_bowl", bowl=eid)
        if "hopper_g" in rec and rec.get("on") and physics.is_powered(w, eid):
            _feeder(w, eid, rec)


def _feeder(w, eid, rec):
    state = w.s
    from ...kernel import clock
    hhmm = clock.now(state).strftime("%H:%M")
    done = list(rec.get("_dispensed") or [])
    today = C.date_key(state)
    settings = rec.get("settings") or {}
    for t in settings.get("times") or []:
        key = "{}:{}".format(today, t)
        if key in done or C.hm_to_h(t) > C.hour(state) or C.hour(state) - C.hm_to_h(t) > 0.25:
            continue
        portion = min(C.num(settings.get("portion_g"), 30.0), C.num(rec.get("hopper_g")))
        m = w.mut(eid)
        done.append(key)
        m["_dispensed"] = done[-10:]
        if portion <= 0.5:
            m["display"] = "HOPPER EMPTY"
            C.log(w, "feeder_empty", feeder=eid)
            continue
        kind = rec.get("hopper_kind", "cat_kibble")
        m["hopper_g"] = round(C.num(rec.get("hopper_g")) - portion, 1)
        foods = dict(m.get("food_g") or {})
        foods[kind] = round(C.num(foods.get(kind)) + portion, 2)
        m["food_g"] = foods
        meta = dict(m.get("food_meta") or {})
        meta[kind] = {"wet": False, "served_s": C.now(state), "density": 0.45, "for": rec.get("owner")}
        m["food_meta"] = meta
        m["display"] = "Dispensed {:.0f} g at {}; hopper {:.0f} g".format(portion, t, m["hopper_g"])
        if rec.get("owner") in state.get("pets", {}):
            account_serving(w, rec["owner"], kind, portion, False)
        C.log(w, "feeder_dispensed", feeder=eid, g=portion)


# ----------------------------------------------------------------------
# raiding reachable food containers (dogs tear bags, eat spilled kibble)

def raid(w, pid, eid):
    """Called by the explorer for pet-food containers: eat until stuffed (~3 portions)."""
    state = w.s
    rec = state["entities"][eid]
    pet = state["pets"][pid]
    kind, wet, _d, _k = food_props(rec)
    grams = min(available_g(rec), M.portion_g(pet) * 3.0)
    if grams <= 0.5:
        return
    B.record_intake(w, pid, {kind: round(grams, 1)}, source=eid)
    if "remaining_g" in rec:
        m = w.mut(eid)
        m["remaining_g"] = round(C.num(rec.get("remaining_g")) - grams, 1)
        if rec.get("openable"):
            m["open"] = True
            m["torn"] = True
            m["damage"] = C.damage_with(m, "torn_open", 0.6)
    else:
        w.destroy(eid)
    pm = w.pet_m(pid)
    pm["raided_g"] = round(C.num(pm.get("raided_g")) + grams, 1)
    if grams > M.portion_g(pet) * 1.5:
        B.add_condition(w, pid, {"kind": "overeating", "since_s": C.now(state), "onset_s": C.now(state) + 1800,
                                 "signs": ["bloated", "lethargy"], "severity": "moderate", "urgency": "moderate"})
    C.harm(w, "food_raid", pet=pid, item=eid, grams=round(grams, 1))
