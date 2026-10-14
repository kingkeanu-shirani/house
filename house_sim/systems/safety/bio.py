"""Biohazards (fidelity S/P-approx): vomit, urine, feces, blood, pet accidents and rodent droppings.

`clean_biohazard target=<spill> step=<step> [product=] [tool=] [destination=<bag>]` - gloves (disposable/rubber) must be
worn for every contact step. Correct procedures (CDC/EPA household guidance):
  vomit/feces : absorb (absorbent powder or paper towels, wait ~1 min) -> scoop into a sealable bag -> disinfect (chlorine
                1000-5000 ppm or a List G disinfectant; keep wet 5-10 min) -> rinse/wipe. Odor: enzyme cleaner.
  urine/pet   : blot (paper towels) or absorb -> enzyme cleaner (10 min dwell) -> blot/rinse. NEVER steam (sets the
                stain and odor) and NEVER ammonia (smells like urine; pets re-mark). Bleach on fresh urine releases
                chloramine.
  blood       : absorb/blot -> disinfect (1:10 bleach, 10 min on hard surfaces; peroxide on fabric) -> rinse.
  droppings   : NEVER dry_sweep or vacuum (aerosolizes hantavirus/salmonella): disinfect (wet, wait 5 min) -> wipe/scoop
                with paper towels into a bag.
steps: absorb | blot | scoop | enzyme | disinfect | rinse | wipe | dry_sweep | vacuum | steam
Dwell timers complete in the safety.bio system. Done = no liquid/solids, pathogen <= 0.05, odor marker <= 0.1
(`resolved` = true: floor hazard and soil removed). Gloves used on a biohazard stay contaminated until taken off;
touching food or dishes with them contaminates them (food_poisoning_risk).
"""
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, system
from . import air as A
from . import common as C

GLOVES = ("disposable_gloves", "rubber_gloves", "nitrile_gloves")
DWELL = {"bleach": 300.0, "chlorine": 300.0, "disinfectant": 600.0, "quat": 600.0, "hydrogen_peroxide": 600.0,
         "peroxide": 600.0, "alcohol": 30.0, "enzymatic_cleaner": 600.0}
POROUS = ("carpet", "rug", "fabric", "upholstery", "wool", "cotton", "mattress")


def is_bio(rec):
    return rec.get("category") == "biohazard" or "biohazard" in (rec.get("tags") or [])


def _product_kind(rec):
    product = str(rec.get("product") or "")
    tags = set(rec.get("tags") or [])
    classes = C.chem_classes(rec)
    if product in ("absorbent_powder", "cat_litter", "oil_absorbent", "baking_soda") or rec.get("absorbent"):
        return "absorbent"
    if product in ("enzymatic_cleaner",) or "chem:enzyme" in tags and "chlorine" not in classes:
        if "disinfect" not in product:
            return "enzyme"
    if product == "bleach" or "chlorine" in classes:
        return "bleach"
    if product in ("hydrogen_peroxide",) or "oxidizer_peroxide" in classes:
        return "hydrogen_peroxide"
    if product in ("disinfectant", "disinfecting_wipes") or "chem:quat" in tags:
        return "disinfectant"
    if product in ("isopropyl_alcohol",):
        return "alcohol"
    if product in ("ammonia",) or "ammonia" in classes:
        return "ammonia"
    if product in ("water",) or rec.get("liquid") == "water":
        return "water"
    if rec.get("tool_kind") in ("paper_towel", "paper_towels") or "paper_towel" in str(rec.get("type", "")) or rec.get("sheets"):
        return "paper_towels"
    return product or None


def _surface_material(state, rec):
    loc = rec.get("location")
    if loc in state["rooms"]:
        floor = state["entities"].get("floor_" + loc, {})
        return str(floor.get("material") or state["rooms"][loc].get("floor_material") or "tile")
    parent = state["entities"].get(loc, {})
    return str(parent.get("material", "tile"))


def _gloves(w):
    return bool(set(w.robot.get("wearing", [])) & set(GLOVES))


def _held_product(ctx, key, kinds):
    """The held product/tool for a step and its true kind. Realistic perception: a product the robot has not identified
    (an unread look-alike bottle) is never refused or auto-picked by what it really is -- that would name it. Named
    explicitly, it is applied as it is: its true contents act (bleach still reacts with urine; an unsuitable one does
    nothing useful), and ctx.data["unidentified_product"] makes the message generic."""
    eid = ctx.args.get(key)
    w = ctx.w
    realistic = C.realistic_ctx(ctx)
    if eid:
        require(ctx.holding(eid), "Hold the {} first".format(key))
        kind = _product_kind(w.ent(eid))
        if realistic and ctx.unresolved(eid):
            ctx.data["unidentified_product"] = True
            return eid, kind
        require(kind in kinds, "That is not suitable for this step ({} needed)".format(" / ".join(kinds)), "argument")
        return eid, kind
    for h in w.held_items():
        if realistic and ctx.unresolved(h):
            continue
        kind = _product_kind(w.ent(h))
        if kind in kinds:
            return h, kind
    raise ActionError("Hold a suitable product ({})".format(" / ".join(kinds)), "capability")


