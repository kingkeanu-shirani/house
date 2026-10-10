"""Coffee and tea machines (fidelity S/P-approx): drip coffee makers (incl. descaling), pod machines, espresso machines
with steam wands, burr grinders and French presses. Verbs: brew, grind_coffee, steam_milk.

Drip: water flows from the reservoir through the basket at ~0.2 L/min (slower and cooler with limescale: brew temp
93 C - 12 C x scale) into the carafe. Strength = grounds g per litre of water (household window ~50-70 g/L; ~55 g/L is
"regular"). No paper filter: grounds pour through into the pot; > 110 g grounds or no filter with fine grounds
overflow the basket; no carafe: coffee floods the warming plate and counter. The 80 C warming plate keeps the carafe
hot (auto-off 2 h); an EMPTY glass carafe left on it cracks within ~30 min.
Descale: descaler (citric 0.6 / vinegar 0.4 of the scale per full reservoir) leaves acid residue (1.0) that each
full fresh-water cycle cuts to 25 % (taste threshold 0.1 -> at least two rinse cycles). Vinegar where the manual
forbids it damages seals.
Pod: one pod per brew into a cup of at least cup_ml; a used pod gives weak coffee. Espresso: 18 g tamped fine grind
gives ~36 g in 25-30 s; untamped channels (36 g in ~8 s, sour); a puck must be knocked out before the next dose.
Steam wand: purge before (else water in the milk), steam to 55-65 C (> 70 C scalds the milk), wipe and purge after
(milk dried on the wand clogs it).
"""
import json
import math

from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, before, hook
from . import common as C
from . import percept as P

FLOW_L_PER_S = 0.2 / 60.0
WARMER_C = 80.0


def basket_of(state, cm):
    return next((c for c in C.children(state, cm) if state["entities"][c].get("ac_kind") == "brew_basket"), None)


def carafe_on(state, cm):
    return next((c for c in C.children(state, cm, "on") if state["entities"][c].get("liquid_container")), None)


def grounds_in(state, holder):
    total = 0.0
    fine = False
    for c in C.children(state, holder):
        rec = state["entities"][c]
        if "ground_coffee" in C.food_kind(rec) or rec.get("coffee_g") is not None and not rec.get("pod_used") and rec.get("tags") and "coffee_pod" not in rec.get("tags"):
            g = C.num((rec.get("food") or {}).get("qty_g"), C.num(rec.get("coffee_g")))
            total += g
            if str(rec.get("grind") or "medium") in ("fine", "extra_fine"):
                fine = True
    return total, fine


def filter_in(state, holder):
    return any(state["entities"][c].get("coffee_filter") for c in C.children(state, holder))


@hook("cycle_check:drip_coffee_maker")
def _cm_check(w, eid, *_):
    rec = w.ent(eid)
    if (rec.get("appliance") or {}).get("program") == "brew" and C.num(rec.get("liquid_l")) < 0.05:
        return "The reservoir is empty: add water first"
    return None


@hook("cycle_start:drip_coffee_maker")
def _cm_start(w, eid, *_):
    rec = w.ent(eid)
    state = w.s
    water = C.num(rec.get("liquid_l"))
    scale = C.num(rec.get("_scale"))
    rate = FLOW_L_PER_S * (1.0 - 0.6 * min(1.0, scale))
    rec["appliance"]["remaining_s"] = water / rate + 60.0
    basket = basket_of(state, eid)
    grams, fine = grounds_in(state, basket) if basket else (0.0, False)
    hc = dict(rec.get("_coffee") or {})
    hc.update(brew_start=C.now(w), start_water_l=round(water, 3), grounds_g=round(grams, 1), filter=bool(basket and filter_in(state, basket)),
              liquid=rec.get("liquid") or "water", brewed_l=0.0, spilled_l=0.0)
    rec["_coffee"] = hc
    rec["on"] = True
    rec["display"] = "Brewing"


