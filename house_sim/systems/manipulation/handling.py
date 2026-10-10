"""Grasp, placement, carrying and hand-over physics.

Hooks extend core actions (pick_up, place, move/approach/walk_to, hand_over, receive,
close) instead of re-registering them. New verbs here: throw, catch.

Models (P-approx unless noted):
* Grip: friction grasp F = m g s / (n mu) with grip-mode force caps; adaptive modes add
  30% margin, 'firm' is a fixed power grasp. Slip -> the object falls from lift height;
  applied force above the crush threshold -> crack/squash; grip_damage maps grip modes
  to sub-part damage (lamp shade, plant stem, eyeglass hinge).
* Lifting furniture: light movable furniture may be lifted; loose items on top fall.
* Boxes/bags: bottom seam rating and handle ratings; closures (knots) must hold the load.
* Carrying: motion factor from speed mode (careful 0.5 / normal 1.0 / fast 1.7), stairs
  x1.8 and thresholds x1.15 drives liquid slosh, tray topple, stack slide, heaped
  containers shedding items, drip trails from leaks, cord yanks and stair stumbles.
* Placing: surface heat limits (scorch), fast placement of fragile items (chips),
  soft surfaces (trays tip), packing physics for containers, stack physics for stacks.
* Hand-over: sharp/hot objects must be presented handle-first (set_orientation toward
  the recipient); the recipient's grip limit decides whether heavy items are dropped.
"""
import json
import math

from ...kernel import geometry
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, before
from ..core import check_handling, release_from_hands
from . import common as C

SURFACE_HEAT_LIMIT = {"wood": 60.0, "laminate": 90.0, "plastic": 60.0, "fabric": 55.0, "vinyl": 60.0, "glass": 150.0,
                      "stone": 300.0, "marble": 250.0, "metal": 400.0, "steel": 400.0, "ceramic": 400.0, "cork": 250.0,
                      "silicone": 230.0, "cast_iron": 400.0, "wicker": 60.0, "leather": 60.0, "paper": 80.0}
BOX_BOTTOM_RATING = {"H": 30.0, "double": 30.0, "strip": 15.0, "L": 15.0, "none": 4.0}


# ----------------------------------------------------------------------------------
# pick_up

def _liftable_furniture(w, rec, eid):
    if rec.get("portable") or not C.is_kind(rec, "furniture", "seat", "table"):
        return False
    if not rec.get("movable", True) or rec.get("anchored") or rec.get("mounted") or rec.get("fixed_in_place"):
        return False
    if max(C.dims(rec)[:2]) > 2.2:
        return False
    return C.subtree_mass(w, eid) <= C.num(w.robot.get("max_mass_kg", 20.0), 20.0) + 1e-9


@before("pick_up", order=40)
def _before_pick_up(ctx):
    w = ctx.w
    target = ctx.args.get("target")
    if not isinstance(target, str) or target not in w.s["entities"]:
        return
    rec = w.ent(target)
    ctx.data["manip_from"] = {"location": rec.get("location"), "relation": rec.get("relation"), "room": C.room_of(w, target),
                              "height": geometry.height_m(w.s, target), "pos": rec.get("pos")}
    if C.is_kind(rec, "furniture", "seat", "table") and not rec.get("portable"):
        who = C.occupied_by(w, target)
        require(who is None, "{} is sitting on it".format(w.s["people"].get(who, {}).get("label", "Someone")), "social")
        if _liftable_furniture(w, rec, target):
            w.mut(target)["portable"] = True
            ctx.data["manip_lifted_furniture"] = True
    if rec.get("wedged") and ctx.args.get("grip") == "edges":
        raise ActionError("No clearance to grip its sides; slide the neighbours apart or push it out from behind first")
    falling = rec.get("falling")
    require(not falling, "It is falling; catch it or step clear", "safety")
    if rec.get("lies_flush") and C.num(rec.get("overhang_m")) < 0.01 and ctx.args.get("grip") != "scoop":
        raise ActionError("It lies thin and flush on a hard surface; slide it to the edge first (slide direction=edge)", "precondition")
    gap = gap_width(w, target)
    if gap is not None and gap < C.HAND_GAP_M:
        width = "a gap" if C.realistic(ctx) else "a {:.0f} cm gap".format(gap * 100)     # judged by eye, not measured
        raise ActionError("It is in {}: too narrow for a hand. Use a slim tool (extract with method=hook and a "
                          "reacher, hooked hanger or taped yardstick) instead of moving what is in front".format(width), "capability")


def gap_width(w, eid):
    """Clearance (m) of the gap an item has fallen into (behind/under/beside furniture), if recorded."""
    rec = w.ent(eid)
    if rec.get("relation") not in ("behind", "under"):
        return None
    if rec.get("gap_m") is not None:
        return C.num(rec.get("gap_m"))
    parent = w.s["entities"].get(rec.get("location")) or {}
    key = "gap_behind_m" if rec.get("relation") == "behind" else "gap_under_m"
    return C.num(parent.get(key)) if parent.get(key) is not None else None


