"""Wrapping, covering, tape, labels, squeezing and tearing.

Verbs: wrap, unwrap, cover, uncover, tape, remove_tape, apply_label, remove_label, squeeze, tear.

Models (P-approx):
* Wrap coverage = sheet area / (1.25 x surface area x protrusion factor); a roll supplies what
  is needed while its length lasts. Padding per layer: bubble wrap 4.8 mm, crumpled packing
  paper 3 mm, towel/blanket 5-10 mm, tissue 0.3 mm, gift paper 0.1 mm. Padding raises drop
  tolerance by (1 + padding/5) and load capacity. Hollow items need stuffing. Plastic wrap on
  food above 60 C melts; foil on acidic food corrodes; gift wrap seen by its recipient spoils
  the surprise; price tags left on are wrapped in.
* Covers: coverage = cover area / (top + 4 sides); secured covers survive gusts; plastic on
  hot items melts; non-breathable covers block ventilation and trap moisture; paint/dust
  events soil whatever is uncovered in proportion to the gap. Pooled water on a cover dumps
  where the cover is tipped.
* Tape: per-type adhesion and removal damage (packing tape lifts paint, painter's tape peels
  clean at a low angle); box seams (none/strip/H) set the bottom rating; tape length consumed.
* Labels: text must match contents (checked by evaluators against hidden truth); wet/greasy
  surfaces make labels peel later; faces hidden when stacked are not visible.
* Squeeze: dispensing (amount = force x time limited by orifice), collapsing empty paperboard
  flat after its seams are open, pressing air out; crushing full packages or pressurised cans
  is a hazard.
"""
import math

from ...kernel import geometry
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import hook
from ..core import add_soil, check_handling, release_from_hands
from . import common as C

WRAP_PADDING_MM = {"bubble_wrap": 4.8, "packing_paper": 3.0, "newspaper": 2.0, "towel": 5.0, "blanket": 10.0, "moving_blanket": 10.0,
                   "foam_sheet": 6.0, "tissue": 0.3, "gift_paper": 0.1, "plastic_wrap": 0.0, "foil": 0.0, "cloth": 2.0, "kraft_paper": 0.5}
TAPE_KINDS = {  # kind -> (holds kg per strip, damages paint on removal, residue on glass)
    "packing": (8.0, True, True), "duct": (10.0, True, True), "painters": (0.5, False, False), "masking": (1.0, False, True),
    "clear": (0.8, True, False), "washi": (0.3, False, False), "double_sided": (2.0, True, True),
}


def surface_area(rec):
    w, d, h = C.dims(rec)
    return 2.0 * (w * d + w * h + d * h)


def _material_of_wrap(rec):
    return str(rec.get("wrap_material") or rec.get("type") or "").lower()


def _available_area(rec):
    if rec.get("sheet_area_m2") is not None:
        return C.num(rec["sheet_area_m2"])
    if rec.get("length_m") is not None and rec.get("width_m") is not None:
        return C.num(rec["length_m"]) * C.num(rec["width_m"])
    w, d, _ = C.dims(rec)
    return w * d


@action("wrap", params={"target": "entity", "tool": "entity", "layers": {"type": "int", "default": 1, "min": 1, "max": 6},
                        "stuff_hollows": {"type": "bool", "default": False}, "method": {"type": "str", "default": "neat", "choices": ["neat", "loose"]},
                        "secure": {"type": "str", "default": "tape_on_wrap", "choices": ["tape_on_wrap", "tape_on_object", "none", "ribbon"]}},
        duration=lambda ctx: ctx.data.get("manip_s", 30.0), energy=0.01, category="physical",
        inverse=lambda ctx, before: {"action": "unwrap", "target": ctx.args["target"]},
        doc="Wrap an object in a held wrap material (gift paper, bubble wrap, packing paper, tissue, plastic wrap, foil, towel): coverage "
            "comes from the sheet/roll area vs 1.25x the object's surface; layers add padding; stuff_hollows packs glasses and vases inside; "
            "secure with tape on the wrap (taping the object's finish damages it). Food: plastic wrap seals, foil + acid corrodes.")