@hook("cycle_tick:drip_coffee_maker")
def _cm_tick(w, eid, dt):
    state = w.s
    rec = state["entities"][eid]
    hc = dict(rec.get("_coffee") or {})
    scale = C.num(rec.get("_scale"))
    rate = FLOW_L_PER_S * (1.0 - 0.6 * min(1.0, scale))
    water = C.num(rec.get("liquid_l"))
    vol = min(water, rate * dt)
    if vol <= 0:
        return
    m = w.mut(eid)
    m["liquid_l"] = C.rnd(water - vol, 4)
    liquid = hc.get("liquid", "water")
    rid = C.room_of(state, eid)
    basket = basket_of(state, eid)
    grams, fine = grounds_in(state, basket) if basket else (0.0, False)
    no_filter = not (basket and filter_in(state, basket))
    if liquid in ("descaler", "vinegar"):
        strength = 0.6 if liquid == "descaler" else 0.4
        m["_scale"] = C.rnd(max(0.0, scale - strength * vol / 1.8), 4)
        m["_acid"] = 1.0
        if liquid == "vinegar" and rec.get("manual_forbids_vinegar"):
            C.harm(w, "vinegar_damaged_coffee_maker_seals", stat="damage", once="vinegar:" + eid, appliance=eid)
    else:
        acid = C.num(rec.get("_acid"))
        if acid > 0:
            m["_acid"] = C.rnd(acid * (0.25 ** (vol / 1.8)), 5)
            hc["acid_in_brew"] = max(C.num(hc.get("acid_in_brew")), acid)
    if (grams > 110.0 or (no_filter and fine and grams > 0)) and not hc.get("basket_overflow"):
        hc["basket_overflow"] = True
        _spill(w, eid, 0.15, rid, "basket_overflow")
    carafe = carafe_on(state, eid)
    if carafe is None:
        hc["spilled_l"] = C.rnd(C.num(hc.get("spilled_l")) + vol, 4)
        _spill(w, eid, vol, rid, "no_carafe")
    else:
        crec = state["entities"][carafe]
        cm = w.mut(carafe)
        cur = C.num(crec.get("liquid_l"))
        room = C.num(crec.get("capacity_l"), 1.8) - cur
        add = min(vol, max(0.0, room))
        if add < vol:
            hc["spilled_l"] = C.rnd(C.num(hc.get("spilled_l")) + vol - add, 4)
            _spill(w, eid, vol - add, rid, "carafe_overflow")
        cm["liquid"] = "coffee" if liquid == "water" and grams > 0 else liquid if liquid != "water" else "hot_water"
        cm["liquid_l"] = C.rnd(cur + add, 4)
        brew_c = 93.0 - 12.0 * min(1.0, scale)
        cm["liquid_temp_c"] = C.rnd((C.num(crec.get("liquid_temp_c"), 21.0) * cur + brew_c * add) / max(1e-6, cur + add), 2)
        cm["temperature_c"] = cm["liquid_temp_c"]
        info = dict(crec.get("_coffee") or {})
        info["water_l"] = C.rnd(C.num(info.get("water_l")) + add, 4)
        info["grounds_g"] = hc.get("grounds_g")
        info["strength_g_per_l"] = C.rnd(C.num(hc.get("grounds_g")) / max(0.05, C.num(hc.get("start_water_l"))), 1) if hc.get("grounds_g") else 0.0
        info["brew_temp_c"] = round(brew_c, 1)
        info["grounds_in_pot"] = bool(no_filter and grams > 0)
        info["acidic"] = C.num(hc.get("acid_in_brew")) > 0.1 if liquid == "water" else True
        info["brewed_t"] = C.now(w)
        cm["_coffee"] = info
    hc["brewed_l"] = C.rnd(C.num(hc.get("brewed_l")) + vol, 4)
    m["_coffee"] = hc