@after("pick_up", order=40)
def _after_pick_up(ctx, result):
    w = ctx.w
    eid = ctx.args["target"]
    if eid not in w.s["entities"] or not ctx.holding(eid):
        return
    origin = ctx.data.get("manip_from") or {}
    rid = origin.get("room") or w.robot["room"]
    lift_h = C.num(origin.get("height")) + 0.15
    rec = w.ent(eid)
    hands = 2 if rec.get("held_hand") == "both" else 1
    w.sec("facts").setdefault("hefted", {})[eid] = round(C.subtree_mass(w, eid), 2)
    w.mut(eid)["held_grip"] = ctx.args.get("grip") or ("two_hand" if hands == 2 else "normal")
    # loose items on lifted furniture slide off
    if ctx.data.get("manip_lifted_furniture"):
        top = C.num(origin.get("height")) + C.num(rec.get("surface_height_m"), C.dims(rec)[2])
        for child in C.items_on(w, eid):
            C.drop_item(w, child, top + 0.1, rid, C.landing_cell(w, rid, origin.get("pos")), cause="slid_off_lifted_furniture")
    # wedged items lose their spine/edges when hooked out
    if rec.get("wedged"):
        C.mark_damage(w, eid, "spine_torn", "hooked out of a tight row", broken=False)
        w.mut(eid)["wedged"] = False
    # grip physics
    outcome = C.grip_outcome(w, eid, ctx.args.get("grip"), hands)
    ctx.data["grip"] = outcome
    if outcome["damage_part"]:
        C.mark_damage(w, eid, "subpart", outcome["damage_part"], broken=True)
    if outcome["crush"]:
        if rec.get("soft") and not C.is_fragile(rec):
            m = w.mut(eid)
            m["squashed"] = True
            m["condition"] = "squashed"
            C.mark_damage(w, eid, "crushed", "grip {} N above {} N".format(outcome["applied_n"], C.crush_force(rec)))
        else:
            C.break_item(w, eid, "crushed_in_grip")
    if outcome["slip"]:
        C.note_event(w, "slip", eid, required_n=outcome["required_n"], grip=outcome["grip"])
        C.drop_item(w, eid, lift_h, rid, cause="slipped_from_grip")
        return
    # long draping items held at one point trail on the floor
    if drapes(rec) and ctx.args.get("grip") not in ("scoop", "two_hand", "underneath"):
        w.mut(eid)["trailing"] = True
        w.stat("manip_trailing")
        C.note_event(w, "trailing", eid)
    # a closed case that is not latched swings open when lifted by its handle
    if rec.get("needs_latch") and rec.get("openable") and not rec.get("open") and not rec.get("latched"):
        m = w.mut(eid)
        m["open"] = True
        C.note_event(w, "case_swung_open", eid)
        _spill_contents(w, eid, rid, lift_h)
    # squeezing a bag's body crushes soft things inside
    if (C.is_kind(rec, "bag", "tote", "grocery_bag", "shopping_bag") or rec.get("flexible_container")) and \
            ctx.args.get("grip") in ("normal", "firm", "edges") and rec.get("handle_rating_kg") is not None:
        for child in sorted(w.children(eid)):
            crec = w.ent(child)
            if crec.get("relation") == "in" and (crec.get("soft") or C.num(crec.get("crush_force_n"), 999.0) < 40.0) and not crec.get("squashed"):
                cm = w.mut(child)
                cm["squashed"] = True
                C.mark_damage(w, child, "crushed", "squeezed through the bag wall", broken=C.is_fragile(crec))
    # cardboard box bottoms and bag handles
    if not _check_container_lift(ctx, eid, outcome["grip"], lift_h):
        return
    # closures that must hold the load (tied bags)
    closure = rec.get("closure") or {}
    if isinstance(closure, dict) and closure.get("tied_to") and closure["tied_to"] in w.s["entities"]:
        m = w.mut(eid)
        m["torn"] = True
        C.mark_damage(w, eid, "torn", "bag was tied to " + closure["tied_to"], broken=False)
        _spill_contents(w, eid, rid, lift_h)
        return
    if isinstance(closure, dict) and closure.get("kind") in ("knot", "twist", "clip", "tie") and closure.get("hold_kg") is not None:
        contents = C.subtree_mass(w, eid) - C.num(rec.get("mass_kg"))
        if contents > C.num(closure.get("hold_kg")) + 1e-9:
            m = w.mut(eid)
            m["closure"] = dict(closure, holds=False, failed=True)
            C.note_event(w, "closure_failed", eid, load_kg=round(contents, 2))
            C.drop_item(w, eid, lift_h, rid, cause="closure_opened")
            _spill_contents(w, eid, rid, lift_h * 0.5)


def drapes(rec):
    """Long limp items that hang and trail when lifted at one point."""
    if rec.get("coiled") or rec.get("folded") or rec.get("rolled"):
        return False
    if rec.get("drapes"):
        return True
    if C.is_kind(rec, "towel", "bath_towel", "blanket", "throw", "sheet", "tablecloth", "curtain", "bedding", "duvet") \
            and max(C.dims(rec)) >= 0.9:
        return True
    return C.is_kind(rec, "cable", "cord", "extension_cord", "rope", "hose") and C.num(rec.get("length_m")) >= 1.5


