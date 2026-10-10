"""Grooming, examining and calming pets.

brush_pet: hair removal depends on brush vs coat (deshedding/slicker suit double/long coats, bristle/rubber
curry suit short coats); against-the-grain strokes and firm slicker work on short coats hurt; brushing over a
hot spot, wound or mat causes pain. Each minute raises stress by (1 - handling_tolerance) * 0.08; a pet
already showing stress signs swats/snaps. ~20 % of loosened hair falls to the floor.
wipe_paws: pet-safe wipes / damp towel remove ~92 % of mud per paw, a dry towel ~50 %; household
disinfecting wipes leave residue the dog licks off. A loose excited dog pulls away after two paws.
bathe_pet: 35-39 C water; > 41 C scalds, < 25 C chills; dog shampoo vs human/dish soap; water in the ears.
dry_pet: towel or pet dryer (high heat close up burns; 80 dB stresses noise-phobic pets).
check_pet: hands-on/visual examination of a pet or its equipment (signs, gait, parasites with a flea comb,
coat, paws, gear fit, aquarium water, enclosure temperatures with an IR thermometer, bottle nozzle, eggs).
calm_pet: presence, den, pressure wrap, stay/wait cue, recall, drop-it, redirect; coaxing a hiding pet out
by force is a forced interaction.
"""
import math

from ...kernel import geometry, physics
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import system
from ...kernel.predicates import root_room
from ..core import add_soil, release_from_hands
from . import behavior as B
from . import common as C
from . import model as M

BRUSH_EFFECT = {  # brush_kind -> {coat_type: fraction of loose hair removed per minute}
    "deshedding": {"double": 0.4, "long": 0.25, "medium": 0.3, "short": 0.15},
    "slicker": {"double": 0.3, "long": 0.35, "medium": 0.25, "short": 0.1},
    "bristle": {"double": 0.1, "long": 0.12, "medium": 0.2, "short": 0.25},
    "rubber_curry": {"double": 0.12, "long": 0.08, "medium": 0.25, "short": 0.35},
}
PAWS = ("fl", "fr", "bl", "br")


def _stress_step(w, pid, amount, why):
    B.spike(w, pid, amount, why)


def _reaction(w, pid, ctx):
    """A pet past its threshold swats (cats) or snaps (dogs)."""
    state = w.s
    pet = state["pets"][pid]
    anx = C.num(pet.get("anxiety"))
    if anx < 0.75:
        return None
    m = w.pet_m(pid)
    if C.species(pet) == "cat":
        m["scratches"] = int(m.get("scratches", 0)) + 1
        C.harm(w, "scratch", pet=pid)
        return "{} swatted and scratched".format(pet.get("label", pid))
    m["bites"] = int(m.get("bites", 0)) + 1
    C.harm(w, "bite", pet=pid)
    return "{} snapped at you".format(pet.get("label", pid))


@action("brush_pet", params={"target": "pet", "tool": "?entity", "direction": {"type": "str", "default": "with_grain", "choices": ["with_grain", "against_grain"]},
                             "pressure": {"type": "str", "default": "gentle", "choices": ["gentle", "firm"]},
                             "area": {"type": "str", "default": "body", "choices": ["body", "back", "sides", "legs", "belly", "tail", "full"]},
                             "minutes": {"type": "number", "default": 2, "min": 0.5, "max": 20}},
        duration=lambda ctx: 60.0 * C.num(ctx.args.get("minutes"), 2.0), energy=0.02, category="physical",
        doc="Brush a calm pet with a held grooming brush for some minutes (with the grain, gentle). Stop at the first stress signs "
            "(check its mood between short sessions). Avoid hot spots, wounds and mats (check_pet aspect=coat first).")
