"""Folding, unfolding, rolling, pairing, hangers and textile placement physics (fidelity G / P-approx).

Fold geometry: every foldable textile has fold_base_m [w, l] (spread size after sleeves are
laid back / legs aligned), layer_thickness_m and a set of allowed styles. A style is a fold
sequence with factors (fx, fy), a layer count and a number of folds. Folding sets size_m and
volume_l to the folded block, so drawers, shelves and stacks are fitted by the kernel's real
geometry. Alignment error per fold depends on speed, slipperiness, surface support and holding
it in the air; misalignment enlarges the footprint (edges stick out) and thickens the block,
so sloppy or mixed styles no longer stack on each other. Fitted sheets folded without
nesting the corners come out lumpy (1.8x thicker, 15 % larger). Damp items can be folded but
then sour (see the fabric system). Large items folded on the floor or on a too-small surface
drag on the floor and pick up dust.
"""
import json
import math

from ...kernel import geometry
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, before, hook
from ..core import place_item, release_from_hands, check_handling
from . import model as M

TOPS = {"t_shirt", "long_sleeve_shirt", "dress_shirt", "blouse", "polo_shirt", "sweater", "cardigan", "hoodie",
        "tank_top", "athletic_shirt", "sweatshirt", "pajamas", "undershirt", "top"}
BOTTOMS = {"jeans", "dress_pants", "chinos", "shorts", "skirt", "leggings", "sweatpants", "trousers", "bottom"}

# style -> (fx, fy, layers, folds, allowed fold_class values or None for any)
STYLES = {
    "half": (1.0, 0.5, 2, 1, None),
    "half_half": (0.5, 0.5, 4, 2, None),
    "thirds_half": (1.0 / 3, 0.5, 6, 3, None),
    "half_thirds": (0.5, 1.0 / 3, 6, 3, None),
    "thirds_thirds": (1.0 / 3, 1.0 / 3, 9, 4, None),
    "quarters": (0.25, 0.25, 16, 4, {"linen", "blanket", "tablecloth", "sheet"}),
    "retail": (0.55, 0.5, 6, 3, {"top"}),
    "file": (0.36, 0.33, 9, 4, {"top", "bottom", "underwear", "sleepwear"}),
    "thirds": (0.36, 1.0, 3, 2, {"top"}),
    "crease_half": (1.0, 0.5, 4, 1, {"bottom"}),
    "crease_thirds": (1.0, 1.0 / 3, 6, 2, {"bottom"}),
    "fitted_nest": (0.25, 0.2, 20, 5, {"fitted"}),
    "nest": (1.0, 1.0, 2, 0, {"bra"}),
    "pair_fold": (1.0, 0.5, 4, 1, {"sock"}),
    "ball": (0.6, 0.3, 6, 1, {"sock"}),
    "square": (0.5, 0.5, 4, 2, {"underwear", "napkin", "linen", "towel", "accessory"}),
    "suit_fold": (0.5, 0.5, 4, 2, {"structured", "top"}),
    "roll": (1.0 / 3, None, 0, 1, None),
    "gather": (0.3, 0.3, 0, 0, {"tablecloth", "linen", "blanket", "sheet"}),
}
NEAT_LIMIT_CM = 3.0


def fold_class(rec):
    cls = rec.get("fold_class")
    if cls:
        return cls
    kind = rec.get("garment_kind") or rec.get("type")
    if rec.get("fitted"):
        return "fitted"
    if kind in TOPS:
        return "top"
    if kind in BOTTOMS:
        return "bottom"
    return rec.get("category", "linen")


def allowed_styles(rec):
    cls = fold_class(rec)
    classes = {cls}
    if rec.get("structured"):
        classes.add("structured")
    if cls in ("towel",):
        classes.add("linen")
    return sorted(s for s, spec in STYLES.items() if spec[4] is None or classes & spec[4])


def fold_dims(rec, style, align_cm=0.0):
    """Folded block [w, d, h] in meters and volume in litres for a style."""
    base = rec.get("fold_base_m") or list(M.flat_size(rec))
    bw, bl = float(base[0]), float(base[1])
    t = float(rec.get("layer_thickness_m", 0.003))
    fx, fy, layers, folds, _ = STYLES[style]
    lumpy = bool(rec.get("fitted")) and style != "fitted_nest" and style not in ("roll", "gather")
    if style == "roll":
        width = bw * fx
        area = bw * bl
        diameter = max(0.04, math.sqrt(4.0 * area * t / math.pi / max(0.05, width)))
        dims = [width, diameter, diameter]
    elif style == "gather":
        loose = rec.get("loose_size_m") or [0.4, 0.4, 0.2]
        dims = list(loose)
    else:
        grow = align_cm / 100.0
        dims = [bw * fx + grow, bl * fy + grow, max(t, layers * t * (1.8 if lumpy else 1.0) * (1.0 + align_cm / 20.0))]
        if lumpy:
            dims[0] *= 1.15
            dims[1] *= 1.15
    dims = [round(d, 3) for d in dims]
    volume = round(dims[0] * dims[1] * dims[2] * 1000.0 * (0.785 if style == "roll" else 1.0), 3)
    return dims, volume, lumpy


def _support(w, eid):
    """(kind, surface id) where the item lies: held / surface / floor / container."""
    rec = w.ent(eid)
    loc = rec.get("location")
    if loc == "robot":
        return "held", None
    if loc in w.s["rooms"]:
        return "floor", loc
    if loc in w.s["entities"]:
        parent = w.ent(loc)
        if parent.get("container") and not parent.get("surface"):
            return "container", loc
        return "surface", loc
    return "other", loc


def _surface_dims(w, sid):
    rec = w.ent(sid)
    size = rec.get("surface_size_m") or (rec.get("size_m") or [0.5, 0.5])[:2]
    return float(size[0]), float(size[1])


def _speed(w):
    return w.robot.get("speed_mode", "normal")


def _floor_touch(w, eid, why, amount=0.05):
    M.add_soil(w, eid, "dust", amount)
    m = w.mut(eid)
    m["floor_contact"] = M.now(w)
    w.emit("textile_floor_contact", target=eid, why=why)


def _relocate_on_surface(w, eid, sid):
    """Re-seat an item on its surface after its footprint changed (keep the spot if it still fits)."""
    rec = w.ent(eid)
    if rec.get("relation") != "on" or sid not in w.s["entities"]:
        return
    surface = w.ent(sid)
    if not (surface.get("surface") or surface.get("accepts_items")):
        return
    spot = geometry.surface_spot(w.s, sid, eid, want=rec.get("spos")) if rec.get("spos") is not None else None
    if spot is None:
        spot = geometry.surface_spot(w.s, sid, eid)
    m = w.mut(eid)
    if spot is not None:
        m["spos"] = spot
    else:
        m.pop("spos", None)


def _fold_seconds(ctx):
    return ctx.data.get("seconds", 20.0)


