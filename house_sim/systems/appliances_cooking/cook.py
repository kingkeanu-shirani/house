"""Heat-driven cooking of food items (fidelity P-approx).

For every food entity in a heating context (pan on a burner, oven/toaster oven/air fryer cavity, toaster slot, waffle
plates, griddle, grill grates, microwave) the model tracks, in the hidden dict ``_cook``:

  core_c / peak_core_c   lagged core temperature (tau = L^2 / (alpha pi^2/4), alpha = 1.4e-7 m2/s, L = half thickness);
                         when the food domain is loaded its ``_core_c`` is used instead
  moist                  surface moisture 0..1; above 100 C it evaporates first (patted-dry meat sears, wet meat steams);
                         crowded pans/trays stay humid (no browning, soggy)
  brown_top/brown_bottom Maillard/caramelisation index per side: 0 raw, 1 golden, 1.5 deep brown, 2 dark, >= 2.5 burnt
                         (smoke); rate = (T_eff - 120 C) / k_profile; T_eff = 100 + (T - 100)(1 - moist)
  tender                 vegetables/pasta: time-at-temperature index (crisp-tender ~1.0-1.7, mush > 2.6; pasta al dente
                         0.92-1.1 of package time)
  danger_s               raw animal food between 5 and 57 C while heating (slow cooker from frozen) - unsafe after 2 h
  doneness / status      label (rare..well, underbaked/baked/overbaked, crunchy/crisp_tender/tender/mushy, hard/al_dente/
                         soft/mushy) and status under|ok|over, plus safe (peak core >= safe minimum) and burnt

Visible cues are written to ``cook_state`` (surface colour, underside colour when it can be seen, bubbles, juices,
crust, steaming, texture words) - deliberately including classic misleading cues (chicken looks opaque white long
before its core is safe; a cake top browns before the centre sets). ``check_doneness`` measures with a probe
thermometer, a cake tester, a fork, by pressing, by eye, or with a water drop on a pan.
"""
import json
import math

from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after
from . import common as C
from . import percept as P

ALPHA = 1.4e-7
K_DRY = 15000.0
BROWN_START_C = 120.0

PROFILES = {
    "poultry": {"cls": "meat", "done_c": 74.0, "over_c": 82.0, "safe_c": 74.0, "brown_k": 24000.0, "moist0": 0.7, "fat": 0.3, "release": True},
    "whole_poultry": {"cls": "meat", "L": 0.06, "done_c": 74.0, "over_c": 85.0, "safe_c": 74.0, "brown_k": 30000.0, "moist0": 0.6, "fat": 0.6},
    "ground_meat": {"cls": "meat", "done_c": 71.0, "over_c": 80.0, "safe_c": 71.0, "brown_k": 22000.0, "moist0": 0.6, "fat": 1.0, "release": True},
    "steak": {"cls": "meat", "done_c": 57.0, "over_c": 71.0, "safe_c": None, "brown_k": 22000.0, "moist0": 0.5, "fat": 0.5, "release": True,
              "levels": True},
    "roast_meat": {"cls": "meat", "L": 0.04, "done_c": 63.0, "over_c": 75.0, "safe_c": 63.0, "brown_k": 28000.0, "moist0": 0.5, "fat": 0.6},
    "pork": {"cls": "meat", "done_c": 63.0, "over_c": 72.0, "safe_c": 63.0, "brown_k": 24000.0, "moist0": 0.6, "fat": 0.6, "release": True},
    "fish": {"cls": "meat", "done_c": 63.0, "over_c": 70.0, "safe_c": 63.0, "brown_k": 20000.0, "moist0": 0.7, "fat": 0.2, "release": True,
             "fragile": True},
    "bacon": {"cls": "meat", "L": 0.002, "done_c": 71.0, "over_c": 99.0, "safe_c": 63.0, "brown_k": 16000.0, "moist0": 0.4, "fat": 3.0,
              "render_g_per_kg": 280.0},
    "egg": {"cls": "egg", "done_c": 71.0, "over_c": 90.0, "safe_c": 71.0, "brown_k": 26000.0, "moist0": 0.9},
    "cake": {"cls": "bake", "done_c": 96.0, "over_c": None, "safe_c": None, "brown_k": 30000.0, "moist0": 0.9, "collapse": True},
    "souffle": {"cls": "bake", "done_c": 80.0, "over_c": None, "safe_c": None, "brown_k": 26000.0, "moist0": 0.9, "collapse": True, "L": 0.035},
    "cookies": {"cls": "bake", "L": 0.005, "done_c": 88.0, "over_c": None, "safe_c": None, "brown_k": 26000.0, "moist0": 0.6, "band": [0.8, 1.4]},
    "bread_loaf": {"cls": "bake", "L": 0.045, "done_c": 93.0, "over_c": None, "safe_c": None, "brown_k": 30000.0, "moist0": 0.8},
    "casserole": {"cls": "reheat", "done_c": 74.0, "over_c": None, "safe_c": 74.0, "brown_k": 32000.0, "moist0": 0.8, "L": 0.025},
    "reheat": {"cls": "reheat", "done_c": 74.0, "over_c": None, "safe_c": 74.0, "brown_k": 32000.0, "moist0": 0.9},
    "vegetables": {"cls": "veg", "tender_k": 300.0, "band": [1.0, 1.7], "mush": 2.6, "brown_k": 40000.0, "moist0": 0.9, "k_dry": 60000.0},
    "potato": {"cls": "veg", "tender_k": 900.0, "band": [1.0, 2.2], "mush": 3.0, "brown_k": 36000.0, "moist0": 0.8, "k_dry": 45000.0},
    "pasta": {"cls": "pasta", "package_s": 600.0, "band": [0.92, 1.1], "soft": 1.3, "brown_k": 1e9, "moist0": 1.0},
    "nuts": {"cls": "nuts", "L": 0.003, "brown_k": 9000.0, "moist0": 0.05, "band": [0.8, 1.4], "burn": 2.2},
    "bread": {"cls": "bread", "L": 0.006, "brown_k": 12000.0, "moist0": 0.35, "band": [0.8, 1.4]},
    "pancake": {"cls": "bake", "L": 0.004, "done_c": 90.0, "over_c": None, "safe_c": None, "brown_k": 14000.0, "moist0": 0.9, "release": True,
                "band": [0.8, 1.4]},
    "waffle": {"cls": "bake", "L": 0.008, "done_c": 95.0, "over_c": None, "safe_c": None, "brown_k": 16000.0, "moist0": 0.9, "band": [0.8, 1.5]},
    "fries": {"cls": "fried", "L": 0.006, "done_c": 75.0, "over_c": None, "safe_c": None, "brown_k": 18000.0, "moist0": 0.5, "band": [0.9, 1.6]},
    "cheese": {"cls": "topping", "L": 0.004, "done_c": 60.0, "over_c": None, "safe_c": None, "brown_k": 15000.0, "moist0": 0.3, "fat": 1.5,
               "band": [0.6, 1.5]},
    "liquid": {"cls": "liquid", "done_c": 74.0, "over_c": None, "safe_c": None, "brown_k": 1e9, "moist0": 1.0},
    "generic": {"cls": "meat", "done_c": 74.0, "over_c": 90.0, "safe_c": None, "brown_k": 25000.0, "moist0": 0.6},
}
STEAK_LEVELS = [(49.0, "blue"), (54.0, "rare"), (60.0, "medium_rare"), (65.0, "medium"), (69.0, "medium_well"), (999.0, "well")]
COLORS = [(0.25, "pale"), (0.7, "light golden"), (1.15, "golden brown"), (1.6, "deep brown"), (2.0, "dark brown"), (2.5, "very dark"),
          (99.0, "charred black")]


