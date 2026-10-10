"""Beds and bedding layers: dress, strip, make, tuck, fluff (fidelity G / P-approx).

A bed (core type bed/crib) carries bed_size (twin/full/queen/king/crib) and mattress_depth_cm.
Bedding items lie on the bed (relation on, no spos) with a bed_role and a stack_pos:
protector < fitted < flat < blanket < duvet < throw; pillows sit at the head with spos.
Quality is real state, not a flag: fitted/protector corners_seated (0-4; a smaller sheet or a
crosswise one cannot seat all four; a shallow pocket pops off after a night or when someone
lies down), face_up (finished/back), flat-sheet head (wide_hem), overhang offsets, tucked edges
and hospital corners, duvet corners_matched and fill_evenness inside its cover, pillowcase fit,
wrinkles per layer, items hidden in the bedding (stripping or making the bed with them inside
buries them or sends them to the laundry). Beds with someone lying in them are refused.
bed_state (visible on the bed) summarises the layers for authors and agents.
"""
from ...kernel import geometry
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ..core import release_from_hands, place_item
from . import model as M
from .dry import TEXTILE_TICKERS

LAYER_ORDER = {"protector": 0, "fitted": 1, "flat": 2, "blanket": 3, "duvet": 4, "throw": 5}
SIZES = ["crib", "twin", "full", "queen", "king"]
SIZE_RANK = {s: i for i, s in enumerate(SIZES)}


def layer_of(rec):
    role = rec.get("bed_role")
    if role in ("duvet_cover",):
        return "duvet" if rec.get("filled_with") else None
    if role in ("comforter", "duvet_insert"):
        return "duvet"
    if role in ("quilt", "blanket"):
        return "blanket"
    if role in LAYER_ORDER:
        return role
    return None


def bed_layers(w, bed):
    layers = []
    for child in w.children(bed):
        rec = w.ent(child)
        layer = layer_of(rec)
        if layer and rec.get("relation") == "on" and rec.get("spread_on") == bed:
            layers.append((int(rec.get("stack_pos", 0) or 0), child, layer))
    return sorted(layers)


def pillows_on(w, bed):
    return [c for c in w.children(bed) if w.ent(c).get("bed_role") in ("pillow", "decor_pillow", "euro_pillow")]


def refresh_bed(w, bed):
    if bed not in w.s["entities"]:
        return
    layers = bed_layers(w, bed)
    names = [layer for _, _, layer in layers]
    order_ok = all(LAYER_ORDER[a] <= LAYER_ORDER[b] for a, b in zip(names, names[1:]))
    fitted = next((e for _, e, l in layers if l == "fitted"), None)
    duvet = next((e for _, e, l in layers if l == "duvet"), None)
    flat = next((e for _, e, l in layers if l == "flat"), None)
    wrinkle = max([float(w.ent(e).get("wrinkled", 0.0)) for _, e, _ in layers] or [0.0])
    hidden = []
    for _, e, _ in layers:
        for child in w.children(e):
            c = w.ent(child)
            if c.get("relation") in ("under", "in") and not c.get("part_of_set") and not M.is_textile(c) or c.get("buried_in_bed") == bed:
                hidden.append(child)
    pillows = pillows_on(w, bed)
    state = {"layers": names, "order_ok": order_ok,
             "fitted_corners": int(w.ent(fitted).get("corners_seated", 0)) if fitted else 0,
             "wrinkle": round(wrinkle, 3),
             "duvet_offset_cm": round(abs(float(w.ent(duvet).get("offset_cm", 0.0))), 1) if duvet else None,
             "flat_offset_cm": round(abs(float(w.ent(flat).get("offset_cm", 0.0))), 1) if flat else None,
             "pillows": len(pillows), "pillows_cased": sum(1 for p in pillows if w.ent(p).get("case")),
             "items_in_bedding": sorted(set(hidden))}
    rec = w.ent(bed)
    if rec.get("bed_state") != state:
        m = w.mut(bed)
        m["bed_state"] = state
        if rec.get("made") and (not names or wrinkle > 0.3):
            m["made"] = False


def lifted_from_bed(w, item, bed):
    rec = w.ent(item)
    m = w.mut(item)
    removed_pos = rec.get("stack_pos")
    m["stack_pos"] = None
    m["corners_seated"] = 0 if rec.get("bed_role") in ("fitted", "protector") else rec.get("corners_seated")
    m["spread_on"] = None
    m["_pop_at"] = None
    m["tucked"] = []
    m["hospital_corners"] = 0
    if rec.get("tucked") and rec.get("bed_role") in ("flat", "blanket"):
        M.add_damage(w, item, "corner_torn", 0.2, why="tucked sheet pulled out without untucking")
    if removed_pos is not None:
        rid = M.room_of(w, bed)
        for _, e, layer in bed_layers(w, bed):
            if int(w.ent(e).get("stack_pos", 0) or 0) > int(removed_pos):
                em = w.mut(e)
                em["wrinkled"] = round(max(float(em.get("wrinkled", 0.0)), 0.5), 3)
                if layer in ("duvet", "blanket", "throw", "flat"):
                    # pulled off together with the layer underneath it
                    em["location"], em["relation"] = rid, "on"
                    em["pos"] = list(M.anchor_cell(w, bed) or (0, 0))
                    for key in ("spos", "spread_on", "stack_pos", "on_bed"):
                        em.pop(key, None)
                    M.add_soil(w, e, "dust", 0.05)
                    w.mut(e)["floor_contact"] = M.now(w)
                    w.emit("bedding_dragged_off", target=e, bed=bed)
    if bed in w.s["entities"] and w.ent(bed).get("made"):
        w.mut(bed)["made"] = False
    refresh_bed(w, bed)


