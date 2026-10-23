"""Grasp, placement, carrying and hand-over physics.

Hooks extend core actions (pick_up, place, move/approach/walk_to, hand_over, close) instead of re-registering them.
New verbs here: throw, catch.

Models (P-approx unless noted):
* Grip: friction grasp F = m g s / (n mu) with grip-mode force caps (commanded levels clamped to the hand's
  body.grip_limit_n; mu the grasp friction, from the item's surface and body.grip_mu); adaptive modes add 30% margin,
  'firm' is a fixed power grasp. Slip -> the robot drops it (body_faults.drop_held); applied force above the squeeze
  threshold -> crack/squash; grip_damage maps grip modes to sub-part damage (lamp shade, plant stem, eyeglass hinge).
* Lifting furniture: light movable furniture may be lifted (within the body's derated two-hand capacity); loose items
  on top fall.
* Boxes/bags: bottom seam rating and handle ratings; closures (knots) must hold the load. While the kernel's handle
  law is deferred this domain applies them at lift-off; the phys.props hook below gives the kernel the same ratings
  (a taped box bottom, a soaked paper bag, a load supported from below) for when it is active.
* Carrying: motion factor from speed mode (careful 0.5 / normal 1.0 / fast 1.7), stairs x1.8 and thresholds x1.15:
  items sliding off a rushed tray, a carried plate stack shedding its top, heaped containers shedding items, drip
  trails from leaks, cord yanks and stair stumbles with a view-blocking load (sight.carried_occluder). Liquid slosh
  and riders tipping over are the kernel's carry law once it is active (this domain's copy runs while it is deferred).
* Placing: hot items on finishes (contact_heat), wet bases on water-sensitive finishes (wet_contact) and fast
  set-downs of fragile ware (setdown) while those laws are deferred; soft surfaces (trays tip), packing physics for
  containers, stack physics for stacks.
* Hand-over: sharp/hot objects must be presented handle-first (set_orientation toward the recipient); the
  recipient's grip limit decides whether heavy items are dropped. A two-hand hand-over (receive) is the kernel's.
"""
import json
import math

from ...kernel import body as kbody, body_faults, geometry, sight
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.phys import impact as kimpact, statics as kstatics, thermal as kthermal
from ...kernel.plugins import after, before, hook
from ..core import release_from_hands
from . import common as C

BOX_BOTTOM_RATING = {"H": 30.0, "double": 30.0, "strip": 15.0, "L": 15.0, "none": 4.0}
SUPPORTED_GRIPS = ("underneath", "scoop", "two_hand")   # the load rests on the hands: handles and bottoms carry nothing
WET_PAPER_HANDLE = 0.3       # a soaked paper bag's handles keep about 30 % of their strength
WET_BOX_BOTTOM = 0.35        # a soaked cardboard bottom keeps about a third of its strength
FLAP_LIFT_KG = 5.0           # lifted by its flaps (grip edges), a box holds at most this much


# ----------------------------------------------------------------------------------
# pick_up

def _liftable_furniture(w, rec, eid):
    if rec.get("portable") or not C.is_kind(rec, "furniture", "seat", "table"):
        return False
    if not rec.get("movable", True) or rec.get("anchored") or rec.get("mounted") or rec.get("fixed_in_place"):
        return False
    if max(C.dims(rec)[:2]) > 2.2:
        return False
    return C.subtree_mass(w, eid) <= kbody.capacity_kg(w.s, "both", eid) + 1e-9


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
    # the grip it is held by: the command's, else the one the body's grasp planner chose and core stored (a pinch for
    # a small item, edges for a thin flat one), else this domain's default for the hands used
    grip = ctx.args.get("grip") or rec.get("held_grip")
    w.mut(eid)["held_grip"] = grip or ("two_hand" if hands == 2 else "normal")
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
    outcome = C.grip_outcome(w, eid, grip, hands)
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
        body_faults.drop_held(w, eid, lift_h, "slip")      # the robot lets go: robot_drops, the kernel's fall
        return
    # long draping items held at one point trail on the floor
    if drapes(rec) and grip not in ("scoop", "two_hand", "underneath"):
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
            body_faults.drop_held(w, eid, lift_h, "closure_opened")
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


