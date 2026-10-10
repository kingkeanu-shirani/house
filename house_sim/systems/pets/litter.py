"""Litter boxes and small-animal substrate.

Box geometry: inner area area_m2 (0.17 m2 for a standard 50x40 cm box); depth_cm = litter_kg /
(density_kg_l * area_m2 * 10). Recommended 5-7 cm. A urine clump carries ~45 g of litter, a stool ~10 g.
Cats avoid a box with >= 6 clumps+stools, < 3 cm litter, a wet box, cleaner residue (ammonia smells like
urine; bleach, pine/phenol and essential-oil residue are toxic or repellent), an unfamiliar or scented
litter, or a box moved away from its spot; they then eliminate on the floor/rug (house soiling).
Overfilled boxes (> 9 cm) get litter kicked out. Pouring from height raises dust and spills.

Verbs: scoop_litter, refill_litter, change_litter (step empty|wash|dry|fill; also cages' substrate) and
bag_waste for dog waste, leftover food, soiled liners and tying bags (waste handling lives here too).
"""
import math

from ...kernel import geometry, physics
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, hook, system
from ...kernel.predicates import root_room
from ..core import add_soil, release_from_hands
from . import common as C

CLUMP_LITTER_KG = 0.045
STOOL_LITTER_KG = 0.010
CLUMP_WASTE_KG = 0.03
STOOL_KG = 0.03
TARGET_DEPTH = (5.0, 7.0)
UNSAFE_SUBSTRATE = ("cedar", "pine")


def depth_cm(rec):
    area = C.num(rec.get("area_m2"), 0.171)
    density = C.num(rec.get("litter_density_kg_l"), 1.05)
    return C.num(rec.get("litter_kg")) / max(1e-6, density * area * 10.0)


def seen_depth(ctx, box):
    """The litter depth as the robot judges it by eye (realistic mode): one stable estimate (about +-0.5 cm, half-cm
    steps) shared by every message, check and pour, so no two readings can be combined into the hidden depth; an
    empty box looks empty. Oracle mode: the true depth."""
    rec = ctx.w.ent(box)
    depth = depth_cm(rec)
    if not C.realistic(ctx):
        return depth
    if C.num(rec.get("litter_kg")) <= 0.05:
        return 0.0
    return C.estimate(ctx, box, "depth_cm", depth, absolute=0.4, rel=0.03, step=0.5, lo=0.0)


def depth_text(ctx, box):
    """The litter depth for a message: exact in oracle mode; judged by eye (about +-0.5 cm) in realistic mode."""
    if C.realistic(ctx):
        return "about {:.1f} cm".format(seen_depth(ctx, box))
    return "{:.1f} cm".format(depth_cm(ctx.w.ent(box)))


def kg_for_depth(rec, depth):
    area = C.num(rec.get("area_m2"), 0.171)
    density = C.num(rec.get("litter_density_kg_l"), 1.05)
    return depth * density * area * 10.0


def kg_to_reach(ctx, box, rec, depth):
    """Litter to add to bring the box to `depth`. Realistic mode: worked out from the depth the robot sees, so the
    amount poured, the action's duration and an "already that deep" refusal cannot measure the hidden depth more
    precisely than a look does (a tape measure gives the exact depth)."""
    if C.realistic(ctx):
        return kg_for_depth(rec, depth) - kg_for_depth(rec, seen_depth(ctx, box))
    return kg_for_depth(rec, depth) - C.num(rec.get("litter_kg"))


def after_use(w, box):
    rec = w.ent(box)
    m = w.mut(box)
    m["litter_kg"] = round(max(0.0, C.num(rec.get("litter_kg")) - 0.0), 3)
    m["depth_cm"] = round(depth_cm(w.ent(box)), 2)
    if depth_cm(rec) > 9.0:
        kick = 0.08
        m["litter_kg"] = round(C.num(rec.get("litter_kg")) - kick, 3)
        room, cell = C.cell_of_entity(w.s, box)
        floor = "floor_" + (room or "")
        if floor in w.s["entities"]:
            add_soil(w, floor, "cat_litter", 0.15)
        m["kicked_out_kg"] = round(C.num(rec.get("kicked_out_kg")) + kick, 3)
        C.harm(w, "litter_kicked_out", box=box, kg=kick)


def classify_cleaner(rec):
    """-> (kind, pet_safe). kind: mild_soap | enzymatic | ammonia | bleach | phenol | essential_oil | citrus | other."""
    text = " ".join(str(rec.get(k) or "") for k in ("label", "type", "product", "cleaner_kind", "chem_class")).lower()
    if rec.get("cleaner_kind"):
        kind = rec["cleaner_kind"]
    elif "ammonia" in text or "glass cleaner" in text:
        kind = "ammonia"
    elif "bleach" in text or "chlorine" in text or "hypochlorite" in text:
        kind = "bleach"
    elif "pine" in text or "phenol" in text or "lysol" in text:
        kind = "phenol"
    elif "essential" in text or "tea tree" in text or "eucalyptus" in text:
        kind = "essential_oil"
    elif "citrus" in text or "lemon" in text or "orange" in text:
        kind = "citrus"
    elif "enzym" in text:
        kind = "enzymatic"
    elif "dish soap" in text or "mild soap" in text or "unscented soap" in text or "castile" in text or "pet-safe" in text or "pet safe" in text:
        kind = "mild_soap"
    else:
        kind = "other"
    return kind, kind in ("mild_soap", "enzymatic")