def profile_name(rec):
    if rec.get("cook_profile"):
        return rec["cook_profile"]
    f = rec.get("food") or {}
    kind = C.food_kind(rec)
    cat = C.food_cat(rec)
    label = str(rec.get("label") or "").lower()
    text = kind + " " + label
    if f.get("phase") == "liquid" or cat in ("beverage",) and f.get("phase") in ("liquid", None):
        return "liquid"
    for words, name in ((("whole_chicken", "whole chicken", "turkey"), "whole_poultry"), (("souffle",), "souffle"),
                        (("bacon",), "bacon"), (("cookie",), "cookies"), (("cake", "brownie", "muffin", "cupcake"), "cake"),
                        (("pancake", "crepe"), "pancake"), (("waffle",), "waffle"), (("fries", "tater"), "fries"),
                        (("pine_nut", "pine nut", "almond", "walnut", "pecan", "sesame", "cumin_seed", "seeds", "nuts"), "nuts"),
                        (("bread_loaf", "loaf", "baguette"), "bread_loaf"), (("bread", "bagel", "toast", "english_muffin", "pastry"), "bread"),
                        (("pasta", "spaghetti", "penne", "macaroni", "noodle"), "pasta"), (("lasagna", "casserole", "gratin", "enchilada"), "casserole"),
                        (("steak", "chop_beef", "sirloin", "ribeye"), "steak"), (("roast", "chuck", "brisket"), "roast_meat"),
                        (("ground", "burger", "patty", "meatball", "sausage"), "ground_meat"), (("pork",), "pork"),
                        (("potato",), "potato"), (("cheese",), "cheese")):
        if any(w in text for w in words):
            return name
    if cat == "poultry":
        return "poultry"
    if cat in ("meat",):
        return "steak" if "beef" in text else "generic"
    if cat in ("ground_meat",):
        return "ground_meat"
    if cat in ("fish", "shellfish"):
        return "fish"
    if cat == "egg":
        return "egg"
    if cat in ("vegetable", "root", "leafy_green", "frozen_veg", "produce", "herb"):
        return "vegetables"
    if cat in ("bread",):
        return "bread"
    if cat in ("prepared", "frozen_meal", "mixture"):
        return "reheat"
    if cat in ("dough",):
        return "bread_loaf"
    return "generic"


def profile(rec):
    return PROFILES.get(profile_name(rec), PROFILES["generic"])


def half_thickness(rec, prof):
    if prof.get("L"):
        return float(prof["L"])
    if rec.get("thickness_m"):
        return max(0.002, C.num(rec["thickness_m"]) / 2.0)
    s = C.size3(rec)
    return max(0.002, min(0.08, min(s) / 2.0))


def color_word(x):
    for limit, word in COLORS:
        if x < limit:
            return word
    return COLORS[-1][1]


# ----------------------------------------------------------------------------------------------
# heat context