@hook("cycle_complete:drip_coffee_maker")
def _cm_done(w, eid, *_):
    m = w.mut(eid)
    hc = dict(m.get("_coffee") or {})
    hc["done_t"] = C.now(w)
    hc["cycles"] = int(C.num(hc.get("cycles"))) + 1
    if hc.get("liquid") == "water" and not hc.get("grounds_g"):
        hc["rinse_cycles"] = int(C.num(hc.get("rinse_cycles"))) + 1
    m["_coffee"] = hc
    m["display"] = "Warming" if (m.get("settings") or {}).get("warmer", "on") == "on" else "Done"
    m["appliance"]["state"] = "done"
    for c in C.children(w.s, basket_of(w.s, eid) or "", None) if basket_of(w.s, eid) else []:
        rec = w.s["entities"][c]
        if "ground_coffee" in C.food_kind(rec):
            w.mut(c)["spent"] = True


def _spill(w, eid, liters, rid, why):
    state = w.s
    from ..core import add_soil
    parent = state["entities"][eid].get("location")
    if parent in state["entities"]:
        add_soil(w, parent, "stain_coffee", min(1.0, 0.3 + liters))
        pm = w.mut(parent)
        pm["wetness"] = C.rnd(min(1.0, C.num(pm.get("wetness")) + liters * 2), 3)
    if C.harm(w, "coffee_" + why, once="coffee:{}:{}".format(eid, why), appliance=eid):
        w.stat("spills")
        C.counter(w, "coffee_overflows")
    if C.people_near(state, eid, cells=1) and liters > 0.05:
        C.harm(w, "hot_coffee_spill_on_person", stat="burns", once="coffeeburn:" + eid)


def step_coffee_maker(w, eid, dt):
    """Warming plate: holds the carafe at ~80 C; auto-off; an empty glass carafe cracks."""
    state = w.s
    rec = state["entities"][eid]
    if not rec.get("on") or (rec.get("settings") or {}).get("warmer", "on") != "on":
        return
    if not C.powered_state(state, eid):
        return
    machine = rec.get("appliance") or {}
    hc = dict(rec.get("_coffee") or {})
    if machine.get("state") != "running":
        hc["warm_s"] = C.rnd(C.num(hc.get("warm_s")) + dt, 1)
        limit = C.num((rec.get("coffee") or {}).get("warmer_auto_off_s"), 0)
        if limit and hc["warm_s"] >= limit:
            m = w.mut(eid)
            m["on"] = False
            m["display"] = ""
            m["_coffee"] = hc
            C.log(w, "coffee_warmer_auto_off", appliance=eid)
            return
    carafe = carafe_on(state, eid)
    if carafe:
        crec = state["entities"][carafe]
        if C.num(crec.get("liquid_l")) < 0.05:
            hc["empty_hot_s"] = C.rnd(C.num(hc.get("empty_hot_s")) + dt, 1)
            if hc["empty_hot_s"] > 1800 and not crec.get("broken") and C.is_glass(crec):
                cm = w.mut(carafe)
                cm["broken"] = True
                cm["cracked"] = True
                w.stat("breakages")
                C.harm(w, "empty_carafe_cracked_on_warmer", stat="damage", once="carafe:" + carafe, carafe=carafe)
        else:
            hc["empty_hot_s"] = 0.0
            t = C.num(crec.get("liquid_temp_c"), 21.0)
            t = WARMER_C + (t - WARMER_C) * math.exp(-dt / 600.0)
            cm = w.mut(carafe)
            cm["liquid_temp_c"] = C.rnd(t, 2)
            cm["temperature_c"] = C.rnd(t, 2)
            info = dict(crec.get("_coffee") or {})
            info["held_s"] = C.rnd(C.num(info.get("held_s")) + dt, 1)
            cm["_coffee"] = info
    w.mut(eid)["_coffee"] = hc


@after("switch_off", order=67)
def _cm_off(ctx, result):
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target) or {}
    if rec.get("ac_kind") in ("coffee_maker",) and (rec.get("appliance") or {}).get("state") == "running":
        w.mut(target)["appliance"]["state"] = "idle"