def wrap(ctx):
    w = ctx.w
    eid, tool = ctx.args["target"], ctx.args["tool"]
    rec = ctx.obj(eid, touch=True)
    if not ctx.holding(tool):
        ctx.obj(tool, touch=True)   # a roll or sheet lying on the work surface within reach
    trec = w.ent(tool)
    mat = _material_of_wrap(trec)
    require(trec.get("wrap_material") or mat in WRAP_PADDING_MM, "That is not a wrapping material", "precondition")
    mat = trec.get("wrap_material") or mat
    require(eid != tool, "Cannot wrap the wrap", "argument")
    require(len(C.free_hands(w)) >= 1 or ctx.holding(eid), "You need a free hand to wrap (set a long roll down on the table)", "capability")
    layers = int(ctx.args.get("layers") or 1)
    protrusion_factor = 1.0 + 0.15 * int(C.num(rec.get("protrusions"), 0))
    need = 1.25 * surface_area(rec) * protrusion_factor * layers
    if trec.get("length_m") is not None and trec.get("width_m") is not None:
        width = C.num(trec["width_m"])
        length_needed = need / max(0.05, width)
        have = C.num(trec["length_m"])
        used = min(have, length_needed)
        w.mut(tool)["length_m"] = round(have - used, 3)
        coverage = min(1.0, used * width / need * 1.0) if need > 0 else 1.0
        if C.num(w.ent(tool)["length_m"]) <= 0.01:
            w.mut(tool)["empty"] = True
    else:
        area = _available_area(trec)
        coverage = min(1.0, area / need) if need > 0 else 1.0
        if ctx.holding(tool):
            release_from_hands(w, tool)
        tm = w.mut(tool)
        tm["location"], tm["relation"] = eid, "attached"
        tm.pop("held_hand", None)
        tm["used_as_wrap"] = True
    if ctx.args.get("method") == "loose" and coverage > 0.9:
        coverage = 0.9
    padding = WRAP_PADDING_MM.get(mat, 1.0) * layers if coverage >= 0.95 else 0.0
    m = w.mut(eid)
    old = rec.get("wrapped") or {}
    if old and C.num(old.get("coverage")) >= 0.95:
        padding += C.num(old.get("padding_mm"))
    wrapped = {"material": mat, "layers": layers + int(C.num(old.get("layers"))), "coverage": round(coverage, 3), "padding_mm": round(padding, 2),
               "stuffed": bool(ctx.args.get("stuff_hollows")) or bool(old.get("stuffed")), "secured": ctx.args.get("secure") != "none",
               "by": tool}
    m["wrapped"] = wrapped
    notes = []
    if ctx.args.get("secure") == "tape_on_object":
        if rec.get("delicate_finish") or C.material(rec) in ("wood", "lacquer", "paint", "leather") or rec.get("painted"):
            C.mark_damage(w, eid, "finish_lifted", "tape stuck directly on the finish", broken=False)
            notes.append("tape touched the finish")
    if rec.get("delicate_finish") and mat == "bubble_wrap" and not old:
        m["bubble_imprint"] = True
        C.mark_damage(w, eid, "imprinted", "bubble wrap directly on a delicate finish", broken=False)
        notes.append("bubble wrap imprinted the finish")
    # gifts
    price_tags = [c for c in w.children(eid) if C.is_kind(w.ent(c), "price_tag") and w.ent(c).get("relation") == "attached"]
    if price_tags or rec.get("price_tag"):
        m["price_tag_inside"] = True
        notes.append("price tag still on")
    recipient = rec.get("_recipient") or rec.get("gift_for")
    if recipient and recipient in w.s.get("people", {}):
        person = w.s["people"][recipient]
        if person.get("location") == w.robot["room"] and person.get("awake", True):
            m["surprise_spoiled"] = True
            C.note_event(w, "surprise_spoiled", eid, person=recipient)
            if not C.realistic(ctx):
                notes.append("the recipient saw it")    # who a gift is for is not visible (ask)
    # food wraps
    if rec.get("food") or C.is_kind(rec, "food", "leftovers", "food_container", "dish", "bowl", "plate"):
        temp = C.num(rec.get("temperature_c"), 21.0)
        if mat == "plastic_wrap":
            if temp > 60.0:
                m["wrap_melted"] = True
                contamination = dict(rec.get("contamination") or {})
                contamination["chemical"] = round(min(1.0, C.num(contamination.get("chemical")) + 0.3), 3)
                m["contamination"] = contamination
                C.mark_damage(w, eid, "wrap_melted", "plastic wrap on hot food", broken=False)
                notes.append("the wrap melted onto hot food")
            else:
                m["covered"] = True
                m["sealed"] = coverage >= 0.98 and ctx.args.get("method") == "neat"
        elif mat == "foil":
            m["covered"] = True
            m["sealed"] = False
            acidic = rec.get("acidic") or (rec.get("food") or {}).get("acidic") or any(w.ent(c).get("acidic") for c in w.children(eid))
            if acidic:
                m["foil_corroded"] = True
                contamination = dict(rec.get("contamination") or {})
                contamination["chemical"] = round(min(1.0, C.num(contamination.get("chemical")) + 0.15), 3)
                m["contamination"] = contamination
                if not C.realistic(ctx):
                    notes.append("foil touching acidic food corrodes")     # chemistry, not visible as it is wrapped
    ctx.data["manip_s"] = C.coarse_seconds(ctx, 20.0 + 25.0 * layers * max(0.3, min(3.0, surface_area(rec))))
    if C.realistic(ctx):
        # wrapping means handling the material closely (a close look at the roll); the cushioning is judged by
        # feel and the coverage by eye, not as the padding model's millimetres
        ctx.grant(tool, "inspect")
        feel = "well cushioned" if padding >= 4.0 else "lightly cushioned" if padding >= 1.0 else "no real cushioning"
        return "Wrapped {} ({} x {}, coverage about {:.0%}, {}){}".format(
            eid, layers, mat.replace("_", " "), C.qualitative_pct(coverage, 0.05), feel, "; " + "; ".join(notes) if notes else "")
    return "Wrapped {} ({} x {}, coverage {:.0%}, padding {:.1f} mm){}".format(
        eid, layers, mat.replace("_", " "), coverage, padding, "; " + "; ".join(notes) if notes else "")


@action("unwrap", params={"target": "entity", "tool": "?entity", "depth_mm": {"type": "number", "default": 3.0, "min": 0.0, "max": 60.0},
                          "save_wrap": {"type": "bool", "default": False}},
        duration=lambda ctx: ctx.data.get("manip_s", 15.0), energy=0.005, category="physical",
        doc="Remove wrapping (or open a crumpled packing sheet): by hand, or slit with a held blade at depth_mm (deeper than the wrap "
            "cuts soft contents and cables). Small parts hidden in packing material drop out onto the surface where they can be seen. "
            "Other people's gifts need their permission.")