def heat_context(state, eid):
    """Where the food's heat comes from: {mode, top_c, bottom_c, air_c, humid, appliance, vessel, radiant}."""
    ents = state["entities"]
    rec = ents[eid]
    surf = C.num(rec.get("temperature_c"), 21.0)
    ctx = {"mode": "air", "top_c": surf, "bottom_c": surf, "air_c": surf, "humid": False, "appliance": None, "vessel": None,
           "radiant": False}
    parent_id = rec.get("location")
    parent = ents.get(parent_id) or {}
    chain = C.parent_chain(state, eid)
    appliance = None
    for pid in chain:
        prec = ents.get(pid) or {}
        if prec.get("ac_kind") in ("oven", "toaster_oven", "air_fryer", "toaster", "microwave", "waffle_maker", "griddle", "grill",
                                   "burner", "slow_cooker", "rice_cooker", "pressure_cooker"):
            appliance = pid
            break
    ctx["appliance"] = appliance
    arec = ents.get(appliance) or {}
    kind = arec.get("ac_kind")
    vessel = parent_id if (parent.get("conducts_heat") or parent.get("liquid_container") or C.is_cookware(parent)) else None
    ctx["vessel"] = vessel
    vrec = ents.get(vessel) or {}
    if vessel and (C.num(vrec.get("liquid_l")) > 0.02 and not C.is_oil(vrec.get("liquid"))) and rec.get("relation") == "in":
        t = C.num(vrec.get("temperature_c"), surf)
        ctx.update(mode="liquid", top_c=t, bottom_c=t, air_c=t, humid=True)
        return ctx
    if kind in ("burner", "griddle") or (vessel and (ents.get(vrec.get("location")) or {}).get("ac_kind") in ("burner", "griddle")):
        pan_t = C.num(vrec.get("temperature_c"), surf) if vessel else C.num(arec.get("temperature_c"), surf)
        lid = bool(vessel) and (vrec.get("openable") and not vrec.get("open") or vrec.get("lid_on") or vrec.get("covered_by"))
        top = C.room_temp(state, C.room_of(state, eid)) + (pan_t - 21.0) * (0.45 if lid else 0.12)
        ctx.update(mode="pan", top_c=min(top, 100.0 if lid else top), bottom_c=pan_t, air_c=top, humid=bool(lid))
        if vessel and C.is_oil(vrec.get("liquid")) and C.num(vrec.get("liquid_l")) > 0.0:
            depth_frac = C.num(vrec.get("liquid_l")) / max(0.1, C.num(vrec.get("capacity_l"), 2.0))
            if depth_frac > 0.25:
                ctx.update(mode="deep_fry", top_c=pan_t, bottom_c=pan_t)
        _crowding(state, eid, vessel or appliance, ctx)
        return ctx
    if kind in ("oven", "toaster_oven", "air_fryer"):
        air = C.num(arec.get("internal_temp_c"), surf)
        mode = (arec.get("settings") or {}).get("mode")
        coupling = C.num(arec.get("heat_coupling"), 1.0)
        top = 21.0 + (air - 21.0) * min(1.3, coupling)
        bottom = C.num(vrec.get("temperature_c"), air) if vessel else air
        if vessel and (vrec.get("surface") or vrec.get("category") == "bakeware"):
            bottom = 21.0 + (bottom - 21.0) * 1.12
        if not vessel and parent.get("ac_kind") != "oven_rack" and parent_id == appliance and kind == "oven":
            bottom = 21.0 + (air - 21.0) * 1.6     # on the oven floor, right over the bake element
        if mode == "broil" or (kind == "toaster_oven" and mode in ("broil", "toast")):
            d = broil_distance(state, eid, appliance)
            top = max(top, radiant_temp(d, C.num((arec.get("oven") or {}).get("broil_w"), 3600.0)))
            ctx["radiant"] = True
        if kind == "air_fryer":
            bottom = top
        ctx.update(mode="oven" if kind != "air_fryer" else "air_fryer", top_c=top, bottom_c=bottom, air_c=air)
        _crowding(state, eid, vessel or parent_id, ctx)
        return ctx
    if kind == "toaster":
        t = C.num((arec.get("_toaster") or {}).get("element_eff_c"), surf)
        ctx.update(mode="toaster", top_c=t, bottom_c=t, air_c=t, radiant=t > 150)
        if (arec.get("settings") or {}).get("mode") == "bagel":
            ctx["top_c"] = 21.0 + (t - 21.0) * 0.35
        return ctx
    if kind == "waffle_maker":
        t = C.num(arec.get("internal_temp_c"), surf)
        ctx.update(mode="waffle", top_c=t, bottom_c=t, air_c=t)
        return ctx
    if kind == "grill":
        g = C.num((arec.get("grill") or {}).get("grate_c"), surf)
        lid_closed = arec.get("openable") and not arec.get("open")
        top = 21.0 + (g - 21.0) * (0.75 if lid_closed else 0.08)
        ctx.update(mode="grill", top_c=top, bottom_c=g, air_c=top)
        return ctx
    if kind in ("microwave",):
        ctx.update(mode="microwave", humid=True)
        return ctx
    if kind in ("slow_cooker", "rice_cooker", "pressure_cooker"):
        t = C.num(vrec.get("temperature_c"), surf) if vessel else surf
        ctx.update(mode="moist_heat", top_c=min(t, 100.0), bottom_c=min(t, 100.0), air_c=t, humid=True)
        return ctx
    return ctx