def brush_pet(ctx):
    w = ctx.w
    state = w.s
    pid = ctx.args["target"]
    pet = C.require_pet(ctx, pid)
    M.ensure_pet(w, pid)
    pet = state["pets"][pid]
    tool = C.held_with(ctx, lambda r: r.get("tool_kind") == "pet_brush" or r.get("brush_kind"), ctx.args.get("tool"),
                       "Hold a grooming brush", "capability")
    require(C.is_awake(pet), "{} is asleep".format(pet.get("label", pid)), "social")
    brush = w.ent(tool)
    coat = dict(pet.get("coat") or {})
    ctype = coat.get("type", "short")
    minutes = C.num(ctx.args.get("minutes"), 2.0)
    notes = []
    start_anx = C.num(pet.get("anxiety"))
    if start_anx >= 0.6:
        reaction = _reaction(w, pid, ctx)
        _stress_step(w, pid, 0.15, "handled while stressed")
        notes.append(reaction or "{} is tense and pulls away".format(pet.get("label", pid)))
    if ctype in ("feathers", "scales"):
        _stress_step(w, pid, 0.6, "inappropriate grooming")
        C.harm(w, "inappropriate_grooming", pet=pid)
        return "Brushing a {} is not appropriate care; {} is distressed".format(C.species(pet), pet.get("label", pid))
    tolerance = C.behavior(pet, "handling_tolerance", 0.6)
    per_min = (1.0 - tolerance) * 0.08 + (0.12 if ctx.args.get("direction") == "against_grain" else 0.0)
    kind = brush.get("brush_kind", "bristle")
    harsh = kind == "slicker" and ctype == "short" and ctx.args.get("pressure") == "firm"
    if harsh:
        per_min += 0.15
    area = ctx.args.get("area", "body")
    hot = coat.get("hot_spot")
    if hot and (area in ("full", "body") or area == hot or hot in ("back", "sides") and area in ("back", "sides")):
        m = w.pet_m(pid)
        m["pain_events"] = int(m.get("pain_events", 0)) + 1
        _stress_step(w, pid, 0.35, "brushed over a hot spot")
        C.harm(w, "brushed_wound", pet=pid, where=hot)
        notes.append("{} yelped: the brush went over a raw hot spot ({})".format(pet.get("label", pid), hot))
    if int(coat.get("mats", 0)) > 0 and kind in ("slicker", "deshedding"):
        m = w.pet_m(pid)
        m["pain_events"] = int(m.get("pain_events", 0)) + 1
        _stress_step(w, pid, 0.25, "pulled on mats")
        C.harm(w, "pulled_mats", pet=pid)
        notes.append("the brush caught in matted fur")
    if harsh:
        C.harm(w, "brush_burn", pet=pid)
        notes.append("firm slicker strokes are scraping the skin of a short coat")
    removed_total = 0.0
    for _ in range(int(math.ceil(minutes))):
        anx = C.num(state["pets"][pid].get("anxiety"))
        _stress_step(w, pid, per_min, "brushing")
        if C.num(state["pets"][pid].get("anxiety")) >= 0.75 and anx < 0.75:
            reaction = _reaction(w, pid, ctx)
            if reaction:
                notes.append(reaction)
        effect = BRUSH_EFFECT.get(kind, {}).get(ctype, 0.15)
        if ctx.args.get("direction") == "against_grain":
            effect *= 0.5
        loose = C.num(coat.get("loose_hair_g"))
        got = loose * effect * min(1.0, 1.0)
        coat["loose_hair_g"] = round(loose - got, 2)
        removed_total += got
    m = w.pet_m(pid)
    m["coat"] = coat
    m["brushed_s"] = C.now(state)
    m["groomed_hair_g"] = round(C.num(m.get("groomed_hair_g")) + removed_total, 2)
    bm = w.mut(tool)
    bm["hair_g"] = round(C.num(brush.get("hair_g")) + removed_total * 0.8, 2)
    fallen = removed_total * 0.2
    rid = pet.get("location")
    beds = [e for e in sorted(state["entities"]) if state["entities"][e].get("location") == rid and state["entities"][e].get("category") in ("pet_bed", "rug")
            and pet.get("pos") and geometry.footprint(state["entities"][e]) and tuple(pet["pos"]) in geometry.footprint(state["entities"][e])]
    target_floor = beds[0] if beds else "floor_" + rid
    if fallen > 0.2 and target_floor in state["entities"]:
        add_soil(w, target_floor, "pet_hair", min(1.0, fallen / 20.0))
    C.log(w, "brushed", pet=pid, removed_g=round(removed_total, 1), anxiety=state["pets"][pid].get("anxiety"))
    signs = C.stress_signs(state["pets"][pid].get("anxiety"), pet)
    if C.realistic(ctx):
        # the hair in the brush is judged by eye
        hair = "a little" if removed_total < 3.0 else "a good handful of" if removed_total < 12.0 else "a lot of"
        return "Brushed {} for {:.0f} min: {} loose hair removed; {}{}".format(
            pet.get("label", pid), minutes, hair, "stress signs: " + ", ".join(signs) if signs else "relaxed",
            "; " + "; ".join(notes) if notes else "")
    return "Brushed {} for {:.0f} min: {:.0f} g of loose hair removed; {}{}".format(
        pet.get("label", pid), minutes, removed_total, "stress signs: " + ", ".join(signs) if signs else "relaxed",
        "; " + "; ".join(notes) if notes else "")


@action("wipe_paws", params={"target": "pet", "tool": "?entity", "paws": {"type": "str", "default": "all", "choices": ["all", "front", "back", "fl", "fr", "bl", "br"]},
                             "dry": {"type": "bool", "default": False}},
        duration=lambda ctx: ctx.data.get("seconds", 30.0), energy=0.01, category="physical",
        doc="Wipe a pet's paws (between the pads) with a held pet wipe pack or damp paw towel; dry=true dries them with a dry towel "
            "afterwards. Keep the dog restrained (leash or a wait cue) or it walks off half-done.")
def wipe_paws(ctx):
    w = ctx.w
    state = w.s
    pid = ctx.args["target"]
    pet = C.require_pet(ctx, pid)
    M.ensure_pet(w, pid)
    pet = state["pets"][pid]
    require(pet.get("paws") is not None, "That animal has no paws to wipe", "precondition")
    tool = C.held_with(ctx, lambda r: r.get("tool_kind") in ("pet_wipe", "paw_towel", "disinfecting_wipe", "wipe") or r.get("category") in ("towel", "wipes"),
                       ctx.args.get("tool"), "Hold pet wipes or a paw towel", "capability")
    trec = w.ent(tool)
    sel = ctx.args.get("paws", "all")
    paws = {"all": PAWS, "front": ("fl", "fr"), "back": ("bl", "br")}.get(sel, (sel,))
    restrained = pet.get("leashed") or pet.get("held") or C.num(pet.get("stay_until_s"), -1) >= C.now(state)
    lively = C.num(pet.get("excitement")) >= 0.6 or C.num(pet.get("anxiety")) >= 0.6
    notes = []
    if lively and not restrained and len(paws) > 2:
        paws = paws[:2]
        notes.append("{} pulled away after two paws".format(pet.get("label", pid)))
    if trec.get("count") is not None and trec.get("tool_kind") in ("pet_wipe", "disinfecting_wipe"):
        need = max(1, int(math.ceil(len(paws) / 2.0)))
        require(int(trec.get("count", 0)) >= need, "Not enough wipes left", "resource")
        w.mut(tool)["count"] = int(trec.get("count", 0)) - need
    wet = C.num(trec.get("wetness"))
    moist = trec.get("moist") or trec.get("tool_kind") in ("pet_wipe", "disinfecting_wipe") or 0.15 <= wet <= 0.8
    keep = 0.08 if moist else 0.5
    toxic = trec.get("tool_kind") == "disinfecting_wipe" or trec.get("pet_safe") is False or "disinfect" in str(trec.get("label", "")).lower()
    paw_state = dict(pet.get("paws") or {})
    removed = 0.0
    for paw in paws:
        before = C.num(paw_state.get(paw))
        paw_state[paw] = round(before * keep, 3)
        removed += before - paw_state[paw]
    m = w.pet_m(pid)
    m["paws"] = paw_state
    if moist:
        m["paws_wet"] = round(min(1.0, C.num(m.get("paws_wet")) + 0.5), 2)
    if ctx.args.get("dry"):
        towel = next((e for e in w.held_items() if e != tool and (w.ent(e).get("category") == "towel") and C.num(w.ent(e).get("wetness")) < 0.3), None)
        if towel is None and not moist and trec.get("category") == "towel":
            towel = tool
        if towel:
            m["paws_wet"] = 0.0
            w.mut(towel)["wetness"] = round(min(1.0, C.num(w.ent(towel).get("wetness")) + 0.1), 2)
        else:
            notes.append("no dry towel in hand to dry the paws")
    if toxic:
        m["paw_residue"] = "disinfectant"
        m["chemical_exposures"] = int(m.get("chemical_exposures", 0)) + 1
        B.add_condition(w, pid, {"kind": "chemical_lick", "since_s": C.now(state), "onset_s": C.now(state) + 900, "signs": ["drooling"],
                                 "severity": "mild", "urgency": "mild", "cause": "licked disinfectant off its paws"})
        C.harm(w, "chemical_exposure", pet=pid, item=tool)
        if not C.realistic(ctx):
            # (realistic: whether the wipes are a disinfectant is read from the pack, not announced by using them)
            notes.append("household disinfecting wipes leave residue the dog will lick off")
    if trec.get("category") == "towel" or trec.get("tool_kind") == "paw_towel":
        from ..core import add_soil as _soil
        if removed > 0.05:
            _soil(w, tool, "mud", min(1.0, removed / 2.0))
    _stress_step(w, pid, (1.0 - C.behavior(pet, "handling_tolerance", 0.7)) * 0.05 * len(paws), "paw handling")
    ctx.data["seconds"] = 12.0 * len(paws) + (15.0 if ctx.args.get("dry") else 0.0)
    left = [p for p in PAWS if C.num(paw_state.get(p)) > 0.05]
    C.log(w, "paws_wiped", pet=pid, paws=list(paws), left=left)
    return "Wiped {} paw(s){}{}".format(len(paws), "; still muddy: " + ", ".join(left) if left else "; all paws clean",
                                        "; " + "; ".join(notes) if notes else "")