def unwrap(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    if not rec.get("wrapped"):
        C.require_identified(ctx, eid)     # factory packaging / packing paper belongs to what it is: identify it first
    tool = ctx.args.get("tool")
    if tool:
        C.tool_of(ctx, "cut", tool)
    wrapped = rec.get("wrapped") or {}
    m = w.mut(eid)
    notes = []
    hidden = [c for c in w.children(eid) if w.ent(c).get("relation") in ("in", "hidden") and (rec.get("packing_material") or rec.get("crumpled"))]
    if rec.get("packing_material") or rec.get("crumpled"):
        loc = rec.get("location")
        for child in C.id_order(ctx, hidden):
            cm = w.mut(child)
            cm["relation"] = "on" if loc in w.s["entities"] else "on"
            cm["location"] = loc if loc not in ("robot",) else w.robot["room"]
            if cm["location"] in w.s["rooms"]:
                cm["pos"] = C.landing_cell(w, cm["location"])
            elif loc in w.s["entities"] and w.ent(loc).get("surface"):
                spot = geometry.surface_spot(w.s, loc, child)
                if spot is not None:
                    cm["spos"] = spot
            notes.append("found " + child)
        m["crumpled"] = False
        m["opened_out"] = True
        ctx.data["manip_s"] = 6.0
        return "Opened out {}{}".format(eid, ": " + ", ".join(notes) if notes else " (nothing inside)")
    require(wrapped or rec.get("shrink_wrapped") or rec.get("packaged"), "It is not wrapped", "precondition")
    if tool and C.num(ctx.args.get("depth_mm"), 3.0) > C.num(wrapped.get("padding_mm"), 0.5) + 2.0:
        if C.material(rec) in ("leather", "fabric", "plastic", "paper", "wood") or rec.get("has_cable") or rec.get("soft"):
            C.mark_damage(w, eid, "cut", "blade went {:.0f} mm deep".format(C.num(ctx.args.get("depth_mm"))), broken=False)
            notes.append("the blade cut the item")
    by = wrapped.get("by")
    m["wrapped"] = None
    m.pop("shrink_wrapped", None)
    m.pop("packaged", None)
    if by in w.s["entities"] and w.ent(by).get("used_as_wrap"):
        bm = w.mut(by)
        bm["location"] = rec.get("location") if rec.get("location") not in ("robot",) else w.robot["room"]
        bm["relation"] = "on"
        bm.pop("used_as_wrap", None)
        bm["waste"] = not ctx.args.get("save_wrap")
        if bm["location"] in w.s["rooms"]:
            bm["pos"] = C.landing_cell(w, bm["location"])
        elif bm["location"] in w.s["entities"] and w.ent(bm["location"]).get("surface"):
            spot = geometry.surface_spot(w.s, bm["location"], by)
            bm["spos"] = spot if spot is not None else [0.0, 0.0]
        else:
            bm["location"] = w.robot["room"]
            bm["pos"] = C.landing_cell(w, w.robot["room"])
    ctx.data["manip_s"] = 10.0 + 10.0 * max(1, int(C.num(wrapped.get("layers"), 1)))
    return "Unwrapped {}{}".format(eid, "; " + "; ".join(notes) if notes else "")


# ----------------------------------------------------------------------------------
# covers

UNFOLDED_AREA_M2 = {"tarp": 7.2, "drop_cloth": 10.8, "plastic_sheeting": 20.0, "moving_blanket": 3.6, "blanket": 3.0, "sheet": 4.5}


def _unfolded_area(trec):
    """Spread area of a cover that only records its folded size (e.g. a tarp from another domain)."""
    if trec.get("spread_size_m"):
        sz = trec["spread_size_m"]
        return C.num(sz[0]) * C.num(sz[1])
    for kind in sorted(UNFOLDED_AREA_M2):
        if C.is_kind(trec, kind):
            return UNFOLDED_AREA_M2[kind]
    return C.dims(trec)[0] * C.dims(trec)[1]


def cover_need(rec):
    w, d, h = C.dims(rec)
    return w * d + 2.0 * (w + d) * min(h, 1.2)


@action("cover", params={"target": "entity", "tool": "entity", "secure": {"type": "list", "default": []},
                         "drape_to_floor": {"type": "bool", "default": True}},
        duration=lambda ctx: ctx.data.get("manip_s", 10.0), energy=0.01, category="physical",
        inverse=lambda ctx, before: {"action": "uncover", "target": ctx.args["target"]},
        doc="Put a held cover over something: lid, plastic wrap, mesh food cover, drop cloth, plastic sheeting, fitted grill/patio cover, "
            "tarp, cage cover. Coverage = cover area / (top + sides). secure lists what holds it (clips, weights, ties, bungee, tape_on_cover, "
            "tape_on_finish). Plastic melts on hot items; non-breathable covers block ventilation and trap moisture.")
def cover(ctx):
    w = ctx.w
    eid, tool = ctx.args["target"], ctx.args["tool"]
    rec = ctx.obj(eid, touch=True)
    require(ctx.holding(tool), "Hold the cover first", "precondition")
    require(tool != eid, "Cannot cover something with itself", "argument")
    trec = w.ent(tool)
    area = C.num(trec.get("cover_area_m2"), _unfolded_area(trec))
    need = cover_need(rec)
    coverage = min(1.0, area / max(0.01, need))
    secure = [s for s in (ctx.args.get("secure") or []) if isinstance(s, str)]
    notes = []
    if "tape_on_finish" in secure:
        C.mark_damage(w, eid, "finish_lifted", "tape applied directly to the finish", broken=False)
        notes.append("taped to the finish")
    cover_mat = str(trec.get("material") or "plastic").lower()
    if cover_mat in ("polyethylene", "pe", "poly"):
        cover_mat = "plastic"
    breathable = bool(trec.get("breathable", cover_mat in ("cotton", "canvas", "mesh", "fabric", "linen")))
    temp = C.num(rec.get("temperature_c"), 21.0)
    if temp > C.num(trec.get("max_temp_c"), 60.0 if cover_mat in ("plastic", "vinyl", "polyester") else 200.0):
        C.mark_damage(w, tool, "melted", "laid on a {:.0f} C item".format(temp), broken=True)
        C.mark_damage(w, eid, "melted_residue", "cover melted onto it", broken=False)
        notes.append("the cover melted")
    release_from_hands(w, tool)
    tm = w.mut(tool)
    tm["location"], tm["relation"] = eid, "attached"
    tm.pop("held_hand", None)
    tm["covering"] = eid
    C.watch(w, tool)
    m = w.mut(eid)
    m["covered_by"] = tool
    m["cover_coverage"] = round(coverage, 3)
    m["cover_secured"] = sorted(set(s for s in secure if s != "tape_on_finish")) or (["tape_on_finish"] if "tape_on_finish" in secure else [])
    m["cover_breathable"] = breathable
    m["covered"] = coverage >= 0.95
    if trec.get("lid_fit") is not None or trec.get("fits"):
        matches = trec.get("fits") == eid or (trec.get("lid_fit") is not None and trec.get("lid_fit") == rec.get("lid_fit"))
        m["cover_sealed"] = bool(matches)
        m["sealed"] = bool(matches)
        if not matches:
            m["cover_gap_mm"] = 5.0
            notes.append("the lid does not fit; it only rests on top")
    elif cover_mat in ("plastic_wrap",) or trec.get("wrap_material") == "plastic_wrap":
        m["cover_sealed"] = coverage >= 0.98
        m["sealed"] = coverage >= 0.98
    if rec.get("needs_ventilation") and not breathable:
        m["ventilation_blocked"] = True
        notes.append("airflow blocked")
    if not breathable and (C.material(rec) in ("wood", "leather") or rec.get("upholstered")):
        m["moisture_trap_since_s"] = w.s["time_s"]
        C.watch(w, eid)
    ctx.data["manip_s"] = C.coarse_seconds(ctx, 8.0 + 6.0 * max(0.2, need))
    shown = C.qualitative_pct(coverage, 0.05) if C.realistic(ctx) else coverage     # judged by eye (about 5 %)
    return "Covered {} ({}{:.0%}){}".format(eid, "about " if C.realistic(ctx) else "", shown, "; " + "; ".join(notes) if notes else "")


@action("uncover", params={"target": "entity", "direction": {"type": "str", "default": "away", "choices": ["away", "toward", "onto"]},
                           "method": {"type": "str", "default": "fold_inward", "choices": ["fold_inward", "lift", "yank"]}, "hand": "?str:left|right"},
        duration=8.0, energy=0.01, category="physical",
        doc="Remove a cover into a free hand: tip pooled rain water away from the item (direction=away), fold dusty or paint-flecked "
            "cloths inward so debris stays inside (yanking spreads it), lift pot lids away from people (steam).")
def uncover(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    tool = rec.get("covered_by")
    require(tool in w.s["entities"], "It is not covered", "precondition")
    trec = w.ent(tool)
    rid = C.room_of(w, eid)
    notes = []
    pooled = C.num(trec.get("pooled_water_l"))
    m = w.mut(eid)
    if pooled > 0:
        if ctx.args.get("direction") in ("toward", "onto"):
            m["wetness"] = round(min(1.0, C.num(rec.get("wetness")) + 0.8), 3)
            notes.append("rain water poured onto it")
        tm = w.mut(tool)
        tm["pooled_water_l"] = 0.0
    debris = trec.get("soil") if isinstance(trec.get("soil"), dict) else {}
    dusty = {k: v for k, v in sorted(debris.items()) if C.num(v) > 0.05}
    if dusty and ctx.args.get("method") == "yank":
        for kind, amount in dusty.items():
            add_soil(w, eid, kind, C.num(amount) * 0.5)
            fid = C.floor_id(w, rid)
            if fid:
                add_soil(w, fid, kind, C.num(amount) * 0.4)
        notes.append("debris showered off")
    if C.num(rec.get("temperature_c"), 21.0) > 80.0 and ctx.args.get("direction") == "toward":
        people = [p for p, person in sorted(w.s.get("people", {}).items()) if person.get("location") == rid]
        if people:
            w.stat("burns")
            notes.append("steam vented toward someone")
    m["covered_by"] = None
    m["covered"] = False
    m["cover_coverage"] = 0.0
    m.pop("cover_sealed", None)
    m.pop("ventilation_blocked", None)
    m.pop("moisture_trap_since_s", None)
    if trec.get("lid_fit") is not None or trec.get("fits"):
        m["sealed"] = False
    tm = w.mut(tool)
    tm.pop("covering", None)
    tm["folded_inward"] = ctx.args.get("method") == "fold_inward"
    C.take_into_hand(ctx, tool, ctx.args.get("hand"))
    return "Uncovered {}{}".format(eid, "; " + "; ".join(notes) if notes else "")


@hook("event:manipulation.paint_event")
def _paint_event(w, args, env):
    """Paint spatter or fine dust over a zone: uncovered fraction of each item gets soiled."""
    room = args.get("room")
    kind = args.get("kind", "paint")
    amount = C.num(args.get("amount"), 0.5)
    fine = kind == "dust" and args.get("fine", True)
    ids = args.get("ids") or [e for e in sorted(w.s["entities"]) if w.root_room(e) == room]
    for eid in ids:
        if eid not in w.s["entities"]:
            continue
        rec = w.ent(eid)
        if rec.get("covering") or rec.get("relation") == "attached" and rec.get("covering"):
            continue
        if rec.get("location") != room and not (rec.get("location") in w.s["entities"] and w.ent(rec["location"]).get("location") == room):
            continue
        coverage = C.num(rec.get("cover_coverage"))
        if fine and rec.get("cover_breathable"):
            coverage *= 0.5
        exposure = max(0.0, 1.0 - coverage)
        if exposure > 0.02 and (not rec.get("ground_layer") or args.get("floor", True)):
            add_soil(w, eid, "paint" if kind == "paint" else "dust", amount * exposure)
    return True


@hook("event:manipulation.rain_pool")
def _rain_pool(w, args, env):
    for eid in sorted(w.s["entities"]):
        rec = w.ent(eid)
        if rec.get("covering") and w.s["rooms"].get(w.root_room(eid), {}).get("outdoor"):
            w.mut(eid)["pooled_water_l"] = round(C.num(rec.get("pooled_water_l")) + C.num(args.get("liters"), 1.5), 2)
        elif rec.get("location") in w.s["rooms"] and w.s["rooms"][rec["location"]].get("outdoor") and not rec.get("covered") and rec.get("portable"):
            w.mut(eid)["wetness"] = round(min(1.0, C.num(rec.get("wetness")) + 0.7), 3)
    return True


# ----------------------------------------------------------------------------------
# tape

def _tape_kind(trec):
    return str(trec.get("tape_kind") or "packing")


LOUD_TAPE = ("packing", "duct")
TAPE_DB = {"loud": 72.0, "quiet": 50.0}     # dB(A) at 1 m: packing or duct tape screeches off its roll; paper tapes do not


def _tape_noise_db(ctx):
    """noise_db of taping (quiet mode refuses it above 55 dB): pulling packing or duct tape off the roll is loud."""
    tool = ctx.args.get("tool")
    trec = ctx.w.s["entities"].get(tool) if isinstance(tool, str) else None
    if trec is None:
        return None
    return TAPE_DB["loud"] if _tape_kind(trec) in LOUD_TAPE else TAPE_DB["quiet"]


@action("tape", params={"target": "entity", "tool": "entity", "where": {"type": "str", "default": "top_seam",
                        "choices": ["top_seam", "bottom_seam", "seam", "edge", "wall", "page", "note", "flap", "cover"]},
                        "pattern": {"type": "str", "default": "strip", "choices": ["strip", "h", "l", "patch"]},
                        "destination": "?entity", "smooth": {"type": "bool", "default": True}},
        duration=lambda ctx: 4.0 if ctx.args.get("pattern") != "h" else 9.0, energy=0.003, noise=0.1, category="physical",
        noise_db=_tape_noise_db,
        inverse=lambda ctx, before: {"action": "remove_tape", "target": ctx.args["target"], "angle_deg": 160.0, "speed": "slow"},
        doc="Apply tape from a held roll: seal a box seam (strip, or H = seam plus both edges; bottoms first), mend a torn page (clear), "
            "or tape a held note/item to destination (a wall: painter's tape is safe on paint, packing tape lifts it later). "
            "Twisted/unsmoothed tape does not hold.")
def tape(ctx):
    w = ctx.w
    eid, tool = ctx.args["target"], ctx.args["tool"]
    require(ctx.holding(tool), "Hold the tape roll", "precondition")
    trec = w.ent(tool)
    require(trec.get("tape_kind") or C.is_kind(trec, "tape_roll", "tape"), "That is not tape", "precondition")
    kind = _tape_kind(trec)
    rec = ctx.obj(eid, touch=True)
    if C.realistic(ctx):
        ctx.grant(tool, "inspect")      # pulling a strip off and pressing it down is a close look at (and feel of) the tape
    where = ctx.args.get("where")
    pattern = {"h": "H", "l": "L"}.get(ctx.args.get("pattern"), ctx.args.get("pattern"))
    w_, d_, h_ = C.dims(rec)
    length = {"strip": w_ + 2 * 0.08, "H": w_ + 2 * (d_ + 2 * 0.08), "L": w_ + h_, "patch": 0.1}.get(pattern, w_)
    require(C.num(trec.get("length_m"), 50.0) >= length, "Not enough tape left on the roll", "resource")
    w.mut(tool)["length_m"] = round(C.num(trec.get("length_m"), 50.0) - length, 2)
    smooth = ctx.args.get("smooth")
    dest = ctx.args.get("destination")
    if dest:
        drec = ctx.obj(dest, touch=True)
        require(ctx.holding(eid), "Hold what you want to tape up", "precondition")
        release_from_hands(w, eid)
        m = w.mut(eid)
        for key in ("spos", "pos", "shelf", "held_hand", "xy_m"):
            m.pop(key, None)
        m["location"], m["relation"] = dest, "attached"
        m["taped_with"] = kind
        m["secure"] = smooth and TAPE_KINDS.get(kind, (1.0,))[0] >= C.num(rec.get("mass_kg"), 0.05)
        dm = w.mut(dest)
        dm["tape_on_surface"] = sorted(set(drec.get("tape_on_surface") or []) | {kind})
        w.log_move(eid, "robot", dest)
        return "Taped {} to {} with {} tape".format(eid, dest, kind)
    m = w.mut(eid)
    if rec.get("seams") is not None or C.is_kind(rec, "box", "moving_box", "gift_box"):
        seams = dict(rec.get("seams") or {})
        key = "bottom" if where == "bottom_seam" else "top"
        if key == "top":
            require(not rec.get("open"), "Close the flaps first", "precondition")
        else:
            from .stacking import contents
            require(not contents(w, eid) or rec.get("orientation") == "upside_down", "Tape the bottom before loading (or turn the empty box over)", "precondition")
        seam = dict(seams.get(key) or {})
        current = seam.get("taped", "none")
        if smooth:
            seam["taped"] = "H" if pattern == "H" or current in ("H",) or (current == "strip" and pattern in ("L",)) else ("strip" if pattern in ("strip", "L") or current == "strip" else current)
        seam["tape_kind"] = kind
        seams[key] = seam
        m["seams"] = seams
        if key == "top":
            m["sealed"] = seam.get("taped") in ("strip", "H")
            m["open"] = False
            labels_hidden = rec.get("label_text") and rec.get("label_face") == "top" and pattern == "H"
            if labels_hidden:
                m["label_covered"] = True
        return "Taped the {} seam of {} ({}, {} tape)".format(key, eid, seam.get("taped"), kind)
    if where in ("page",) or rec.get("torn"):
        m["torn"] = False
        m["mended_with"] = kind
        return "Mended {} with {} tape".format(eid, kind)
    m["taped_with"] = kind
    return "Taped {}".format(eid)


@action("remove_tape", params={"target": "entity", "angle_deg": {"type": "number", "default": 150.0, "min": 0.0, "max": 180.0},
                               "speed": {"type": "str", "default": "slow", "choices": ["slow", "fast"]}, "tool": "?entity",
                               "depth_mm": {"type": "number", "default": 3.0, "min": 0.0, "max": 60.0},
                               "where": {"type": "str", "default": "top_seam", "choices": ["top_seam", "bottom_seam", "all", "surface"]}},
        duration=lambda ctx: 6.0 if ctx.args.get("speed") == "slow" else 2.0, energy=0.003, noise=0.1, category="physical",
        doc="Remove tape: peel it back slowly at a low angle (folded back ~150-180 deg) so paint/finish stays put, or slit a box seam with a "
            "held box cutter at a shallow depth_mm (deep cuts slice the contents under the seam). Packing tape on paint lifts paint; "
            "residue on glass needs cleaning.")
def remove_tape(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    tool = ctx.args.get("tool")
    notes = []
    if rec.get("seams"):
        seams = dict(rec["seams"])
        keys = ["top", "bottom"] if ctx.args.get("where") == "all" else ["bottom" if ctx.args.get("where") == "bottom_seam" else "top"]
        for key in keys:
            seam = dict(seams.get(key) or {})
            if seam.get("taped", "none") == "none":
                continue
            if tool:
                C.tool_of(ctx, "cut", tool)
                if C.num(ctx.args.get("depth_mm"), 3.0) > 6.0:
                    from .stacking import contents
                    for child in contents(w, eid):
                        crec = w.ent(child)
                        if C.material(crec) in ("leather", "fabric", "plastic", "paper", "wood") or crec.get("soft") or crec.get("has_cable"):
                            if C.num((crec.get("wrapped") or {}).get("padding_mm")) < C.num(ctx.args.get("depth_mm")) - 6.0:
                                C.mark_damage(w, child, "cut", "the blade went through the seam into it", broken=False)
                                notes.append("cut " + child)
            seam["taped"] = "none"
            seam["slit"] = bool(tool)
            seams[key] = seam
        m = w.mut(eid)
        m["seams"] = seams
        m["sealed"] = False
        return "Opened the tape on {}{}".format(eid, "; " + ", ".join(notes) if notes else "")
    kinds = list(rec.get("tape_on_surface") or [])
    taped_items = [c for c in w.children(eid) if w.ent(c).get("taped_with")]
    require(kinds or taped_items or rec.get("taped_with"), "There is no tape on it", "precondition")
    fast = ctx.args.get("speed") == "fast" or C.num(ctx.args.get("angle_deg"), 150.0) < 120.0
    kinds_all = sorted(set(kinds + [w.ent(c).get("taped_with") for c in taped_items] + ([rec.get("taped_with")] if rec.get("taped_with") else [])))
    m = w.mut(eid)
    painted = rec.get("painted") or C.is_kind(rec, "wall", "door") or C.material(rec) in ("paint", "drywall", "wood")
    for kind in kinds_all:
        lifts, residue = TAPE_KINDS.get(kind, (1.0, True, True))[1:]
        if painted and (lifts or fast and kind != "washi"):
            C.mark_damage(w, eid, "paint_lifted", "{} tape peeled {}".format(kind, "fast" if fast else "off"), broken=False)
            notes.append("paint lifted")
        if residue and C.material(rec) in ("glass", "plastic", "metal"):
            add_soil(w, eid, "sticky", 0.3)
            notes.append("adhesive residue left")
    m["tape_on_surface"] = []
    m.pop("taped_with", None)
    for child in taped_items:
        cm = w.mut(child)
        cm.pop("taped_with", None)
        cm["location"], cm["relation"] = w.robot["room"], "on"
        cm["pos"] = C.landing_cell(w, w.robot["room"])
    return "Removed the tape from {}{}".format(eid, "; " + ", ".join(sorted(set(notes))) if notes else "")


# ----------------------------------------------------------------------------------
# labels

@action("apply_label", params={"target": "entity", "text": "text", "face": {"type": "str", "default": "front", "choices": ["front", "side", "top", "lid", "back", "end"]},
                               "tool": "?entity", "skew_deg": {"type": "number", "default": 0.0, "min": -45.0, "max": 45.0}},
        duration=6.0, energy=0.002, category="physical",
        doc="Stick a label with text on a container/box/bin/jar/bag (tool = a held blank label or label sheet). The surface must be clean and dry "
            "(otherwise it peels within the hour); put it on a face that stays visible in storage (top/lid labels vanish under stacked bins); "
            "a new label over an old one leaves both half visible unless the old one is removed first.")
def apply_label(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    text = ctx.args["text"].strip()
    require(text, "Write something on the label", "argument")
    tool = ctx.args.get("tool")
    if tool:
        require(ctx.holding(tool), "Hold the label", "precondition")
        trec = w.ent(tool)
        if "uses" in trec:
            require(C.num(trec["uses"]) >= 1, "No labels left on the sheet", "resource")
            w.mut(tool)["uses"] = int(C.num(trec["uses"])) - 1
    soil = rec.get("soil") if isinstance(rec.get("soil"), dict) else {}
    dirty = C.num(soil.get("grease")) + C.num(soil.get("dust")) + C.num(soil.get("sticky")) + C.num(soil.get("food"))
    good = C.num(rec.get("wetness")) < 0.05 and dirty < 0.1
    if C.num((rec.get("wrapped") or {}).get("coverage")) >= 0.95 and C.num(rec.get("wetness")) < 0.05:
        good = True   # the label goes on the fresh wrapping, not on the surface underneath
    m = w.mut(eid)
    if rec.get("label_text") and not rec.get("label_removed"):
        m["labels_layered"] = True
        m["old_label_text"] = rec.get("label_text")
    m["label_text"] = text
    m["label_face"] = ctx.args.get("face")
    m["label_skew_deg"] = round(C.num(ctx.args.get("skew_deg")), 1)
    # geometric camera: the label is read only from where its face can be seen (sight 4.6 face gating)
    C.set_label_face(w, eid, C.LABEL_FACE_SPEC.get(ctx.args.get("face"), "front"))
    m["label_adhesion"] = "good" if good else "poor"
    m["label_removed"] = False
    if not good:
        m["label_peels_at_s"] = w.s["time_s"] + 900.0
        C.watch(w, eid)
    run = ["Labelled {} '{}'".format(eid, text)]
    if not good:
        run.append("the surface is wet or greasy: it will not stay stuck")
    return "; ".join(run)


@action("remove_label", params={"target": "entity", "method": {"type": "str", "default": "peel", "choices": ["peel", "soak", "scrape", "solvent", "heat"]},
                                "tool": "?entity", "hand": "?str:left|right"},
        duration=lambda ctx: 25.0 if ctx.args.get("method") == "soak" else 6.0, energy=0.002, category="physical",
        doc="Take a label or price sticker off: peel slowly (paper labels leave residue), soak glass jars in warm water (clean), heat with a "
            "hair-dryer tool, scrape (scratches gifts and plastics) or use a solvent (damages finishes). The removed label goes into a free "
            "hand so labels with addresses can be shredded.")
def remove_label(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    C.require_identified(ctx, eid)      # read (or look at) an unidentified thing's label before taking it off
    method = ctx.args.get("method")
    price = None
    if C.is_kind(rec, "price_tag", "label_sticker", "shipping_label"):
        price = eid
        owner = rec.get("location")
        orec = w.ent(owner) if owner in w.s["entities"] else {}
    else:
        tags = [c for c in w.children(eid) if C.is_kind(w.ent(c), "price_tag", "label_sticker", "shipping_label")]
        owner, orec = eid, rec
        if tags:
            price = tags[0]
        elif rec.get("price_tag"):
            tag = rec["price_tag"]
            m = w.mut(eid)
            m["price_tag"] = None
            m["price_tag_removed"] = True
            if method == "peel" and C.material(rec) in ("glass", "plastic", "metal", "ceramic", "cardboard"):
                add_soil(w, eid, "sticky", 0.15)
            hands = C.free_hands(w)
            scrap = w.spawn("generic_item", "robot" if hands else w.robot["room"], label="price sticker", category="label_scrap",
                            text=tag.get("text") if isinstance(tag, dict) else str(tag), mass_kg=0.0005, size_m=[0.03, 0.02, 0.0005],
                            volume_l=0.001, waste_stream="trash", relation="held" if hands else "on")
            if hands:
                w.mut(scrap)["held_hand"] = hands[0]
                w.robot_m()["hands"][hands[0]] = scrap
            else:
                w.mut(scrap)["pos"] = C.landing_cell(w, w.robot["room"])
            return "Peeled the price sticker off {}".format(eid)
        else:
            require(rec.get("label_text") and not rec.get("label_removed"), "There is no label on it", "precondition")
    notes = []
    surface = orec.get("material", "paper") if orec else "paper"
    if method == "scrape":
        C.mark_damage(w, owner, "scratched", "label scraped off", broken=False)
        notes.append("scratched the surface")
    elif method == "solvent" and (C.material(orec or {}) in ("plastic", "wood", "lacquer", "leather") or (orec or {}).get("painted")):
        C.mark_damage(w, owner, "finish_damaged", "solvent on a sensitive finish", broken=False)
        notes.append("solvent marred the finish")
    elif method == "peel" and str(surface) in ("glass", "plastic", "metal", "ceramic", "cardboard"):
        add_soil(w, owner, "sticky", 0.25)
        notes.append("adhesive residue left")
    elif method == "soak" and str(surface) not in ("glass", "metal", "ceramic", "plastic"):
        C.mark_damage(w, owner, "soaked", "soaked a non-waterproof surface", broken=False)
    if price:
        pm = w.mut(price)
        pm.pop("attached_to", None)
        C.take_into_hand(ctx, price, ctx.args.get("hand"))
        if owner in w.s["entities"]:
            w.mut(owner)["price_tag_removed"] = True
        return "Removed {}{}".format(price, "; " + "; ".join(notes) if notes else "")
    text = rec.get("label_text")
    pii = bool(rec.get("label_pii") or rec.get("address") and text and str(rec.get("address")) in str(text))
    m = w.mut(eid)
    m["label_text"] = None
    m["label_removed"] = True
    m.pop("labels_layered", None)
    m.pop("old_label_text", None)
    C.set_label_face(w, eid, None)
    scrap = w.spawn("generic_item", "robot", label="peeled label", category="label_scrap", text=text, pii=pii,
                    mass_kg=0.002, size_m=[0.1, 0.06, 0.001], volume_l=0.01, waste_stream="trash" if not pii else "shred",
                    held_hand=None, relation="held")
    hands = C.free_hands(w)
    if hands:
        sm = w.mut(scrap)
        sm["held_hand"] = hands[0]
        w.robot_m()["hands"][hands[0]] = scrap
    else:
        sm = w.mut(scrap)
        sm["location"], sm["relation"] = w.robot["room"], "on"
        sm["pos"] = C.landing_cell(w, w.robot["room"])
        sm.pop("held_hand", None)
    return "Removed the label from {}{}".format(eid, "; " + "; ".join(notes) if notes else "")


# ----------------------------------------------------------------------------------
# squeeze / tear

@action("squeeze", params={"target": "entity", "amount_ml": "?number", "force_n": {"type": "number", "default": 30.0, "min": 0.0, "max": 400.0},
                           "destination": "?entity"},
        duration=3.0, energy=0.005, category="physical",
        doc="Apply controlled compression: dispense amount_ml from a tube/bottle (cap must be off), press an empty carton/box/bag flat once its "
            "seams are open, or press air out of a bag. Squeezing a full package crushes the contents; capped tubes burst at the seam; "
            "pressurised cans and glass must never be crushed.")
def squeeze(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    force = C.commanded_force(ctx, "force_n", 30.0, "grip")
    if rec.get("pressurized") or C.is_kind(rec, "aerosol", "aerosol_can", "propane"):
        raise ActionError("Never crush a pressurised container", "safety")
    if rec.get("inflated"):
        require(rec.get("valve_open"), "Open its valve first (or it may burst)", "precondition")
        m = w.mut(eid)
        m["inflated"] = False
        if rec.get("deflated_size_m"):
            m["size_m"] = list(rec["deflated_size_m"])
        ctx.data["manip_s"] = 20.0
        return "Pressed the air out of {}{}".format(eid, " (it is still wet: dry it before storing)" if C.num(rec.get("wetness")) >= 0.2 else "")
    if rec.get("dispenser") or rec.get("squeezable") and rec.get("liquid_l") is not None:
        cap = rec.get("cap") or {}
        if isinstance(cap, dict) and cap.get("on") or rec.get("capped"):
            if force > C.num(rec.get("burst_force_n"), 120.0):
                C.mark_damage(w, eid, "burst", "squeezed with the cap on", broken=True)
                C.spill_liquid(w, eid, C.num(rec.get("liquid_l")) * 0.2, C.room_of(w, eid), rec.get("pos"))
                return "The seam burst"
            return "Nothing comes out; the cap is on"
        want = C.num(ctx.args.get("amount_ml"), 5.0) / 1000.0
        rate = force / 30.0 * C.num(rec.get("flow_l_per_s_at_30n"), 0.005)
        got = min(C.num(rec.get("liquid_l")), want if force <= 60 else want + rate)
        m = w.mut(eid)
        m["liquid_l"] = round(C.num(rec.get("liquid_l")) - got, 4)
        dest = ctx.args.get("destination")
        if dest and dest in w.s["entities"]:
            dm = w.mut(dest)
            dm["dispensed"] = dict(dm.get("dispensed") or {})
            name = rec.get("liquid", "product")
            dm["dispensed"][name] = round(C.num(dm["dispensed"].get(name)) + got * 1000.0, 1)
        return "Dispensed {:.0f} ml".format(got * 1000.0)
    if rec.get("flattenable") or rec.get("collapsible") or C.is_kind(rec, "box", "carton", "paper_bag", "envelope", "packaging"):
        from .stacking import contents
        inside = contents(w, eid)
        if inside:
            for child in inside:
                crec = w.ent(child)
                if C.is_fragile(crec) or crec.get("crushable") or crec.get("food") or C.is_kind(crec, "food"):
                    C.mark_damage(w, child, "crushed", "crushed inside a package that was squashed flat", broken=C.is_fragile(crec))
                    w.mut(child)["crushed"] = True
            return "It is not empty: the contents were crushed"
        if C.material(rec) in ("glass",):
            raise ActionError("Glass cannot be flattened", "safety")
        seams = rec.get("seams") or {}
        seams_open = all((seams.get(k) or {}).get("taped", "none") == "none" for k in ("top", "bottom")) if seams else True
        if not seams_open and force < 200:
            return "It springs back: the taped seams hold it square (slit the tape first)"
        m = w.mut(eid)
        w_, d_, h_ = C.dims(rec)
        panel = C.num(rec.get("panel_thickness_m"), 0.004 if C.material(rec) == "cardboard" else 0.001)
        m["unflattened_size_m"] = list(rec.get("size_m") or [w_, d_, h_])
        m["size_m"] = [round(w_ + d_, 3), round(h_ + d_, 3), round(panel * 2.0, 4)]
        m["flattened"] = True
        m["volume_l"] = round((w_ + d_) * (h_ + d_) * panel * 2.0 * 1000.0, 3)
        m["open"] = True
        m["container"] = False
        m["accepts_items"] = False
        if rec.get("_keep"):
            C.mark_damage(w, eid, "flattened_keeper", "this box was being kept", broken=False)
        if not seams_open:
            C.mark_damage(w, eid, "torn_flat", "crushed flat without slitting the tape", broken=False)
        return "Pressed {} flat".format(eid)
    if C.is_kind(rec, "bag", "zip_bag", "trash_bag") or rec.get("air_frac") is not None:
        m = w.mut(eid)
        m["air_frac"] = 0.1
        if any("sharp" in (w.ent(c).get("hazards") or []) or w.ent(c).get("broken") for c in w.descendants(eid)):
            m["torn"] = True
            m["leaking"] = True
            C.mark_damage(w, eid, "punctured", "pressed against sharp contents", broken=False)
            return "A sharp edge inside punctured the bag"
        return "Pressed the air out of {}".format(eid)
    threshold = C.crush_force(rec)
    if threshold is not None and force > threshold:
        if rec.get("soft"):
            w.mut(eid)["squashed"] = True
            C.mark_damage(w, eid, "crushed", "squeezed with {:.0f} N".format(force))
        else:
            C.break_item(w, eid, "squeezed")
        return "It gave way under the pressure"
    if wringable(rec):
        wet = C.num(rec.get("wetness"))
        m = w.mut(eid)
        m["wetness"] = round(max(0.05, wet * (0.3 if force >= 30 else 0.6)), 3)
        released = (wet - m["wetness"]) * C.num(rec.get("absorb_capacity_l"), 0.1)
        dest = ctx.args.get("destination")
        over_drain = dest in w.s["entities"] and (w.ent(dest).get("drain") or w.ent(dest).get("liquid_container")) if dest else False
        if released > 0.001 and not over_drain:
            if dest in w.s["entities"]:
                dm = w.mut(dest)
                dm["wetness"] = round(min(1.0, C.num(dm.get("wetness")) + 0.4), 3)
            else:
                C.make_spill(w, C.room_of(w, eid) or w.robot["room"], w.robot.get("pos"), "water", released, source=eid)
        return "Wrung out {}".format(eid)
    return "Squeezed {}".format(eid)


HAND_TEARABLE = ("paper", "fabric", "cardstock", "tissue", "newsprint", "foil")
SOFT_ABSORBENT = ("cellulose", "sponge", "foam", "cotton", "microfiber", "terry", "fabric", "chamois", "wool", "linen")


def wringable(rec):
    """Things that hold water in their body and give it up when squeezed: sponges, cloths, rags, mop heads (by kind or
    by a soft absorbent material). An 'absorbent' product in a rigid bottle (oil-absorbent granules) is not."""
    if C.is_kind(rec, "sponge", "cloth", "rag", "mop_head"):
        return True
    if "sponge" in str(rec.get("type") or ""):
        return True
    return bool(rec.get("absorbent")) and C.material(rec) in SOFT_ABSORBENT


def tears_by_hand(rec):
    """Sheet materials a hand can rip: any paper (thermal receipt paper, foil-laminated gift wrap, kraft, tissue,
    crepe), kitchen foil, cardstock and plain fabrics. Not corrugated cardboard, plastics, wire-cored ties."""
    if rec.get("tearable") is not None:
        return bool(rec.get("tearable"))
    mat = C.material(rec)
    if "wire" in mat:
        return False
    return mat in HAND_TEARABLE or mat.endswith("_paper") or mat.startswith("paper")


@action("tear", params={"target": "entity", "length_m": {"type": "number", "default": 0.4, "min": 0.02, "max": 10.0},
                        "along": {"type": "str", "default": "perforation", "choices": ["perforation", "edge", "notch", "anywhere"]}, "hand": "?str:left|right"},
        duration=3.0, energy=0.002, noise=0.05, category="physical",
        doc="Tear: a sheet of length_m off a roll (wrapping paper, foil, plastic wrap, paper towel, packing paper, tape without a dispenser) into a "
            "free hand, or open packaging at its tear notch/perforation. Tearing documents or fabric damages them.")
def tear(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    length = C.num(ctx.args.get("length_m"), 0.4)
    if rec.get("length_m") is not None and (rec.get("width_m") is not None or rec.get("wrap_material") or rec.get("tape_kind")):
        have = C.num(rec["length_m"])
        require(have >= length - 1e-6, "Not enough left on the roll" if C.realistic(ctx) else "Only {:.2f} m left on the roll".format(have), "resource")
        hands = C.free_hands(w)
        require(hands, "No free hand for the sheet", "capability")
        width = C.num(rec.get("width_m"), 0.3)
        w.mut(eid)["length_m"] = round(have - length, 3)
        if C.realistic(ctx):
            ctx.grant(eid, "inspect")      # tearing a sheet off is a close look at the roll (and the sheet says what it is)
        props = {"label": "sheet of " + str(rec.get("wrap_material") or rec.get("label", "paper")), "category": "sheet",
                 "wrap_material": rec.get("wrap_material"), "sheet_area_m2": round(length * width, 4), "size_m": [round(width, 3), round(length, 3), 0.001],
                 "mass_kg": round(C.num(rec.get("sheet_mass_kg_per_m2"), 0.08) * length * width, 4), "volume_l": 0.05, "material": rec.get("material", "paper"),
                 "relation": "held", "held_hand": hands[0]}
        if rec.get("tape_kind"):
            props["tape_kind"] = rec["tape_kind"]
            props["category"] = "tape_strip"
        sheet = w.spawn("generic_item", "robot", **props)
        w.robot_m()["hands"][hands[0]] = sheet
        return "Tore off a {:.2f} m sheet ({})".format(length, sheet)
    along = ctx.args.get("along")
    # whether it tears, and how, depends on what it is made of: an unidentified look-alike must be identified first
    # (every member of its group gets this same answer)
    C.require_identified(ctx, eid)
    m = w.mut(eid)
    if rec.get("tear_notch") or along in ("perforation", "notch") and (rec.get("perforated") or rec.get("tear_notch")):
        m["open"] = True
        m["torn_open"] = True
        m["sealed"] = False
        return "Tore {} open along its notch".format(eid)
    if C.is_kind(rec, "document", "paper", "letter", "book", "photo") or tears_by_hand(rec):
        C.mark_damage(w, eid, "torn", "torn by hand", broken=False)
        m["torn"] = True
        return "Tore {}".format(eid)
    if rec.get("packaged") or rec.get("shrink_wrapped"):
        m["packaged"] = False
        m["shrink_wrapped"] = False
        return "Tore the film off {}".format(eid)
    raise ActionError("It does not tear by hand; use a cutting tool", "capability")