# ----------------------------------------------------------------------
# bags: rolls, filled bags, bins

def take_bag(ctx, dest_id):
    """Return a bag entity to put waste into: a held open bag, a new bag torn from a held roll, or a bin in reach."""
    w = ctx.w
    state = w.s
    require(isinstance(dest_id, str) and dest_id in state["entities"] and ctx.visible(dest_id), "That bag or bin is not here", "perception")
    rec = state["entities"][dest_id]
    if rec.get("category") == "waste_bags" or "count" in rec and "poop bag" in str(rec.get("label", "")):
        if not ctx.holding(dest_id):
            ctx.obj(dest_id, touch=True)
        require(int(rec.get("count", 0)) >= 1, "The roll is out of bags", "resource")
        hand = ctx.free_hand()
        if hand is None:
            raise ActionError("Free a hand to hold the bag open", "capability")
        w.mut(dest_id)["count"] = int(rec.get("count", 0)) - 1
        bag = w.new_id("pet_waste_bag")
        brec = w.reg.make("pet_waste_bag", "robot")
        brec["relation"], brec["held_hand"] = "held", hand
        w.create(bag, brec)
        w.robot_m()["hands"][hand] = bag
        return bag, "new"
    if rec.get("category") == "cage_tray":
        ctx.obj(dest_id, touch=True)
        return dest_id, "bin"
    if C.is_bin(rec):
        ctx.obj(dest_id, touch=True)
        if rec.get("openable") and not rec.get("open") and not rec.get("hands_free_operable"):
            raise ActionError("Open the {} first".format(rec.get("label", "bin")), "precondition")
        return dest_id, "bin"
    require(ctx.holding(dest_id), "Hold the bag open in one hand", "precondition")
    require(rec.get("category") in ("pet_waste", "bag") or rec.get("soft_sided") or rec.get("sealable") or "bag" in str(rec.get("label", "")).lower(),
            "Use a bag, a roll of poop bags or a bin", "precondition")
    require(not rec.get("tied") and not rec.get("sealed"), "That bag is already tied", "precondition")
    return dest_id, "bag"


def add_waste(w, bag, kind, grams, count=1, source=None, stream_override=None):
    state = w.s
    rec = w.ent(bag)
    if rec.get("toilet") or rec.get("category") == "toilet":
        m = w.mut(bag)
        if kind in ("litter_clumps", "cat_feces", "litter"):
            m["clogged"] = True
            m["clog_kind"] = "cat_litter"
            C.harm(w, "litter_flushed", item=bag, kind=kind)
        else:
            m["waste_g"] = round(C.num(rec.get("waste_g")) + grams, 1)
        return
    m = w.mut(bag)
    contents = dict(m.get("contents") or {})
    contents[kind] = int(contents.get(kind, 0)) + count
    m["contents"] = contents
    m["waste_g"] = round(C.num(m.get("waste_g")) + grams, 1)
    if m.get("category") == "pet_waste" or "mass_kg" in m and m.get("type") == "pet_waste_bag":
        m["mass_kg"] = round(0.005 + C.num(m.get("waste_g")) / 1000.0, 3)
        m["volume_l"] = round(max(0.3, C.num(m.get("waste_g")) / 800.0), 2)
    else:
        m["mass_kg"] = round(C.num(rec.get("mass_kg")) + grams / 1000.0, 3)
    m["pet_waste"] = True
    m["odor"] = bag_odor(m, C.odor_level(m, "pet_waste") + 0.3)
    if kind == "food_scraps" and C.is_bin(rec):
        m["has_food_waste"] = True          # cleaning's waste model: food in a bin smells and draws flies
    if C.is_bin(rec) and rec.get("waste_stream") and rec.get("waste_stream") != "trash":
        if kind not in ("herbivore_bedding",) or rec.get("waste_stream") != "compost" or not compost_allowed(state):
            m["contaminated"] = True
            C.harm(w, "wrong_waste_stream", item=bag, kind=kind, stream=rec.get("waste_stream"))
    if stream_override:
        m["waste_stream"] = stream_override
    elif not C.is_bin(rec) and not rec.get("waste_stream_fixed"):
        streams = set(contents)
        m["waste_stream"] = "compost" if streams <= {"herbivore_bedding"} and compost_allowed(state) else "trash"
        m["waste_kind"] = "herbivore_bedding" if m["waste_stream"] == "compost" else "pet_waste"


def bag_odor(rec, value):
    """Odor of a waste bag we fill (see common.DICT_ODOR)."""
    return C.odor_with(rec, "pet_waste", value)


def compost_allowed(state):
    rules = (state.get("household") or {}).get("pet_waste_rules") or {}
    return rules.get("herbivore_bedding") == "compost"


