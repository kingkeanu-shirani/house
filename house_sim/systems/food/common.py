"""Guards and effects shared by food actions."""
from ...kernel import geometry
from ...kernel.errors import ActionError, require
from ..core import add_soil, require_tool
from . import data as D
from . import model as M

STONE = ("stone", "granite", "marble", "quartz", "ceramic", "glass", "porcelain")


def seconds(ctx, value):
    ctx.data["seconds"] = ctx.data.get("seconds", 0.0) + float(value)


def duration(default=5.0):
    return lambda ctx: ctx.data.get("seconds", default)


def pour_ml(rec, grams):
    """Volume of `grams` of the food in rec: pouring, scraping and serving take time by volume (a camera sees how much
    moves, not how dense it is), so durations never reveal a hidden mass or identity (flour vs sugar)."""
    return float(grams) / M.density(rec)


def food_target(ctx, eid, touch=True, grasp=False):
    rec = ctx.obj(eid, touch=touch, grasp=grasp)
    require(M.food(rec) is not None, "That is not food", "argument")
    require(M.qty(rec) > 0, "There is none left", "precondition")
    return rec


def name(ctx, eid, default="it"):
    """Label in oracle mode; the id (an opaque handle for the agent) in realistic mode, so a refusal never names an
    unresolved look-alike."""
    rec = ctx.w.s["entities"].get(eid) if isinstance(eid, str) else None
    if rec is None:
        return default
    return eid if getattr(ctx.env, "realism", None) is not None else rec.get("label", default)


def require_open(rec, what="package", ctx=None, eid=None):
    if rec.get("package") and rec.get("openable") and not rec.get("open"):
        raise ActionError("Open the {} first".format(name(ctx, eid, what) if ctx is not None and eid else rec.get("label", what)), "precondition")


def held_known(ctx):
    """What the robot holds and knows what it is: with realistic perception an unfamiliar (novel) thing in hand is no
    tool, utensil, opener or probe the robot knows of, whatever it really is (review I4; percept.unfamiliar_first
    refuses one named in a parameter). Every held item in oracle mode."""
    from . import percept as P
    return [h for h in ctx.w.held_items() if not P.unfamiliar(ctx.env, h)]


def tool(ctx, capability, tool_id=None):
    """The held tool offering `capability` (core require_tool): the named one, else the first held one the robot knows
    of (held_known: an unfamiliar knife in hand cuts nothing, as an unfamiliar kettle does not)."""
    if tool_id:
        require(ctx.holding(tool_id), "Hold the {} first".format(name(ctx, tool_id, "tool")), "capability")
        return require_tool(ctx, capability, tool_id, consume=False)
    if getattr(ctx.env, "realism", None) is None:
        return require_tool(ctx, capability, None, consume=False)
    for eid in held_known(ctx):
        rec = ctx.w.ent(eid)
        if capability in set(rec.get("affordances", [])) | set(rec.get("capabilities", [])) | {rec.get("tool_kind")}:
            return require_tool(ctx, capability, eid, consume=False)
    raise ActionError("Hold a tool that can {}".format(capability.replace("_", " ")), "capability")


def optional_tool(ctx, capability, tool_id=None):
    try:
        return tool(ctx, capability, tool_id)
    except ActionError:
        if tool_id:
            raise
        return None


def free_hand_for_steadying(ctx, what="steady the food"):
    require(ctx.free_hand() is not None, "Keep one hand free to {} (claw grip)".format(what), "capability")


def resting_surface(state, eid):
    """(surface id, kind) the food rests on: board, plate, tray, pan, counter, vessel or None."""
    rec = state["entities"][eid]
    loc = rec.get("location")
    parent = state["entities"].get(loc)
    if parent is None:
        return None, "held" if loc == "robot" else "floor"
    if parent.get("tool_kind") == "cutting_board" or "cutting_board" in parent.get("is_a", []):
        return loc, "board"
    if parent.get("serving") and parent.get("surface"):
        return loc, "plate"
    if parent.get("container"):
        return loc, "vessel"
    if parent.get("surface") or parent.get("accepts_items"):
        if parent.get("category") == "counter" or "counter" in parent.get("is_a", []) or "table" in parent.get("is_a", []):
            return loc, "counter"
        if M.food(parent):
            return loc, "food"
        return loc, "surface"
    return loc, "other"