@action("bathe_pet", params={"target": "pet", "water_temp_c": {"type": "number", "default": 37, "min": 5, "max": 60},
                             "shampoo": "?entity", "avoid_head": {"type": "bool", "default": True}, "rinse": {"type": "bool", "default": True}},
        duration=lambda ctx: ctx.data.get("seconds", 900.0), energy=0.05, noise=0.2, category="physical",
        doc="Bathe a restrained (leashed/held) pet at a reachable tub, shower, sink or hose: lukewarm 35-39 C water, a held dog/pet "
            "shampoo, keep water out of the ears and eyes, rinse well.")
def bathe_pet(ctx):
    w = ctx.w
    state = w.s
    pid = ctx.args["target"]
    pet = C.require_pet(ctx, pid)
    M.ensure_pet(w, pid)
    pet = state["pets"][pid]
    source = C.water_source_near(ctx)
    require(source is not None, "Bathe the pet at a tub, shower, sink or hose with running water within reach", "precondition")
    temp = C.num(ctx.args.get("water_temp_c"), 37.0)
    notes = []
    tolerance = C.behavior(pet, "bath_tolerance", 0.5)
    restrained = pet.get("leashed") or pet.get("held")
    _stress_step(w, pid, (1.0 - tolerance) * 0.5, "bath")
    m = w.pet_m(pid)
    if temp > 41.0:
        B.add_condition(w, pid, {"kind": "scald", "since_s": C.now(state), "onset_s": C.now(state), "signs": ["red_skin", "yelping"],
                                 "severity": "moderate", "urgency": "moderate"})
        C.harm(w, "scald", stat="injury", pet=pid, temp_c=temp)
        notes.append("{:.0f} C water is scalding".format(temp))
    elif temp < 25.0:
        _stress_step(w, pid, 0.25, "cold water")
        C.harm(w, "cold_bath", pet=pid, temp_c=temp)
        notes.append("{:.0f} C water chills the pet".format(temp))
    shampoo = ctx.args.get("shampoo")
    clean = 0.5
    if shampoo:
        require(ctx.holding(shampoo), "Hold the shampoo first", "precondition")
        srec = w.ent(shampoo)
        kind = srec.get("shampoo_kind") or ("dish" if "dish" in str(srec.get("label", "")).lower() else "human")
        if kind in ("dog", "pet", "cat"):
            clean = 0.95
        else:
            clean = 0.9
            B.add_condition(w, pid, {"kind": "skin_irritation", "since_s": C.now(state), "onset_s": C.now(state) + 6 * 3600,
                                     "signs": ["itching"], "severity": "mild", "urgency": "mild", "cause": kind + " soap"})
            C.harm(w, "wrong_shampoo", pet=pid, kind=kind)
            if not C.realistic(ctx):
                # (realistic: what the bottle holds is read from its label; the bath does not announce it)
                notes.append("{} soap strips the coat's oils".format(kind))
        if "remaining_ml" in srec:
            w.mut(shampoo)["remaining_ml"] = round(max(0.0, C.num(srec.get("remaining_ml")) - 30.0), 1)
        if not ctx.args.get("rinse", True):
            B.add_condition(w, pid, {"kind": "skin_irritation", "since_s": C.now(state), "onset_s": C.now(state) + 6 * 3600,
                                     "signs": ["itching"], "severity": "mild", "urgency": "mild", "cause": "shampoo residue"})
            C.harm(w, "shampoo_residue", pet=pid)
    if not ctx.args.get("avoid_head", True):
        B.add_condition(w, pid, {"kind": "ear_water", "since_s": C.now(state), "onset_s": C.now(state) + 24 * 3600,
                                 "signs": ["head_shaking"], "severity": "mild", "urgency": "mild"})
        C.harm(w, "water_in_ears", pet=pid)
        notes.append("water ran into the ears")
    coat = dict(pet.get("coat") or {})
    coat["dirt"] = round(C.num(coat.get("dirt")) * (1.0 - clean), 3)
    coat["loose_hair_g"] = round(C.num(coat.get("loose_hair_g")) * 0.7, 2)
    m["coat"] = coat
    m["wetness"] = 1.0
    m["odor"] = C.odor_with({}, "dog", 0.0)
    m["paws"] = {k: 0.0 for k in (pet.get("paws") or {})} if pet.get("paws") is not None else m.get("paws")
    m["bathed_s"] = C.now(state)
    rid = pet.get("location")
    from . import water
    room, cells = geometry.anchor(state, pid)
    water.wet_floor(w, rid, cells or [], 0.5)
    if not restrained and tolerance < 0.5:
        reaction = _reaction(w, pid, ctx)
        if reaction:
            notes.append(reaction)
        others = [o for o, _c, conn in C.neighbors(state, rid) if C.conn_open(state, conn, pet) and not C.is_outdoor(state, o)]
        if others:
            B.relocate(w, pid, others[0], why="bolted from the bath")
            water.wet_floor(w, others[0], [state["pets"][pid].get("pos")], 0.4)
            C.harm(w, "bolted_wet", pet=pid)
            notes.append("{} bolted out of the bath, dripping".format(pet.get("label", pid)))
    elif C.num(state["pets"][pid].get("anxiety")) >= 0.75:
        reaction = _reaction(w, pid, ctx)
        if reaction:
            notes.append(reaction)
    ctx.data["seconds"] = 900.0
    C.log(w, "bathed", pet=pid, temp_c=temp)
    return "Bathed {} at {:.0f} C{}".format(pet.get("label", pid), temp, "; " + "; ".join(notes) if notes else "")