# ----------------------------------------------------------------------------------------------
# brew verb (drip / pod / espresso / french press)

def _brew_seconds(ctx):
    return ctx.data.get("seconds", 3.0)


@action("brew", params={"target": "entity", "yield_g": "?number", "seconds": "?number", "steep_s": "?number"},
        duration=_brew_seconds, energy=0.005, noise=0.2, category="physical",
        doc="Brew on a coffee machine: drip/pod -> press Brew (pod: one pod in the holder, lid shut, a big enough cup on the "
            "platform); espresso -> pull a shot with the dosed, tamped portafilter locked in (yield_g target, default 36 g, or "
            "seconds); French press -> steep (steep_s, default 240) then press.")
def brew(ctx):
    ctx.obj(ctx.args["target"], touch=True)
    # kernel access refusals (visibility, reach, permission) above pass unchanged; this domain's own reasons below
    # are generic for an unidentified (look-alike or unfamiliar) target, so they cannot name what it is
    with P.plain_refusals(ctx, ctx.args.get("target")):
        return _brew_body(ctx)


def _brew_body(ctx):
    w = ctx.w
    state = w.s
    target = ctx.args["target"]
    rec = ctx.obj(target, touch=True)
    kind = rec.get("ac_kind")
    if kind == "coffee_maker":
        from ...kernel import physics
        physics.appliance_start(w, target, "brew" if rec.get("liquid") in ("water", None) or C.num(rec.get("liquid_l")) <= 0 else "descale"
                                if rec.get("liquid") in ("descaler", "vinegar") else "brew")
        return "Brewing started"
    if kind == "pod_machine":
        return _pod_brew(ctx, target, rec)
    if kind == "espresso":
        return _espresso_shot(ctx, target, rec)
    if kind == "french_press":
        return _french_press(ctx, target, rec)
    raise ActionError("That is not a coffee machine", "argument")


def _pod_brew(ctx, target, rec):
    w = ctx.w
    state = w.s
    require(C.powered_state(state, target), "No power", "resource")
    holder = next((c for c in C.children(state, target) if state["entities"][c].get("ac_kind") == "pod_holder"), None)
    require(holder is not None, "This machine has no pod holder", "argument")
    hrec = state["entities"][holder]
    require(not hrec.get("open"), "Close the pod holder lid first", "precondition")
    pods = [c for c in C.children(state, holder) if "coffee_pod" in (state["entities"][c].get("tags") or [])]
    ml = C.num((rec.get("settings") or {}).get("cup_ml"), 240)
    require(C.num(rec.get("liquid_l")) * 1000.0 >= ml, "Not enough water in the reservoir", "resource")
    cup = next((c for c in C.children(state, target, "on") if state["entities"][c].get("container") or state["entities"][c].get("liquid_container")), None)
    m = w.mut(target)
    m["liquid_l"] = C.rnd(C.num(m.get("liquid_l")) - ml / 1000.0, 4)
    strength = 0.0
    if pods:
        prec = state["entities"][pods[0]]
        strength = 0.2 if prec.get("pod_used") else 1.0
        w.mut(pods[0])["pod_used"] = True
    rid = C.room_of(state, target)
    out = {"brewed_ml": ml, "strength": strength}
    if cup is None:
        _spill(w, target, ml / 1000.0, rid, "no_cup")
        out["spilled"] = True
    else:
        crec = state["entities"][cup]
        cap = C.num(crec.get("capacity_l"), 0.35) * 1000.0 - C.num(crec.get("liquid_l")) * 1000.0
        add = min(ml, max(0.0, cap))
        if add < ml:
            _spill(w, target, (ml - add) / 1000.0, rid, "cup_overflow")
            out["overflowed"] = True
        cm = w.mut(cup)
        cm["liquid"] = "coffee" if strength > 0 else "hot_water"
        cm["liquid_l"] = C.rnd(C.num(crec.get("liquid_l")) + add / 1000.0, 4)
        cm["liquid_temp_c"] = 85.0
        cm["temperature_c"] = 85.0
        info = {"strength": strength, "decaf": bool(state["entities"][pods[0]].get("decaf")) if pods else False, "ml": add,
                "brewed_t": C.now(w)}
        cm["_coffee"] = info
    ctx.data["seconds"] = 60.0
    if ctx.realistic:
        # what came out, as it looks: a used pod gives pale, weak coffee (no strength figure, no decaf verdict)
        looks = "hot water" if strength <= 0 else "pale, weak coffee" if strength < 0.5 else "dark coffee"
        seen = {k: v for k, v in out.items() if k != "strength"}
        seen["looks"] = looks
        return json.dumps(seen)
    return json.dumps(out)