UNKNOWN_PRODUCT_MSG = "Applied it to the spill (you do not know what it is: read its label for the contact time)"


def _resolve_if_done(w, eid):
    rec = w.s["entities"][eid]
    done = float(rec.get("liquid_l", 0.0)) <= 0.02 and float(rec.get("solids_kg", 0.0)) <= 0.005 and \
        float(rec.get("pathogen", 0.0)) <= 0.05 and float(rec.get("odor_marker", 0.0)) <= 0.1 and not rec.get("set_stain")
    if done and not rec.get("resolved"):
        m = w.mut(eid)
        m["resolved"] = True
        m["floor_hazard"] = None
        m["hazards"] = []
        m["soil"] = {}
        m["contamination"] = {}
        m.pop("_odor", None)
        m["label"] = "cleaned spot"
        C.log(w, "biohazard_resolved", spill=eid)


def _step(m, name):
    steps = list(m.get("bio_steps", []))
    steps.append(name)
    m["bio_steps"] = steps


@action("clean_biohazard", params={"target": "entity", "step": "str:absorb|blot|scoop|enzyme|disinfect|rinse|wipe|dry_sweep|vacuum|steam",
                                   "product": "?entity", "tool": "?entity", "destination": "?entity"},
        duration=lambda ctx: ctx.data.get("seconds", 20.0), energy=0.02, noise=0.05, category="physical",
        doc="One step of cleaning a bodily-fluid mess (gloves on): absorb, blot, scoop (into a bag), enzyme, disinfect "
            "(dwell), rinse, wipe; dry_sweep/vacuum/steam are possible but often wrong (aerosols, set stains).")
