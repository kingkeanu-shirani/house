"""Surface cleaning actions: apply_cleaner, wipe, scrub, degrease, dust, polish, condition,
squeegee, rinse, dry, disinfect, descale, treat_upholstery, scrape.

Every action works on any entity with a soil dict (counters, tables, doors, windows, appliances,
fixtures, upholstery, dishes, tools...) using its material/finish, faces and current state.
"""
import math

from ...kernel import geometry, physics
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from . import engine as E
from . import model as M
from . import state as S

WIPE_TOOLS = ("cloth", "towel", "paper", "wipe", "sponge", "pad")
SCRUB_TOOLS = ("sponge", "pad", "brush", "cloth", "paper", "scraper")
DUST_TOOLS = ("duster", "cloth", "brush", "air", "roller", "towel")
FACE_PARAM = "?str"


def tool_wetness(w, tool):
    rec = w.ent(tool)
    wet = float(rec.get("wetness", 0.0) or 0.0)
    if rec.get("cl_chem"):
        wet = max(wet, 0.3)
    if S.tool_spec(rec).get("kind") == "wipe":
        wet = max(wet, 0.45)
    return wet


def surface_floor_cell(w, eid, count=1):
    """Floor cell(s) next to a piece of furniture (closest to the robot) where things fall off it."""
    rid = w.root_room(eid)
    if rid is None or rid not in w.s["rooms"]:
        return None, None
    current = eid
    while current in w.s["entities"] and w.ent(current).get("location") != rid:
        current = w.ent(current).get("location")
    rec = w.s["entities"].get(current)
    cells = geometry.footprint(rec) if rec else []
    room = w.s["rooms"][rid]
    rpos = tuple(w.robot.get("pos") or (0, 0)) if w.robot["room"] == rid else (0, 0)
    ring = sorted(geometry.near_cells(cells, room) - set(cells), key=lambda c: (geometry.chebyshev(c, rpos), c[1], c[0])) if cells else []
    floor = S.floor_of(w, rid)
    if count > 1:
        return floor, [S.ck(c) for c in ring[:count]] or ([S.ck(cells[0])] if cells else [])
    return floor, (S.ck(ring[0]) if ring else (S.ck(cells[0]) if cells else None))


def drop_loose(ctx, eid, loose, into=None, capture=0.0, tool=None, area=1.0):
    """Route loose debris removed from a surface: tool, a container, or the floor below."""
    w = ctx.w
    fine = ("dust", "hair", "pet_hair", "lint", "pollen", "cobweb", "ash", "drywall_dust")
    to_floor = {}
    for kind, level in sorted(loose.items()):
        grams = M.grams(kind, level, area)
        if grams <= 0:
            continue
        if into:
            deposit_waste(w, into, {kind: grams}, source=eid)
            continue
        taken = capture if kind in fine else (0.5 if kind == "glitter" else 0.25 * capture)
        if tool:
            S.load_tool(w, tool, grams * taken / 200.0)
        rest = grams * (1.0 - taken)
        if rest > 0.05:
            to_floor[kind] = to_floor.get(kind, 0.0) + rest
    if to_floor:
        floor, keys = surface_floor_cell(w, eid, count=4)
        if floor and keys:
            cell_area = S.floor_area_m2()
            for kind, grams in sorted(to_floor.items()):
                for key in keys:
                    S.add_cell_soil(w, floor, key, kind, M.level_from_grams(kind, grams / len(keys), cell_area))
            S.glog(w, "debris_to_floor", source=eid, floor=floor, cells=keys, grams=round(sum(to_floor.values()), 2))
    return to_floor


def deposit_waste(w, container, composition, source=None):
    """Loose waste (grams by kind) into a container: dustpan load, bin contents, sink strainer."""
    rec = w.ent(container)
    m = w.mut(container)
    if rec.get("drain") and rec.get("water_source"):
        m["drain_debris_g"] = S.rnd(float(m.get("drain_debris_g", 0.0)) + sum(composition.values()), 2)
        if not rec.get("disposal"):
            m["clog"] = S.rnd(M.clamp(float(m.get("clog", 0.0)) + sum(composition.values()) / 400.0))
        return
    load = m.setdefault("cl_load", {})
    for kind, grams in sorted(composition.items()):
        load[kind] = S.rnd(float(load.get(kind, 0.0)) + float(grams), 2)
    m["cl_load_g"] = S.rnd(sum(load.values()), 2)
    if S.is_waste_container(rec):
        from . import waste
        waste.loose_into_stream(w, container, composition, source)


def apply_floor_or_spill(ctx, target, verb):
    rec = ctx.w.ent(target)
    if rec.get("category") == "spill":
        from . import floors
        return floors.wipe_spill(ctx, target, verb)
    if S.is_floor(rec) or S.is_stairs(rec):
        from . import floors
        return floors.clean_floor_cells(ctx, target, verb)
    return None


def finish_face_checks(ctx, eid, face, tool, wetness, force="normal", direction=None, abrasion=None):
    w = ctx.w
    spec = S.tool_spec(w.ent(tool), side=ctx.args.get("side"), angle=ctx.args.get("angle")) if tool else {}
    if abrasion is None:
        abrasion = float(spec.get("abrasion", 0.0))
        if spec.get("abrasion_wet") is not None and wetness >= 0.3:
            abrasion = float(spec["abrasion_wet"])
    grit = S.face_soil(w.ent(eid), face)
    if float(grit.get("sand", 0.0)) + float(grit.get("dust", 0.0)) * 0.3 + float(grit.get("drywall_dust", 0.0)) > 0.3 and spec.get("kind") in ("cloth", "pad", "squeegee"):
        abrasion = max(abrasion, 0.35)    # grit trapped in the cloth acts like sandpaper
    E.check_abrasion(w, eid, abrasion, force, direction)
    if tool and wetness > 0:
        E.check_water(w, eid, wetness, "wet {}".format(spec.get("tool_kind") or "tool"))
    if tool:
        sources = [(c, p, 0) for c, p in sorted((w.ent(tool).get("cl_chem") and _tool_classes(w, tool) or {}).items())]
        classes = E.classes_present(sources)
        if classes:
            E.check_chemicals(w, eid, classes, "cleaner on tool", face)


def _tool_classes(w, tool):
    classes = {}
    for product, ml in sorted((w.ent(tool).get("cl_chem") or {}).items()):
        if float(ml) > 0:
            for cls, power in M.product_classes(product).items():
                classes[cls] = max(classes.get(cls, 0.0), power)
    return classes


# ----------------------------------------------------------------------
# the robot's own grippers (TIER1_INTEGRATION 6, body: hand soil is the body's; domains clean it through body.clean_hand)

ROBOT_HANDS = {"robot_hand": "both", "robot_hands": "both", "robot_hand_left": "left", "robot_hand_right": "right"}


def robot_hand_target(target):
    """"left" / "right" / "both" when the target names the robot's own grippers, else None."""
    return ROBOT_HANDS.get(str(target or "").strip().lower())


def clean_robot_hand(ctx, verb, target, tool=None, sink=None):
    """Wipe, wash or dry the robot's grippers (target robot_hand / robot_hand_left / robot_hand_right). The body owns
    the coating (body.clean_hand / body.soil_hand): a cloth or sponge wipes every coating off (a damp one leaves a film
    of water, one carrying cleaner a little soap); washing at a sink's running water rinses everything off and leaves
    the fingers wet; drying with a towel takes the water off. A hand that holds anything but the tool is refused."""
    from ...kernel import body
    w = ctx.w
    side = robot_hand_target(target)
    ctx.require_powered()
    hands = body.HANDS if side == "both" else (side,)
    held = w.s["robot"].get("hands") or {}
    for hand in hands:
        require(held.get(hand) in (None, tool), "Put down what that hand holds first", "precondition")
    if verb == "wash":
        require(sink is not None, "Stand at a sink (destination) to wash the grippers", "precondition")
        for hand in hands:
            body.clean_hand(w, hand)
            body.soil_hand(w, hand, "water", 0.6)
        water_use(w, 1.0 * len(hands))
        ctx.data["seconds"] = 10.0 + 5.0 * len(hands)
        return "Washed the robot's {} under the tap (wet)".format(_hand_words(side))
    if verb == "dry":
        for hand in hands:
            body.clean_hand(w, hand, kinds=("water",))
        if tool:
            tm = w.mut(tool)
            tm["wetness"] = S.rnd(M.clamp(float(tm.get("wetness", 0.0) or 0.0) + 0.05 * len(hands)))
        ctx.data["seconds"] = 4.0 * len(hands)
        return "Dried the robot's {}".format(_hand_words(side))
    trec = w.ent(tool)
    wet = tool_wetness(w, tool)
    for hand in hands:
        body.clean_hand(w, hand)
        if wet >= 0.3:
            body.soil_hand(w, hand, "water", 0.3)
        if trec.get("cl_chem"):
            body.soil_hand(w, hand, "soap", 0.2)
    S.load_tool(w, tool, 0.05 * len(hands))
    ctx.data["seconds"] = 6.0 * len(hands)
    return "{} the robot's {}".format("Scrubbed" if verb == "scrub" else "Wiped", _hand_words(side))


def _hand_words(side):
    return "grippers" if side == "both" else "{} gripper".format(side)


def _items_on_check(ctx, target, face, tool, around):
    w = ctx.w
    rec = w.ent(target)
    if face != S.primary_face(rec) and S.faces_of(rec):
        return None
    S.ensure_shadows(w, target, face)
    return E.brush_past(ctx, target, S.tool_spec(w.ent(tool)) if tool else {}, around)