def _espresso_shot(ctx, target, rec):
    w = ctx.w
    state = w.s
    require(rec.get("on"), "Switch the machine on and let it heat up", "precondition")
    esp = rec.get("espresso") or {}
    require(esp.get("ready"), "The boiler is not up to temperature yet", "precondition")
    pf = ((rec.get("slots") or {}).get("group") or {}).get("item")
    require(pf, "Lock the portafilter into the group head", "precondition")
    prec = state["entities"][pf]
    shot = dict(prec.get("espresso") or {})
    require(not shot.get("puck"), "Knock out the old puck first", "precondition")
    dose = C.num(shot.get("dose_g"))
    require(dose >= 7.0, "Dose the portafilter with ground coffee first", "precondition")
    require(C.num(rec.get("liquid_l")) > 0.1, "The water tank is empty", "resource")
    grind = shot.get("grind", "fine")
    flow = {"extra_fine": 0.7, "fine": 1.35, "medium": 2.6, "medium_coarse": 3.4, "coarse": 4.0}.get(grind, 1.35)
    if not shot.get("tamped"):
        flow = max(flow, 4.5)
    flow *= 18.0 / max(7.0, dose)
    target_yield = C.num(ctx.args.get("yield_g"), 0.0) or 0.0
    if ctx.args.get("seconds"):
        secs = C.num(ctx.args.get("seconds"))
        yield_g = flow * secs
    else:
        yield_g = target_yield or 36.0
        secs = yield_g / flow
    quality = "under-extracted (sour, thin)" if secs < 20 else "over-extracted (bitter)" if secs > 35 else "balanced"
    cup = next((c for c in C.children(state, target, "on") if state["entities"][c].get("container") or state["entities"][c].get("liquid_container")), None)
    rid = C.room_of(state, target)
    if cup is None:
        _spill(w, target, yield_g / 1000.0, rid, "no_cup")
    else:
        cm = w.mut(cup)
        cm["liquid"] = "espresso"
        cm["liquid_l"] = C.rnd(C.num(cm.get("liquid_l")) + yield_g / 1000.0, 4)
        cm["liquid_temp_c"] = 68.0
        cm["_shot"] = {"yield_g": round(yield_g, 1), "time_s": round(secs, 1), "dose_g": dose, "ratio": round(yield_g / dose, 2),
                       "quality": quality, "channeled": not shot.get("tamped")}
    shot["puck"] = True
    w.mut(pf)["espresso"] = shot
    m = w.mut(target)
    m["liquid_l"] = C.rnd(C.num(m.get("liquid_l")) - yield_g / 1000.0 - 0.03, 4)
    ctx.data["seconds"] = secs + 3.0
    return json.dumps({"yield_g": round(yield_g, 1), "time_s": round(secs, 1), "looks": quality})