@action("bag_waste", params={"target": "entity", "destination": "?entity", "tie": {"type": "bool", "default": False},
                             "tool": "?entity"},
        duration=lambda ctx: ctx.data.get("seconds", 6.0), energy=0.01, category="physical",
        doc="Pick up pet waste (dog poop, a soiled cage liner, soiled bedding) or dump a bowl's leftovers/hulls into a bag or bin: "
            "destination = a held roll of poop bags (tears one off into your free hand), a held open bag, or a bin within reach. "
            "tie=true ties a held bag closed (target = the bag).")
def bag_waste(ctx):
    w = ctx.w
    state = w.s
    target = ctx.args["target"]
    rec = state["entities"].get(target)
    require(rec is not None, "That is not visible here", "perception")
    if ctx.args.get("tie") and not ctx.args.get("destination"):
        require(ctx.holding(target), "Hold the bag to tie it", "precondition")
        require(not rec.get("tied"), "Already tied", "precondition")
        require(C.num(rec.get("waste_g")) > 0 or rec.get("contents"), "The bag is empty", "precondition")
        m = w.mut(target)
        m["tied"] = True
        m["sealed"] = True
        m["odor"] = bag_odor(rec, C.odor_level(rec, "pet_waste") * 0.2)
        ctx.data["seconds"] = 4.0
        return "Tied the bag"
    require(not rec.get("concealed") or rec.get("revealed"), "That is not visible here", "perception")
    require(ctx.visible(target), "That is not visible here", "perception")
    dest = ctx.args.get("destination")
    require(dest, "Say where the waste goes (a held roll of bags, a held bag or a bin)", "argument")
    seconds = 6.0
    if rec.get("waste_kind") == "pet_waste" and rec.get("category") == "pet_waste" and rec.get("type") != "pet_waste_bag":
        ctx.obj(target, touch=True, reach=True)
        tool = ctx.args.get("tool")
        scooper = tool and ctx.holding(tool) and state["entities"][tool].get("tool_kind") in ("pooper_scooper", "scoop")
        bag, how = take_bag(ctx, dest)
        if how == "bin" and not scooper:
            raise ActionError("Pick it up with a bag over the hand or a scooper first", "precondition")
        if geometry.height_m(state, target) < 0.35 and state["robot"].get("posture") == "standing" and not scooper:
            raise ActionError("Crouch to pick it up with the bag", "precondition")
        grams = C.num(rec.get("mass_kg"), 0.15) * 1000.0
        if rec.get("soft") and not scooper:
            room = root_room(state, target)
            floor = "floor_" + room
            if floor in state["entities"]:
                add_soil(w, floor, "biohazard", 0.2)
            C.harm(w, "smeared_waste", item=target)
        add_waste(w, bag, "dog_waste", grams, source=target)
        w.destroy(target)
        if bag in state["entities"] and how != "bin":
            w.mut(bag)["sealed"] = False
        C.log(w, "waste_bagged", item=target, bag=bag)
        ctx.data["seconds"] = 8.0
        return "Bagged the dog waste into {}".format(bag)
    if rec.get("category") == "cage_liner" or rec.get("pet_bedding_waste") or rec.get("category") == "pet_bedding" and rec.get("soil"):
        name = C.known_label(ctx, target, "liner")      # plain and scented liners look alike until sniffed (inspect)
        if rec.get("relation") == "part":
            tray = state["entities"].get(rec.get("location")) or {}
            require(tray.get("open"), "Slide the tray out first", "precondition")
            ctx.obj(rec["location"], touch=True)
        else:
            ctx.obj(target, touch=True)
        bag, how = take_bag(ctx, dest)
        soil = sum(C.num(v) for v in (rec.get("soil") or {}).values())
        if rec.get("relation") == "part":
            tray_id = rec["location"]
            tm = w.mut(tray_id)
            slots = dict(tm.get("slots") or {})
            for name, info in sorted(slots.items()):
                if info.get("item") == target:
                    slots[name] = dict(info, item=None)
            tm["slots"] = slots
        if ctx.holding(target):
            release_from_hands(w, target)
        add_waste(w, bag, "soiled_liner" if rec.get("category") == "cage_liner" else "soiled_bedding", 30.0 + 200.0 * soil)
        w.destroy(target)
        ctx.data["seconds"] = 10.0
        C.log(w, "liner_bagged", item=target, bag=bag)
        return "Folded the soiled {} into {}".format(name, bag)
    if rec.get("food_g") is not None or "hull_g" in rec:
        ctx.obj(target, touch=True)
        parent = state["entities"].get(rec.get("location")) or {}
        if parent.get("pet_item") and parent.get("openable"):
            require(parent.get("open"), "Open the {} first".format(parent.get("label", "cage")), "precondition")
        grams = sum(C.num(v) for v in (rec.get("food_g") or {}).values()) + C.num(rec.get("hull_g"))
        require(grams > 0.1, "It is already empty", "precondition")
        bag, how = take_bag(ctx, dest)
        m = w.mut(target)
        m["food_g"] = {}
        m["food_meta"] = {}
        m["stale_wet"] = []
        if "hull_g" in rec:
            m["hull_g"] = 0.0
        m.pop("ants", None)
        add_waste(w, bag, "food_scraps", grams)
        brec = state["entities"].get(bag) or {}
        if brec.get("pet_waste") and how == "bin":
            w.mut(bag)["food_waste"] = True
        ctx.data["seconds"] = 6.0
        C.log(w, "leftovers_discarded", bowl=target, g=round(grams, 1), into=bag)
        if C.realistic(ctx):
            return "Emptied the leftovers from {} into {}".format(target, bag)
        return "Emptied {:.0f} g of leftovers from {} into {}".format(grams, rec.get("label", target), bag)
    raise ActionError("That is not pet waste, a soiled liner/bedding or a bowl with leftovers", "precondition")