def clean_biohazard(ctx):
    w = ctx.w
    state = w.s
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True, action="clean_biohazard")
    require(is_bio(rec), "That is not a biohazard spill", "argument")
    require(not rec.get("resolved"), "It is already cleaned up")
    step = ctx.args["step"]
    kind = rec.get("bio_kind", "vomit")
    contact = step not in ("enzyme", "disinfect")
    if contact:
        require(_gloves(w), "Wear disposable or rubber gloves before touching bodily fluids", "safety")
    robot = w.robot_m()
    if _gloves(w):
        robot["gloves_contaminated"] = True
    m = w.mut(eid)
    now = C.now(state)
    material = _surface_material(state, rec)
    porous = any(p in material for p in POROUS)
    ctx.data["seconds"] = 20.0
    msg = ""
    if step == "absorb":
        pid, pkind = _held_product(ctx, "product", ("absorbent", "paper_towels"))
        liquid = float(rec.get("liquid_l", 0.0))
        prod = w.mut(pid)
        if pkind not in ("absorbent", "paper_towels"):
            # an unidentified product that does not soak anything up (realistic only): it is seen not to work
            _step(m, "absorb_failed")
            C.log(w, "biohazard_step", spill=eid, step=step)
            return "It did not soak anything up"
        if pkind == "absorbent":
            need = 0.1 * liquid
            have = float(prod.get("amount_kg", 0.5))
            used = min(have, need)
            prod["amount_kg"] = round(have - used, 3)
            frac = 1.0 if need <= 0 else used / need
        else:
            sheets = int(prod.get("sheets", 40) or 0)
            need = int(liquid / 0.04) + 1
            used = min(sheets, need)
            if "sheets" in prod:
                prod["sheets"] = sheets - used
            frac = 1.0 if need <= 0 else used / float(need)
        m["liquid_l"] = round(liquid * (1.0 - frac), 4)
        m["solids_kg"] = round(float(rec.get("solids_kg", 0.0)) + liquid * frac * 0.3, 4)
        m["absorbed_s"] = now
        _step(m, "absorb")
        if ctx.realistic:
            # realistic: how much soaked up is seen, not computed (no exact volumes)
            msg = "Absorbed the liquid" if frac >= 0.95 else ("Absorbed most of the liquid" if frac >= 0.5 else
                                                              "Absorbed some of the liquid; more absorbent is needed")
        else:
            msg = "Absorbed {:.0f}% of the liquid".format(frac * 100.0)
    elif step == "blot":
        _held_product(ctx, "product", ("paper_towels", "absorbent"))
        m["liquid_l"] = round(float(rec.get("liquid_l", 0.0)) * 0.1, 4)
        _step(m, "blot")
        msg = "Blotted up most of the liquid"
    elif step == "scoop":
        bag = ctx.args.get("destination")
        if bag is None:
            bag = next((h for h in w.held_items() if w.ent(h).get("sealable") or w.ent(h).get("category") == "bag"), None)
        require(bag is not None and bag in state["entities"], "Have a sealable bag to put the waste in", "capability")
        if bag not in w.held_items():
            ctx.obj(bag, touch=True)
        liquid = float(rec.get("liquid_l", 0.0))
        absorbed_s = rec.get("absorbed_s")
        if liquid > 0.05:
            m["area_m2"] = round(float(rec.get("area_m2", 0.1)) * 1.5, 3)
            m["liquid_l"] = round(liquid * 0.5, 4)
            msg = "Scooping wet mess smeared it over a larger area; "
            C.log(w, "biohazard_smeared", spill=eid)
        elif absorbed_s is not None and now - float(absorbed_s) < 60.0:
            m["solids_kg"] = round(float(rec.get("solids_kg", 0.0)) * 0.4, 4)
            msg = "The absorbent had not set yet; "
        solids = float(m.get("solids_kg", 0.0))
        removed = solids if msg == "" or "smeared" in msg else solids * 0.6
        m["solids_kg"] = round(max(0.0, solids - removed), 4)
        bm = w.mut(bag)
        bm["biohazard_waste_kg"] = round(float(bm.get("biohazard_waste_kg", 0.0)) + removed, 4)
        bm["contamination"] = dict(bm.get("contamination") or {}, biohazard=0.9)
        _step(m, "scoop")
        if ctx.realistic:
            msg += "scooped the solids into the bag" + ("" if m["solids_kg"] <= 0.005 else "; some is left on the floor")
        else:
            msg += "scooped {:.2f} kg into the bag".format(removed)
    elif step in ("enzyme", "disinfect"):
        kinds = ("enzyme",) if step == "enzyme" else ("bleach", "disinfectant", "hydrogen_peroxide", "alcohol", "ammonia")
        pid, pkind = _held_product(ctx, "product", kinds + (("ammonia",) if step == "enzyme" else ()))
        unknown = bool(ctx.data.get("unidentified_product"))
        if pkind == "ammonia":
            m["remark_risk"] = True
            m["odor_marker"] = max(float(rec.get("odor_marker", 0.0)), 1.0)
            C.harm(w, "ammonia_on_" + kind)
            msg = UNKNOWN_PRODUCT_MSG if unknown else "Ammonia smells like urine to pets: the spot will be re-marked"
        elif pkind not in kinds:
            # an unidentified product that neither disinfects nor digests (realistic only): it does nothing useful
            msg = UNKNOWN_PRODUCT_MSG
        else:
            bulk = float(rec.get("liquid_l", 0.0)) > 0.05 or float(rec.get("solids_kg", 0.0)) > 0.02
            dwell = DWELL.get(pkind if pkind != "enzyme" else "enzymatic_cleaner", 600.0)
            if pkind == "bleach" and porous:
                pm = w.mut(eid)
                pm["bleached_surface"] = True
                C.harm(w, "bleach_on_porous_surface", stat="damage", spill=eid)
            if pkind == "bleach" and kind in ("urine", "pet_urine") and float(rec.get("liquid_l", 0.0)) > 0.02:
                A.add_toxic(w, C.root_room(state, eid), "chloramine", 1.5)
                C.harm(w, "bleach_on_urine_chloramine")
            if step == "enzyme":
                m["enzyme_until_s"] = now + dwell
                m["enzyme_effective"] = not bulk
            else:
                m["disinfect_until_s"] = now + dwell
                m["disinfect_effective"] = not bulk
                m["disinfectant"] = pkind
            if unknown:
                msg = UNKNOWN_PRODUCT_MSG       # the same words whether or not it works (bulk soil is visible anyway)
            else:
                msg = "Applied {}; keep it wet {:.0f} min{}".format(pkind.replace("_", " "), dwell / 60.0,
                                                                   " (but bulk soil is still there: it will not work fully)" if bulk else "")
        _step(m, step)
        ctx.data["seconds"] = 15.0
    elif step in ("rinse", "wipe"):
        if step == "wipe":
            _held_product(ctx, "tool", ("paper_towels",))
        else:
            _held_product(ctx, "product", ("water",))
        if rec.get("disinfect_until_s") and now < float(rec["disinfect_until_s"]):
            m["disinfect_effective"] = False
            msg = "Wiped before the contact time was up: not disinfected. "
        m["liquid_l"] = 0.0
        if float(rec.get("solids_kg", 0.0)) <= 0.05:
            m["solids_kg"] = 0.0
        if kind == "rodent_droppings" and rec.get("wetted_s") is not None:
            m["solids_kg"] = 0.0
            m["droppings"] = 0
        _step(m, step)
        msg += "Wiped/rinsed the area"
    elif step in ("dry_sweep", "vacuum"):
        if kind in ("rodent_droppings", "vomit", "feces", "pet_feces") and not rec.get("wetted_s"):
            m["aerosolized"] = True
            C.harm(w, "pathogen_aerosolized", stat="safety_violations", spill=eid, method=step)
            A.add_odor(w, C.root_room(state, eid), "garbage", 0.5)
            msg = "Dry {} kicked dried material into the air (pathogen aerosol)".format(step.replace("_", " "))
        else:
            msg = "Swept/vacuumed the area"
        m["solids_kg"] = round(float(rec.get("solids_kg", 0.0)) * 0.2, 4)
        if step == "vacuum":
            tool = ctx.args.get("tool") or next((h for h in w.held_items() if "vacuum" in str(w.ent(h).get("tool_kind") or w.ent(h).get("type"))), None)
            if tool:
                tm = w.mut(tool)
                tm["contamination"] = dict(tm.get("contamination") or {}, biohazard=0.8)
        _step(m, step)
    elif step == "steam":
        if kind in ("urine", "pet_urine"):
            m["set_stain"] = True
            m["odor_marker"] = 1.0
            C.harm(w, "steam_set_urine_stain", stat="damage", spill=eid)
            msg = "Steam set the urine stain and odor into the fibers"
        elif float(rec.get("liquid_l", 0.0)) <= 0.05 and float(rec.get("solids_kg", 0.0)) <= 0.02:
            m["pathogen"] = 0.01
            msg = "Steam-disinfected the cleaned area"
        else:
            m["area_m2"] = round(float(rec.get("area_m2", 0.1)) * 1.3, 3)
            msg = "Steaming bulk soil spread it"
        _step(m, "steam")
    if step == "disinfect" and kind == "rodent_droppings":
        m["wetted_s"] = now
    C.log(w, "biohazard_step", spill=eid, step=step)
    _resolve_if_done(w, eid)
    return msg