def _crowding(state, eid, holder, ctx):
    """More than ~75 % of a pan/tray covered by food steams it."""
    if not holder or holder not in state["entities"]:
        return
    hrec = state["entities"][holder]
    if hrec.get("container") and hrec.get("inner_size_m"):
        sw, sd = C.num(hrec["inner_size_m"][0]), C.num(hrec["inner_size_m"][1])
    else:
        sw, sd = C.surface_dims(hrec)
    area = max(0.01, sw * sd)
    covered = 0.0
    layers = 1.0
    for c in C.children(state, holder):
        crec = state["entities"][c]
        if not C.is_food(crec):
            continue
        s = C.size3(crec)
        pieces = int(C.num((crec.get("food") or {}).get("pieces"), 1) or 1)
        if pieces > 1 and (crec.get("food") or {}).get("piece_mm"):
            pm = crec["food"]["piece_mm"]
            covered += pieces * C.num(pm[0], 20) * C.num(pm[1], 20) / 1e6
        else:
            covered += s[0] * s[1]
    frac = covered / area
    ctx["coverage"] = round(frac, 3)
    if frac > 0.75:
        ctx["humid"] = True
        ctx["crowded"] = True
        layers = frac
    ctx["layers"] = round(max(1.0, layers), 2)


def broil_distance(state, eid, oven_id):
    """Distance (m) from the top of the food to the broil element at the top of the cavity."""
    ents = state["entities"]
    oven = ents[oven_id]
    inner = oven.get("inner_size_m") or [0.6, 0.45, 0.4]
    cavity_h = C.num(inner[2], 0.4)
    z_oven = C.num(oven.get("z_m"), 0.0)
    from ...kernel import geometry
    z_food = geometry.height_m(state, eid) - z_oven
    top = z_food + C.size3(ents[eid])[2]
    return max(0.01, cavity_h - top)


def radiant_temp(d, broil_w=3600.0):
    """Effective surface temperature under a broil element at distance d (m): ~550 C at 8 cm, ~330 C at 20 cm."""
    return min(560.0, 180.0 + 30.0 * (broil_w / 3600.0) / max(0.02, d))


# ----------------------------------------------------------------------------------------------
# per-substep update

def needs_update(rec):
    if not C.is_food(rec):
        return False
    t = C.num(rec.get("temperature_c"), 21.0)
    hc = rec.get("_cook")
    if t > 38.0:
        return True
    if isinstance(hc, dict) and (C.num(hc.get("core_c"), 21.0) > 38.0 or hc.get("active")):
        return True
    return False