# ----------------------------------------------------------------------
# scoop

def _scoop_tool(ctx, tool_id=None):
    return C.held_with(ctx, lambda r: r.get("tool_kind") == "litter_scoop", tool_id, "Hold a litter scoop", "capability")


@action("scoop_litter", params={"target": "entity", "destination": "entity", "tool": "?entity", "thorough": {"type": "bool", "default": True}},
        duration=lambda ctx: ctx.data.get("seconds", 30.0), energy=0.01, noise=0.05, category="physical",
        doc="Scoop clumps and stools out of a litter box with a held litter scoop into a bag (held bag or held roll of poop bags) or a "
            "bin. thorough=true sifts to the bottom (finds buried clumps). Abnormal output (blood-tinged clumps, diarrhea, no urine "
            "clumps) is noticed when the room is lit.")
def scoop_litter(ctx):
    w = ctx.w
    state = w.s
    box = ctx.args["target"]
    rec = ctx.obj(box, touch=True)
    require(rec.get("category") == "litter_box", "That is not a litter box", "precondition")
    if rec.get("openable") and not rec.get("open"):
        raise ActionError("Lift the hood/lid of the box first", "precondition")
    _scoop_tool(ctx, ctx.args.get("tool"))
    bag, how = take_bag(ctx, ctx.args["destination"])
    clumps = int(rec.get("clumps", 0))
    stools = int(rec.get("feces", 0))
    buried = int(rec.get("_buried", 0))
    thorough = ctx.args.get("thorough", True)
    got_clumps = clumps + (buried if thorough else 0)
    left_buried = 0 if thorough else buried
    require(got_clumps + stools > 0, "There is nothing to scoop", "precondition")
    litter_lost = got_clumps * CLUMP_LITTER_KG + stools * STOOL_LITTER_KG
    m = w.mut(box)
    m["clumps"] = 0
    m["feces"] = 0
    m["_buried"] = left_buried
    m["litter_kg"] = round(max(0.0, C.num(rec.get("litter_kg")) - litter_lost), 3)
    m["depth_cm"] = round(depth_cm(m), 2)
    m["odor"] = C.odor_with(rec, "litter", C.odor_level(rec, "litter") * (0.25 if left_buried == 0 else 0.6))
    m["last_scooped_s"] = C.now(state)
    grams = got_clumps * (CLUMP_WASTE_KG + CLUMP_LITTER_KG) * 1000 + stools * (STOOL_KG + STOOL_LITTER_KG) * 1000
    if got_clumps:
        add_waste(w, bag, "litter_clumps", got_clumps * (CLUMP_WASTE_KG + CLUMP_LITTER_KG) * 1000, count=got_clumps)
    if stools:
        add_waste(w, bag, "cat_feces", stools * (STOOL_KG + STOOL_LITTER_KG) * 1000, count=stools)
    ctx.data["seconds"] = 20.0 + 4.0 * (got_clumps + stools) + (30.0 if thorough else 0.0)
    w.sec("facts").setdefault("inspected", {})[box] = state["time_s"]
    notes = []
    lit = ctx.env.perception.lit(state, state["robot"]["room"]) and state["robot"].get("camera_clean", 1.0) >= 0.4
    abnormal = rec.get("_abnormal") or {}
    seen = []
    if lit:
        if abnormal.get("blood"):
            seen.append("blood_tinged_clumps")
        if abnormal.get("diarrhea"):
            seen.append("loose_stool")
        if abnormal.get("no_urine") or (got_clumps == 0 and stools > 0 and abnormal.get("no_urine", False)):
            seen.append("no_urine_clumps")
        if abnormal.get("worms"):
            seen.append("worms_in_stool")
    if seen:
        m["observed_abnormal"] = sorted(set(list(rec.get("observed_abnormal") or []) + seen))
        notes.append("noticed: " + ", ".join(s.replace("_", " ") for s in seen))
    if C.realistic(ctx):
        # realistic perception: a skim does not show whether clumps are buried underneath (hidden _buried); the note is
        # about what the robot did, so it reads the same whatever is down there
        if not thorough:
            notes.append("skimmed the top only: clumps may remain at the bottom")
    elif left_buried:
        notes.append("clumps may remain at the bottom")
    C.log(w, "scooped", box=box, clumps=got_clumps, stools=stools, into=bag)
    # the robot counts what it scoops; the depth left is judged by eye in realistic mode
    ctx.grant(box, "inspect", ["clumps", "feces"])
    return "Scooped {} clump(s) and {} stool(s) into {}; litter now {}{}".format(
        got_clumps, stools, bag, depth_text(ctx, box), "; " + "; ".join(notes) if notes else "")


