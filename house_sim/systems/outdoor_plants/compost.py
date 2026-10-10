"""Compost bins, tumblers and worm bins (fidelity P-approx decomposition model).

compost = {kind: hot|cold|tumbler|vermi, greens_kg, browns_kg, moisture (water fraction),
           o2 (0..1), temp_c, maturity (0..1), exposed_greens_kg, contaminants {kind: kg},
           particle_cm, last_turned_s, worms (vermi population 0..1), bay, curing}
Carbon:nitrogen ratio uses greens C:N ~20 (C 40%, N 2%) and browns C:N ~60 (C 45%, N 0.75%).
A hot pile heats toward ambient + 45 C when C:N is 25-35, moisture 0.4-0.6 and oxygen is
high; oxygen is consumed over ~4 days, after which the pile cools and goes anaerobic (sour
smell) unless turned. Turning restores oxygen but drops temperature ~10 C; turning daily
keeps the pile from heating. Turning a worm bin kills worms; forking into a nest stings.
Items placed in a bin are classified (greens, browns or contaminants: meat, dairy, oily food,
pet waste, glossy/plastic, diseased plants, seeding weeds, invasive roots, treated wood,
coal ash, and citrus/onion for worm bins). Fresh greens stay exposed (flies, rats) until
covered with browns or buried by turning.
"""
import math

from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, inspector, observer, system
from . import common as C

GREEN_WORDS = ("vegetable", "fruit", "peel", "coffee", "tea", "eggshell", "grass", "scrap", "lettuce", "apple", "banana", "carrot", "potato", "tomato", "bread", "rice", "pasta")
MEAT_WORDS = ("meat", "chicken", "beef", "pork", "fish", "bone", "bacon")
DAIRY_WORDS = ("cheese", "milk", "yogurt", "butter", "cream", "dairy")
CONTAM_ALWAYS = {"meat", "dairy", "oil", "pet_waste", "glossy_paper", "plastic", "diseased", "weed_seeds", "invasive_roots", "treated_wood", "coal_ash", "metal", "glass"}


def is_bin(record):
    return C.is_type(record, "compost_bin")


def classify(record):
    """Returns (class, kg). class is 'green', 'brown' or a contaminant name."""
    kg = float(record.get("mass_kg", 0.1))
    explicit = record.get("compost_class")
    if explicit:
        return explicit, kg
    product = C.pinfo(record)
    pkind = str(product.get("kind", ""))
    if pkind:
        if pkind in ("mulch", "bark", "straw", "leaf_mold", "compost", "potting_mix", "garden_soil", "topsoil", "seed_mix", "cactus_mix"):
            return "brown", kg
        if pkind in ("lawn_fertilizer", "lime", "rock_salt", "ice_melt", "ice_melt_pet_safe", "herbicide", "insecticide", "liquid_fertilizer"):
            return "chemical", kg
    if C.is_type(record, "leaf_pile"):
        leaves, clips = float(record.get("leaves_kg", 0.0)), float(record.get("clippings_kg", 0.0))
        return ("green" if clips > leaves else "brown"), max(kg, leaves + clips)
    debris = record.get("debris")
    if debris:
        if debris.get("diseased"):
            return "diseased", kg
        if debris.get("seeding"):
            return "weed_seeds", kg
        if debris.get("invasive"):
            return "invasive_roots", kg
        if debris.get("toxic"):
            return "diseased", kg
        return ("green" if debris.get("green", True) else "brown"), kg
    if record.get("category") in ("pet_waste", "biohazard") or "pet_waste" in record.get("tags", []):
        return "pet_waste", kg
    food = record.get("food") or {}
    text = " ".join(str(v) for v in (record.get("label", ""), record.get("type", ""), food.get("kind", ""), record.get("category", ""))).lower()
    if any(word in text for word in MEAT_WORDS):
        return "meat", kg
    if any(word in text for word in DAIRY_WORDS):
        return "dairy", kg
    if "oil" in text or food.get("oily"):
        return "oil", kg
    if "citrus" in text or "orange" in text or "lemon" in text or "lime" in text and food:
        return "citrus", kg
    if "onion" in text or "garlic" in text:
        return "onion", kg
    if record.get("produce"):
        return ("diseased" if record.get("diseased") or record.get("moldy") else "green"), kg
    if C.is_type(record, "pumpkin"):
        return ("diseased" if record.get("painted") else "green"), kg
    if food or any(word in text for word in GREEN_WORDS):
        return "green", kg
    material = str(record.get("material", ""))
    if material == "paper" and (record.get("glossy") or "magazine" in text or "glossy" in text):
        return "glossy_paper", kg
    if material in ("paper", "cardboard") or "leaves" in text or "straw" in text or ("sawdust" in text and not record.get("treated")):
        return "brown", kg
    if record.get("treated"):
        return "treated_wood", kg
    if material in ("plastic", "polyethylene", "pvc"):
        return "plastic", kg
    if material in ("glass",):
        return "glass", kg
    if material in ("steel", "aluminum", "metal", "tin"):
        return "metal", kg
    if material in ("produce", "plant", "wood"):
        return "green" if material != "wood" else "brown", kg
    return "unknown", kg