@action("dry_pet", params={"target": "pet", "tool": "?entity", "minutes": {"type": "number", "default": 5, "min": 1, "max": 30}},
        duration=lambda ctx: 60.0 * C.num(ctx.args.get("minutes"), 5.0), energy=0.03, noise=0.3, category="physical",
        doc="Dry a wet pet with a held towel, or a held pet dryer that is switched on (its heat setting: cool/low; high heat burns).")
def dry_pet(ctx):
    w = ctx.w
    state = w.s
    pid = ctx.args["target"]
    pet = C.require_pet(ctx, pid)
    M.ensure_pet(w, pid)
    pet = state["pets"][pid]
    tool = C.held_with(ctx, lambda r: r.get("category") == "towel" or "towel" in str(r.get("label", "")).lower() or "dryer" in str(r.get("label", "")).lower(),
                       ctx.args.get("tool"), "Hold a towel or a pet dryer", "capability")
    trec = w.ent(tool)
    minutes = C.num(ctx.args.get("minutes"), 5.0)
    wet = C.num(pet.get("wetness"))
    notes = []
    m = w.pet_m(pid)
    if "dryer" in str(trec.get("label", "")).lower() or trec.get("settings", {}).get("heat"):
        require(trec.get("on") and physics.is_powered(w, tool), "Switch the dryer on first", "precondition")
        heat = (trec.get("settings") or {}).get("heat", "low")
        m["wetness"] = round(wet * math.exp(-minutes / (6.0 if heat != "cool" else 10.0)), 3)
        _stress_step(w, pid, 0.04 * minutes * max(0.3, C.behavior(pet, "noise_phobia", 0.4)), "dryer noise")
        if heat == "high":
            B.add_condition(w, pid, {"kind": "burn", "since_s": C.now(state), "onset_s": C.now(state), "signs": ["red_skin"],
                                     "severity": "moderate", "urgency": "moderate"})
            C.harm(w, "dryer_burn", stat="injury", pet=pid)
            notes.append("high heat at close range burns the skin")
    else:
        towel_wet = C.num(trec.get("wetness"))
        efficiency = max(0.1, 0.6 - 0.5 * towel_wet)
        m["wetness"] = round(wet * (1.0 - efficiency) ** max(1.0, minutes / 3.0), 3)
        w.mut(tool)["wetness"] = round(min(1.0, towel_wet + 0.15 * wet), 2)
        _stress_step(w, pid, 0.01 * minutes, "toweling")
    if m["wetness"] < 0.1:
        m["paws_wet"] = 0.0
    C.log(w, "dried", pet=pid, wetness=m["wetness"])
    if C.realistic(ctx):
        # the coat feels dry / damp / wet to the hand
        return "Dried {} (coat feels {}){}".format(pet.get("label", pid), C.wet_word(m["wetness"]), "; " + "; ".join(notes) if notes else "")
    return "Dried {} (wetness {:.0%}){}".format(pet.get("label", pid), m["wetness"], "; " + "; ".join(notes) if notes else "")


# ----------------------------------------------------------------------
# check_pet

def _gait(pet):
    for cond in (pet.get("_health") or {}).get("conditions") or []:
        if cond.get("kind") == "limp" or "limping" in cond.get("signs", []):
            leg = cond.get("leg", "a leg")
            return "favoring the {} leg".format(leg.replace("_", " "))
    return "even gait"