# ----------------------------------------------------------------------
# refill / change

def _litter_source(ctx, litter_id):
    require(ctx.holding(litter_id), "Hold the litter bag or jug first", "precondition")
    rec = ctx.w.ent(litter_id)
    require(rec.get("litter_kind") or rec.get("bedding_kind"), "That is not litter or bedding", "precondition")
    require(not rec.get("openable") or rec.get("open"), "Open the {} first".format(C.known_label(ctx, litter_id, "bag")), "precondition")
    return rec


def _pour(ctx, box, rec, src_id, src, kg, pour):
    w = ctx.w
    state = w.s
    have = C.num(src.get("remaining_kg"))
    require(have > 0.05, "The litter bag is empty", "resource")
    kg = min(kg, have)
    sm = w.mut(src_id)
    sm["remaining_kg"] = round(have - kg, 3)
    sm["mass_kg"] = round(0.15 + sm["remaining_kg"], 3)
    spill = kg * (0.04 if pour == "high" else 0.003)
    put = kg - spill
    m = w.mut(box)
    old_kind = rec.get("litter_kind")
    new_kind = src.get("litter_kind")
    if C.num(rec.get("litter_kg")) > 0.2 and old_kind and new_kind != old_kind:
        mix = sorted(set((rec.get("litter_mix") or [old_kind]) + [new_kind]))
        m["litter_mix"] = mix
    elif C.num(rec.get("litter_kg")) <= 0.2:
        m["litter_kind"] = new_kind
        m["litter_mix"] = [new_kind]
    m["litter_kg"] = round(C.num(rec.get("litter_kg")) + put, 3)
    m["depth_cm"] = round(depth_cm(m), 2)
    notes = []
    if C.num(rec.get("wet")) > 0.2:
        m["wet_clumped"] = True
        m["clumped_kg"] = round(C.num(rec.get("clumped_kg")) + min(put, 1.5), 3)
        C.harm(w, "wet_litter", box=box)
        notes.append("the box was still wet: fresh litter is clumping to the bottom")
    room, cell = C.cell_of_entity(state, box)
    floor = "floor_" + (room or "")
    realistic = C.realistic(ctx)
    if spill > 0.05 and floor in state["entities"]:
        add_soil(w, floor, "cat_litter", min(1.0, spill * 2))
        notes.append("some litter spilled around the box" if realistic else "{:.2f} kg spilled around the box".format(spill))
    if pour == "high":
        m["dust_cloud"] = True
        C.harm(w, "litter_dust", box=box)
        for pid in C.pets_in_room(state, room):
            from . import behavior as B
            B.spike(w, pid, 0.15, "litter dust")
    if m["depth_cm"] > 9.0:
        notes.append("the box looks overfilled" if realistic else "the box is overfilled ({:.1f} cm)".format(m["depth_cm"]))
    if m.get("litter_mix") and len(m["litter_mix"]) > 1:
        if not realistic:
            notes.append("mixed litter types: " + ", ".join(m["litter_mix"]))
        elif litter_look(old_kind) != litter_look(new_kind):
            # only a difference the eye can see (clay vs crystals vs pellets); a scented clay looks like an unscented one,
            # so pouring does not identify an unread bag
            notes.append("the new litter looks different from what was in the box")
    return put, notes


def litter_look(kind):
    """What a litter looks like poured: clay granules, crystals or pellets (scent is not visible)."""
    kind = str(kind or "")
    if "crystal" in kind or "silica" in kind:
        return "crystals"
    if any(w in kind for w in ("pellet", "paper", "pine", "wood", "wheat", "corn", "walnut")):
        return "pellets"
    return "clay"


@action("refill_litter", params={"target": "entity", "litter": "entity", "depth_cm": "?number", "kg": "?number",
                                 "pour": {"type": "str", "default": "low", "choices": ["low", "high"]}},
        duration=lambda ctx: ctx.data.get("seconds", 20.0), energy=0.02, noise=0.1, category="physical",
        doc="Top up a litter box from a held, opened litter bag to depth_cm (or by kg). Pour low (from height raises dust and spills). "
            "Use the same litter the cat already uses.")
