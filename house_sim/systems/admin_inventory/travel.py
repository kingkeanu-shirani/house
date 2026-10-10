"""Travel and bags: weighing luggage with a hook scale (lifted clear of the floor, in the
household's units), luggage tags, leaks when a bag holding an open bottle is carried, and an
airport/road-trip check event that applies carry-on liquid limits (100 ml in one clear 1 L bag),
keeps spare lithium batteries out of checked bags and leaks unsealed bottles under cabin pressure.
"""
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, hook
from ..core import release_from_hands, subtree_mass
from .common import admin_facts, household, need_tool, units_metric
from .perceive import dumps

WATER_SENSITIVE = ("paper", "cardboard", "fabric", "electronics", "leather")


def _is_bag(rec):
    return rec.get("category") in ("luggage",) or rec.get("type") in ("bag", "backpack", "work_bag") or "luggage" in rec.get("is_a", [])


@action("weigh_luggage", params={"target": "entity", "scale": "?entity", "lift": {"type": "bool", "default": True}},
        duration=8.0, energy=0.01, category="physical",
        doc="Hook a held luggage scale on a bag's handle and lift until it hangs clear of the floor; the scale reads the total mass (bag and contents) in its units. lift=false reads it while the bag still rests on the floor (under-reads).")
def weigh_luggage(ctx):
    w = ctx.w
    target = ctx.args["target"]
    bag = ctx.obj(target, touch=True)
    require(bag.get("portable"), "That cannot be lifted", "precondition")
    scale = need_tool(ctx, "weigh_luggage", ctx.args.get("scale"), "Hold a luggage scale")
    s = w.ent(scale)
    mass = subtree_mass(w, target)
    require(mass <= float(s.get("capacity_kg", 50.0)), "Over the scale's capacity", "precondition")
    if ctx.args["lift"]:
        require(mass <= float(w.robot.get("max_mass_kg", 20.0)) * 1.6, "Too heavy for the robot to lift on the scale; use a bathroom scale", "safety")
        reading = mass
    else:
        reading = mass * 0.35
    resolution = float(s.get("resolution_kg", 0.05))
    reading = round(round(reading / resolution) * resolution, 3)
    units = (s.get("settings") or {}).get("units", "kg" if units_metric(w.s) else "lb")
    shown = "{:.2f} kg".format(reading) if units == "kg" else "{:.1f} lb".format(reading * 2.20462)
    w.mut(scale)["display"] = shown
    result = {"target": target, "reading": shown, "kg": reading if units == "kg" else None,
              "lb": round(reading * 2.20462, 1) if units == "lb" else None, "lifted": ctx.args["lift"]}
    admin_facts(w)["last_weigh"] = result
    if not w.children(target):
        ctx.grant(target, "measure", ["mass_kg"])     # an empty bag: the reading is the bag's own mass (to 50 g)
    return dumps(ctx.env, result)


@action("tag_luggage", params={"target": "entity", "tag": "entity", "text": "text"}, duration=20.0, energy=0.002,
        category="physical",
        doc="Write owner details on a held luggage tag (needs a held pen, or a pre-filled tag) and loop it through the bag's handle.")
def tag_luggage(ctx):
    w = ctx.w
    target, tag = ctx.args["target"], ctx.args["tag"]
    bag = ctx.obj(target, touch=True)
    require(_is_bag(bag), "Tags go on bags and suitcases", "argument")
    require(ctx.holding(tag), "Hold the luggage tag", "precondition")
    t = w.ent(tag)
    require(t.get("type") == "luggage_tag" or "luggage_tag" in t.get("is_a", []), "That is not a luggage tag", "argument")
    text = ctx.args["text"].strip()
    if not t.get("tag_text"):
        need_tool(ctx, "write", None, "Hold a pen to fill in the tag")
    release_from_hands(w, tag)
    tm = w.mut(tag)
    tm["location"], tm["relation"] = target, "attached"
    tm.pop("held_hand", None)
    tm["tag_text"] = text
    bm = w.mut(target)
    bm["tagged"] = True
    bm["tag_text"] = text
    return "Tagged {}".format(target)


