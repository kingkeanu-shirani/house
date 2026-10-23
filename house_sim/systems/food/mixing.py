"""Mixture model (P-approx): composition, homogeneity, aeration, gluten, emulsion and phase.

A vessel holds food entities. Liquids/powders/pastes deposited into a vessel that already holds a
liquid/paste/dough merge with it at once (you cannot pick an egg back out of batter). Stirring,
mixing and whisking merge everything that is not a whole solid item into one mixture entity
whose kind and phase follow its composition. Work done (strokes x intensity) drives:

* homogeneity  h = 1 - (1 - h0) exp(-W / k), k grows with viscosity, folding is gentle;
* gluten       grows with work x hydration (beating >> stirring >> folding); muffin batter is
               tough above 0.35, bread dough passes the windowpane test at 0.75-1.15;
* aeration     egg whites/cream: soft peaks 0.45-0.70, stiff 0.70-0.95, over-whipped > 1.05
               (cream splits into butter and buttermilk, whites go grainy). Any yolk fat in the
               whites (> 0.1 % of mass) caps aeration at 0.25; warm cream (> 10 C) caps at 0.4;
* emulsion     oil + acid: stable minutes ~ work/20 (x4 with mustard);
* creaming     butter + sugar aerates only when the butter is soft (16-22 C).
"""
import math

from ..core import clear_contents_from
from . import data as D
from . import model as M

EGG_PARTS = {"egg_white", "egg_yolk", "raw_egg", "egg"}
LIQUIDS = {"milk", "water", "buttermilk", "heavy_cream", "oat_milk", "almond_milk", "stock", "beer", "lemon_juice",
           "orange_juice", "lime_juice", "vinegar", "soy_sauce", "wine", "juice", "egg_white", "egg_yolk", "raw_egg",
           "beaten_egg", "yogurt", "coconut_milk"}
FLOURS = {"all_purpose_flour", "bread_flour", "cake_flour", "whole_wheat_flour", "flour", "cornstarch", "masa"}
FATS = {"butter", "salted_butter", "unsalted_butter", "olive_oil", "vegetable_oil", "oil", "shortening"}
ACIDS = {"vinegar", "lemon_juice", "lime_juice", "red_wine_vinegar", "balsamic_vinegar"}
WATER_FRACTION = {"milk": 0.87, "water": 1.0, "buttermilk": 0.89, "heavy_cream": 0.58, "egg_white": 0.88,
                  "egg_yolk": 0.5, "raw_egg": 0.75, "beaten_egg": 0.75, "oat_milk": 0.9, "almond_milk": 0.95,
                  "stock": 0.95, "beer": 0.92, "yogurt": 0.85, "lemon_juice": 0.92, "orange_juice": 0.88}


def classify(comp):
    """(kind, phase, hydration) for a composition dict {kind: grams}."""
    total = sum(comp.values()) or 1.0
    keys = {k for k, v in comp.items() if v > 0.002 * total}
    flour = sum(v for k, v in comp.items() if k in FLOURS)
    water = sum(v * WATER_FRACTION.get(k, 0.0) for k, v in comp.items())
    fat = sum(v for k, v in comp.items() if k in FATS)
    if keys <= EGG_PARTS:
        if keys == {"egg_white"}:
            return "egg_whites", "liquid", 0
        if keys == {"egg_yolk"}:
            return "egg_yolks", "liquid", 0
        return "beaten_egg", "liquid", 0
    if keys <= {"heavy_cream", "sugar", "powdered_sugar", "vanilla"} and "heavy_cream" in keys:
        return "cream", "liquid", 0
    if flour > 0:
        hydration = water / flour
        if hydration >= 0.85:
            return "batter", "liquid", hydration
        if "yeast" in keys and hydration >= 0.5:
            return "bread_dough", "dough", hydration
        if hydration >= 0.35 or fat / flour > 0.4:
            return "dough", "dough", hydration
        return "dry_mix", "powder", hydration
    if keys & FATS and keys & ACIDS and not flour:
        return "vinaigrette", "liquid", 0
    if keys <= {"butter", "unsalted_butter", "salted_butter", "sugar", "brown_sugar", "powdered_sugar", "vanilla"} and keys & FATS:
        return "creamed_butter", "paste", 0
    if water / total > 0.45:
        return "mixture", "liquid", 0
    if water / total < 0.05:
        return "dry_mix", "powder", 0
    return "mixture", "paste", 0