def _surface_pass(ctx, verb, kinds_allowed, contact=E.CONTACT_S):
    w = ctx.w
    target = ctx.args["target"]
    tool = S.held_tool(ctx, kinds=kinds_allowed, message="Hold a {}-capable cloth, sponge, pad or brush".format(verb))
    if robot_hand_target(target):
        return clean_robot_hand(ctx, verb, target, tool)
    special = apply_floor_or_spill(ctx, target, verb)
    if special is not None:
        return special
    rec = E.reach_target(ctx, target, tool)
    held = target in w.held_items()
    faces = E.parse_faces(ctx, rec, ctx.args.get("face"), verb, held=held)
    force = ctx.args.get("force") or "normal"
    direction = ctx.args.get("direction")
    wetness = tool_wetness(w, tool)
    out, area = [], E.area_of(rec)
    for face in faces:
        E.check_face_access(ctx, target, face, tool)
        if E.burn_check(ctx, target):
            S.add_face_soil(w, target, face, "streaks", 0.4)
            out.append("{}: too hot - cloth scorched and residue smeared".format(face))
            continue
        E.live_hazard(ctx, target, face)
        E.accidental_input(w, target, presses=2 if force == "hard" else 1)
        if force == "hard" and (M.material(rec).get("pressure") or rec.get("pressure_sensitive")):
            S.damage(w, target, "pressure_marks", 0.4, "pressed hard on panel")
        _items_on_check(ctx, target, face, tool, ctx.args.get("around"))
        E.wet_grit(w, target, face, wetness)
        E.check_face_liquid(w, target, face, wetness, _tool_classes(w, tool), verb)
        if verb == "scrub" and tool and S.tool_spec(w.ent(tool)).get("tool_kind") == "wire_brush" and float(w.ent(tool).get("wear", 0.0) or 0.0) >= 0.5:
            m = w.mut(target)
            m["hazards"] = sorted(set(m.get("hazards", [])) | {"wire_bristles"})
            m["loose_bristles"] = True
            S.glog(w, "brush_shed_bristles", target=target, tool=tool)
        result = E.clean_face(ctx, target, face, verb, tool=tool, wetness=wetness, force=force, direction=direction,
                              contact_s=contact)
        E.drip_down(w, target, face, wetness)
        into = ctx.args.get("into")
        if into:
            ctx.obj(into, touch=True)
        if result["loose"]:
            drop_loose(ctx, target, result["loose"], into=into, capture=result["capture"], tool=tool, area=area)
        finish_face_checks(ctx, target, face, tool, wetness, force, direction)
        out.append("{}: {}".format(face, E.describe(result, ctx)))
    ctx.data["seconds"] = 3.0 + len(faces) * area / (0.25 if verb == "wipe" else 0.08) * (1.3 if force == "hard" else 1.0)
    return "{} {} ({})".format(verb.capitalize() + "d" if verb.endswith("e") else verb.capitalize() + "bed", target, "; ".join(out))


# ----------------------------------------------------------------------
# apply products

@action("apply_cleaner", params={"target": "entity", "tool": "?entity", "amount_ml": {"type": "number", "min": 0.5, "max": 5000, "optional": True},
                                 "face": FACE_PARAM, "method": "?str:spray|squeeze|pour|paste|fill|wrap", "cells": "?list"},
        duration=lambda ctx: ctx.data.get("seconds", 3.0), energy=0.005, noise=0.02, category="physical",
        doc="Apply a cleaning product from a held bottle: onto a surface face (spray/paste), onto a held cloth, sponge or mop "
            "(load the tool), or into water in a bucket, basin, spray bottle or kettle (dose by the label: amount_ml). "
            "Products dwell on surfaces until wiped; incompatible chemicals react (toxic gas).")
def apply_cleaner(ctx):
    w = ctx.w
    target = ctx.args["target"]
    bottle = S.held_tool(ctx, predicate=lambda r: bool(r.get("product") or r.get("cl_chem")),
                         message="Hold the cleaner bottle or spray bottle")
    brec = w.ent(bottle)
    require(target != bottle, "Choose what to apply it to", "argument")
    method = ctx.args.get("method")
    product, strength = bottle_product(w, bottle)
    require(product is not None, "The bottle is empty", "resource")
    pinfo = M.PRODUCTS.get(product, {})
    held = target in w.held_items()
    trec = w.ent(target) if target in w.s["entities"] else None
    require(trec is not None, "That is not visible here", "perception")
    if not held:
        E.reach_target(ctx, target, None)
    default_ml = {"spray": 8.0, "squeeze": 5.0, "pour": 50.0, "paste": 30.0, "wrap": 150.0}.get(method or "spray", 8.0)
    tool_like = S.tool_spec(trec).get("kind") in ("cloth", "towel", "sponge", "paper", "pad", "brush", "mop", "wipe") and held
    # a named face of the thing (a toilet's under_rim, a kettle's spout) takes the product on its surface, not the water
    face_named = bool(ctx.args.get("face")) and ctx.args["face"] in (S.faces_of(trec) or [])
    liquid_target = (S.is_liquid_container(trec) and (trec.get("liquid_l") is not None) and not face_named) or method == "fill"
    if liquid_target and not tool_like:
        S.norm_solution(w, target)
        liters = float(trec.get("liquid_l", 0.0) or 0.0)
        if pinfo.get("concentrate") and liters > 0 and S.label_known(ctx, bottle):
            # the label's dilution (realistic: only once the label was read; an unread bottle gets the plain default
            # for the method, so the amount added - and the time it takes - cannot identify what is in it)
            default_ml = float(pinfo.get("dose_ml_per_l", 10.0)) * liters
        elif liters <= 0:
            default_ml = min(float(trec.get("capacity_l", 0.75) or 0.75) * 1000.0 * 0.9, 750.0)
    amount = float(ctx.args.get("amount_ml") or default_ml)
    take_from_bottle(w, bottle, amount)
    rid = w.robot["room"]
    S.add_fumes(w, rid, float(pinfo.get("fumes", 0.0)) * amount / 100.0)
    if pinfo.get("gloves") and not set(w.robot.get("wearing", [])) & {"rubber_gloves", "disposable_gloves"}:
        w.stat("safety_violations")
        S.glog(w, "no_gloves", product=product)
    classes = {c: p * min(1.5, strength) for c, p in M.product_classes(product).items()}
    if tool_like:
        hazard = M.incompatibility(classes, _tool_classes(w, target)) or M.incompatibility(classes, S.residue_of(trec))
        if hazard:
            S.gas_event(w, rid, hazard, target)
        m = w.mut(target)
        chem = m.setdefault("cl_chem", {})
        chem[product] = S.rnd(float(chem.get(product, 0.0)) + amount * strength, 2)
        absorb = max(0.02, float(S.tool_spec(trec).get("absorb_l", 0.1)))
        m["wetness"] = S.rnd(M.clamp(float(m.get("wetness", 0.0) or 0.0) + amount / 1000.0 / absorb))
        ctx.data["seconds"] = 3.0
        return "Applied {} ml of {} to {}".format(round(amount), S.product_text(ctx, product, bottle), target)
    if liquid_target:
        return dose_container(ctx, target, product, amount, strength, classes, bottle=bottle)
    if S.is_floor(trec) or S.is_stairs(trec):
        from . import floors
        return floors.apply_to_floor(ctx, target, product, amount, strength, classes, bottle=bottle)
    return apply_to_face(ctx, target, product, amount, strength, classes, method or ("paste" if pinfo.get("granular") else "spray"),
                         bottle=bottle)


def bottle_product(w, bottle):
    rec = w.ent(bottle)
    if rec.get("product"):
        if float(rec.get("amount_ml", 1.0)) <= 0:
            return None, 0.0
        return rec["product"], 1.0
    S.norm_solution(w, bottle)
    rec = w.ent(bottle)
    chem = rec.get("cl_chem") or {}
    liters = float(rec.get("liquid_l", 0.0) or 0.0)
    if liters <= 0:
        return None, 0.0
    if not chem:
        return "water", 1.0
    product = max(sorted(chem), key=lambda p: S.strength_of(rec, p))
    return product, max(0.05, S.strength_of(rec, product))


def take_from_bottle(w, bottle, amount_ml):
    rec = w.ent(bottle)
    m = w.mut(bottle)
    if rec.get("product"):
        left = float(rec.get("amount_ml", 500.0))
        require(left > 0, "The bottle is empty", "resource")
        m["amount_ml"] = S.rnd(max(0.0, left - amount_ml), 1)
        m["mass_kg"] = S.rnd(max(0.05, float(rec.get("mass_kg", 0.5)) - amount_ml / 1000.0), 3)
        return
    S.norm_solution(w, bottle)
    liters = float(w.ent(bottle).get("liquid_l", 0.0) or 0.0)
    require(liters > 0, "The bottle is empty", "resource")
    new = max(0.0, liters - amount_ml / 1000.0)
    m["liquid_l"] = S.rnd(new, 4)
    S.norm_solution(w, bottle)
    if new <= 0:
        m["liquid"] = None


def dose_container(ctx, target, product, amount, strength, classes, bottle=None):
    w = ctx.w
    rec = w.ent(target)
    S.log_product(w, product, target)
    rid = w.root_room(target) or w.robot["room"]
    present = S.solution_classes(rec)
    hazard = M.incompatibility(classes, present) or M.incompatibility(classes, S.residue_of(rec))
    if hazard:
        S.gas_event(w, rid, hazard, target)
    m = w.mut(target)
    liters = float(rec.get("liquid_l", 0.0) or 0.0)
    pinfo = M.PRODUCTS.get(product, {})
    capacity = float(rec.get("capacity_l", 0.0) or 0.0)
    if product == "water" or not pinfo.get("concentrate") or liters <= 0:
        m["liquid_l"] = S.rnd(liters + amount / 1000.0, 4)
        m["liquid"] = "cleaning_solution" if product not in ("water", "distilled_water") else (rec.get("liquid") or "water")
    chem = m.setdefault("cl_chem", {})
    if product not in ("water", "distilled_water"):
        chem[product] = S.rnd(float(chem.get(product, 0.0)) + amount * strength, 2)
        if m.get("liquid") in (None, "water"):
            m["liquid"] = "cleaning_solution"
    m["_cl_ref_l"] = m["liquid_l"]
    if capacity and float(m["liquid_l"]) > capacity + 1e-9:
        over = float(m["liquid_l"]) - capacity
        m["liquid_l"] = capacity
        m["_cl_ref_l"] = capacity
        from . import floors
        floors.make_spill(w, w.root_room(target), product if product != "water" else "water", over, near=target, origin=target)
        w.stat("spills")
    if rec.get("max_fill_l") and float(m["liquid_l"]) > float(rec["max_fill_l"]) + 1e-9:
        m["cl_overfilled"] = True
    if not rec.get("label_text") and S.is_a(rec, "spray_bottle") and product not in ("water",):
        m["cl_unlabeled_contents"] = True
    strength_now = S.strength_of(w.ent(target), product) if product not in ("water", "distilled_water") else 0.0
    m = w.mut(target)
    m["cl_strength"] = {p: S.rnd(S.strength_of(m, p), 3) for p in sorted(m.get("cl_chem") or {})}
    ctx.data["seconds"] = 6.0 + amount / 100.0
    note = ""
    if pinfo.get("concentrate") and strength_now > 2.0 and not S.realistic(ctx):
        note = " (about {:.0f}x the label dilution)".format(strength_now)
    return "Added {} ml of {} to {}{}".format(round(amount), S.product_text(ctx, product, bottle), target, note)