def carried_ratings(rec, grip=None):
    """(bottom rating, handle rating) in kg of a container lifted with a grip, None for unrated: a seamed box's bottom
    by how it was taped (H or double 30 kg, strip or L 15, untaped 4; soaked x0.35; lifted by its flaps at most 5 kg),
    a bag's handles (soaked paper x0.3). A grip from below (underneath, scoop, two_hand) loads neither."""
    grip = grip or rec.get("held_grip")
    if grip in SUPPORTED_GRIPS:
        return None, None
    bottom = None
    if rec.get("seams") and rec.get("container"):
        taped = ((rec.get("seams") or {}).get("bottom") or {}).get("taped", "none")
        bottom = BOX_BOTTOM_RATING.get(taped, BOX_BOTTOM_RATING["none"])
        if C.num(rec.get("wetness")) >= 0.3:
            bottom *= WET_BOX_BOTTOM
        if grip == "edges":
            bottom = min(bottom, FLAP_LIFT_KG)
    handle = rec.get("handle_rating_kg")
    if handle is not None:
        handle = C.num(handle)
        if C.num(rec.get("wetness")) >= 0.3 and C.material(rec) == "paper":
            handle *= WET_PAPER_HANDLE
    return bottom, handle


def _carry_rating_props(record, phys):
    """phys.props hook (TIER1_PHYSICS 7.3): the kernel's handle law sees the rating this domain applies to a carried
    container -- the weaker of a seamed box's bottom (by its taping) and a bag's handles -- and none while the hands
    support it from below (held underneath, its handles and bottom carry nothing)."""
    if record.get("held_grip") in SUPPORTED_GRIPS:
        return {"handle_kg": None}
    ratings = [r for r in carried_ratings(record) if r is not None]
    if not ratings:
        return None
    current = phys.get("handle_kg")
    lowest = min(ratings)
    if current is not None and current < lowest:
        return None
    return {"handle_kg": float(lowest)}


_carry_rating_props.applies = lambda record: record.get("relation") == "held" and bool(
    record.get("held_grip") in SUPPORTED_GRIPS or (record.get("seams") and record.get("container"))
    or record.get("handle_rating_kg") is not None)
hook("phys.props")(_carry_rating_props)


def _check_container_lift(ctx, eid, grip, lift_h):
    """A box bottom or bag handles over their rating fail at lift-off (this domain's copy of the kernel handle law,
    kept while that law is deferred): the box's bottom bursts and its contents fall, or the bag's handles tear and it
    drops with them. Returns False when it failed."""
    w = ctx.w
    if C.kernel_owns("handle"):
        return True          # the kernel's handle law tears it at settle (the ratings reach it through phys.props)
    rec = w.ent(eid)
    rid = w.robot["room"]
    contents = C.subtree_mass(w, eid) - C.num(rec.get("mass_kg"))
    bottom, handle = carried_ratings(rec, grip)
    if bottom is not None and contents > 0 and contents > bottom + 1e-9:
        C.note_event(w, "box_bottom_failed", eid, load_kg=round(contents, 2), rating_kg=bottom)
        C.mark_damage(w, eid, "bottom_burst", "{:.1f} kg on a {:.0f} kg bottom".format(contents, bottom), broken=False)
        m = w.mut(eid)
        m["bottom_failed"] = True
        m["torn"] = True
        _spill_contents(w, eid, rid, lift_h)
        return False
    if handle is not None and C.subtree_mass(w, eid) > handle + 1e-9:
        C.note_event(w, "handle_tore", eid, load_kg=round(C.subtree_mass(w, eid), 2), rating_kg=handle)
        m = w.mut(eid)
        m["handles_torn"] = True
        m["torn"] = True
        C.mark_damage(w, eid, "handle_torn", "overloaded handles", broken=False)
        body_faults.drop_held(w, eid, lift_h, "handle_tore")
        return False
    return True


def _spill_contents(w, container, rid, height):
    """What is in or on a container that opened, tore or burst falls height metres to the floor around the robot (the
    kernel's fall for each item: breakage, a spilled liquid, floor dents), and its own liquid runs out."""
    at = w.robot.get("pos") if rid == w.robot.get("room") else None
    for child in sorted(w.children(container)):
        crec = w.ent(child)
        if crec.get("relation") in ("in", "on") and not crec.get("fixed_in_place"):
            C.drop_item(w, child, height, rid, at, cause="spilled_from_container")
            if C.is_kind(crec, "trash", "waste", "litter", "food_scrap", "garbage"):
                fid = C.floor_id(w, rid)
                if fid:
                    from ..core import add_soil
                    add_soil(w, fid, "food" if C.is_kind(crec, "food_scrap") else "dirt", 0.2)
    if C.num(w.ent(container).get("liquid_l")) > 0:
        C.spill_liquid(w, container, C.num(w.ent(container)["liquid_l"]), rid)