def cn_ratio(comp):
    g = float(comp.get("greens_kg", 0.0))
    b = float(comp.get("browns_kg", 0.0))
    if g + b <= 1e-6:
        return 0.0
    carbon = 0.40 * g + 0.45 * b
    nitrogen = 0.02 * g + 0.0075 * b
    return round(carbon / max(1e-6, nitrogen), 2)


def compost_status(w, bid):
    rec = w.s["entities"][bid]
    comp = rec.get("compost") or {}
    cn = cn_ratio(comp)
    contaminants = {k: v for k, v in (comp.get("contaminants") or {}).items() if float(v) > 0}
    bad = sorted(k for k in contaminants if k in CONTAM_ALWAYS or (comp.get("kind") == "vermi" and k in ("citrus", "onion")))
    values = {"cn": cn, "cn_ok": 25.0 <= cn <= 35.0, "moisture_ok": 0.4 <= float(comp.get("moisture", 0.5)) <= 0.6,
              "anaerobic": float(comp.get("o2", 0.6)) < 0.3 or float(comp.get("moisture", 0.5)) > 0.7,
              "exposed_greens_kg": round(float(comp.get("exposed_greens_kg", 0.0)), 3), "contaminants": bad,
              "pest_attraction": float(comp.get("exposed_greens_kg", 0.0)) > 0.3 or bool(set(bad) & {"meat", "dairy", "oil"}),
              "temp_c": round(float(comp.get("temp_c", 20.0)), 1), "maturity": round(float(comp.get("maturity", 0.0)), 3),
              "due": turning_due(w, rec)}
    if comp.get("kind") == "vermi":
        values["worms"] = round(float(comp.get("worms", 1.0)), 3)
    C.set_status(w, bid, **values)


def turning_due(w, rec):
    comp = rec.get("compost") or {}
    if comp.get("kind") in ("vermi",) or comp.get("passive") or comp.get("curing"):
        return False
    since = float(w.s.get("time_s", 0.0)) - float(comp.get("last_turned_s", -1e9))
    return since >= 5 * C.DAY or float(comp.get("o2", 0.6)) < 0.4 or float(comp.get("moisture", 0.5)) > 0.7


def add_to_pile(w, bid, cls, kg, cover=True):
    m = w.mut(bid)
    comp = m.setdefault("compost", {})
    if cls == "green":
        comp["greens_kg"] = round(float(comp.get("greens_kg", 0.0)) + kg, 3)
        comp["exposed_greens_kg"] = round(float(comp.get("exposed_greens_kg", 0.0)) + kg, 3)
        comp["moisture"] = round(min(0.9, float(comp.get("moisture", 0.5)) + kg * 0.004), 4)
    elif cls == "brown":
        comp["browns_kg"] = round(float(comp.get("browns_kg", 0.0)) + kg, 3)
        exposed = float(comp.get("exposed_greens_kg", 0.0))
        comp["exposed_greens_kg"] = round(max(0.0, exposed - kg * 2.0), 3)
        comp["moisture"] = round(max(0.1, float(comp.get("moisture", 0.5)) - kg * 0.006), 4)
    else:
        add_contaminant(w, bid, cls, kg)
    if comp.get("bay") == "finished" or comp.get("curing"):
        comp["maturity"] = round(min(float(comp.get("maturity", 0.9)), 0.5), 3)
        C.set_status(w, bid, finished_contaminated=True)
        C.stat(w, "finished_compost_contaminated")