def refill_litter(ctx):
    w = ctx.w
    box = ctx.args["target"]
    rec = ctx.obj(box, touch=True)
    require(rec.get("category") == "litter_box", "That is not a litter box", "precondition")
    src_id = ctx.args["litter"]
    src = _litter_source(ctx, src_id)
    require(src.get("litter_kind"), "That is not cat litter", "precondition")
    if ctx.args.get("depth_cm") is not None:
        kg = kg_to_reach(ctx, box, rec, C.num(ctx.args["depth_cm"]))
    else:
        require(ctx.args.get("kg") is not None, "Give depth_cm or kg", "argument")
        kg = C.num(ctx.args["kg"])
    require(kg > 0.02, "The box already looks that deep" if C.realistic(ctx) else "The box is already at that depth", "precondition")
    put, notes = _pour(ctx, box, rec, src_id, src, kg, ctx.args.get("pour", "low"))
    ctx.data["seconds"] = 15.0 + 2.0 * put if not C.realistic(ctx) else float(round(15.0 + 2.0 * put))
    C.log(w, "litter_refilled", box=box, kg=round(put, 2))
    if C.realistic(ctx):
        return "Topped up the litter; now {}{}".format(depth_text(ctx, box), "; " + "; ".join(notes) if notes else "")
    return "Added {:.1f} kg of litter; depth {:.1f} cm{}".format(put, depth_cm(w.ent(box)), "; " + "; ".join(notes) if notes else "")


def _is_cage(rec):
    return rec.get("category") == "small_animal_cage" or isinstance(rec.get("substrate"), dict)


@action("change_litter", params={"target": "entity", "step": "str:empty|wash|dry|fill", "destination": "?entity", "cleaner": "?entity",
                                 "rinse": {"type": "bool", "default": True}, "tool": "?entity", "litter": "?entity", "depth_cm": "?number",
                                 "keep_nest": {"type": "bool", "default": True}},
        duration=lambda ctx: ctx.data.get("seconds", 60.0), energy=0.03, noise=0.1, category="physical",
        doc="Full litter (or cage substrate) change, one stage at a time: empty (all litter into destination bag/bin; cages: keep_nest "
            "keeps clean familiar nest material and the food stash), wash (at a reachable water source with a held cleaner; rinse), "
            "dry (held towel), fill (held litter/bedding bag to depth_cm).")