# ----------------------------------------------------------------------------------
# place / close / hand_over

def heat_limit(w, sid):
    """Hottest contact (deg C) a support's finish tolerates: the kernel's (phys.thermal.heat_limit_c: the record's
    heat_limit_c, a heat-resistant item's rating, else its material class, per-instance variation applied); None: no
    limit."""
    return kthermal.heat_limit_c(w.s, sid)


def edge_margin(w, eid):
    rec = w.ent(eid)
    parent = w.s["entities"].get(rec.get("location"))
    if parent is None or rec.get("relation", "on") != "on" or not isinstance(rec.get("spos"), list):
        return None
    sw, sd = C.surface_wd(parent)
    iw, idp = C.footprint_wd(rec)
    x, y = C.num(rec["spos"][0]), C.num(rec["spos"][1])
    return round(min(x, y, sw - (x + iw), sd - (y + idp)), 3)


def jolt_contents(w, container, speed):
    """A bag or box set down fast jars what is inside: each fragile item takes the set-down drop of the speed
    (phys.impact.SETDOWN_DROP_M: 6 cm fast) times the hardness of what the container landed on, through the
    container's and its own padding (the kernel's impact: eggs crack, glasses survive)."""
    drop = kimpact.SETDOWN_DROP_M.get(C.effective_speed(w, speed), 0.0)
    rec = w.ent(container)
    loc = rec.get("location")
    if drop <= 0 or (loc not in w.s["entities"] and loc not in w.s["rooms"]):
        return
    if loc in w.s["rooms"]:
        land = kimpact.landing(w.s, loc, rec.get("pos"))
    else:
        land = kimpact.landing(w.s, C.room_of(w, container), onto=loc)
    h_eff = kimpact.effective_drop_m(drop, land, C.subtree_mass(w, container)) / kstatics.padding_factor(rec)
    for child in sorted(w.children(container)):
        crec = w.ent(child)
        if crec.get("relation") == "in" and C.is_fragile(crec) and not crec.get("broken"):
            kimpact.impact(w, child, h_eff / kstatics.padding_factor(crec), "container_set_down_hard")


def after_placement(ctx, eid, speed=None, kernel_setdown=False):
    """Consequences shared by core place and domain placement verbs. kernel_setdown: the caller (core place) already
    applied the kernel's set-down law when it is active; this domain's own placement verbs apply it here instead."""
    w = ctx.w
    if "held_grip" in w.ent(eid):
        w.mut(eid).pop("held_grip", None)
    if w.ent(eid).get("trailing"):
        w.mut(eid).pop("trailing", None)
    rec = w.ent(eid)
    loc = rec.get("location")
    speed = C.effective_speed(w, speed)
    if speed == "fast" and rec.get("container"):
        jolt_contents(w, eid, speed)
    if C.kernel_owns("setdown") and not kernel_setdown:
        kimpact.setdown(w, eid, speed)          # the set-down law for this domain's own placement verbs
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
        # a wet base rings a water-sensitive finish (the kernel's wet_contact law once it is active, which also marks a
        # sweating cold can after a while); wet paint transfers whatever that law says
        soil = rec.get("soil") if isinstance(rec.get("soil"), dict) else {}
        wet = (C.num(rec.get("wetness")) >= 0.3 or rec.get("wet_bottom")) and not C.kernel_owns("wet_contact")
        if (wet or C.num(soil.get("paint")) > 0.1) and C.material(parent) in ("paper", "cardboard", "fabric", "wood", "leather"):
            C.mark_damage(w, loc, "ring_stain", "{} left a ring".format(eid), broken=False)
        margin = edge_margin(w, eid)
        if margin is not None:
            w.mut(eid)["edge_margin_m"] = margin
        # heat damage to finishes (a trivet or heat-resistant parent protects what is under it): this domain's copy of
        # the contact_heat law, with the kernel's heat limits and contact temperature, while that law is deferred
        if not C.kernel_owns("contact_heat"):
            limit = heat_limit(w, loc)
            temp = kthermal.contact_temp_c(w.s, eid)
            if limit is not None and temp > limit + 1e-9:
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
        # fast placement chips fragile ware (this domain's copy of the set-down law, while it is deferred)
        if speed == "fast" and C.is_fragile(rec) and not C.kernel_owns("setdown") and not rec.get("broken") and \
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
    """A hook or rack loaded beyond its rating pulls out (a heavy e-bike on a light hook): this domain's copy of the
    kernel hang law, kept while that law is deferred. The verdict and the consequence are the kernel's
    (phys.statics.overload on the hung slot: the per-instance rating, the scene allowance; phys.impact.collapse: the
    hung items fall, an adhesive hook comes off, a drywall anchor tears out, any other hanger bends)."""
    w = ctx.w
    if C.kernel_owns("hang"):
        return
    hook_id = w.ent(eid).get("location")
    if hook_id not in w.s["entities"]:
        return
    status = kstatics.overload(w.s, hook_id, ("hung",), ctx.env)
    if status is None or not status["fails"]:
        return
    C.note_event(w, "hook_failed", eid, hook=hook_id, load_kg=round(status["load"], 1))
    kimpact.collapse(w, hook_id, ("hung",))