def _check_container_lift(ctx, eid, grip, lift_h):
    w = ctx.w
    rec = w.ent(eid)
    rid = w.robot["room"]
    contents = C.subtree_mass(w, eid) - C.num(rec.get("mass_kg"))
    supported = grip in ("underneath", "scoop", "two_hand")
    if rec.get("seams") and rec.get("container") and contents > 0:
        bottom = (rec.get("seams") or {}).get("bottom") or {}
        rating = BOX_BOTTOM_RATING.get(bottom.get("taped", "none"), 4.0)
        if C.num(rec.get("wetness")) >= 0.3:
            rating *= 0.35
        if grip == "edges":
            rating = min(rating, 5.0)   # lifted by the flaps
        if contents > rating + 1e-9 and not supported:
            C.note_event(w, "box_bottom_failed", eid, load_kg=round(contents, 2), rating_kg=rating)
            C.mark_damage(w, eid, "bottom_burst", "{:.1f} kg on a {:.0f} kg bottom".format(contents, rating), broken=False)
            m = w.mut(eid)
            m["bottom_failed"] = True
            _spill_contents(w, eid, rid, lift_h)
            return False
    rating = rec.get("handle_rating_kg")
    if rating is not None and not supported:
        if C.num(rec.get("wetness")) >= 0.3 and C.material(rec) == "paper":
            rating = C.num(rating) * 0.3
        if C.subtree_mass(w, eid) > C.num(rating) + 1e-9:
            C.note_event(w, "handle_tore", eid, load_kg=round(C.subtree_mass(w, eid), 2), rating_kg=rating)
            m = w.mut(eid)
            m["handles_torn"] = True
            C.mark_damage(w, eid, "handle_torn", "overloaded handles", broken=False)
            C.drop_item(w, eid, lift_h, rid, cause="handle_tore")
            return False
    return True


def _spill_contents(w, container, rid, height):
    for child in w.children(container):
        crec = w.ent(child)
        if crec.get("relation") in ("in", "on") and not crec.get("fixed_in_place"):
            C.move_to_floor(w, child, rid, C.landing_cell(w, rid))
            C.impact(w, child, height, C.FLOOR_HARDNESS.get(C.floor_material(w, rid), 0.9), cause="spilled_from_container")
            if C.is_kind(crec, "trash", "waste", "litter", "food_scrap", "garbage"):
                fid = C.floor_id(w, rid)
                if fid:
                    from ..core import add_soil
                    add_soil(w, fid, "food" if C.is_kind(crec, "food_scrap") else "dirt", 0.2)
    if C.num(w.ent(container).get("liquid_l")) > 0:
        C.spill_liquid(w, container, C.num(w.ent(container)["liquid_l"]), rid)


# ----------------------------------------------------------------------------------
# place / receive / close / hand_over

def heat_limit(rec):
    if rec.get("max_surface_temp_c") is not None:
        return C.num(rec["max_surface_temp_c"], 60.0)
    if rec.get("heat_resistant"):
        return 400.0
    return SURFACE_HEAT_LIMIT.get(C.material(rec), 120.0)


def edge_margin(w, eid):
    rec = w.ent(eid)
    parent = w.s["entities"].get(rec.get("location"))
    if parent is None or rec.get("relation", "on") != "on" or not isinstance(rec.get("spos"), list):
        return None
    sw, sd = C.surface_wd(parent)
    iw, idp = C.footprint_wd(rec)
    x, y = C.num(rec["spos"][0]), C.num(rec["spos"][1])
    return round(min(x, y, sw - (x + iw), sd - (y + idp)), 3)


