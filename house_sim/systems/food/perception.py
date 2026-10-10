"""What the robot can sense about food, what residents can tell it, and global food harms."""
import copy

from ...kernel import clock, geometry
from ...kernel.plugins import answerer, inspector, invariant, observer
from . import data as D
from . import mixing as X
from . import model as M
from . import percept as P
from . import systems as S


def _readable(ctx, w):
    lit = ctx.env.perception.lit(w.s, w.robot["room"])
    return lit and w.robot.get("camera_clean", 1.0) >= 0.4


def _exposed(state, eid):
    rec = state["entities"][eid]
    if rec.get("package") and not rec.get("open"):
        return False
    loc, seen = rec.get("location"), set()
    while loc in state["entities"] and loc not in seen:
        seen.add(loc)
        parent = state["entities"][loc]
        if parent.get("openable") and not parent.get("open") and parent.get("container"):
            return False
        loc = parent.get("location")
    return True


def _food_view(w, eid, rec, closely, ctx):
    f = M.food(rec)
    view = {}
    hidden = set(rec.get("secret_fields", []))
    if "food" not in hidden:
        view["kind"] = f.get("kind")
        for key in ("cut", "pieces", "piece_cv", "phase", "count", "parts", "peeled", "layers", "form", "cooked", "ice",
                    "seeds", "shell_fragments", "yolks_intact", "sheet", "gap_mm", "pulp", "drained", "wet", "torn",
                    "cracked", "burst", "slice_thickness_mm", "slice_form", "grate_side", "particle_mm", "lump_frac",
                    "texture", "skin_in_product", "slivers", "woody_stem_in_product", "clumped_paste", "smeared",
                    "boneless", "shucked", "silk_strands", "cork_bits", "marinade", "proof", "soggy", "browning",
                    "egg_doneness", "toast_level", "grain_texture", "burnt", "scorch"):
            if f.get(key) is not None and f.get(key) != []:
                view[key] = copy.deepcopy(f[key])
        if isinstance(view.get("layers"), list):
            # (layers remember their coating's source in a hidden _src key; never part of the view)
            view["layers"] = [{k: v for k, v in l.items() if not str(k).startswith("_")} if isinstance(l, dict) else l
                              for l in view["layers"]]
        if f.get("piece_mm"):
            view["piece_size_mm"] = [round(v, 0) for v in f["piece_mm"]]
        view["approx_g"] = round(M.qty(rec) / 5.0) * 5.0 if M.qty(rec) > 20 else round(M.qty(rec), 1)
    if float(f.get("yolk_fat_g", 0.0) or 0.0) > 0:
        view["yolk_in_whites"] = True
    view["appearance"] = rec.get("appearance") or M.appearance(rec)
    if _exposed(w.s, eid):
        view["odor"] = rec.get("_food_odor") or M.odor(rec)
    if f.get("mix") is not None or f.get("phase") in ("dough",):
        view["state"] = X.state_words(rec)
    if closely:
        view["firmness"] = _firmness_word(rec)
        if f.get("phase") == "dough":
            view["windowpane"] = "translucent membrane, does not tear" if f.get("windowpane") or \
                D.GLUTEN_WINDOWPANE[0] <= float((f.get("mix") or {}).get("gluten", 0.0)) <= D.GLUTEN_WINDOWPANE[1] else "tears before turning translucent"
        if rec.get("_pin_bones") and "pin_bones" not in f.get("parts", []):
            m = w.mut(eid)
            m["food"]["parts"] = sorted(set(m["food"].get("parts", [])) | {"pin_bones"})
            view["parts"] = m["food"]["parts"]
            view["found"] = "{} pin bones along the fillet".format(int(rec["_pin_bones"]))
        if rec.get("_bulging"):
            view["can"] = "bulging, domed lid"
        if rec.get("_loose_lid"):
            view["lid"] = "lid sits loose on the thread"
        if M.cat(rec) == "egg" and f.get("phase", "solid") == "solid":
            view["shell"] = "intact" if not f.get("cracked") else "cracked"
    if f.get("ripens"):
        r = float(f.get("ripeness", 0.5))
        view["ripeness"] = "unripe" if r < 0.5 else "ripe" if r < 0.95 else "overripe"
    if rec.get("date_label") and _readable(ctx, w):
        view["date_label"] = rec["date_label"]
    if rec.get("opened"):
        view["opened"] = True
    if rec.get("food_label"):
        view["food_label"] = {k: v for k, v in rec["food_label"].items() if k in ("contents", "dates", "owner", "text")}
    return view