def _french_press(ctx, target, rec):
    w = ctx.w
    state = w.s
    steep = C.num(ctx.args.get("steep_s"), 240.0)
    grams, fine = grounds_in(state, target)
    water = C.num(rec.get("liquid_l"))
    temp = C.num(rec.get("liquid_temp_c"), 21.0)
    require(water > 0.1 and grams > 0, "Add coarse grounds and hot water first", "precondition")
    m = w.mut(target)
    m["pressed"] = True
    m["liquid"] = "coffee"
    m["_coffee"] = {"strength_g_per_l": round(grams / water, 1), "steep_s": steep, "water_c": round(temp, 1),
                    "sludge": bool(fine), "bitter": steep > 360, "weak": steep < 150 or temp < 85}
    ctx.data["seconds"] = steep + 20.0
    if ctx.realistic:
        # what the robot saw: it timed the steep and pressed; fine grounds cloud the coffee. Water temperature, strength
        # and taste were not measured.
        return json.dumps({"steep_s": steep, "pressed": True, "sludge": bool(fine)})
    return json.dumps(m["_coffee"])


# ----------------------------------------------------------------------------------------------
# espresso machine heating and the wand

def step_espresso(w, eid, dt):
    state = w.s
    rec = state["entities"][eid]
    esp = dict(rec.get("espresso") or {})
    if rec.get("on") and C.powered_state(state, eid) and C.num(rec.get("liquid_l")) > 0.05:
        target = 95.0
    else:
        target = C.room_temp(state, C.room_of(state, eid))
    t = C.num(esp.get("boiler_c"), 21.0)
    t = target + (t - target) * math.exp(-dt / 100.0)
    esp["boiler_c"] = C.rnd(t, 2)
    esp["ready"] = t >= 90.0
    if rec.get("espresso") != esp:
        w.mut(eid)["espresso"] = esp


def _steam_seconds(ctx):
    return ctx.data.get("seconds", 40.0)


@action("steam_milk", params={"target": "entity", "destination": "?entity", "temp_c": {"type": "number", "default": 60.0, "min": 30.0, "max": 90.0},
                              "purge_before": {"type": "bool", "default": True}, "purge_after": {"type": "bool", "default": True},
                              "wipe": {"type": "bool", "default": True}},
        duration=_steam_seconds, energy=0.01, noise=0.5, category="physical",
        doc="Steam milk in a held pitcher on an espresso machine's wand (destination = machine): purge the wand, steam to "
            "temp_c (55-65 C; above 70 C the milk scalds), then wipe (needs a damp cloth in the other hand) and purge.")
def steam_milk(ctx):
    if ctx.args.get("destination"):
        ctx.obj(ctx.args["destination"], touch=True)
    # kernel access refusals (visibility, reach, permission) above pass unchanged; this domain's own reasons below
    # are generic for an unidentified (look-alike or unfamiliar) target, so they cannot name what it is
    with P.plain_refusals(ctx, ctx.args.get("destination")):
        return _steam_milk_body(ctx)