def _occupied(w, bed):
    who = M.bed_occupant(w, bed)
    require(who is None, "{} is in the bed".format(who), "social")


def _is_bed(rec):
    return rec.get("category") == "bed" or M.is_kind(rec, "bed", "crib")


def _bed_size(rec):
    return rec.get("bed_size") or ("crib" if M.is_kind(rec, "crib") else ("twin" if float((rec.get("size_m") or [1.6])[0]) < 1.2 else "queen"))


def _dress_seconds(ctx):
    return ctx.data.get("seconds", 60.0)


@action("dress_bed", params={"target": "entity", "destination": "entity", "face_up": "?str:finished|back",
                             "orientation": "?str:lengthwise|crosswise", "head": "?str:wide_hem|narrow_hem",
                             "corners": {"type": "int", "default": 4, "min": 0, "max": 4},
                             "offset_cm": {"type": "number", "default": 0.0, "min": -100, "max": 100},
                             "method": "?str:inside_out|stuff|invert", "ties": {"type": "bool", "default": True},
                             "close": {"type": "bool", "default": True}, "open_end": "?str:away_from_door|toward_door|center",
                             "zip_side": "?str:back|front|bottom"},
        duration=_dress_seconds, energy=0.03, category="physical",
        doc="Put bedding on: a mattress protector, fitted sheet, flat (top) sheet, blanket, duvet or throw onto a bed "
            "(face_up finished|back, orientation lengthwise|crosswise, head wide_hem|narrow_hem for top sheets, corners "
            "seated 0-4, offset_cm from centred); a pillowcase or pillow protector onto a pillow; a duvet cover onto a "
            "duvet insert (method inside_out|stuff, ties, close); a cushion cover onto its insert (zip_side).")
def dress_bed(ctx):
    w = ctx.w
    target, dest = ctx.args["target"], ctx.args["destination"]
    rec = ctx.obj(target, touch=True)
    drec = ctx.obj(dest, touch=True, action="dress_bed")
    role = rec.get("bed_role") or rec.get("cover_role")
    require(role, "That is not bedding or a cover", "argument")
    require(not rec.get("folded") or rec.get("folded") is False, "Unfold it first")
    if M.unresolved(ctx.env, target) and M.group_varies(ctx.env, target, _dressing_key):
        # look-alikes that go on differently (fitted vs flat sheet, pillowcase vs sham vs protector): which arguments
        # are asked for, which refusal comes back and what the result says would identify them; the robot first has
        # to see which one it holds (the same answer for every member)
        raise ActionError("Unfold it and look it over first (inspect it) to see which kind it is", "precondition")
    if _is_bed(drec):
        return _dress_layer(ctx, target, rec, dest, drec, role)
    if role in ("pillowcase", "pillow_protector", "sham", "cushion_cover"):
        return _dress_cover(ctx, target, rec, dest, drec, role)
    if role == "duvet_cover":
        return _dress_duvet(ctx, target, rec, dest, drec)
    raise ActionError("Put it on a bed" if ctx.realistic else "Put a {} on a bed".format(role), "argument")


def _dressing_key(rec):
    """What decides how a piece of bedding is put on (its role and size)."""
    return (rec.get("bed_role"), rec.get("cover_role"), rec.get("bed_size"), rec.get("pillow_size"))