def step_food(w, eid, dt, hctx=None):
    state = w.s
    rec = state["entities"][eid]
    prof = profile(rec)
    name = profile_name(rec)
    hctx = hctx or heat_context(state, eid)
    hc = dict(rec.get("_cook") or {})
    surf = C.num(rec.get("temperature_c"), 21.0)
    f = rec.get("food") or {}
    # ---- frozen food (only when the food domain, which owns latent heat, is not managing this item)
    managed = rec.get("_tt") is not None
    ice = C.num(f.get("ice"))
    in_heated_vessel = bool((state["entities"].get(rec.get("location")) or {}).get("_ac_heat"))
    if not managed and ice > 0.0 and surf > 0.0 and in_heated_vessel:
        w.mut(eid)["temperature_c"] = 0.0          # the vessel model melts the ice; the surface stays at 0 C until thawed
        surf = 0.0
    elif not managed and ice > 0.0 and surf > 0.0:
        mass = C.mass_kg(rec)
        melt = (surf - 0.0) * mass * 3500.0 / (334000.0 * mass * 0.75)
        ice_new = max(0.0, ice - melt)
        m = w.mut(eid)
        m["food"] = dict(f, ice=round(ice_new, 5))
        f = m["food"]
        if ice_new > 0.0:
            m["temperature_c"] = 0.0
            surf = 0.0
        else:
            left = (melt - ice) * 334000.0 * 0.75 / 3500.0
            m["temperature_c"] = C.rnd(max(0.0, left), 3)
            surf = C.num(m["temperature_c"])
        rec = state["entities"][eid]
    # ---- core temperature
    if rec.get("_tt") is not None and rec.get("_core_c") is not None:
        core = C.num(rec.get("_core_c"), surf)
    else:
        core = C.num(hc.get("core_c"), surf if "core_c" not in hc else hc["core_c"])
        if "core_c" not in hc:
            core = C.num(rec.get("_core_c"), surf)
        L = half_thickness(rec, prof)
        tau = max(20.0, L * L / (ALPHA * 2.47))
        if f.get("phase") == "liquid":
            tau = 90.0          # liquids even out by convection within a minute or two (faster still when stirred)
        core = surf + (core - surf) * math.exp(-dt / tau)
        if C.num(f.get("ice")) > 0.05:
            core = min(core, 0.0)
    hc["core_c"] = C.rnd(core, 3)
    hc["peak_core_c"] = C.rnd(max(C.num(hc.get("peak_core_c"), core), core), 2)
    hc["profile"] = name
    heating = hctx["mode"] != "air" or surf > 45.0
    hc["active"] = bool(heating and (hctx["mode"] != "air" or surf > 40.0))
    # ---- surface moisture
    if "moist" not in hc:
        hc["moist"] = C.rnd(min(1.0, C.num(prof.get("moist0"), 0.6) + 0.5 * C.num(rec.get("wetness")) +
                                (0.4 if C.num(f.get("ice")) > 0.1 or C.num(rec.get("temperature_c")) < -2 else 0.0)), 4)
    moist = C.num(hc["moist"])
    k_dry = C.num(prof.get("k_dry"), K_DRY)
    hottest = max(hctx["top_c"], hctx["bottom_c"])
    if hctx["mode"] in ("liquid", "moist_heat", "microwave"):
        moist = 1.0
    elif hottest > 100.0:
        moist -= dt * (min(hottest, 320.0) - 100.0) / k_dry * (0.25 if hctx.get("humid") else 1.0)
        floor = 0.5 if hctx.get("crowded") else 0.0
        moist = max(floor, moist)
    hc["moist"] = C.rnd(max(0.0, min(1.0, moist)), 4)
    # ---- browning per side (air fryer basket layers shield the lower pieces until shaken)
    exposure = 1.0
    if hctx["mode"] == "air_fryer":
        layers = C.num(hctx.get("layers"), 1.0)
        exposure = 1.0 if layers <= 1.05 else (1.0 / layers if not hc.get("shaken_recent") else 0.85)
    for side, temp in (("brown_top", hctx["top_c"]), ("brown_bottom", hctx["bottom_c"])):
        t_eff = temp if temp <= 100.0 else 100.0 + (temp - 100.0) * (1.0 - hc["moist"])
        rate = max(0.0, t_eff - BROWN_START_C) / C.num(prof.get("brown_k"), 25000.0) * exposure
        if rate > 0:
            hc[side] = C.rnd(C.num(hc.get(side)) + rate * dt, 4)
    burnt_at = C.num(prof.get("burn"), 2.5)
    top_b, bot_b = C.num(hc.get("brown_top")), C.num(hc.get("brown_bottom"))
    if max(top_b, bot_b) >= burnt_at:
        if not hc.get("burnt"):
            hc["burnt"] = True
            C.log(w, "food_burnt", food=eid)
            C.counter(w, "food_burnt")
        if hctx["mode"] != "air":
            rid = C.room_of(state, eid)
            C.add_smoke(w, rid, 0.02 * dt * min(3.0, max(top_b, bot_b) - burnt_at + 0.3) * max(0.1, C.mass_kg(rec)), source=eid)
            if hctx.get("radiant") and top_b >= 3.2 and (C.num(prof.get("fat")) >= 0.5 or "cheese" in C.food_kind(rec)) \
                    and not hc.get("flared"):
                hc["flared"] = True
                from .stove import ignite_item
                ignite_item(w, eid, rid, cause="broiler_flare_up")
    # ---- doneness by class
    cls = prof.get("cls")
    if cls in ("veg",) and core >= 82.0 and hctx["mode"] != "air":
        hc["tender"] = C.rnd(C.num(hc.get("tender")) + dt * (core - 80.0) / 20.0 / C.num(prof.get("tender_k"), 300.0), 4)
    if cls == "pasta" and hctx["mode"] == "liquid":
        water_t = hctx["top_c"]
        pkg = C.num(f.get("package_min"), 0) * 60.0 or C.num(rec.get("package_s"), 0) or C.num(prof.get("package_s"), 600.0)
        if "added_at_c" not in hc:
            hc["added_at_c"] = C.rnd(water_t, 1)
            if water_t < 90.0:
                hc["clumped"] = True
        if water_t >= 95.0:
            hc["tender"] = C.rnd(C.num(hc.get("tender")) + dt / pkg, 4)
        elif water_t >= 60.0:
            hc["tender"] = C.rnd(C.num(hc.get("tender")) + dt / pkg * (water_t - 60.0) / 70.0, 4)
            hc["gummy"] = True
    # ---- danger zone for raw animal food that is being heated slowly
    if f.get("raw") or f.get("needs_cooking") or cls == "meat":
        if hctx["mode"] != "air" and 5.0 < core < 57.0 and C.num(hc.get("peak_core_c")) < 60.0:
            hc["danger_s"] = C.rnd(C.num(hc.get("danger_s")) + dt, 1)
    # ---- labels
    hc.update(_labels(rec, prof, name, hc, core))
    visible = cues(rec, prof, name, hc, hctx, surf, core)
    m = w.mut(eid)
    if m.get("_cook") != hc:
        m["_cook"] = hc
    if m.get("cook_state") != visible:
        m["cook_state"] = visible
    if prof.get("render_g_per_kg") and hctx["mode"] in ("pan", "grill") and C.num(hc.get("brown_bottom")) > 0.3:
        _render_fat(w, eid, prof, hctx, dt)