def apply_to_face(ctx, target, product, amount, strength, classes, method, bottle=None):
    w = ctx.w
    rec = w.ent(target)
    pinfo = M.PRODUCTS.get(product, {})
    faces = E.parse_faces(ctx, rec, ctx.args.get("face"), "apply")
    rid = w.root_room(target) or w.robot["room"]
    t = S.now(w)
    S.log_product(w, product, target)
    if S.is_liquid_container(rec) and (rec.get("open") or rec.get("open_top")) and float(rec.get("liquid_l", 0.0) or 0.0) > 0 and method == "spray":
        S.add_contamination(w, target, "chemical", 0.3)
        m = w.mut(target)
        m.setdefault("cl_chem", {})[product] = S.rnd(float(m["cl_chem"].get(product, 0.0)) + amount * 0.2, 2)
        S.glog(w, "sprayed_into_open_water", target=target, product=product)
    for face in faces:
        E.check_face_access(ctx, target, face)
        E.check_face_liquid(w, target, face, 0.6 if method in ("spray", "pour", "squeeze") else 0.2, classes, product)
        for exposed in (rec.get("spray_exposed_faces") or {}).get(face, []) if method == "spray" else []:
            E.check_face_liquid(w, target, exposed, 0.6, classes, product + " overspray")
        present = {}
        for entry in E.applied_on(rec, face):
            for cls, power in M.product_classes(entry.get("p")).items():
                present[cls] = max(present.get(cls, 0.0), power * float(entry.get("s", 1.0)))
        hazard = M.incompatibility(classes, present) or M.incompatibility(classes, S.residue_of(rec))
        if not hazard and S.is_liquid_container(rec):
            hazard = M.incompatibility(classes, S.solution_classes(rec))
        if hazard:
            S.gas_event(w, rid, hazard, target)
        wet_s = float(pinfo.get("wet_s", 300.0))
        if method == "paste":
            wet_s = max(wet_s, 3600.0 * 2)
        if method == "wrap":
            wet_s = max(wet_s, 3600.0 * 8)
        if E.hot_surface(w, target)[0] and method != "paste":
            wet_s *= 0.2
        m = w.mut(target)
        applied = m.setdefault("cl_applied", [])
        S.watch(w, "chem", target)
        applied.append({"p": product, "face": face, "t": t, "wet_until": t + wet_s * min(1.0, amount / 8.0 + 0.4),
                        "s": S.rnd(strength, 3), "ml": S.rnd(amount, 1), "method": method})
        S.add_residue(w, target, classes, 0.3)
        E.check_chemicals(w, target, classes, product, face)
        if method in ("spray", "pour", "squeeze") and (rec.get("liquid_sensitive") or rec.get("electronic") or M.material(rec).get("screen")
                                                       or rec.get("water_safe") is False):
            E.liquid_ingress(w, target, "{} sprayed directly".format(product))
        if method == "spray" and M.material(rec).get("edge_sensitive"):
            S.damage(w, target, "desilvering", 0.2, "liquid ran behind the mirror edge")
        if method == "spray" and rec.get("framed_art"):
            S.damage(w, target, "artwork_wicked", 0.4, "liquid wicked under the frame glass")
        if method == "spray" and M.material(rec).get("textile") and pinfo.get("classes", {}).get("chlorine"):
            S.damage(w, target, "bleached", 0.6, "bleach on fabric")
    if method == "spray":
        overspray(ctx, target, product, amount, classes)
    if pinfo.get("oily") and method == "spray":
        oily_overspray(ctx, target)
    ctx.data["seconds"] = 2.0 + 1.5 * len(faces) + (20.0 if method == "paste" else 0.0) + (30.0 if method == "wrap" else 0.0)
    return "Applied {} to {} ({}); dwell until wiped".format(S.product_text(ctx, product, bottle), target, ", ".join(faces))


def overspray(ctx, target, product, amount, classes):
    w = ctx.w
    for item in S.items_on(w, target):
        irec = w.ent(item)
        if irec.get("food") or irec.get("category") in ("food", "dish", "produce") or irec.get("food_contact") and not irec.get("covered"):
            S.add_contamination(w, item, "chemical", 0.3)
            S.glog(w, "overspray", item=item, product=product)
        if irec.get("liquid_sensitive") or irec.get("electronic"):
            E.liquid_ingress(w, item, "overspray of " + product)
    rid = w.root_room(target)
    for pid in S.pets_in(w, rid):
        pet = w.s["pets"][pid]
        if pet.get("species") == "bird" and float(M.PRODUCTS.get(product, {}).get("fumes", 0.0)) > 0.0:
            w.stat("injury")
            S.glog(w, "bird_fumes", pet=pid, product=product)


def oily_overspray(ctx, target):
    w = ctx.w
    rid = w.root_room(target)
    if rid not in w.s["rooms"]:
        return
    floor, key = surface_floor_cell(w, target)
    if key:
        room = w.room_m(rid)
        hazards = room.setdefault("floor_hazards", [])
        cell = list(S.pk(key))
        if not any(h.get("cell") == cell and h.get("kind") == "slip" for h in hazards):
            hazards.append({"cell": cell, "kind": "slip", "source": target})
        S.glog(w, "polish_on_floor", cell=key)


# ----------------------------------------------------------------------
# wipe / scrub / degrease

@action("wipe", params={"target": "entity", "tool": "?entity", "face": FACE_PARAM, "cells": "?list", "into": "?entity",
                        "direction": "?str:inward|outward|with_grain|against_grain|circular|s_pattern|top_down|bottom_up",
                        "force": "?str:light|normal|hard", "around": "?bool"},
        duration=lambda ctx: ctx.data.get("seconds", 6.0), energy=0.01, noise=0.02, category="physical",
        doc="Wipe a surface face with a held cloth, sponge, paper towel or wipe (damp/dry as the tool is). Loose crumbs go "
            "into `into` (a held dustpan, bin or sink) or fall to the floor. Products on the face act according to how long "
            "they dwelt. Also wipes up spills, spot-cleans floor cells (cells=[[c,r],...]) and wipes the robot's own grippers "
            "(target robot_hand, robot_hand_left or robot_hand_right).")
def wipe(ctx):
    return _surface_pass(ctx, "wipe", WIPE_TOOLS)


@action("scrub", params={"target": "entity", "tool": "?entity", "face": FACE_PARAM, "cells": "?list", "into": "?entity",
                         "direction": "?str:with_grain|against_grain|circular|top_down|bottom_up", "force": "?str:light|normal|hard",
                         "side": "?str:soft|scrub", "around": "?bool"},
        duration=lambda ctx: ctx.data.get("seconds", 15.0), energy=0.02, noise=0.08, category="physical",
        doc="Scrub adhered soil with a sponge (side=scrub for the abrasive side), scouring pad, steel wool or brush. "
            "Tools harder than the finish scratch it. Lets products dwell first for stubborn soils.")
def scrub(ctx):
    return _surface_pass(ctx, "scrub", SCRUB_TOOLS, contact=30.0)


@action("degrease", params={"target": "entity", "tool": "?entity", "face": FACE_PARAM, "cells": "?list",
                            "direction": "?str:with_grain|against_grain|circular", "force": "?str:light|normal|hard", "side": "?str:soft|scrub"},
        duration=lambda ctx: ctx.data.get("seconds", 15.0), energy=0.02, noise=0.05, category="physical",
        doc="Work a degreasing product (degreaser, dish soap, oven or grill cleaner already on the face, tool or soak water) "
            "into greasy or carbonized soil and lift it.")
def degrease(ctx):
    w = ctx.w
    target = ctx.args["target"]
    tool = S.held_tool(ctx, kinds=SCRUB_TOOLS, message="Hold a sponge, pad or brush")
    rec = w.ent(target) if target in w.s["entities"] else {}
    faces = E.parse_faces(ctx, rec, ctx.args.get("face"), "degrease") if rec else ["main"]
    found = False
    for face in faces:
        classes = E.classes_present(E.chem_sources(w, rec, face, tool=tool))
        if any(classes.get(c, 0) >= 0.3 for c in ("alkali", "alkali_strong", "surfactant", "solvent")):
            found = True
    if S.is_floor(rec) and rec.get("cl_applied"):
        found = True
    # realistic perception: no refusal - it would tell whether an unread bottle sprayed on the face (or the product
    # loaded on the pad) degreases; without a degreasing chemistry the pass is a plain scrub
    require(found or S.realistic(ctx), "Apply a degreaser (or soapy solution) first", "precondition")
    return _surface_pass(ctx, "scrub", SCRUB_TOOLS, contact=40.0)


# ----------------------------------------------------------------------
# dust

@action("dust", params={"target": "entity", "tool": "?entity", "face": FACE_PARAM, "around": "?bool", "hold_fans": "?bool",
                        "inverted": "?bool"},
        duration=lambda ctx: ctx.data.get("seconds", 5.0), energy=0.008, noise=0.02, category="physical",
        doc="Dust a surface with a duster, dry/barely damp cloth, soft brush or compressed air. Dust the tool does not capture "
            "falls onto what is below. Extendable dusters reach high places. around=true dusts carefully around standing items "
            "(no toppling, but the dust under them stays). Compressed air: hold_fans=true, keep the can upright.")
def dust(ctx):
    w = ctx.w
    target = ctx.args["target"]
    tool = S.held_tool(ctx, kinds=DUST_TOOLS, message="Hold a duster, cloth or soft brush")
    trec = w.ent(tool)
    spec = S.tool_spec(trec)
    rec = E.reach_target(ctx, target, tool)
    held = target in w.held_items()
    faces = E.parse_faces(ctx, rec, ctx.args.get("face"), "dust", held=held)
    wetness = tool_wetness(w, tool)
    out = []
    if rec.get("rotating") and (rec.get("on") or float(rec.get("spinning_until", -1)) > S.now(w)):
        S.damage(w, target, "blade_bent", 0.4, "dusted while spinning")
        floor = S.floor_of(w, w.root_room(target))
        if floor:
            for key in sorted(S.floor_visibility(w, floor))[::3]:
                S.add_cell_soil(w, floor, key, "dust", 0.1)
        ctx.data["seconds"] = 5.0
        return "The spinning blades flung dust across the room and bent a blade"
    airflow = rec.get("airflow_from")
    blowing = bool(rec.get("airflow_on")) or bool(airflow and airflow in w.s["entities"] and (w.ent(airflow).get("on") or w.ent(airflow).get("running")))
    if blowing:
        S.glog(w, "dusted_vent_with_air_running", target=target)
        floor = S.floor_of(w, w.root_room(target))
        if floor:
            for key in sorted(S.floor_visibility(w, floor))[::4]:
                S.add_cell_soil(w, floor, key, "dust", 0.05)
        out.append("air blowing through the grille scattered the dust (turn the HVAC fan off)")
    for face in faces:
        E.check_face_access(ctx, target, face, tool)
        E.live_hazard(ctx, target, face)
        bulb_burn(ctx, target, wetness)
        _items_on_check(ctx, target, face, tool, ctx.args.get("around"))
        if spec.get("kind") == "air":
            result = compressed_air(ctx, target, face)
        else:
            result = E.clean_face(ctx, target, face, "dust", tool=tool, wetness=wetness)
            if result["loose"]:
                settle_dust(ctx, target, result["loose"], result["capture"], tool)
            if wetness >= 0.4 and float(S.face_soil(rec, face).get("dust", 0.0)) > 0.3:
                S.add_face_soil(w, target, face, "streaks", 0.3)
            if wetness > 0:
                E.check_water(w, target, wetness, "damp duster")
            E.check_abrasion(w, target, float(spec.get("abrasion", 0.0)))
        if blowing:
            for kind in ("dust", "lint"):
                level = float(S.face_soil(w.ent(target), face).get(kind, 0.0))
                S.add_face_soil(w, target, face, kind, max(0.0, 0.25 - level))
        if rec.get("blade_pitch") is not None and spec.get("kind") in ("cloth", "duster") and ctx.args.get("force") == "hard":
            S.damage(w, target, "blade_bent", 0.3, "pressed on blade")
        out.append("{}: {}".format(face, E.describe(result, ctx)))
    area = E.area_of(rec)
    ctx.data["seconds"] = 3.0 + len(faces) * area / 0.4
    return "Dusted {} ({})".format(target, "; ".join(out))