def _leak(w, container, bottle, fraction, reason):
    b = w.mut(bottle)
    lost = round(float(b.get("liquid_l", 0.0)) * fraction, 4)
    if lost <= 0:
        return []
    b["liquid_l"] = round(float(b["liquid_l"]) - lost, 4)
    b["leaked"] = True
    soaked = []
    for item in w.descendants(container):
        if item == bottle:
            continue
        rec = w.ent(item)
        if rec.get("material") in WATER_SENSITIVE or rec.get("category") in ("document", "electronics") or rec.get("water_sensitive"):
            m = w.mut(item)
            m["wetness"] = round(min(1.0, float(m.get("wetness", 0.0)) + min(1.0, lost * 20)), 3)
            if m["wetness"] > 0.3:
                m["water_damaged"] = True
                m["damaged"] = True
                soaked.append(item)
    cm = w.mut(container)
    cm["wetness"] = round(min(1.0, float(cm.get("wetness", 0.0)) + 0.3), 3)
    w.emit("bag_leak", target=container, bottle=bottle, liters=lost, soaked=soaked, reason=reason)
    if soaked:
        w.stat("damage")
    return soaked


def _open_bottles(w, container):
    found = []
    for item in w.descendants(container):
        rec = w.ent(item)
        if rec.get("liquid_container") and float(rec.get("liquid_l", 0.0)) > 0.0 and not w.ent(item).get("location") == "robot":
            if (rec.get("openable") and rec.get("open")) or rec.get("cap_loose") or rec.get("leaky"):
                found.append(item)
    return found


@after("pick_up", order=75)
def _carry_leak(ctx, result):
    w = ctx.w
    target = ctx.args["target"]
    rec = w.ent(target)
    if not rec.get("accepts_items") or not rec.get("portable"):
        return
    for bottle in _open_bottles(w, target):
        _leak(w, target, bottle, 0.3, "carried with an open bottle")


@hook("event:admin_travel_check")
def _travel_check(w, args, env):
    """Airport screening / trip: args {mode: flight|car, carry_on: [bags], checked: [bags]}."""
    mode = args.get("mode", "flight")
    result = {"mode": mode, "confiscated": [], "violations": [], "leaks": []}
    if mode == "flight":
        for bag in args.get("carry_on", []):
            if bag not in w.s["entities"]:
                continue
            liquids_bags = [b for b in w.descendants(bag) if w.ent(b).get("luggage_class") == "liquids_bag"]
            for item in w.descendants(bag):
                rec = w.ent(item)
                if not (rec.get("liquid_container") or rec.get("gel") or rec.get("liquid_product")):
                    continue
                if rec.get("medication") or rec.get("exempt_liquid"):
                    continue
                size = float(rec.get("capacity_l", rec.get("liquid_l", 0.0)) or 0.0)
                inside_clear_bag = any(w.within(item, lb) for lb in liquids_bags)
                if size > 0.1:
                    result["confiscated"].append(item)
                    for sub in w.descendants(item):
                        w.destroy(sub)
                    w.destroy(item)
                elif not inside_clear_bag:
                    result["violations"].append("{} not in the clear liquids bag".format(item))
        for bag in args.get("checked", []):
            if bag not in w.s["entities"]:
                continue
            for item in w.descendants(bag):
                rec = w.ent(item)
                if rec.get("lithium_spare") or rec.get("power_bank"):
                    result["violations"].append("{} spare lithium battery in a checked bag".format(item))
                    w.stat("safety_violations")
        for bag in list(args.get("carry_on", [])) + list(args.get("checked", [])):
            if bag not in w.s["entities"]:
                continue
            for item in w.descendants(bag):
                rec = w.ent(item)
                if rec.get("liquid_container") and float(rec.get("liquid_l", 0)) > 0 and not (rec.get("sealed_cap") or rec.get("bagged")):
                    result["leaks"].extend(_leak(w, bag, item, 0.15, "cabin pressure change"))
    for bag in list(args.get("carry_on", [])) + list(args.get("checked", [])):
        if bag in w.s["entities"]:
            for bottle in _open_bottles(w, bag):
                result["leaks"].extend(_leak(w, bag, bottle, 0.5, "open bottle in transit"))
    household(w).setdefault("travel_checks", []).append(result)
    w.emit("travel_check", **result)
    return result
