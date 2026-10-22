"""Knife and produce work: cut, slice, dice, mince, chop, cube, trim, peel, remove_part, carve,
fillet, shuck, grate, zest, juice, crush, grind, mash.

Geometry model (fidelity G / P-approx): every food item has whole dimensions (size_m or
food.unit_size_m) and, once cut, a piece description (pieces, piece_mm [l,w,h], piece_cv,
round_pieces, cut_axis). Piece size comes from the requested size and the cut style; size
spread (coefficient of variation) comes from technique (claw grip), board stability, knife
sharpness, slips and greasy fingers on the handle. Rolling round produce, a sliding board, flat fingers, a dull blade
or a handle in greasy fingers make the knife slip (a cut gripper pad, body_faults gripper_cut, -> stats.damage). Parts (peel, seeds, pit, stem, root,
bones, ...) are tracked per item; removing a part moves its mass to a scraps pile whose waste
stream decides where it may be discarded.
"""
import math

from ...kernel import body
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.physics import is_powered
from ..core import add_soil
from . import common as C
from . import data as D
from . import model as M
from . import percept as P

TECH = "?str:claw|flat|none"


def _prepare(ctx, capability, allow=("board", "plate"), need_free_hand=True):
    w = ctx.w
    fid = ctx.args["target"]
    rec = C.food_target(ctx, fid)
    f = rec["food"]
    require(not rec.get("package"), "Take it out of its packaging first", "precondition")
    require(f.get("phase") not in ("liquid", "granular", "powder", "paste"), "That cannot be cut", "argument")
    knife = C.tool(ctx, capability, ctx.args.get("tool"))
    surface, kind = C.cutting_surface(ctx, fid, allow)
    if need_free_hand:
        C.free_hand_for_steadying(ctx)
    technique = ctx.args.get("technique") or "claw"
    if float(f.get("ice", 0.0)) > 0.5:
        technique = "none" if technique != "claw" else "flat"
    style = ctx.data.get("style", "pieces")
    slipped = C.slip_check(ctx, fid, knife, surface, technique, style)
    C.dull(ctx, knife, ctx.data.get("dull_knife", 0.0))
    C.tool_touch(ctx, knife, fid, 0.6)
    C.surface_touch(ctx, surface, fid, 0.6)
    C.touch_food(ctx, fid, 0.4)
    cv = D.CV_SLOPPY if technique != "claw" else D.CV_GOOD
    if surface in w.s["entities"] and w.ent(surface).get("tool_kind") == "cutting_board" and C.board_slip_risk(w.s, surface) > 0.25:
        cv += 0.08
    if float(w.ent(knife).get("sharpness", 1.0)) < 0.5:
        cv += 0.08
    if ctx.hand_of(knife) and body.slippery(w.s, ctx.hand_of(knife)) >= 0.5:
        cv += 0.08              # the handle turns in greasy or doughy fingers: uneven pieces (wash the hands first)
    if slipped:
        cv = max(cv, 0.45)
    return fid, w.ent(fid), knife, surface, technique, cv, slipped


def _whole_dims(rec):
    dims = sorted(C.dims_mm(rec), reverse=True)
    return dims   # [length, width, height] descending


def _done(ctx, fid, msg, seconds):
    C.seconds(ctx, seconds)
    w = ctx.w
    rec = w.ent(fid)
    f = rec["food"]
    out = {"target": fid, "result": msg, "cut": f.get("cut"), "pieces": f.get("pieces"),
           "piece_mm": f.get("piece_mm"), "piece_cv": f.get("piece_cv")}
    if ctx.data.get("slipped"):
        out["warning"] = "knife slipped: " + ctx.data["slipped"]
    C.seen_inside(ctx, fid)                 # a cut egg shows whether it was boiled
    if ctx.realistic and P.inside_look(rec):
        out["inside"] = P.inside_look(rec)  # a cut face shows the centre's doneness
    return out


def _onion_quality(rec, cv, extra, style="dice"):
    """Dicing an onion needs the root end on (else layers splay into slivers) and the skin off (else skin pieces end up
    in the product). Halving, quartering, trimming or making a flat side does not cut skin into the product."""
    f = rec["food"]
    if style in ("halve", "quarter", "flat_side", "trim", "wedges"):
        return cv
    if f.get("kind") in ("onion", "red_onion", "shallot") and f.get("layered", True):
        if "root" not in f.get("parts", []):
            extra["slivers"] = True
            cv = max(cv, 0.5)
        if "skin" in f.get("parts", []):
            extra["skin_in_product"] = True
    if f.get("kind") in ("garlic_clove",) and "skin" in f.get("parts", []):
        extra["skin_in_product"] = True
    return cv


@action("cut", params={"target": "entity", "tool": "?entity", "technique": TECH,
                       "style": "?str:pieces|halve|quarter|wedges|strips|chunks|flat_side|julienne|shred|radial|portions",
                       "pieces": {"type": "int", "default": 0, "min": 0, "max": 400},
                       "size_mm": {"type": "number", "default": 0, "min": 0, "max": 200},
                       "axis": "?str:lengthwise|crosswise", "angle_to_grain_deg": {"type": "number", "default": 90, "min": 0, "max": 90},
                       "offset_deg": {"type": "number", "default": 0, "min": 0, "max": 359}},
        duration=C.duration(8.0), irreversible=True, noise=0.05,
        doc="Cut a food item on a cutting board with a held knife: halve/quarter (axis lengthwise|crosswise), wedges, strips "
            "(meat: angle_to_grain_deg 90 = across the grain), chunks/pieces (pieces=n or size_mm), flat_side (stabilise round "
            "produce), julienne, shred (cooked meat), radial (round cakes/pizzas into equal wedges from offset_deg) or portions.")
@P.perceived_result
def cut(ctx):
    style = ctx.args.get("style") or "pieces"
    ctx.data["style"] = style
    pre = ctx.w.s["entities"].get(ctx.args["target"]) or {}
    pf = M.food(pre) or {}
    if style in ("halve", "quarter", "wedges", "flat_side", "radial", "portions") and int(pf.get("pieces", 1) or 1) > max(1, int(pf.get("count", 1) or 1)):
        raise ActionError("It is already cut into pieces", "precondition")
    fid, rec, knife, surface, technique, cv, slipped = _prepare(ctx, "cut")
    w = ctx.w
    f = rec["food"]
    L, W, H = _whole_dims(rec)
    axis = ctx.args.get("axis") or ("lengthwise" if style in ("quarter", "strips", "julienne") else "crosswise")
    extra = {}
    n_req = int(ctx.args.get("pieces") or 0)
    size = float(ctx.args.get("size_mm") or 0)
    round_pieces = False
    units = max(1, int(f.get("count", 1) or 1))
    if style == "flat_side":
        loss = 0.05 * M.qty(rec)
        M.set_qty(w, fid, M.qty(rec) - loss)
        M.add_scraps(w, surface, f.get("kind", "food") + "_trim", loss, D.SCRAP_STREAM.get(f.get("cat"), "compost"), fid)
        m = w.mut(fid)
        m["food"]["flat_side"] = True
        return _done(ctx, fid, "made a flat side; it no longer rolls", 6.0)
    if style == "halve":
        piece = [L, W / 2.0, H] if axis == "lengthwise" else [L / 2.0, W, H]
        round_pieces = bool(f.get("round"))
        seconds = 3.0 * units
    elif style == "quarter":
        piece = [L, W / 2.0, H / 2.0] if axis == "lengthwise" else [L / 4.0, W, H]
        round_pieces = bool(f.get("round")) and axis == "crosswise"
        seconds = 5.0 * units
    elif style == "wedges":
        n = max(2, n_req or 8)
        piece = [L, max(4.0, math.pi * W / n), H / 2.0]
        seconds = 2.0 * n * units
    elif style in ("strips", "julienne"):
        width = size or (3.0 if style == "julienne" else 8.0)
        piece = [L, width, min(H, width if style == "julienne" else H)]
        if M.catinfo(rec).get("raw_animal") or f.get("grain"):
            angle = float(ctx.args.get("angle_to_grain_deg", 90))
            extra["grain_angle_deg"] = angle
            extra["texture"] = "tender" if angle >= 60 else "chewy" if angle >= 30 else "tough"
            piece = [W, width, H]
        seconds = 1.2 * (W / width) * units
    elif style == "shred":
        # realistic perception: the robot judges "cooked" by the browned outside; shredding then shows the centre
        cooked = P.looks_cooked(rec) if ctx.realistic and M.catinfo(rec).get("raw_animal") else f.get("cooked")
        require(cooked or f.get("cat") in ("leafy_green", "vegetable", "cheese_hard", "cheese_soft"), "Shred cooked meat or leafy produce", "argument")
        piece = [40.0, 4.0, 4.0]
        seconds = 30.0 * max(1.0, M.qty(rec) / 200.0)
    elif style in ("radial", "portions"):
        n = max(2, n_req or 8)
        piece = [L / 2.0, math.pi * W / n, H]
        extra.update(_fair_split(rec, n, float(ctx.args.get("offset_deg", 0.0)), style))
        seconds = 4.0 * n
    else:   # pieces / chunks
        if size:
            piece = [size, size, size]
        else:
            n = max(1, n_req or 4)
            vol = L * W * H / n
            side = vol ** (1.0 / 3.0)
            piece = [side, side, side]
        seconds = 1.5 * max(1, int(round(L * W * H / max(1.0, piece[0] * piece[1] * piece[2])))) * 0.4
    cv = _onion_quality(rec, cv, extra, style)
    if style == "pieces" and n_req and not size and units == 1:
        # cutting n pieces from one item: piece volume follows the item's own volume
        extra["piece_ml_target"] = round(M.volume_ml(rec) / n_req, 2)
    C.set_pieces(w, fid, piece, "halved" if style == "halve" else "quartered" if style == "quarter" else
                 "wedges" if style == "wedges" else "strips" if style == "strips" else style if style in ("julienne", "shred") else
                 "chunks" if style in ("pieces", "chunks") else "portions", cv, round_pieces, axis, extra)
    if style == "pieces" and n_req and units == 1 and not size:
        m = w.mut(fid)
        m["food"]["pieces"] = n_req
        m["food"]["piece_ml"] = round(M.volume_ml(rec) / n_req, 3)
    return _done(ctx, fid, "cut ({})".format(style), seconds + 4.0)