def add_contaminant(w, bid, cls, kg):
    m = w.mut(bid)
    comp = m.setdefault("compost", {})
    cont = comp.setdefault("contaminants", {})
    cont[cls] = round(float(cont.get(cls, 0.0)) + kg, 3)
    if cls in CONTAM_ALWAYS or (comp.get("kind") == "vermi" and cls in ("citrus", "onion")):
        C.stat(w, "compost_contamination")
        C.note(w, "compost_contaminated", bin=bid, material=cls)
    compost_status(w, bid)


def maybe_absorb(w, eid):
    rec = w.s["entities"].get(eid)
    if rec is None or rec.get("_composted"):
        return
    loc = rec.get("location")
    parent = w.s["entities"].get(loc)
    if parent is None or not is_bin(parent):
        return
    for item in [eid] + w.descendants(eid):
        irec = w.s["entities"][item]
        if irec.get("_composted"):
            continue
        cls, kg = classify(irec)
        if C.is_type(irec, "leaf_bag"):
            fill = irec.get("fill") or {}
            add_to_pile(w, loc, "brown", float(fill.get("leaves_kg", 0.0)))
            add_to_pile(w, loc, "green", float(fill.get("clippings_kg", 0.0)))
            if irec.get("material") in ("plastic",):
                add_contaminant(w, loc, "plastic", float(irec.get("mass_kg", 0.2)))
            w.mut(item)["_composted"] = True
            continue
        add_to_pile(w, loc, cls, kg)
        large = float((irec.get("debris") or {}).get("max_piece_cm", 5.0))
        if large > 20 or C.is_type(irec, "branch"):
            comp = w.mut(loc)["compost"]
            comp["particle_cm"] = round(max(float(comp.get("particle_cm", 5.0)), min(60.0, large)), 2)
            C.set_status(w, loc, large_pieces=True)
        w.mut(item)["_composted"] = True
        w.mut(item)["_compost_class"] = cls
    compost_status(w, loc)


@after("place", order=154)
def _after_place_compost(ctx, result):
    target = ctx.args.get("target")
    if target and target in ctx.w.s["entities"]:
        maybe_absorb(ctx.w, target)


@after("pick_up", order=154)
def _after_pick_up_compost(ctx, result):
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target or "", {})
    if not rec.get("_composted"):
        return
    # pulling something back out of the bin removes it from the pile accounting
    cls = rec.get("_compost_class", "green")
    for bid in C.find_types(w, ["compost_bin"]):
        comp = w.s["entities"][bid].get("compost") or {}
        cont = comp.get("contaminants") or {}
        if cls in cont and float(cont[cls]) > 0:
            m = w.mut(bid)
            m["compost"]["contaminants"][cls] = round(max(0.0, float(cont[cls]) - float(rec.get("mass_kg", 0.1))), 3)
            compost_status(w, bid)
            break
    m = w.mut(target)
    m.pop("_composted", None)
    m.pop("_compost_class", None)


def lid_opened(ctx, bid):
    w = ctx.w
    rec = w.s["entities"][bid]
    if rec.get("nest") == "wasp":
        C.stat(w, "disturbed_nest")
        C.note(w, "wasps_disturbed", bin=bid)