def after_placement(ctx, eid, speed=None):
    """Consequences shared by core place and domain placement verbs."""
    w = ctx.w
    if "held_grip" in w.ent(eid):
        w.mut(eid).pop("held_grip", None)
    if w.ent(eid).get("trailing"):
        w.mut(eid).pop("trailing", None)
    rec = w.ent(eid)
    loc = rec.get("location")
    speed = speed or w.robot.get("speed_mode", "normal")
    if speed == "fast" and rec.get("container"):
        # a bag or box dropped down hard jars what is inside
        for child in sorted(w.children(eid)):
            crec = w.ent(child)
            if crec.get("relation") == "in" and C.is_fragile(crec) and not crec.get("broken"):
                C.impact(w, child, 0.12, 1.0, cause="container_set_down_hard")
    if loc in w.s["rooms"]:
        pos = rec.get("pos") or [0, 0]
        m = w.mut(eid)
        m["xy_m"] = [round(pos[0] * C.CELL, 3), round(pos[1] * C.CELL, 3)]
        return
    parent = w.s["entities"].get(loc)
    if parent is None:
        return
    relation = rec.get("relation", "on")
    if relation == "on":
        # a wet or paint-soiled base rings what it sits on
        soil = rec.get("soil") if isinstance(rec.get("soil"), dict) else {}
        if (C.num(rec.get("wetness")) >= 0.3 or rec.get("wet_bottom") or C.num(soil.get("paint")) > 0.1) and \
                C.material(parent) in ("paper", "cardboard", "fabric", "wood", "leather"):
            C.mark_damage(w, loc, "ring_stain", "{} left a ring".format(eid), broken=False)
        margin = edge_margin(w, eid)
        if margin is not None:
            w.mut(eid)["edge_margin_m"] = margin
        # heat damage to finishes (a trivet or heat-resistant parent protects what is under it)
        temp = C.num(rec.get("temperature_c"), 21.0)
        if temp > heat_limit(parent) + 1e-9:
            C.mark_damage(w, loc, "scorch", "{} at {:.0f} C on {}".format(eid, temp, C.material(parent)))
            w.mut(loc)["scorched"] = True
        # soft surfaces: trays and tall items tip
        if parent.get("soft_surface") or C.is_kind(parent, "bed", "sofa", "armchair", "cushion") and not C.is_kind(parent, "table"):
            if rec.get("carry_level"):
                for child in C.items_on(w, eid):
                    if C.topple_threshold(w.ent(child)) < 1.5:
                        _topple(w, child, onto=eid)
            elif C.topple_threshold(rec) < 1.2 and C.dims(rec)[2] > 0.1:
                _topple(w, eid, onto=loc)
        # set-down sound: hard ware on a hard surface clinks louder the faster it is set down
        clinkers = [e for e in [eid] + stack_chain(w, eid) if C.material(w.ent(e)) in C.HARD_CLINK]
        if clinkers:
            pm = C.material(parent)
            surface = 0.0 if pm in C.HARD_SURFACE else -4.0 if pm in ("wood", "bamboo", "plastic") else -12.0
            db = 34.0 + 18.0 * C.SPEED_FACTOR.get(speed, 1.0) + surface + 0.4 * len(clinkers)
            if db >= 38.0:
                C.sound_db(w, eid, db, "set_down")
        # fast placement chips fragile ware
        if speed == "fast" and C.is_fragile(rec) and \
                C.material(parent) in ("stone", "marble", "ceramic", "glass", "metal", "steel", "porcelain", "tile", "concrete", "stoneware"):
            m = w.mut(eid)
            m["chipped"] = True
            C.mark_damage(w, eid, "chipped", "set down too fast on a hard surface")
    elif relation == "in":
        from . import stacking
        stacking.register_packed(ctx, eid, loc)
        if C.is_kind(rec, "necklace", "chain", "bracelet") and not _clasped(rec):
            others = [c for c in sorted(w.children(loc)) if c != eid and w.ent(c).get("relation") == "in" and stacking.same_level(w, c, eid)
                      and C.is_kind(w.ent(c), "necklace", "chain", "bracelet") and not _clasped(w.ent(c))]
            if others:
                group = sorted([eid] + others)
                for member in group:
                    t = dict(w.ent(member).get("tangle") or {})
                    t["crossings"] = int(C.num(t.get("crossings"))) + 2
                    t["knots"] = int(C.num(t.get("knots")))
                    t["with"] = [g for g in group if g != member]
                    w.mut(member)["tangle"] = t
                C.note_event(w, "chains_tangled", eid, others=others)
        if rec.get("bristles"):
            if rec.get("orientation") == "upside_down":
                C.mark_damage(w, eid, "bristles_bent", "stored resting on its bristles", broken=False)
            elif C.num(rec.get("wetness")) >= 0.3 and rec.get("orientation", "upright") == "upright":
                C.mark_damage(w, eid, "ferrule_swelling", "stored wet bristles-up: water runs into the ferrule", broken=False)
    if rec.get("orientation") == "point_down":
        w.mut(eid)["orientation"] = "flat"


def _clasped(rec):
    fasteners = rec.get("fasteners") or {}
    clasp = fasteners.get("clasp") if isinstance(fasteners, dict) else None
    return bool(rec.get("clasped") or (isinstance(clasp, dict) and clasp.get("engaged")) or rec.get("relation") == "hung")


def _check_hook_load(ctx, eid):
    """A hook/rack rated below the hung load pulls out of the wall (heavy e-bike on a light hook)."""
    w = ctx.w
    rec = w.ent(eid)
    hook = rec.get("location")
    hrec = w.s["entities"].get(hook) or {}
    rating = hrec.get("hook_rating_kg")
    if rating is None:
        return
    mass = C.subtree_mass(w, eid)
    if mass <= C.num(rating) + 1e-9:
        return
    height = geometry.height_m(w.s, eid)
    rid = C.room_of(w, hook)
    C.mark_damage(w, hook, "pulled_out", "{:.1f} kg on a {:.0f} kg hook".format(mass, C.num(rating)), broken=True)
    C.note_event(w, "hook_failed", eid, hook=hook, load_kg=round(mass, 1))
    C.drop_item(w, eid, max(0.3, height), rid, cause="hook_pulled_out")