def _firmness_word(rec):
    f = M.food(rec) or {}
    if float(f.get("ice", 0.0)) > 0.8:
        return "frozen hard"
    if f.get("ripens"):
        r = float(f.get("ripeness", 0.5))
        return "hard" if r < 0.5 else "yields to gentle pressure" if r < 0.95 else "mushy"
    if float(rec.get("_spoil", 0.0)) >= 1.0 and M.catinfo(rec).get("slime"):
        return "tacky, slimy"
    firm = float(f.get("firmness", 0.6))
    return "firm" if firm > 0.6 else "soft" if firm > 0.25 else "very soft, delicate"


def _probe(w, eid, rec, details, ctx):
    probe = next((h for h in w.held_items() if w.ent(h).get("tool_kind") in ("thermometer", "probe_thermometer", "meat_thermometer")
                  or "probe_temperature" in w.ent(h).get("capabilities", [])), None)
    if probe is None:
        return
    core = float(rec.get("_core_c", rec.get("temperature_c", 21.0)))
    if ctx.realistic:
        # the probe's own precision (its accuracy_c, else the kernel thermometer model, about +-0.3 C); it measures the
        # core, not the surface
        acc = w.ent(probe).get("accuracy_c")
        if isinstance(acc, (int, float)) and not isinstance(acc, bool) and acc > 0:
            details["probe_temp_c"] = P.reading(ctx.env, eid, "probe_temp_c", core, 0.5 * float(acc))
        else:
            reading = ctx.percept_value(eid, "internal_temp_c", core, "measure")
            details["probe_temp_c"] = reading if reading is not None else round(core, 1)
        ctx.grant(eid, "measure", ["_core_c"])
    else:
        details["probe_temp_c"] = round(core, 1)
    M.contact(w, probe, eid, 0.5)
    log = w.sec("food_log")
    contam = M.stored_contam(w.s, probe)
    log.setdefault("probe_readings", []).append({"item": eid, "reading": round(core, 1), "time_s": w.s["time_s"],
                                                "probe_dirty": contam.get("raw_meat", 0.0) >= D.CONTAM_THRESHOLD})
    del log["probe_readings"][:-100]


@inspector("food.details", order=120)
def food_inspector(w, eid, rec, details, ctx):
    if eid in w.s.get("people", {}) or eid in w.s.get("pets", {}):
        return
    if ctx.realistic:
        return _seen_inspector(w, eid, rec, details, ctx)
    closely = ctx.args.get("where") == "closely"
    if M.food(rec):
        details["food"] = _food_view(w, eid, rec, closely, ctx)
        if closely:
            _probe(w, eid, rec, details, ctx)
    foods = M.vessel_foods(w.s, eid)
    if foods and (not rec.get("openable") or rec.get("open") or rec.get("transparent")):
        details["food_contents"] = [dict(_food_view(w, c, w.ent(c), closely, ctx), id=c) for c in foods]
        if rec.get("fill_level") is not None:
            details["fill_level"] = rec["fill_level"]
    if rec.get("tool_kind") == "kitchen_scale":
        details["display"] = S.scale_display(w.s, eid)
    if rec.get("internal_temp_c") is not None and rec.get("food_air_model") and rec.get("display_temp", True):
        details["thermometer_c"] = round(float(rec["internal_temp_c"]), 1)
    if rec.get("liquid_l") and rec.get("dissolved_salt_g"):
        details["salted_water"] = True


# ----------------------------------------------------------------------
# realistic perception (kernel v2.4-v2.8): what a close look, a touch, a sniff and the probe give

SEEN_COUNTS = ("pieces", "count", "seeds", "shell_fragments", "silk_strands", "cork_bits")
SEEN_MM = ("gap_mm", "slice_thickness_mm", "particle_mm")
# what the robot sees of food it has not identified (an unresolved look-alike, a portion poured out of one, an egg in
# its shell): state, never the member's own make-up (kind, phase, cut, parts, firmness...), and a volume, not grams --
# so every member of a group shows the same kind of view
UNIDENTIFIED_VIEW = ("approx_ml", "count", "appearance", "odor", "frozen", "shell", "opened", "date_label", "food_label",
                     "can", "lid", "surface", "found")