def board_slip_risk(state, board):
    rec = state["entities"][board]
    if rec.get("non_slip"):
        return 0.0
    under = state["entities"].get(rec.get("location"), {})
    if under.get("category") in ("towel", "cloth", "textile") or under.get("grip_mat") or \
            (under.get("material") in ("cotton", "fabric", "microfiber", "rubber") and float(under.get("wetness", 0.0)) >= 0.2):
        return 0.0
    if under.get("grip_mat"):
        return 0.0
    base = float(rec.get("slip_risk", 0.2))
    if float(rec.get("wetness", 0.0)) > 0.3:
        base += 0.2
    return base


def cutting_surface(ctx, fid, allow=("board", "plate")):
    """Where the cut happens; cutting on a counter top scratches it (or dulls the knife on stone)."""
    w = ctx.w
    surface, kind = resting_surface(w.s, fid)
    if kind == "held":
        raise ActionError("Never cut food held in the hand; set it on a cutting board", "safety")
    require(kind not in ("floor", "vessel", "other", None), "Set it on a cutting board first", "precondition")
    if kind in allow:
        return surface, kind
    if kind in ("counter", "surface"):
        rec = w.ent(surface)
        if rec.get("material") in STONE:
            ctx.data["dull_knife"] = 0.25
        else:
            m = w.mut(surface)
            M.add_damage(m, "knife_scratches", 0.2)
            w.stat("damage")
            w.emit("surface_scratched", target=surface)
        return surface, kind
    raise ActionError("Set it on a cutting board first", "precondition")


def slip_check(ctx, fid, knife, surface, technique, style):
    """Deterministic knife-slip model: rolling round produce, unstable board, flat fingers, dull blade."""
    w = ctx.w
    rec = w.ent(fid)
    f = rec["food"]
    risk = 0.0
    reasons = []
    if f.get("round") and not f.get("flat_side") and f.get("cut", "whole") == "whole" and style not in ("halve", "quarter", "flat_side", "wedges", "trim"):
        risk += 0.7
        reasons.append("round food rolled under the blade")
    if surface in w.s["entities"] and (w.ent(surface).get("tool_kind") == "cutting_board"):
        r = board_slip_risk(w.s, surface)
        if r:
            risk += r
            reasons.append("board slid")
    if technique in ("flat", "flat_hand"):
        risk += 0.4
        reasons.append("fingertips in the blade path")
    elif technique in ("none", "in_hand", "toward"):
        risk += 0.7
        reasons.append("hand in the blade path")
    sharp = float(w.ent(knife).get("sharpness", 1.0)) if knife else 1.0
    if sharp < 0.4:
        risk += 0.3
        reasons.append("dull blade skidded")
    if f.get("slippery") and technique not in ("claw",):
        risk += 0.2
    hand = ctx.hand_of(knife) if knife else None
    if hand is not None:
        # dough, oil or egg left on the fingers holding the handle (body.soil_hand; washed off by a hand wash)
        from ...kernel import body
        greasy = body.slippery(w.s, hand)
        if greasy > 0.0:
            risk += round(D.GREASY_HANDLE_RISK * greasy, 4)
            if greasy >= 0.5:
                reasons.append("the handle slid in greasy fingers")
    if risk >= D.SLIP_RISK_LIMIT:
        knife_slip(ctx, fid, "; ".join(reasons), tool=knife)
        return True
    return False


# ----------------------------------------------------------------------
# tier-1 adapters (TIER1_INTEGRATION 6): robot faults and soiled fingers (body), visual occurrences (camera),
# notices (the kernel notice bus)

GRIPPER_CUT = "gripper_cut"


def _register_faults():
    """The robot fault a slipping blade causes (body_faults: a typed record, the fault log, detection). cleaning
    raises the same kind when a knife is wiped toward its edge; whichever domain loads first registers it, with the
    same effect: the gashed pad grips worse (a gripper_slip fault on that hand) until service replaces it."""
    from ...kernel import body_faults
    if GRIPPER_CUT not in body_faults.FAULT_KINDS:
        body_faults.register_fault_kind(
            GRIPPER_CUT, "gripper", lambda robot, record: None, onset=_cut_pad_slips,
            doc="A blade cut a gripper pad (a knife, mandoline, grater or oyster knife slipped into the holding hand): "
                "the gashed pad grips worse (a gripper_slip fault on that hand) until service replaces it")