def _labels(rec, prof, name, hc, core):
    out = {}
    cls = prof.get("cls")
    peak = C.num(hc.get("peak_core_c"), core)
    safe_c = prof.get("safe_c")
    out["safe"] = bool(safe_c is None or peak >= safe_c)
    status = "under"
    label = "raw"
    if cls in ("meat", "egg", "reheat", "liquid", "fried", "topping"):
        done = C.num(prof.get("done_c"), 74.0)
        over = prof.get("over_c")
        if name == "steak" or prof.get("levels"):
            label = next(word for limit, word in STEAK_LEVELS if peak < limit)
        else:
            label = "raw" if peak < done - 15 else "undercooked" if peak < done else "cooked"
        status = "under" if peak < done else ("over" if over is not None and peak > over else "ok")
        if over is not None and peak > over:
            label = "overcooked" if name != "steak" else label
    elif cls == "bake":
        done = C.num(prof.get("done_c"), 96.0)
        band = prof.get("band") or [0.7, 1.6]
        top = C.num(hc.get("brown_top"))
        if peak < done:
            label, status = ("raw" if peak < done - 30 else "underbaked"), "under"
        elif max(top, C.num(hc.get("brown_bottom"))) > band[1] + 0.3:
            label, status = "overbaked", "over"
        else:
            label, status = "baked", "ok"
    elif cls == "veg":
        t = C.num(hc.get("tender"))
        band = prof.get("band") or [1.0, 1.7]
        label = "raw" if t < 0.3 else "crunchy" if t < band[0] else "crisp_tender" if t <= band[1] else "tender" if t < prof.get("mush", 2.6) else "mushy"
        status = "under" if t < band[0] else ("ok" if t < prof.get("mush", 2.6) else "over")
    elif cls == "pasta":
        t = C.num(hc.get("tender"))
        band = prof.get("band")
        label = "hard" if t < 0.8 else "chalky_core" if t < band[0] else "al_dente" if t <= band[1] else "soft" if t <= prof.get("soft", 1.3) else "mushy"
        status = "under" if t < band[0] else "ok" if t <= band[1] else "over"
        if hc.get("clumped") or hc.get("gummy"):
            label += "_clumped"
            status = "over" if status != "under" else status
    elif cls in ("nuts", "bread"):
        b = max(C.num(hc.get("brown_top")), C.num(hc.get("brown_bottom")))
        band = prof.get("band") or [0.8, 1.4]
        label = color_word(b)
        status = "under" if b < band[0] else "ok" if b <= band[1] else "over"
    if hc.get("burnt"):
        status = "over"
    out["doneness"] = label
    out["status"] = status
    if cls in ("veg", "fried", "nuts", "bread", "bake") and (hc.get("brown_top", 0) or hc.get("brown_bottom", 0)):
        b = prof.get("band") or [0.8, 1.6]
        tb, bb = C.num(hc.get("brown_top")), C.num(hc.get("brown_bottom"))
        out["even"] = bool(abs(tb - bb) <= 0.15 * max(1.0, max(tb, bb)) + 0.15)
        out["brown_ok"] = bool(b[0] <= min(tb, bb) and max(tb, bb) <= b[1] + 0.2)
    return out


def cues(rec, prof, name, hc, hctx, surf, core):
    """What the robot can see/smell from outside (never the core temperature)."""
    cls = prof.get("cls")
    top = C.num(hc.get("brown_top"))
    bottom = C.num(hc.get("brown_bottom"))
    out = {"surface": color_word(top)}
    if hctx["mode"] in ("pan", "grill", "deep_fry") or bottom > top + 0.3:
        out["underside"] = color_word(bottom)
    if cls == "meat" and name not in ("bacon",):
        if surf < 50:
            out["surface"] = "raw" if top < 0.2 else out["surface"]
        elif top < 0.25:
            out["surface"] = "opaque white" if name in ("poultry", "whole_poultry", "fish") else "grey-brown"
        out["juices"] = "pink" if core < 68.0 else "clear"
        out["firmness"] = "soft" if core < 50 else "springy" if core < 62 else "firm" if core < 72 else "very firm"
    if name == "pancake":
        out["bubbles"] = "none" if core < 45 else "forming" if C.num(hc.get("moist")) > 0.35 else "set at the edges, popping"
    if cls == "bake" and name not in ("pancake",):
        out["top"] = color_word(top)
        if hc.get("collapsed"):
            out["shape"] = "sunken in the centre"
        elif core > 70:
            out["shape"] = "risen"
    if cls == "veg":
        out["texture"] = {"raw": "raw", "crunchy": "firm", "crisp_tender": "bright, crisp-tender", "tender": "soft",
                          "mushy": "falling apart"}.get(hc.get("doneness"), "firm")
    if cls == "pasta":
        out["texture"] = "stiff" if C.num(hc.get("tender")) < 0.5 else "bending" if C.num(hc.get("tender")) < 1.0 else "limp"
        if hc.get("clumped"):
            out["clumps"] = True
    if hctx.get("crowded"):
        out["steaming"] = True
        if cls in ("veg", "meat", "fried"):
            out["texture"] = "grey and soggy" if top < 0.5 else out.get("texture", "soft")
    elif hctx["mode"] in ("pan", "oven", "deep_fry", "liquid") and surf > 70:
        out["steaming"] = C.num(hc.get("moist")) > 0.2
    if hc.get("burnt"):
        out["smoke"] = True
    if hc.get("torn"):
        out["torn"] = True
    return out


def _render_fat(w, eid, prof, hctx, dt):
    """Bacon renders fat into the pan or the griddle's grease trough."""
    state = w.s
    rec = state["entities"][eid]
    hc = dict(rec.get("_cook") or {})
    total = C.mass_kg(rec) * C.num(prof.get("render_g_per_kg"))
    done = C.num(hc.get("rendered_g"))
    if done >= total:
        return
    g = min(total - done, total * dt / 480.0)
    hc["rendered_g"] = C.rnd(done + g, 2)
    w.mut(eid)["_cook"] = hc
    holder = hctx.get("vessel") or hctx.get("appliance")
    hrec = state["entities"].get(holder) or {}
    if hrec.get("ac_kind") == "griddle":
        from .countertop import griddle_grease
        griddle_grease(w, holder, g / 900.0)
    elif holder:
        m = w.mut(holder)
        if not m.get("liquid") or C.num(m.get("liquid_l")) <= 0:
            m["liquid"] = "grease"
        if C.is_oil(m.get("liquid")):
            m["liquid_l"] = C.rnd(C.num(m.get("liquid_l")) + g / 900.0, 5)