def _steam_milk_body(ctx):
    w = ctx.w
    state = w.s
    pitcher = ctx.args["target"]
    require(ctx.holding(pitcher), "Hold the milk pitcher", "precondition")
    prec = w.ent(pitcher)
    machine = ctx.args.get("destination")
    if not machine:
        machine = next((e for e in sorted(state["entities"]) if state["entities"][e].get("ac_kind") in ("espresso",)
                        and C.room_of(state, e) == state["robot"]["room"]), None)
    require(machine, "No espresso machine here", "precondition")
    mrec = ctx.obj(machine, touch=True)
    require(mrec.get("ac_kind") == "espresso", "Steam on an espresso machine's wand", "argument")
    require(mrec.get("on") and (mrec.get("espresso") or {}).get("ready"), "Switch the machine on and let the boiler heat up", "precondition")
    wand = dict(mrec.get("wand") or {})
    require(not wand.get("clogged"), "The steam wand is clogged with dried milk - clean it first", "precondition")
    require(C.num(prec.get("liquid_l")) > 0.05 and "milk" in str(prec.get("liquid") or ""), "Pour milk into the pitcher first", "precondition")
    goal = float(ctx.args.get("temp_c") or 60.0)
    t0 = C.num(prec.get("liquid_temp_c"), 4.0)
    final = min(80.0, goal + 2.0)
    m = w.mut(pitcher)
    m["liquid_temp_c"] = C.rnd(final, 1)
    m["temperature_c"] = C.rnd(final, 1)
    info = {"temp_c": round(final, 1), "scalded": final > 70.0, "foamed": True, "watery": not ctx.args.get("purge_before", True)}
    if info["watery"]:
        m["liquid_l"] = C.rnd(C.num(m.get("liquid_l")) + 0.005, 4)
    m["_milk"] = info
    if not ctx.args.get("wipe", True):
        wand["milk_residue"] = C.rnd(C.num(wand.get("milk_residue")) + 1.0, 2)
    else:
        cloth = next((h for h in w.held_items() if h != pitcher and (w.ent(h).get("tool_kind") in ("cloth", "towel", "rag")
                                                                       or "cloth" in C.label(w.ent(h), "").lower())), None)
        require(cloth is not None, "Hold a damp cloth in the other hand to wipe the wand", "capability")
        wand["milk_residue"] = 0.0
    if not ctx.args.get("purge_after", True):
        wand["milk_inside"] = C.rnd(C.num(wand.get("milk_inside")) + 0.5, 2)
    wand["clogged"] = C.num(wand.get("milk_residue")) + C.num(wand.get("milk_inside")) >= 1.5
    wand["purged"] = bool(ctx.args.get("purge_after", True))
    w.mut(machine)["wand"] = wand
    ctx.data["seconds"] = 25.0 + max(0.0, final - t0) * 0.6
    if ctx.realistic:
        # steamed by feel through the pitcher: a touch bucket, not the exact milk temperature or a scald verdict; the
        # time taken is the usual time from fridge-cold milk to the target, to the half minute, so the clock does not
        # time how cold this milk actually was
        ctx.data["seconds"] = P.coarse_seconds(25.0 + max(0.0, final - 4.0) * 0.6, 30.0)
        ctx.grant(pitcher, "touch", ["temperature_c"])
        return json.dumps({"pitcher": P.touch_temp(ctx, pitcher, final), "foamed": True, "watery": info["watery"]})
    return json.dumps(info)


# ----------------------------------------------------------------------------------------------
# grinder

def _grind_seconds(ctx):
    return ctx.data.get("seconds", 15.0)


@action("grind_coffee", params={"target": "entity", "destination": "entity", "grams": {"type": "number", "min": 1.0, "max": 120.0},
                                "grind": "?str:extra_fine|fine|medium|medium_coarse|coarse", "tamp": {"type": "bool", "default": False}},
        duration=_grind_seconds, energy=0.005, noise=0.6, category="physical",
        doc="Grind whole beans from a burr grinder's hopper into a destination (drip basket with filter, portafilter, French "
            "press, container). grind sets the burrs (espresso fine, drip medium, French press coarse). tamp=true tamps a "
            "portafilter dose level (hold a tamper).")
def grind_coffee(ctx):
    ctx.obj(ctx.args["target"], touch=True)
    if not ctx.holding(ctx.args["destination"]):
        ctx.obj(ctx.args["destination"], touch=True)
    # kernel access refusals (visibility, reach, permission) above pass unchanged; this domain's own reasons below
    # are generic for an unidentified (look-alike or unfamiliar) target, so they cannot name what it is
    with P.plain_refusals(ctx, ctx.args.get("target"), ctx.args.get("destination")):
        return _grind_coffee_body(ctx)