# what the outside of a sealed opaque package shows (the amount, cut and condition of what is inside do not)
SEALED_VIEW = ("kind", "appearance", "opened", "date_label", "food_label", "can", "lid", "frozen")
# inspector keys food renders itself (passed through by the kernel's filter; see ctx.perceived exact=)
FOOD_PERCEPTS = ("food_view", "food_contents", "whole_items", "probe_temp_c", "thermometer_c", "frozen")


def _seen_view(w, eid, rec, closely, ctx, at=P.INSPECT):
    """The inspector's food view as perceived (realistic mode): estimates instead of model numbers, words instead of
    quality clocks, no printed/handwritten text before it is read, no core doneness (that needs the probe), no smell
    through an intact eggshell, nothing inside a sealed package, and for something not identified at knowledge level
    `at` (INSPECT for the inspected item itself, the item's own knowledge for things seen inside it) only the
    group-consistent UNIDENTIFIED_VIEW."""
    env = ctx.env
    view = _food_view(w, eid, rec, closely, ctx)
    f = M.food(rec) or {}
    unknown = P.unresolved(env, eid, at=at)
    if not P.has_read(env, eid, "date_label"):
        view.pop("date_label", None)
    if not P.has_read(env, eid, "food_label"):
        view.pop("food_label", None)
    for key in SEEN_COUNTS:
        if isinstance(view.get(key), int) and not isinstance(view.get(key), bool):
            view[key] = P.count(env, eid, key, view[key])
    if "piece_cv" in view:
        view["evenness"] = P.evenness(view.pop("piece_cv"))
    if view.get("piece_size_mm"):
        view["piece_size_mm"] = [P.millimetres(env, eid, "piece_size_mm.{}".format(i), v) for i, v in enumerate(view["piece_size_mm"])]
    for key in SEEN_MM:
        if isinstance(view.get(key), (int, float)) and not isinstance(view.get(key), bool):
            view[key] = P.millimetres(env, eid, key, view[key])
    if "lump_frac" in view:
        view["lumps"] = P.lumps(view.pop("lump_frac"))
    if "ice" in view:
        ice = view.pop("ice")
        if float(ice or 0.0) > 0.02 or f.get("frozen_food") or (M.cat(rec) or "").startswith("frozen"):
            view["frozen"] = P.frozen(ice)
    for key, fn in (("soggy", P.soggy), ("browning", P.browning), ("burnt", P.charring), ("scorch", P.charring), ("proof", P.proof)):
        if isinstance(view.get(key), (int, float)) and not isinstance(view.get(key), bool):
            view[key] = fn(view[key])
    if isinstance(view.get("marinade"), dict):
        view["marinade"] = {"kind": view["marinade"].get("kind")}
    if isinstance(view.get("layers"), list):
        view["layers"] = P.seen_layers(env, f.get("layers"))      # from the record: the hidden _src tells what is known
    if isinstance(view.get("sheet"), dict):
        sheet = dict(view["sheet"])
        for key in list(sheet):
            if key.endswith("_mm") and isinstance(sheet[key], (int, float)):
                sheet[key] = P.millimetres(env, eid, "sheet." + key, sheet[key])
            elif key.endswith("_cv") and isinstance(sheet[key], (int, float)):
                sheet["evenness"] = P.evenness(sheet.pop(key))
        view["sheet"] = sheet
    view.pop("cooked", None)
    look = P.surface_look(rec)
    if look:
        view["surface"] = look
    if P.egg_in_shell(rec):
        view.pop("egg_doneness", None)       # a boiled egg looks like a raw one until it is peeled or cut
        view.pop("odor", None)               # and a rotten egg smells only once it is cracked
        view.pop("firmness", None)           # through the shell both feel hard
        view["appearance"] = "cracked shell" if f.get("cracked") else "clean, intact shell"
    if "approx_g" in view:
        if unknown:
            view.pop("approx_g")
            view["approx_ml"] = P.millilitres(env, eid, "approx_ml", M.volume_ml(rec))
        else:
            view["approx_g"] = P.grams(env, eid, "approx_g", M.qty(rec))
    if P.sealed(rec):
        view = {k: v for k, v in view.items() if k in SEALED_VIEW}
    if unknown:
        view = {k: v for k, v in view.items() if k in UNIDENTIFIED_VIEW}
    return view


def _by_handle(ctx, ids):
    """Realistic listings follow handles, never true ids (the order must not identify look-alikes)."""
    handles = ctx.env.realism.handles
    return sorted(ids, key=lambda e: handles.handle(e))