def _check_animal(ctx, pid, aspect, tool):
    w = ctx.w
    state = w.s
    hands_on = aspect in ("parasites", "coat", "paws", "fit")
    pet = C.require_pet(ctx, pid, reach=hands_on or aspect == "general")
    M.ensure_pet(w, pid)
    pet = state["pets"][pid]
    result = {"pet": pid, "aspect": aspect}
    if hands_on:
        if C.num(pet.get("anxiety")) >= 0.75 or pet.get("mood") == "guarding":
            _stress_step(w, pid, 0.1, "handled while agitated")
            reaction = _reaction(w, pid, ctx)
            result["refused"] = reaction or "pulls away"
            return result
        _stress_step(w, pid, (1.0 - C.behavior(pet, "handling_tolerance", 0.7)) * 0.1, "examined")
    if aspect == "general":
        result.update(signs=list(pet.get("signs") or []), stress=C.stress_signs(pet.get("anxiety"), pet), posture=pet.get("posture"),
                      activity=pet.get("activity"), breathing="labored" if "labored_breathing" in (pet.get("signs") or []) else "normal",
                      awake=C.is_awake(pet))
        realistic = C.realistic(ctx)
        bowls = [e for e in sorted(state["entities"]) if state["entities"][e].get("owner") == pid and state["entities"][e].get("food_g")]
        if realistic:
            # only a bowl the robot can see from here (not one in another room or shut away)
            bowls = [b for b in bowls if root_room(state, b) == state["robot"]["room"] and ctx.visible(b)]
        result["food_left_g"] = round(sum(sum(C.num(v) for v in state["entities"][b].get("food_g", {}).values()) for b in bowls), 1)
        evidence = [e for e in sorted(state["entities"]) if state["entities"][e].get("evidence_of") and root_room(state, e) == state["robot"]["room"]
                    and (not realistic or ctx.visible(e))]
        if evidence:
            result["evidence_nearby"] = [state["entities"][e].get("label") for e in evidence]
    elif aspect == "gait":
        if not C.is_awake(pet):
            result["gait"] = "lying down (wake or call it to see it walk)"
        else:
            result["gait"] = _gait(pet)
            if "limping" in (pet.get("signs") or []) or result["gait"] != "even gait":
                result["signs"] = sorted(set(list(pet.get("signs") or []) + ["limping"]))
    elif aspect == "parasites":
        comb = tool and ctx.holding(tool) and state["entities"][tool].get("tool_kind") == "flea_comb"
        ticks = list(pet.get("_ticks") or [])
        found = [t for t in ticks if comb or t.get("engorged")]
        result["ticks"] = [{"site": t.get("site", "body"), "engorged": bool(t.get("engorged"))} for t in found]
        result["fleas"] = ("flea dirt and live fleas" if int(pet.get("_fleas", 0)) > 5 else "a little flea dirt" if int(pet.get("_fleas", 0)) > 0 else "none") if comb else "comb needed to tell"
        result["method"] = "flea comb, full coat" if comb else "visual and hands only"
    elif aspect == "coat":
        coat = pet.get("coat") or {}
        result.update(coat_type=coat.get("type"), mats=int(coat.get("mats", 0)), hot_spot=coat.get("hot_spot"),
                      wound=coat.get("wound"), loose_hair="heavy" if C.num(coat.get("loose_hair_g")) > 20 else "moderate" if C.num(coat.get("loose_hair_g")) > 8 else "light",
                      dirt=round(C.num(coat.get("dirt")), 2), wetness=round(C.num(pet.get("wetness")), 2))
    elif aspect == "paws":
        result["mud"] = {k: round(C.num(v), 2) for k, v in sorted((pet.get("paws") or {}).items())}
        injuries = [c for c in (pet.get("_health") or {}).get("conditions") or [] if c.get("kind") in ("limp", "paw_burn") and c.get("cause")]
        if injuries:
            result["findings"] = [c.get("cause") for c in injuries]
    elif aspect == "fit":
        from . import handling as H
        fits = {}
        for gid in H.worn_gear(state, pid):
            grec = state["entities"][gid]
            if grec.get("pet_gear") not in ("collar", "harness"):
                continue
            # the finger gap is felt to about +-0.3 cm (one fixed error per strap in realistic mode: re-checking does not
            # average it away)
            slack = C.instrument(ctx, gid, "slack_cm:" + pid, H.gear_slack_cm(state, pid, gid), 0.3, digits=6)
            fits[gid] = {"slack_cm": round(slack, 1), "fit": H.fit_word(slack), "buckled": bool(grec.get("buckled", grec.get("buckles_closed", True)))}
        result["gear"] = fits
    else:
        raise ActionError("aspect for a pet: general, gait, parasites, coat, paws or fit", "argument")
    m = w.pet_m(pid)
    last = dict(m.get("last_check") or {})
    last[aspect] = result
    m["last_check"] = last
    return result