def _dress_layer(ctx, target, rec, bed, brec, role):
    w = ctx.w
    _occupied(w, bed)
    layer = layer_of(rec)
    require(layer is not None, "Fill the duvet cover first" if role == "duvet_cover" else "That does not go on a bed as a layer", "precondition")
    require(ctx.holding(target), "Hold it first")
    others = [h for h in w.held_items() if h != target]
    require(not others, "Both hands are needed to spread bedding", "capability")
    layers = bed_layers(w, bed)
    require(not any(l == layer for _, _, l in layers), "There is already a {} on the bed; strip it first".format(layer))
    if layer in ("protector", "fitted"):
        above = [e for _, e, l in layers if LAYER_ORDER[l] > LAYER_ORDER[layer] and l != "protector"]
        require(not above or layer == "protector", "Remove the upper bedding before putting on a {}".format(layer))
        require(ctx.args.get("face_up"), "Say which face goes up (face_up finished|back)", "argument")
        require(ctx.args.get("orientation"), "Say how it is oriented (orientation lengthwise|crosswise)", "argument")
    if layer == "flat":
        require(ctx.args.get("face_up") and ctx.args.get("head"), "Say face_up (finished|back) and head (wide_hem|narrow_hem) for a top sheet", "argument")
    bed_size = _bed_size(brec)
    item_size = rec.get("bed_size") or bed_size
    rank_diff = SIZE_RANK.get(item_size, 3) - SIZE_RANK.get(bed_size, 3)
    release_from_hands(w, target)
    m = w.mut(target)
    from .wash import unfold_record
    unfold_record(m)
    m["location"], m["relation"] = bed, "on"
    for key in ("pos", "spos", "shelf", "held_hand"):
        m.pop(key, None)
    m["spread_on"] = bed
    fw, fl = M.flat_size(rec)
    m["size_m"] = [round(fw, 3), round(fl, 3), float(rec.get("layer_thickness_m", 0.002))]
    m["stack_pos"] = max([p for p, _, _ in layers] or [0]) + 1
    m["face_up"] = ctx.args.get("face_up") or "finished"
    m["orientation"] = ctx.args.get("orientation") or "lengthwise"
    m["offset_cm"] = float(ctx.args.get("offset_cm") or 0.0)
    m["tucked"] = list(m.get("tucked") or [])
    m["wrinkled"] = round(max(0.15, float(rec.get("wrinkled", 0.0)) * 0.6), 3)
    seconds = 45.0
    if layer in ("protector", "fitted"):
        corners = int(ctx.args.get("corners", 4))
        cap = 4
        if rank_diff < 0:
            cap = 2
        if m["orientation"] == "crosswise":
            cap = min(cap, 2)
        seated = min(corners, cap)
        m["corners_seated"] = seated
        m["loose_fit"] = rank_diff > 0
        if rank_diff > 0:
            m["wrinkled"] = 0.6
        depth = float(brec.get("mattress_depth_cm", 30.0))
        pocket = float(rec.get("pocket_depth_cm", 35.0))
        if seated < 4 or pocket < depth - 2.0:
            m["_pop_at"] = M.now(w) + 6 * 3600.0
        seconds = 80.0 + (40.0 if brec.get("against_wall") else 0.0) + (30.0 if brec.get("bunk") else 0.0)
    if layer == "flat":
        m["head"] = ctx.args.get("head")
        seconds = 50.0
    if layer == "duvet":
        seconds = 40.0
    m["on_bed"] = bed
    if layer in ("flat", "blanket", "duvet") and abs(float(ctx.args.get("offset_cm") or 0.0)) > 30.0:
        M.add_soil(w, target, "dust", 0.04)
        w.mut(target)["floor_contact"] = M.now(w)
    if brec.get("made"):
        w.mut(bed)["made"] = False
    refresh_bed(w, bed)
    ctx.data["seconds"] = seconds
    w.log_move(target, "robot", bed)
    seat = " ({} of 4 corners seated)".format(m.get("corners_seated")) if layer in ("protector", "fitted") else ""
    return "Put {} on {} as the {}{}".format(target, bed, layer, seat)


def _dress_cover(ctx, target, rec, dest, drec, role):
    w = ctx.w
    require(drec.get("bed_role") in ("pillow", "decor_pillow", "euro_pillow") or drec.get("cushion_insert"), "Put it on a pillow or cushion insert")
    require(ctx.holding(target), "Hold the cover first")
    slot = "protector" if role == "pillow_protector" else "case"
    if slot == "protector":
        require(not drec.get("case"), "Take the pillowcase off before putting on the protector")
        require(not drec.get("protector"), "It already has a protector")
    else:
        require(not drec.get("case"), "It already has a case")
    sizes = {"standard": 0, "queen": 1, "king": 2, "euro": 3, "cushion": 0}
    cover_size = sizes.get(rec.get("pillow_size", "standard"), 0)
    pillow_size = sizes.get(drec.get("pillow_size", "standard"), 0)
    if rec.get("cover_dims_m") and drec.get("insert_dims_m"):
        cw, cl = sorted(rec["cover_dims_m"])[:2]
        iw, il = sorted(drec["insert_dims_m"])[:2]
        require(cw >= iw - 0.03 and cl >= il - 0.03, "The cover is too small for that insert")
        loose = cw > iw + 0.06 or cl > il + 0.08
    else:
        require(cover_size >= pillow_size, "The case is too small for that pillow")
        loose = cover_size > pillow_size
    method = ctx.args.get("method") or "stuff"
    release_from_hands(w, target)
    m = w.mut(target)
    m.update(location=dest, relation="attached")
    for key in ("pos", "spos", "shelf", "held_hand"):
        m.pop(key, None)
    m["covers"] = dest
    m["corners_filled"] = (method == "invert" or role == "cushion_cover") and not loose
    if rec.get("inside_out") and role != "pillow_protector":
        m["shows_inside_out"] = True
    m["open_end"] = ctx.args.get("open_end")
    if role == "cushion_cover":
        m["zip_side"] = ctx.args.get("zip_side") or "back"
        M.set_zipper(w, target, bool(ctx.args.get("close", True)))
        m = w.mut(target)
        damp = max(float(rec.get("wetness", 0.0)), float(rec.get("_core_wetness", 0.0) or 0.0))
        if damp >= M.DAMP:
            m["fitted_damp"] = True
        if float(rec.get("shrunk", 0.0)) >= 0.04:
            m["corners_filled"] = False
            m["strained"] = True
        if drec.get("cover_id") and drec.get("cover_id") != target:
            m["mismatched_insert"] = True
    pm = w.mut(dest)
    pm[slot] = target
    if role == "pillow_protector":
        M.set_zipper(w, target, bool(ctx.args.get("close", True)))
    ctx.data["seconds"] = 20.0 if method == "invert" else 15.0
    w.log_move(target, "robot", dest)
    bed = drec.get("location")
    if bed in w.s["entities"] and _is_bed(w.ent(bed)):
        refresh_bed(w, bed)
    return "Put {} on {}{}".format(target, dest, " (loose fit)" if loose else "")