def _topple(w, eid, onto=None):
    """An item tips onto its side where it stands (a tray on a soft seat, a tall item knocked): the kernel's tip
    (phys.impact.tip_over: on_side, the impact of its centre of mass dropping, an open container's liquid run out,
    what rested on it coming off, a fall when it now hangs past the edge)."""
    C.note_event(w, "topple", eid)
    return kimpact.tip_over(w, eid, "toppled")


@after("place", order=40)
def _after_place(ctx, result):
    eid = ctx.args.get("target")
    if eid in ctx.w.s["entities"] and ctx.w.ent(eid).get("relation") == "hung":
        _check_hook_load(ctx, eid)
    if eid in ctx.w.s["entities"]:
        after_placement(ctx, eid, kernel_setdown=True)
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


def present_ok(w, eid, pid):
    rec = w.ent(eid)
    pres = rec.get("presented") or {}
    return pres.get("toward") == pid and pres.get("feature") in ("handle", "base", "grip")


HANDOVER_CONTACT_S = 2.0        # a recipient grips a handed-over thing about this long before letting go
CHILD_ROLES = ("child", "toddler", "infant")


def _is_child(person):
    role = (person or {}).get("role", "adult")
    age = (person or {}).get("age")
    return role in CHILD_ROLES or (age is not None and C.num(age, 99.0) < 13 and role not in ("adult", "elderly"))


def hot_handover_burns(w, eid, pid):
    """Whether handing eid to pid burns their hand: the same rule as the people domain's hot hand-over (its surface
    burns skin within the HANDOVER_CONTACT_S grip by the kernel's thermal.burn_seconds, Moritz and Henriques: an adult
    at about 65 C and above, a child at about 60 C), unless it is offered by its handle / base / grip, held by its
    handle, or insulated / cool to the touch. The surface temperature decides, not a "hot" hazard tag (a hot glue
    gun that is off and at room temperature burns nobody)."""
    rec = w.ent(eid)
    person = w.s["people"].get(pid) or {}
    if present_ok(w, eid, pid) or rec.get("insulated") or rec.get("cool_touch") or rec.get("handle_out"):
        return False
    if "handle" in (rec.get("safe_grips") or []) and rec.get("held_grip") == "handle":
        return False
    temp = C.num(rec.get("surface_temp_c", rec.get("temperature_c")), 21.0)
    return kthermal.burn_seconds(temp, child=_is_child(person)) <= HANDOVER_CONTACT_S


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
    if hot_handover_burns(w, eid, pid):
        w.stat("burns")
        C.note_event(w, "burn", eid, person=pid, detail="hot surface presented toward the recipient")
    limit = person.get("max_receive_kg")
    if limit is None:
        grip = (person.get("abilities") or {}).get("grip")
        limit = 2.0 if grip in ("weak", "limited") else 3.0 if person.get("role") in ("child",) else 1.0 if person.get("role") == "infant" else 10.0
    m = w.mut(eid)
    m.pop("presented", None)
    m.pop("held_grip", None)
    if ctx.data.get("manip_handover_mass", 0.0) > C.num(limit) + 1e-9:
        # too heavy for them: it slips out of their hands, about 0.9 m up, onto the floor where they stand (the
        # kernel's fall, charged to the robot's hand-over)
        rid = person.get("location")
        C.note_event(w, "recipient_dropped", eid, person=pid)
        kimpact.fall(w, eid, height_m=0.9, cause="recipient_dropped", at=(rid, list(person.get("pos") or [0, 0])))


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