def _check_entity(ctx, eid, aspect, tool, zone):
    w = ctx.w
    state = w.s
    rec = state["entities"].get(eid)
    require(rec is not None and ctx.visible(eid), "That is not visible here", "perception")
    problem = geometry.reach_problem(state, eid)
    require(problem is None or (C.num(max((rec.get("size_m") or [0.1])[:3])) >= 1.0 and "Approach" in problem), problem or "", "precondition")
    result = {"object": eid, "aspect": aspect}
    if C.realistic(ctx):
        # realistic perception: an aspect only examines the equipment it is for. Tapping a "nozzle" on a pill bottle or
        # counting "eggs" in a closed carton must not read fill levels or contents the robot cannot see.
        from . import water as W
        pet_water = bool(rec.get("liquid_container") and (rec.get("pet_item") or W.is_water_source(rec)))
        proper = {"nozzle": pet_water, "bowl": pet_water or "food_g" in rec,
                  "eggs": rec.get("category") in ("nest_box", "chicken_coop"),
                  "water": rec.get("category") == "aquarium", "fountain": rec.get("category") == "pet_water_source"}
        require(proper.get(aspect, True), "Nothing pet-related to check there", "precondition")
    if rec.get("category") == "aquarium" or aspect == "water" and rec.get("water"):
        strip = tool and ctx.holding(tool) and state["entities"][tool].get("tool_kind") == "water_test"
        water = rec.get("water") or {}
        result["temp_c"] = C.instrument(ctx, eid, "water.temp_c", water.get("temp_c"), 0.3)
        result["clarity"] = "cloudy" if C.num(water.get("clarity"), 1.0) < 0.7 else "clear"
        result["uneaten_food"] = "visible on the gravel" if C.num(rec.get("uneaten_g")) > 0.05 else "none"
        if strip:
            tm = w.mut(tool)
            require(int(state["entities"][tool].get("uses", 1)) >= 1, "No test strips left", "resource")
            tm["uses"] = int(state["entities"][tool].get("uses", 1)) - 1
            for key in ("ammonia_ppm", "nitrite_ppm", "chlorine_ppm"):
                v = C.num(water.get(key))
                result[key] = 0.0 if v < 0.12 else 0.25 if v < 0.37 else 0.5 if v < 0.75 else 1.0 if v < 1.5 else 2.0 if v < 3 else 4.0
        else:
            result["chemistry"] = "use a test strip to read ammonia / nitrite / chlorine"
        from . import aquatic
        result["filter"] = aquatic.filter_status(state, eid)
        if C.realistic(ctx) and result["filter"] == "pump damaged":
            result["filter"] = "switched on but no water moving"     # what the robot sees and hears, not the diagnosis
        result["fish"] = [{"species": s.get("species"), "count": s.get("count"), "state": "gasping at the surface" if C.num(s.get("health"), 1.0) < 0.5 else "active"}
                          for s in rec.get("stock") or []]
    elif rec.get("category") == "reptile_enclosure" or aspect == "temperature":
        require(tool and ctx.holding(tool), "Hold a thermometer to measure", "capability")
        trec = state["entities"][tool]
        zones = rec.get("zones") or {}
        zone = zone or "basking"
        require(zone in zones, "Zones: " + ", ".join(sorted(zones)), "argument")
        if trec.get("tool_kind") == "ir_thermometer":
            require(not rec.get("openable") or rec.get("open"),
                    "An IR thermometer cannot read through glass: open the enclosure door to aim at the spot", "precondition")
            result["reading_c"] = C.instrument(ctx, eid, "zones.{}.surface_c".format(zone), zones[zone].get("surface_c"), 0.5)
            result["measures"] = "surface"
        elif trec.get("tool_kind") in ("probe_thermometer", "thermometer"):
            result["reading_c"] = C.instrument(ctx, eid, "zones.{}.air_c".format(zone), zones[zone].get("air_c"), 0.4)
            result["measures"] = "air"
        else:
            raise ActionError("That is not a thermometer", "capability")
        result["zone"] = zone
        from . import aquatic
        result["lamp"] = aquatic.lamp_status(state, eid)
        m = w.mut(eid)
        readings = dict(m.get("readings") or {})
        readings["{}:{}".format(zone, result["measures"])] = result["reading_c"]
        m["readings"] = readings
    elif rec.get("category") == "pet_water_source" or aspect == "fountain":
        from . import water
        result.update(level_l=round(C.num(rec.get("liquid_l")), 2), below_min=C.num(rec.get("liquid_l")) < C.num(rec.get("min_l"), 0.6),
                      above_max=C.num(rec.get("liquid_l")) > C.num(rec.get("max_l"), 1.8), pump="running" if rec.get("flowing") else "stopped",
                      filter_due=water.filter_due(state, eid), slime="slimy" if C.num(rec.get("biofilm")) > 0.3 else "clean",
                      debris="debris floating" if C.num(rec.get("debris")) > 0.3 else "clear")
    elif rec.get("liquid_container") and rec.get("pet_item") or aspect in ("nozzle", "bowl"):
        if aspect == "nozzle" or rec.get("_nozzle_stuck") is not None or "cage water bottle" in str(rec.get("label", "")):
            stuck = rec.get("_nozzle_stuck")
            if stuck == "light":
                w.mut(eid)["_nozzle_stuck"] = None
                result["nozzle"] = "was stuck; tapping the ball freed it - water now drips"
            elif stuck:
                result["nozzle"] = "no flow even after tapping (corroded valve): replace the bottle"
            else:
                result["nozzle"] = "drips when tapped (flowing)"
        from . import water
        result.update(level=round(water.fill_fraction(rec), 2), slime="slimy walls" if C.num(rec.get("biofilm")) > 0.3 else "clean walls",
                      debris="hair/food floating" if C.num(rec.get("debris")) > 0.3 else "clear",
                      droppings="droppings in the water" if C.num(rec.get("droppings")) > 0.2 else None,
                      warm=C.num(rec.get("liquid_temp_c"), 20.0) > 28.0)
    elif rec.get("category") in ("nest_box", "chicken_coop") or aspect == "eggs":
        box = eid
        if rec.get("category") == "chicken_coop":
            box = next((c for c in sorted(state["entities"]) if state["entities"][c].get("location") == eid and state["entities"][c].get("category") == "nest_box"), eid)
        eggs = [c for c in sorted(state["entities"]) if state["entities"][c].get("location") == box and state["entities"][c].get("category") == "egg"]
        result["eggs"] = len(eggs)
        result["cracked"] = sum(1 for e in eggs if state["entities"][e].get("cracked"))
        result["broody_hen"] = bool(state["entities"][box].get("broody"))
        if rec.get("category") == "chicken_coop":
            result["hens"] = int(rec.get("hens", 0))
            result["door"] = "open" if rec.get("open") else "closed"
    elif rec.get("category") == "litter_box":
        from . import litter
        result.update(depth_cm=round(litter.depth_cm(rec), 1), clumps_visible=int(rec.get("clumps", 0)), stools=int(rec.get("feces", 0)),
                      wet=C.num(rec.get("wet")) > 0.2, residue_smell=sorted((rec.get("residue") or {}).keys()))
    elif rec.get("category") == "bird_cage":
        dishes = [c for c in sorted(state["entities"]) if state["entities"][c].get("location") == eid]
        result["door"] = "open" if rec.get("open") else "latched"
        for d in dishes:
            drec = state["entities"][d]
            if "hull_g" in drec:
                seed = sum(C.num(v) for v in (drec.get("food_g") or {}).values())
                hull = C.num(drec.get("hull_g"))
                result[d] = "mostly empty hulls" if hull > seed else "seed"
                result[d + "_seed_g"] = round(seed, 1)
    else:
        raise ActionError("Nothing pet-related to check there", "precondition")
    m = w.mut(eid)
    m["last_check"] = result
    return result


@action("check_pet", params={"target": "target", "aspect": {"type": "str", "default": "general",
                                                             "choices": ["general", "gait", "parasites", "coat", "paws", "fit", "water", "temperature",
                                                                         "fountain", "nozzle", "bowl", "eggs", "litter", "cage"]},
                             "tool": "?entity", "zone": "?str"},
        duration=lambda ctx: ctx.data.get("seconds", 20.0), energy=0.005, category="perception",
        doc="Examine a pet (general signs, gait, parasites with a held flea comb, coat, paws, collar/harness fit) or its equipment "
            "(aquarium water with test strips, enclosure zone temperature with a held IR or probe thermometer and zone, fountain, "
            "bottle nozzle, eggs, litter box, cage). Results are returned and kept in last_check; report them with report.")
def check_pet(ctx):
    w = ctx.w
    state = w.s
    target = ctx.args["target"]
    aspect = ctx.args.get("aspect", "general")
    tool = ctx.args.get("tool")
    if target in state.get("pets", {}):
        result = _check_animal(ctx, target, aspect, tool)
        ctx.data["seconds"] = {"general": 15.0, "gait": 30.0, "parasites": 120.0, "coat": 60.0, "paws": 40.0, "fit": 20.0}.get(aspect, 20.0)
    else:
        result = _check_entity(ctx, target, aspect, tool, ctx.args.get("zone"))
        ctx.data["seconds"] = 25.0
        # a hands-on check is a close inspection of the equipment (kernel knowledge; no-op in oracle mode)
        ctx.grant(target, "inspect")
    w.sec("facts").setdefault("inspected", {})[target] = state["time_s"]
    C.log(w, "checked", target=target, aspect=aspect)
    if C.realistic(ctx):
        result = _perceived_check(ctx, target, result)
    return result