def _dress_duvet(ctx, cover, crec, insert, irec):
    w = ctx.w
    require(irec.get("bed_role") in ("duvet_insert",), "Put a duvet cover on a duvet insert")
    require(not crec.get("filled_with"), "The cover already holds a duvet")
    unbutton = 0.0 if crec.get("closure_open", True) else 30.0
    others = [h for h in w.held_items() if h not in (cover, insert)]
    require(not others, "Both hands are needed for a duvet", "capability")
    method = ctx.args.get("method")
    require(method in ("inside_out", "stuff"), "Choose method inside_out or stuff", "argument")
    require(ctx.args.get("orientation"), "Say how the duvet is oriented in the cover (orientation lengthwise|crosswise)", "argument")
    support = irec.get("location")
    require(support not in w.s["rooms"], "Lay the duvet on a bed or table, not the floor")
    sizes_ok = SIZE_RANK.get(crec.get("bed_size", "queen"), 3) - SIZE_RANK.get(irec.get("bed_size", "queen"), 3)
    require(sizes_ok >= 0, "That insert is bigger than the cover")
    if ctx.holding(cover):
        release_from_hands(w, cover)
    if ctx.holding(insert):
        release_from_hands(w, insert)
    corners = 4 if method == "inside_out" else 2
    evenness = 0.95 if method == "inside_out" else 0.6
    if ctx.args.get("orientation") == "crosswise" and abs(M.flat_size(irec)[0] - M.flat_size(irec)[1]) > 0.05:
        corners, evenness = 0, 0.4
    if sizes_ok > 0:
        evenness = min(evenness, 0.5)
        corners = min(corners, 2)
    ties = bool(ctx.args.get("ties", True)) and bool(crec.get("corner_ties")) and corners == 4
    loc = irec.get("location")
    cm = w.mut(cover)
    from .wash import unfold_record
    unfold_record(cm)
    cm["location"], cm["relation"] = loc, irec.get("relation", "on")
    for key in ("pos", "spos", "shelf", "held_hand"):
        cm.pop(key, None)
    if irec.get("pos") is not None:
        cm["pos"] = list(irec["pos"])
    if irec.get("spos") is not None:
        cm["spos"] = list(irec["spos"])
    cm["filled_with"] = insert
    cm["corners_matched"] = corners
    cm["ties_tied"] = ties if crec.get("corner_ties") else None
    cm["fill_evenness"] = evenness
    cm["closure_open"] = not bool(ctx.args.get("close", True))
    cm["inside_out"] = False
    fw, fl = M.flat_size(crec)
    cm["size_m"] = [round(fw, 3), round(fl, 3), 0.05]
    cm["mass_kg"] = round(float(crec.get("cover_mass_kg", crec.get("mass_kg", 1.0))), 3)
    for key in ("spread_on", "stack_pos", "offset_cm", "face_up", "orientation"):
        if key in irec:
            cm[key] = irec[key]
    im = w.mut(insert)
    im["location"], im["relation"] = cover, "in"
    for key in ("pos", "spos", "shelf", "held_hand", "spread_on", "stack_pos"):
        im.pop(key, None)
    im["part_of_set"] = True
    ctx.data["seconds"] = (240.0 if method == "inside_out" else 150.0) + unbutton
    w.log_move(insert, loc, cover)
    if loc in w.s["entities"] and _is_bed(w.ent(loc)):
        refresh_bed(w, loc)
    return "Put the duvet into {} ({} corners matched{})".format(cover, corners, ", ties tied" if ties else "")


def _strip_seconds(ctx):
    return ctx.data.get("seconds", 120.0)