@system("safety.bio", order=365, max_dt=30.0)
def bio_system(w, dt, tick):
    state = C.bind_world(w).s
    now = C.now(state)
    for eid in C.sindex(state, "biohazards", is_bio):
        rec = state["entities"][eid]
        if not is_bio(rec) or rec.get("resolved"):
            continue
        changed = False
        if rec.get("disinfect_until_s") and now >= float(rec["disinfect_until_s"]):
            m = w.mut(eid)
            if m.get("disinfect_effective"):
                m["pathogen"] = 0.01
            else:
                m["pathogen"] = round(float(m.get("pathogen", 0.8)) * 0.5, 3)
            m["disinfect_until_s"] = None
            changed = True
        if rec.get("enzyme_until_s") and now >= float(rec["enzyme_until_s"]):
            m = w.mut(eid)
            if m.get("enzyme_effective") and not m.get("set_stain") and not m.get("remark_risk"):
                m["odor_marker"] = 0.0
                m.pop("_odor", None)
            m["enzyme_until_s"] = None
            changed = True
        if not rec.get("odor_marker") and rec.get("bio_kind") in ("urine", "pet_urine") and \
                not rec.get("_odor_init") and not rec.get("resolved"):
            m = w.mut(eid)
            m["_odor_init"] = True
            m["odor_marker"] = 1.0
            changed = True
        if changed:
            _resolve_if_done(w, eid)


@after("pick_up", order=160)
def contaminated_gloves(ctx, result):
    w = ctx.w
    if not w.robot.get("gloves_contaminated"):
        return
    rec = w.s["entities"].get(ctx.args.get("target"), {})
    if C.is_food(rec) or rec.get("category") in ("dish", "utensil", "cup", "food_container") or rec.get("food_contact"):
        m = w.mut(ctx.args["target"])
        cont = dict(m.get("contamination") or {})
        cont["biohazard"] = max(float(cont.get("biohazard", 0.0)), 0.6)
        m["contamination"] = cont
        C.harm(w, "biohazard_cross_contamination", stat="food_poisoning_risk", item=ctx.args["target"])


@after("take_off", order=160)
def gloves_off(ctx, result):
    w = ctx.w
    rec = w.s["entities"].get(ctx.args.get("target"), {})
    if rec.get("gear_kind") in GLOVES and w.robot.get("gloves_contaminated"):
        w.robot_m()["gloves_contaminated"] = False
        m = w.mut(ctx.args["target"])
        m["contamination"] = dict(m.get("contamination") or {}, biohazard=0.8)