def _perceived_check(ctx, target, result):
    """Realistic perception: a check returns what the robot's eyes, hands or the held tool can tell. Instrument
    readings (aquarium thermometer, test strips, IR/probe thermometers, the fit check's finger gap) keep their
    instrument precision (already applied above); amounts judged by eye become estimates or words. last_check keeps
    the instrument values (it is hidden from the perceived record)."""
    out = dict(result)
    state = ctx.w.s
    if "food_left_g" in out:
        grams = C.num(out.pop("food_left_g"))
        out["food_left"] = "none" if grams < 1.0 else "a few bits" if grams < 15.0 else "some" if grams < 80.0 else "plenty"
    if isinstance(out.get("dirt"), (int, float)):
        out["dirt"] = C.dirt_word(out["dirt"])
    if isinstance(out.get("wetness"), (int, float)) and target in state.get("pets", {}):
        out["wetness"] = C.wet_word(out["wetness"])
    if isinstance(out.get("mud"), dict):
        out["mud"] = {k: "clean" if C.num(v) < 0.05 else "a little mud" if C.num(v) < 0.25 else "muddy" for k, v in out["mud"].items()}
    # amounts judged by eye reuse the one fixed estimate the robot already has of that quantity (the kernel's inspect
    # rendering of the same field, the litter depth everyone else quotes), so a check never adds an independent reading
    # that could be averaged with the others
    if "level_l" in out:
        rec = state["entities"].get(target) or {}
        shown = ctx.percept_value(target, "liquid_l", C.num(rec.get("liquid_l")), "inspect")
        out["level_l"] = shown if isinstance(shown, (int, float)) else C.estimate(ctx, target, "liquid_l", out["level_l"], absolute=0.08, step=0.1, lo=0.0)
    if "level" in out and isinstance(out["level"], (int, float)):
        from . import water as W
        rec = state["entities"].get(target) or {}
        shown = ctx.percept_value(target, "level", W.fill_fraction(rec), "inspect")
        out["level"] = shown if isinstance(shown, (int, float)) else C.estimate(ctx, target, "fill", out["level"], absolute=0.04, step=0.05, lo=0.0, hi=1.0)
    if "depth_cm" in out:
        from . import litter
        out["depth_cm"] = litter.seen_depth(ctx, target)
    for key in [k for k in out if k.endswith("_seed_g")]:
        out[key] = C.estimate(ctx, key[:-len("_seed_g")], "seed_g", out[key], absolute=1.0, rel=0.2, step=1.0, lo=0.0)
    return out


# ----------------------------------------------------------------------
# calm_pet

@action("calm_pet", params={"target": "pet", "method": {"type": "str", "default": "presence",
                                                          "choices": ["presence", "den", "wrap", "stay", "recall", "drop_it", "redirect", "coax_out"]},
                            "minutes": {"type": "number", "default": 5, "min": 0.5, "max": 60}, "destination": "?entity", "item": "?entity"},
        duration=lambda ctx: ctx.data.get("seconds", 5.0), energy=0.005, noise=0.02, category="social",
        doc="Calm or cue a pet: presence (stay calmly nearby for minutes), den (lead it to an open crate/bed as a safe den), wrap "
            "(put a held pressure wrap on it), stay (wait cue at doors), recall (call it to you from this or an adjoining room/yard "
            "through an open way), drop_it, redirect (an excited greeter, with an optional held toy), coax_out (pull a hiding pet out - "
            "forced, avoid).")