# ----------------------------------------------------------------------------------------------
# turning food (manipulation's flip) and shaking baskets

@after("flip", order=80)
def _flipped(ctx, result):
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target) or {}
    if C.is_food(rec):
        flip_food(w, target)


def flip_food(w, eid):
    """Turn a food item over in its pan/on its tray: the browned side goes up. Turning before the crust releases tears
    delicate food (fish, pancakes) and pulls the crust off meat."""
    state = w.s
    rec = state["entities"][eid]
    prof = profile(rec)
    hc = dict(rec.get("_cook") or {})
    parent = state["entities"].get(rec.get("location")) or {}
    bottom = C.num(hc.get("brown_bottom"))
    nonstick = str(parent.get("coating") or "") == "nonstick" or parent.get("seasoned")
    oily = C.is_oil(parent.get("liquid")) and C.num(parent.get("liquid_l")) > 0.005
    if prof.get("release") and bottom < 0.8 and not nonstick:
        if prof.get("fragile") or profile_name(rec) == "pancake" or not oily:
            hc["torn"] = True
            C.log(w, "food_torn", food=eid)
        else:
            hc["crust_lost"] = True
            bottom = bottom * 0.6
    if profile_name(rec) == "pancake" and C.num(hc.get("moist"), 1.0) > 0.45:
        hc["torn"] = True
    hc["brown_top"], hc["brown_bottom"] = C.rnd(bottom, 4), C.rnd(C.num(hc.get("brown_top")), 4)
    hc["flips"] = int(C.num(hc.get("flips"))) + 1
    w.mut(eid)["_cook"] = hc
    return hc


def shake_basket(w, holder):
    """Lifting and shaking a basket redistributes pieces (air fryer: shielded pieces get exposed)."""
    state = w.s
    for c in C.children(state, holder):
        if C.is_food(state["entities"][c]):
            m, hc = C.mut_hidden(w, c, "_cook")
            hc["shaken_recent"] = True
            hc["shakes"] = int(C.num(hc.get("shakes"))) + 1
            top, bottom = C.num(hc.get("brown_top")), C.num(hc.get("brown_bottom"))
            avg = (top + bottom) / 2.0
            hc["brown_top"] = hc["brown_bottom"] = C.rnd(avg, 4)


@after("shake", order=80)
def _shaken(ctx, result):
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target) or {}
    if rec.get("ac_kind") == "fryer_basket" or C.is_cookware(rec):
        shake_basket(w, target)


# ----------------------------------------------------------------------------------------------
# check_doneness

def _held_tool(ctx, kinds):
    for h in ctx.w.held_items():
        rec = ctx.w.ent(h)
        if rec.get("tool_kind") in kinds or set(rec.get("affordances") or []) & set(kinds):
            return h
    return None


def _inside_closed(state, eid):
    for pid in C.parent_chain(state, eid):
        prec = state["entities"][pid]
        if prec.get("openable") and not prec.get("open") and prec.get("ac_kind"):
            return pid
    return None


def _check_seconds(ctx):
    return ctx.data.get("seconds", 5.0)


@action("check_doneness", params={"target": "entity", "method": {"type": "str", "default": "visual",
                                                                    "choices": ["thermometer", "toothpick", "visual", "fork", "press", "water_drop"]},
                                  "where": "?str:thickest|center|edge|bone"},
        duration=_check_seconds, energy=0.003, category="perception",
        doc="Judge doneness: thermometer (hold an instant-read probe; where=thickest|center|edge|bone), toothpick (hold a cake "
            "tester; clean / moist crumbs / wet batter), fork (vegetables, potatoes, pasta texture), press (meat firmness), "
            "visual (colour, bubbles, juices, crust - cheap but can mislead), water_drop (pan/griddle surface temperature). "
            "Probing food inside an appliance needs its door/lid open.")
def check_doneness(ctx):
    ctx.obj(ctx.args["target"], touch=(ctx.args.get("method") or "visual") != "visual")
    # kernel access refusals (visibility, reach, permission) above pass unchanged; this domain's own reasons below
    # are generic for an unidentified (look-alike or unfamiliar) target, so they cannot name what it is
    with P.plain_refusals(ctx, ctx.args.get("target")):
        return _check_doneness_body(ctx)