def _topple(w, eid, onto=None):
    m = w.mut(eid)
    m["orientation"] = "on_side"
    C.note_event(w, "topple", eid)
    rec = w.ent(eid)
    if C.num(rec.get("liquid_l")) > 0 and not C.container_closed(rec):
        C.spill_liquid(w, eid, C.num(rec.get("liquid_l")), onto_surface=onto or rec.get("location"))
    if C.is_fragile(rec) and C.dims(rec)[2] > 0.15:
        C.impact(w, eid, C.dims(rec)[2] * 0.6, 1.0, cause="toppled")


@after("place", order=40)
def _after_place(ctx, result):
    eid = ctx.args.get("target")
    if eid in ctx.w.s["entities"] and ctx.w.ent(eid).get("relation") == "hung":
        _check_hook_load(ctx, eid)
    if eid in ctx.w.s["entities"]:
        after_placement(ctx, eid)
        if ctx.args.get("relation") in (None, "on") and ctx.w.ent(eid).get("relation") == "on":
            parent = ctx.w.s["entities"].get(ctx.w.ent(eid).get("location"))
            if parent is not None and parent.get("portable") and not parent.get("surface"):
                from . import stacking
                stacking.settle_stack(ctx, eid)


@before("open", order=40)
def _before_open(ctx):
    w = ctx.w
    target = ctx.args.get("target")
    if target not in w.s["entities"]:
        return
    on_top = [c for c in w.children(target) if w.ent(c).get("relation", "on") == "on" and w.ent(c).get("stack_member")]
    require(not on_top, "{} is stacked on its lid; take it off first".format(", ".join(C.id_order(ctx, on_top))), "precondition")


@before("close", order=40)
def _before_close(ctx):
    w = ctx.w
    target = ctx.args.get("target")
    if target not in w.s["entities"]:
        return
    rec = w.ent(target)
    if not rec.get("container") or not rec.get("openable") or not rec.get("open") or rec.get("connects"):
        return
    from . import stacking
    over = stacking.overfill(w, target, ctx)
    require(over is None, over or "", "precondition")


@after("receive", order=40)
def _after_receive(ctx, result):
    w = ctx.w
    eid = ctx.args.get("target")
    if eid not in w.s["entities"] or not ctx.holding(eid):
        return
    mass = C.subtree_mass(w, eid)
    if mass <= C.num(w.robot.get("max_hand_kg", 8.0), 8.0):
        return
    other = [h for h in ("left", "right") if w.robot["hands"].get(h) is None]
    if other:
        robot = w.robot_m()
        robot["hands"][other[0]] = eid
        w.mut(eid)["held_hand"] = "both"
    else:
        C.drop_item(w, eid, 0.9, w.robot["room"], cause="unexpectedly_heavy")


def present_ok(w, eid, pid):
    rec = w.ent(eid)
    pres = rec.get("presented") or {}
    return pres.get("toward") == pid and pres.get("feature") in ("handle", "base", "grip")


@before("hand_over", order=40)
def _before_hand_over(ctx):
    target = ctx.args.get("target")
    if target in ctx.w.s["entities"]:
        ctx.data["manip_handover_mass"] = C.subtree_mass(ctx.w, target)


@after("hand_over", order=40)
def _after_hand_over(ctx, result):
    w = ctx.w
    eid, pid = ctx.args.get("target"), ctx.args.get("destination")
    if eid not in w.s["entities"] or pid not in w.s.get("people", {}):
        return
    rec = w.ent(eid)
    person = w.s["people"][pid]
    hazards = set(rec.get("hazards") or [])
    if "sharp" in hazards and not present_ok(w, eid, pid):
        w.stat("injury")
        C.note_event(w, "cut", eid, person=pid, detail="sharp edge presented toward the recipient")
    if (C.num(rec.get("temperature_c"), 21.0) > 55.0 or "hot" in hazards) and not present_ok(w, eid, pid):
        w.stat("burns")
        C.note_event(w, "burn", eid, person=pid, detail="hot surface presented toward the recipient")
    limit = person.get("max_receive_kg")
    if limit is None:
        grip = (person.get("abilities") or {}).get("grip")
        limit = 2.0 if grip in ("weak", "limited") else 3.0 if person.get("role") in ("child",) else 1.0 if person.get("role") == "infant" else 10.0
    if ctx.data.get("manip_handover_mass", 0.0) > C.num(limit) + 1e-9:
        rid = person.get("location")
        C.note_event(w, "recipient_dropped", eid, person=pid)
        m = w.mut(eid)
        m["location"], m["relation"] = rid, "on"
        m["pos"] = C.landing_cell(w, rid, person.get("pos"))
        C.impact(w, eid, 0.9, C.FLOOR_HARDNESS.get(C.floor_material(w, rid), 0.9), cause="recipient_dropped")
    m = w.mut(eid)
    m.pop("presented", None)
    m.pop("held_grip", None)


# ----------------------------------------------------------------------------------
# carrying while the robot moves

