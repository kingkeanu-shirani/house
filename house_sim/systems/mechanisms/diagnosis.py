"""Hidden mechanical causes and the evidence a robot can gather about them.

Sticking drawers (hidden _mech.stick): swollen (wood + room humidity > 0.6), dirty_runners,
roller_off_track, overloaded (contents > max_load_kg), screw_head (a protruding screw), item_caught
(something behind the drawer box). Each has a resistance profile felt by a gentle pull
(pull_handle force=gentle) and visual clues (inspect, lit and close). Fixes: lower humidity or wax
the runners (tools' lubricate with wax; oil stains wood), wipe the runners (push_handle part=runners
with a held cloth/brush, or cleaning's wipe/vacuum), re-seat the roller (push_handle lift=true),
take heavy contents out, tighten the screw (turn part=screw with a held screwdriver, or tools'
tighten), remove the caught item (inspect where=behind, pick_up). Reports are checked against
the hidden cause name.
"""
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after
from ..core import subtree_mass
from . import common as C

FEEL = {
    "swollen": "Resistance rises steadily along the whole travel; the wood feels tight in the opening",
    "dirty_runners": "It grinds and catches unevenly the whole way, with a gritty sound",
    "roller_off_track": "It drops a few millimetres and catches hard at the same point each time",
    "overloaded": "Very heavy: the bottom drags on the rail",
    "screw_head": "It catches sharply about a third of the way out with a scraping click",
    "item_caught": "It stops dead after a couple of centimetres: something is jammed behind it",
}
CLUES = {
    "swollen": "the drawer front fits tight in its opening; the room feels humid",
    "dirty_runners": "crumbs and grit on the runners",
    "roller_off_track": "the drawer sits slightly lower on one side",
    "overloaded": "the drawer bottom bows under its load",
    "screw_head": "a screw head stands proud of the side rail",
    "item_caught": "something is visible in the gap behind the drawer",
    "paint_seal": "a painted-over seam where the sash meets the frame",
    "debris": "grit and a dead insect in the sash track",
}


def stick_cause(w, eid):
    """Active sticking cause of a drawer (None when it slides freely)."""
    rec = w.ent(eid)
    h = C.hidden(rec)
    cause = h.get("stick")
    if not cause or h.get("fixed") in ("waxed_" + cause, "cleaned_" + cause):
        return None
    if cause == "swollen":
        room = C.room_of(w.s, eid)
        humid = C.num((w.s["rooms"].get(room) or {}).get("humidity"), 0.45)
        if humid <= 0.6 or h.get("fixed") == "waxed":
            return None
    if cause == "dirty_runners" and h.get("fixed") == "cleaned":
        return None
    if cause == "screw_head" and h.get("fixed") == "tightened":
        return None
    if cause == "overloaded":
        load = subtree_mass(w, eid) - C.num(rec.get("mass_kg"), 2.0)
        if load <= C.num(rec.get("max_load_kg"), 8.0):
            return None
    if cause == "item_caught":
        if not [c for c in w.children(eid) if w.ent(c).get("relation") == "behind"]:
            return None
    return cause


def feel_drawer(ctx, eid):
    w = ctx.w
    cause = stick_cause(w, eid)
    rm, mech = C.mut_mech(w, eid)
    mech["felt_at"] = w.s["time_s"]
    w.sec("facts").setdefault("inspected", {})[eid] = w.s["time_s"]
    if cause is None:
        return "It slides smoothly"
    mech["resistance"] = FEEL[cause]
    return FEEL[cause]