def _cut_pad_slips(w, record):
    from ...kernel import body, body_faults
    if body.fault(w.s, "gripper_slip_" + record["part"]) is None:
        body_faults.add_fault(w, "gripper_slip", part=record["part"], source=GRIPPER_CUT)


_register_faults()


def cut_hand(ctx, fid, tool=None):
    """The hand a slipping blade reaches: the one holding the food (grater, mandoline, peeling toward the hand, an
    oyster), else the steadying hand opposite the held blade, else the free hand."""
    hand = ctx.hand_of(fid)
    if hand is None and tool and ctx.hand_of(tool):
        hand = "left" if ctx.hand_of(tool) == "right" else "right"
    return hand or ctx.free_hand() or "left"


def knife_slip(ctx, fid, why, tool=None):
    """The blade slipped into the robot's hand: a cut gripper pad (body_faults gripper_cut, which starts a gripper_slip
    fault on that hand) and the global damage flag."""
    from ...kernel import body_faults
    w = ctx.w
    w.stat("damage")
    hand = cut_hand(ctx, fid, tool)
    body_faults.add_fault(w, GRIPPER_CUT, part=hand, source="food")
    w.emit("knife_slip", target=fid, reason=why)
    ctx.note("The knife slipped: " + why)
    ctx.data["slipped"] = why


def soil_hands(ctx, kind, amount, hands=None):
    """Food left on the robot's fingers (body.soil_hand: the body owns the coating; slippery soil can start a latent
    gripper_slip fault): flour or crumbs ("flour"), oil or fat ("oil"), wet or sticky food ("food"). hands: "both",
    a hand, or None for the working hand (the free one, else the right)."""
    from ...kernel import body
    if amount <= 0:
        return
    hand = hands or ctx.free_hand() or "right"
    body.soil_hand(ctx.w, hand, kind, amount)


def coating_soil(rec):
    """The kind of hand soil a food leaves on fingers that work it (None: none worth counting)."""
    f = M.food(rec) or {}
    k = f.get("kind") or ""
    if k in ("oil", "olive_oil", "vegetable_oil", "butter", "salted_butter", "unsalted_butter", "bacon_grease", "fat",
             "lard", "shortening") or f.get("cat") in ("oil", "butter"):
        return "oil"
    if f.get("phase") in ("powder", "granular"):
        return "flour"
    if f.get("phase") in ("liquid", "paste", "dough") or f.get("cat") in ("egg", "poultry", "meat", "ground_meat", "fish"):
        return "food"
    return None


def witness(w, kind, source=None, size_m=0.3, visual=None, **data):
    """A visual food occurrence (a spill, smoke, a spraying can): camera.visual_event, which is exactly
    w.emit(kind, **data) in room mode; in geometric mode the head camera's current pose witnesses it (camera_log.events,
    obs.camera.events and a "visual" notice when seen). size_m: how big the occurrence looks; visual: the perceived
    words ("spill", "smoke")."""
    from ...kernel import camera
    return camera.visual_event(w, kind, source=source if isinstance(source, str) and source in w.s["entities"] else None,
                               size_m=size_m, visual=visual, **data)


def post_notice(w, kind, source=None, room=None, db=None, visual=False):
    """Something perceivable happened (w.notice): the timeline decides whether the robot hears or sees it and stores
    it while the notice bus is on or a wait listens. Cheap when nobody listens. Returns the stored record or None."""
    from ...kernel import timeline
    if not timeline.active(w):
        return None
    if room is None and isinstance(source, str) and source in w.s["entities"]:
        from ...kernel.predicates import root_room
        room = root_room(w.s, source)
    return w.notice(kind, room=room, db=db, source=source if isinstance(source, str) else None, visual=visual)


# notices this domain posts beyond the kernel's built-ins ("smoke" is built in): (kind, doc, db at 1 m)
NOTICE_KINDS = (
    ("hiss", "something hisses or sprays out under pressure as it is opened (a shaken drink, a bulging can)", 60.0),)


def _register_notice_kinds():
    from ...kernel import timeline
    for kind, doc, db in NOTICE_KINDS:
        timeline.register_notice_kind(kind, doc, implies=("any_sound",), db=db)


_register_notice_kinds()


def dull(ctx, knife, amount):
    if knife and amount:
        m = ctx.w.mut(knife)
        m["sharpness"] = round(max(0.0, float(m.get("sharpness", 1.0)) - amount), 3)