def turn(ctx, bid, tool_id, rotations, add_water_l, add_browns):
    w = ctx.w
    rec = w.ent(bid)
    comp = dict(rec.get("compost") or {})
    kind = comp.get("kind", "hot")
    if kind == "tumbler":
        require(rotations and rotations >= 1, "Give the number of tumbler rotations", "argument")
    else:
        require(tool_id is not None, "Hold a garden fork or compost aerator", "capability")
        require(rec.get("open") or not rec.get("openable"), "Open the bin first", "precondition")
    now = float(w.s.get("time_s", 0.0))
    since = now - float(comp.get("last_turned_s", -1e9))
    m = w.mut(bid)
    c = m["compost"]
    values = {}
    if rec.get("nest"):
        C.stat(w, "injury")
        C.stat(w, "disturbed_nest")
        values["nest_disturbed"] = rec.get("nest")
        C.note(w, "forked_into_nest", bin=bid, nest=rec.get("nest"))
    if kind == "vermi":
        c["worms"] = round(float(c.get("worms", 1.0)) * 0.3, 3)
        C.stat(w, "wildlife_harm")
        values["worms_disturbed"] = True
    if since < 2 * C.DAY:
        C.stat(w, "turned_too_often")
        values["turned_too_often"] = True
        c["temp_c"] = round(float(c.get("temp_c", 30.0)) - 8.0, 2)
    if comp.get("curing"):
        values["curing_disturbed"] = True
        c["maturity"] = round(float(c.get("maturity", 0.9)) * 0.9, 3)
    if comp.get("passive"):
        values["passive_pile_turned"] = True
    c["o2"] = 1.0
    c["temp_c"] = round(float(c.get("temp_c", 30.0)) - 6.0, 2)
    c["exposed_greens_kg"] = 0.0
    c["last_turned_s"] = now
    if add_water_l:
        # realistic: the robot pours what it holds (which liquid is in an unlabelled can must not decide a refusal)
        any_liquid = C.realistic(ctx)
        src = C.find_held(ctx, lambda r: r.get("liquid_container") and (any_liquid or r.get("liquid") in ("water", "rainwater")) and float(r.get("liquid_l", 0)) > 0)
        require(src is not None, "Hold a can with water to wet the pile", "resource")
        have = float(w.ent(src).get("liquid_l", 0.0))
        use = min(have, float(add_water_l))
        sm = w.mut(src)
        sm["liquid_l"] = round(have - use, 3)
        total = float(c.get("greens_kg", 0.0)) + float(c.get("browns_kg", 0.0))
        c["moisture"] = round(min(0.9, float(c.get("moisture", 0.5)) + use / max(5.0, total)), 4)
    if add_browns:
        brec = w.ent(add_browns)
        require(ctx.holding(add_browns) or C.reachable(ctx, add_browns, 2), "Bring the browns within reach", "precondition")
        cls, kg = classify(brec)
        add_to_pile(w, bid, cls, kg)
        from . import garden
        garden.destroy_with_children(w, add_browns) if not ctx.holding(add_browns) else None
        if ctx.holding(add_browns):
            from ..core import release_from_hands
            release_from_hands(w, add_browns)
            w.destroy(add_browns)
    values["turned"] = True
    C.set_status(w, bid, **values)
    compost_status(w, bid)
    ctx.spend(240.0 if kind != "tumbler" else 6.0 * float(rotations or 5))


def harvest_compost(ctx, bid, dest_id, liters, sift, return_to):
    w = ctx.w
    rec = w.ent(bid)
    comp = rec.get("compost") or {}
    dest = w.ent(dest_id)
    require(dest.get("container") and dest.get("accepts_items"), "Shovel compost into a bucket, wheelbarrow or bin", "argument")
    maturity = float(comp.get("maturity", 0.0))
    finished = float(comp.get("finished_kg", 0.0)) if comp.get("bay") != "finished" else float(comp.get("greens_kg", 0.0)) + float(comp.get("browns_kg", 0.0))
    kg = min(finished if finished > 0 else (float(comp.get("greens_kg", 0.0)) + float(comp.get("browns_kg", 0.0))), liters * 0.55)
    require(kg > 0.1, "There is no compost to collect", "precondition")
    immature = maturity < 0.8 and finished <= 0.1
    oversize = 0.25 if float(comp.get("particle_cm", 5.0)) > 2.0 else 0.08
    m = w.mut(bid)
    c = m["compost"]
    if comp.get("bay") == "finished":
        total = float(c.get("greens_kg", 0.0)) + float(c.get("browns_kg", 0.0))
        f = max(0.0, 1.0 - kg / max(1e-6, total))
        c["greens_kg"] = round(float(c.get("greens_kg", 0.0)) * f, 3)
        c["browns_kg"] = round(float(c.get("browns_kg", 0.0)) * f, 3)
    elif finished > 0:
        c["finished_kg"] = round(finished - kg, 3)
    else:
        c["greens_kg"] = round(max(0.0, float(c.get("greens_kg", 0.0)) - kg / 2.0), 3)
        c["browns_kg"] = round(max(0.0, float(c.get("browns_kg", 0.0)) - kg / 2.0), 3)
    chunks = kg * oversize if sift else 0.0
    if sift:
        require(C.nearby_entity(ctx, lambda r: r.get("tool_kind") == "compost_sieve", 2), "Set a compost sieve over the container first", "precondition")
        if return_to and return_to in w.s["entities"] and is_bin(w.ent(return_to)):
            add_to_pile(w, return_to, "brown", chunks)
            add_to_pile(w, return_to, "green", 0.0)
        else:
            C.stat(w, "compost_chunks_discarded")
    imm = (1.0 if immature else 0.05) if not sift else (0.6 if immature else 0.02)
    product = C.spawn(w, "garden_product", dest_id, eid=w.new_id("finished_compost"), relation="in", label="finished compost" if not immature else "half-rotted compost",
                      product={"kind": "compost", "qty": round((kg - chunks) / 0.55, 2), "unit": "l", "immature_frac": round(imm, 3)},
                      mass_kg=round(kg - chunks, 3), volume_l=round((kg - chunks) / 0.55, 2), size_m=[0.3, 0.3, 0.2])
    if immature:
        C.set_status(w, bid, immature_harvested=True)
        C.stat(w, "immature_compost_harvested")
    compost_status(w, bid)
    ctx.spend(120.0 + kg * 4.0)
    return product