def _seen_inspector(w, eid, rec, details, ctx):
    """food.details in realistic mode. The food view goes under "food_view" (the raw `food` record is never shown: it
    holds exact grams, mixing numbers and hidden flags), vessel contents under "food_contents" (in handle order). Food
    renders these itself, so they are marked as percepts (details["_perceived"]) for the kernel's filter."""
    closely = ctx.args.get("where") == "closely"
    if M.food(rec):
        details["food_view"] = _seen_view(w, eid, rec, closely, ctx)
        if closely:
            _probe(w, eid, rec, details, ctx)
    foods = M.vessel_foods(w.s, eid)
    if foods and (not rec.get("openable") or rec.get("open") or rec.get("transparent")):
        seen = [c for c in foods if ctx.env.perception.visible(w.s, c)]
        details["food_contents"] = [dict(_seen_view(w, c, w.ent(c), closely, ctx, at=0), id=c) for c in _by_handle(ctx, seen)]
        units = M.whole_items(w.s, eid)
        if units:
            details["whole_items"] = P.count(ctx.env, eid, "whole_items", units)   # counted by eye: exact up to 12
        if rec.get("fill_level") is not None:
            details["fill_level"] = rec["fill_level"]
    dial = rec.get("display_temp") or set(rec.get("tags") or []) & {"refrigerator", "freezer"}
    if rec.get("internal_temp_c") is not None and rec.get("food_air_model") and dial:
        # a refrigerator's dial thermometer (a lunch box, cooler or water bath has none: probe the food instead)
        reading = ctx.percept_value(eid, "internal_temp_c", float(rec["internal_temp_c"]), "measure")
        if reading is not None:
            details["thermometer_c"] = reading
            ctx.grant(eid, "measure", ["internal_temp_c"])
    if rec.get("latent_pack"):
        # squeezing a gel ice pack tells whether it is frozen (its frozen fraction is not a number anyone can see)
        details["frozen"] = P.frozen(rec.get("ice_frac", 0.0))
    mine = [k for k in FOOD_PERCEPTS if k in details]
    if mine:
        details["_perceived"] = sorted(set(details.get("_perceived") or ()) | set(mine))
    # the scale display is read with `weigh` or `read`; dissolved salt is invisible


@observer("food.smells", order=120)
def food_smells(env, w, obs):
    if "olfactory" not in env.sensors:
        return
    state = w.s
    room = w.robot["room"]
    realistic = env.realism is not None
    smells = {}
    for eid in S.food_ids(w):
        rec = state["entities"][eid]
        f = M.food(rec)
        if not f or M.qty(rec) < 5:
            continue
        if geometry.anchor(state, eid)[0] != room or not _exposed(state, eid):
            continue
        if realistic and P.egg_in_shell(rec):
            continue          # an intact shell keeps a rotten egg's smell in until it is cracked
        od = rec.get("_food_odor") or M.odor(rec)
        if od != "normal":
            kind = "rotten_egg" if "sulfur" in od else "spoiled_food"
            smells[kind] = max(smells.get(kind, 0.0), min(1.0, 0.2 + M.qty(rec) / 1000.0))
        burn = max(float(f.get("scorch", 0.0)), float(f.get("burnt", 0.0)))
        if burn > 0.2:
            smells["burning_food"] = max(smells.get("burning_food", 0.0), min(1.0, burn))
        if f.get("kind") in ("onion", "garlic_clove", "fish", "salmon_fillet") and f.get("cut", "whole") != "whole":
            smells["pungent_food"] = max(smells.get("pungent_food", 0.0), 0.3)
    if smells:
        channel = obs.setdefault("smells", [])
        for kind, intensity in sorted(smells.items()):
            if realistic:
                # a nose (or gas sensor) gives a smell class and a rough strength for the room, not a source or amount
                strength = "faint" if intensity < 0.35 else "noticeable" if intensity < 0.7 else "strong"
                channel.append({"kind": kind, "intensity": strength, "source_hint": room})
            else:
                channel.append({"kind": kind, "intensity": round(intensity, 2), "source_hint": room})