STUMBLE_EXTRA_M = 0.4        # a load dropped in a stumble on the stairs lands about two risers below the hands


def carry_dynamics(ctx, kind):
    w = ctx.w
    held = w.held_items()
    if not held:
        return
    motion, stairs, speed = _motion_factor(ctx, kind)
    rid = w.robot["room"]
    kernel_carry = C.kernel_owns("carry")
    if not kernel_carry:
        ctx.data["carry_slosh_applied"] = True      # plumbing's legacy slosh steps aside for this domain's copy
    occluder = sight.carried_occluder(w.s) if stairs and speed != "careful" else None
    for eid in held:
        if eid not in w.s["entities"] or not ctx.holding(eid):
            continue
        rec = w.ent(eid)
        # stair stumble with a load that hides the treads (the camera's carried-load occluder): the robot catches
        # itself and the load drops down the stairs (body_faults.drop_held: robot_drops, the kernel's fall). The robot
        # does not fall, so no fall is counted (TIER1_INTEGRATION 6; navigation's own rule makes a robot descending
        # blind fall, through body_faults.fall)
        if occluder is not None and eid in occluder["items"]:
            C.note_event(w, "stumble", eid, detail="view blocked on stairs")
            body_faults.drop_held(w, eid, kbody.hand_drop_m(w.s) + STUMBLE_EXTRA_M, "stumbled_on_stairs")
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
        _carry_contents(ctx, eid, motion, rid, kernel_carry)
        # drips from leaking loads
        _drip(w, eid, ctx.data.get("from_room") if kind == "move" else None, rid)


def _carry_contents(ctx, eid, motion, rid, kernel_carry=False):
    """What riding a carried load does on the way. Liquid sloshing out of the load and of what rides on it, and an
    upright rider tipping over, are this domain's copy of the kernel carry law (kernel_carry false: the law is
    deferred); riders sliding off a rushed tray, a hand-carried stack chattering and shedding its top plates and a
    heaped container shedding its top item are this domain's own rules either way. Falls go through the kernel."""
    w = ctx.w
    rec = w.ent(eid)
    hand_z = kbody.hand_drop_m(w.s)
    # liquid in the held container itself
    if not kernel_carry:
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
                C.drop_item(w, child, hand_z, rid, w.robot.get("pos"), cause="slid_off_tray")
            elif kernel_carry:
                continue
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
                C.drop_item(w, top, hand_z + 0.1, rid, w.robot.get("pos"), cause="slid_off_stack")
    # heaped open containers shed items
    if rec.get("container") and not C.container_closed(rec) and C.num(rec.get("capacity_l")) > 0:
        inside = [c for c in w.children(eid) if w.ent(c).get("relation") == "in"]
        used = sum(C.num(w.ent(c).get("volume_l"), 0.5) for c in inside)
        if used > C.num(rec["capacity_l"]) * 1.0 + 1e-9 and motion > 0.6 and inside:
            last = max(inside, key=lambda c: (C.num(w.ent(c).get("pack_seq"), 0), c))
            C.drop_item(w, last, hand_z, rid, w.robot.get("pos"), cause="fell_out_of_heaped_container")
        if not kernel_carry:
            for child in inside:
                if child not in w.s["entities"] or w.ent(child).get("location") != eid:
                    continue
                amount = C.slosh_spill_l(w.ent(child), motion)
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


REF_PRECISION_MM = 2.0       # a humanoid hand's positioning precision (profiles.json grippers.hand): throw errors scale with it
CATCH_PRECISION_MM = 6.0     # a hand that cannot place itself within this (dark, dirty lens, weak arm) fumbles a catch


def _aim_factor(w, hand):
    """How much worse than a healthy humanoid hand the robot aims now (body.precision_mm: arm health, slippery
    fingers, a dirty lens, darkness, fast speed): 1.0 at best."""
    return max(1.0, kbody.precision_mm(w.s, hand) / REF_PRECISION_MM)


@action("throw", params={"target": "entity", "destination": "target", "force": {"type": "str", "default": "normal", "choices": ["soft", "normal", "hard"]},
                         "cell": "?cell"},
        duration=2.0, energy=0.01, noise=0.1, category="physical", irreversible=True,
        doc="Toss a held item into a container, to a floor cell, or to a person ready to catch it. Accuracy falls with distance "
            "and force (and with a dirty lens, darkness, a weak arm or fast speed); fragile items break on landing; hard throws "
            "at people injure them.")