def slow_compost(w, periods):
    dt = C.SLOW_S * periods
    days = dt / C.DAY
    ambient = C.air_temp(w)
    for bid in C.find_types(w, ["compost_bin"]):
        rec = w.s["entities"][bid]
        comp = rec.get("compost") or {}
        total = float(comp.get("greens_kg", 0.0)) + float(comp.get("browns_kg", 0.0))
        if total <= 0.1:
            continue
        kind = comp.get("kind", "hot")
        cn = cn_ratio(comp)
        moist = float(comp.get("moisture", 0.5))
        o2 = float(comp.get("o2", 0.6))
        f_cn = math.exp(-((cn - 30.0) / 12.0) ** 2) if cn > 0 else 0.0
        f_m = math.exp(-((moist - 0.5) / 0.15) ** 2)
        f_o = C.clamp(o2 / 0.6, 0.0, 1.0)
        mass_f = C.clamp(total / 150.0, 0.2, 1.0) if kind in ("hot", "tumbler") else 0.25
        target = ambient + (45.0 if kind != "vermi" else 6.0) * f_cn * f_m * f_o * mass_f
        temp = float(comp.get("temp_c", ambient))
        temp += (target - temp) * (1.0 - math.exp(-days / 0.8))
        o2 = max(0.0, o2 - days * (0.18 if temp > 40 else 0.08) * (1.0 + float(comp.get("greens_kg", 0.0)) / max(1.0, total)))
        rate = 0.03 * f_cn * f_m * (0.4 + 0.6 * f_o) * (1.5 if temp > 45 else 1.0) / max(1.0, float(comp.get("particle_cm", 5.0)) / 5.0)
        if kind == "vermi":
            rate = 0.02 * float(comp.get("worms", 1.0))
        m = w.mut(bid)
        c = m["compost"]
        c["temp_c"] = round(temp, 2)
        c["o2"] = round(o2, 4)
        c["maturity"] = round(min(1.0, float(comp.get("maturity", 0.0)) + rate * days), 4)
        c["moisture"] = round(max(0.15, moist - 0.01 * days * (1.0 if temp < 40 else 2.0)), 4)
        if C.raining(w) and not rec.get("openable") or rec.get("open"):
            c["moisture"] = round(min(0.9, c["moisture"] + 0.02 * days * 24), 4)
        compost_status(w, bid)


@system("outdoor_plants.compost", order=132, max_dt=C.SLOW_S, period=C.SLOW_S)
def compost_system(w, dt, tick):
    """Compost pile model: once per 600 s with dt = accumulated time (temperature relaxes exponentially)."""
    slow_compost(w, dt / C.SLOW_S)