def log(w, vessel, op, **data):
    if vessel not in w.s["entities"]:
        return
    m = w.mut(vessel)
    entry = {"op": op, "t": w.s["time_s"]}
    entry.update(data)
    logs = list(m.get("_mix_log", []))
    logs.append(entry)
    m["_mix_log"] = logs[-80:]


def absorb_vessel_water(w, vessel, into):
    """Plain water poured by plumbing into a vessel joins the liquid food in it."""
    rec = w.ent(vessel)
    liters = float(rec.get("liquid_l", 0.0) or 0.0)
    if liters <= 0 or rec.get("liquid") not in ("water", None, "ice_water"):
        return
    grams = liters * 1000.0
    m = w.mut(into)
    f = m["food"]
    total = M.qty(m) + grams
    f.setdefault("composition", {})["water"] = round(f["composition"].get("water", 0.0) + grams, 2)
    if f.get("salt_in_water_g"):
        pass
    t_food = float(m.get("temperature_c", 21.0))
    t_water = float(rec.get("liquid_temp_c", rec.get("temperature_c", 21.0)))
    m["temperature_c"] = round((t_food * M.qty(m) + t_water * grams) / total, 3)
    f["qty_g"] = round(total, 2)
    m["mass_kg"] = round(total / 1000.0, 4)
    m["volume_l"] = round(total / M.density(m) / 1000.0, 4)
    v = w.mut(vessel)
    salt = float(v.get("dissolved_salt_g", 0.0))
    if salt:
        f["composition"]["salt"] = round(f["composition"].get("salt", 0.0) + salt, 3)
        v["dissolved_salt_g"] = 0.0
    v["liquid_l"] = 0.0
    v["liquid"] = None
    clear_contents_from(w, vessel)          # the vessel's own liquid is gone (it joined the food)
    log(w, vessel, "add", kind="water", g=round(grams, 1))


def mixable(rec):
    f = M.food(rec) or {}
    return f.get("phase") in ("liquid", "granular", "powder", "paste", "dough", "pieces") and not rec.get("package")


def combine(w, vessel):
    """Merge mixable food in a vessel into one entity; returns its id (or None)."""
    state = w.s
    foods = [f for f in M.vessel_foods(state, vessel, "in") if mixable(state["entities"][f])]
    if not foods:
        return None
    foods.sort(key=lambda e: (-M.qty(state["entities"][e]), e))
    main = foods[0]
    for other in foods[1:]:
        before_h = float((M.food(state["entities"][main]).get("mix") or {}).get("h", 0.0))
        g_main, g_other = M.qty(state["entities"][main]), M.qty(state["entities"][other])
        M.merge_into(w, main, other)
        M.destroy_food(w, other)
        m = w.mut(main)
        mix = dict(m["food"].get("mix") or {})
        mix["h"] = round(before_h * g_main / max(1.0, g_main + g_other), 4)
        m["food"]["mix"] = mix
    absorb_vessel_water(w, vessel, main)
    reclassify(w, main)
    return main