@answerer("food:")
def food_answers(env, w, pid, topic, ctx):
    """Residents answer food questions from their own profile: allergies, diet, likes, dislikes, orders,
    the household food rules (best_before policy, scraps for stock, storage habits)."""
    person = w.s.get("people", {}).get(pid)
    if person is None:
        return None
    parts = topic.split(":")
    key = parts[1] if len(parts) > 1 else ""
    sim = w.s.get("user_simulator", {}).get("by_person", {}).get(pid, {})
    if topic in sim:
        value = copy.deepcopy(sim[topic])
    elif key == "allergies":
        value = sorted(person.get("allergies", []) or [])
    elif key == "diet":
        diet = person.get("diet") or []
        value = [diet] if isinstance(diet, str) else sorted(diet)
    elif key in ("likes", "dislikes", "order", "preference"):
        prefs = (person.get("preferences") or {}).get("food", {})
        sub = parts[2] if len(parts) > 2 else None
        if key == "order" or key == "preference":
            value = copy.deepcopy((prefs.get("orders") or {}).get(sub)) if sub else copy.deepcopy(prefs.get("orders"))
        else:
            value = sorted(prefs.get(key, []))
    elif key == "rule":
        rules = (w.s.get("household") or {}).get("food_rules") or {}
        sub = parts[2] if len(parts) > 2 else None
        value = copy.deepcopy(rules.get(sub)) if sub else copy.deepcopy(rules)
    elif key == "age":
        value = person.get("age")
    else:
        return None
    facts = w.s.get("facts", {})
    known = (pid + ":" + topic) in facts.get("asked", {}) or topic in facts or \
        any(k.startswith("food:" + pid) or k == topic for k in (w.s.get("memory") or {}))
    w.stat("unnecessary_questions" if known or value in (None, [], {}) else "necessary_questions")
    w.sec("facts")[topic] = copy.deepcopy(value)
    return {"topic": topic, "value": value, "by": pid}


# ----------------------------------------------------------------------
# global harms (compared with the episode baseline so pre-existing problems never fail a task)

def _is_food(rec):
    return isinstance(rec.get("food"), dict)


def _is_drain(rec):
    return bool(rec.get("drain"))


def _ids(state, world, name, pred):
    """Candidate ids from the World's static index (cached until entities are created/destroyed); a full scan only
    for callers that evaluate a bare state without a World."""
    ents = state["entities"]
    if world is not None and getattr(world, "s", None) is state:
        return [e for e in world.static_index(name, pred) if e in ents]
    return [e for e in sorted(ents) if pred(ents[e])]


@invariant("food.rte_cross_contamination")
def rte_cross_contamination(state, baseline, world):
    before = baseline.get("entities", {})
    ents = state["entities"]
    for eid in _ids(state, world, "food.foods", _is_food):
        rec = ents[eid]
        f = M.food(rec)
        if not f or not f.get("rte") or rec.get("type") == "prep_scraps" or rec.get("_zone") in ("trash", "compost"):
            continue
        now = float((rec.get("contamination") or {}).get("raw_meat", 0.0))
        if now < 0.1:
            continue
        old = float((before.get(eid, {}).get("contamination") or {}).get("raw_meat", 0.0)) if eid in before else 0.0
        if now > old + 0.05:
            return "food.rte_cross_contamination"
    return None


@invariant("food.cold_chain_break")
def cold_chain_break(state, baseline, world):
    before = baseline.get("entities", {})
    ents = state["entities"]
    for eid in _ids(state, world, "food.foods", _is_food):
        rec = ents[eid]
        if not rec.get("_abused") or rec.get("_zone") in ("trash", "compost"):
            continue
        old = before.get(eid)
        if old is None or old.get("_abused"):
            continue
        if M.zone_of(baseline, eid)["zone"] in ("fridge", "freezer", "cooler", "lunchbox") or old.get("location") == "robot":
            return "food.cold_chain_break"
    return None


@invariant("food.solids_down_drain")
def solids_down_drain(state, baseline, world):
    before = baseline.get("entities", {})
    ents = state["entities"]
    for eid in _ids(state, world, "food.drains", _is_drain):
        rec = ents[eid]
        if rec.get("_solids_g") and float(rec["_solids_g"]) > float(before.get(eid, {}).get("_solids_g", 0.0)) + 50.0:
            return "food.solids_down_drain"
    return None


@invariant("food.fat_down_drain")
def fat_down_drain(state, baseline, world):
    before = baseline.get("entities", {})
    ents = state["entities"]
    for eid in _ids(state, world, "food.drains", _is_drain):
        rec = ents[eid]
        if rec.get("_fat_g") and float(rec["_fat_g"]) > float(before.get(eid, {}).get("_fat_g", 0.0)) + 20.0:
            return "food.fat_down_drain"
    return None