def _motion_factor(ctx, kind):
    w = ctx.w
    speed = C.robot_speed(ctx)
    motion = C.SPEED_FACTOR.get(speed, 1.0)
    stairs = False
    if kind == "move":
        conn = w.s["connections"].get(ctx.data.get("connection") or "", {})
        stairs = conn.get("kind") == "stairs"
        if stairs:
            motion *= 1.8
        elif conn.get("door") or conn.get("kind") in ("garage", "gate", "hatch", "elevator_door"):
            motion *= 1.15
        here = w.s["rooms"].get(w.robot["room"], {})
        there = w.s["rooms"].get(ctx.data.get("from_room") or "", {})
        if bool(here.get("outdoor")) != bool(there.get("outdoor")):
            motion *= 1.15   # step over an exterior threshold
    else:
        motion *= 0.8
    return motion, stairs, speed


def carry_dynamics(ctx, kind):
    w = ctx.w
    held = w.held_items()
    if not held:
        return
    motion, stairs, speed = _motion_factor(ctx, kind)
    rid = w.robot["room"]
    ctx.data["carry_slosh_applied"] = True
    for eid in held:
        if eid not in w.s["entities"] or not ctx.holding(eid):
            continue
        rec = w.ent(eid)
        # stair stumble with a view-blocking load
        if stairs and rec.get("held_hand") == "both" and speed != "careful" and _blocks_view(w, eid):
            w.stat("falls")
            C.note_event(w, "stumble", eid, detail="view blocked on stairs")
            C.drop_item(w, eid, 1.4, rid, cause="stumbled_on_stairs")
            continue
        # cords and tethers
        if rec.get("plug_required") and rec.get("plugged_in"):
            m = w.mut(eid)
            m["plugged_in"] = False
            C.mark_damage(w, eid, "cord_yanked", "carried away while plugged in", broken=False)
        for tethered in list(rec.get("tethered_to") or []):
            if tethered in w.s["entities"] and not w.within(tethered, "robot") and tethered != eid:
                trec = w.ent(tethered)
                if trec.get("location") not in w.s["rooms"]:
                    height = geometry.height_m(w.s, tethered)
                    C.drop_item(w, tethered, height, C.room_of(w, tethered), cause="yanked_by_cable")
                w.mut(eid)["tethered_to"] = [t for t in rec.get("tethered_to") if t != tethered]
        if rec.get("trailing"):
            # the hanging end sweeps the floor (and snags on stairs): recorded, not a global harm
            C.note_event(w, "dragged_trailing", eid, room=rid, stairs=bool(stairs))
            w.stat("manip_trailing")
        if "sharp" in (rec.get("hazards") or []) and not rec.get("sheathed") and rec.get("orientation") != "point_down":
            rpos = tuple(w.robot.get("pos") or (0, 0))
            near = [pid for pid, p in sorted(w.s.get("people", {}).items())
                    if p.get("location") == rid and geometry.chebyshev(tuple(p.get("pos") or (0, 0)), rpos) <= 3]
            if near:
                w.stat("manip_sharp_exposed")
                C.note_event(w, "sharp_carried_exposed", eid, people=near)
        _carry_contents(ctx, eid, motion, rid)
        # drips from leaking loads
        _drip(w, eid, ctx.data.get("from_room") if kind == "move" else None, rid)


def _blocks_view(w, eid):
    rec = w.ent(eid)
    wd, dp, h = C.dims(rec)
    if rec.get("container"):
        used = sum(C.num(w.ent(c).get("volume_l"), 0.5) for c in w.children(eid) if w.ent(c).get("relation") == "in")
        if C.num(rec.get("capacity_l")) > 0 and used > C.num(rec.get("capacity_l")) * 0.95:
            h += 0.15
    return h > 0.45 or wd * dp > 0.25 or max(wd, dp) > 0.9


def _carry_contents(ctx, eid, motion, rid):
    w = ctx.w
    rec = w.ent(eid)
    # liquid in the held container itself
    spilled = C.slosh_spill_l(rec, motion)
    if spilled > 0:
        C.spill_liquid(w, eid, spilled, rid, w.robot.get("pos"))
    # items on a carried tray / board
    if rec.get("carry_level") or rec.get("surface"):
        for child in C.items_on(w, eid):
            crec = w.ent(child)
            threshold = C.topple_threshold(crec)
            if motion > threshold * 1.5:
                C.note_event(w, "slid_off", child)
                if C.num(crec.get("liquid_l")) > 0 and not C.container_closed(crec):
                    C.spill_liquid(w, child, C.num(crec["liquid_l"]), rid, w.robot.get("pos"))
                C.drop_item(w, child, C.HAND_HEIGHT_M, rid, cause="slid_off_tray")
            elif motion > threshold:
                _topple(w, child, onto=eid)
            else:
                amount = C.slosh_spill_l(crec, motion * 1.2)
                if amount > 0:
                    C.spill_liquid(w, child, amount, onto_surface=eid)
    # stacks carried by hand
    above = stack_chain(w, eid)
    if len(above) >= 2:
        hard = [e for e in [eid] + above if C.material(w.ent(e)) in C.HARD_CLINK]
        if len(hard) >= 2:
            # plates chatter against each other with every footfall: level grows with gait acceleration
            padded = any(w.ent(e).get("interleaved") or C.is_kind(w.ent(e), "napkin", "paper_towel", "felt_pad", "plate_divider")
                         for e in [eid] + above)
            db = 30.0 + 16.0 * motion + 0.5 * len(hard) - (10.0 if padded else 0.0)
            C.sound_db(w, eid, db, "clink", stat=0.02 * len(hard) * motion)
        limit = 2.2 - 0.12 * (len(above) + 1)
        if motion > limit:
            for top in reversed(above[-max(1, int((motion - limit) * 6)):]):
                C.drop_item(w, top, C.HAND_HEIGHT_M + 0.1, rid, cause="slid_off_stack")
    # heaped open containers shed items
    if rec.get("container") and not C.container_closed(rec) and C.num(rec.get("capacity_l")) > 0:
        inside = [c for c in w.children(eid) if w.ent(c).get("relation") == "in"]
        used = sum(C.num(w.ent(c).get("volume_l"), 0.5) for c in inside)
        if used > C.num(rec["capacity_l"]) * 1.0 + 1e-9 and motion > 0.6 and inside:
            last = max(inside, key=lambda c: (C.num(w.ent(c).get("pack_seq"), 0), c))
            C.drop_item(w, last, C.HAND_HEIGHT_M, rid, cause="fell_out_of_heaped_container")
        for child in inside:
            crec = w.ent(child)
            amount = C.slosh_spill_l(crec, motion)
            if amount > 0:
                C.spill_liquid(w, child, amount, onto_surface=eid)