def reclassify(w, eid):
    m = w.mut(eid)
    f = m["food"]
    comp = f.get("composition") or {f.get("kind", "food"): M.qty(m)}
    if len([k for k, v in comp.items() if v > 0]) <= 1 and f.get("kind") in comp:
        return
    kind, phase, hydration = classify(comp)
    if f.get("kind") in ("whipped_cream", "butter_and_buttermilk", "meringue") and kind == "cream":
        return
    f["kind"] = kind
    f["phase"] = phase if not (phase == "liquid" and f.get("phase") == "pieces" and _solid_share(comp) > 0.5) else f.get("phase")
    f["cat"] = "dough" if phase == "dough" else "mixture"
    if hydration:
        f["hydration"] = round(hydration, 3)
    f["density"] = {"liquid": 1.03, "powder": 0.55, "paste": 1.05, "dough": 1.1}.get(f["phase"], f.get("density", 1.0))
    f["cut"] = "mixed"
    m["label"] = kind.replace("_", " ")
    m["perishable"] = bool(m.get("perishable")) or bool(set(comp) & (EGG_PARTS | {"milk", "heavy_cream", "buttermilk", "yogurt"}))
    m["volume_l"] = round(M.qty(m) / f["density"] / 1000.0, 4)
    m["size_m"] = M.portion_size(m)
    M.conform_to_vessel(w, eid)


def _solid_share(comp):
    total = sum(comp.values()) or 1.0
    return sum(v for k, v in comp.items() if k not in LIQUIDS) / total


def apply_work(w, eid, work, method, temp_c=None):
    """Advance the mixture state machine by work units (strokes x intensity)."""
    m = w.mut(eid)
    f = m["food"]
    mix = dict(f.get("mix") or {})
    comp = f.get("composition") or {f.get("kind", "food"): M.qty(m)}
    total = sum(comp.values()) or 1.0
    phase = f.get("phase")
    k_h = {"liquid": 25.0, "powder": 30.0, "paste": 50.0, "dough": 90.0}.get(phase, 40.0)
    if method == "fold":
        k_h *= 3.0
    h0 = float(mix.get("h", 0.0))
    mix["h"] = round(1.0 - (1.0 - h0) * math.exp(-work / k_h), 4)
    kind = f.get("kind")
    temp = float(m.get("temperature_c", 21.0)) if temp_c is None else temp_c
    if kind in ("egg_whites",) or (set(comp) <= {"egg_white", "sugar", "cream_of_tartar"} and "egg_white" in comp):
        fat = float(f.get("yolk_fat_g", 0.0)) + float((m.get("contamination") or {}).get("fat", 0.0)) * 10
        cap = 0.25 if fat / total > 0.001 else 2.0
        rate = 0.0009 * (1.2 if temp > 15 else 1.0)    # ~800 balloon-whisk strokes to stiff peaks by hand
        a = min(cap, float(mix.get("aeration", 0.0)) + work * rate * (0.2 if method == "fold" else 1.0))
        mix["aeration"] = round(a, 4)
        if a > D.OVERWHIP:
            mix["overwhipped"] = True
            mix["grainy"] = True
    elif kind in ("cream",) or (set(comp) <= {"heavy_cream", "sugar", "powdered_sugar", "vanilla"} and "heavy_cream" in comp):
        cap = 0.4 if temp > 10 else 2.0
        a = min(cap, float(mix.get("aeration", 0.0)) + work * 0.0012 * (0.2 if method == "fold" else 1.0))
        mix["aeration"] = round(a, 4)
        if a > D.OVERWHIP:
            mix["overwhipped"] = True
            f["kind"] = "butter_and_buttermilk"
            f["split"] = True
            m["label"] = "split cream (butter and buttermilk)"
        elif a >= 0.45:
            f["kind"] = "whipped_cream"
            m["label"] = "whipped cream"
    elif kind == "vinaigrette":
        emulsifier = 4.0 if "mustard" in comp or "egg_yolk" in comp else 1.0
        mix["emulsion_stable_min"] = round(min(60.0, float(mix.get("emulsion_stable_min", 0.0)) + work / 20.0 * emulsifier), 2)
        mix["emulsified_at_s"] = w.s["time_s"]
    elif kind == "creamed_butter":
        butter_temp_ok = 16.0 <= temp <= 23.0
        if butter_temp_ok:
            mix["aeration"] = round(min(1.0, float(mix.get("aeration", 0.0)) + work * 0.004), 4)
        elif temp < 16.0:
            mix["lumpy_butter"] = True
        else:
            mix["greasy"] = True
        mix["creamed"] = mix.get("aeration", 0.0) >= 0.5
    flour = sum(v for k, v in comp.items() if k in FLOURS)
    if flour > 0 and method == "knead":
        # hand kneading develops gluten toward a well-developed (windowpane) dough in ~8-10 min; it rarely over-works
        g0 = float(mix.get("gluten", 0.0))
        mix["gluten"] = round(g0 + (1.1 - g0) * (1.0 - math.exp(-work / 300.0)), 4) if g0 < 1.1 else g0
    elif flour > 0:
        hydration = float(f.get("hydration", 0.0))
        rate = {"beat": 0.003, "stir": 0.0012, "whisk": 0.0018, "fold": 0.0003, "knead": 0.011, "cut_in": 0.0002, "cream": 0.002}.get(method, 0.0012)
        hyd = min(1.5, max(0.0, hydration)) / 0.65 if hydration else 0.3
        mix["gluten"] = round(float(mix.get("gluten", 0.0)) + work * rate * min(1.6, hyd), 4)
        if f.get("kind") == "batter" or f.get("phase") == "liquid":
            mix["dry_pockets"] = mix["h"] < D.HOMOGENEOUS
            mix["overmixed"] = mix["gluten"] > D.GLUTEN_MUFFIN_MAX
    if f.get("kind") == "beaten_egg":
        mix["white_strands"] = mix["h"] < D.HOMOGENEOUS
    if mix.get("broken_emulsion"):
        water = float(comp.get("water", 0.0)) + float(comp.get("lemon_juice", 0.0))
        if water >= 0.01 * total and work >= 30 and float(m.get("temperature_c", 21.0)) < 70:
            mix["broken_emulsion"] = False
            mix["restored"] = True
            if m.get("_defect") == "broken_sauce":
                m["_defect_fixed"] = True
    if method == "fold" and "aeration" in mix:
        pass
    elif method in ("stir", "beat", "mix") and mix.get("aerated_component"):
        mix["aeration"] = round(float(mix.get("aeration", 0.0)) * 0.4, 4)
    f["mix"] = mix
    return mix