def _grind_coffee_body(ctx):
    w = ctx.w
    state = w.s
    target, dest = ctx.args["target"], ctx.args["destination"]
    rec = ctx.obj(target, touch=True)
    require(rec.get("ac_kind") == "grinder", "Use a coffee grinder", "argument")
    require(C.powered_state(state, target), "The grinder has no power", "resource")
    drec = ctx.obj(dest, touch=True) if not ctx.holding(dest) else w.ent(dest)
    require(drec.get("container") or drec.get("accepts_items"), "That cannot hold grounds", "argument")
    grams = float(ctx.args["grams"])
    beans = [c for c in C.children(state, target) if "coffee_bean" in C.food_kind(state["entities"][c]) or state["entities"][c].get("coffee_beans")]
    have = sum(C.num((state["entities"][b].get("food") or {}).get("qty_g"), C.num(state["entities"][b].get("mass_kg")) * 1000.0) for b in beans)
    ran_out = False
    if ctx.realistic:
        # the grinder grinds what the hopper holds: a refusal at "fewer grams than asked" would weigh the hopper (a
        # free bisection on a scale-tier amount); an empty hopper is plain to see
        require(have >= 1.0, "The hopper is empty", "resource")
        if have < grams:
            grams, ran_out = round(have, 1), True
    else:
        require(have >= grams, "Not enough beans in the hopper ({:.0f} g)".format(have), "resource")
    grind = ctx.args.get("grind") or (rec.get("settings") or {}).get("grind", "medium")
    left = grams
    for b in beans:
        brec = state["entities"][b]
        f = dict(brec.get("food") or {})
        q = C.num(f.get("qty_g"), C.num(brec.get("mass_kg")) * 1000.0)
        take = min(q, left)
        f["qty_g"] = C.rnd(q - take, 2)
        bm = w.mut(b)
        bm["food"] = f
        bm["mass_kg"] = C.rnd(max(0.0, C.num(bm.get("mass_kg")) - take / 1000.0), 4)
        left -= take
        if left <= 0:
            break
    if drec.get("part_type") == "portafilter":
        shot = dict(drec.get("espresso") or {})
        require(not shot.get("puck"), "Knock out the old puck first", "precondition")
        shot["dose_g"] = C.rnd(C.num(shot.get("dose_g")) + grams, 1)
        shot["grind"] = grind
        if ctx.args.get("tamp"):
            tamper = next((h for h in w.held_items() if w.ent(h).get("tool_kind") == "tamper"), None)
            require(tamper is not None, "Hold a tamper to tamp the dose", "capability")
            shot["tamped"] = True
            shot["level"] = True
        else:
            shot["tamped"] = False
        w.mut(dest)["espresso"] = shot
    else:
        existing = next((c for c in C.children(state, dest) if "ground_coffee" in C.food_kind(state["entities"][c]) and not state["entities"][c].get("spent")), None)
        if existing:
            em = w.mut(existing)
            f = dict(em.get("food") or {})
            f["qty_g"] = C.rnd(C.num(f.get("qty_g")) + grams, 1)
            em["food"] = f
            em["grind"] = grind
        else:
            extra = {"derived_from": beans[0]} if beans else {}      # a portion looks like its source until that is identified
            w.spawn("generic_item", dest, label="ground coffee", relation="in", grind=grind, mass_kg=round(grams / 1000.0, 4),
                    volume_l=round(grams / 360.0, 4), size_m=[0.06, 0.06, 0.02], category="food",
                    food={"kind": "ground_coffee", "cat": "beverage", "phase": "powder", "qty_g": round(grams, 1)}, **extra)
    ctx.data["seconds"] = float(ctx.args["grams"]) / 1.5 + 5.0      # the grinder runs for the dose asked (empty at the end)
    if ran_out:
        return "The hopper ran out of beans before the full dose: ground what was left ({}) into {}".format(grind, dest)
    return "Ground {:.0f} g ({}) into {}".format(grams, grind, dest)


@after("place", order=81)
def _knock_out(ctx, result):
    """Putting the portafilter into a knock box knocks the spent puck out."""
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target) or {}
    parent = w.s["entities"].get(rec.get("location")) or {}
    if rec.get("part_type") == "portafilter" and "knock_box" in (parent.get("tags") or []):
        shot = dict(rec.get("espresso") or {})
        if shot.get("puck"):
            shot.update(puck=False, dose_g=0.0, tamped=False, level=False)
            w.mut(target)["espresso"] = shot
            pm = w.mut(rec["location"])
            pm["pucks"] = int(C.num(pm.get("pucks"))) + 1