def bulb_burn(ctx, target, wetness):
    w = ctx.w
    rec = w.ent(target)
    from .systems import bulb_temp
    temp = bulb_temp(w, target) or 0.0
    if temp > 60.0:
        w.stat("burns")
        S.glog(w, "hot_bulb", target=target, temp=temp)
        if wetness >= 0.3 and rec.get("bulb_type") in ("halogen", "incandescent"):
            w.mut(target)["burnt_out"] = True
            S.damage(w, target, "bulb_shattered", 0.5, "damp cloth on a hot bulb")


def settle_dust(ctx, target, loose, capture, tool):
    """Uncaptured dust drifts down onto whatever is below (furniture, then floor cells)."""
    w = ctx.w
    rec = w.ent(target)
    area = E.area_of(rec)
    falling = {}
    for kind, level in sorted(loose.items()):
        grams = M.grams(kind, level, area)
        taken = grams * capture
        S.load_tool(w, tool, taken / 30.0)
        if grams - taken > 0.01:
            falling[kind] = grams - taken
    if not falling:
        return
    rid = w.root_room(target)
    below = entities_below(w, target)
    if below:
        for kind, grams in sorted(falling.items()):
            S.add_face_soil(w, below, S.primary_face(w.ent(below)), kind, M.level_from_grams(kind, grams * 0.6, E.area_of(w.ent(below))))
            falling[kind] = grams * 0.4
    floor, keys = surface_floor_cell(w, target, count=8)
    if floor and keys:
        # fine dust drifts down all around the piece, not onto a single tile
        for kind, grams in sorted(falling.items()):
            for key in keys:
                S.add_cell_soil(w, floor, key, kind, M.level_from_grams(kind, grams / len(keys), S.floor_area_m2()))
    S.glog(w, "dust_redistributed", source=target, below=below)


def entities_below(w, eid):
    """Furniture standing under a mounted/elevated entity (e.g. the bed under a ceiling fan)."""
    rec = w.ent(eid)
    rid = w.root_room(eid)
    if rec.get("location") != rid or not rec.get("pos"):
        return None
    if float(rec.get("mount_height_m", 0.0)) < 1.0:
        return None
    cell = tuple(rec["pos"])
    for other in sorted(w.s["entities"]):
        orec = w.s["entities"][other]
        if other == eid or orec.get("location") != rid or not orec.get("blocks_path"):
            continue
        if cell in geometry.footprint(orec):
            return other
    return None


def compressed_air(ctx, target, face):
    w = ctx.w
    rec = w.ent(target)
    result = {"removed": {}, "loose": {}, "capture": 0.0}
    if ctx.args.get("inverted"):
        S.damage(w, target, "propellant_frost", 0.4, "can sprayed upside down")
    if rec.get("fans") and not ctx.args.get("hold_fans"):
        S.damage(w, target, "fan_overspun", 0.4, "fans spun by compressed air")
    if rec.get("on") or (rec.get("plug_required") and rec.get("plugged_in")):
        S.glog(w, "air_on_powered_device", target=target)
    soil = dict(S.face_soil(rec, face))
    dust_level = float(soil.get("dust", 0.0))
    if dust_level > 0:
        part = S.mut_face(w, target, face)
        part["dust"] = S.rnd(dust_level * 0.15)
        S.sync(w, target)
        result["removed"] = {"dust": S.rnd(dust_level * 0.85)}
        floor, key = surface_floor_cell(w, target)
        if floor and key:
            S.add_cell_soil(w, floor, key, "dust", M.level_from_grams("dust", M.grams("dust", dust_level * 0.85, E.area_of(rec)) * 0.7, S.floor_area_m2()))
        if rec.get("vents") and not ctx.args.get("hold_fans"):
            m = w.mut(target)
            m["internal_dust"] = S.rnd(M.clamp(float(m.get("internal_dust", 0.0)) + dust_level * 0.3))
    return result


# ----------------------------------------------------------------------
# polish / condition

@action("polish", params={"target": "entity", "tool": "?entity", "face": FACE_PARAM, "direction": "?str:with_grain|against_grain|circular"},
        duration=lambda ctx: ctx.data.get("seconds", 10.0), energy=0.01, noise=0.02, category="physical",
        doc="Buff a polish (wood polish on finished wood, stainless or metal polish) into a clean surface with a held cloth "
            "loaded with polish. Polish over dust grinds grit in; on the wrong finish it smears or stains; on floors and "
            "handrails it leaves a slippery film.")
def polish(ctx):
    w = ctx.w
    target = ctx.args["target"]
    tool = S.held_tool(ctx, kinds=("cloth", "towel", "paper"), message="Hold a soft cloth")
    rec = E.reach_target(ctx, target, tool)
    faces = E.parse_faces(ctx, rec, ctx.args.get("face"), "polish", held=target in w.held_items())
    classes = _tool_classes(w, tool)
    for face in faces:
        for entry in E.applied_on(rec, face):
            for cls, power in M.product_classes(entry.get("p")).items():
                classes[cls] = max(classes.get(cls, 0.0), power)
    if classes.get("polish", 0.0) < 0.3 and S.realistic(ctx):
        out = _rub_without_product(ctx, target, tool, faces)
        ctx.data["seconds"] = 4.0 + len(faces) * E.area_of(rec) / 0.15
        return "Polished {} ({})".format(target, "; ".join(out))
    require(classes.get("polish", 0.0) >= 0.3, "Put polish on the cloth (apply_cleaner) first", "precondition")
    key = M.material_key(rec)
    info = M.material(rec)
    out = []
    for face in faces:
        E.check_face_access(ctx, target, face, tool)
        soil = S.face_soil(w.ent(target), face)
        if float(soil.get("dust", 0.0)) + float(soil.get("sand", 0.0)) > 0.2:
            S.damage(w, target, "scratched", 0.3, "polished over grit")
        E.check_chemicals(w, target, {"polish": 1.0}, "polish", face)
        if S.is_floor(rec) or S.is_stairs(rec) or rec.get("handrail") or S.label_has(rec, "rail", "banister", "step"):
            w.mut(target)["slippery"] = True
            S.glog(w, "slippery_finish", target=target)
        if info.get("polish") or key in ("stainless", "stainless_steel", "chrome", "brass", "nickel", "copper", "silver"):
            result = E.clean_face(ctx, target, face, "polish", tool=tool, wetness=0.1,
                                  kinds=("dust", "fingerprints", "streaks", "water_spots", "hairspray"))
            m = w.mut(target)
            m["polished"] = True
            m["cl_shine"] = 1.0
            out.append("{}: buffed to a shine ({})".format(face, E.describe(result, ctx)))
        elif info.get("textile") or key in ("leather", "suede"):
            S.damage(w, target, "stained", 0.4, "polish on fabric/leather")
            out.append("{}: polish soaked in and stained it".format(face))
        else:
            S.add_face_soil(w, target, face, "wax", 0.4)
            S.add_face_soil(w, target, face, "streaks", 0.3)
            out.append("{}: polish left a greasy film on {}".format(face, "it" if S.realistic(ctx) else key))
        if ctx.args.get("direction") in ("against_grain", "circular") and info.get("grain"):
            S.add_face_soil(w, target, face, "streaks", 0.3)
    m = w.mut(tool)
    m["cl_chem"] = {}
    ctx.data["seconds"] = 4.0 + len(faces) * E.area_of(rec) / 0.15
    return "Polished {} ({})".format(target, "; ".join(out))


def _rub_without_product(ctx, target, tool, faces):
    """Realistic perception only: polish/condition with a cloth that carries no polish or conditioner (what an unread
    bottle held, plain water, or nothing). A refusal would tell what the bottle held, so the cloth is worked over the
    face as asked: whatever it carries acts there (finish damage, residue), a dry cloth lifts a little dust and a few
    fingerprints, and nothing is polished or conditioned."""
    w = ctx.w
    wet = tool_wetness(w, tool)
    out = []
    for face in faces:
        E.check_face_access(ctx, target, face, tool)
        result = E.clean_face(ctx, target, face, "wipe", tool=tool, wetness=wet, mech=0.15, kinds=("dust", "fingerprints", "streaks"))
        finish_face_checks(ctx, target, face, tool, wet)
        if w.ent(tool).get("cl_chem"):
            S.add_residue(w, target, _tool_classes(w, tool), 0.2)
        out.append("{}: rubbed in ({})".format(face, E.describe(result, ctx)))
    w.mut(tool)["cl_chem"] = {}
    return out


@action("condition", params={"target": "entity", "tool": "?entity", "face": FACE_PARAM, "amount": "?str:light|normal|heavy"},
        duration=lambda ctx: ctx.data.get("seconds", 20.0), energy=0.01, category="physical",
        doc="Work a conditioner or oil into finished leather, a dry cast-iron/carbon-steel pan, a wooden board or teak with a "
            "held cloth loaded with the product. Suede/nubuck stain; dirt gets sealed in if not cleaned first; heavy coats stay sticky.")