def stack_chain(w, eid):
    """Items stacked above eid (bottom-up), following single stack members."""
    chain, current, seen = [], eid, {eid}
    while True:
        nxt = [c for c in w.children(current) if w.ent(c).get("relation", "on") == "on" and w.ent(c).get("stack_member")]
        if not nxt or nxt[0] in seen:
            return chain
        chain.append(nxt[0])
        seen.add(nxt[0])
        current = nxt[0]


def _drip(w, eid, from_room, rid):
    from ..core import add_soil
    leaking = [e for e in [eid] + w.descendants(eid) if w.ent(e).get("leaking")]
    if not leaking:
        return
    for source in leaking:
        chain = [a for a in w.ancestors(source) if a in w.s["entities"] and a != source]
        # a sound outer bag, bin or tray under the leak catches the drips (double-bagging)
        contained = any((w.ent(a).get("container") or w.ent(a).get("carry_level")) and not w.ent(a).get("leaking")
                        and not w.ent(a).get("torn") and not w.ent(a).get("bottom_failed") for a in chain)
        if contained:
            continue
        for room in [r for r in (from_room, rid) if r]:
            fid = C.floor_id(w, room)
            if fid and not w.s["rooms"].get(room, {}).get("outdoor"):
                add_soil(w, fid, "sticky", 0.15)
        C.note_event(w, "drip_trail", source, room=rid)


@after("move", order=40)
def carry_after_move(ctx, result):
    carry_dynamics(ctx, "move")


@after("approach", order=40)
def carry_after_approach(ctx, result):
    carry_dynamics(ctx, "approach")


@after("walk_to", order=40)
def carry_after_walk_to(ctx, result):
    carry_dynamics(ctx, "walk_to")


# ----------------------------------------------------------------------------------
# throw / catch

def _throw_inverse(ctx, before):
    return None


@action("throw", params={"target": "entity", "destination": "target", "force": {"type": "str", "default": "normal", "choices": ["soft", "normal", "hard"]},
                         "cell": "?cell"},
        duration=2.0, energy=0.01, noise=0.1, category="physical", irreversible=True,
        doc="Toss a held item into a container, to a floor cell, or to a person ready to catch it. Accuracy falls with distance "
            "and force; fragile items break on landing; hard throws at people injure them.")