@action("fold", params={"target": "entity", "style": "str", "smooth": {"type": "bool", "default": True}},
        duration=_fold_seconds, energy=0.02, category="physical",
        doc="Fold a dry textile into a style (half, half_half, thirds_half, half_thirds, thirds_thirds, quarters, retail, "
            "file, thirds, crease_half, crease_thirds, fitted_nest, nest, pair_fold, ball, square, suit_fold, roll, gather). "
            "Small items can be folded in hand; anything over ~1 m must lie on a table, bed or counter big enough to hold it. "
            "The folded size depends on the style and on alignment (speed, support, slippery fabric).")
def fold(ctx):
    w = ctx.w
    target, style = ctx.args["target"], ctx.args["style"]
    rec = ctx.obj(target, touch=True)
    require(M.is_textile(rec), "Only textiles can be folded", "argument")
    require(style in STYLES, "Unknown fold style; try one of " + ", ".join(sorted(STYLES)), "argument")
    if ctx.unresolved(target) and M.group_varies(ctx.env, target, allowed_styles):
        # look-alikes that fold differently (fitted vs flat sheet): the style list, a refusal, the time taken or a
        # lumpy result would tell them apart, so the robot first has to see which one it holds (same answer for all)
        raise ActionError("Spread it out and look it over first (inspect it) to see which kind it is and how it folds", "precondition")
    styles = allowed_styles(rec)
    require(style in styles, "{} folds as: {}".format("It" if ctx.realistic else "A {}".format(rec.get("label", target)), ", ".join(styles)), "argument")
    occupant = M.textile_occupant(w, target)
    require(occupant is None, "{} is lying on/under it; leave it".format(occupant), "social")
    require(not rec.get("on_hanger"), "Take it off the hanger first")
    require(not rec.get("paired_with") or style in ("pair_fold", "ball", "file", "thirds", "square"), "Unpair it first")
    support, sid = _support(w, target)
    fw, fl = M.flat_size(rec)
    big = max(fw, fl)
    others = [h for h in w.held_items() if h != target]
    if big > 1.0:
        require(support != "held", "Lay it on a table, bed or counter to fold something this large")
        require(not others, "Both hands must be free to fold something this large", "capability")
    if support == "container":
        raise ActionError("Take it out of {} to fold it".format(sid))     # by id: translated to a handle, never a label
    speed = _speed(w)
    per_fold = {"careful": 0.4, "normal": 0.9, "fast": 2.0}.get(speed, 0.9)
    if M.fabric(rec).get("slippery"):
        per_fold *= 1.6
    if rec.get("fabric") == "terry":
        per_fold += 0.3
    if support == "held" and big > 0.5:
        per_fold *= 1.5
    dragged = False
    if support == "floor":
        dragged = True
    elif support == "surface":
        sw, sl = _surface_dims(w, sid)
        small_s, big_s = sorted((sw, sl))
        small_i, big_i = sorted((fw, fl))
        if small_s < 0.5 * small_i or big_s < 0.5 * big_i:
            per_fold *= 2.0
            if big > 1.2:
                dragged = True
    folds = STYLES[style][3]
    align = round(per_fold * folds, 2) if style not in ("roll", "gather", "nest") else 0.0
    dims, volume, lumpy = fold_dims(rec, style, align)
    m = w.mut(target)
    hidden = []
    for child in w.children(target):
        c = w.ent(child)
        if c.get("relation") in ("on", "under", "in") and not c.get("pocket") and not c.get("part_of_set"):
            hidden.append(child)
    for child in hidden:
        cm = w.mut(child)
        cm["relation"] = "under"
        cm["revealed"] = False
        cm["folded_inside"] = target
        cm.pop("spos", None)
    m = w.mut(target)
    m["folded"] = style
    m["size_m"] = dims
    m["volume_l"] = volume
    m["fold_quality"] = {"align_cm": align, "lumpy": lumpy, "neat": align <= NEAT_LIMIT_CM and not lumpy}
    m["spread_on"] = None
    m.pop("drape_cm", None)
    m["rolled"] = style == "roll"
    m["gathered"] = style == "gather"
    if style == "gather" and M.soil_level(rec, "crumbs") > 0:
        m["contains_crumbs"] = True
    if ctx.args["smooth"] and style not in ("gather", "ball", "roll"):
        m["wrinkled"] = round(float(m.get("wrinkled", 0.0)) * 0.5, 3)
    if rec.get("structured") and style not in ("suit_fold", "half"):
        m["wrinkled"] = max(float(m.get("wrinkled", 0.0)), 0.6)
        M.add_damage(w, target, "deep_creases", 0.3, why="structured garment folded small", stat=False)
    if rec.get("crease") == "pressed" and fold_class(rec) == "bottom" and style not in ("crease_half", "crease_thirds"):
        m = w.mut(target)
        m["double_crease"] = True
    if rec.get("molded_cups") and style != "nest":
        M.add_damage(w, target, "cups_crushed", 1.0, why="molded-cup bra folded instead of nested")
    if style == "ball":
        m = w.mut(target)
        m["elastic_stretch"] = round(float(m.get("elastic_stretch", 0.0)) + 0.1, 3)
    damp = max(float(rec.get("wetness", 0.0)), float(rec.get("_core_wetness", 0.0) or 0.0))
    if damp >= M.DAMP:
        w.mut(target)["folded_damp"] = True
    if dragged:
        _floor_touch(w, target, "folded on/over the floor", 0.05)
    if support == "surface":
        _relocate_on_surface(w, target, sid)
    seconds = 8.0 if big <= 0.5 else (20.0 if big <= 1.0 else (45.0 if big <= 2.0 else 70.0))
    if rec.get("fitted"):
        seconds += 45.0 if style == "fitted_nest" else 10.0
    if ctx.args["smooth"] and float(rec.get("wrinkled", 0.0) or 0.0) > 0.3:
        seconds += 5.0      # smoothing out visible creases (the fabric's hidden crease-proneness does not set the time)
    ctx.data["seconds"] = seconds
    ctx.grant(target, "inspect", ["fold_quality", "wrinkled", "folded"])   # it just smoothed and folded it by hand
    if ctx.realistic:
        # the robot sees its folded block (a camera estimate to ~5 cm) and whether the edges line up, not millimetres
        est = ctx.percept_value(target, "size_m", list(dims)) or list(dims)
        note = "" if align <= NEAT_LIMIT_CM and not lumpy else (" (lumpy)" if lumpy else " (edges not lined up)")
        return "Folded {} ({}) to about {:.2f} x {:.2f} m{}".format(target, style, est[0], est[1], note)
    note = "" if align <= NEAT_LIMIT_CM and not lumpy else (" (lumpy)" if lumpy else " (edges misaligned by {:.1f} cm)".format(align))
    return "Folded {} ({}) to {:.2f} x {:.2f} x {:.3f} m{}".format(target, style, dims[0], dims[1], dims[2], note)


def _knock_items_on(w, surface_id, keep=(), why="knocked over"):
    knocked = []
    for child in w.children(surface_id):
        c = w.ent(child)
        if child in keep or c.get("relation") != "on" or M.is_textile(c) and c.get("spread_on") == surface_id:
            continue
        if c.get("fragile"):
            m = w.mut(child)
            m["broken"] = True
            w.stat("breakages")
            w.stat("damage")
        else:
            w.mut(child)["orientation"] = "on_side"
        knocked.append(child)
    if knocked:
        w.emit("items_knocked", surface=surface_id, items=knocked, why=why)
    return knocked