def state_words(rec):
    f = M.food(rec) or {}
    mix = f.get("mix") or {}
    words = []
    if "aeration" in mix:
        a = float(mix["aeration"])
        if mix.get("overwhipped"):
            words.append("over-whipped: grainy/split")
        elif a >= 0.7:
            words.append("stiff peaks")
        elif a >= 0.45:
            words.append("soft peaks")
        elif a >= 0.15:
            words.append("foamy")
        else:
            words.append("liquid")
    if mix.get("h") is not None:
        words.append("smooth and uniform" if mix["h"] >= D.HOMOGENEOUS else "streaky / not fully combined")
    if mix.get("dry_pockets"):
        words.append("dry flour pockets")
    if mix.get("overmixed"):
        words.append("tough, rubbery (over-mixed)")
    if "emulsion_stable_min" in mix:
        words.append("emulsified" if mix["emulsion_stable_min"] >= 5 else "separating")
    if mix.get("lumpy_butter"):
        words.append("lumps of cold butter")
    if mix.get("greasy"):
        words.append("greasy, melted butter")
    if f.get("phase") == "dough":
        g = float(mix.get("gluten", 0.0))
        words.append("smooth and elastic" if D.GLUTEN_WINDOWPANE[0] <= g <= D.GLUTEN_WINDOWPANE[1] else
                     "shaggy and tears easily" if g < D.GLUTEN_WINDOWPANE[0] else "tight, overworked")
    if f.get("shell_fragments"):
        words.append("{} shell fragment(s) visible".format(int(f["shell_fragments"])))
    if float(f.get("yolk_fat_g", 0.0)) > 0:
        words.append("streaks of yolk in the whites")
    return ", ".join(words)


def _finished(f):
    """A food that is a dish, drink or sauce in its own right (soup, stew, juice, sour cream, ketchup ...) rather than a
    mixing base the classifier knows (water, milk, cream, eggs, flour, fats, a batter, a dough or a mixture)."""
    kind = f.get("kind")
    return bool(kind) and f.get("cat") not in ("mixture", "dough") and not (
        kind in WATER_FRACTION or kind in LIQUIDS or kind in FLOURS or kind in FATS or kind in EGG_PARTS or kind in ACIDS)