@action("strip_bed", params={"target": "entity", "into": "?entity", "keep": "?list",
                             "method": {"type": "str", "default": "gentle", "choices": ["gentle", "yank"]},
                             "aside": "?entity"},
        duration=_strip_seconds, energy=0.03, category="physical",
        doc="Strip bedding for washing. target bed: every washable layer (cases, duvet cover, sheets; the protector only if "
            "not in keep) goes into the basket/hamper 'into' (else it lands on the floor); the duvet insert and pillows stay "
            "on the bed or go to 'aside'. target pillow/cushion: take its case/cover off. target duvet cover: free the insert. "
            "Anything hidden in the bedding goes along unless removed first. method yank tears elastic and corner ties.")
def strip_bed(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = ctx.obj(target, touch=True)
    yank = ctx.args["method"] == "yank"
    into = ctx.args.get("into")
    if into:
        irec = ctx.obj(into, touch=True)
        require(irec.get("container") or irec.get("accepts_items"), "Strip into a basket, hamper or laundry bag")
        require(not irec.get("openable") or irec.get("open"), "Open the hamper first")
    if _is_bed(rec):
        return _strip_whole_bed(ctx, target, rec, into, yank)
    if rec.get("case") or rec.get("protector") or rec.get("cushion_insert"):
        return _strip_cover(ctx, target, rec, into, yank)
    if rec.get("filled_with"):
        return _free_insert(ctx, target, rec, into, yank)
    raise ActionError("Nothing to strip there", "argument")


def _to_basket(ctx, item, into, why):
    w = ctx.w
    m = w.mut(item)
    before = m.get("location")
    for key in ("spread_on", "stack_pos", "covers", "on_bed", "_pop_at"):
        m.pop(key, None)
    m["corners_seated"] = 0 if m.get("bed_role") in ("fitted", "protector") else m.get("corners_seated")
    m["tucked"] = []
    m["hospital_corners"] = 0
    if into:
        m["location"], m["relation"] = into, "in" if w.ent(into).get("container") else "on"
        for key in ("pos", "spos", "shelf", "held_hand"):
            m.pop(key, None)
    else:
        rid = w.robot["room"]
        cell = geometry.free_floor_cell(w.s, rid, near=tuple(w.robot.get("pos") or (0, 0)), ignore=(item,)) or list(w.robot.get("pos") or [0, 0])
        m["location"], m["relation"], m["pos"] = rid, "on", list(cell)
        for key in ("spos", "shelf", "held_hand"):
            m.pop(key, None)
        M.add_soil(w, item, "dust", 0.05)
        w.mut(item)["floor_contact"] = M.now(w)
    w.mut(item)["folded"] = False
    w.log_move(item, before, into or w.robot["room"])
    # anything hidden inside travels with it
    for child in w.children(item):
        c = w.ent(child)
        if not M.is_textile(c) and c.get("relation") in ("in", "under"):
            w.mut(child)["in_laundry"] = True


def _strip_whole_bed(ctx, bed, brec, into, yank):
    w = ctx.w
    _occupied(w, bed)
    keep = set(ctx.args.get("keep") or [])
    aside = ctx.args.get("aside")
    if aside:
        arec = ctx.obj(aside, touch=True)
        require(arec.get("surface") or arec.get("accepts_items"), "Set things aside on a surface")
    removed, seconds = [], 30.0
    for pillow in pillows_on(w, bed):
        prec = w.ent(pillow)
        for slot in ("case", "protector"):
            cover = prec.get(slot)
            if cover and w.has(cover) and not (slot == "protector" and "protector" in keep) and not (slot == "case" and "pillowcase" in keep):
                for child in w.children(cover):
                    if not M.is_textile(w.ent(child)):
                        w.mut(child)["in_laundry"] = True
                _to_basket(ctx, cover, into, "stripped")
                w.mut(pillow)[slot] = None
                removed.append(cover)
                seconds += 15.0
        if aside:
            pm = w.mut(pillow)
            pm["location"], pm["relation"] = aside, "on"
            pm.pop("spos", None)
            spot = geometry.surface_spot(w.s, aside, pillow)
            if spot is not None:
                pm["spos"] = spot
    for _, item, layer in reversed(bed_layers(w, bed)):
        irec = w.ent(item)
        if layer == "protector" and "protector" in keep:
            continue
        if layer == "duvet" and irec.get("filled_with"):
            insert = irec["filled_with"]
            if not irec.get("closure_open"):
                if yank:
                    M.add_damage(w, item, "seam_strained", 0.3, why="cover pulled off with the closure fastened")
                seconds += 10.0
            if irec.get("ties_tied") and yank:
                M.add_damage(w, item, "corner_ties_torn", 0.3, why="corner ties ripped instead of untied")
            im = w.mut(insert)
            im["location"], im["relation"] = (aside or bed), "on"
            im.pop("part_of_set", None)
            im["spread_on"] = None if aside else bed
            im["stack_pos"] = None
            cm = w.mut(item)
            cm["filled_with"] = None
            cm["closure_open"] = True
            cm["ties_tied"] = False if irec.get("corner_ties") else None
            for child in w.children(item):
                c = w.ent(child)
                if child != insert and not M.is_textile(c):
                    w.mut(child)["in_laundry"] = True
            _to_basket(ctx, item, into, "stripped")
            removed.append(item)
            seconds += 90.0
            continue
        if layer == "duvet" and irec.get("bed_role") in ("comforter", "duvet_insert"):
            continue
        if layer in ("blanket", "throw") and "blanket" in keep:
            continue
        if yank and layer in ("fitted", "protector"):
            M.add_damage(w, item, "elastic_torn", 0.4, why="fitted corners yanked off")
            w.mut(bed)["mattress_askew"] = True
        _to_basket(ctx, item, into, "stripped")
        removed.append(item)
        seconds += 35.0
    m = w.mut(bed)
    m["made"] = False
    refresh_bed(w, bed)
    ctx.data["seconds"] = seconds
    return "Stripped {} into {}: {}".format(bed, into or "a pile on the floor", ", ".join(removed) or "nothing")


def _strip_cover(ctx, insert, rec, into, yank):
    w = ctx.w
    slot = "case" if rec.get("case") else ("protector" if rec.get("protector") else "cushion_cover")
    cover = rec.get("case") or rec.get("protector") or rec.get("cushion_cover")
    if not cover:
        cover = next((c for c in w.children(insert) if w.ent(c).get("cover_role") == "cushion_cover"), None)
    require(cover and w.has(cover), "There is no cover on it")
    crec = w.ent(cover)
    if crec.get("cover_role") == "cushion_cover":
        require(not M.zipper_closed(crec) or yank, "Unzip the cover first")
        if M.zipper_closed(crec) and yank:
            M.add_damage(w, cover, "zipper_broken", 0.5, why="cover forced off with the zip closed")
        if yank and float(w.ent(insert).get("mass_kg", 0.5)) > 0:
            M.add_damage(w, insert, "foam_torn", 0.4, why="foam pulled out by one corner")
    elif yank:
        M.add_damage(w, cover, "seam_torn", 0.3, why="pillowcase yanked off")
    items = [c for c in w.children(cover) if not M.is_textile(w.ent(c))]
    bed = rec.get("location")
    for child in items:
        cm = w.mut(child)
        if yank and bed in w.s["entities"]:
            rid = M.room_of(w, bed)
            cm["location"], cm["relation"], cm["pos"] = rid, "on", list(M.anchor_cell(w, bed) or (0, 0))
            cm["lost_on_floor"] = True
            for key in ("spos",):
                cm.pop(key, None)
        else:
            cm["location"], cm["relation"] = (bed if bed in w.s["entities"] else insert), "on"
            cm["revealed"] = True
            if bed in w.s["entities"]:
                spot = geometry.surface_spot(w.s, bed, child)
                if spot is not None:
                    cm["spos"] = spot
    if into:
        _to_basket(ctx, cover, into, "stripped")
    else:
        hand = ctx.free_hand()
        require(hand is not None, "Free a hand for the cover or name where it goes", "capability")
        cm = w.mut(cover)
        cm.update(location="robot", relation="held", held_hand=hand)
        cm.pop("covers", None)
        w.robot_m()["hands"][hand] = cover
    pm = w.mut(insert)
    if slot in ("case", "protector"):
        pm[slot] = None
    else:
        pm["cushion_cover"] = None
    ctx.data["seconds"] = 15.0 if crec.get("cover_role") != "cushion_cover" else 40.0
    if bed in w.s["entities"] and _is_bed(w.ent(bed)):
        refresh_bed(w, bed)
    return "Took {} off {}{}".format(cover, insert, "; found " + ", ".join(items) if items else "")


def _free_insert(ctx, cover, rec, into, yank):
    w = ctx.w
    insert = rec["filled_with"]
    seconds = 120.0
    if not rec.get("closure_open"):
        if yank:
            M.add_damage(w, cover, "seam_strained", 0.3, why="cover pulled off with the closure fastened")
        else:
            seconds += 30.0
    if rec.get("ties_tied") and yank:
        M.add_damage(w, cover, "corner_ties_torn", 0.3, why="corner ties ripped")
    loc = rec.get("location")
    require(loc not in w.s["rooms"] and loc != "robot", "Lay it on a bed or table to take the cover off")
    im = w.mut(insert)
    im["location"], im["relation"] = loc, rec.get("relation", "on")
    im.pop("part_of_set", None)
    if rec.get("spread_on"):
        im["spread_on"] = rec["spread_on"]
        im["stack_pos"] = rec.get("stack_pos")
    cm = w.mut(cover)
    cm["filled_with"] = None
    cm["closure_open"] = True
    cm["ties_tied"] = False if rec.get("corner_ties") else None
    if into:
        _to_basket(ctx, cover, into, "duvet cover removed")
    else:
        cm["spread_on"] = None
        cm["stack_pos"] = None
        hand = ctx.free_hand()
        require(hand is not None, "Free a hand for the cover or name where it goes", "capability")
        cm.update(location="robot", relation="held", held_hand=hand)
        w.robot_m()["hands"][hand] = cover
    ctx.data["seconds"] = seconds
    if loc in w.s["entities"] and _is_bed(w.ent(loc)):
        refresh_bed(w, loc)
    return "Freed {} from {}".format(insert, cover)


def _make_seconds(ctx):
    return ctx.data.get("seconds", 120.0)


@action("make_bed", params={"target": "entity", "duvet": {"type": "str", "default": "spread", "choices": ["spread", "folded_foot"]},
                            "pillows": {"type": "str", "default": "upright", "choices": ["upright", "flat", "stacked"]},
                            "decor": {"type": "bool", "default": True},
                            "standard": {"type": "str", "default": "basic", "choices": ["basic", "hotel"]},
                            "fold_over": {"type": "bool", "default": False}},
        duration=_make_seconds, energy=0.03, category="physical",
        doc="Make a dressed bed: smooth every layer, square the duvet (or fold it at the foot), arrange sleeping pillows "
            "(upright/flat/stacked) and decorative pillows already on the bed (decor), zippers hidden. standard=hotel "
            "pulls everything taut (needs all fitted corners seated) and folds the top sheet over (fold_over). Anything "
            "still lying in or under the bedding gets buried.")
def make_bed(ctx):
    w = ctx.w
    bed = ctx.args["target"]
    brec = ctx.obj(bed, touch=True)
    require(_is_bed(brec), "That is not a bed", "argument")
    _occupied(w, bed)
    layers = bed_layers(w, bed)
    names = [l for _, _, l in layers]
    require(names, "Dress the bed first (no bedding on it)")
    hotel = ctx.args["standard"] == "hotel"
    fitted = next((e for _, e, l in layers if l == "fitted"), None)
    corners_ok = fitted is not None and int(w.ent(fitted).get("corners_seated", 0)) == 4
    top = layers[-1][1]
    buried = []
    for child in w.children(bed):
        c = w.ent(child)
        if child in [e for _, e, _ in layers] or c.get("bed_role") in ("pillow", "decor_pillow", "euro_pillow") or c.get("spread_on") == bed:
            continue
        if c.get("relation") == "on" and not c.get("bed_role"):
            buried.append(child)
    for _, e, _ in layers:
        for child in w.children(e):
            c = w.ent(child)
            if not M.is_textile(c) and c.get("relation") in ("under", "in") and child != w.ent(e).get("filled_with"):
                buried.append(child)
    for item in sorted(set(buried)):
        m = w.mut(item)
        m["location"], m["relation"] = top, "under"
        m["revealed"] = False
        m["buried_in_bed"] = bed
        m.pop("spos", None)
    smooth = 0.02 if hotel and corners_ok else (0.1 if hotel else 0.08)
    for _, e, layer in layers:
        m = w.mut(e)
        m["wrinkled"] = round(min(float(m.get("wrinkled", 0.0)), smooth) if not m.get("loose_fit") else 0.4, 3)
        if layer in ("duvet", "flat", "blanket"):
            m["offset_cm"] = 0.0
        if layer == "duvet":
            m["duvet_style"] = ctx.args["duvet"]
            if m.get("filled_with") and float(m.get("fill_evenness", 1.0)) < 0.8:
                m["fill_evenness"] = round(min(0.8, float(m.get("fill_evenness", 0.6)) + 0.1), 3)
        if layer == "flat" and hotel and ctx.args["fold_over"]:
            m["fold_over"] = True
        if layer == "throw":
            m["folded"] = "thirds_half" if hotel else m.get("folded")
            m["throw_at_foot"] = True
    width = float(geometry.footprint_m(brec)[0])
    pillows = sorted(pillows_on(w, bed), key=lambda p: (w.ent(p).get("bed_role") != "pillow", p))
    sleeping = [p for p in pillows if w.ent(p).get("bed_role") == "pillow"]
    decor = [p for p in pillows if w.ent(p).get("bed_role") != "pillow"]
    for i, p in enumerate(sleeping):
        m = w.mut(p)
        m["pose"] = ctx.args["pillows"]
        m["zipper_hidden"] = True
        m["loft"] = max(float(m.get("loft", 0.8)), 0.85)
        m["arranged_index"] = i
    for i, p in enumerate(decor):
        m = w.mut(p)
        m["pose"] = "upright_front" if ctx.args["decor"] else "aside_on_bed"
        m["zipper_hidden"] = True
        m["arranged_index"] = len(sleeping) + i
    m = w.mut(bed)
    m["made"] = True
    m["made_at"] = M.now(w)
    m["made_style"] = {"duvet": ctx.args["duvet"], "pillows": ctx.args["pillows"], "decor": bool(ctx.args["decor"]),
                       "standard": ctx.args["standard"]}
    refresh_bed(w, bed)
    ctx.data["seconds"] = (360.0 if hotel else 120.0) + 10.0 * len(pillows)
    note = "; buried " + ", ".join(sorted(set(buried))) if buried else ""
    if ctx.realistic and buried:
        # smoothing the covers the robot feels a lump; it never saw what is under there
        note = "; something lumpy is under the covers"
    return "Made {} ({}){}".format(bed, ctx.args["standard"], note)


@action("tuck", params={"target": "entity", "edges": {"type": "list", "default": ["foot"]},
                        "corners": {"type": "str", "default": "simple", "choices": ["simple", "hospital"]},
                        "layer": {"type": "str", "default": "flat", "choices": ["flat", "blanket", "duvet"]},
                        "untuck": {"type": "bool", "default": False}},
        duration=lambda ctx: 20.0 * max(1, len(ctx.args.get("edges") or [])), energy=0.01, category="physical",
        doc="Tuck (or untuck) the top sheet/blanket under the mattress along edges foot/left/right, with simple or hospital "
            "corners. Duvets are not tucked.")
def tuck(ctx):
    w = ctx.w
    bed = ctx.args["target"]
    brec = ctx.obj(bed, touch=True)
    require(_is_bed(brec), "Tuck bedding on a bed", "argument")
    _occupied(w, bed)
    edges = ctx.args["edges"]
    require(all(e in ("foot", "left", "right", "head") for e in edges), "Edges are foot, left, right or head", "argument")
    layer = ctx.args["layer"]
    item = next((e for _, e, l in bed_layers(w, bed) if l == layer), None)
    require(item is not None, "There is no {} on the bed".format(layer))
    m = w.mut(item)
    tucked = set(m.get("tucked") or [])
    if ctx.args["untuck"]:
        tucked -= set(edges)
        m["hospital_corners"] = 0 if "foot" in edges else int(m.get("hospital_corners", 0))
    else:
        tucked |= set(edges)
        if ctx.args["corners"] == "hospital" and "foot" in tucked:
            m["hospital_corners"] = 2 if {"left", "right"} <= tucked else (1 if tucked & {"left", "right"} else 0)
        if layer == "duvet":
            m["tucked_duvet"] = True
        m["wrinkled"] = round(min(float(m.get("wrinkled", 0.2)), 0.15), 3)
    m["tucked"] = sorted(tucked)
    refresh_bed(w, bed)
    return "{} {} {}".format("Untucked" if ctx.args["untuck"] else "Tucked", item, ", ".join(edges))


@action("fluff", params={"target": "entity", "zipper": "?str:hidden|visible"}, duration=lambda ctx: ctx.data.get("seconds", 8.0),
        energy=0.005, category="physical",
        doc="Fluff a pillow, cushion or duvet (restores loft, evens out the fill) and turn its zipper hidden or visible.")
def fluff(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = ctx.obj(target, touch=True)
    require(rec.get("bed_role") in ("pillow", "decor_pillow", "euro_pillow", "duvet_cover", "duvet_insert", "comforter")
            or rec.get("cushion_insert") or rec.get("fluffable"), "That cannot be fluffed", "argument")
    occupant = M.textile_occupant(w, target)
    require(occupant is None, "{} is using it".format(occupant), "social")
    m = w.mut(target)
    m["loft"] = 1.0
    if rec.get("filled_with"):
        m["fill_evenness"] = round(max(float(rec.get("fill_evenness", 0.6)), 0.85 if int(rec.get("corners_matched", 0)) >= 4 else min(0.7, float(rec.get("fill_evenness", 0.6)) + 0.1)), 3)
    if ctx.args.get("zipper"):
        m["zipper_hidden"] = ctx.args["zipper"] == "hidden"
    ctx.data["seconds"] = 30.0 if rec.get("filled_with") or rec.get("bed_role") in ("duvet_insert", "comforter") else 8.0
    return "Fluffed " + target


# ---------------------------------------------------------------------------
# fitted sheets pop off after a night (or when someone lies down) unless all corners hold

def _fitted_ticker(w, eid, rec, dt, context):
    pop_at = rec.get("_pop_at")
    if pop_at is None or rec.get("bed_role") not in ("fitted", "protector"):
        return
    bed = rec.get("location")
    if not bed or bed not in w.s["entities"]:
        return
    if M.now(w) >= float(pop_at) or M.bed_occupant(w, bed):
        m = w.mut(eid)
        m["corners_seated"] = max(0, int(m.get("corners_seated", 0)) - 1)
        m["popped_off"] = True
        m["wrinkled"] = round(max(float(m.get("wrinkled", 0.0)), 0.5), 3)
        m["_pop_at"] = None
        if w.ent(bed).get("made"):
            w.mut(bed)["made"] = False
        refresh_bed(w, bed)
        w.emit("fitted_corner_popped", target=eid, bed=bed)


TEXTILE_TICKERS.append(_fitted_ticker)