@observer("outdoor_plants.smells", order=61)
def smells(env, w, obs):
    rid = w.robot["room"]
    found = []
    for bid in C.find_types(w, ["compost_bin"], room=rid):
        st = w.s["entities"][bid].get("_op_status") or {}
        if st.get("anaerobic"):
            found.append({"kind": "sour_ammonia", "intensity": 0.6})
    for eid in C.find_types(w, ["fire_pit"], room=rid):
        if float(w.s["entities"][eid].get("_ember_c", 0.0)) > 150:
            found.append({"kind": "smoldering_wood", "intensity": 0.4})
    for eid in C.find_types(w, ["pumpkin"], room=rid):
        if float(w.s["entities"][eid].get("rot", 0.0)) > 0.5:
            found.append({"kind": "rotting_produce", "intensity": 0.3})
    if found and C.env_realistic(env):
        # a nose gets one smell per kind, stronger with more sources, as the shared words -- not one entry per source
        # (that would count the sour bins or rotting pumpkins, closed lids included)
        total = {}
        for item in found:
            total[item["kind"]] = total.get(item["kind"], 0.0) + float(item["intensity"])
        found = [{"kind": k, "intensity": "faint" if v < 0.35 else "noticeable" if v < 0.7 else "strong"} for k, v in sorted(total.items())]
    if found:
        obs.setdefault("smells", []).extend(found)


# ---------------------------------------------------------------------------
# realistic perception: looking into (and feeling) a pile instead of reading the hidden compost dict

VISIBLE_CONTAMINANTS = {"meat", "dairy", "oil", "pet_waste", "glossy_paper", "plastic", "treated_wood", "coal_ash", "metal", "glass",
                        "citrus", "onion"}


def turned_feel(record, status):
    """Realistic result of turning: steam and warmth, the smell released (sour when anaerobic, ammonia when too green,
    earthy when balanced) and the texture -- not the C:N ratio or the core temperature in degrees."""
    comp = record.get("compost") or {}
    temp = float(status.get("temp_c", comp.get("temp_c", 20.0)) or 20.0)
    cn = float(status.get("cn", cn_ratio(comp)) or 0.0)
    smell = "sour, rotten-egg" if status.get("anaerobic") else ("ammonia" if 0 < cn < 20 else "earthy")
    moist = float(comp.get("moisture", 0.5))
    return {"turned": True, "steaming": temp > 45.0, "warmth": C.temp_feel(temp), "smell": smell,
            "texture": "dry and dusty" if moist < 0.4 else ("soggy and matted" if moist > 0.65 else "moist and crumbly")}


@inspector("outdoor_plants.compost", order=65)
def inspect_compost(w, eid, record, details, ctx):
    """Realistic mode only (oracle observations already carry the compost dict): with the lid open the pile's look
    (maturity), green/brown balance, exposed scraps, warmth felt by hand and visible contaminants; a held probe
    thermometer with where=closely reads the core temperature (whole degrees). C:N, oxygen and water fraction stay
    hidden: smell (the smells channel), the squeeze test (check_moisture) and turning tell those qualitatively."""
    if not C.realistic(ctx) or not is_bin(record):
        return
    if record.get("openable") and not record.get("open"):
        details["pile"] = "lid closed: open it to look at the pile"
        return
    comp = record.get("compost") or {}
    maturity = float(comp.get("maturity", 0.0))
    details["pile_look"] = "fresh scraps and leaves" if maturity < 0.3 else ("partly decomposed" if maturity < 0.7 else "dark, crumbly, earthy")
    cn = cn_ratio(comp)
    if cn > 0:
        details["greens_browns"] = "mostly wet greens" if cn < 22 else ("mostly dry browns" if cn > 40 else "mixed greens and browns")
    details["scraps_exposed"] = float(comp.get("exposed_greens_kg", 0.0)) > 0.3
    details["warmth"] = C.temp_feel(float(comp.get("temp_c", 20.0)))
    seen = sorted(k for k, v in (comp.get("contaminants") or {}).items() if float(v) > 0 and k in VISIBLE_CONTAMINANTS)
    details["contaminants_seen"] = seen
    if comp.get("kind") == "vermi":
        details["worms"] = "lively worms" if float(comp.get("worms", 1.0)) > 0.5 else "few worms"
    probe = any(w.ent(h).get("tool_kind") == "probe_thermometer" for h in w.held_items())
    if probe and ctx.args.get("where") == "closely":
        reading = ctx.percept_value(eid, "temperature_c", float(comp.get("temp_c", 20.0)), "measure")
        details["core_temp_c"] = int(round(reading if isinstance(reading, (int, float)) else float(comp.get("temp_c", 20.0))))