def add(w, rec, vessel, relation=None):
    """Deposit a portion into a vessel/surface. Liquids, powders and pastes poured into a vessel that
    already holds a liquid/paste/dough join it immediately (they cannot be picked back out)."""
    state = w.s
    parent = state["entities"].get(vessel)
    f = rec["food"]
    if relation is None:
        relation = "in" if parent is None or parent.get("container") else "on"
    if parent is not None and relation == "in" and f.get("kind") == "ice" and (float(parent.get("liquid_l", 0.0) or 0.0) > 0.3 or parent.get("ice_bath")):
        # ice into a bowl of water makes an ice bath: a 0 C sink with strong coupling for whatever sits in it
        m = w.mut(vessel)
        m["ice_bath"] = True
        m["ice_kg"] = round(float(m.get("ice_kg", 0.0)) + float(f.get("qty_g", 0.0)) / 1000.0, 3)
        m["internal_temp_c"] = 0.5
        m["food_air_model"] = True
        m["heat_coupling"] = 25.0
        m["ua_w_k"] = 1.5
        m["air_heat_capacity_j_k"] = 4180.0 * (float(m.get("liquid_l", 1.0) or 1.0) + m["ice_kg"])
        M.register(w, "air", vessel)
        log(w, vessel, "add", kind="ice", g=round(float(f.get("qty_g", 0.0)), 1))
        return vessel
    if parent is not None and relation == "in" and not parent.get("drain"):
        if f.get("phase") in ("liquid", "granular", "powder", "paste"):
            for other in M.vessel_foods(state, vessel, "in"):
                orec = state["entities"][other]
                of = M.food(orec)
                if orec.get("package") or of.get("phase") not in ("liquid", "paste", "dough"):
                    continue
                if of.get("phase") == "liquid" and f.get("phase") == "liquid" and of.get("kind") == f.get("kind"):
                    M.merge_into(w, other, rec)
                elif _finished(of):
                    # stirred into a finished dish, drink or sauce (cornstarch into a stew, sugar into sour cream): it
                    # joins the dish, which stays what it was (it used to be reclassified from a composition whose
                    # main part carried no water, so a soup with a scoop of powder became a "dry mix" powder)
                    M.merge_into(w, other, rec)
                else:
                    g_old, g_new = M.qty(orec), float(f.get("qty_g", 0.0))
                    h = float((of.get("mix") or {}).get("h", 1.0 if len(of.get("composition") or {}) <= 1 else 0.0))
                    M.merge_into(w, other, rec)
                    m = w.mut(other)
                    mix = dict(m["food"].get("mix") or {})
                    mix["h"] = round(h * g_old / max(1.0, g_old + g_new), 4)
                    m["food"]["mix"] = mix
                    reclassify(w, other)
                log(w, vessel, "add", kind=f.get("kind"), g=round(float(f.get("qty_g", 0.0)), 1))
                M.update_fill(w, vessel)
                return other
    eid = M.deposit(w, rec, vessel, relation)
    log(w, vessel, "add", kind=f.get("kind"), g=round(float(f.get("qty_g", 0.0)), 1))
    if vessel in state["entities"]:
        M.update_fill(w, vessel)
    return eid


BLEND_SPEED = {"low": 0.5, "medium": 1.0, "high": 1.6, "pulse": 0.8, "smoothie": 1.4, "ice_crush": 1.8}