def _check_doneness_body(ctx):
    w = ctx.w
    state = w.s
    target = ctx.args["target"]
    method = ctx.args.get("method") or "visual"
    if method == "water_drop":
        rec = ctx.obj(target, touch=True)
        t = C.num(rec.get("temperature_c"), 21.0)
        word = ("sits and steams slowly" if t < 120 else "sizzles and evaporates in a few seconds" if t < 190 else
                "beads up and dances (about 200-230 C)" if t < 240 else "vanishes instantly with a hiss (very hot)")
        ctx.data["seconds"] = 3.0
        ctx.grant(target, "touch", ["temperature_c"])        # a coarse surface-temperature test, like a touch
        return json.dumps({"target": target, "method": method, "water_drop": word})
    touch = method != "visual"
    rec = ctx.obj(target, touch=touch)
    require(C.is_food(rec), "Check the food itself (not its container)", "argument")
    if touch:
        closed = _inside_closed(state, target)
        if closed is not None:
            if ctx.realistic:
                # the appliance by id (a handle), never by a label that could resolve a look-alike (rice cooker vs
                # pressure cooker) the command did not name
                raise ActionError("Open {} first to reach the food".format(closed), "precondition")
            raise ActionError("Open the {} first to reach the food".format(C.label(state["entities"][closed])), "precondition")
    prof = profile(rec)
    hc = dict(rec.get("_cook") or {})
    hctx = heat_context(state, target)
    surf = C.num(rec.get("temperature_c"), 21.0)
    core = C.num(rec.get("_core_c"), C.num(hc.get("core_c"), surf)) if rec.get("_tt") is not None else C.num(hc.get("core_c"), surf)
    out = {"target": target, "method": method}
    if method == "thermometer":
        tool = _held_tool(ctx, ("thermometer", "measure_temperature"))
        require(tool is not None, "Hold a probe thermometer to take an internal temperature", "capability")
        trec = w.ent(tool)
        acc = C.num(trec.get("accuracy_c"), 0.5)
        where = ctx.args.get("where") or "thickest"
        L = half_thickness(rec, prof)
        if where in ("thickest", "center"):
            reading = core if L >= 0.005 else (core + surf) / 2.0
        elif where == "edge":
            reading = (core + 2 * surf) / 3.0
        else:
            reading = core + 6.0
        if ctx.realistic:
            # the probe's error for this food is one fixed draw: probing again gives the same reading, so repeated
            # probes cannot be averaged below the instrument's accuracy
            reading += C.num(hc.get("probe_bias")) + ctx.fixed_rng("ac", "probe", tool, target).uniform(-acc, acc)
        else:
            reading += C.num(hc.get("probe_bias")) + ctx.rng.uniform(-acc, acc)
        lo, hi = (trec.get("range_c") or [-40.0, 300.0])[:2]
        out["reading_c"] = round(max(C.num(lo), min(C.num(hi), reading)), 1)
        out["where"] = where
        # the probe measured the food at that point (to its accuracy_c); the knowledge is about the core, not the surface
        ctx.grant(target, "measure", ["_core_c", "internal_temp_c"])
        if (rec.get("food") or {}).get("raw") or (rec.get("food") or {}).get("needs_cooking") and not hc.get("safe"):
            tm = w.mut(tool)
            contam = dict(tm.get("contamination") or {})
            contam["raw_meat"] = round(min(1.0, C.num(contam.get("raw_meat")) + 0.5), 3)
            tm["contamination"] = contam
        ctx.data["seconds"] = 6.0
    elif method == "toothpick":
        tool = _held_tool(ctx, ("cake_tester", "toothpick", "skewer", "test_doneness"))
        require(tool is not None, "Hold a cake tester or toothpick", "capability")
        if prof.get("cls") != "bake":
            out["result"] = "a tester tells nothing useful for this food"
        else:
            done = C.num(prof.get("done_c"), 96.0)
            out["result"] = "comes out clean" if core >= done else "moist crumbs cling" if core >= done - 8 else "wet batter on it"
        ctx.data["seconds"] = 6.0
    elif method == "fork":
        cls = prof.get("cls")
        if cls == "veg":
            out["result"] = {"raw": "hard", "crunchy": "resists, firm centre", "crisp_tender": "pierces with slight resistance",
                             "tender": "slides in easily", "mushy": "falls apart"}.get(hc.get("doneness"), "hard")
        elif cls == "pasta":
            t = C.num(hc.get("tender"))
            out["result"] = ("hard and chalky" if t < 0.8 else "white chalky core" if t < 0.92 else "al dente: tender with a slight bite"
                             if t <= 1.1 else "soft" if t <= 1.3 else "mushy")
        elif cls in ("meat",):
            out["result"] = "juices run {}".format("pink" if core < 68 else "clear")
        else:
            out["result"] = "no clear signal"
        ctx.data["seconds"] = 4.0
    elif method == "press":
        if prof.get("cls") == "meat":
            level = next(word for limit, word in STEAK_LEVELS if core < limit)
            out["result"] = {"blue": "very soft", "rare": "soft, like the base of the thumb", "medium_rare": "springy",
                             "medium": "slightly firm", "medium_well": "firm", "well": "very firm"}[level]
        else:
            out["result"] = "springs back" if core > 85 else "leaves a dent"
        ctx.data["seconds"] = 3.0
    else:
        looks = cues(rec, prof, profile_name(rec), hc, hctx, surf, core)
        if ctx.realistic:
            looks.pop("firmness", None)          # firmness needs a press (method=press), not a look
            looks.pop("juices", None)            # the juices show only when it is pierced or cut (method=fork)
        out["looks"] = looks
        ctx.data["seconds"] = 2.0
    m, hcm = C.mut_hidden(w, target, "_cook")
    hcm["checks"] = int(C.num(hcm.get("checks"))) + 1
    hcm.setdefault("check_log", []).append({"t": C.now(w), "method": method, "core_c": round(core, 1)})
    del hcm["check_log"][:-20]
    facts = w.sec("facts")
    facts.setdefault("doneness", {})[target] = {k: v for k, v in out.items() if k != "target"}
    facts.setdefault("inspected", {})[target] = state["time_s"]
    return json.dumps(out, sort_keys=True)