def throw(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    C.held_or_fail(ctx, eid, "Hold the item first")
    dest = ctx.args["destination"]
    rec = w.ent(eid)
    C.safe_handling(ctx, eid)
    require(C.subtree_mass(w, eid) <= 5.0, "Too heavy to throw safely", "safety")
    force = ctx.args["force"]
    hand = C.grip_hand(w, eid)
    error_per_m = {"soft": 0.04, "normal": 0.07, "hard": 0.12}[force] * _aim_factor(w, hand)
    speed_h = {"soft": 0.2, "normal": 0.6, "hard": 1.5}[force]
    robot = w.robot
    rid = robot["room"]
    rx, ry = (robot["pos"][0] + 0.5) * C.CELL, (robot["pos"][1] + 0.5) * C.CELL
    if dest in w.s.get("people", {}):
        person = w.s["people"][dest]
        require(person.get("location") == rid, "They are not here", "perception")
        soft = rec.get("soft") or C.material(rec) in ("fabric", "foam", "rubber", "plush") and C.num(rec.get("mass_kg")) <= 0.6
        ready = person.get("awake", True) and person.get("ready_to_catch")
        if not ready or force == "hard" or not soft:
            if not soft or force == "hard":
                w.stat("injury")
                C.note_event(w, "hit_person", eid, person=dest)
            cell = C.landing_cell(w, rid, person.get("pos"))
            C.drop_item(w, eid, 1.0 + speed_h, rid, cell, cause="thrown")
            return "The {} was not caught".format(C.name_of(ctx, eid, rec.get("label", eid)))
        release_from_hands(w, eid)
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
    fits = target.get("container") and (not target.get("openable") or target.get("open")) and geometry.fits_inside(C.dims(rec), target.get("inner_size_m"))
    if fits and lateral <= opening + 1e-9:
        release_from_hands(w, eid)
        m = w.mut(eid)
        m.pop("held_hand", None)
        m["location"], m["relation"] = dest, "in"
        w.log_move(eid, "robot", dest)
        C.impact(w, eid, 0.3 + speed_h, 0.8, cause="thrown")
        from . import stacking
        stacking.register_packed(ctx, eid, dest)
        return "Landed in {}".format(dest)
    cell = C.landing_cell(w, rid, target.get("pos") or robot.get("pos"))
    C.drop_item(w, eid, 1.0 + speed_h * 0.5, rid, cell, cause="missed_throw")
    if C.realistic(ctx):
        return "Missed; it landed on the floor"           # how far off is not something the robot measured
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
    ctx.require_arm()
    cell = falling.get("cell") or rec.get("pos") or [0, 0]
    rpos = w.robot.get("pos") or [0, 0]
    reach = min([geometry.chebyshev(cell, rpos)] + [geometry.chebyshev(c, rpos) for c in falling.get("from_cells") or []])
    require(reach <= geometry.REACH_CELLS, "Out of reach; it will land before you get there", "precondition")
    hands = C.free_hands(w)
    require(hands, "No free hand to catch with", "capability")
    hand = ctx.args.get("hand") if ctx.args.get("hand") in hands else hands[0]
    hazards = set(rec.get("hazards") or [])
    heavy = C.subtree_mass(w, eid) > 5.0
    hot = C.num(rec.get("temperature_c"), 21.0) > 60.0 or "hot" in hazards
    m = w.mut(eid)
    m.pop("falling", None)
    if "sharp" in hazards or hot or heavy:
        # the gripper pads are cut or burnt: a gripper fault of that hand (it slips from now on, body.grip_mu)
        body_faults.add_fault(w, "gripper_slip", part=hand, source="manipulation.catch")
        C.mark_damage(w, eid, "caught_dangerously", "caught a sharp/hot/heavy falling object")
        w.stat("safety_violations")
        C.drop_item(w, eid, 0.5, w.robot["room"], cell, cause="fumbled_catch")
        return "Caught it badly: the gripper was damaged and it fell"
    if kbody.precision_mm(w.s, hand) > CATCH_PRECISION_MM + 1e-9:
        # the hand cannot get under it in time (darkness, a dirty lens, a weak arm): it glances off the fingers
        C.drop_item(w, eid, C.num(falling.get("height_m"), 0.9), falling.get("room") or w.robot["room"], cell, cause="fumbled_catch")
        return "Missed the catch; it fell"
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