def blend(w, vessel, seconds, speed="high"):
    """What a blender run does to the food in its jar (the appliance domain owns the motor, lid interlock and power).
    Frozen chunks break down only when there is enough liquid around the blades (>= ~30 % by mass);
    a thick, dry load cavitates (an air pocket forms over the blades) and chunks survive. Melting frozen fruit
    pulls the drink's temperature down (latent heat). Returns the merged food id."""
    state = w.s
    foods = [f for f in M.vessel_foods(state, vessel, "in") if not state["entities"][f].get("package")]
    if not foods:
        return None
    for fid in foods:                       # whole soft fruit/veg is pulped by the blades
        if (M.food(state["entities"][fid]) or {}).get("phase") == "solid":
            w.mut(fid)["food"]["phase"] = "pieces"
    chunks = 0.0
    ice_g = 0.0
    liquid_g = 1000.0 * float(state["entities"][vessel].get("liquid_l", 0.0) or 0.0)
    for fid in foods:
        rec = state["entities"][fid]
        f = M.food(rec)
        ice = float(f.get("ice", 0.0) or 0.0)
        if f.get("frozen_chunks") is not None:
            chunks += float(f["frozen_chunks"])
        elif ice > 0.3:
            chunks += float(f.get("pieces") or f.get("count") or max(1.0, M.qty(rec) / 12.0))
        ice_g += ice * M.qty(rec) * float(M.catinfo(rec).get("water", 0.8))
        comp = f.get("composition") or {f.get("kind"): M.qty(rec)}
        if f.get("phase") == "liquid":
            liquid_g += M.qty(rec)
        else:
            liquid_g += sum(float(v) for k, v in comp.items() if k in LIQUIDS)
    main = combine(w, vessel)
    if main is None:
        return None
    m = w.mut(main)
    f = m["food"]
    total = M.qty(m) or 1.0
    liq = liquid_g / total
    rate = 0.12 * BLEND_SPEED.get(speed, 1.0)
    mix = dict(f.get("mix") or {})
    if liq < 0.3:
        rate = 0.015
        mix["cavitated"] = True
    else:
        mix.pop("cavitated", None)
    broken = 1.0 - math.exp(-rate * float(seconds))
    chunks *= 1.0 - broken
    f["frozen_chunks"] = 0 if chunks < 0.5 else int(round(chunks))
    mix["h"] = round(1.0 - (1.0 - float(mix.get("h", 0.0))) * math.exp(-float(seconds) / (8.0 if liq >= 0.3 else 40.0)), 4)
    f["mix"] = mix
    # latent heat of the ice that melts in the vortex (melted share follows chunk breakdown)
    melted = ice_g * broken
    if melted > 0:
        t = float(m.get("temperature_c", 21.0)) - melted * 334.0 / (total * 3.6)
        m["temperature_c"] = round(max(-1.0, t), 3)
        m["_core_c"] = m["temperature_c"]
    f["ice"] = 0.0 if f["frozen_chunks"] == 0 else round(min(1.0, f["frozen_chunks"] * 12.0 / total), 4)
    comp = f.get("composition") or {}
    fruity = any(k in comp for k in ("banana", "strawberries", "blueberries", "mango", "mixed_berries", "frozen_berries",
                                      "frozen_mango", "pineapple", "peach", "spinach", "apple"))
    f["phase"] = "liquid" if liq >= 0.3 else "paste"
    if fruity and liq >= 0.3:
        f["kind"] = "smoothie"
        f["cat"] = "mixture"
        m["label"] = "smoothie"
    f["blended"] = True
    vrec = state["entities"][vessel]
    cap = float(vrec.get("capacity_l", 0.0) or 0.0)
    vol = float(m.get("volume_l", total / 1000.0))
    if cap and vol > 0.95 * cap:
        lost = (vol - 0.9 * cap) * 1000.0 * float(f.get("density", 1.0)) + (0.15 * total if vrec.get("open", True) and vrec.get("openable") else 0.0)
        lost = min(lost, 0.5 * total)
        M.set_qty(w, main, total - lost)
        M.spill(w, vessel, f.get("kind", "smoothie"), lost, "liquid")
        w.mut(main)["food"]["overflowed"] = True
    elif vrec.get("openable") and vrec.get("open", True):
        lost = 0.1 * total * min(1.0, float(seconds) / 10.0)
        M.set_qty(w, main, total - lost)
        M.spill(w, vessel, f.get("kind", "smoothie"), lost, "liquid")
    log(w, vessel, "blend", seconds=round(float(seconds), 1), speed=speed)
    M.update_fill(w, vessel)
    return main