def _unfold_seconds(ctx):
    return ctx.data.get("seconds", 25.0)


@action("unfold", params={"target": "entity", "destination": "?target", "side": "?str:right|wrong",
                          "offset_cm": "?list", "rotate": {"type": "bool", "default": False}, "hem": "?str:down|up",
                          "cell": "?cell"},
        duration=_unfold_seconds, energy=0.02, category="physical",
        doc="Unfold/spread a textile: shake it out in hand (no destination), spread it on a table or bed (tablecloth, "
            "throw, top sheet) with side right/wrong up, hem down/up, offset_cm [dx, dy] from centred and rotate to turn "
            "it 90 degrees, or lay a mat flat on the floor at cell (side right = backing down).")
def unfold(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = ctx.obj(target, touch=True)
    require(M.is_textile(rec), "Only textiles can be unfolded", "argument")
    dest = ctx.args.get("destination")
    fw, fl = M.flat_size(rec)
    ctx.data["seconds"] = 10.0 if max(fw, fl) <= 1.0 else 30.0
    from .wash import unfold_record
    if not dest or dest == "hand":
        require(ctx.holding(target), "Hold it to shake it out, or name where to spread it")
        m = w.mut(target)
        unfold_record(m)
        m["gathered"] = False
        m["rolled"] = False
        moved = shed_debris(w, target, w.robot["room"], "shook it out")
        outdoor = w.s["rooms"].get(w.robot["room"], {}).get("outdoor")
        return "Shook out {}{}".format(target, "" if not moved else (" (debris fell {})".format("outside" if outdoor else "on the floor")))
    if dest in w.s["rooms"]:
        require(dest == w.robot["room"], "Go to that room first")
        require(ctx.holding(target), "Hold it first")
        cell = ctx.args.get("cell")
        release_from_hands(w, target)
        place_item(ctx, target, dest, cell=cell)
        m = w.mut(target)
        unfold_record(m)
        m["size_m"] = [round(fw, 3), round(fl, 3), float(rec.get("layer_thickness_m", 0.005)) * 2]
        side = ctx.args.get("side") or "right"
        m["side_up"] = side
        m["laid_flat"] = True
        if rec.get("mat") or rec.get("floor_item"):
            m["orientation"] = "flat" if side == "right" else "upside_down"
            _mat_state(w, target)
        else:
            _floor_touch(w, target, "spread on the floor")
        w.log_move(target, "robot", dest)
        return "Laid {} flat on the floor".format(target)
    drec = ctx.obj(dest, touch=True, action="unfold")
    require(drec.get("surface") or drec.get("category") in ("bed", "table", "counter", "seat"), "Spread it on a table, bed or sofa")
    require(ctx.holding(target) or rec.get("location") == dest, "Hold it or have it on that surface first")
    occupant = M.bed_occupant(w, dest) if drec.get("category") == "bed" else None
    require(occupant is None, "{} is in the bed".format(occupant), "social")
    if ctx.holding(target):
        release_from_hands(w, target)
    m = w.mut(target)
    was_folded = bool(rec.get("folded"))
    unfold_record(m)
    m["location"], m["relation"] = dest, "on"
    for key in ("pos", "spos", "shelf", "held_hand"):
        m.pop(key, None)
    m["spread_on"] = dest
    m["size_m"] = [round(fw, 3), round(fl, 3), float(rec.get("layer_thickness_m", 0.002))]
    m["side_up"] = ctx.args.get("side") or "right"
    m["hem"] = ctx.args.get("hem") or "down"
    if was_folded:
        m["wrinkled"] = max(float(m.get("wrinkled", 0.0)), 0.15 * M.wrinkle_prone(rec))
    tw, td = geometry.footprint_m(drec)
    cw, cl = fw, fl
    if (tw >= td) != (cw >= cl):
        cw, cl = cl, cw
    if ctx.args.get("rotate"):
        cw, cl = cl, cw
    offset = ctx.args.get("offset_cm") or [0, 0]
    require(isinstance(offset, list) and len(offset) == 2 and all(isinstance(v, (int, float)) for v in offset), "offset_cm must be [dx, dy]", "argument")
    dx, dy = float(offset[0]), float(offset[1])
    drop = {"left": round((cw - tw) * 50.0 + dx, 1), "right": round((cw - tw) * 50.0 - dx, 1),
            "near": round((cl - td) * 50.0 + dy, 1), "far": round((cl - td) * 50.0 - dy, 1)}
    m["drape_cm"] = drop
    m["drop_diff_cm"] = round(max(abs(drop["left"] - drop["right"]), abs(drop["near"] - drop["far"])), 1)
    shape = rec.get("shape", "rect")
    table_shape = drec.get("shape", "rect")
    m["shape_match"] = (shape == table_shape) or (shape == "square" and table_shape == "rect" and abs(tw - td) < 0.1)
    if min(drop.values()) < 0:
        m["drop_short"] = True
    knocked = _knock_items_on(w, dest, keep=(target,), why="cloth spread under items on the table")
    if drec.get("category") == "bed" and rec.get("bed_role"):
        from .bedding import refresh_bed
        refresh_bed(w, dest)
    w.log_move(target, "robot", dest)
    if ctx.realistic:
        # drops judged by eye, to about 5 cm (a tape measure gives them exactly)
        seen = {k: int(round(v / 5.0) * 5) for k, v in drop.items()}
        msg = "Spread {} on {} (drops about cm: {})".format(target, dest, json.dumps(seen, sort_keys=True))
    else:
        msg = "Spread {} on {} (drops cm: {})".format(target, dest, json.dumps(drop, sort_keys=True))
    if knocked:
        msg += "; knocked over " + ", ".join(knocked)
    return msg


SHED_KINDS = {"crumbs": 1.0, "sand": 1.0, "leaves": 1.0, "litter": 1.0, "dust": 0.6, "pet_hair": 0.5, "hair": 0.5, "lint": 0.3,
              "dirt": 0.4, "pollen": 0.5}


def shed_debris(w, eid, rid, why):
    """Shaking a textile out moves loose debris off it: outdoors it is gone, indoors it lands on the floor."""
    rec = w.ent(eid)
    soil = dict(rec.get("soil") or {})
    moved = {}
    for kind, share in sorted(SHED_KINDS.items()):
        level = float(soil.get(kind, 0.0))
        if level > 0:
            moved[kind] = level * share
            soil[kind] = round(level * (1.0 - share), 4)
            if soil[kind] < 0.0005:
                soil.pop(kind)
    m = w.mut(eid)
    m["soil"] = soil
    m["contains_crumbs"] = False
    insects = bool(rec.get("insects"))
    m["insects"] = False
    room = w.s["rooms"].get(rid or "", {})
    if room and not room.get("outdoor"):
        floor = "floor_" + rid
        if w.has(floor):
            for kind, amount in sorted(moved.items()):
                M.add_soil(w, floor, kind, amount)
        if insects:
            w.emit("insects_released_indoors", target=eid, room=rid)
        if moved:
            w.emit("debris_shaken_indoors", target=eid, room=rid, why=why)
    return moved


def _shed_crumbs(w, eid, rid, why):
    level = M.soil_level(w.ent(eid), "crumbs")
    if level <= 0 or rid not in w.s["rooms"]:
        return
    room = w.s["rooms"][rid]
    floor = "floor_" + rid
    if room.get("outdoor"):
        M.set_soil(w, eid, "crumbs", 0.0)
        w.mut(eid)["contains_crumbs"] = False
        return
    if w.has(floor):
        M.add_soil(w, floor, "crumbs", level)
    M.set_soil(w, eid, "crumbs", 0.0)
    w.mut(eid)["contains_crumbs"] = False
    w.emit("crumbs_spilled", target=eid, room=rid, why=why)


def _is_mat(rec):
    """STATIC predicate: bath mats, doormats and other floor mats of this domain."""
    return bool(rec.get("mat"))


@hook("episode_start")
def derive_mat_ground_layer(w, env):
    """A mat that a scenario set curled or upside down is a trip/slip hazard, one lying flat is a walkable ground
    layer (kernel v2.1 marks other loose portable floor items as `floor_item` hazards)."""
    ents = w.s["entities"]
    for eid in w.static_index("textiles_laundry.mats", _is_mat):
        rec = ents.get(eid)
        if rec is None or rec.get("location") not in w.s["rooms"]:
            continue
        flat = bool(rec.get("laid_flat")) and not rec.get("curled") and rec.get("orientation") != "upside_down"
        hazard = None if flat else ("slip" if rec.get("orientation") == "upside_down" else "trip")
        if bool(rec.get("ground_layer")) != flat or rec.get("floor_hazard") != hazard:
            m = w.mut(eid)
            m["ground_layer"] = flat
            m["floor_hazard"] = hazard


def _mat_state(w, eid):
    rec = w.ent(eid)
    if rec.get("location") not in w.s["rooms"]:
        return
    m = w.mut(eid)
    m["curled"] = not bool(rec.get("laid_flat"))
    if rec.get("orientation") == "upside_down":
        m["floor_hazard"] = "slip"
    elif m["curled"]:
        m["floor_hazard"] = "trip"
    else:
        m["floor_hazard"] = None
    # a mat lying flat right side up is meant to be walked on (kernel v2.1: a ground layer is not a `floor_item`
    # hazard); a curled or upside-down one is a trip/slip hazard that path planning avoids
    m["ground_layer"] = m["floor_hazard"] is None
    room = w.s["rooms"][rec["location"]]
    wet = {tuple(c) for c in room.get("wet_cells", [])}
    cells = set(geometry.footprint(rec))
    if any(c in wet for c in cells):
        m["damp_under"] = True
    blocked = []
    if float((rec.get("size_m") or [0, 0, 0])[2]) >= 0.012 or not rec.get("laid_flat"):
        for cid, conn in sorted(w.s.get("connections", {}).items()):
            door = conn.get("door")
            cell = conn.get("cells", {}).get(rec["location"])
            if door and cell and tuple(cell) in cells and w.has(door) and w.ent(door).get("handle_kind") not in ("sliding", "opener"):
                w.mut(door)["obstructed"] = True
                blocked.append(door)
    w.mut(eid)["_obstructs"] = blocked


@action("roll_garment", params={"target": "entity", "tightness": {"type": "str", "default": "tight", "choices": ["tight", "loose"]}},
        duration=15.0, energy=0.01, category="physical",
        doc="Roll a garment or towel into a tight roll (packing, spa-roll towels). Structured items (blazers, dress shirts, "
            "suit trousers) crease badly when rolled; loose rolls unwind when moved.")
def roll_garment(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = ctx.obj(target, touch=True)
    require(M.is_textile(rec), "Only textiles can be rolled", "argument")
    require(not rec.get("on_hanger"), "Take it off the hanger first")
    fw, fl = M.flat_size(rec)
    require(max(fw, fl) <= 2.0, "Too large to roll; fold it")
    support, sid = _support(w, target)
    require(support != "container", "Take it out first")
    dims, volume, _ = fold_dims(rec, "roll")
    m = w.mut(target)
    m["folded"] = "roll"
    m["rolled"] = True
    m["roll_tight"] = ctx.args["tightness"] == "tight"
    m["size_m"] = dims
    m["volume_l"] = volume
    m["fold_quality"] = {"align_cm": 0.0, "lumpy": False, "neat": ctx.args["tightness"] == "tight"}
    if rec.get("structured"):
        m["wrinkled"] = max(float(m.get("wrinkled", 0.0)), 0.7)
        M.add_damage(w, target, "deep_creases", 0.3, why="structured garment rolled", stat=False)
    if max(float(rec.get("wetness", 0.0)), float(rec.get("_core_wetness", 0.0) or 0.0)) >= M.DAMP:
        w.mut(target)["folded_damp"] = True
    if support == "surface":
        _relocate_on_surface(w, target, sid)
    return "Rolled {} ({})".format(target, ctx.args["tightness"])


@action("pair", params={"target": "entity", "destination": "?entity", "split": {"type": "bool", "default": False}},
        duration=lambda ctx: ctx.data.get("seconds", 5.0), energy=0.005, category="physical",
        doc="Put two socks, shoes or gloves together as a pair (destination joins target and they move as one), or "
            "split=true to separate a pair into a free hand. Matching is the robot's judgement: inspect colour, length, "
            "size, side and brand marks first.")
def pair(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = ctx.obj(target, touch=True)
    require(rec.get("pairable"), "Only socks, shoes and gloves are paired", "argument")
    if ctx.args["split"]:
        partner = rec.get("paired_with")
        require(partner and w.has(partner) and w.ent(partner).get("location") == target, "It is not paired with anything")
        hand = ctx.free_hand()
        require(hand is not None, "Free a hand to separate the pair", "capability")
        pm = w.mut(partner)
        pm.update(location="robot", relation="held", held_hand=hand)
        pm["paired_with"] = None
        w.robot_m()["hands"][hand] = partner
        m = w.mut(target)
        m["paired_with"] = None
        if m.get("single_size_m"):
            m["size_m"] = list(m["single_size_m"])
            m["volume_l"] = float(m.get("single_volume_l", m.get("volume_l", 0.5)))
        if m.get("folded") in ("pair_fold", "ball"):
            from .wash import unfold_record
            unfold_record(m)
        ctx.data["seconds"] = 3.0
        return "Separated {} from {}".format(partner, target)
    dest = ctx.args.get("destination")
    require(dest and dest != target, "Name the item to pair it with", "argument")
    drec = ctx.obj(dest, touch=True)
    require(drec.get("pairable") and drec.get("pair_kind") == rec.get("pair_kind"),
            "Those are not the same kind of item ({} vs {})".format(rec.get("pair_kind"), drec.get("pair_kind")), "argument")
    require(not rec.get("paired_with") and not drec.get("paired_with"), "One of them is already paired")
    require(rec.get("relation") != "attached" and drec.get("relation") != "attached", "Separate the existing pair first")
    if ctx.holding(dest):
        release_from_hands(w, dest)
    single = list(rec.get("size_m") or [0.1, 0.1, 0.1])
    m = w.mut(target)
    m["single_size_m"] = single
    m["single_volume_l"] = float(rec.get("volume_l", 0.5))
    if rec.get("pair_kind") == "shoe":
        m["size_m"] = [round(single[0] * 2 + 0.01, 3), single[1], single[2]]
    else:
        m["size_m"] = [single[0], single[1], round(single[2] * 2, 3)]
    m["volume_l"] = round(float(rec.get("volume_l", 0.5)) + float(drec.get("volume_l", 0.5)), 3)
    m["paired_with"] = dest
    dm = w.mut(dest)
    dm.update(location=target, relation="attached", paired_with=target)
    for key in ("pos", "spos", "shelf", "held_hand"):
        dm.pop(key, None)
    if dm.get("orientation") != m.get("orientation") and m.get("orientation"):
        dm["orientation"] = m["orientation"]
    support, sid = _support(w, target)
    if support == "surface":
        _relocate_on_surface(w, target, sid)
    w.log_move(dest, drec.get("location"), target)
    ctx.data["seconds"] = 4.0 if rec.get("pair_kind") == "shoe" else 6.0
    return "Paired {} with {}".format(target, dest)


@action("turn_inside_out", params={"target": "entity"}, duration=lambda ctx: ctx.data.get("seconds", 8.0), energy=0.005,
        category="physical", inverse=lambda ctx, before: {"action": "turn_inside_out", "target": ctx.args["target"]},
        doc="Turn a garment, sock, pillowcase, duvet cover or cushion cover inside out (or back right side out).")
def turn_inside_out(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = ctx.obj(target, touch=True)
    require(M.is_textile(rec) and rec.get("invertible", True), "That cannot be turned inside out", "argument")
    require(not rec.get("folded"), "Unfold it first")
    require(not rec.get("on_hanger"), "Take it off the hanger first")
    require(not any(w.ent(c).get("relation") == "in" and not w.ent(c).get("pocket") for c in w.children(target)), "Empty it first")
    m = w.mut(target)
    m["inside_out"] = not bool(rec.get("inside_out"))
    m["balled"] = False
    fw, fl = M.flat_size(rec)
    ctx.data["seconds"] = 8.0 if max(fw, fl) < 1.2 else 25.0
    return "{} is now {}".format(target, "inside out" if m["inside_out"] else "right side out")


@action("roll_sleeves", params={"target": "entity"}, duration=8.0, energy=0.004, category="physical",
        inverse=lambda ctx, before: {"action": "unroll_sleeves", "target": ctx.args["target"]},
        doc="Roll up the sleeves (or cuff the legs) of a garment.")
def roll_sleeves(ctx):
    rec = ctx.obj(ctx.args["target"], touch=True)
    require(rec.get("has_sleeves") or rec.get("has_legs"), "It has no sleeves or legs to roll", "argument")
    require(not rec.get("sleeves_rolled") and not rec.get("cuffed"), "Already rolled")
    m = ctx.w.mut(ctx.args["target"])
    if rec.get("has_sleeves"):
        m["sleeves_rolled"] = True
    else:
        m["cuffed"] = True
    return "Rolled up " + ctx.args["target"]


@action("unroll_sleeves", params={"target": "entity"}, duration=8.0, energy=0.004, category="physical",
        inverse=lambda ctx, before: {"action": "roll_sleeves", "target": ctx.args["target"]},
        doc="Unroll rolled sleeves, uncuff pant legs, or unball a balled-up sock so it washes evenly.")
def unroll_sleeves(ctx):
    rec = ctx.obj(ctx.args["target"], touch=True)
    require(rec.get("sleeves_rolled") or rec.get("cuffed") or rec.get("balled"), "Nothing is rolled or balled up")
    m = ctx.w.mut(ctx.args["target"])
    m["sleeves_rolled"] = False
    m["cuffed"] = False
    m["balled"] = False
    return "Unrolled " + ctx.args["target"]


# ---------------------------------------------------------------------------
# hangers (garments), hooks/rings (curtains), clothespins (lines)

def hanger_risk(rec, hrec):
    """Static risks of a garment on a given hanger (applied over time once it hangs)."""
    risk = {}
    mass = float(rec.get("mass_kg", 0.3)) * (1.0 + 1.5 * float(rec.get("wetness", 0.0)))
    knit = M.is_knit(rec) or rec.get("knit_heavy")
    method = rec.get("hang_method")
    if knit and method != "bar_fold":
        risk["stretch_per_day"] = round(0.01 * (3.0 if mass > 0.5 else 1.0), 4)
    slippery = M.fabric(rec).get("slippery") or rec.get("strapless") or rec.get("slips_on_hangers")
    grippy = hrec.get("non_slip") or hrec.get("clips") or (rec.get("strappy") and hrec.get("notches") and method == "straps")
    if slippery and not grippy:
        risk["slip_after_s"] = 3600.0 if rec.get("strapless") else 3.0 * 3600.0
    if method == "bar_fold" and fold_class(rec) == "bottom" and not hrec.get("non_slip"):
        risk["slip_after_s"] = 6.0 * 3600.0
    limit = float(hrec.get("hang_load_kg", 1.5))
    if mass > limit:
        risk["bump_after_s"] = 3600.0
        risk["bends_hanger"] = True
    elif mass > 1.0 and not hrec.get("wide_shoulder") and fold_class(rec) == "top":
        risk["bump_after_s"] = 6.0 * 3600.0
    return risk


def _hang_seconds(ctx):
    return ctx.data.get("seconds", 10.0)


@action("hang_on_hanger", params={"target": "entity", "hanger": "entity",
                                  "method": "?str:hem|neck|clips|bar_fold|straps|loop",
                                  "button_top": {"type": "bool", "default": False},
                                  "facing": "?str:left|right", "along_crease": {"type": "bool", "default": True},
                                  "side": "?str:left|right", "lining": "?str:window|room",
                                  "hooks": "?int", "spacing_cm": "?number"},
        duration=_hang_seconds, energy=0.01, category="physical",
        doc="Put a garment on a hanger (method hem = through the hem, neck = through the collar, clips, bar_fold over "
            "the bar, straps on notches; button_top fastens the top button; facing matches the closet). For curtains "
            "and shower curtains, hanger is the rod: side, lining (toward window/room), hooks/rings used and spacing_cm.")
def hang_on_hanger(ctx):
    w = ctx.w
    target, hanger = ctx.args["target"], ctx.args["hanger"]
    rec = ctx.obj(target, touch=True)
    hrec = ctx.obj(hanger, touch=True, action="hang_on_hanger")
    if rec.get("curtain") or rec.get("shower_curtain"):
        return _hang_curtain(ctx, target, rec, hanger, hrec)
    require(M.is_textile(rec), "Only garments go on hangers", "argument")
    require(hrec.get("category") == "hanger", "That is not a clothes hanger", "argument")
    require(not hrec.get("broken"), "That hanger is bent/broken")
    require(not rec.get("on_hanger"), "It is already on a hanger")
    require(hrec.get("location") != target and not hrec.get("holding_garment"), "That hanger is in use")
    require(ctx.holding(target) or ctx.holding(hanger), "Hold the garment or the hanger")
    require(not rec.get("folded"), "Unfold it first")
    method = ctx.args.get("method") or ("clips" if hrec.get("clips") and fold_class(rec) == "bottom" else ("bar_fold" if fold_class(rec) == "bottom" else "hem"))
    if method == "clips":
        require(hrec.get("clips"), "That hanger has no clips")
    if method == "straps":
        require(rec.get("strappy"), "It has no straps")
    m = w.mut(target)
    m["hang_method"] = method
    if method == "neck" and rec.get("narrow_neck"):
        M.add_damage(w, target, "collar_stretched", 0.4, why="hanger forced through a narrow neck opening")
    if method == "bar_fold" and rec.get("crease") == "pressed" and not ctx.args["along_crease"]:
        m = w.mut(target)
        m["double_crease"] = True
    if ctx.holding(hanger):
        release_from_hands(w, hanger)
    hm = w.mut(hanger)
    hm.update(location=target, relation="attached")
    for key in ("pos", "spos", "shelf", "held_hand"):
        hm.pop(key, None)
    hm["holding_garment"] = target
    m = w.mut(target)
    m["on_hanger"] = hanger
    m["hanger_kind"] = hrec.get("hanger_kind")
    m["needs_hanger"] = bool(rec.get("needs_hanger", True))
    if rec.get("has_buttons") or rec.get("buttons"):
        m["top_button"] = bool(ctx.args["button_top"])
    m["facing"] = ctx.args.get("facing") or "left"
    m["_hang_risk"] = hanger_risk(w.ent(target), hrec)
    if not m["_hang_risk"]:
        m.pop("_hang_risk")
    if m.get("relation") == "hung":
        m["_hung_at"] = M.now(w)
    w.log_move(hanger, hrec.get("location"), target)
    ctx.data["seconds"] = 10.0 + (4.0 if ctx.args["button_top"] else 0.0)
    return "{} is on {} ({})".format(target, hanger, method)


def _hang_curtain(ctx, target, rec, rod, rrec):
    w = ctx.w
    require(rrec.get("curtain_rod") or rrec.get("shower_rod") or rrec.get("category") in ("curtain_rod", "shower_rod"), "Hang it on a curtain or shower rod")
    require(ctx.holding(target), "Hold the panel first")
    total = int(rec.get("hooks_total", rec.get("grommets", 0)) or 0)
    used = int(ctx.args.get("hooks") if ctx.args.get("hooks") is not None else total)
    require(0 < used <= max(1, total) or total == 0, "Use between 1 and {} hooks/rings".format(total), "argument")
    if rec.get("hooks_total") and int(rec.get("hooks_attached", 0)) < used:
        raise ActionError("Only {} hooks are attached to the panel; reattach the hooks first".format(rec.get("hooks_attached", 0)))
    release_from_hands(w, target)
    m = w.mut(target)
    m["location"], m["relation"] = rod, "hung"
    for key in ("pos", "spos", "shelf", "held_hand"):
        m.pop(key, None)
    m["rod"] = rod
    m["side"] = ctx.args.get("side") or rec.get("side")
    m["lining_facing"] = ctx.args.get("lining") or "room"
    m["hooks_used"] = used
    spacing = ctx.args.get("spacing_cm")
    m["hook_spacing_even"] = spacing is None or abs(float(spacing) - float(rec.get("hook_spacing_cm", spacing or 15))) <= 2.0
    if rec.get("grommets"):
        m["rings_attached"] = used
    rod_h = float(geometry.height_m(w.s, rod)) if rod in w.s["entities"] else 2.2
    m["hem_height_m"] = round(rod_h - float(rec.get("drop_m", 2.1)) - (0.0 if used == total else 0.03), 3)
    m["sagging"] = used < total
    if rec.get("liner") is not None:
        m["inside_tub"] = bool(rec.get("liner")) and ctx.args.get("side") != "room"
    if float(rec.get("wetness", 0.0)) > 0:
        m["drying_mode"] = "hung"
    ctx.data["seconds"] = 8.0 + 4.0 * used
    w.log_move(target, "robot", rod)
    return "Hung {} on {} with {} of {} hooks/rings".format(target, rod, used, total)


@action("take_off_hanger", params={"target": "entity", "destination": "?entity"},
        duration=lambda ctx: ctx.data.get("seconds", 6.0), energy=0.005, category="physical",
        doc="Take a garment off its hanger (the hanger goes to a free hand or destination); remove a curtain panel from "
            "its rod (to a free hand) or the hooks from a held panel (into destination bag); or collect the "
            "clothespins left on a clothesline into the pin bag (destination).")
def take_off_hanger(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = ctx.obj(target, touch=True)
    dest = ctx.args.get("destination")
    if rec.get("clothesline"):
        require(dest and w.ent(dest).get("clothespins"), "Name the clothespin bag to put the pins in", "argument")
        ctx.obj(dest, touch=True)
        pins = int(rec.get("pins_on_line", 0))
        require(pins > 0, "No pins left on the line")
        w.mut(target)["pins_on_line"] = 0
        bag = w.mut(dest)
        bag["count"] = int(bag.get("count", 0)) + pins
        ctx.data["seconds"] = 2.0 * pins
        return "Collected {} clothespins into {}".format(pins, dest)
    if rec.get("curtain") or rec.get("shower_curtain"):
        if rec.get("relation") == "hung":
            hand = ctx.free_hand()
            require(hand is not None, "Free a hand to take the panel down", "capability")
            check_handling(ctx, target)
            rod = rec.get("location")
            m = w.mut(target)
            m["last_rod"] = rod
            m.update(location="robot", relation="held", held_hand=hand)
            m["rod"] = None
            if rec.get("grommets"):
                m["rings_attached"] = 0
            w.robot_m()["hands"][hand] = target
            w.log_move(target, rod, "robot")
            ctx.data["seconds"] = 10.0 + 2.0 * int(rec.get("hooks_used", rec.get("grommets", 4)) or 4)
            return "Took {} down from {}".format(target, rod)
        hooks = int(rec.get("hooks_attached", 0))
        require(hooks > 0, "No hooks are attached to the panel")
        m = w.mut(target)
        m["hooks_attached"] = 0
        if dest:
            drec = ctx.obj(dest, touch=True)
            require(drec.get("container") or drec.get("accepts_items"), "Put the hooks in a bag or box")
            dm = w.mut(dest)
            dm["hook_count"] = int(dm.get("hook_count", 0)) + hooks
            ctx.data["seconds"] = 3.0 * hooks
            return "Removed {} hooks from {} into {}".format(hooks, target, dest)
        m["hooks_lost"] = int(m.get("hooks_lost", 0)) + hooks
        rid = w.robot["room"]
        floor = "floor_" + rid
        if w.has(floor):
            fm = w.mut(floor)
            fm["loose_hooks"] = int(fm.get("loose_hooks", 0)) + hooks
            fm["floor_hazard"] = fm.get("floor_hazard") or None
        ctx.data["seconds"] = 3.0 * hooks
        return "Pulled {} hooks off {}; they dropped on the floor".format(hooks, target)
    hanger = rec.get("on_hanger")
    require(hanger and w.has(hanger), "It is not on a hanger")
    require(rec.get("relation") != "hung", "Lift it off the rod first (pick it up)")
    hm = w.mut(hanger)
    hm["holding_garment"] = None
    if dest:
        hm["location"], hm["relation"] = target, "attached"
        place_item(ctx, hanger, dest)
    else:
        hand = ctx.free_hand()
        require(hand is not None, "Free a hand for the hanger or name where to put it", "capability")
        hm = w.mut(hanger)
        hm.update(location="robot", relation="held", held_hand=hand)
        w.robot_m()["hands"][hand] = hanger
    m = w.mut(target)
    m["on_hanger"] = None
    m["hanger_kind"] = None
    m.pop("_hang_risk", None)
    m["_hung_at"] = None
    w.log_move(hanger, target, dest or "robot")
    ctx.data["seconds"] = 6.0
    return "Took {} off {}".format(target, hanger)


# ---------------------------------------------------------------------------
# carrying loose bedding

@after("move", order=60)
def carrying_textiles(ctx, result):
    w = ctx.w
    held = [h for h in w.held_items() if M.is_textile(w.ent(h))]
    if not held:
        return
    conn = w.s.get("connections", {}).get(ctx.data.get("connection") or "", {})
    stairs = conn.get("kind") == "stairs"
    trailing = [h for h in held if not w.ent(h).get("folded") and not w.ent(h).get("gathered") and not w.ent(h).get("rigid")
                and max(M.flat_size(w.ent(h))) >= 2.0]
    volume = sum(float(w.ent(h).get("loose_volume_l", w.ent(h).get("volume_l", 1.0))) for h in held if not w.ent(h).get("folded"))
    if stairs and trailing:
        for h in trailing:
            _floor_touch(w, h, "a loose sheet trailed down the stairs while carried", 0.03)
        w.stat("safety_violations")
        w.emit("trailing_textile_on_stairs", items=trailing)
    if stairs and volume >= 40.0 and (ctx.args.get("speed") or w.robot.get("speed_mode")) != "careful":
        w.stat("safety_violations")
        w.emit("view_blocked_on_stairs", volume_l=round(volume, 1))


# ---------------------------------------------------------------------------
# hooks on core place / pick_up

def _stack_root(w, dest):
    """For a placement on a folded textile, the first non-textile ancestor (container/shelf) and the stack."""
    current, stack = dest, []
    while current in w.s["entities"] and M.is_textile(w.ent(current)) and w.ent(current).get("relation") in ("on", "in"):
        stack.append(current)
        current = w.ent(current).get("location")
    return current, stack


@before("place", order=55)
def textile_stack_fit(ctx):
    w = ctx.w
    target, dest = ctx.args.get("target"), ctx.args.get("destination")
    if not (isinstance(target, str) and target in w.s["entities"] and isinstance(dest, str) and dest in w.s["entities"]):
        return
    rec = w.ent(target)
    drec = w.ent(dest)
    if M.is_textile(drec) and (ctx.args.get("relation") in (None, "on")) and not drec.get("container"):
        root, stack = _stack_root(w, dest)
        if root in w.s["entities"]:
            rrec = w.ent(root)
            capacity = float(rrec.get("capacity_l", 0) or 0)
            if capacity > 0 and (rrec.get("container") or rrec.get("openable")):
                used = sum(float(w.ent(d).get("volume_l", 0.0)) for d in w.descendants(root)
                           if w.ent(d).get("relation") not in ("part", "attached"))
                require(used + float(rec.get("volume_l", 0.5)) <= capacity + 1e-9, "No room left; the stack would overfill it")
                inner = rrec.get("inner_size_m")
                height = sum(float((w.ent(s).get("size_m") or [0, 0, 0.02])[2]) for s in stack) + float((rec.get("size_m") or [0, 0, 0.02])[2])
                limit = None
                heights = rrec.get("shelf_heights")
                base_item = stack[-1] if stack else dest
                shelf = w.ent(base_item).get("shelf")
                if heights and shelf is not None and int(shelf) + 1 < len(heights):
                    limit = float(heights[int(shelf) + 1]) - float(heights[int(shelf)]) - 0.02
                elif inner:
                    limit = float(inner[2])
                if limit is not None:
                    if ctx.realistic:
                        # trying it shows the stack does not fit; the exact heights would need a tape measure
                        require(height <= limit + 1e-9, "The stack would be too tall for the space (the drawer/shelf would not close)")
                    require(height <= limit + 1e-9, "The stack would be {:.2f} m tall; only {:.2f} m clearance (the drawer/shelf would not close)".format(height, limit))
    # loose items on a crushable textile/hat are checked after placement


@before("place", order=56)
def rods_need_hangers(ctx):
    w = ctx.w
    target, dest = ctx.args.get("target"), ctx.args.get("destination")
    if ctx.args.get("relation") != "hung" or not (isinstance(dest, str) and dest in w.s["entities"]) or target not in w.s["entities"]:
        return
    drec, rec = w.ent(dest), w.ent(target)
    if drec.get("type") in ("closet", "wardrobe", "closet_rod", "garment_rack") and M.is_textile(rec) and rec.get("category") == "garment":
        require(rec.get("on_hanger"), "Put it on a hanger to hang it on a rod")


@before("pick_up", order=55)
def textile_occupied(ctx):
    target = ctx.args.get("target")
    w = ctx.w
    if not (isinstance(target, str) and target in w.s["entities"]):
        return
    rec = w.ent(target)
    if not (M.is_textile(rec) or rec.get("pairable")):
        return
    occupant = M.textile_occupant(w, target)
    require(occupant is None, "{} is lying on/under it; do not pull it away".format(occupant), "social")
    if rec.get("worn_by"):
        raise ActionError("Someone is wearing it", "social")


@after("place", order=56)
def textile_placed(ctx, result):
    w = ctx.w
    target = ctx.args.get("target")
    if not (isinstance(target, str) and target in w.s["entities"]):
        return
    rec = w.ent(target)
    loc = rec.get("location")
    # floor contact
    if loc in w.s["rooms"] and (M.is_textile(rec) and not rec.get("floor_item") and not rec.get("mat")):
        room = w.s["rooms"][loc]
        floor = "floor_" + loc
        amount = 0.04 + (0.5 * M.soil_total(w.ent(floor)) if w.has(floor) else 0.0)
        if room.get("outdoor"):
            terrain = room.get("terrain")
            M.add_soil(w, target, "mud" if (w.s.get("weather") or {}).get("condition") in ("rain", "storm", "drizzle") and terrain in ("grass", "soil") else "dirt", 0.15)
        _floor_touch(w, target, "set down on the floor", min(0.3, amount))
    if (rec.get("mat") or rec.get("floor_item")) and loc in w.s["rooms"]:
        _mat_state(w, target)
    if M.is_textile(rec) and loc in w.s["rooms"] and w.s["rooms"][loc].get("outdoor"):
        weather = w.s.get("weather") or {}
        if weather.get("condition") in ("rain", "storm", "drizzle", "showers", "snow") or w.s["rooms"][loc].get("wet_cells") or rec.get("dewy_ground"):
            w.mut(target)["wetness"] = round(min(1.0, float(rec.get("wetness", 0.0)) + 0.3), 4)
    # muddy footwear soils whatever it is put next to
    if rec.get("pair_kind") == "shoe" and M.soil_level(rec, "mud") + M.soil_level(rec, "dirt") >= 0.2 and loc in w.s["entities"]:
        for other in w.children(loc):
            orec = w.ent(other)
            if other != target and other != rec.get("paired_with") and M.is_textile(orec) and not w.ent(loc).get("drip_safe"):
                M.add_soil(w, other, "mud", 0.08)
    if loc in w.s["entities"]:
        parent = w.ent(loc)
        # crushing structured items (hats, molded bras, structured bags, boot shafts)
        if float(rec.get("mass_kg", 0.0)) >= 0.25:
            victims = []
            if parent.get("crushable") and rec.get("relation") == "on":
                victims.append(loc)
            if rec.get("relation") == "in":
                victims.extend(c for c in w.children(loc) if c != target and w.ent(c).get("crushable") and not w.ent(c).get("crushed"))
            for victim in sorted(set(victims)):
                w.mut(victim)["crushed"] = True
                M.add_damage(w, victim, "crushed", 0.5, why="heavier item put on top of it")
        if rec.get("relation") == "hung":
            m = w.mut(target)
            m["_hung_at"] = M.now(w)
            if M.is_textile(rec) and "_hang_risk" not in rec and not rec.get("on_hanger"):
                if M.is_knit(rec) and fold_class(rec) == "top":
                    m["_hang_risk"] = {"stretch_per_day": 0.02}
                elif float(rec.get("mass_kg", 0.3)) * (1.0 + float(rec.get("wetness", 0.0))) > 1.5 and rec.get("category") == "garment":
                    m["_hang_risk"] = {"stretch_per_day": 0.03}     # a heavy coat hung by its loop on a hook
            limit = parent.get("max_load_kg", 10.0 if parent.get("category") == "hooks" and not parent.get("type") in ("closet_rod",) else None)
            if limit is not None:
                hung = [c for c in w.children(loc) if w.ent(c).get("relation") == "hung"]
                total = sum(float(w.ent(c).get("mass_kg", 0.3)) * (1.0 + float(w.ent(c).get("wetness", 0.0))) for c in hung)
                if total > float(limit):
                    pm = w.mut(loc)
                    pm["broken"] = True
                    rid = M.room_of(w, loc)
                    cell = list(M.anchor_cell(w, loc) or (0, 0))
                    for c in hung:
                        cm = w.mut(c)
                        cm["location"], cm["relation"], cm["pos"] = rid, "on", cell
                        cm["_hung_at"] = None
                        _floor_touch(w, c, "hooks pulled out of the wall")
                    w.stat("damage")
                    w.emit("hooks_pulled_out", target=loc, load_kg=round(total, 2))
                    return
            length = float(rec.get("hang_length_m", 0.0) or 0.0)
            if length:
                clearance = geometry.height_m(w.s, target) - length
                w.mut(target)["hem_on_floor"] = clearance < 0.02
            slots = int(parent.get("hang_slots", 0) or 0)
            used = sum(1 for c in w.children(loc) if w.ent(c).get("relation") == "hung")
            if slots and used >= max(2, int(math.ceil(slots * 0.9))):
                for c in w.children(loc):
                    if w.ent(c).get("relation") == "hung" and M.is_textile(w.ent(c)):
                        cm = w.mut(c)
                        cm["wrinkled"] = round(max(float(cm.get("wrinkled", 0.0)), 0.35 * M.wrinkle_prone(cm)), 3)
                        cm["crowded"] = True
        if parent.get("category") == "bed" and parent.get("crib") or parent.get("type") == "crib" or "crib" in (parent.get("is_a") or []):
            if rec.get("loose_bedding"):
                w.stat("safety_violations")
                w.emit("loose_bedding_in_crib", target=target, crib=loc)
        if parent.get("category") == "bed" and rec.get("bed_role"):
            from .bedding import refresh_bed
            refresh_bed(w, loc)


@after("pick_up", order=56)
def textile_lifted(ctx, result):
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.ent(target)
    before = None
    for entry in reversed(w.s.get("logs", {}).get("moves", [])):
        if entry.get("entity") == target:
            before = entry.get("from")
            break
    m = None
    if rec.get("spread_on"):
        surface = rec["spread_on"]
        m = w.mut(target)
        m["spread_on"] = None
        m.pop("drape_cm", None)
        if surface in w.s["entities"]:
            _knock_items_on(w, surface, keep=(target,), why="cloth pulled out from under items")
        if M.soil_level(rec, "crumbs") > 0 and not rec.get("gathered"):
            _shed_crumbs(w, target, M.room_of(w, surface) or w.robot["room"], "cloth lifted without gathering the corners")
        if before in w.s["entities"] and w.ent(before).get("category") == "bed":
            from .bedding import refresh_bed
            refresh_bed(w, before)
    if rec.get("soaking"):
        m = w.mut(target)
        m["soaking"] = None
        m["wetness"] = 1.0
    if rec.get("drying_mode"):
        m = w.mut(target)
        if rec.get("pins") and before in w.s["entities"] and w.ent(before).get("clothesline"):
            line = w.mut(before)
            line["pins_on_line"] = int(line.get("pins_on_line", 0)) + int(rec.get("pins", 0))
        m["drying_mode"] = None
        m["pins"] = 0
        m["rack_side"] = None
        m["_dried_on"] = None
        if isinstance(m.get("secured"), dict) and m["secured"].get("by") == "clothespins":
            m.pop("secured", None)
    if rec.get("_hung_at") is not None:
        w.mut(target)["_hung_at"] = None
    rec = w.ent(target)
    if M.is_textile(rec) and not rec.get("folded") and not rec.get("rigid") and rec.get("loose_size_m") and rec.get("pair_kind") != "shoe":
        if list(rec.get("size_m") or []) != list(rec["loose_size_m"]):
            m = w.mut(target)
            m["size_m"] = list(rec["loose_size_m"])
            if rec.get("loose_volume_l"):
                m["volume_l"] = float(rec["loose_volume_l"])
        if rec.get("laid_flat"):
            w.mut(target)["laid_flat"] = False
        if rec.get("mat") and rec.get("ground_layer"):
            w.mut(target)["ground_layer"] = False
    if rec.get("folded") == "roll" and not rec.get("roll_tight", True):
        from .wash import unfold_record
        unfold_record(w.mut(target))
        w.mut(target)["rolled"] = False
    if rec.get("bed_role") and before in w.s["entities"]:
        from .bedding import lifted_from_bed
        lifted_from_bed(w, target, before)
    if rec.get("_obstructs"):
        for door in rec["_obstructs"]:
            if w.has(door) and w.ent(door).get("obstructed"):
                w.mut(door)["obstructed"] = False
        w.mut(target)["_obstructs"] = []