def findings(w, eid, rec, realistic=False):
    """Visual evidence for hidden causes (inspector): [(clue text, cause name)]. In realistic perception the latch
    gap is an eyeball estimate (to 5 mm), not the hidden offset."""
    h = C.hidden(rec)
    out = []
    cause = h.get("stick")
    if cause and stick_cause(w, eid):
        out.append((CLUES.get(cause, ""), cause))
    stuck = h.get("stuck")
    if stuck in CLUES:
        out.append((CLUES[stuck], stuck))
    gap = C.num(h.get("sag_mm")) + C.num(h.get("latch_offset_mm"))
    if gap > C.LATCH_TOLERANCE_MM and not rec.get("latched"):
        shown = C.approx(gap, 5.0) if realistic else gap
        out.append(("the latch bar sits about {:.0f} mm below the catch".format(shown), "sag" if C.num(h.get("sag_mm")) >= C.num(h.get("latch_offset_mm")) else "latch_misaligned"))
    fault = h.get("fault")
    if fault == "strike_misaligned":
        out.append(("shiny scrape marks on the edge of the strike plate", "strike_misaligned"))
    if fault in ("bent_bar",):
        out.append(("the latch bar is bent", "bent_bar"))
    if fault == "missing_screw":
        out.append(("a screw is missing from the latch keeper", "missing_screw"))
    if fault == "broken_spring" or h.get("spring") == "broken":
        out.append(("the hinge spring hangs loose", "broken_spring"))
    if h.get("spring") == "weak":
        out.append(("the closing spring feels slack", "weak_spring"))
    if C.num(h.get("keeper_offset_mm")) > 2.5:
        out.append(("the sash meeting rails are out of line", "keeper_misaligned"))
    if h.get("vacuum") and not h.get("vacuum_broken"):
        out.append(("the lid's safety button is sucked down (vacuum sealed)", "vacuum"))
    if C.num(h.get("strike_offset_mm")) > 3.0 and fault != "strike_misaligned":
        out.append(("the door sits proud of its stop; weatherstrip pushes it out", "strike_offset"))
    return [(text, name) for text, name in out if text]


def clues(w, eid, rec):
    """Visual evidence for hidden causes (inspector), as texts."""
    return [text for text, _ in findings(w, eid, rec)]


def tighten_screw(ctx, eid):
    w = ctx.w
    tool = C.held_tool(ctx, kinds=("screwdriver", "drill_driver"), affordances=("screwdriver", "tighten"))
    require(tool, "Hold a screwdriver", "capability")
    h = C.mut_hidden(w, eid)
    if h.get("stick") == "screw_head":
        h["fixed"] = "tightened"
        return "Drove the proud screw flush"
    return "The screws are already tight"


def clean_runners(ctx, eid, tool_id=None):
    w = ctx.w
    tool = C.held_tool(ctx, tool_id, kinds=("cloth", "rag", "microfiber_cloth", "brush", "scrub_brush", "vacuum", "handheld_vacuum"), affordances=("wipe", "dust", "vacuum"))
    require(tool, "Hold a cloth or brush to clean the runners", "capability")
    rec = w.ent(eid)
    require(rec.get("open"), "Pull the drawer out to reach its runners", "precondition")
    h = C.mut_hidden(w, eid)
    if h.get("stick") == "dirty_runners":
        h["fixed"] = "cleaned"
        return "Wiped the grit off the runners"
    return "The runners were clean already"


def wax_runners(w, eid, lubricant):
    rec = w.ent(eid)
    kind = (lubricant or {}).get("lubricant_kind") or (lubricant or {}).get("tool_kind") or ""
    h = C.mut_hidden(w, eid)
    if "wax" in str(kind) or "paraffin" in str(kind) or "soap" in str(kind):
        if h.get("stick") in ("swollen", "dirty_runners"):
            h["fixed"] = "waxed"
        return True
    if rec.get("material") in ("wood", "oak", "pine", "maple") and ("oil" in str(kind) or "wd40" in str(kind)):
        from ..core import add_soil
        add_soil(w, eid, "stain_oil", 0.3)
        add_soil(w, eid, "dirt", 0.1)
    return False


@after("lubricate", order=80)
def _after_lubricate(ctx, result):
    """tools_vehicles' lubricate on a sticking drawer: wax fixes wood runners, oil stains them."""
    w = ctx.w
    eid = ctx.args.get("target")
    if eid not in w.s["entities"] or C.kind_of(w.ent(eid)) not in ("drawer", "sash_window", "slider_window", "hinged_door", "gate"):
        return
    lub = None
    for key in ("tool", "lubricant", "product"):
        if ctx.args.get(key) in w.s["entities"]:
            lub = w.ent(ctx.args[key])
    wax_runners(w, eid, lub or {})


@after("tighten", order=80)
def _after_tighten(ctx, result):
    w = ctx.w
    eid = ctx.args.get("target")
    if eid in w.s["entities"] and C.hidden(w.ent(eid)).get("stick") == "screw_head":
        h = C.mut_hidden(w, eid)
        h["fixed"] = "tightened"


@after("wipe", order=80)
def _after_wipe(ctx, result):
    w = ctx.w
    eid = ctx.args.get("target")
    if eid in w.s["entities"] and w.ent(eid).get("open") and C.hidden(w.ent(eid)).get("stick") == "dirty_runners":
        h = C.mut_hidden(w, eid)
        h["fixed"] = "cleaned"


@after("vacuum", order=80)
def _after_vacuum(ctx, result):
    _after_wipe(ctx, result)