def seen_inside(ctx, eid):
    """An egg's inside has been seen (cracked open, peeled or cut into): it is identified from now on (the food.json
    egg_in_shell group resolves on its hidden _seen_inside label field once the robot has read-level knowledge)."""
    rec = ctx.w.s["entities"].get(eid)
    if rec is None or rec.get("_seen_inside") or (M.food(rec) or {}).get("cat") != "egg":
        return
    ctx.w.mut(eid)["_seen_inside"] = True
    if ctx.realistic:
        ctx.grant(eid, "read")


def touch_food(ctx, fid, k=0.4):
    """The robot's hand (or worn glove) touches food."""
    M.contact(ctx.w, M.hands_target(ctx.w.s), fid, k)


def tool_touch(ctx, tool_id, fid, k=0.6):
    if tool_id:
        M.contact(ctx.w, tool_id, fid, k)


def surface_touch(ctx, surface, fid, k=0.6):
    if surface and surface in ctx.w.s["entities"]:
        M.contact(ctx.w, surface, fid, k)
        rec = ctx.w.ent(fid)
        if M.is_raw_animal(rec) and ctx.w.ent(surface).get("tool_kind") == "cutting_board":
            add_soil(ctx.w, surface, "food", 0.08)


def vessel_reach(ctx, vessel):
    rec = ctx.obj(vessel, touch=True)
    require(rec.get("container") or rec.get("surface") or rec.get("accepts_items") or rec.get("drain"), "That cannot hold food", "argument")
    if rec.get("openable") and not rec.get("open") and rec.get("container"):
        raise ActionError("Open the {} first".format(name(ctx, vessel, "container")), "precondition")
    return rec


def dims_mm(rec):
    f = M.food(rec) or {}
    if f.get("piece_mm") and int(f.get("pieces", 1) or 1) > 1:
        return [float(v) for v in f["piece_mm"]]
    size = f.get("unit_size_m") if f.get("count", 1) and int(f.get("count", 1) or 1) > 1 and f.get("unit_size_m") else rec.get("size_m")
    size = size or [0.05, 0.05, 0.05]
    return [round(float(v) * 1000.0, 1) for v in size[:3]]


def set_pieces(w, fid, piece_mm, cut, cv, round_pieces=False, axis=None, extra=None):
    rec = w.ent(fid)
    f = rec["food"]
    units = max(1, int(f.get("count", 1) or 1))
    vol_ml = M.volume_ml(rec)
    piece_ml = max(0.001, piece_mm[0] * piece_mm[1] * piece_mm[2] / 1000.0)
    pieces = max(units, int(round(vol_ml / piece_ml)))
    m = w.mut(fid)
    mf = m["food"]
    mf["cut"] = cut
    mf["pieces"] = pieces
    mf["piece_mm"] = [round(v, 1) for v in piece_mm]
    mf["piece_ml"] = round(vol_ml / pieces, 3)
    mf["piece_cv"] = round(cv, 3)
    mf["round_pieces"] = bool(round_pieces)
    mf["phase"] = "pieces" if pieces > 1 else mf.get("phase", "solid")
    if axis:
        mf["cut_axis"] = axis
    if f.get("round") and cut not in ("whole",):
        mf["flat_side"] = True
    if extra:
        mf.update(extra)
    m["size_m"] = M.portion_size(m)
    return pieces


def part_info(rec, part):
    f = M.food(rec) or {}
    info = (f.get("part_info") or {}).get(part)
    if info is None:
        defaults = {"peel": 0.08, "skin": 0.05, "seeds": 0.03, "stem": 0.03, "core": 0.1, "pit": 0.1, "rind": 0.35,
                    "shell": 0.11, "bone": 0.25, "pin_bones": 0.005, "leaves": 0.6, "woody_stem": 0.35, "rib": 0.3,
                    "top": 0.08, "root": 0.03, "calyx": 0.04, "husk": 0.15, "silk": 0.02, "crust": 0.3, "fat": 0.1,
                    "outer_leaves": 0.1, "eyes": 0.01, "green_patch": 0.02, "woody_base": 0.2, "ends": 0.03,
                    "membrane": 0.15, "pith": 0.06, "cork": 0.0, "crown": 0.1, "head": 0.15, "tail": 0.05, "vein": 0.02,
                    "tender_stem": 0.3, "stem_end": 0.03, "dark_green_top": 0.35, "blemishes": 0.03, "clove": 0.1}
        info = {"frac": defaults.get(part, 0.05)}
    return info