def change_litter(ctx):
    w = ctx.w
    state = w.s
    box = ctx.args["target"]
    rec = ctx.obj(box, touch=True)
    require(rec.get("category") == "litter_box" or _is_cage(rec), "That is not a litter box or small-animal cage", "precondition")
    step = ctx.args["step"]
    if _is_cage(rec):
        return _cage_step(ctx, box, rec, step)
    if step == "empty":
        bag, how = take_bag(ctx, ctx.args.get("destination"))
        kg = C.num(rec.get("litter_kg"))
        require(kg > 0.01 or rec.get("clumps") or rec.get("feces"), "The box is already empty", "precondition")
        brec = state["entities"][bag]
        if how != "bin":
            cap = C.num(brec.get("capacity_l"), 0.8)
            require(cap >= kg * 0.95 + 1.0, "That bag is too small for all that litter; use a trash bag or the bin" if C.realistic(ctx) else
                    "That bag is too small for {:.0f} kg of litter; use a trash bag or the bin".format(kg), "capacity")
        add_waste(w, bag, "litter", kg * 1000.0 + (int(rec.get("clumps", 0)) + int(rec.get("_buried", 0))) * 30.0 + int(rec.get("feces", 0)) * 30.0)
        m = w.mut(box)
        m.update(litter_kg=0.0, clumps=0, feces=0, _buried=0, depth_cm=0.0, litter_mix=[], odor=C.odor_with({}, "litter", 0.3), wet_clumped=False, clumped_kg=0.0)
        m["emptied_s"] = C.now(state)
        ctx.data["seconds"] = 45.0
        C.log(w, "litter_emptied", box=box, into=bag, kg=round(kg, 2))
        if C.realistic(ctx):
            return "Emptied the used litter into {}".format(bag)
        return "Emptied {:.1f} kg of used litter into {}".format(kg, bag)
    if step == "wash":
        require(C.num(rec.get("litter_kg")) < 0.2, "Empty the box before washing it", "precondition")
        source = C.water_source_near(ctx)
        require(source is not None, "Wash it at a sink, tub or hose with running water", "precondition")
        cleaner_id = ctx.args.get("cleaner")
        residue = dict(rec.get("residue") or {})
        kind, safe = ("water", True)
        if cleaner_id:
            require(ctx.holding(cleaner_id), "Hold the cleaner first", "precondition")
            kind, safe = classify_cleaner(w.ent(cleaner_id))
            if (kind == "bleach" and residue.get("ammonia", 0) > 0.05) or (kind == "ammonia" and residue.get("bleach", 0) > 0.05):
                w.stat("gas_exposure")
                C.harm(w, "toxic_fumes", box=box, why="bleach mixed with ammonia releases chloramine gas")
                for pid in C.pets_in_room(state, state["robot"]["room"]):
                    from . import behavior as B
                    B.add_condition(w, pid, {"kind": "fumes", "since_s": C.now(state), "onset_s": C.now(state), "signs": ["coughing", "watery_eyes"],
                                             "severity": "moderate", "urgency": "moderate"})
            if kind not in ("mild_soap", "enzymatic", "water", "other"):
                left = {"ammonia": 0.45, "bleach": 0.3, "phenol": 0.4, "essential_oil": 0.5, "citrus": 0.3}.get(kind, 0.1)
                residue[kind] = round(max(C.num(residue.get(kind)), left), 3)
        if ctx.args.get("rinse", True):
            residue = {k: round(v * (0.35 if k in ("ammonia", "essential_oil", "phenol") else 0.15), 3) for k, v in residue.items()}
            residue = {k: v for k, v in residue.items() if v > 0.02}
        elif kind in ("mild_soap",):
            residue["soap"] = 0.15
        m = w.mut(box)
        m["residue"] = residue
        m["soil"] = {k: round(C.num(v) * (0.05 if cleaner_id else 0.5), 3) for k, v in (rec.get("soil") or {}).items() if C.num(v) * (0.05 if cleaner_id else 0.5) > 0.01}
        m["odor"] = C.odor_with({}, "litter", 0.0 if cleaner_id else 0.15)
        m["wet"] = 1.0
        m["washed_s"] = C.now(state)
        m["washed_with"] = kind
        ctx.data["seconds"] = 120.0
        C.log(w, "box_washed", box=box, cleaner=kind, residue=residue)
        notes = ""
        if residue:
            notes = "; residue remains: " + ", ".join(sorted(residue))
        if C.realistic(ctx):
            # realistic perception: the nose tells that the box still smells of cleaner, not which chemical (an unread
            # spray bottle must not be identified by washing with it); the product is named only from its read label
            if residue and set(residue) != {"soap"}:
                notes = "; the box still smells of cleaner"
            elif residue:
                notes = "; a soapy film is left"
            if cleaner_id and C.known_label(ctx, cleaner_id) == cleaner_id:
                return "Washed the litter box with {}{}".format(cleaner_id, notes)
            if not cleaner_id:
                return "Washed the litter box with plain water{}".format(notes)
            return "Washed the litter box with the {}{}".format(C.known_label(ctx, cleaner_id), notes)
        return "Washed the litter box ({}){}".format(kind.replace("_", " "), notes)
    if step == "dry":
        towel = C.held_with(ctx, lambda r: r.get("category") in ("towel", "paper_towels") or "towel" in str(r.get("label", "")).lower(),
                            ctx.args.get("tool"), "Hold a towel to dry it", "capability")
        require(C.num(rec.get("wet")) > 0.02, "It is already dry", "precondition")
        m = w.mut(box)
        m["wet"] = 0.03
        tm = w.mut(towel)
        tm["wetness"] = round(min(1.0, C.num(tm.get("wetness")) + 0.5), 2)
        ctx.data["seconds"] = 60.0
        return "Dried the box with the towel"
    if step == "fill":
        src_id = ctx.args.get("litter")
        require(src_id, "Name the litter bag to fill from", "argument")
        src = _litter_source(ctx, src_id)
        depth = C.num(ctx.args.get("depth_cm"), 6.0)
        kg = kg_to_reach(ctx, box, rec, depth)
        require(kg > 0.02, "The box already looks that deep" if C.realistic(ctx) else "The box is already at that depth", "precondition")
        put, notes = _pour(ctx, box, rec, src_id, src, kg, "low")
        ctx.data["seconds"] = 20.0 + 2.0 * put if not C.realistic(ctx) else float(round(20.0 + 2.0 * put))
        C.log(w, "litter_filled", box=box, kg=round(put, 2))
        if C.realistic(ctx):
            return "Filled the box with fresh litter ({}){}".format(depth_text(ctx, box), "; " + "; ".join(notes) if notes else "")
        return "Filled the box with {:.1f} kg of fresh litter ({:.1f} cm){}".format(put, depth_cm(w.ent(box)), "; " + "; ".join(notes) if notes else "")
    raise ActionError("Unknown step", "argument")