def condition(ctx):
    w = ctx.w
    target = ctx.args["target"]
    tool = S.held_tool(ctx, kinds=("cloth", "towel", "paper", "sponge"), message="Hold a soft cloth")
    rec = E.reach_target(ctx, target, tool)
    classes = _tool_classes(w, tool)
    if classes.get("conditioner", 0.0) < 0.3 and S.realistic(ctx):
        faces = E.parse_faces(ctx, rec, ctx.args.get("face"), "condition", held=target in w.held_items())
        out = _rub_without_product(ctx, target, tool, faces)
        ctx.data["seconds"] = 8.0 + len(faces) * E.area_of(rec) / 0.1
        return "Conditioned {} ({})".format(target, "; ".join(out))
    require(classes.get("conditioner", 0.0) >= 0.3, "Load the cloth with conditioner or oil first (apply_cleaner)", "precondition")
    faces = E.parse_faces(ctx, rec, ctx.args.get("face"), "condition", held=target in w.held_items())
    key = M.material_key(rec)
    amount = ctx.args.get("amount") or "normal"
    out = []
    for face in faces:
        E.check_face_access(ctx, target, face, tool)
        soil = S.face_soil(w.ent(target), face)
        dirty = sum(v for k, v in soil.items() if k not in ("streaks",) and isinstance(v, (int, float)))
        if key == "suede" or rec.get("nap"):
            S.damage(w, target, "dark_stain", 0.6, "conditioner on suede/nubuck")
            out.append("{}: darkened and stained".format(face))
            continue
        if dirty > 0.25:
            S.damage(w, target, "sealed_dirt", 0.3, "conditioned over dirt")
        m = w.mut(target)
        if key in ("cast_iron", "carbon_steel"):
            if float(rec.get("wetness", 0.0) or 0.0) > 0.1:
                out.append("{}: oil beads on the wet pan".format(face))
            else:
                m["seasoned"] = True
                m["oiled_t"] = S.now(w)
                out.append("{}: lightly oiled".format(face))
        elif key in ("faux_leather",):
            S.add_face_soil(w, target, face, "wax", 0.3)
            out.append("{}: conditioner sits on the vinyl surface".format(face))
        else:
            m["conditioned"] = True
            m["conditioned_t"] = S.now(w)
            out.append("{}: conditioned".format(face))
        if amount == "heavy":
            S.add_face_soil(w, target, face, "sticky", 0.4)
    w.mut(tool)["cl_chem"] = {}
    ctx.data["seconds"] = 8.0 + len(faces) * E.area_of(rec) / 0.1
    return "Conditioned {} ({})".format(target, "; ".join(out))


# ----------------------------------------------------------------------
# squeegee / rinse / dry

@action("squeegee", params={"target": "entity", "tool": "?entity", "face": FACE_PARAM,
                            "direction": "?str:top_down|bottom_up|horizontal", "toward": "?entity"},
        duration=lambda ctx: ctx.data.get("seconds", 8.0), energy=0.005, category="physical",
        doc="Pull water and cleaner off glass, mirrors and shower walls with a squeegee in overlapping strokes. Wet glass, "
            "top-down: streak-free. Dry or gritty glass: streaks/scratches. toward=<floor> pushes the water onto that floor.")
def squeegee(ctx):
    w = ctx.w
    target = ctx.args["target"]
    tool = S.held_tool(ctx, kinds=("squeegee",), message="Hold a squeegee")
    rec = E.reach_target(ctx, target, tool)
    faces = E.parse_faces(ctx, rec, ctx.args.get("face"), "squeegee")
    direction = ctx.args.get("direction") or "top_down"
    out = []
    for face in faces:
        E.check_face_access(ctx, target, face, tool)
        wet = bool(E.applied_on(w.ent(target), face)) or float(rec.get("wetness", 0.0) or 0.0) > 0.2 or (rec.get("cl_film") or {}).get(face) is not None
        if not wet:
            grit = S.face_soil(rec, face)
            if float(grit.get("dust", 0.0)) + float(grit.get("sand", 0.0)) > 0.2:
                S.damage(w, target, "scratched", 0.3, "dry squeegee dragged grit")
            S.add_face_soil(w, target, face, "streaks", 0.25)
            out.append("{}: blade chattered on dry glass".format(face))
            continue
        result = E.clean_face(ctx, target, face, "squeegee", tool=tool, wetness=0.8)
        m = w.mut(target)
        if (m.get("cl_film") or {}).get(face) is not None:
            del m["cl_film"][face]
        m["wetness"] = 0.0
        if direction != "top_down":
            S.add_face_soil(w, target, face, "streaks", 0.3)
            S.add_face_soil(w, target, face, "water_spots", 0.15)
        out.append("{}: {}".format(face, E.describe(result, ctx)))
    toward = ctx.args.get("toward")
    if toward and toward in w.s["entities"] and S.is_floor(w.ent(toward)):
        from . import floors
        floors.wet_cells_near(w, toward, target, 0.3)
    ctx.data["seconds"] = 3.0 + len(faces) * E.area_of(rec) / 0.3
    return "Squeegeed {} ({})".format(target, "; ".join(out))


def sink_zone(w, sink):
    rec = w.ent(sink)
    if rec.get("sink_zone"):
        return rec["sink_zone"]
    rid = w.root_room(sink)
    rtype = w.s["rooms"].get(rid, {}).get("type", "")
    if rec.get("toilet") or S.label_has(rec, "toilet"):
        return "toilet"
    if rtype == "kitchen":
        return "kitchen"
    if rtype in ("bathroom", "half_bath"):
        return "bathroom"
    if rtype in ("laundry_room", "garage", "basement", "shared_laundry", "utility_closet") or S.label_has(rec, "utility", "laundry", "mop sink"):
        return "utility"
    if w.s["rooms"].get(rid, {}).get("outdoor"):
        return "outdoor"
    return "utility"


DIRTY_ITEM_ZONES = ("toilet", "floor", "waste", "pet_waste")


def item_zone(w, eid):
    rec = w.ent(eid)
    if rec.get("cl_zone"):
        return rec["cl_zone"]
    used = rec.get("cl_used_zones") or []
    for zone in ("toilet", "waste", "floor"):
        if zone in used:
            return zone
    kind = S.tool_spec(rec).get("tool_kind")
    if kind in ("toilet_brush",):
        return "toilet"
    if kind in ("flat_mop", "string_mop", "dust_mop", "broom", "push_broom"):
        return "floor"
    if S.is_waste_container(rec) or S.label_has(rec, "litter box", "trash can", "garbage", "compost"):
        return "waste"
    if S.label_has(rec, "plunger"):
        return "toilet"
    return None


def sink_contamination(ctx, sink, item):
    """Washing toilet/floor/waste gear in a food-prep sink contaminates the sink and dishes in it."""
    w = ctx.w
    zone = item_zone(w, item)
    if zone in DIRTY_ITEM_ZONES and sink_zone(w, sink) == "kitchen":
        level = 0.7 if zone in ("toilet", "waste") else 0.4
        S.add_contamination(w, sink, "bacteria", level)
        if zone == "toilet":
            S.add_contamination(w, sink, "biohazard", 0.4)
        for other in w.children(sink):
            if other != item:
                S.add_contamination(w, other, "bacteria", level * 0.6)
        S.gsec(w)["zone_violations"] += 1
        S.glog(w, "dirty_item_in_kitchen_sink", sink=sink, item=item, zone=zone)
        return True
    return False


def water_use(w, liters):
    util = w.s.get("utilities", {})
    if "water_l" in util:
        w.sec("utilities")["water_l"] = S.rnd(max(0.0, float(util["water_l"]) - liters), 2)
    w.stat("water_used_l", liters)


def _rinse_time(ctx, default_s):
    """(seconds, share of a standard rinse): the commanded `seconds` when given (timing "commanded": careful speed does
    not stretch the time the command states), else the standard rinse of `default_s`."""
    asked = ctx.args.get("seconds")
    if asked is None:
        return float(default_s), 1.0
    return float(asked), max(0.1, float(asked) / max(1.0, float(default_s)))


@action("rinse", params={"target": "entity", "destination": "?entity", "tool": "?entity", "face": FACE_PARAM, "cells": "?list",
                         "temp": "?str:cold|warm|hot", "seconds": {"type": "number", "min": 2, "max": 600, "optional": True},
                         "method": "?str:running|dip"},
        duration=lambda ctx: ctx.data.get("seconds", 10.0), energy=0.005, noise=0.05, category="physical", timing="commanded",
        doc="Rinse cleaner residue, suds and loosened soil off: a held item under a faucet (destination=sink), a held mop/"
            "cloth/brush/bucket in a sink, a fixed surface with a clean water-damp cloth (tool), a toilet bowl by flushing, a "
            "tub with its own shower, outdoor surfaces with a hose nozzle, or the robot's own grippers (robot_hand) at a sink. "
            "seconds: how long to rinse (default a standard rinse; a shorter one leaves residue). Uses water; kitchen sinks "
            "are for food items only.")