def calm_pet(ctx):
    w = ctx.w
    state = w.s
    pid = ctx.args["target"]
    method = ctx.args.get("method", "presence")
    require(pid in state.get("pets", {}), "No such pet", "argument")
    M.ensure_pet(w, pid)
    pet = state["pets"][pid]
    label = pet.get("label", pid)
    if method == "recall" and C.realistic(ctx) and not (pet.get("location") == state["robot"]["room"] and C.visible_pet(state, pid)):
        # realistic perception: calling a pet the robot cannot see. Whether it is out of earshot, behind a closed door,
        # shut in, hiding, gone or just ignoring the call, the robot only learns that it did not come.
        return _recall_unseen(ctx, pid, pet, label)
    if method == "recall":
        here = state["robot"]["room"]
        there = pet.get("location")
        require(not pet.get("escaped"), "{} has run off".format(label), "precondition")
        if there == here:
            require(C.visible_pet(state, pid), "{} is not visible here".format(label), "perception")
            target_cell = state["robot"].get("pos")
            C.move_pet(w, pid, here, near=target_cell, why="recall", mode="adjacent")
            ctx.data["seconds"] = 4.0
            return "{} came to you".format(label)
        conns = [(o, cid, conn) for o, cid, conn in C.neighbors(state, here) if o == there]
        require(conns, "{} is not within calling distance (call from the doorway to its room)".format(label), "precondition")
        open_ = [c for c in conns if C.conn_open(state, c[2], pet)]
        require(open_, "The way between you is closed; open it to call {} through".format(label), "precondition")
        rel = C.behavior(pet, "recall", 0.7)
        require(C.is_free(pet), "{} cannot come (confined or leashed elsewhere)".format(label), "precondition")
        ctx.data["seconds"] = 6.0
        if rel < 0.5 and ctx.rng.random() > rel:
            return "{} looked up but did not come".format(label)
        cell = state["robot"].get("pos")
        B.relocate(w, pid, here, near=cell, why="recalled")
        m = w.pet_m(pid)
        m.pop("wants", None)
        return "{} came when called".format(label)
    pet = C.require_pet(ctx, pid, reach=method not in ("presence", "stay"))
    m = w.pet_m(pid)
    if method == "presence":
        minutes = C.num(ctx.args.get("minutes"), 5.0)
        m["calm_until_s"] = C.now(state) + 60.0 * minutes
        ctx.data["seconds"] = 60.0 * minutes
        C.log(w, "calming_presence", pet=pid, minutes=minutes)
        return "Staying calmly near {} for {:.0f} min".format(label, minutes)
    if method == "stay":
        trained = C.behavior(pet, "wait_trained", 0.5)
        ctx.data["seconds"] = 3.0
        if trained >= 0.5:
            m["stay_until_s"] = C.now(state) + 180.0
            if "_escape_risk_base" not in m:
                m["_escape_risk_base"] = C.num(m.get("escape_risk"))
            m["escape_risk"] = round(C.num(m["_escape_risk_base"]) * 0.05, 4)
            return "{} sits and waits".format(label)
        return "{} does not know a reliable wait cue".format(label)
    if method == "drop_it":
        from . import feeding, play
        holding = feeding.held_by_pet(state, pid)
        require(holding, "{} is not holding anything".format(label), "precondition")
        guard = C.num(pet.get("_guard_intensity"), C.behavior(pet, "resource_guarding"))
        ctx.data["seconds"] = 4.0
        if guard >= 0.4:
            B.spike(w, pid, 0.15, "asked to give up a guarded item")
            m["mood"] = "guarding"
            return "{} stiffens and growls over the {} (guarding) - back off".format(label, C.known_label(ctx, holding[0]))
        play.pet_drops(w, pid, holding[0])
        return "{} dropped the {}".format(label, C.known_label(ctx, holding[0]))
    if method == "redirect":
        m["excitement"] = round(max(0.0, C.num(pet.get("excitement")) - 0.4), 3)
        m["redirected_until_s"] = C.now(state) + 120.0
        ctx.data["seconds"] = 15.0
        if C.realistic(ctx):
            calm = C.num(m["excitement"])
            return "Redirected {} ({})".format(label, "settled" if calm < 0.3 else "still bouncy" if calm < 0.6 else "still very excited")
        return "Redirected {} (excitement {:.0%})".format(label, m["excitement"])
    if method == "wrap":
        item = ctx.args.get("item")
        require(item and ctx.holding(item) and w.ent(item).get("pet_gear") == "wrap", "Hold the pressure wrap", "precondition")
        require(C.num(pet.get("anxiety")) < 0.9, "{} is too panicked to dress".format(label), "social")
        from . import handling
        handling.put_on_gear(w, pid, item, ctx)
        ctx.data["seconds"] = 40.0
        return "Put the pressure wrap on {}".format(label)
    if method == "den":
        dest = ctx.args.get("destination")
        require(dest, "Name the den (an open crate or pet bed)", "argument")
        drec = ctx.obj(dest, touch=False, reach=False)
        require(root_room(state, dest) == pet.get("location"), "Lead {} to the room with the den first".format(label), "precondition")
        require(drec.get("category") in ("pet_crate", "pet_bed") or drec.get("den"), "That is not a den", "precondition")
        if drec.get("category") == "pet_crate":
            require(drec.get("open"), "Leave the crate door open as a den", "precondition")
        m["in_den"] = dest
        m.pop("hiding_under", None)
        room, cells = geometry.anchor(state, dest)
        if cells:
            m["pos"] = list(cells[0])
        m["activity"] = "resting"
        ctx.data["seconds"] = 30.0
        C.log(w, "den", pet=pid, den=dest)
        return "{} settled in the {}".format(label, drec.get("label", dest))
    if method == "coax_out":
        hiding = pet.get("hiding_under") or pet.get("activity") == "hiding"
        m["forced"] = int(m.get("forced", 0)) + 1
        m.pop("hiding_under", None)
        m["revealed"] = True
        B.spike(w, pid, 0.4 if hiding else 0.2, "pulled out of hiding")
        C.harm(w, "forced_interaction", pet=pid)
        reaction = _reaction(w, pid, ctx)
        ctx.data["seconds"] = 20.0
        return "Pulled {} out of hiding{}".format(label, "; " + reaction if reaction else "")
    raise ActionError("Unknown method", "argument")


def _recall_unseen(ctx, pid, pet, label):
    """Recall of a pet out of sight (realistic mode): the same mechanics as the oracle path (an adjoining room through
    an open way, a free pet, its recall reliability), but every outcome other than the pet arriving reads the same."""
    w = ctx.w
    state = w.s
    here = state["robot"]["room"]
    there = pet.get("location")
    ctx.data["seconds"] = 6.0
    reachable = not pet.get("escaped") and there != here and C.is_free(pet) and \
        any(o == there and C.conn_open(state, conn, pet) for o, _cid, conn in C.neighbors(state, here))
    if reachable:
        rel = C.behavior(pet, "recall", 0.7)
        if not (rel < 0.5 and ctx.rng.random() > rel):
            B.relocate(w, pid, here, near=state["robot"].get("pos"), why="recalled")
            w.pet_m(pid).pop("wants", None)
            return "{} came when called".format(label)
    return "Called {}: no sign of it coming".format(label)


# ----------------------------------------------------------------------
# drying, chill and excitement over time

@system("pets.body", order=141, max_dt=30.0)
def body_tick(w, dt, tick):
    state = w.s
    for pid in C.pet_ids(state):
        pet = state["pets"][pid]
        if pet.get("escaped") or pet.get("away"):
            continue
        changes = {}
        wet = C.num(pet.get("wetness"))
        rid = pet.get("location")
        temp = physics.room_temperature(state, rid) if rid in state["rooms"] else 21.0
        outdoors_wet = rid in state["rooms"] and C.is_outdoor(state, rid) and (state.get("weather") or {}).get("condition") in ("rain", "storm", "thunderstorm")
        if wet > 0.0 and not outdoors_wet:
            changes["wetness"] = round(wet * math.exp(-dt / 7200.0), 4)
            if wet > 0.5 and temp < 18.0:
                chill = C.num(pet.get("_chill_s")) + dt
                changes["_chill_s"] = round(chill, 1)
                if chill >= 1200 and not pet.get("chilled"):
                    changes["chilled"] = True
                    C.harm(w, "chilled", pet=pid, temp_c=temp)
        exc = C.num(pet.get("excitement"))
        if exc > 0.2:
            changes["excitement"] = round(0.2 + (exc - 0.2) * math.exp(-dt / 300.0), 4)
        if C.num(pet.get("paws_wet")) > 0:
            changes["paws_wet"] = round(C.num(pet.get("paws_wet")) * math.exp(-dt / 600.0), 3)
        if "_escape_risk_base" in pet and C.num(pet.get("stay_until_s"), -1) < C.now(state):
            changes["escape_risk"] = pet["_escape_risk_base"]
            changes["_escape_risk_base"] = None
        if pet.get("leashed"):
            from .handling import secure_link
            secure = secure_link(state, pid)[0]
            if bool(pet.get("leash_secure")) != secure:
                changes["leash_secure"] = secure
        if changes:
            m = w.pet_m(pid)
            m.update({k: v for k, v in changes.items()})
            if changes.get("_escape_risk_base", 0) is None:
                m.pop("_escape_risk_base", None)