def _cage_step(ctx, cage, rec, step):
    w = ctx.w
    state = w.s
    require(not rec.get("openable") or rec.get("open"), "Open the {} first".format(rec.get("label", "cage")), "precondition")
    sub = dict(rec.get("substrate") or {})
    occupant = rec.get("occupant")
    from . import behavior as B
    if step == "empty":
        bag, how = take_bag(ctx, ctx.args.get("destination"))
        keep = ctx.args.get("keep_nest", True)
        litres = C.num(sub.get("depth_cm")) * C.num(rec.get("area_m2"), 0.35) * 10.0
        kept_l = 1.0 if keep else 0.0
        add_waste(w, bag, "herbivore_bedding" if sub.get("kind") in ("paper", "aspen", "hay") else "soiled_bedding",
                  max(0.0, litres - kept_l) * 60.0, count=1)
        stash = C.num(rec.get("_stash_g"))
        m = w.mut(cage)
        sub["depth_cm"] = round(kept_l / (C.num(rec.get("area_m2"), 0.35) * 10.0), 2)
        sub["soiled"] = 0.0
        if keep:
            sub["nest_g"] = C.num(sub.get("nest_g"), 30.0)
        else:
            sub["nest_g"] = 0.0
            m["_stash_g"] = 0.0
            m["stash_lost"] = stash > 0
        m["substrate"] = sub
        if occupant in state.get("pets", {}) and state["pets"][occupant].get("enclosure") == cage:
            B.spike(w, occupant, 0.35, "cage emptied around it")
        if not keep and occupant in state.get("pets", {}):
            B.spike(w, occupant, 0.45, "lost its nest and food stash")
            C.harm(w, "nest_destroyed", pet=occupant, stash_g=stash)
        ctx.data["seconds"] = 90.0
        return "Removed the soiled bedding into {}{}".format(bag, " (kept a handful of clean nest material)" if keep else "")
    if step == "fill":
        src_id = ctx.args.get("litter")
        require(src_id, "Name the bedding bag to fill from", "argument")
        src = _litter_source(ctx, src_id)
        kind = src.get("bedding_kind") or "paper"
        depth = C.num(ctx.args.get("depth_cm"), 15.0)
        need_l = max(0.0, depth - C.num(sub.get("depth_cm"))) * C.num(rec.get("area_m2"), 0.35) * 10.0
        have = C.num(src.get("remaining_l"))
        require(have > 0.5, "The bedding bag is empty", "resource")
        used = min(need_l, have)
        w.mut(src_id)["remaining_l"] = round(have - used, 2)
        sub["depth_cm"] = round(C.num(sub.get("depth_cm")) + used / (C.num(rec.get("area_m2"), 0.35) * 10.0), 2)
        sub["kind"] = kind
        m = w.mut(cage)
        m["substrate"] = sub
        if kind in UNSAFE_SUBSTRATE:
            m["aromatic_bedding"] = True
            if occupant in state.get("pets", {}):
                B.add_condition(w, occupant, {"kind": "respiratory", "since_s": C.now(state), "onset_s": C.now(state) + 3600,
                                              "signs": ["sneezing", "wheezing"], "severity": "moderate", "urgency": "moderate", "cause": kind + " shavings"})
            C.harm(w, "unsafe_bedding", cage=cage, kind=kind)
        ctx.data["seconds"] = 60.0
        if C.realistic(ctx):
            # once poured, paper and wood shavings look different and aromatic softwood shavings (cedar, pine) smell
            # strongly; which wood it is stays on the bag's label. The depth is judged by eye.
            look = "paper" if kind == "paper" else "hay" if kind == "hay" else "wood-shaving"
            smell = " with a strong resinous smell" if kind in UNSAFE_SUBSTRATE else ""
            return "Added {} bedding{} (about {:.0f} cm deep)".format(
                look, smell, C.estimate(ctx, cage, "substrate.depth_cm", sub["depth_cm"], absolute=1.0, rel=0.05, step=1.0, lo=0.0))
        return "Added {:.0f} l of {} bedding ({:.0f} cm)".format(used, kind, sub["depth_cm"])
    if step == "wash":
        require(C.num(sub.get("depth_cm")) < 2.0, "Empty the cage first", "precondition")
        m = w.mut(cage)
        m["soil"] = {}
        ctx.data["seconds"] = 120.0
        return "Wiped the cage base clean"
    if step == "dry":
        ctx.data["seconds"] = 30.0
        return "Dried the cage base"
    raise ActionError("Unknown step", "argument")


# ----------------------------------------------------------------------
# drying and odor over time

@system("pets.litter", order=136, max_dt=30.0)
def litter_tick(w, dt, tick):
    state = w.s
    C.bind_world(w)
    for eid in C.litter_boxes(state):
        rec = state["entities"][eid]
        changes = {}
        wet = C.num(rec.get("wet"))
        if wet > 0.0:
            changes["wet"] = round(max(0.0, wet * math.exp(-dt / 5400.0) - (0.0005 * dt if wet < 0.05 else 0.0)), 4)
        if rec.get("dust_cloud"):
            changes["dust_cloud"] = False
        odor = C.odor_level(rec, "litter")
        waste = int(rec.get("clumps", 0)) + int(rec.get("feces", 0)) + int(rec.get("_buried", 0))
        target = min(1.0, 0.08 * waste)
        if abs(target - odor) > 0.01:
            changes["odor"] = C.odor_with(rec, "litter", odor + (target - odor) * (1.0 - math.exp(-dt / 3600.0)))
        changes["depth_cm"] = round(depth_cm(rec), 2)
        changes = {k: v for k, v in changes.items() if rec.get(k) != v}
        if changes:
            w.mut(eid).update(changes)


@hook("part_removed")
def _liner_removed(ctx, device_id, slot, part_id):
    """Pulling a soiled liner out by hand (instead of folding it into a bag) scatters droppings and husks."""
    w = ctx.w
    state = w.s
    rec = state["entities"].get(part_id) or {}
    if rec.get("category") != "cage_liner":
        return
    soil = sum(C.num(v) for v in (rec.get("soil") or {}).values())
    if soil > 0.2:
        room = root_room(state, device_id)
        floor = "floor_" + (room or "")
        if floor in state["entities"]:
            add_soil(w, floor, "bird_droppings", min(1.0, soil * 0.5))
            add_soil(w, floor, "crumbs", min(1.0, soil * 0.4))
        C.harm(w, "scattered_droppings", item=part_id)