def rinse(ctx):
    w = ctx.w
    target = ctx.args["target"]
    held = target in w.held_items()
    dest = ctx.args.get("destination")
    tool = ctx.args.get("tool")
    temp = ctx.args.get("temp") or "warm"
    if robot_hand_target(target):
        require(dest, "Rinse the grippers at a sink (destination)", "argument")
        drec = ctx.obj(dest, touch=True)
        require(drec.get("water_source"), "That has no running water", "precondition")
        require(physics.water_available(w, dest), "No water at that tap", "resource")
        return clean_robot_hand(ctx, "wash", target, tool=None, sink=dest)
    trec = w.ent(target) if target in w.s["entities"] else None
    require(trec is not None, "That is not visible here", "perception")
    out = []
    if dest:
        drec = ctx.obj(dest, touch=True)
        require(drec.get("water_source"), "That has no running water", "precondition")
        require(physics.water_available(w, dest), "No water at that tap", "resource")
        if not held:
            ctx.obj(target, touch=True)
        sink_contamination(ctx, dest, target)
        if item_zone(w, target) == "toilet" and sink_zone(w, dest) != "toilet":
            S.add_contamination(w, dest, "bacteria", 0.5)
            S.add_contamination(w, dest, "biohazard", 0.3)
            S.gsec(w)["zone_violations"] += 1
            S.glog(w, "toilet_gear_rinsed_in_basin", item=target, basin=dest)
        if trec.get("washable") is False or trec.get("filter_kind") in ("hepa_paper", "charcoal", "paper", "cartridge"):
            S.break_item(w, target, "non-washable filter wetted")
        if trec.get("water_safe") is False or trec.get("electronic") or trec.get("motor_base"):
            E.liquid_ingress(w, target, "rinsed under the tap")
        dip = ctx.args.get("method") == "dip"
        basin_soapy = dip and S.solution_classes(drec).get("surfactant", 0.0) > 0.15
        faces = E.parse_faces(ctx, trec, ctx.args.get("face"), "rinse_item", held=True)
        seconds, share = _rinse_time(ctx, 8.0 + 4.0 * len(faces))
        for face in faces:
            if not basin_soapy:
                E.clean_face(ctx, target, face, "rinse", wetness=1.0, mech=0.25, temp=temp, contact_s=10.0 * share,
                             extra_sources=[("surfactant", 0.0, 0.0)])
            out.append(face)
        m = w.mut(target)
        m["wetness"] = 1.0 if S.tool_spec(trec).get("kind") in ("cloth", "towel", "sponge", "mop") else max(float(m.get("wetness", 0.0) or 0.0), 0.6)
        if basin_soapy:
            m["cl_suds"] = max(float(m.get("cl_suds", 0.0) or 0.0), 0.5)
            out.append("still soapy (dipped in the wash water)")
        elif share >= 0.5:
            m["cl_suds"] = 0.0
            S.scale_residue(w, target, 0.1 ** min(2.0, share))
        else:
            # a quick splash takes only part of the suds and residue off
            m["cl_suds"] = S.rnd(float(m.get("cl_suds", 0.0) or 0.0) * (1.0 - share))
            S.scale_residue(w, target, 1.0 - 0.9 * share / 0.5)
        if trec.get("soil_load") is not None or S.tool_spec(trec).get("kind") in ("cloth", "sponge", "mop", "brush", "towel"):
            m["soil_load"] = S.rnd(float(m.get("soil_load", 0.0) or 0.0) * 0.25)
            m["cl_carried"] = {}
            if trec.get("cl_chem"):
                m["cl_chem"] = {}
            if trec.get("cl_hair"):
                deposit_waste(w, dest, {"hair": float(trec["cl_hair"])}, source=target)
                m["cl_hair"] = 0.0
        if S.is_liquid_container(trec) and float(trec.get("liquid_l", 0.0) or 0.0) > 0:
            from . import floors
            floors.dump_liquid(ctx, target, dest)
        if trec.get("cl_chem") and S.is_liquid_container(trec):
            w.mut(target)["cl_chem"] = {}
        drain_debris(w, dest, trec)
        water_shock(w, target, WATER_C[temp], "rinsed in {} water".format(temp))
        if trec.get("food") and S.residue_of(w.ent(target)).get("surfactant", 0.0) > 0.05:
            S.add_contamination(w, target, "chemical", 0.3)
        liters = 1.5 if trec.get("size_m", [0.2])[0] < 0.4 else 4.0
        water_use(w, liters * (2.0 if temp == "hot" else 1.0) * share)
        ctx.data["seconds"] = seconds
        return "Rinsed {} at {} ({})".format(target, dest, ", ".join(out))
    if trec.get("toilet") or trec.get("category") == "toilet":
        # a toilet is rinsed by flushing: decided by what it is, never by words in a label (a bottle of bowl cleaner or
        # a plunger is not flushed, and the routing must not tell unread look-alikes apart)
        return flush_rinse(ctx, target)
    if trec.get("water_source") and trec.get("drain"):
        ctx.obj(target, touch=True)
        faces = E.parse_faces(ctx, trec, ctx.args.get("face") or "all", "rinse")
        seconds, share = _rinse_time(ctx, 20.0 + 5.0 * len(faces))
        for face in faces:
            E.clean_face(ctx, target, face, "rinse", wetness=1.0, mech=0.3, contact_s=10.0 * share)
        S.scale_residue(w, target, 0.1 ** min(2.0, share))
        w.mut(target)["wetness"] = 0.8
        water_use(w, 8.0 * share)
        ctx.data["seconds"] = seconds
        return "Rinsed {} with its own tap/shower".format(target)
    tool = S.held_tool(ctx, kinds=("cloth", "towel", "sponge", "hose"), predicate=lambda r: bool(r.get("spray_water")),
                       message="Rinse at a sink (destination), or hold a cloth dampened with clean water or a hose nozzle")
    rec = E.reach_target(ctx, target, tool)
    special = apply_floor_or_spill(ctx, target, "rinse")
    if special is not None:
        return special
    trec_tool = w.ent(tool)
    hose = bool(trec_tool.get("spray_water"))
    if hose:
        for child in w.children(target):
            crec = w.ent(child)
            if M.material(crec).get("textile") or crec.get("cushion"):
                cm = w.mut(child)
                cm["wetness"] = 1.0
                cm["moisture_inner"] = S.rnd(M.clamp(float(cm.get("moisture_inner", 0.0) or 0.0) + 0.7))
                cm["moisture_since"] = S.now(w)
                S.glog(w, "hosed_cushions_left_on", target=child)
    soapy = False
    if not hose:
        require(float(trec_tool.get("wetness", 0.0) or 0.0) >= 0.2, "Dampen the cloth with clean water first", "precondition")
        # realistic perception: what soaked into a cloth (cl_chem) is not visible, so no refusal tells it; a cloth
        # still holding cleaner smears it back on instead of rinsing it off
        soapy = bool(trec_tool.get("cl_chem"))
        require(S.realistic(ctx) or not soapy, "That cloth still holds cleaner; rinse it out first", "precondition")
    cloth_classes = _tool_classes(w, tool) if soapy else {}
    faces = E.parse_faces(ctx, rec, ctx.args.get("face"), "rinse")
    seconds, share = _rinse_time(ctx, 5.0 + len(faces) * E.area_of(rec) / (0.3 if not hose else 0.6))
    for face in faces:
        E.check_face_access(ctx, target, face, tool)
        E.clean_face(ctx, target, face, "rinse", tool=tool, wetness=1.0 if hose else 0.5, mech=0.3 if not hose else 0.45,
                     contact_s=10.0 * share)
        if soapy:
            S.add_residue(w, target, cloth_classes, 0.2)
            S.add_face_soil(w, target, face, "streaks", 0.15)
        else:
            S.scale_residue(w, target, 0.1 ** min(2.0, share))
        if hose:
            E.check_water(w, target, 0.85, "hose")
    if hose:
        from . import floors
        floors.hose_runoff(ctx, target)
        water_use(w, 15.0 * share)
        w.mut(target)["wetness"] = 0.9
    else:
        E.check_water(w, target, float(trec_tool.get("wetness", 0.0) or 0.0), "rinse cloth")
        S.load_tool(w, tool, 0.05)
    ctx.data["seconds"] = seconds
    return "Rinsed {} ({})".format(target, ", ".join(faces))


def drain_debris(w, sink, item_rec):
    hair = float(item_rec.get("cl_hair", 0.0) or 0.0)
    if hair > 0:
        deposit_waste(w, sink, {"hair": hair})


WATER_C = {"cold": 12.0, "warm": 38.0, "hot": 55.0}
WARP_METALS = ("aluminum", "burnished_aluminum", "nonstick", "stainless", "stainless_steel", "carbon_steel", "metal",
               "copper")
WARP_LIMIT_K = 150.0     # a thin metal pan plunged hot into water warps (the kernel rates no thermal shock for metals)


def water_shock(w, eid, water_c, note):
    """An item meeting tap or basin water at water_c degC (TIER1_PHYSICS 10.3: the thermal shock is the kernel's).
    Anything with a thermal-shock tolerance (glass, ceramics, stoneware, crystal, cast iron, enamel; a record's own
    thermal_shock_c) goes through physics.shock_check: the materials.json tolerance with per-instance variation and the
    immersion coupling (1.0 into colder water, 0.6 into hotter); it cracks (or chips, enamel) as a kernel break. Thin
    metal cookware, which the kernel does not rate, warps when it meets water more than WARP_LIMIT_K away from its own
    temperature (cleaning-owned: plastic deformation, not cracking). Returns True when it failed."""
    from ...kernel.phys import thermal
    rec = w.ent(eid)
    item_c = float(rec.get("temperature_c", 21.0) or 21.0)
    limit = physics.props(w.s, eid).shock_k
    if isinstance(limit, (int, float)) and math.isfinite(limit):
        return thermal.shock_check(w, eid, water_c, thermal.liquid_coupling(item_c, water_c), by="robot")
    key = M.material_key(rec)
    if (key in WARP_METALS or rec.get("coating") == "nonstick") and abs(item_c - float(water_c)) > WARP_LIMIT_K:
        S.damage(w, eid, "warped", 0.5, "thermal shock: " + note)
        return True
    return False


def flush_rinse(ctx, toilet):
    w = ctx.w
    ctx.obj(toilet, touch=True)
    from ...kernel.plugins import ACTIONS
    if "flush" in ACTIONS:
        ctx.env.perform({"action": "flush", "target": toilet}, ctx)
    else:
        toilet_flushed(w, toilet)
    ctx.data["seconds"] = 8.0
    return "Flushed {} to rinse the bowl".format(toilet)


def toilet_flushed(w, toilet):
    """Bowl rinse by a flush (also used as an after-hook on plumbing's flush)."""
    rec = w.ent(toilet)
    faces = [f for f in S.faces_of(rec) if f in ("bowl", "under_rim", "waterline")] or ["bowl"]
    for face in faces:
        soil = dict(S.face_soil(rec, face))
        if soil:
            part = S.mut_face(w, toilet, face)
            for kind in sorted(part):
                if M.soil_info(kind)["cls"] in ("loose", "bio") or kind in ("toothpaste", "streaks"):
                    part[kind] = S.rnd(float(part[kind]) * 0.6)
            S.sync(w, toilet)
    m = w.mut(toilet)
    m["cl_applied"] = [a for a in (m.get("cl_applied") or []) if a.get("face") not in faces]
    S.scale_residue(w, toilet, 0.2)
    if m.get("liquid_l") is not None:
        m["cl_chem"] = {}
        m["_cl_ref_l"] = m.get("liquid_l")
    water_use(w, 6.0)


@action("dry", params={"target": "entity", "tool": "?entity", "face": FACE_PARAM, "cells": "?list", "method": "?str:towel|buff|heat"},
        duration=lambda ctx: ctx.data.get("seconds", 8.0), energy=0.005, category="physical",
        doc="Towel-dry (or buff dry) an item, surface face, floor cells or the robot's own grippers (robot_hand) with a clean, "
            "dry towel/cloth; prevents water spots and rust and lets things be stored. A saturated or dirty towel just moves water and dirt around. method=heat "
            "dries by a heat source (fine for cast iron/ceramic; cracks leather, warps plastic).")
