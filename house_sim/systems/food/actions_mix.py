"""Eggs, mixing, dough, straining, sifting, marinating, seasoning and coating."""
import math

from ...kernel import geometry
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ..core import add_soil, clear_contents_from, mark_contents_from
from . import common as C
from . import data as D
from . import mixing as X
from . import model as M
from . import percept as P

SPEED = {"slow": 0.6, "medium": 1.0, "fast": 1.5}


# ----------------------------------------------------------------------
# eggs

def _egg_in_hand(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = C.food_target(ctx, eid)
    f = rec["food"]
    require(f.get("cat") == "egg" and f.get("phase", "solid") == "solid" and "shell" in f.get("parts", ["shell"]),
            "That is not a whole raw egg", "argument")
    require(ctx.holding(eid), "Pick up the egg first", "precondition")
    # realistic perception: a boiled egg looks and feels like a raw one, so the robot cannot refuse up front; the shell
    # cracks and nothing pours out (_cooked_egg_cracked) -- that, not a refusal, is how it finds out
    require(not f.get("cooked") or ctx.realistic, "Peel cooked eggs instead (remove_part shell)", "argument")
    return eid, rec


def _cooked_egg_cracked(ctx, eid, seconds):
    """Realistic mode: cracking a hard-boiled egg (it looked like any egg) cracks the shell; nothing pours out. It
    takes as long as cracking a raw egg would."""
    w = ctx.w
    m = w.mut(eid)
    m["food"]["cracked"] = True
    C.seen_inside(ctx, eid)
    C.touch_food(ctx, eid, 0.2)
    C.seconds(ctx, seconds)
    return {"egg": eid, "result": "the shell cracked but nothing poured out: the egg is cooked inside",
            "shell_fragments_added": 0, "yolk_broken": False}


def _shell_to_hand(w, ctx, egg_id, grams):
    """After cracking, the robot holds the empty shell halves (they still carry raw egg)."""
    hand = ctx.hand_of(egg_id) or "right"
    rec = w.ent(egg_id)
    M.destroy_food(w, egg_id)
    shell = M.spawn_food(w, "eggshell", grams, "robot", relation="held", type_name="prep_scraps",
                         label="eggshell halves", waste_stream="compost")
    m = w.mut(shell)
    m["held_hand"] = hand
    m["food"]["composition"] = {"eggshell": grams}
    m["contamination"] = {"raw_meat": 0.3}
    M.ensure_secret(m, "contamination")
    r = w.robot_m()
    for h, held in list(r["hands"].items()):
        if held == egg_id:
            r["hands"][h] = shell
    return shell


@action("crack_egg", params={"target": "entity", "destination": "entity", "surface": "?str:flat|rim|edge",
                             "force": "?str:gentle|normal|hard"},
        duration=C.duration(6.0), irreversible=True, operates=True, noise=0.05,
        doc="Crack a held egg on a flat surface (or a bowl rim / counter edge, which drives shell shards in) and open it "
            "into the destination bowl. You are left holding the shell. Crack into a small separate bowl first so one bad "
            "egg cannot ruin the batch.")
@P.perceived_result
def crack_egg(ctx):
    w = ctx.w
    eid, rec = _egg_in_hand(ctx)
    dest = ctx.args["destination"]
    drec = C.vessel_reach(ctx, dest)
    require(drec.get("container"), "Crack it into a bowl or cup", "argument")
    surface = ctx.args.get("surface") or "flat"
    force = ctx.args.get("force") or "normal"
    f = rec["food"]
    if f.get("cooked"):
        return _cooked_egg_cracked(ctx, eid, 6.0 if force != "gentle" else 9.0)
    mass = M.qty(rec)
    shell_g = 0.11 * mass
    content = mass - shell_g
    fragments = {"flat": 0, "rim": 1, "edge": 1}[surface]
    yolk_broken = False
    spilled = 0.0
    if force == "hard":
        fragments += 3
        yolk_broken = True
        spilled = 0.35 * content
    if rec.get("_weak_yolk"):
        yolk_broken = True
    portion = M.portion_record(w.s, rec, content)
    pf = portion["food"]
    pf.update({"kind": "raw_egg", "phase": "liquid", "cut": "cracked", "count": None, "unit_g": None, "density": 1.03,
               "composition": {"egg_white": round(content * 0.65, 2), "egg_yolk": round(content * 0.35, 2)},
               "yolks_intact": 0 if yolk_broken else 1, "shell_fragments": fragments, "parts": [], "rte": False, "raw": True})
    portion["label"] = "raw egg"
    portion["type"] = "food_item"
    portion["is_a"] = ["portable_object", "object"]
    portion["perishable"] = True
    if spilled:
        pf["qty_g"] = round(content - spilled, 2)
        M.spill(w, dest, "raw_egg", spilled, "liquid")
        M.add_contam(w, w.ent(dest).get("location") if w.ent(dest).get("location") in w.s["entities"] else dest, "raw_meat", 0.3)
    new = X.add(w, portion, dest, "in")
    C.touch_food(ctx, new, 0.3)
    C.soil_hands(ctx, "food", 0.05, hands=ctx.hand_of(eid))      # a smear of white on the fingertips
    M.contact(w, dest, new, 0.4)
    shell = _shell_to_hand(w, ctx, eid, shell_g)
    C.seconds(ctx, 6.0 if force != "gentle" else 9.0)
    return {"egg": new, "destination": dest, "shell_in_hand": shell, "shell_fragments_added": fragments,
            "yolk_broken": yolk_broken}


@action("separate_egg", params={"target": "entity", "whites_to": "entity", "yolk_to": "entity",
                                "method": "?str:shell|separator|hand"},
        duration=C.duration(15.0), irreversible=True, operates=True, noise=0.03,
        doc="Separate a held egg: white into whites_to, yolk into yolk_to, by passing between shell halves (sheds shell "
            "edge shards), with an egg separator set on the whites bowl, or through the fingers. A weak yolk can break and "
            "leak fat into the whites; use a small bowl for each white before adding it to the main whites (3-bowl method).")
@P.perceived_result
def separate_egg(ctx):
    w = ctx.w
    eid, rec = _egg_in_hand(ctx)
    whites_to, yolk_to = ctx.args["whites_to"], ctx.args["yolk_to"]
    require(whites_to != yolk_to, "Use two different bowls", "argument")
    wrec = C.vessel_reach(ctx, whites_to)
    C.vessel_reach(ctx, yolk_to)
    method = ctx.args.get("method") or "shell"
    if method == "separator":
        require(any(w.ent(c).get("tool_kind") == "egg_separator" for c in w.children(whites_to)),
                "Set an egg separator on the whites bowl first", "precondition")
    if rec["food"].get("cooked"):
        return _cooked_egg_cracked(ctx, eid, 15.0)
    mass = M.qty(rec)
    content = mass * 0.89
    white_g, yolk_g = content * 0.66, content * 0.34
    weak = bool(rec.get("_weak_yolk"))
    fat_leak = 1.5 if weak else 0.0
    fragments = 1 if method == "shell" else 0
    if rec.get("_rotten"):
        fat_leak = max(fat_leak, 1.0)
    white = M.portion_record(w.s, rec, white_g)
    white["food"].update({"kind": "egg_white", "phase": "liquid", "cut": "separated", "count": None, "unit_g": None, "density": 1.04,
                          "composition": {"egg_white": round(white_g, 2)}, "yolk_fat_g": fat_leak, "shell_fragments": fragments,
                          "parts": [], "rte": False, "raw": True})
    white.update(label="egg white", type="food_item", is_a=["portable_object", "object"], perishable=True)
    yolk = M.portion_record(w.s, rec, yolk_g - fat_leak)
    yolk["food"].update({"kind": "egg_yolk", "phase": "liquid", "cut": "separated", "count": None, "unit_g": None, "density": 1.03,
                         "composition": {"egg_yolk": round(yolk_g - fat_leak, 2)}, "yolks_intact": 0 if weak else 1,
                         "parts": [], "rte": False, "raw": True})
    yolk.update(label="egg yolk", type="food_item", is_a=["portable_object", "object"], perishable=True)
    wid = X.add(w, white, whites_to, "in")
    yid = X.add(w, yolk, yolk_to, "in")
    for new, vessel in ((wid, whites_to), (yid, yolk_to)):
        M.contact(w, vessel, new, 0.4)
    C.touch_food(ctx, wid, 0.7 if method == "hand" else 0.3)
    C.soil_hands(ctx, "food", 0.3 if method == "hand" else 0.05, hands=ctx.hand_of(eid))   # white through the fingers
    _shell_to_hand(w, ctx, eid, mass * 0.11)
    C.seconds(ctx, 15.0)
    return {"whites": wid, "yolk": yid, "yolk_broke": weak, "shell_fragments_added": fragments}


# ----------------------------------------------------------------------
# mixing

def _vessel_contents(ctx, vessel_id):
    rec = ctx.obj(vessel_id, touch=True)
    require(rec.get("container"), "Mix in a bowl, pot or pan", "argument")
    require(not (rec.get("openable") and not rec.get("open")), "Open it first", "precondition")
    foods = [f for f in M.vessel_foods(ctx.w.s, vessel_id, "in") if X.mixable(ctx.w.ent(f))]
    require(foods or float(rec.get("liquid_l", 0.0) or 0.0) > 0, "There is nothing to mix in it", "precondition")
    return rec


def _splash(ctx, vessel, eid, speed, tool_id):
    w = ctx.w
    vrec = w.ent(vessel)
    depth = float((vrec.get("inner_size_m") or vrec.get("size_m") or [0.2, 0.2, 0.1])[2])
    fill = float(vrec.get("fill_level", 0.3))
    risk = 0.0
    if speed == "fast":
        risk += 0.04
    if depth < 0.04:
        risk += 0.08
    if fill > 0.7:
        risk += 0.05 * (fill - 0.7) / 0.3 + 0.03
    if tool_id and w.ent(tool_id).get("powered"):
        risk *= 2.0
    if risk <= 0.035 or M.food(w.ent(eid)).get("phase") not in ("liquid",):
        return 0.0
    lost = M.qty(w.ent(eid)) * min(0.3, risk)
    M.set_qty(w, eid, M.qty(w.ent(eid)) - lost)
    M.spill(w, vessel, M.kind(w.ent(eid)), lost, "liquid")
    return lost


def _nonstick_check(ctx, vessel, tool_id):
    w = ctx.w
    vrec = w.ent(vessel)
    if tool_id and vrec.get("coating") == "nonstick" and w.ent(tool_id).get("material") in ("steel", "stainless_steel", "metal", "iron"):
        m = w.mut(vessel)
        m["coating_damage"] = round(min(1.0, float(m.get("coating_damage", 0.0)) + 0.2), 3)
        M.add_damage(m, "scratched_coating", m["coating_damage"])
        w.stat("damage")
        w.emit("nonstick_scratched", target=vessel, tool=tool_id)


def _mix_common(ctx, capability, method_default, verb):
    w = ctx.w
    vessel = ctx.args["target"]
    if vessel in w.s["entities"] and M.food(w.ent(vessel)):
        vessel = w.ent(vessel).get("location")
    _vessel_contents(ctx, vessel)
    tool_id = C.tool(ctx, capability, ctx.args.get("tool"))
    trec = w.ent(tool_id)
    vrec = w.ent(vessel)
    if trec.get("melt_c") is not None and float(vrec.get("temperature_c", 21.0)) >= float(trec["melt_c"]):
        m = w.mut(tool_id)
        m["broken"] = True
        m["condition"] = "melted"
        w.stat("damage")
    _nonstick_check(ctx, vessel, tool_id)
    eid = X.combine(w, vessel)
    require(eid, "There is nothing to mix", "precondition")
    strokes = int(ctx.args.get("strokes") or 20)
    speed = ctx.args.get("speed") or "medium"
    tool_factor = float(trec.get("mix_factor", 1.0))
    if trec.get("powered"):
        tool_factor *= 4.0
    work = strokes * SPEED.get(speed, 1.0) * tool_factor
    method = ctx.args.get("method") or method_default
    mix = X.apply_work(w, eid, work, method)
    M.contact(w, tool_id, eid, 0.6)
    M.contact(w, vessel, eid, 0.3)
    lost = _splash(ctx, vessel, eid, speed, tool_id)
    m = w.mut(eid)
    m["food"]["last_stirred_s"] = w.s["time_s"]
    X.log(w, vessel, verb, method=method, work=round(work, 1), kinds=sorted((m["food"].get("composition") or {}).keys()))
    M.update_fill(w, vessel)
    return vessel, eid, tool_id, mix, strokes, lost


@action("whisk", params={"target": "entity", "tool": "?entity", "strokes": {"type": "int", "default": 60, "min": 1, "max": 3000},
                         "speed": "?str:slow|medium|fast", "method": "?str:whisk|beat|whip|emulsify|fold"},
        duration=C.duration(30.0), irreversible=True, operates=True, noise=0.15,
        doc="Whisk/beat/whip the contents of a bowl with a held whisk or fork: beat eggs until no white strands, whip cream "
            "or whites to soft (0.45) / stiff (0.7) peaks and stop before over-whipping (>1.05), emulsify a vinaigrette. "
            "Use a deep bowl; fast whisking in a shallow or over-full vessel splashes.")
@P.perceived_result
def whisk(ctx):
    vessel, eid, tool_id, mix, strokes, lost = _mix_common(ctx, "whisk", "whisk", "whisk")
    C.seconds(ctx, 3.0 + strokes / 3.0)
    return {"target": vessel, "mixture": eid, "state": X.state_words(ctx.w.ent(eid)), "splashed_g": round(lost, 1)}


@action("mix", params={"target": "entity", "tool": "?entity", "strokes": {"type": "int", "default": 30, "min": 1, "max": 3000},
                       "speed": "?str:slow|medium|fast", "method": "?str:stir|fold|beat|cream|cut_in"},
        duration=C.duration(30.0), irreversible=True, operates=True, noise=0.1,
        doc="Combine everything in a bowl (spatula/spoon/whisk): stir, fold (gentle, keeps air), beat, cream (soft butter + "
            "sugar), cut_in (cold fat into flour). Batter is done when no dry pockets remain; more work develops gluten "
            "(muffins turn tough above 0.35). Order of additions is recorded for recipe checks.")
@P.perceived_result
def mix(ctx):
    vessel, eid, tool_id, state, strokes, lost = _mix_common(ctx, "mix", "stir", "mix")
    C.seconds(ctx, 3.0 + strokes / 2.0)
    return {"target": vessel, "mixture": eid, "state": X.state_words(ctx.w.ent(eid)), "splashed_g": round(lost, 1)}


@action("stir", params={"target": "entity", "tool": "?entity", "strokes": {"type": "int", "default": 10, "min": 1, "max": 600},
                        "speed": "?str:slow|medium|fast", "scrape_bottom": {"type": "bool", "default": True}},
        duration=C.duration(10.0), irreversible=True, operates=True, noise=0.05,
        doc="Stir a bowl or pot with a heat-safe utensil: evens out temperature (no cold centre after microwaving), stops "
            "scorching on the heat (stir every ~3 min). Scraping an already-burnt bottom mixes burnt flavour through. Fast "
            "stirring of a full pot sloshes; metal utensils scratch nonstick.")
@P.perceived_result
def stir(ctx):
    w = ctx.w
    vessel = ctx.args["target"]
    if vessel in w.s["entities"] and M.food(w.ent(vessel)):
        vessel = w.ent(vessel).get("location")
    _vessel_contents(ctx, vessel)
    tool_id = C.tool(ctx, "stir", ctx.args.get("tool"))
    trec, vrec = w.ent(tool_id), w.ent(vessel)
    if trec.get("melt_c") is not None and float(vrec.get("temperature_c", 21.0)) >= float(trec["melt_c"]):
        m = w.mut(tool_id)
        m["broken"] = True
        m["condition"] = "melted"
        w.stat("damage")
        for fid in M.vessel_foods(w.s, vessel):
            M.add_contam(w, fid, "chemical", 0.3)
    _nonstick_check(ctx, vessel, tool_id)
    strokes = int(ctx.args["strokes"])
    speed = ctx.args.get("speed") or "medium"
    foods = M.vessel_foods(w.s, vessel, "in")
    wet = [f for f in foods if (M.food(w.ent(f)) or {}).get("phase") in ("liquid", "paste") and not w.ent(f).get("package")]
    if wet and float(vrec.get("liquid_l", 0.0) or 0.0) > 0:
        # water poured into the pot (tap/kettle) is stirred through the soup/sauce: it dilutes salt and thins it
        X.absorb_vessel_water(w, vessel, max(wet, key=lambda f: (M.qty(w.ent(f)), f)))
    total = sum(M.qty(w.ent(f)) for f in foods) or 1.0
    t_mean = sum((float(w.ent(f).get("temperature_c", 21.0)) + float(w.ent(f).get("_core_c", w.ent(f).get("temperature_c", 21.0)))) / 2.0 * M.qty(w.ent(f)) for f in foods) / total
    out = {"target": vessel}
    for fid in foods:
        m = w.mut(fid)
        f = m["food"]
        mixfrac = 1.0 - math.exp(-strokes / 8.0)
        t = float(m.get("temperature_c", 21.0))
        core = float(m.get("_core_c", t))
        m["temperature_c"] = round(t + (t_mean - t) * mixfrac, 3)
        m["_core_c"] = round(core + (t_mean - core) * mixfrac, 3)
        f["last_stirred_s"] = w.s["time_s"]
        if float(f.get("foam_g", 0.0)) > 0.5:
            f["foam_g"] = 0.0
            f["cloudy"] = True
        if ctx.args["scrape_bottom"] and float(f.get("scorch", 0.0)) >= 0.3:
            f["burnt_flavor"] = True
            out["warning"] = "burnt layer scraped into the food"
        elif ctx.args["scrape_bottom"]:
            f["scorch"] = 0.0
        M.contact(w, tool_id, fid, 0.5)
        if f.get("phase") in ("liquid", "paste") and f.get("mix") is not None:
            X.apply_work(w, fid, strokes * SPEED.get(speed, 1.0), "stir")
    if foods:
        fill = float(vrec.get("fill_level", 0.4))
        if speed == "fast" and fill > 0.75:
            lost = 0.03 * total
            M.set_qty(w, foods[0], M.qty(w.ent(foods[0])) - lost)
            M.spill(w, vessel, M.kind(w.ent(foods[0])), lost, "liquid")
            out["sloshed_g"] = round(lost, 1)
    X.log(w, vessel, "stir", strokes=strokes)
    out["temperature_c"] = round(t_mean, 1)
    C.seconds(ctx, 2.0 + strokes * 0.8)
    return out


@action("knead", params={"target": "entity", "minutes": {"type": "number", "default": 8, "min": 0.5, "max": 30},
                         "added_flour_g": {"type": "number", "default": 0, "min": 0, "max": 500}},
        duration=C.duration(480.0), irreversible=True, operates=True, noise=0.1, timing="commanded",
        doc="Knead dough by hand on a lightly floured surface with both hands free (~8-10 min). Gluten develops until it "
            "passes the windowpane test; extra flour lowers hydration (dry, stiff dough). Cover it afterwards to proof.")
@P.perceived_result
def knead(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = C.food_target(ctx, eid)
    f = rec["food"]
    require(f.get("phase") == "dough", "Only dough is kneaded", "argument")
    require(all(v is None for v in w.robot.get("hands", {}).values()), "Knead with both hands free", "capability")
    minutes = float(ctx.args["minutes"])
    added = float(ctx.args["added_flour_g"])
    if added > 0:
        m = w.mut(eid)
        comp = m["food"].setdefault("composition", {})
        flour_kind = next((k for k in comp if k in X.FLOURS), "all_purpose_flour")
        comp[flour_kind] = round(comp.get(flour_kind, 0.0) + added, 2)
        M.set_qty(w, eid, M.qty(m) + added)
        X.reclassify(w, eid)
        surface = rec.get("location")
        if surface in w.s["entities"]:
            add_soil(w, surface, "dust", min(0.3, 0.02 + added / 400.0))
    X.apply_work(w, eid, minutes * 60.0, "knead")
    C.touch_food(ctx, eid, 0.5)
    C.soil_hands(ctx, "food", 0.4, hands="both")              # dough sticks to both hands (body.soil_hand)
    if added > 0:
        C.soil_hands(ctx, "flour", 0.1, hands="both")
    m = w.mut(eid)
    mf = m["food"]
    g = float(mf["mix"].get("gluten", 0.0))
    target = float(mf.get("target_hydration", 0.65))
    hyd = float(mf.get("hydration", target))
    mf["windowpane"] = D.GLUTEN_WINDOWPANE[0] <= g <= D.GLUTEN_WINDOWPANE[1] and abs(hyd - target) <= 0.03 + 1e-9
    mf["kneaded_min"] = round(float(mf.get("kneaded_min", 0.0)) + minutes, 2)
    if hyd < target - 0.05:
        mf["texture"] = "dry and stiff"
    C.seconds(ctx, minutes * 60.0)
    return {"target": eid, "gluten": round(g, 3), "hydration": round(hyd, 3), "state": X.state_words(w.ent(eid))}


@action("roll_dough", params={"target": "entity", "tool": "?entity", "thickness_mm": {"type": "number", "default": 4.0, "min": 1.0, "max": 30.0},
                              "rotate": {"type": "bool", "default": True}, "flour_g": {"type": "number", "default": 8, "min": 0, "max": 200},
                              "passes": {"type": "int", "default": 10, "min": 1, "max": 60}},
        duration=C.duration(120.0), irreversible=True, operates=True, noise=0.05,
        doc="Roll dough on a floured surface with a held rolling pin to an even thickness, rotating a quarter turn between "
            "passes (else an oval with thin edges). Butter-rich pastry above ~15 C sticks and tears; heavy flouring toughens.")
@P.perceived_result
def roll_dough(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = C.food_target(ctx, eid)
    f = rec["food"]
    require(f.get("phase") == "dough", "Only dough is rolled out", "argument")
    surface, kind = C.resting_surface(w.s, eid)
    require(kind in ("counter", "board", "surface"), "Roll it out on a counter or board", "precondition")
    pin = C.tool(ctx, "roll", ctx.args.get("tool"))
    C.free_hand_for_steadying(ctx, "guide the pin")
    thickness = float(ctx.args["thickness_mm"])
    passes = int(ctx.args["passes"])
    flour = float(ctx.args["flour_g"])
    vol_ml = M.volume_ml(rec)
    needed = max(4, int(round(math.log(max(1.5, 40.0 / thickness), 1.25))))
    achieved = thickness * (1.0 + max(0, needed - passes) * 0.15)
    diameter = 2.0 * math.sqrt(vol_ml * 1000.0 / (math.pi * achieved))
    comp = f.get("composition") or {}
    fat_frac = sum(v for k, v in comp.items() if k in X.FATS) / max(1.0, sum(comp.values()))
    temp = float(rec.get("temperature_c", 21.0))
    sheet = {"thickness_mm": round(achieved, 2), "diameter_mm": round(diameter, 1),
             "thickness_cv": 0.05 if ctx.args["rotate"] else 0.25, "shape": "round" if ctx.args["rotate"] else "oval",
             "holes": False, "stuck": False, "tough": flour > 30.0}
    if fat_frac > 0.2 and temp > 15.0:
        sheet.update(holes=True, stuck=True)
    if flour < 2.0 and fat_frac <= 0.2:
        sheet["stuck"] = True
    m = w.mut(eid)
    m["food"]["sheet"] = sheet
    m["food"]["cut"] = "rolled"
    m["size_m"] = [round(min(0.6, diameter / 1000.0), 3), round(min(0.6, diameter / 1000.0 * (1.0 if ctx.args["rotate"] else 0.75)), 3), round(achieved / 1000.0, 4)]
    if flour:
        comp = m["food"].setdefault("composition", {})
        comp["all_purpose_flour"] = round(comp.get("all_purpose_flour", 0.0) + flour * 0.5, 2)
        if surface in w.s["entities"]:
            add_soil(w, surface, "dust", min(0.3, 0.02 + flour / 300.0))
    C.tool_touch(ctx, pin, eid, 0.4)
    C.seconds(ctx, 10.0 + passes * 6.0)
    return {"target": eid, "sheet": sheet}


@action("shape", params={"target": "entity", "form": "str:rolls|loaf|round|balls|baguette|wrap|burrito|taco",
                         "count": {"type": "int", "default": 1, "min": 1, "max": 48}, "destination": "?entity",
                         "method": "?str:scale|eyeball", "spacing_mm": {"type": "number", "default": 50, "min": 0, "max": 200},
                         "seam": "?str:down|up", "fold_ends": {"type": "bool", "default": True}},
        duration=C.duration(60.0), irreversible=True, operates=True, noise=0.03,
        doc="Divide and shape dough into count pieces on a tray (method=scale weighs each piece: +/-2 %; eyeballing drifts "
            "+/-20 %), seam down, laid out spacing_mm apart; or fold a warmed tortilla around the filling resting on it "
            "(wrap/burrito with ends folded, taco). Cold tortillas crack; overfilling bursts.")
@P.perceived_result
def shape(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = C.food_target(ctx, eid)
    f = rec["food"]
    form = ctx.args["form"]
    if form in ("wrap", "burrito", "taco"):
        return _wrap(ctx, eid, rec, form)
    require(f.get("phase") == "dough", "Shape dough (or fold a tortilla)", "argument")
    dest = ctx.args.get("destination")
    require(dest, "Name the tray to shape onto", "argument")
    drec = ctx.obj(dest, touch=True)
    require(drec.get("surface") or drec.get("accepts_items"), "Shape onto a tray or board", "argument")
    count = int(ctx.args["count"])
    method = ctx.args.get("method") or "eyeball"
    if method == "scale":
        require(any(w.ent(e).get("tool_kind") == "kitchen_scale" and w.ent(e).get("on") for e in sorted(w.s["entities"])
                    if ctx.visible(e) and geometry.reach_problem(w.s, e) is None and not P.unfamiliar(ctx.env, e)),
                "A switched-on kitchen scale within reach is needed", "precondition")
    total = M.qty(rec)
    if float(f.get("skin", 0.0)) > 0.6:
        tear = True
    else:
        tear = False
    weights = []
    for i in range(count):
        if method == "scale":
            dev = 0.02 * (1 if i % 2 == 0 else -1) * (0.5 + 0.5 * ((i * 7) % 3) / 2.0)
        else:
            dev = 0.22 * (1.0 - 2.0 * i / max(1, count - 1)) if count > 1 else 0.0
        weights.append(total / count * (1.0 + dev))
    scale_fix = total / sum(weights)
    weights = [g * scale_fix for g in weights]
    spacing = float(ctx.args["spacing_mm"]) / 1000.0
    sw, sd = (drec.get("surface_size_m") or (drec.get("size_m") or [0.4, 0.3])[:2])[:2]
    pieces = []
    x, y = 0.01, 0.01
    row_h = 0.0
    seam = ctx.args.get("seam") or "down"
    for i, g in enumerate(weights):
        vol_m3 = g / M.density(rec) / 1e6
        side = round(max(0.03, (vol_m3 / 0.6) ** (1.0 / 3.0) * 1.2), 3)
        if x + side > sw + 1e-9:
            x = 0.01
            y += row_h + spacing
            row_h = 0.0
        if y + side > sd + 1e-9:
            raise ActionError("Not enough room on the tray at that spacing", "precondition")
        portion = M.portion_record(w.s, rec, g)
        portion["food"].update({"cut": form, "seam": seam, "shaped": True, "torn": tear})
        portion["size_m"] = [side, side, round(side * 0.6, 3)]
        portion["label"] = "{} ({})".format(rec.get("label", "dough"), form)
        pid = M.create_from_record(w, portion, dest, "on")
        w.mut(pid)["spos"] = [round(x, 3), round(y, 3)]
        pieces.append(pid)
        x += side + spacing
        row_h = max(row_h, side)
    for pid in pieces:
        _gap(w, pid, pieces)
    M.destroy_food(w, eid)
    C.soil_hands(ctx, "food", 0.15, hands="both")             # shaping by hand leaves dough on the fingers
    C.seconds(ctx, 15.0 * count + (10.0 * count if method == "scale" else 0.0))
    return {"pieces": pieces, "grams": [round(g, 1) for g in weights], "torn": tear}


def _gap(w, pid, group):
    rec = w.ent(pid)
    x, y = rec["spos"]
    sx, sy = geometry.footprint_m(rec)
    best = 1.0
    for other in group:
        if other == pid:
            continue
        o = w.ent(other)
        ox, oy = o["spos"]
        osx, osy = geometry.footprint_m(o)
        dx = max(0.0, max(ox - (x + sx), x - (ox + osx)))
        dy = max(0.0, max(oy - (y + sy), y - (oy + osy)))
        best = min(best, math.hypot(dx, dy) if dx and dy else max(dx, dy))
    w.mut(pid)["food"]["gap_mm"] = round(best * 1000.0, 1)


def _wrap(ctx, eid, rec, form):
    w = ctx.w
    f = rec["food"]
    require(f.get("wrapper"), "Fold a tortilla or taco shell", "argument")
    fillings = [c for c in w.children(eid) if M.food(w.ent(c))]
    require(fillings, "Put the filling on it first", "precondition")
    require(ctx.free_hand() is not None, "You need a free hand", "capability")
    vol = sum(M.volume_ml(w.ent(c)) for c in fillings)
    cap = float(f.get("wrap_capacity_ml", 300.0)) * (0.5 if form == "taco" else 1.0)
    out = {"target": eid, "form": form}
    m = w.mut(eid)
    mf = m["food"]
    pliable = float(rec.get("temperature_c", 21.0)) >= 35.0 or f.get("warmed")
    if f.get("hard_shell"):
        if vol > cap:
            mf["cracked"] = True
            m["broken"] = True
            out["warning"] = "the hard shell cracked"
    elif not pliable:
        mf["cracked"] = True
        out["warning"] = "the cold tortilla cracked while folding"
    if vol > cap * 1.05:
        mf["burst"] = True
        M.spill(w, eid, "filling", (vol - cap) * 0.8, "paste")
        out["warning"] = "overfilled; it burst"
    mf["form"] = form
    mf["closed"] = form != "taco"
    mf["ends_folded"] = bool(ctx.args["fold_ends"]) and form != "taco"
    mf["seam"] = ctx.args.get("seam") or "down"
    mf["fill_ml"] = round(vol, 1)
    C.touch_food(ctx, eid, 0.4)
    C.seconds(ctx, 20.0)
    return out


# ----------------------------------------------------------------------
# strain, sift

MESH_MM = {"colander": 4.0, "sieve": 1.0, "fine_mesh_sieve": 0.5, "strainer": 1.0, "cheesecloth": 0.1, "flour_sifter": 1.0}


@action("strain", params={"target": "entity", "destination": "entity", "tool": "?entity", "keep": "?str:solids|liquid",
                          "reserve_ml": {"type": "number", "default": 0, "min": 0, "max": 2000}, "reserve_to": "?entity",
                          "pour_direction": "?str:away|toward"},
        duration=C.duration(30.0), irreversible=True, operates=True, noise=0.2,
        doc="Pour a pot/bowl through a colander, sieve or cheesecloth set in the destination (a bowl to keep the liquid, or "
            "the sink to discard it). Solids larger than the mesh stay in the strainer; reserve_ml of liquid can be kept back "
            "into reserve_to first. Fat/grease must never go down a drain.")
@P.perceived_result
def strain(ctx):
    w = ctx.w
    src = ctx.args["target"]
    srec = ctx.obj(src, touch=True)
    require(srec.get("container"), "Strain from a pot or bowl", "argument")
    require(ctx.holding(src) or float(srec.get("mass_kg", 1.0)) < 25, "Lift the pot to pour", "capability")
    dest = ctx.args["destination"]
    drec = ctx.obj(dest, touch=True)
    tool_id = ctx.args.get("tool")
    if tool_id:
        trec = ctx.obj(tool_id, touch=True)
    else:
        tool_id = next((c for c in w.children(dest) if w.ent(c).get("tool_kind") in MESH_MM), None) or \
            next((h for h in C.held_known(ctx) if w.ent(h).get("tool_kind") in MESH_MM), None)
        require(tool_id, "Set a colander or sieve in the sink/bowl first", "precondition")
        trec = w.ent(tool_id)
    require(trec.get("tool_kind") in MESH_MM, "That is not a strainer", "argument")
    require(trec.get("location") == dest or ctx.holding(tool_id), "Set the strainer in the destination", "precondition")
    mesh = MESH_MM[trec["tool_kind"]]
    to_drain = bool(drec.get("drain"))
    out = {"target": src}
    foods = M.vessel_foods(w.s, src, "in")
    water_l = float(srec.get("liquid_l", 0.0) or 0.0)
    reserve = float(ctx.args["reserve_ml"])
    if reserve > 0:
        rto = ctx.args.get("reserve_to")
        require(rto, "Name a container for the reserved liquid", "argument")
        C.vessel_reach(ctx, rto)
        take = min(reserve / 1000.0, water_l)
        if take > 0:
            r = w.mut(rto)
            r["liquid"] = srec.get("liquid") or "water"
            r["liquid_l"] = round(float(r.get("liquid_l", 0.0) or 0.0) + take, 4)
            r["liquid_temp_c"] = srec.get("temperature_c", 21.0)
            mark_contents_from(w, rto, src)        # provenance: where the reserved liquid came from (kernel v2.9)
            if srec.get("dissolved_starch"):
                r["dissolved_starch"] = True
            water_l -= take
            out["reserved_ml"] = round(take * 1000.0, 1)
    passed_liquid_g = water_l * 1000.0
    solids_kept = []
    fat_g = 0.0
    for fid in foods:
        rec = w.ent(fid)
        f = rec["food"]
        size = min(f.get("piece_mm") or [v * 1000 for v in (rec.get("size_m") or [0.05, 0.05, 0.05])])
        if f.get("phase") == "liquid" or f.get("kind") in ("fat", "bacon_grease", "oil", "olive_oil", "vegetable_oil") or size <= mesh:
            grams = M.qty(rec)
            if f.get("kind") in ("fat", "bacon_grease", "oil", "olive_oil", "vegetable_oil") or (f.get("composition") or {}).get("fat"):
                fat_g += grams if f.get("kind") in ("fat", "bacon_grease", "oil", "olive_oil", "vegetable_oil") else float(f["composition"]["fat"])
            if to_drain:
                M.destroy_food(w, fid)
            else:
                portion, grams = M.take(w, fid, grams)
                X.add(w, portion, dest, "in")
                M.destroy_food(w, fid) if M.qty(w.ent(fid)) <= 0 else None
            passed_liquid_g += grams
        else:
            m = w.mut(fid)
            m["location"], m["relation"] = tool_id, "in"
            for key in ("spos", "shelf"):
                m.pop(key, None)
            m["food"]["drained"] = True
            m["food"]["wet"] = 0.2
            solids_kept.append(fid)
            M.contact(w, tool_id, fid, 0.4)
    if not to_drain and water_l > 0:
        mark_contents_from(w, dest, src)       # before src is emptied: its own provenance carries over
    s = w.mut(src)
    s["liquid_l"] = 0.0
    s["liquid"] = None
    clear_contents_from(w, src)
    if not to_drain and water_l > 0:
        d = w.mut(dest)
        d["liquid"] = srec.get("liquid") or "water"
        d["liquid_l"] = round(float(d.get("liquid_l", 0.0) or 0.0) + water_l, 4)
    if to_drain and fat_g > 0:
        d = w.mut(dest)
        d["_fat_g"] = round(float(d.get("_fat_g", 0.0)) + fat_g, 1)
        w.emit("fat_down_drain", target=dest, grams=round(fat_g, 1))
    if not to_drain:
        cap = M.capacity_ml(drec)
        held = M.vessel_food_ml(w.s, dest)
        if cap > 0 and held > cap:
            over = held - cap
            out["overflow_ml"] = round(over, 1)
            for fid in M.vessel_foods(w.s, dest, "in"):
                if M.is_liquid(w.ent(fid)):
                    M.set_qty(w, fid, max(0.0, M.qty(w.ent(fid)) - over))
                    break
            M.spill(w, dest, "liquid", over, "liquid")
        M.update_fill(w, dest)
    if (ctx.args.get("pour_direction") or "away") == "toward" and float(srec.get("temperature_c", 21.0)) > 80:
        w.emit("steam_toward_robot", target=src)
        out["warning"] = "steam engulfed the robot's arm"
    out.update({"solids_in_strainer": solids_kept, "liquid_ml_passed": round(passed_liquid_g, 1), "to_drain": to_drain})
    C.seconds(ctx, 15.0 + passed_liquid_g / 200.0)
    return out


@action("sift", params={"target": "entity", "destination": "entity", "tool": "?entity",
                        "amount_g": {"type": "number", "default": 0, "min": 0, "max": 3000}},
        duration=C.duration(40.0), irreversible=True, operates=True, noise=0.05,
        doc="Sift flour/cocoa/powdered sugar through a held sifter or sieve into the destination bowl: lumps > 1 mm are "
            "removed and the powder is aerated (lighter per cup: '1 cup sifted flour' ~110 g vs '1 cup flour, sifted' 125 g).")
@P.perceived_result
def sift(ctx):
    w = ctx.w
    src = ctx.args["target"]
    srec = ctx.obj(src, touch=True)
    if not M.food(srec):
        foods = [f for f in M.vessel_foods(w.s, src) if M.food(w.ent(f)).get("phase") in ("powder", "granular")]
        require(foods, "Nothing to sift in it", "precondition")
        src = foods[0]
        srec = w.ent(src)
    C.require_open(srec, ctx=ctx, eid=src)
    require(M.food(srec).get("phase") in ("powder", "granular"), "Only dry powders are sifted", "argument")
    tool_id = C.tool(ctx, "sift", ctx.args.get("tool"))
    dest = ctx.args["destination"]
    drec = C.vessel_reach(ctx, dest)
    amount = float(ctx.args["amount_g"] or 0) or M.qty(srec)
    portion, grams = M.take(w, src, amount)
    pf = portion["food"]
    pf["lumps"] = 0
    pf["sifted"] = True
    k = pf.get("kind")
    pf["density"] = D.DENSITY.get(k, {}).get("sifted", round(float(pf.get("density", 0.55)) * 0.88, 3))
    spill = 0.0
    sw = float((w.ent(tool_id).get("size_m") or [0.15])[0])
    dw = float((drec.get("size_m") or [0.25])[0])
    if sw > dw:
        spill = 0.04 * grams
        pf["qty_g"] = round(grams - spill, 2)
        M.spill(w, dest, k, spill, "powder")
    new = X.add(w, portion, dest, "in")
    M.contact(w, tool_id, new, 0.4)
    C.seconds(ctx, 10.0 + C.pour_ml(srec, grams) / 27.0)     # by volume (~ grams / 15 for flour)
    return {"sifted": new, "grams": round(grams - spill, 1), "spilled_g": round(spill, 1), "_subject": new}


# ----------------------------------------------------------------------
# marinate, season, coat

@action("marinate", params={"target": "entity", "marinade": "entity", "destination": "entity",
                            "amount_ml": {"type": "number", "default": 0, "min": 0, "max": 2000}},
        duration=C.duration(30.0), irreversible=True, operates=True, noise=0.02,
        doc="Put food and marinade together in a non-reactive container or zip bag (destination); then close it and "
            "refrigerate. Acid turns fish mushy after ~1 h; aluminium reacts with acid; raw-meat marinade becomes "
            "contaminated and must be discarded or boiled before serving.")
@P.perceived_result
def marinate(ctx):
    w = ctx.w
    fid = ctx.args["target"]
    rec = C.food_target(ctx, fid)
    mid = ctx.args["marinade"]
    mrec = ctx.obj(mid, touch=True)
    if not M.food(mrec):
        liquids = [f for f in M.vessel_foods(w.s, mid) if M.is_liquid(w.ent(f))]
        require(liquids, "There is no marinade in it", "precondition")
        mid = liquids[0]
        mrec = w.ent(mid)
    require(M.is_liquid(mrec) or M.food(mrec).get("phase") == "paste", "Use a liquid or paste marinade", "argument")
    dest = ctx.args["destination"]
    drec = C.vessel_reach(ctx, dest)
    require(drec.get("container"), "Marinate in a container or bag", "argument")
    if rec.get("location") != dest:
        require(ctx.holding(fid) or ctx.free_hand() is not None, "You need a free hand", "capability")
        from ..core import release_from_hands
        release_from_hands(w, fid)
        m = w.mut(fid)
        m["location"], m["relation"] = dest, "in"
        for key in ("spos", "pos", "shelf", "held_hand"):
            m.pop(key, None)
    amount = float(ctx.args["amount_ml"] or 0) or M.volume_ml(mrec)
    known = ctx.identified(mid)      # an unidentified liquid's acidity is not seen
    portion, grams = M.take(w, mid, amount * M.density(mrec))
    comp = portion["food"].get("composition") or {}
    acid = bool(set(comp) & X.ACIDS) or bool(portion["food"].get("acid"))
    marinade_id = M.deposit(w, portion, dest, "in", merge=False)
    M.contact(w, fid, marinade_id, 0.8)
    M.contact(w, dest, fid, 0.5)
    fvol = M.volume_ml(w.ent(fid))
    coverage = min(1.0, grams / M.density(w.ent(marinade_id)) / max(1.0, 0.25 * fvol))
    if drec.get("category") in ("bag", "food_bag"):
        coverage = min(1.0, coverage * 2.0)
    m = w.mut(fid)
    m["food"]["marinade"] = {"kind": portion["food"].get("kind"), "acid": acid, "start_s": w.s["time_s"],
                             "elapsed_s": 0.0, "coverage": round(coverage, 3), "container": dest}
    if acid and drec.get("material") in ("aluminum", "aluminium", "copper", "cast_iron"):
        m["food"]["taint"] = "metallic"
    mm = w.mut(marinade_id)
    mm["food"]["raw_marinade"] = M.is_raw_animal(w.ent(fid))
    mm["food"]["rte"] = False if M.is_raw_animal(w.ent(fid)) else mm["food"].get("rte", True)
    M.update_fill(w, dest)
    C.touch_food(ctx, fid, 0.4)
    C.seconds(ctx, 25.0)
    out = {"target": fid, "container": dest, "coverage": round(coverage, 3), "_subject": mid}
    if known:
        out["acid"] = acid
    return out


SHAKE_G = {"salt": 0.12, "kosher_salt": 0.1, "ground_black_pepper": 0.05, "black_pepper": 0.05, "sugar": 0.15}
PINCH_G = {"salt": 0.36, "kosher_salt": 0.3, "ground_black_pepper": 0.1, "sugar": 0.4}


@action("season", params={"target": "entity", "seasoning": "entity", "amount_g": {"type": "number", "default": 0, "min": 0, "max": 200},
                          "pinches": {"type": "int", "default": 0, "min": 0, "max": 50}, "shakes": {"type": "int", "default": 0, "min": 0, "max": 100},
                          "turns": {"type": "int", "default": 0, "min": 0, "max": 200}, "tool": "?entity"},
        duration=C.duration(5.0), irreversible=True, operates=True, noise=0.02,
        doc="Season a food, a pot or a single plated portion from a shaker (shakes), pepper mill (turns), salt cellar "
            "(pinches) or jar with a held measuring spoon (amount by the spoon's volume). What goes in is whatever the "
            "container really holds. A loose shaker lid dumps; shaking over a steaming pot clumps the shaker.")
@P.perceived_result
def season(ctx):
    w = ctx.w
    tid = ctx.args["target"]
    trec = ctx.obj(tid, touch=True)
    targets = [tid] if M.food(trec) else M.dish_foods(w.s, tid)
    require(targets, "There is no food there to season", "precondition")
    sid = ctx.args["seasoning"]
    srec = ctx.obj(sid, touch=True)
    require(M.food(srec) and M.food(srec).get("phase") in ("powder", "granular", "liquid", "paste"), "That is not a seasoning", "argument")
    require(ctx.holding(sid) or ctx.args.get("tool"), "Hold the shaker/mill, or scoop with a held measuring spoon", "precondition")
    k = M.kind(srec)
    grams = float(ctx.args.get("amount_g") or 0)
    out = {"target": tid}
    spoon = ctx.args.get("tool")
    if spoon:
        spoon_rec = w.ent(spoon)
        require(ctx.holding(spoon), "Hold the spoon", "capability")
        C.require_open(srec, ctx=ctx, eid=sid)
        ml = float(spoon_rec.get("measure_ml") or spoon_rec.get("bowl_ml") or 5.0)
        grams = ml * D.DENSITY.get(k, {}).get("default", M.density(srec)) * (grams / grams if grams else 1.0)
        M.contact(w, spoon, sid, 0.4)
    elif ctx.args.get("shakes"):
        grams = SHAKE_G.get(k, 0.1) * int(ctx.args["shakes"])
    elif ctx.args.get("turns"):
        grams = 0.04 * int(ctx.args["turns"])
    elif ctx.args.get("pinches"):
        grams = PINCH_G.get(k, 0.3) * int(ctx.args["pinches"])
    require(grams > 0, "Say how much (shakes, turns, pinches, amount_g or a measuring spoon)", "argument")
    if srec.get("_loose_lid") and not spoon:
        dumped = min(M.qty(srec), max(15.0, 0.3 * M.qty(srec)))
        grams += dumped
        out["warning"] = "the lid came off and dumped {}".format(P.grams_text(ctx, sid, "dumped_g", dumped))
        m = w.mut(sid)
        m["_loose_lid"] = False
    if srec.get("clumped"):
        grams *= 0.3
    grams = min(grams, M.qty(srec))
    hot_steam = any(float(w.ent(t).get("temperature_c", 21.0)) > 80 for t in targets) and not M.food(trec) and trec.get("container")
    if hot_steam and srec.get("shaker") and not spoon:
        w.mut(sid)["clumped"] = True
    portion, grams = M.take(w, sid, grams)
    each = grams / len(targets)
    pk = portion["food"].get("kind")
    for t in targets:
        m = w.mut(t)
        comp = m["food"].setdefault("composition", {m["food"].get("kind", "food"): M.qty(m)})
        comp[pk] = round(comp.get(pk, 0.0) + each, 3)
        m["food"]["qty_g"] = round(M.qty(m) + each, 3)
        m["mass_kg"] = round(M.tare_kg(m) + m["food"]["qty_g"] / 1000.0, 4)
        m["allergens"] = sorted(set(m.get("allergens", [])) | set(srec.get("allergens", [])))
        m["diet_tags"] = sorted(set(m.get("diet_tags", [])) | set(srec.get("diet_tags", [])))
        seasoned = dict(m["food"].get("seasoned") or {})
        seasoned[pk] = round(seasoned.get(pk, 0.0) + each, 3)
        m["food"]["seasoned"] = seasoned
        for key, val in sorted((srec.get("contamination") or {}).items()):
            M.add_contam(w, t, key, val * 0.3)
    if not M.food(trec) and trec.get("container") and float(trec.get("liquid_l", 0.0) or 0.0) > 0 and len(targets) == 0:
        pass
    if not targets and trec.get("container"):
        w.mut(tid)["dissolved_salt_g"] = round(float(trec.get("dissolved_salt_g", 0.0)) + grams, 3)
    if spoon:
        for t in targets:
            M.contact(w, spoon, t, 0.3)
    # (realistic perception: what the seasoning is shows only once the jar/shaker has been identified)
    out.update({"added_g": round(grams, 3), "seasoning": sid if ctx.unresolved(sid) else pk,
                "_subject": sid})
    C.seconds(ctx, 3.0 + 0.3 * (int(ctx.args.get("shakes") or 0) + int(ctx.args.get("turns") or 0)))
    return out


@action("coat", params={"target": "entity", "coating": "entity", "method": "?str:dredge|dip|toss|brush|rub|spread|press|grease",
                        "tool": "?entity", "amount_g": {"type": "number", "default": 0, "min": 0, "max": 500},
                        "shake_off": {"type": "bool", "default": True}},
        duration=C.duration(12.0), irreversible=True, operates=True, noise=0.02,
        doc="Coat food (or grease a pan/tray) from a coating source: dredge in flour, dip in egg wash or batter, press into "
            "crumbs, toss in oil/sauce, brush, rub spices, or spread butter/peanut butter/jam/frosting with a held knife or "
            "spatula (method=spread). Pat wet food dry first; flour before egg before crumbs; cold butter tears bread; "
            "frosting melts on a warm cake. The coating source picks up raw-meat/allergen tags from what it touches.")
@P.perceived_result
def coat(ctx):
    w = ctx.w
    tid = ctx.args["target"]
    trec = ctx.obj(tid, touch=True)
    cid = ctx.args["coating"]
    crec = ctx.obj(cid, touch=True)
    if not M.food(crec):
        srcs = M.vessel_foods(w.s, cid)
        require(srcs, "There is no coating in it", "precondition")
        cid = srcs[0]
        crec = w.ent(cid)
    C.require_open(crec, ctx=ctx, eid=cid)
    method = ctx.args.get("method") or "dredge"
    ck = M.kind(crec)
    out = {"target": tid, "method": method}
    if not M.food(trec):
        require(method in ("grease", "brush", "spread", "rub"), "Only grease/brush a pan or tray", "argument")
        require(trec.get("category") in ("cookware", "bakeware") or trec.get("conducts_heat") or trec.get("surface"), "Grease cookware or bakeware", "argument")
        grams = float(ctx.args.get("amount_g") or 5.0)
        portion, grams = M.take(w, cid, grams)
        if not ctx.args.get("tool") and method in ("grease", "rub"):
            C.soil_hands(ctx, C.coating_soil(crec) or "oil", 0.3)     # butter or oil rubbed on with the fingers
        m = w.mut(tid)
        m["greased"] = True
        m["grease_g"] = round(float(m.get("grease_g", 0.0)) + grams, 2)
        out["greased"] = True
        C.seconds(ctx, 15.0)
        return out
    tf = trec["food"]
    units = max(1, int(tf.get("count", 1) or 1))
    pieces = max(units, int(tf.get("pieces", 1) or 1))
    if method == "spread":
        tool_id = C.tool(ctx, "spread", ctx.args.get("tool"))
        grams = float(ctx.args.get("amount_g") or 10.0 * pieces)
        temp = float(crec.get("temperature_c", 21.0))
        coverage, torn = 0.95, False
        if ck in ("butter", "salted_butter", "unsalted_butter") and temp < 15.0:
            coverage, torn = 0.45, bool(tf.get("tearable", tf.get("cat") == "bread"))
        if tf.get("cake") and float(trec.get("temperature_c", 21.0)) > 30.0 and ck in ("frosting", "buttercream", "cream_cheese_frosting"):
            coverage = 0.4
            out["warning"] = "frosting melted on the warm cake"
        if grams < 4.0 * pieces:
            coverage = min(coverage, 0.5)
        M.contact(w, tool_id, cid, 0.6)
        M.contact(w, tool_id, tid, 0.6)
    else:
        if method in ("dredge", "press", "dip", "toss", "rub"):
            require(ctx.free_hand() is not None or ctx.holding(tid), "You need a free hand", "capability")
        grams_per_piece = {"dredge": 2.0, "dip": 4.0, "press": 5.0, "toss": 1.5, "brush": 1.0, "rub": 1.5}.get(method, 2.0)
        grams = float(ctx.args.get("amount_g") or grams_per_piece * pieces * (1.0 if ctx.args["shake_off"] else 2.5))
        coverage = 0.95
        torn = False
        wet = float(tf.get("wet", 0.0)) > 0.3
        layers = [l.get("kind") for l in tf.get("layers", [])]
        cphase = M.food(crec).get("phase")
        if method in ("dredge",) and wet:
            coverage = 0.65
            out["warning"] = "the coating turned gummy on the wet surface"
        if method in ("dip",) and ck in ("raw_egg", "beaten_egg", "egg_wash") and not any(l in ("all_purpose_flour", "flour", "cornstarch") for l in layers):
            coverage = 0.5
            out["warning"] = "egg slid off without a flour layer"
        if method == "press" and ck in ("breadcrumbs", "panko") and not any(l in ("raw_egg", "beaten_egg", "egg_wash", "batter") for l in layers):
            coverage = 0.3
            out["warning"] = "crumbs will not stick without an egg layer"
        if method == "dip" and ck == "batter":
            mix = M.food(crec).get("mix") or {}
            visc = float(M.food(crec).get("hydration", 1.3))
            coverage = 0.95 if 1.1 <= visc <= 1.8 else 0.6
            if not ctx.args["shake_off"]:
                M.spill(w, tid, "batter", 3.0 * pieces, "liquid")
                out["warning"] = "batter dripped on the counter"
            if mix.get("overmixed"):
                out["note"] = "over-mixed batter: gluey coating"
        if not ctx.args["shake_off"] and cphase in ("powder", "granular"):
            out["note"] = "thick clumps of coating"
        C.touch_food(ctx, tid, 0.4)
        C.touch_food(ctx, cid, 0.3)
        if method != "brush" and not ctx.args.get("tool"):
            # dredging, dipping, pressing into crumbs, tossing or rubbing by hand coats the fingers too
            C.soil_hands(ctx, C.coating_soil(crec) or "food", 0.25 if method in ("dredge", "press") else 0.3,
                         hands=ctx.hand_of(tid))
    portion, used = M.take(w, cid, grams)
    m = w.mut(tid)
    mf = m["food"]
    layers = list(mf.get("layers", []))
    layers.append({"kind": ck, "_src": cid, "coverage": round(coverage, 3), "g": round(used, 2), "method": method,
                   "barrier": ck in ("butter", "salted_butter", "unsalted_butter", "mayonnaise", "cream_cheese", "peanut_butter", "almond_butter"),
                   "excess": not ctx.args["shake_off"] and method != "spread"})
    mf["layers"] = layers
    if torn:
        mf["torn"] = True
        out["warning"] = "the bread tore"
    comp = mf.setdefault("composition", {mf.get("kind", "food"): M.qty(m)})
    comp[ck] = round(comp.get(ck, 0.0) + used, 3)
    mf["qty_g"] = round(M.qty(m) + used, 3)
    m["mass_kg"] = round(M.tare_kg(m) + mf["qty_g"] / 1000.0, 4)
    m["allergens"] = sorted(set(m.get("allergens", [])) | set(crec.get("allergens", [])))
    m["diet_tags"] = sorted(set(m.get("diet_tags", [])) | set(crec.get("diet_tags", [])))
    if ck in ("lemon_juice", "lime_juice", "acidulated_water"):
        mf["acid_treated"] = True
    if ck in ("dressing", "vinaigrette") and mf.get("cat") == "leafy_green":
        mf["dressed"] = True
    if M.is_raw_animal(w.ent(cid)) or M.food(w.ent(cid)).get("has_raw_animal"):
        mf["has_raw_animal"] = True
    M.contact(w, tid, cid, 0.6)
    out.update({"coverage": round(coverage, 3), "used_g": round(used, 2), "_subject": cid,
                "layers": [P.layer_name(ctx.env, l) for l in layers] if ctx.realistic else [l["kind"] for l in layers]})
    C.seconds(ctx, 6.0 + 4.0 * pieces)
    return out