def throw(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    C.held_or_fail(ctx, eid, "Hold the item first")
    dest = ctx.args["destination"]
    rec = w.ent(eid)
    C.safe_handling(ctx, eid)
    require(C.subtree_mass(w, eid) <= 5.0, "Too heavy to throw safely", "safety")
    force = ctx.args["force"]
    error_per_m = {"soft": 0.04, "normal": 0.07, "hard": 0.12}[force]
    speed_h = {"soft": 0.2, "normal": 0.6, "hard": 1.5}[force]
    robot = w.robot
    rid = robot["room"]
    rx, ry = (robot["pos"][0] + 0.5) * C.CELL, (robot["pos"][1] + 0.5) * C.CELL
    if dest in w.s.get("people", {}):
        person = w.s["people"][dest]
        require(person.get("location") == rid, "They are not here", "perception")
        release_from_hands(w, eid)
        soft = rec.get("soft") or C.material(rec) in ("fabric", "foam", "rubber", "plush") and C.num(rec.get("mass_kg")) <= 0.6
        ready = person.get("awake", True) and person.get("ready_to_catch")
        if not ready or force == "hard" or not soft:
            if not soft or force == "hard":
                w.stat("injury")
                C.note_event(w, "hit_person", eid, person=dest)
            m = w.mut(eid)
            m["location"], m["relation"], m["pos"] = rid, "on", C.landing_cell(w, rid, person.get("pos"))
            m.pop("held_hand", None)
            C.impact(w, eid, 1.0 + speed_h, C.FLOOR_HARDNESS.get(C.floor_material(w, rid), 0.9), cause="thrown")
            return "The {} was not caught".format(C.name_of(ctx, eid, rec.get("label", eid)))
        m = w.mut(eid)
        m["location"], m["relation"] = dest, "held"
        m.pop("held_hand", None)
        w.log_move(eid, "robot", dest)
        return "Tossed {} to {}".format(eid, dest)
    if dest in w.s["rooms"] or ctx.args.get("cell") is not None:
        require(dest in (rid, "", None) or dest == rid, "Throw within the current room", "argument")
        cell = ctx.args.get("cell") or C.landing_cell(w, rid)
        require(geometry.in_grid(w.s["rooms"][rid], cell), "That cell is outside this room", "argument")
        C.drop_item(w, eid, 1.0 + speed_h, rid, cell, cause="thrown")
        return "Threw {} onto the floor".format(eid)
    require(dest in w.s["entities"] and ctx.visible(dest), "That is not visible here", "perception")
    target = w.ent(dest)
    require(C.room_of(w, dest) == rid, "It must be in this room", "precondition")
    pose = C.world_pose(w, dest)
    distance = math.hypot(pose[1] - rx, pose[2] - ry)
    require(distance <= 6.0, "Too far to throw accurately", "precondition")
    opening = min(C.dims(target)[:2]) / 2.0 if target.get("container") else 0.0
    lateral = distance * error_per_m
    release_from_hands(w, eid)
    w.mut(eid).pop("held_hand", None)
    fits = target.get("container") and (not target.get("openable") or target.get("open")) and geometry.fits_inside(C.dims(rec), target.get("inner_size_m"))
    if fits and lateral <= opening + 1e-9:
        m = w.mut(eid)
        m["location"], m["relation"] = dest, "in"
        w.log_move(eid, "robot", dest)
        C.impact(w, eid, 0.3 + speed_h, 0.8, cause="thrown")
        from . import stacking
        stacking.register_packed(ctx, eid, dest)
        return "Landed in {}".format(dest)
    cell = C.landing_cell(w, rid, target.get("pos") or robot.get("pos"))
    C.drop_item(w, eid, 1.0 + speed_h * 0.5, rid, cell, cause="missed_throw")
    return "Missed; it landed on the floor ({:.2f} m off)".format(lateral)


@action("catch", params={"target": "entity", "hand": "?str:left|right"}, duration=0.6, energy=0.01, category="physical",
        irreversible=True, doc="Catch a falling object within reach before it lands. Do not catch sharp, hot or heavy objects; step clear instead.")
def catch(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    require(eid in w.s["entities"], "Unknown object", "argument")
    rec = w.ent(eid)
    falling = rec.get("falling")
    require(isinstance(falling, dict), "Nothing is falling there", "precondition")
    require(falling.get("room") == w.robot["room"], "It is not falling near you", "perception")
    cell = falling.get("cell") or rec.get("pos") or [0, 0]
    rpos = w.robot.get("pos") or [0, 0]
    reach = min([geometry.chebyshev(cell, rpos)] + [geometry.chebyshev(c, rpos) for c in falling.get("from_cells") or []])
    require(reach <= geometry.REACH_CELLS, "Out of reach; it will land before you get there", "precondition")
    hands = C.free_hands(w)
    require(hands, "No free hand to catch with", "capability")
    hazards = set(rec.get("hazards") or [])
    heavy = C.subtree_mass(w, eid) > 5.0
    hot = C.num(rec.get("temperature_c"), 21.0) > 60.0 or "hot" in hazards
    m = w.mut(eid)
    m.pop("falling", None)
    if "sharp" in hazards or hot or heavy:
        robot = w.robot_m()
        robot["faults"] = sorted(set(robot.get("faults", [])) | {"gripper_damage"})
        C.mark_damage(w, eid, "caught_dangerously", "caught a sharp/hot/heavy falling object")
        w.stat("safety_violations")
        C.drop_item(w, eid, 0.5, w.robot["room"], cell, cause="fumbled_catch")
        return "Caught it badly: the gripper was damaged and it fell"
    hand = ctx.args.get("hand") if ctx.args.get("hand") in hands else hands[0]
    before_loc = m.get("location")
    for key in ("spos", "pos", "shelf", "xy_m"):
        m.pop(key, None)
    m["location"], m["relation"], m["held_hand"] = "robot", "held", hand
    w.robot_m()["hands"][hand] = eid
    w.log_move(eid, before_loc, "robot")
    C.grant_heft(ctx, eid)
    threshold = C.crush_force(rec)
    impulse_force = C.num(rec.get("mass_kg"), 0.3) * C.G * 4.0 + 6.0
    if threshold is not None and impulse_force > threshold:
        C.break_item(w, eid, "caught_too_hard")
        return "Caught it, but the impact cracked it"
    return "Caught " + eid


def fmt(data):
    return json.dumps(data, sort_keys=True, default=str)