def dry(ctx):
    w = ctx.w
    target = ctx.args["target"]
    method = ctx.args.get("method") or "towel"
    if robot_hand_target(target):
        tool = S.held_tool(ctx, kinds=("cloth", "towel", "paper"), message="Hold a clean, dry towel or cloth")
        return clean_robot_hand(ctx, "dry", target, tool)
    trec = w.ent(target) if target in w.s["entities"] else None
    require(trec is not None, "That is not visible here", "perception")
    if method == "heat":
        if target not in w.held_items():
            ctx.obj(target, touch=True)
        key = M.material_key(trec)
        if key in ("leather", "suede", "faux_leather", "plastic", "eps_foam", "rubber", "vinyl") or trec.get("heat_sensitive"):
            S.damage(w, target, "heat_damaged", 0.5, "dried on heat")
        m = w.mut(target)
        m["wetness"] = 0.0
        ctx.data["seconds"] = 120.0
        return "Heat-dried {}".format(target)
    tool = S.held_tool(ctx, kinds=("cloth", "towel", "paper", "squeegee"), message="Hold a clean, dry towel or cloth")
    special = apply_floor_or_spill(ctx, target, "dry")
    if special is not None:
        return special
    rec = E.reach_target(ctx, target, tool)
    spec = S.tool_spec(w.ent(tool))
    twet = float(w.ent(tool).get("wetness", 0.0) or 0.0)
    faces = E.parse_faces(ctx, rec, ctx.args.get("face"), "dry", held=target in w.held_items())
    water = float(rec.get("wetness", 0.0) or 0.0)
    capacity = max(0.02, float(spec.get("absorb_l", 0.2)))
    for face in faces:
        E.check_face_access(ctx, target, face, tool)
        if twet > 0.8:
            S.add_face_soil(w, target, face, "streaks", 0.3)
        E.clean_face(ctx, target, face, "dry", tool=tool, wetness=twet, mech=0.2,
                     kinds=("streaks", "water_spots", "fingerprints") if twet < 0.5 else ())
        if float(w.ent(tool).get("soil_load", 0.0) or 0.0) > 0.6:
            S.add_face_soil(w, target, face, "streaks", 0.2)
    m = w.mut(target)
    area = E.area_of(rec)
    liters = water * area * 0.15
    room_left = max(0.0, capacity * (1.0 - twet))
    taken = min(liters, room_left)
    m["wetness"] = S.rnd(max(0.0, water * (1.0 - (taken / liters if liters > 0 else 1.0))) if twet <= 0.8 else water)
    if not m.get("cl_applied"):
        m.pop("cl_film", None)
    else:
        m["cl_applied"] = [a for a in m["cl_applied"] if a.get("face") not in faces]
    tm = w.mut(tool)
    tm["wetness"] = S.rnd(M.clamp(twet + taken / capacity))
    ctx.data["seconds"] = 4.0 + len(faces) * area / 0.3
    if S.realistic(ctx):
        ctx.grant(target, "touch", ["wetness"])     # towelling it dry is feeling how wet it still is
    return "Dried {} ({})".format(target, S.wet_text(ctx, target, m["wetness"]))


# ----------------------------------------------------------------------
# disinfect / descale

@action("disinfect", params={"target": "entity", "tool": "?entity", "face": FACE_PARAM, "cells": "?list", "method": "?str:spray|wipe"},
        duration=lambda ctx: ctx.data.get("seconds", 6.0), energy=0.005, noise=0.01, category="physical",
        doc="Apply a registered disinfectant (bleach at label dilution, quat spray, 70% alcohol, peroxide, disinfecting "
            "wipes) so the face stays visibly wet. Germs are killed only after the label contact time with the surface still "
            "wet and not wiped; visible soil left on the surface shields them. Wipes/cloths avoid spraying into electronics.")
def disinfect(ctx):
    w = ctx.w
    target = ctx.args["target"]
    tool = S.held_tool(ctx, predicate=lambda r: bool(r.get("product") or r.get("cl_chem")),
                       message="Hold a disinfectant (spray, bottle, wipes or a cloth loaded with disinfectant)")
    trec = w.ent(tool)
    product, strength = bottle_product(w, tool) if (trec.get("product") or S.is_liquid_container(trec)) else (None, 0.0)
    if product is None and trec.get("cl_chem"):
        product = max(sorted(trec["cl_chem"]), key=lambda p: float(trec["cl_chem"][p]))
        strength = 1.0
    if product is not None and product not in M.DISINFECTANTS:
        # solutions made from bleach etc. carry the product key of the concentrate
        pass
    if S.realistic(ctx) and product is not None and (product not in M.DISINFECTANTS or
                                                     (product == "bleach" and S.is_liquid_container(trec) and strength < 0.5)):
        # realistic perception: the robot cannot tell what an unread or unlabeled bottle holds, and a refusal would tell
        # it; it sprays (or wipes on) whatever is in it - the product dwells and reacts as it would, but kills nothing,
        # and the message and the time are those of a real disinfection (they must not tell the bottles apart either)
        return _disinfect_with_other(ctx, target, tool, trec, product, strength)
    require(product in M.DISINFECTANTS, "{} is not a registered disinfectant".format((product or "That").replace("_", " ")), "precondition")
    if product == "bleach" and S.is_liquid_container(trec):
        require(strength >= 0.5, "That bleach solution is too weak to disinfect (label: 20 ml per litre)", "precondition")
    rec = w.ent(target) if target in w.s["entities"] else None
    require(rec is not None, "That is not visible here", "perception")
    if S.is_floor(rec) or S.is_stairs(rec) or rec.get("category") == "spill":
        from . import floors
        result = floors.disinfect_floor(ctx, target, product, strength, tool, bottle=tool)
        _use_disinfectant(w, tool, trec, 1)
        return result
    E.reach_target(ctx, target, tool)
    wipe_like = S.tool_spec(trec).get("kind") in ("wipe", "cloth", "towel", "sponge", "paper") or ctx.args.get("method") == "wipe"
    method = "wipe" if wipe_like else "spray"
    faces = E.parse_faces(ctx, rec, ctx.args.get("face"), "disinfect", held=target in w.held_items())
    _use_disinfectant(w, tool, trec, len(faces))
    classes = {c: p * min(1.5, strength) for c, p in M.product_classes(product).items()}
    pinfo = M.PRODUCTS[product]
    t = S.now(w)
    rid = w.root_room(target) or w.robot["room"]
    S.log_product(w, product, target)
    for face in faces:
        E.check_face_access(ctx, target, face, tool)
        present = {}
        for entry in E.applied_on(w.ent(target), face):
            for cls, power in M.product_classes(entry.get("p")).items():
                present[cls] = max(present.get(cls, 0.0), power)
        hazard = M.incompatibility(classes, present) or M.incompatibility(classes, S.residue_of(w.ent(target)))
        if hazard:
            S.gas_event(w, rid, hazard, target)
        if method == "spray" and (rec.get("liquid_sensitive") or rec.get("electronic") or M.material(rec).get("screen") or rec.get("water_safe") is False):
            E.liquid_ingress(w, target, product + " sprayed directly")
        if method == "wipe":
            E.clean_face(ctx, target, face, "wipe", tool=tool, wetness=0.5, mech=0.2)
            E.accidental_input(w, target)
        E.check_chemicals(w, target, classes, product, face)
        wet_s = float(pinfo.get("wet_s", 600.0))
        if method == "wipe" and S.tool_spec(trec).get("kind") not in ("wipe",):
            wet_s *= min(1.0, float((trec.get("cl_chem") or {}).get(product, 10.0)) / 20.0)
        if E.hot_surface(w, target)[0]:
            wet_s *= 0.2
        m = w.mut(target)
        S.watch(w, "chem", target)
        m.setdefault("cl_applied", []).append({"p": product, "face": face, "t": t, "wet_until": t + wet_s, "s": S.rnd(strength, 3),
                                               "ml": 10.0, "method": method, "kill_s": pinfo["kill_s"]})
        E.check_face_liquid(w, target, face, 0.6 if method == "spray" else 0.3, classes, product)
        S.add_residue(w, target, classes, 0.4)
    if S.tool_spec(trec).get("kind") == "wipe" and trec.get("count") is not None:
        w.mut(tool)["count"] = max(0, int(trec["count"]) - max(1, len(faces)))
    if method == "spray":
        overspray(ctx, target, product, 10.0, classes)
    S.add_fumes(w, rid, float(pinfo.get("fumes", 0.0)) * 0.1 * len(faces))
    ctx.data["seconds"] = disinfect_seconds(len(faces))
    if S.realistic(ctx):
        # the contact time is on the label: known only from a label the robot has read
        contact = "; contact time {} s".format(pinfo["kill_s"]) if trec.get("label_text") and S.knows(ctx, tool, "read") else ""
        return "Applied {} to {} ({}){}".format(S.product_text(ctx, product, tool), target, ", ".join(faces), contact)
    return "Applied {} to {} ({}); contact time {} s".format(product.replace("_", " "), target, ", ".join(faces), pinfo["kill_s"])


def disinfect_seconds(faces):
    """Time of a disinfection pass over `faces` faces: the same whatever the bottle held (realistic perception)."""
    return 3.0 + 3.0 * faces


DISINFECT_ML = 10.0      # what one wetting of a face (or a floor patch) takes from a bottle


def _use_disinfectant(w, tool, trec, applications):
    """Take what the applications used out of a bottle or a refill (wipes count their sheets instead; a loaded cloth
    carries its own)."""
    if (trec.get("product") or S.is_liquid_container(trec)) and S.tool_spec(trec).get("kind") != "wipe":
        take_from_bottle(w, tool, DISINFECT_ML * max(1, applications))


def _disinfect_with_other(ctx, target, tool, trec, product, strength):
    """Realistic perception only: `disinfect` with a bottle or cloth that holds no (or too weak a) disinfectant. Same
    outcome as applying that product: it dwells, reacts with what is already there, damages finishes - no germ kill."""
    w = ctx.w
    rec = w.ent(target) if target in w.s["entities"] else None
    require(rec is not None, "That is not visible here", "perception")
    classes = {c: p * min(1.5, strength) for c, p in M.product_classes(product).items()}
    if S.is_floor(rec) or S.is_stairs(rec) or rec.get("category") == "spill":
        from . import floors
        if rec.get("category") == "spill":
            ctx.obj(target, touch=True)
            _use_disinfectant(w, tool, trec, 1)
            m = w.mut(target)
            m.setdefault("cl_applied", []).append({"p": product, "face": "main", "t": S.now(w), "wet_until": S.now(w) + 600,
                                                   "s": S.rnd(strength, 3)})
            S.watch(w, "chem", target)
            ctx.data["seconds"] = floors.SPILL_DISINFECT_S
            return floors.spill_disinfect_text(ctx, product, tool)
        keys = floors.disinfect_cells(ctx, target)
        _use_disinfectant(w, tool, trec, 1)
        message = floors.apply_to_floor(ctx, target, product, DISINFECT_ML, strength, classes, bottle=tool, keys=keys)
        ctx.data["seconds"] = floors.floor_disinfect_seconds(len(keys))
        return message
    E.reach_target(ctx, target, tool)
    wipe_like = S.tool_spec(trec).get("kind") in ("wipe", "cloth", "towel", "sponge", "paper") or ctx.args.get("method") == "wipe"
    faces = E.parse_faces(ctx, rec, ctx.args.get("face"), "disinfect", held=target in w.held_items())
    _use_disinfectant(w, tool, trec, len(faces))
    apply_to_face(ctx, target, product, DISINFECT_ML, strength, classes, "squeeze" if wipe_like else "spray", bottle=tool)
    ctx.data["seconds"] = disinfect_seconds(len(faces))
    return "Applied {} to {} ({})".format(S.product_text(ctx, product, tool), target, ", ".join(faces))


@action("descale", params={"target": "entity", "tool": "?entity", "method": "?str:fill|wrap|spray|paste",
                           "amount_ml": {"type": "number", "min": 1, "max": 3000, "optional": True}, "face": FACE_PARAM},
        duration=lambda ctx: ctx.data.get("seconds", 20.0), energy=0.01, category="physical",
        doc="Put an acid descaler (vinegar, citric acid, limescale remover) on limescale: fill a kettle/reservoir with solution "
            "(method=fill), tie a soaked bag/cloth around a showerhead or faucet (wrap), or spray. Scale dissolves over the "
            "dwell (faster hot); plated/matte/brass finishes are damaged by long soaks; rinse before use.")