def _fair_split(rec, n, offset, style):
    """Equal-angle wedges of a round item; topping counts per wedge from the item's topping layout."""
    f = rec["food"]
    toppings = f.get("toppings") or []      # list of [angle_deg, radius_frac, kind]
    counts = [0] * n
    kinds = [dict() for _ in range(n)]
    for angle, _radius, tkind in toppings:
        idx = int(((float(angle) - offset) % 360.0) // (360.0 / n))
        counts[idx] += 1
        kinds[idx][tkind] = kinds[idx].get(tkind, 0) + 1
    return {"wedges": n, "wedge_toppings": counts, "wedge_topping_kinds": kinds,
            "topping_spread": (max(counts) - min(counts)) if counts else 0}


@action("slice", params={"target": "entity", "tool": "?entity", "technique": TECH,
                         "thickness_mm": {"type": "number", "default": 5.0, "min": 0.5, "max": 40.0},
                         "form": "?str:rounds|half_moons|bias|planks|wedges"},
        duration=C.duration(10.0), irreversible=True, noise=0.05,
        doc="Slice a food item into slices of thickness_mm (rounds, half_moons, bias, planks) with a held knife, or with a "
            "mandoline set on the board (hold its guard or wear a cut glove). Round produce needs a flat side first.")
@P.perceived_result
def slice_(ctx):
    w = ctx.w
    form = ctx.args.get("form") or "rounds"
    ctx.data["style"] = "slice"
    thickness = float(ctx.args["thickness_mm"])
    mandoline = _mandoline(ctx)
    if mandoline:
        fid = ctx.args["target"]
        rec = C.food_target(ctx, fid)
        require(rec.get("location") == "robot", "Hold the food to run it over the mandoline", "precondition")
        guard = mandoline.get("guarded")
        L, W, H = _whole_dims(rec)
        f = rec["food"]
        if f.get("hard_core") or float(f.get("ice", 0.0)) > 0.5:
            m = w.mut(ctx.data["mandoline_id"])
            m["broken"] = True
            m["condition"] = "chipped blade"
            w.stat("damage")
        stub_mm = 20.0
        if not guard:
            C.knife_slip(ctx, fid, "fingers reached the mandoline blade at the last {} mm".format(int(stub_mm)))
        C.tool_touch(ctx, ctx.data["mandoline_id"], fid, 0.6)
        C.touch_food(ctx, fid, 0.4)
        sliced = max(0.0, (L - stub_mm) / L) if guard else 1.0
        if guard and sliced < 1.0:
            nub = M.qty(rec) * (1.0 - sliced)
            M.set_qty(w, fid, M.qty(rec) - nub)
            M.add_scraps(w, ctx.data["mandoline_id"], f.get("kind", "food") + "_nub", nub, D.SCRAP_STREAM.get(f.get("cat"), "compost"), fid)
        C.set_pieces(w, fid, [thickness, W, H], "sliced", 0.05, bool(f.get("round")), "crosswise", {"slice_form": form, "slice_thickness_mm": thickness})
        dest = ctx.data.get("mandoline_surface")
        if dest:
            M.place_new(w, fid, dest, "on" if w.ent(dest).get("surface") else "in")
        return _done(ctx, fid, "sliced on the mandoline", 2.0 + L / thickness * 0.4)
    fid, rec, knife, surface, technique, cv, slipped = _prepare(ctx, "slice")
    f = rec["food"]
    L, W, H = _whole_dims(rec)
    if f.get("cut") in ("halved",) and form == "rounds":
        form = "half_moons"
    piece = [thickness, W, H]
    round_pieces = bool(f.get("round")) and form == "rounds"
    if form == "half_moons":
        piece = [thickness, W / 2.0, H]
    elif form == "bias":
        piece = [thickness, W * 1.4, H]
    elif form == "planks":
        piece = [L, thickness, H]
    extra = {"slice_form": form, "slice_thickness_mm": thickness}
    cv = _onion_quality(rec, cv, extra)
    C.set_pieces(w, fid, piece, "sliced", cv, round_pieces, "crosswise", extra)
    n = max(1, int(L / thickness)) * max(1, int(f.get("count", 1) or 1))
    return _done(ctx, fid, "sliced", 4.0 + 0.6 * n)


def _mandoline(ctx):
    """A mandoline named as the tool, resting on a board/bowl; the food (or the guard holding it) is in hand."""
    w = ctx.w
    tid = ctx.args.get("tool")
    if not tid or tid not in w.s["entities"] or w.ent(tid).get("tool_kind") != "mandoline":
        return None
    rec = ctx.obj(tid, touch=True)
    require(not ctx.holding(tid), "Set the mandoline down on a board or over a bowl first", "precondition")
    require(not rec.get("broken"), "The mandoline blade is damaged; do not use it", "safety")
    guard = any(w.ent(h).get("tool_kind") == "mandoline_guard" for h in w.held_items()) or         "cut_resistant_gloves" in w.robot.get("wearing", [])
    ctx.data["mandoline_id"] = tid
    ctx.data["mandoline_surface"] = rec.get("location") if rec.get("location") in w.s["entities"] else None
    return {"guarded": guard}


@action("dice", params={"target": "entity", "tool": "?entity", "technique": TECH,
                        "size": "?str:brunoise|small|medium|large", "size_mm": {"type": "number", "default": 0, "min": 0, "max": 40}},
        duration=C.duration(30.0), irreversible=True, noise=0.05,
        doc="Dice into near-cubes (brunoise 3, small 6, medium 12, large 20 mm). For onions keep the root end on while "
            "making the cuts; peel first; halve round produce first so it does not roll.")
@P.perceived_result
def dice(ctx):
    ctx.data["style"] = "dice"
    fid, rec, knife, surface, technique, cv, slipped = _prepare(ctx, "dice")
    w = ctx.w
    size = float(ctx.args.get("size_mm") or D.DICE_MM.get(ctx.args.get("size") or "medium", 12.0))
    extra = {}
    cv = _onion_quality(rec, cv, extra)
    piece = [size, size, size]
    if extra.get("slivers"):
        piece = [size * 2.4, size * 0.6, size * 0.5]
    C.set_pieces(w, fid, piece, "diced", cv, False, None, extra)
    f = w.ent(fid)["food"]
    return _done(ctx, fid, "diced", 15.0 + 0.15 * int(f.get("pieces", 1)))


@action("mince", params={"target": "entity", "tool": "?entity", "technique": TECH,
                         "size_mm": {"type": "number", "default": 2.0, "min": 0.3, "max": 6.0}, "method": "?str:knife|press|grater"},
        duration=C.duration(40.0), irreversible=True, noise=0.05,
        doc="Mince aromatics to ~1-3 mm with a rocking knife (or a garlic press). Finer than ~1 mm ruptures cells into a "
            "watery paste; peel cloves first.")
@P.perceived_result
def mince(ctx):
    w = ctx.w
    method = ctx.args.get("method") or "knife"
    ctx.data["style"] = "mince"
    if method == "press":
        fid = ctx.args["target"]
        rec = C.food_target(ctx, fid)
        press = C.tool(ctx, "press_garlic", ctx.args.get("tool"))
        C.tool_touch(ctx, press, fid, 0.8)
        extra = {"pressed": True, "moisture_release": 0.35}
        C.set_pieces(w, fid, [1.0, 1.0, 1.0], "minced", 0.35, False, None, extra)
        if "skin" in rec["food"].get("parts", []):
            m = w.mut(fid)
            m["food"]["parts"] = [p for p in m["food"]["parts"] if p != "skin"]
            loss = 0.08 * M.qty(rec)
            M.set_qty(w, fid, M.qty(rec) - loss)
        return _done(ctx, fid, "pressed", 12.0)
    fid, rec, knife, surface, technique, cv, slipped = _prepare(ctx, "mince")
    size = float(ctx.args["size_mm"])
    extra = {}
    cv = _onion_quality(rec, cv, extra)
    passes = int(round(30.0 / size))
    extra["moisture_release"] = round(min(1.0, 0.03 * passes / max(0.3, size)), 3)
    if size < 1.0:
        extra["paste"] = True
    C.set_pieces(w, fid, [size, size, size], "minced", max(cv, 0.25), False, None, extra)
    return _done(ctx, fid, "minced", 20.0 + 4.0 * passes)


@action("chop", params={"target": "entity", "tool": "?entity", "technique": TECH,
                        "size": "?str:fine|medium|coarse", "method": "?str:rock|chiffonade"},
        duration=C.duration(25.0), irreversible=True, noise=0.05,
        doc="Roughly chop (fine 4, medium 8, coarse 15 mm). Herbs: dry them first, strip woody stems; chiffonade basil.")
@P.perceived_result
def chop(ctx):
    ctx.data["style"] = "chop"
    fid, rec, knife, surface, technique, cv, slipped = _prepare(ctx, "chop")
    w = ctx.w
    f = rec["food"]
    size = D.CHOP_MM.get(ctx.args.get("size") or "medium", 8.0)
    extra = {}
    if f.get("cat") == "herb":
        wet = float(f.get("wet", 0.0))
        passes = 30.0 / size
        bruise = 0.05 * passes
        if (ctx.args.get("method") or "rock") != "chiffonade" and f.get("bruises_easily"):
            bruise += 0.25 * passes / 4.0
        if wet > 0.3:
            extra["clumped_paste"] = True
            bruise += 0.4
        if float(knife and ctx.w.ent(knife).get("sharpness", 1.0) or 1.0) < 0.5:
            bruise += 0.3
        extra["bruise"] = round(min(1.0, bruise), 3)
        if "woody_stem" in f.get("parts", []):
            extra["woody_stem_in_product"] = True
    cv = _onion_quality(rec, cv, extra)
    C.set_pieces(w, fid, [size, size, max(1.0, size / 3.0) if f.get("cat") == "herb" else size], "chopped", cv, False, None, extra)
    return _done(ctx, fid, "chopped", 12.0 + 120.0 / size)


@action("cube", params={"target": "entity", "tool": "?entity", "technique": TECH,
                        "size_mm": {"type": "number", "default": 25.0, "min": 10.0, "max": 50.0}},
        duration=C.duration(30.0), irreversible=True, noise=0.05,
        doc="Cut into 15-40 mm cubes (melon, pineapple, meat for stew, cheese, bread). Remove rind/core first; wash melon "
            "rind before the first cut.")
@P.perceived_result
def cube(ctx):
    ctx.data["style"] = "cube"
    fid, rec, knife, surface, technique, cv, slipped = _prepare(ctx, "cube")
    w = ctx.w
    f = rec["food"]
    size = float(ctx.args["size_mm"])
    extra = {}
    for part in ("rind", "core", "skin", "peel"):
        if part in f.get("parts", []) and (f.get("part_info") or {}).get(part, {}).get("inedible", part in ("rind", "core")):
            extra[part + "_in_product"] = True
    if f.get("cat") in ("fruit", "berry") and f.get("juicy"):
        M.spill(w, fid, f.get("kind", "juice"), 0.0 if surface and w.ent(surface).get("juice_groove") else 25.0, "liquid")
    C.set_pieces(w, fid, [size, size, size], "cubed", cv, False, None, extra)
    return _done(ctx, fid, "cubed", 20.0 + 0.5 * int(w.ent(fid)["food"].get("pieces", 1)))


@action("trim", params={"target": "entity", "tool": "?entity", "technique": TECH,
                        "part": "str:ends|stem_end|woody_base|root|top|fat|blemishes|crust|outer_leaves|flat_side|dark_green_top",
                        "method": "?str:cut|snap", "length_mm": {"type": "number", "default": 0, "min": 0, "max": 300},
                        "scraps_to": "?entity"},
        duration=C.duration(10.0), irreversible=True, noise=0.03,
        doc="Trim inedible ends/bases/blemishes. Asparagus snaps at its natural woody point (method=snap); a fixed "
            "length_mm cut can over- or under-trim. Keep the onion root on if it will be diced.")
@P.perceived_result
def trim(ctx):
    w = ctx.w
    part = ctx.args["part"]
    method = ctx.args.get("method") or "cut"
    if method == "snap":
        fid = ctx.args["target"]
        rec = C.food_target(ctx, fid)
        require(rec["food"].get("snaps"), "Only stalks like asparagus snap cleanly", "argument")
        C.touch_food(ctx, fid, 0.4)
        surface = rec.get("location")
        knife = None
        cv = D.CV_GOOD
    else:
        ctx.data["style"] = "trim"
        fid, rec, knife, surface, technique, cv, slipped = _prepare(ctx, "trim")
    f = rec["food"]
    info = C.part_info(rec, part)
    total = M.qty(rec)
    ideal = float(info.get("frac", 0.03)) * total
    if method == "snap" or part not in ("woody_base", "ends", "stem_end"):
        removed = ideal
    else:
        L = _whole_dims(rec)[0]
        length = float(ctx.args.get("length_mm") or 0) or L * float(info.get("frac", 0.03))
        removed = min(total * 0.95, total * length / max(1.0, L))
    m = w.mut(fid)
    mf = m["food"]
    left_inedible = removed < ideal * 0.8
    if part in mf.get("parts", []) and not left_inedible:
        mf["parts"] = [p for p in mf["parts"] if p != part]
    if left_inedible:
        mf["inedible_remaining"] = sorted(set(mf.get("inedible_remaining", [])) | {part})
    if part == "flat_side":
        mf["flat_side"] = True
    mf["trim_loss_frac"] = round(float(mf.get("trim_loss_frac", 0.0)) + max(0.0, removed - ideal) / max(1.0, total), 4)
    M.set_qty(w, fid, total - removed)
    stream = ctx.args.get("scraps_to") or surface
    if stream and stream in w.s["entities"]:
        M.add_scraps(w, stream, "{}_{}".format(f.get("kind", "food"), part), removed, D.SCRAP_STREAM.get(f.get("cat"), "compost"), fid)
    return _done(ctx, fid, "trimmed {} ({} removed)".format(part, P.grams_text(ctx, fid, "trim_loss_g", removed)), 4.0 + 2.0 * max(1, int(f.get("count", 1) or 1)))


@action("peel", params={"target": "entity", "tool": "?entity", "direction": "?str:away|toward",
                        "thickness_mm": {"type": "number", "default": 0, "min": 0, "max": 15}, "grip": "?str:gentle|normal|firm",
                        "scraps_to": "?entity"},
        duration=C.duration(30.0), irreversible=True, noise=0.03,
        doc="Peel produce: a held peeler (thin, ~1 mm) or paring knife (~2.5 mm), stroking away from the hand; citrus and "
            "bananas by hand (no tool). Soft fruit needs grip=gentle. Potato eyes and green patches need remove_part.")
@P.perceived_result
def peel(ctx):
    w = ctx.w
    fid = ctx.args["target"]
    rec = C.food_target(ctx, fid)
    f = rec["food"]
    parts = f.get("parts", [])
    part = next((p for p in ("peel", "skin", "rind") if p in parts), None)
    require(part is not None, "It has no peel to remove", "precondition")
    hand_peel = f.get("hand_peel")
    tool_id = C.optional_tool(ctx, "peel", ctx.args.get("tool"))
    if tool_id is None:
        require(hand_peel, "Hold a peeler or paring knife for this", "capability")
        require(ctx.free_hand() is not None or rec.get("location") == "robot", "You need a free hand", "capability")
        thickness = 0.5
        coverage = 0.97
        C.touch_food(ctx, fid, 0.6)
    else:
        tk = w.ent(tool_id).get("tool_kind")
        thickness = float(ctx.args.get("thickness_mm") or (1.0 if tk == "peeler" else 2.5))
        coverage = 0.99 if tk == "peeler" else 0.93
        C.free_hand_for_steadying(ctx, "hold the produce")
        C.tool_touch(ctx, tool_id, fid, 0.6)
        C.touch_food(ctx, fid, 0.4)
        if (ctx.args.get("direction") or "away") == "toward" and tk != "peeler":
            C.knife_slip(ctx, fid, "blade stroked toward the hand holding the produce")
        if f.get("round") and tk != "peeler" and f.get("hard"):
            pass
    firmness = float(f.get("firmness", 0.6))
    grip = ctx.args.get("grip") or "normal"
    extra = {}
    if firmness < 0.3 and grip != "gentle" and not hand_peel:
        extra["crushed"] = True
        extra["bruise"] = round(min(1.0, float(f.get("bruise", 0.0)) + 0.6), 3)
    L, W, H = _whole_dims(rec)
    units = max(1, int(f.get("count", 1) or 1))
    area_m2 = units * (2 * (L * W + L * H + W * H) / 1e6) * 0.8
    flesh_loss = area_m2 * thickness / 1000.0 * 1e6 * M.density(rec)   # grams
    peel_mass = float(C.part_info(rec, part).get("frac", 0.08)) * M.qty(rec)
    loss = min(M.qty(rec) * 0.6, peel_mass + max(0.0, flesh_loss - peel_mass * 0.5))
    M.set_qty(w, fid, M.qty(rec) - loss)
    m = w.mut(fid)
    mf = m["food"]
    mf["parts"] = [p for p in mf.get("parts", []) if p != part]
    mf["peeled"] = True
    mf["peel_coverage"] = coverage
    mf["peel_loss_frac"] = round(loss / max(1.0, loss + M.qty(w.ent(fid))), 3)
    mf.update(extra)
    if tool_id is None and f.get("pith_after_hand_peel"):
        mf["parts"] = sorted(set(mf["parts"]) | {"pith"})
    dest = ctx.args.get("scraps_to") or rec.get("location")
    if dest in w.s["entities"]:
        M.add_scraps(w, dest, "{}_{}".format(f.get("kind", "food"), part), loss, D.SCRAP_STREAM.get(f.get("cat"), "compost"), fid)
    C.seconds(ctx, 6.0 + (20.0 if not hand_peel else 10.0) * units)
    return {"target": fid, "result": "peeled", "coverage": coverage, "loss_g": round(loss, 1),
            "warning": ctx.data.get("slipped")}


PART_TOOLS = {"pit": ("pit", "cut", "scoop"), "calyx": ("hull", "cut"), "seeds": ("scoop", "cut"), "core": ("core", "cut"),
              "pin_bones": ("tweezers",), "bone": ("debone", "cut"), "cork": ("uncork",), "shell": ("shuck", "cut"),
              "clove": None, "leaves": None, "woody_stem": None, "membrane": None, "pith": ("cut", "peel"),
              "eyes": ("cut", "peel"), "green_patch": ("cut", "peel"), "rib": ("cut",), "stem": None, "crust": ("cut",),
              "fat": ("cut",), "skin": ("cut", "peel"), "outer_leaves": None, "tender_stem": ("cut",), "vein": ("cut",),
              "silk": None, "husk": None, "top": ("cut",), "root": ("cut",), "head": ("cut",), "crown": None,
              "rind": ("cut",)}
METHOD_LOSS = {("calyx", "huller"): 0.01, ("calyx", "paring_knife"): 0.04, ("calyx", "slice_top"): 0.22,
               ("pit", "cherry_pitter"): 0.02, ("pit", "paring_knife"): 0.08, ("pit", "spoon"): 0.01, ("pit", "knife_tap"): 0.01,
               ("pit", "twist"): 0.0, ("pit", "cut_around"): 0.03, ("seeds", "spoon"): 0.02, ("seeds", "cut_away"): 0.25,
               ("bone", "boning_knife"): 0.1, ("bone", "chef_knife"): 0.2, ("core", "corer"): 0.03, ("core", "cut"): 0.06}


@action("remove_part", params={"target": "entity", "part": "str", "tool": "?entity",
                               "method": "?str:huller|paring_knife|slice_top|cherry_pitter|spoon|knife_tap|knife_strike|twist|cut_around|cut_away|boning_knife|chef_knife|strip|pull|tweezers|corkscrew|center|angled|corer|cut|hands",
                               "count": {"type": "int", "default": 0, "min": 0, "max": 500}, "scraps_to": "?entity"},
        duration=C.duration(15.0), irreversible=True, noise=0.03,
        doc="Remove one part of produce/meat/fish: stem, seeds, pit, leaves, hull (calyx), bone, pin_bones, shell, core, rib, "
            "woody_stem, membrane, pith, eyes, green_patch, crust, cork (wine, method center), clove (split a garlic head; "
            "count=n). The removed part goes to the scraps pile on the board (or scraps_to). Method sets yield loss and safety.")
@P.perceived_result
def remove_part(ctx):
    w = ctx.w
    fid = ctx.args["target"]
    part = ctx.args["part"]
    rec = C.food_target(ctx, fid)
    f = rec["food"]
    method = ctx.args.get("method")
    if part == "pin_bones":
        # gripping the bone tips needs a precise hand (checked before anything depends on whether there are bones, so
        # a refusal never tells a realistic robot that the fillet has them)
        ctx.require_precision(_tool_hand(ctx, PART_TOOLS["pin_bones"]), D.PIN_BONE_MM, "gripping pin bone tips")
    known = set(f.get("parts", [])) | set(f.get("hidden_parts", [])) | ({"pin_bones"} if rec.get("_pin_bones") else set())
    if ctx.realistic and part in SEARCHED_PARTS and part not in known and f.get("cat") in SEARCHED_PARTS[part]:
        # whether a fillet still has pin bones is found by running tweezers along it, not by a refusal
        return _search_none(ctx, fid, part)
    require(part in known, "It has no {} to remove".format(part.replace("_", " ")), "precondition")
    caps = PART_TOOLS.get(part, ("cut",))
    tool_id = None
    if caps:
        for cap in caps:
            try:
                tool_id = C.optional_tool(ctx, cap, ctx.args.get("tool"))
            except ActionError:
                tool_id = None
            if tool_id:
                break
        if tool_id is None and ctx.args.get("tool"):
            raise ActionError("That tool cannot remove the {} ({})".format(part.replace("_", " "), " / ".join(caps)), "capability")
        if tool_id is None and part not in ("seeds", "calyx", "stem") and not f.get("hand_removable", {}).get(part):
            raise ActionError("Hold a suitable tool ({})".format(" / ".join(caps)), "capability")
    tk = w.ent(tool_id).get("tool_kind") if tool_id else "hands"
    method = method or {"cherry_pitter": "cherry_pitter", "huller": "huller", "paring_knife": "paring_knife",
                        "spoon": "spoon", "boning_knife": "boning_knife", "chef_knife": "chef_knife", "tweezers": "tweezers",
                        "corkscrew": "center", "corer": "corer"}.get(tk, "cut" if tool_id else "pull")
    if part == "cork":
        return _uncork(ctx, fid, rec, tool_id, method)
    if part == "clove":
        return _split_cloves(ctx, fid, rec)
    surface, kind = C.resting_surface(w.s, fid)
    held = rec.get("location") == "robot"
    if tool_id and w.ent(tool_id).get("hazards") and "sharp" in w.ent(tool_id).get("hazards", []):
        if held and method in ("knife_strike", "cut", "paring_knife", "cut_around"):
            C.knife_slip(ctx, fid, "blade driven toward the palm holding the food")
        elif method == "knife_strike":
            C.knife_slip(ctx, fid, "striking a knife into a pit slips off the stone")
    if f.get("capsaicin") and part in ("seeds", "rib", "stem") and "disposable_gloves" not in w.robot.get("wearing", []):
        M.add_contam(w, M.HANDS, "capsaicin", 0.8)
    units = max(1, int(f.get("count", 1) or 1))
    count = int(ctx.args.get("count") or 0) or units
    count = min(count, units)
    info = C.part_info(rec, part)
    frac = float(info.get("frac", 0.05))
    extra_loss = METHOD_LOSS.get((part, method), METHOD_LOSS.get((part, tk), 0.04 if tool_id else 0.02))
    if part in ("pit",) and f.get("clingstone") and method == "twist":
        extra = {"mashed": True}
        extra_loss = 0.15
    else:
        extra = {}
    per_unit = M.qty(rec) / units
    removed = per_unit * count * (frac + extra_loss)
    part_mass = per_unit * count * frac
    if part == "pin_bones":
        removed = 0.5 * float(rec.get("_pin_bones", 10)) * (count / units)
        part_mass = removed
    m = w.mut(fid)
    mf = m["food"]
    if count >= units:
        mf["parts"] = [p for p in mf.get("parts", []) if p != part]
        mf["hidden_parts"] = [p for p in mf.get("hidden_parts", []) if p != part]
        if part == "pin_bones":
            m.pop("_pin_bones", None)
        if part == "bone":
            mf["boneless"] = True
    else:
        mf["units_with_" + part] = units - count
    if part in ("leaves",) and f.get("cat") in ("herb", "leafy_green"):
        mf["cut"] = "stems_only"
    if part in ("woody_stem", "rib") and f.get("cat") in ("herb", "leafy_green"):
        mf["cut"] = "leaves_stripped"
    mf["part_loss_frac"] = round(float(mf.get("part_loss_frac", 0.0)) + max(0.0, removed - part_mass) / max(1.0, M.qty(rec)), 4)
    mf.update(extra)
    M.set_qty(w, fid, M.qty(rec) - removed)
    dest = ctx.args.get("scraps_to") or (surface if surface in w.s["entities"] else None)
    if part == "seeds" and f.get("kind") == "pomegranate":
        pass
    if dest:
        stream = "trash" if part in ("bone", "pin_bones", "shell", "fat", "skin") and M.catinfo(rec).get("raw_animal") else D.SCRAP_STREAM.get(f.get("cat"), "compost")
        if part == "pit" and f.get("cat") in ("fruit",):
            stream = "compost"
        scraps = M.add_scraps(w, dest, "{}_{}".format(f.get("kind", "food"), part), removed, stream, fid)
        if scraps and part in ("bone",):
            w.mut(scraps)["food"]["stock_bones"] = True
    if tool_id:
        C.tool_touch(ctx, tool_id, fid, 0.6)
    C.touch_food(ctx, fid, 0.4)
    if part == "shell":
        C.seen_inside(ctx, fid)
    C.seconds(ctx, PART_SECONDS.get(part, 8.0) * count)
    return {"target": fid, "removed": part, "count": count, "loss_g": round(removed, 1), "warning": ctx.data.get("slipped")}


def _tool_hand(ctx, caps):
    """The hand holding the working tool (the named one, else a held tool with one of caps), else the right hand."""
    for cap in caps or ():
        try:
            tool_id = C.optional_tool(ctx, cap, ctx.args.get("tool"))
        except ActionError:
            tool_id = None
        if tool_id and ctx.hand_of(tool_id):
            return ctx.hand_of(tool_id)
    return "right"


PART_SECONDS = {"pit": 6.0, "calyx": 3.0, "bone": 45.0, "pin_bones": 20.0, "seeds": 10.0}
# parts found only by searching for them (realistic perception): food categories where the search makes sense
SEARCHED_PARTS = {"pin_bones": ("fish",)}


def _search_none(ctx, fid, part):
    """Realistic mode: searching for a hidden part that is not there finds nothing (same tool and time as finding it)."""
    caps = PART_TOOLS.get(part) or ("cut",)
    tool_id = None
    for cap in caps:
        try:
            tool_id = C.optional_tool(ctx, cap, ctx.args.get("tool"))
        except ActionError:
            tool_id = None
        if tool_id:
            break
    if tool_id is None:
        raise ActionError("Hold a suitable tool ({})".format(" / ".join(caps)), "capability")
    C.tool_touch(ctx, tool_id, fid, 0.6)
    C.touch_food(ctx, fid, 0.4)
    units = max(1, int((M.food(ctx.w.ent(fid)) or {}).get("count", 1) or 1))
    count = min(units, int(ctx.args.get("count") or 0) or units)
    C.seconds(ctx, PART_SECONDS.get(part, 8.0) * count)
    return {"target": fid, "removed": part, "count": 0, "found": "none", "loss_g": 0.0, "warning": None}


def _uncork(ctx, fid, rec, tool_id, method):
    w = ctx.w
    require(tool_id and w.ent(tool_id).get("tool_kind") == "corkscrew", "Hold a corkscrew", "capability")
    C.free_hand_for_steadying(ctx, "hold the bottle")
    m = w.mut(fid)
    mf = m["food"]
    mf["parts"] = [p for p in mf.get("parts", []) if p != "cork"]
    m["open"] = True
    m["opened"] = True
    m["opened_on"] = m.get("opened_on") or M.iso(M.today(w.s))
    if method in ("angled", "push") or rec.get("_old_cork") and method != "center":
        mf["cork_bits"] = 6 if method != "push" else 0
        mf["cork_pushed_in"] = method == "push"
    C.seconds(ctx, 20.0)
    return {"target": fid, "result": "uncorked", "cork_bits": mf.get("cork_bits", 0)}


def _split_cloves(ctx, fid, rec):
    w = ctx.w
    f = rec["food"]
    require(f.get("kind") == "garlic_head", "Only a garlic head splits into cloves", "argument")
    C.touch_food(ctx, fid, 0.4)
    n = int(ctx.args.get("count") or 0) or int(f.get("cloves", 10))
    n = min(n, int(f.get("cloves", 10)))
    per = M.qty(rec) * 0.9 / max(1, int(f.get("cloves", 10)))
    surface, kind = C.resting_surface(w.s, fid)
    new = M.spawn_food(w, "garlic_clove", per * n, surface or rec.get("location"), relation="on" if kind in ("board", "plate", "counter", "surface") else "in",
                       type_name="garlic_clove")
    m = w.mut(new)
    m["food"]["count"] = n
    m["food"]["unit_g"] = round(per, 2)
    if m["relation"] == "on":
        from ...kernel import geometry
        spot = geometry.surface_spot(w.s, m["location"], new)
        m["spos"] = spot or [0.0, 0.0]
    left = int(f.get("cloves", 10)) - n
    if left <= 0:
        M.add_scraps(w, surface or rec.get("location"), "garlic_papery_skin", M.qty(rec) * 0.1, "compost", fid)
        M.destroy_food(w, fid)
    else:
        mm = w.mut(fid)
        mm["food"]["cloves"] = left
        M.set_qty(w, fid, M.qty(rec) - per * n)
    C.seconds(ctx, 6.0 + 1.5 * n)
    return {"target": fid, "cloves": new, "count": n}


@action("carve", params={"target": "entity", "tool": "?entity", "technique": TECH,
                         "direction": "?str:across_grain|with_grain", "thickness_mm": {"type": "number", "default": 6.0, "min": 2.0, "max": 30.0},
                         "style": "?str:slices|joints"},
        duration=C.duration(90.0), irreversible=True, noise=0.03,
        doc="Carve a cooked roast or bird on a board: slices across the grain (tender) or joints (breast, thighs, wings). "
            "Let it rest ~10 min after cooking or the juices run out.")
@P.perceived_result
def carve(ctx):
    ctx.data["style"] = "carve"
    fid, rec, knife, surface, technique, cv, slipped = _prepare(ctx, "carve")
    w = ctx.w
    f = rec["food"]
    # realistic perception: whether the centre is done is not visible before carving -- the browned outside is; the
    # carved slices then show the centre (_done "inside")
    cooked = P.looks_cooked(rec) if ctx.realistic else (f.get("cooked") or not M.catinfo(rec).get("raw_animal"))
    require(cooked, "Carve cooked meat; raw meat is cut", "argument")
    now = w.s["time_s"]
    off = rec.get("_heat_off_s")
    rested = off is None or now - float(off) >= 600
    extra = {"rested_before_carving": rested}
    if not rested:
        lost = 0.12 * M.qty(rec)
        M.set_qty(w, fid, M.qty(rec) - lost)
        M.spill(w, fid, "meat_juice", lost * 0.3, "liquid")
        extra["juiciness"] = "dry"
    style = ctx.args.get("style") or "slices"
    direction = ctx.args.get("direction") or "across_grain"
    extra["texture"] = "tender" if direction == "across_grain" else "chewy"
    L, W, H = _whole_dims(rec)
    thickness = float(ctx.args["thickness_mm"])
    if style == "joints":
        piece = [L / 2.0, W / 2.0, H / 2.0]
        extra["joints"] = 8
    else:
        piece = [thickness, W * 0.8, H * 0.8]
    if "bone" in f.get("parts", []):
        bones = 0.25 * M.qty(rec)
        M.set_qty(w, fid, M.qty(rec) - bones)
        sc = M.add_scraps(w, surface, f.get("kind", "meat") + "_carcass", bones, "trash", fid)
        if sc:
            w.mut(sc)["food"]["stock_bones"] = True
        w.mut(fid)["food"]["parts"] = [p for p in f.get("parts", []) if p != "bone"]
    C.set_pieces(w, fid, piece, "carved", cv, False, None, extra)
    return _done(ctx, fid, "carved", 60.0 + 3.0 * int(w.ent(fid)["food"].get("pieces", 1)))


@action("fillet", params={"target": "entity", "tool": "?entity", "technique": TECH, "scraps_to": "?entity"},
        duration=C.duration(240.0), irreversible=True, noise=0.03,
        doc="Fillet a whole fish with a flexible fillet knife: two boneless-side fillets (pin bones remain until removed with "
            "tweezers), frame/head/skin to scraps (or a stock bag).")
@P.perceived_result
def fillet(ctx):
    ctx.data["style"] = "fillet"
    fid, rec, knife, surface, technique, cv, slipped = _prepare(ctx, "fillet")
    w = ctx.w
    f = rec["food"]
    require(f.get("whole_fish"), "Only a whole fish is filleted", "argument")
    flexible = w.ent(knife).get("tool_kind") == "fillet_knife"
    yield_frac = 0.45 if flexible and not slipped else 0.36
    total = M.qty(rec)
    frame = total * (1.0 - yield_frac)
    M.set_qty(w, fid, total * yield_frac)
    m = w.mut(fid)
    mf = m["food"]
    mf["whole_fish"] = False
    mf["cut"] = "fillets"
    mf["pieces"] = 2
    mf["parts"] = [p for p in mf.get("parts", []) if p not in ("head", "bones", "guts", "tail", "fins")] + ["skin"]
    m["_pin_bones"] = int(rec.get("_pin_bones", 10)) + (0 if flexible else 6)
    mf["hidden_parts"] = sorted(set(mf.get("hidden_parts", [])) | {"pin_bones"})
    m["label"] = (rec.get("label") or "fish") + " fillets"
    dest = ctx.args.get("scraps_to") or surface
    if dest in w.s["entities"]:
        sc = M.add_scraps(w, dest, f.get("kind", "fish") + "_frame", frame, "trash", fid)
        if sc:
            w.mut(sc)["food"]["stock_bones"] = True
    C.seconds(ctx, 200.0)
    return {"target": fid, "result": "filleted", "yield_frac": yield_frac}


@action("shuck", params={"target": "entity", "tool": "?entity", "method": "?str:hinge|side|husk",
                         "count": {"type": "int", "default": 0, "min": 0, "max": 100}, "scraps_to": "?entity"},
        duration=C.duration(30.0), irreversible=True, noise=0.05,
        doc="Shuck oysters/clams with an oyster knife at the hinge (protect the holding hand: cut-resistant glove or the "
            "oyster in a folded towel) or husk corn (husk and silk to compost).")
@P.perceived_result
def shuck(ctx):
    w = ctx.w
    fid = ctx.args["target"]
    rec = C.food_target(ctx, fid)
    f = rec["food"]
    units = max(1, int(f.get("count", 1) or 1))
    count = min(units, int(ctx.args.get("count") or 0) or units)
    surface, kind = C.resting_surface(w.s, fid)
    dest = ctx.args.get("scraps_to") or (surface if surface in w.s["entities"] else None)
    if f.get("cat") == "shellfish":
        knife = C.tool(ctx, "shuck", ctx.args.get("tool"))
        C.free_hand_for_steadying(ctx, "hold the oyster")
        if (ctx.args.get("method") or "hinge") == "hinge":
            ctx.require_precision(ctx.hand_of(knife) or "right", D.OYSTER_HINGE_MM, "working the knife into the hinge")
        protected = "cut_resistant_gloves" in w.robot.get("wearing", []) or \
            (surface in w.s["entities"] and w.ent(surface).get("category") in ("towel", "cloth", "textile"))
        if not protected:
            C.knife_slip(ctx, fid, "oyster knife slipped off the shell into the unprotected holding hand")
        method = ctx.args.get("method") or "hinge"
        fragments = 0 if method == "hinge" else 2 * count
        shell = 0.75 * M.qty(rec) * count / units
        M.set_qty(w, fid, M.qty(rec) - shell)
        m = w.mut(fid)
        mf = m["food"]
        mf["shucked"] = mf.get("shucked", 0) + count
        mf["shell_fragments"] = int(mf.get("shell_fragments", 0)) + fragments
        if mf["shucked"] >= units:
            mf["parts"] = [p for p in mf.get("parts", []) if p != "shell"]
        C.tool_touch(ctx, knife, fid, 0.6)
        if dest:
            M.add_scraps(w, dest, "oyster_shells", shell, "trash", fid)
        C.seconds(ctx, 25.0 * count)
        return {"target": fid, "shucked": count, "shell_fragments": fragments, "warning": ctx.data.get("slipped")}
    require("husk" in f.get("parts", []) or "silk" in f.get("parts", []), "Nothing to shuck", "precondition")
    require(ctx.free_hand() is not None or rec.get("location") == "robot", "You need a free hand", "capability")
    C.touch_food(ctx, fid, 0.4)
    husk = float(C.part_info(rec, "husk").get("frac", 0.15)) * M.qty(rec) * count / units
    M.set_qty(w, fid, M.qty(rec) - husk)
    m = w.mut(fid)
    mf = m["food"]
    mf["parts"] = [p for p in mf.get("parts", []) if p not in ("husk",)]
    if count >= units and "silk" in mf["parts"]:
        mf["silk_strands"] = 15 * count
        mf["parts"] = [p for p in mf["parts"] if p != "silk"]
    if dest:
        M.add_scraps(w, dest, "corn_husk", husk, "compost", fid)
    C.seconds(ctx, 35.0 * count)
    return {"target": fid, "shucked": count}


@action("grate", params={"target": "entity", "tool": "?entity", "destination": "entity",
                         "side": "?str:coarse|medium|fine|zest", "amount_g": {"type": "number", "default": 0, "min": 0, "max": 2000},
                         "stop_at_mm": {"type": "number", "default": 25, "min": 0, "max": 100}},
        duration=C.duration(40.0), irreversible=True, noise=0.15,
        doc="Grate (or shred, coarse side) a held block/vegetable on a held grater braced on the destination board/bowl. "
            "Stop while >= ~20 mm remains (the nub) unless wearing a cut glove; soft cheese needs chilling first.")
@P.perceived_result
def grate(ctx):
    w = ctx.w
    fid = ctx.args["target"]
    rec = C.food_target(ctx, fid)
    f = rec["food"]
    require(rec.get("location") == "robot", "Hold the food against the grater", "precondition")
    require(not rec.get("package"), "Unwrap it first", "precondition")
    grater = C.tool(ctx, "grate", ctx.args.get("tool"))
    dest = ctx.args["destination"]
    drec = C.vessel_reach(ctx, dest)
    side = ctx.args.get("side") or "coarse"
    sides = w.ent(grater).get("grate_sides") or ["coarse", "fine"]
    require(side in sides, "This grater has no {} side ({})".format(side, ", ".join(sides)), "argument")
    L, W, H = _whole_dims(rec)
    total = M.qty(rec)
    per_mm = total / max(1.0, L)
    amount = float(ctx.args.get("amount_g") or 0) or max(0.0, (L - float(ctx.args["stop_at_mm"])) * per_mm)
    amount = min(amount, total)
    remaining_mm = L - amount / per_mm
    guarded = "cut_resistant_gloves" in w.robot.get("wearing", [])
    if remaining_mm < 20.0 and not guarded:
        C.knife_slip(ctx, fid, "knuckles reached the grater teeth with {:.0f} mm left".format(max(0.0, remaining_mm)))
    if f.get("hard_core") or float(f.get("ice", 0.0)) > 0.5:
        mg = w.mut(grater)
        mg["sharpness"] = round(float(mg.get("sharpness", 1.0)) - 0.3, 3)
    piece = {"coarse": [30.0, 4.0, 2.0], "medium": [20.0, 2.5, 1.5], "fine": [5.0, 1.0, 1.0], "zest": [2.0, 0.5, 0.5]}[side]
    extra = {"grate_side": side}
    if f.get("cat") in ("cheese_soft",) and float(rec.get("temperature_c", 4.0)) > 8.0:
        extra["smeared"] = True
        extra["mashed"] = True
    if side in ("fine", "zest") and f.get("cat") in ("vegetable", "root_veg") and f.get("kind") not in ("ginger", "garlic_clove"):
        extra["mushy"] = True
    C.tool_touch(ctx, grater, fid, 0.6)
    C.touch_food(ctx, fid, 0.4)
    portion, grams = M.take(w, fid, amount)
    pf = portion["food"]
    pf["cut"] = "shredded" if side == "coarse" else "grated"
    pf["pieces"] = max(1, int(grams / max(0.05, piece[0] * piece[1] * piece[2] / 1000.0 * M.density(portion))))
    pf["piece_mm"] = piece
    pf["phase"] = "pieces"
    pf.update(extra)
    pf["density"] = round(min(M.density(portion), 0.45), 3)    # loose shreds (1 cup shredded cheese ~ 113 g)
    if extra.get("smeared"):
        stuck = grams * 0.15
        pf["qty_g"] = round(grams - stuck, 2)
    new = M.deposit(w, portion, dest, "on" if drec.get("surface") and not drec.get("container") else "in")
    M.contact(w, grater, new, 0.4)
    M.update_fill(w, dest)
    C.seconds(ctx, 8.0 + grams / 6.0)
    return {"target": fid, "grated": new, "grams": round(grams, 1), "remaining_mm": round(remaining_mm, 1),
            "warning": ctx.data.get("slipped")}


@action("zest", params={"target": "entity", "tool": "?entity", "destination": "entity",
                        "amount_g": {"type": "number", "default": 0, "min": 0, "max": 100}, "pressure": "?str:light|firm"},
        duration=C.duration(40.0), irreversible=True, noise=0.03,
        doc="Zest a held citrus fruit on a microplane/zester over the destination: light pressure takes only the coloured "
            "peel; firm pressure digs into bitter white pith. Wash waxed fruit first; zest before juicing.")
@P.perceived_result
def zest(ctx):
    w = ctx.w
    fid = ctx.args["target"]
    rec = C.food_target(ctx, fid)
    f = rec["food"]
    require(f.get("kind") in D.ZEST_G_PER_UNIT, "Zest citrus fruit", "argument")
    require(rec.get("location") == "robot", "Hold the fruit against the zester", "precondition")
    require(not f.get("juiced"), "The juiced halves are too limp to zest; zest before juicing", "precondition")
    require(not f.get("zested"), "Already zested", "precondition")
    tool_id = C.tool(ctx, "zest", ctx.args.get("tool"))
    dest = ctx.args["destination"]
    drec = C.vessel_reach(ctx, dest)
    units = max(1, int(f.get("count", 1) or 1))
    avail = D.ZEST_G_PER_UNIT[f["kind"]] * units
    amount = min(avail, float(ctx.args.get("amount_g") or avail))
    pith = 0.03 if (ctx.args.get("pressure") or "light") == "light" else 0.3
    C.tool_touch(ctx, tool_id, fid, 0.6)
    C.touch_food(ctx, fid, 0.4)
    new = M.spawn_food(w, f["kind"] + "_zest", amount, dest, relation="in" if drec.get("container") else "on",
                       type_name="food_item", food={"cat": "herb", "phase": "pieces", "rte": True, "density": 0.35,
                                                     "pith_frac": pith, "cut": "zest"},
                       perishable=False, allergens=list(rec.get("allergens", [])))
    if f.get("waxed") and not f.get("washed"):
        M.add_contam(w, new, "wax", 0.5)
    for k, v in sorted((rec.get("contamination") or {}).items()):
        M.add_contam(w, new, k, v * 0.6)
    m = w.mut(fid)
    m["food"]["zested"] = True
    m["food"]["parts"] = [p for p in m["food"].get("parts", []) if p != "zest"]
    M.set_qty(w, fid, M.qty(rec) - amount)
    M.update_fill(w, dest)
    C.seconds(ctx, 25.0 * units)
    return {"target": fid, "zest": new, "grams": round(amount, 1), "pith_frac": pith}


@action("juice", params={"target": "entity", "tool": "?entity", "destination": "entity",
                         "strain": "?entity", "pulp": "?str:keep|remove"},
        duration=C.duration(30.0), irreversible=True, noise=0.05,
        doc="Juice halved citrus with a held reamer/press (or by hand) into the destination. Seeds fall into the juice unless "
            "a strainer is used (strain=<sieve on the destination> or a citrus press). Halve the fruit first.")
@P.perceived_result
def juice(ctx):
    w = ctx.w
    fid = ctx.args["target"]
    rec = C.food_target(ctx, fid)
    f = rec["food"]
    require(f.get("kind") in D.JUICE_ML_PER_UNIT or f.get("juicy"), "That is not a juicing fruit", "argument")
    require(not f.get("juiced"), "Already juiced", "precondition")
    require(f.get("cut") in ("halved",) or f.get("kind") not in D.JUICE_ML_PER_UNIT, "Halve the citrus first", "precondition")
    tool_id = C.optional_tool(ctx, "juice", ctx.args.get("tool"))
    dest = ctx.args["destination"]
    drec = C.vessel_reach(ctx, dest)
    tk = w.ent(tool_id).get("tool_kind") if tool_id else "hands"
    efficiency = {"citrus_press": 0.9, "citrus_reamer": 0.85, "electric_juicer": 0.95}.get(tk, 0.6)
    units = max(1, int(f.get("count", 1) or 1))
    ml = D.JUICE_ML_PER_UNIT.get(f.get("kind"), 0.3 * M.qty(rec)) * units * efficiency
    if f.get("zested"):
        ml *= 0.98
    seeds = 0 if tk == "citrus_press" else D.SEEDS_PER_UNIT.get(f.get("kind"), 4) * units
    strainer = ctx.args.get("strain")
    if strainer:
        srec = ctx.obj(strainer, touch=True)
        require(srec.get("tool_kind") in ("sieve", "strainer", "colander", "fine_mesh_sieve") and
                (srec.get("location") == dest or ctx.holding(strainer)), "Set the strainer over the destination", "precondition")
        seeds = 0
    pulp = (ctx.args.get("pulp") or "keep")
    if pulp == "remove" and not strainer:
        pulp = "keep"
    kind_name = f.get("kind", "fruit") + "_juice"
    new = M.spawn_food(w, kind_name, ml * 1.03, dest, relation="in", type_name="food_item",
                       food={"cat": "beverage", "phase": "liquid", "rte": True, "density": 1.03, "seeds": seeds,
                             "pulp": pulp, "acid": True},
                       perishable=True, life_days=3, allergens=list(rec.get("allergens", [])), storage="fridge")
    for k, v in sorted((rec.get("contamination") or {}).items()):
        M.add_contam(w, new, k, v * 0.5)
    if tool_id:
        C.tool_touch(ctx, tool_id, fid, 0.5)
    C.touch_food(ctx, fid, 0.5)
    if not drec.get("container"):
        M.spill(w, dest, kind_name, ml * 0.2, "liquid")
    m = w.mut(fid)
    m["food"]["juiced"] = True
    M.set_qty(w, fid, M.qty(rec) - ml * 1.03)
    M.update_fill(w, dest)
    C.seconds(ctx, 12.0 * units)
    return {"target": fid, "juice": new, "ml": round(ml, 1), "seeds_in_juice": seeds}


@action("crush", params={"target": "entity", "tool": "?entity", "force": "?str:light|firm",
                         "passes": {"type": "int", "default": 6, "min": 1, "max": 60}},
        duration=C.duration(20.0), irreversible=True, noise=0.3,
        doc="Crush with a rolling pin (crackers/cookies inside a closed bag), the flat of a knife (garlic) or a mortar "
            "(peppercorns). Crushing in an open bag sprays crumbs; glassware used as a hammer breaks.")
@P.perceived_result
def crush(ctx):
    w = ctx.w
    fid = ctx.args["target"]
    rec = C.food_target(ctx, fid)
    f = rec["food"]
    tool_id = ctx.args.get("tool")
    if tool_id and ctx.holding(tool_id) and "crush" not in (w.ent(tool_id).get("capabilities") or []):
        # an improvised pounder (jar, can, glass): rigid enough to crush, but glassware shatters
        require(float(w.ent(tool_id).get("mass_kg", 0.0)) >= 0.2 or w.ent(tool_id).get("fragile"),
                "That is too light to crush with", "capability")
    else:
        tool_id = C.tool(ctx, "crush", tool_id)
    trec = w.ent(tool_id)
    passes = int(ctx.args["passes"])
    force = ctx.args.get("force") or "firm"
    if trec.get("fragile") or trec.get("material") == "glass":
        mt = w.mut(tool_id)
        mt["broken"] = True
        w.stat("breakages")
        w.stat("damage")
        M.add_contam(w, fid, "glass_shards", 1.0)
        C.seconds(ctx, 3.0)
        return {"target": fid, "result": "the improvised glass tool shattered", "warning": "broken glass"}
    parent = w.s["entities"].get(rec.get("location"), {})
    in_closed_bag = parent.get("category") in ("bag", "food_bag") and not parent.get("open")
    in_mortar = parent.get("tool_kind") == "mortar"
    if not in_closed_bag and not in_mortar and trec.get("tool_kind") == "rolling_pin":
        sprayed = 0.15 * M.qty(rec)
        M.set_qty(w, fid, M.qty(rec) - sprayed)
        M.spill(w, fid, f.get("kind", "crumbs"), sprayed, "granular")
        M.spill(w, w.ent(fid).get("location") or fid, f.get("kind", "crumbs"), sprayed * 0.5, "granular")
    size = max(0.3, (12.0 if force == "light" else 8.0) / math.sqrt(passes))
    extra = {}
    if f.get("kind") == "garlic_clove":
        extra["skin_loose"] = True
        size = max(size, 4.0)
    C.tool_touch(ctx, tool_id, fid, 0.5)
    C.set_pieces(w, fid, [size, size, size], "crushed", 0.4, False, None, extra)
    C.seconds(ctx, 4.0 + 2.0 * passes)
    return {"target": fid, "result": "crushed", "particle_mm": round(size, 2)}


def _grind_db(ctx):
    """Sound at 1 m (body quiet mode, robot_sound): a blade spice or coffee grinder runs at about 80-90 dB; a pestle
    worked round a mortar is about 50 dB."""
    w = ctx.w
    gid = ctx.args.get("target")
    rec = w.s["entities"].get(gid) or {}
    if M.food(rec) is not None:
        rec = w.s["entities"].get(rec.get("location")) or {}
    return 50.0 if rec.get("tool_kind") == "mortar" else 85.0


@action("grind", params={"target": "entity", "tool": "?entity", "setting": "?str:fine|medium|coarse",
                         "seconds": {"type": "number", "default": 0, "min": 0, "max": 120}},
        duration=C.duration(20.0), irreversible=True, noise=0.6, timing="commanded", noise_db=_grind_db,
        doc="Grind whole spices/peppercorns/coffee beans inside a grinder (lid closed, powered) or mortar (target = the "
            "grinder/mortar holding them). Particle size follows setting/time; the grinder keeps the flavour of what it ground.")
@P.perceived_result
def grind(ctx):
    w = ctx.w
    gid = ctx.args["target"]
    grec = ctx.obj(gid, touch=True)
    if M.food(grec):
        gid = grec.get("location")
        require(gid in w.s["entities"], "Put it in a grinder or mortar first", "precondition")
        grec = ctx.obj(gid, touch=True)
    require(grec.get("tool_kind") in ("spice_grinder", "mortar", "pepper_mill", "coffee_grinder", "grinder"), "That is not a grinder", "argument")
    contents = M.vessel_foods(w.s, gid)
    require(contents, "The grinder is empty", "precondition")
    if grec.get("tool_kind") == "mortar":
        C.tool(ctx, "pestle", ctx.args.get("tool"))
        secs = float(ctx.args.get("seconds") or 60)
        size = max(0.2, 3.0 * math.exp(-secs / 40.0))
    else:
        if grec.get("plug_required") or grec.get("power_w"):
            require(is_powered(w, gid), "The grinder has no power", "resource")
        setting = ctx.args.get("setting") or "medium"
        secs = float(ctx.args.get("seconds") or {"fine": 25, "medium": 12, "coarse": 5}[setting])
        size = max(0.15, 2.4 * math.exp(-secs / 10.0))
        if grec.get("openable") and grec.get("open"):
            for c in contents:
                lost = 0.4 * M.qty(w.ent(c))
                M.set_qty(w, c, M.qty(w.ent(c)) - lost)
                M.spill(w, gid, M.kind(w.ent(c)), lost, "powder")
            C.witness(w, "grinder_spray", source=gid, size_m=0.2, visual="powder sprays", target=gid)
        fill = M.vessel_food_ml(w.s, gid) / max(1.0, M.capacity_ml(grec))
        if fill > 1.0:
            size *= 2.5
            m = w.mut(gid)
            m["motor_strain"] = True
    designated = grec.get("designated_for")
    for c in contents:
        k = M.kind(w.ent(c))
        if designated and designated not in (k, "any") and not w.ent(c)["food"].get("cat") == designated:
            M.add_contam(w, gid, "flavor_" + k, 0.8)
        M.contact(w, gid, c, 0.6)
        m = w.mut(c)
        m["food"]["particle_mm"] = round(size, 3)
        m["food"]["cut"] = "ground"
        m["food"]["phase"] = "powder"
        if m["food"].get("cat") == "spice":
            m["food"]["kind"] = m["food"]["kind"] if m["food"]["kind"].startswith("ground_") else "ground_" + m["food"]["kind"]
    C.seconds(ctx, secs + 5.0)
    return {"target": gid, "particle_mm": round(size, 3)}


@action("mash", params={"target": "entity", "tool": "?entity", "strokes": {"type": "int", "default": 40, "min": 1, "max": 400}},
        duration=C.duration(30.0), irreversible=True, noise=0.1,
        doc="Mash cooked potatoes, avocado, banana or beans in a bowl/pot with a masher, ricer or fork. Work potatoes hot; "
            "overworking (or a blender) makes them gluey.")
@P.perceived_result
def mash(ctx):
    w = ctx.w
    tid = ctx.args["target"]
    trec = ctx.obj(tid, touch=True)
    foods = [tid] if M.food(trec) else M.vessel_foods(w.s, tid)
    require(foods, "There is nothing to mash", "precondition")
    tool_id = C.tool(ctx, "mash", ctx.args.get("tool"))
    tk = w.ent(tool_id).get("tool_kind")
    strokes = int(ctx.args["strokes"])
    k = {"potato_ricer": 4.0, "potato_masher": 18.0, "fork": 30.0}.get(tk, 25.0)
    for fid in foods:
        rec = w.ent(fid)
        f = rec["food"]
        require(f.get("mashable") and (f.get("cooked") or not f.get("needs_cooking_to_mash")), "{} is too firm to mash; cook it first".format(P.name(ctx, fid)), "precondition")
        work = float(f.get("mash_work", 0.0)) + strokes
        lumps = round(math.exp(-work / k), 4)
        glue = float(f.get("glue", 0.0))
        if f.get("starchy"):
            factor = 1.0 if float(rec.get("temperature_c", 21.0)) >= 60 else 2.5
            glue = min(1.0, glue + factor * max(0.0, work - 60.0) / 300.0 * (strokes / max(1.0, work)) * (work / 60.0))
        m = w.mut(fid)
        mf = m["food"]
        mf.update({"mash_work": work, "lump_frac": lumps, "glue": round(glue, 4), "cut": "mashed", "phase": "paste",
                   "piece_mm": [round(5 + 20 * lumps, 1)] * 3})
        C.tool_touch(ctx, tool_id, fid, 0.5)
    vessel = tid if not M.food(trec) else trec.get("location")
    if vessel in w.s["entities"] and float(w.ent(vessel).get("fill_level", 0.0)) > 0.9:
        spilled = 0.05 * sum(M.qty(w.ent(f)) for f in foods)
        M.spill(w, vessel, M.kind(w.ent(foods[0])), spilled, "paste")
    C.seconds(ctx, 3.0 + strokes * 0.6)
    return {"target": tid, "lump_frac": w.ent(foods[0])["food"].get("lump_frac"), "glue": w.ent(foods[0])["food"].get("glue")}