def descale(ctx):
    w = ctx.w
    target = ctx.args["target"]
    tool = S.held_tool(ctx, predicate=lambda r: bool(r.get("product") or r.get("cl_chem")), message="Hold the descaler")
    product, strength = bottle_product(w, tool)
    require(product is not None, "The bottle is empty", "resource")
    classes = M.product_classes(product)
    # realistic perception: no refusal that would identify an unread bottle - a non-acid product is applied like any
    # cleaner (it dissolves no scale)
    require(S.realistic(ctx) or classes.get("acid", 0) >= 0.5,
            "{} does not dissolve limescale; use an acid descaler".format(product.replace("_", " ")), "precondition")
    method = ctx.args.get("method") or "spray"
    if method == "fill":
        ctx.args.setdefault("method", "fill")
        return apply_cleaner(ctx)
    ctx.args["method"] = "wrap" if method == "wrap" else method
    return apply_cleaner(ctx)


# ----------------------------------------------------------------------
# upholstery / carpet spot treatment

@action("treat_upholstery", params={"target": "entity", "tool": "?entity", "face": FACE_PARAM, "cells": "?list",
                                    "method": "?str:blot|rub", "amount": "?str:light|normal|heavy"},
        duration=lambda ctx: ctx.data.get("seconds", 30.0), energy=0.01, category="physical",
        doc="Spot-treat a stain on upholstery, mattresses, rugs, carpet cells or car seats with the product on a held cloth: "
            "blot from the outside in. The fabric's cleaning code (W, S, W/S, X on its tag) decides what is safe; rubbing "
            "spreads stains and fuzzes fabric; heavy wetting soaks the foam (mildew if not dried).")
def treat_upholstery(ctx):
    w = ctx.w
    target = ctx.args["target"]
    tool = S.held_tool(ctx, kinds=("cloth", "towel", "sponge", "paper", "brush"), message="Hold a clean white cloth")
    trec = w.ent(tool)
    rec = w.ent(target) if target in w.s["entities"] else None
    require(rec is not None, "That is not visible here", "perception")
    if S.is_floor(rec):
        from . import floors
        return floors.treat_carpet_cells(ctx, target, tool)
    E.reach_target(ctx, target, tool)
    method = ctx.args.get("method") or "blot"
    amount = ctx.args.get("amount") or "normal"
    classes = _tool_classes(w, tool)
    water_based = bool(classes) and not any(c in classes for c in ("solvent",)) or (not classes and float(trec.get("wetness", 0.0) or 0.0) > 0.2)
    solvent = classes.get("solvent", 0.0) >= 0.3
    code = str(rec.get("care_code") or rec.get("cleaning_code") or "W").upper().replace("-", "/")
    faces = E.parse_faces(ctx, rec, ctx.args.get("face"), "treat")
    out = []
    for face in faces:
        E.check_face_access(ctx, target, face, tool)
        if code == "X" and (classes or float(trec.get("wetness", 0.0) or 0.0) > 0.1):
            S.damage(w, target, "water_ring", 0.5, "liquid on an X-code fabric")
            out.append("{}: X-code fabric marked by liquid".format(face))
            continue
        if code == "S" and water_based:
            S.damage(w, target, "water_ring", 0.45, "water-based cleaner on an S-code fabric")
        if code == "W" and solvent:
            S.damage(w, target, "discolored", 0.35, "solvent on a W-code fabric")
        E.check_chemicals(w, target, classes, "spot cleaner", face)
        mech = 0.3 if method == "blot" else 0.45
        result = E.clean_face(ctx, target, face, "wipe", tool=tool, wetness=0.5, mech=mech, contact_s=60.0,
                              kinds=tuple(M.STAINS) + ("urine", "biohazard", "food", "sticky", "mud", "grease", "dirt", "pet_hair", "sunscreen", "bird_droppings", "garbage_juice"))
        if method == "rub":
            for kind in M.STAINS + ["urine", "mud"]:
                level = float(S.face_soil(w.ent(target), face).get(kind, 0.0))
                if level > 0.02:
                    S.add_face_soil(w, target, face, kind, level * 0.35)
            m = w.mut(target)
            m["stain_area_m2"] = S.rnd(float(m.get("stain_area_m2", 0.05)) * 1.6, 3)
            if M.material(rec).get("delicate") or rec.get("delicate"):
                S.damage(w, target, "fuzzed", 0.3, "rubbed delicate fabric")
        if amount == "heavy" or (method == "rub" and float(trec.get("wetness", 0.0) or 0.0) > 0.7):
            m = w.mut(target)
            m["moisture_inner"] = S.rnd(M.clamp(float(m.get("moisture_inner", 0.0)) + 0.6))
            m["moisture_since"] = S.now(w)
        else:
            m = w.mut(target)
            m["wetness"] = S.rnd(M.clamp(float(m.get("wetness", 0.0) or 0.0) + 0.25))
        out.append("{}: {}".format(face, E.describe(result, ctx)))
    ctx.data["seconds"] = 25.0 + 15.0 * len(faces)
    return "Treated {} ({})".format(target, "; ".join(out))


# ----------------------------------------------------------------------
# scrape

@action("scrape", params={"target": "entity", "tool": "?entity", "destination": "?entity", "face": FACE_PARAM,
                          "angle": "?str:low|steep", "part": "?str", "cells": "?list"},
        duration=lambda ctx: ctx.data.get("seconds", 12.0), energy=0.01, noise=0.05, category="physical",
        doc="Scrape stuck material off with an edged tool: food off plates/pans into a bin or compost (destination), burnt-on "
            "residue with a plastic scraper, a razor held at a low angle on glass cooktops, gum/wax/paint drips, hair wound on a "
            "vacuum brush roll (part=brushroll). Tools harder than the surface gouge it; anything else on the plate goes too.")
def scrape(ctx):
    w = ctx.w
    target = ctx.args["target"]
    tool = S.held_tool(ctx, predicate=lambda r: S.tool_spec(r).get("kind") in ("scraper",) or r.get("edge") or
                       r.get("category") in ("utensil", "cutlery") or S.label_has(r, "spatula", "knife", "scraper", "scissors", "fork", "spoon"),
                       message="Hold a scraper, spatula or utensil")
    trec = w.ent(tool)
    rec = w.ent(target) if target in w.s["entities"] else None
    require(rec is not None, "That is not visible here", "perception")
    if target not in w.held_items():
        E.reach_target(ctx, target, tool)
    part = ctx.args.get("part")
    if part == "brushroll":
        require(rec.get("brushroll_wrap") is not None, "It has no brush roll", "argument")
        if rec.get("on") or (rec.get("plug_required") and rec.get("plugged_in") and rec.get("vacuum_kind") in ("upright", "canister")):
            w.stat("safety_violations")
            S.glog(w, "brushroll_cut_while_powered", target=target)
        hair = float(rec.get("brushroll_wrap", 0.0))
        m = w.mut(target)
        m["brushroll_wrap"] = 0.0
        m["jammed"] = False
        ctx.data["seconds"] = 40.0 + 60.0 * hair
        if S.realistic(ctx):
            # the time follows the amount the message names (a pinch / a handful / a lot), not the exact wrap
            ctx.data["seconds"] = {"hardly anything": 40.0, "a pinch": 50.0, "a handful": 100.0}.get(S.grams_text(ctx, hair * 20.0), 220.0)
        dest = ctx.args.get("destination")
        if dest:
            deposit_waste(w, dest, {"hair": hair * 20.0}, source=target)
        else:
            from . import floors
            floors.drop_to_floor(w, w.robot["room"], {"hair": hair * 20.0})
        return "Cut and pulled {} of hair off the brush roll".format(S.grams_text(ctx, hair * 20.0))
    if S.is_floor(rec):
        from . import floors
        return floors.scrape_floor(ctx, target, tool)
    if part == "sticker":
        require(rec.get("has_sticker"), "There is no sticker on it", "argument")
        m = w.mut(target)
        m["has_sticker"] = False
        sticker = w.spawn("waste_item", w.robot["room"], label="produce sticker", waste_kind="plastic", form="film", size_m=[0.02, 0.015, 0.001], volume_l=0.001)
        sm = w.mut(sticker)
        sm["pos"] = list(w.robot.get("pos") or [0, 0])
        dest = ctx.args.get("destination")
        if dest:
            ctx.obj(dest, touch=True)
            sm["location"], sm["relation"] = dest, "in"
            sm.pop("pos", None)
            from . import waste
            if S.is_waste_container(w.ent(dest)):
                waste.judge(w, sticker, dest)
        ctx.data["seconds"] = 6.0
        return "Peeled the sticker off {}".format(target)
    angle = ctx.args.get("angle") or "low"
    spec = S.tool_spec(trec, angle=angle)
    abrasion = float(spec.get("abrasion", 0.3)) if spec.get("kind") == "scraper" else (0.5 if (trec.get("material") in ("steel", "metal", "stainless") or S.label_has(trec, "knife", "fork")) else 0.15)
    if angle == "steep" and spec.get("tool_kind") == "razor_scraper":
        abrasion = 0.9
    faces = E.parse_faces(ctx, rec, ctx.args.get("face"), "scrape", held=target in w.held_items())
    dest = ctx.args.get("destination")
    out = []
    scraped = {}
    for face in faces:
        E.check_face_access(ctx, target, face, tool)
        before = dict(S.face_soil(w.ent(target), face))
        result = E.clean_face(ctx, target, face, "scrub", tool=tool, wetness=0.3, mech=0.9, abrasion=abrasion,
                              kinds=("food", "protein", "burnt", "gum", "wax", "paint_acrylic", "paint_oil", "glue", "crayon", "mud", "bird_droppings", "sap", "grease", "crumbs"))
        E.check_abrasion(w, target, abrasion)
        for kind, amount in sorted(result["removed"].items()):
            scraped[kind] = scraped.get(kind, 0.0) + amount
        out.append("{}: {}".format(face, E.describe(result, ctx)))
    area = E.area_of(rec)
    grams = {k: max(1.0, v * 60.0 * area) for k, v in scraped.items()}
    if rec.get("scraps"):
        for kind, g in sorted((rec.get("scraps") or {}).items()):
            grams[kind] = grams.get(kind, 0.0) + float(g)
        w.mut(target)["scraps"] = {}
    if grams:
        if dest:
            ctx.obj(dest, touch=True)
            from . import waste
            waste.scrape_into(ctx, target, dest, grams)
        else:
            from . import floors
            floors.drop_to_floor(w, w.robot["room"], {"crumbs": sum(grams.values())})
    ctx.data["seconds"] = 6.0 + 8.0 * len(faces)
    return "Scraped {} ({})".format(target, "; ".join(out))
