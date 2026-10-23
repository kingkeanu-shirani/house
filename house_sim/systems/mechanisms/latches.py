"""Verbs latch and unlatch: gate latches (gravity, thumb, magnetic pool latch, slide/barrel bolts,
two-step and squeeze-lift latches), drop bolts on double doors, hasps and padlocks (keyed or
combination), child locks, window child stops and guards, curtain tiebacks, blind cord cleats,
suitcase/toolbox/compost clasps and snap-lid tabs, high-chair frame locks/tray/brakes, pet-door
locking panels, storm-door hold-open washers, tumbler hatches.

Gate latch physics: the latch bar engages when the bar-to-catch offset (hidden sag plus keeper
misalignment) is within 6 mm; lifting the gate while latching (lift=true) takes up to 15 mm of sag.
A latch that does not engage leaves the bar resting on the catch - the gate looks shut but a push
opens it. Combination locks: three wrong codes on one lock counts as brute-forcing
(authority_violations).
"""
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import hook
from ..core import hands_free_count, held_key_for, release_from_hands
from . import closures as K
from . import common as C

def norm_code(code):
    return str(code).replace("-", "").replace(" ", "")


def _padlock_key_ok(ctx, padlock, code):
    """None when the key/code opens it, else the outcome message (wrong codes are counted)."""
    w = ctx.w
    rec = w.ent(padlock)
    if rec.get("_code") is not None or rec.get("combination"):
        require(code is not None, "It is a combination lock: give the code", "argument")
        if norm_code(code) != norm_code(rec.get("_code", "")):
            return wrong_code(ctx, padlock)
        return None
    key = rec.get("key")
    # its own key, or (as core lock / unlock: held_key_for) a held key whose `opens` names it
    require(key is None or ctx.holding(key) or held_key_for(w, padlock, rec) is not None, "Hold the padlock's key",
            "precondition")
    return None


def wrong_code(ctx, lock_id):
    """A wrong combination: nothing opens. Three wrong tries on one lock count as brute-forcing it."""
    w = ctx.w
    sec = w.sec("mechanisms")
    tries = sec.setdefault("wrong_codes", {})
    tries[lock_id] = int(tries.get(lock_id, 0)) + 1
    if tries[lock_id] >= 3:
        C.harm(w, "authority_violations", lock=lock_id, cause="brute-forcing a combination lock")
        return "Wrong combination again: repeatedly guessing codes is brute-forcing a lock"
    return "Wrong combination; it stays locked"


def sync_hasp_lock(w, host):
    rec = w.ent(host)
    hasp = C.mget(rec, "hasp")
    if not isinstance(hasp, dict):
        return
    pad = hasp.get("padlock")
    secured = bool(pad and hasp.get("closed") and hasp.get("through_staple", True) and pad in w.s["entities"]
                   and w.ent(pad).get("shackle_closed"))
    own = bool(C.mget(rec, "own_lock_locked"))
    rm = w.mut(host)
    rm["locked"] = secured or own


def fixed_leaf(ctx, eid, opening):
    w = ctx.w
    rec = w.ent(eid)
    leaves = dict(C.leaf_state(rec))
    require(leaves, "It has a single leaf", "precondition")
    fixed = dict(leaves.get("fixed") or {})
    rm, mech = C.mut_mech(w, eid)
    if opening:
        require(not fixed.get("bolted"), "The fixed leaf is held by its drop bolt; unlatch part=drop_bolt", "precondition")
        require(rec.get("open"), "Open the active leaf first", "precondition")
        fixed["open"] = True
        leaves["fixed"] = fixed
        C.set_leaf_state(mech, leaves)
        return "Opened the fixed leaf too"
    fixed["open"] = False
    leaves["fixed"] = fixed
    C.set_leaf_state(mech, leaves)
    return "Swung the fixed leaf shut"


def pet_door_test(ctx, eid, rec):
    w = ctx.w
    here = w.robot["room"]
    mode = C.mget(rec, "mode", "open")
    rooms = C.door_rooms(w.s, eid) or list(rec.get("connects") or [])
    inside = next((r for r in rooms if not C.is_outdoor(w.s, r)), rooms[0] if rooms else here)
    pushing_out = here == inside
    passes = mode == "open" or (mode == "out_only" and pushing_out) or (mode == "in_only" and not pushing_out)
    if C.mget(rec, "panel") == "in":
        passes = False
    rm, mech = C.mut_mech(w, eid)
    mech["tested_at"] = w.s["time_s"]
    mech["test_held"] = not passes
    w.sec("facts").setdefault("mechanism_tests", {})[eid] = {"time_s": w.s["time_s"], "held": not passes}
    return "The flap swings through" if passes else "The flap holds"


def pet_door_mode(rec):
    if C.mget(rec, "panel") == "in":
        return "locked"
    return C.mget(rec, "mode", "open")


def _high_chair(ctx, eid, part, engage):
    w = ctx.w
    rec = w.ent(eid)
    rm, mech = C.mut_mech(w, eid)
    if part.startswith("frame"):
        locks = dict(C.mget(rec, "frame_locks") or {"left": True, "right": True})
        sides = ["left", "right"] if part == "frame" else [part.split("_", 1)[1]]
        for side in sides:
            require(side in locks, "No {} frame lock".format(side), "argument")
            require(not rec.get("folded") and C.mget(rec, "folded") is not True, "Unfold it first (open)", "precondition")
            locks[side] = engage
        mech["frame_locks"] = locks
        return "{} frame lock(s) {}".format("/".join(sides), "clicked in" if engage else "released")
    if part == "tray":
        require(C.mget(rec, "tray") != "off" or not engage, "The tray is off the chair; attach it first", "precondition")
        mech["tray"] = "latched" if engage else "unlatched"
        return "Tray {}".format("latched" if engage else "released")
    if part == "brakes":
        mech["brakes"] = engage
        return "Caster brakes {}".format("on" if engage else "off")
    if part == "harness":
        mech["harness"] = "ready" if engage else "unbuckled"
        return "Harness {}".format("buckled ready" if engage else "unbuckled")
    raise ActionError("Unknown high-chair part " + part, "argument")


def _frame_lock(ctx, eid, part, engage):
    """Folding furniture (tables, racks, strollers): mech.frame_locks {name: engaged}; part=frame/legs = all."""
    w = ctx.w
    rec = w.ent(eid)
    locks = dict(C.mget(rec, "frame_locks") or {})
    names = sorted(locks) if part in ("frame", "legs") else [part.split("_", 1)[1] if "_" in part else part]
    for name in names:
        require(name in locks, "Locks here: " + ", ".join(sorted(locks)), "argument")
        locks[name] = engage
    rm, mech = C.mut_mech(w, eid)
    mech["frame_locks"] = locks
    return "{} {}".format("/".join(names), "locked" if engage else "released")


def _covering_part(ctx, eid, part, engage):
    w = ctx.w
    rec = w.ent(eid)
    rm, mech = C.mut_mech(w, eid)
    if part.startswith("tieback"):
        ties = dict(C.mget(rec, "tiebacks") or {})
        require(ties, "This curtain has no tiebacks", "precondition")
        sides = sorted(ties) if part in ("tieback", "tiebacks") else [part.split("_", 1)[1]]
        panels = C.mget(rec, "panels") or {}
        for side in sides:
            require(side in ties, "No {} tieback".format(side), "argument")
            if engage:
                require(C.num(panels.get(side), 0.0) <= 0.15, "Draw the {} panel open before tying it back".format(side), "precondition")
            ties[side] = engage
        mech["tiebacks"] = ties
        from . import coverings
        coverings.sync_window(w, eid)
        return "Tieback(s) {} {}".format("/".join(sides), "fastened" if engage else "released")
    if part in ("cord", "cords"):
        require(C.mget(rec, "lift") not in ("cordless", "motorized"), "It has no lift cord", "precondition")
        if engage:
            mech["cord_cleated"] = True
            mech["cord_low_m"] = max(C.CORD_SAFE_HEIGHT_M, C.num(C.mget(rec, "cleat_height_m"), 1.6))
            return "Wrapped the cord on its cleat out of a child's reach"
        mech["cord_cleated"] = False
        mech["cord_low_m"] = 0.6
        return "Unwrapped the cord"
    raise ActionError("Unknown part " + part, "argument")


def _window_part(ctx, eid, part, engage):
    w = ctx.w
    rec = w.ent(eid)
    rm, mech = C.mut_mech(w, eid)
    if part in ("child_stop", "stop"):
        stop = dict(C.mget(rec, "child_stop") or {})
        require(stop, "This window has no child stop", "precondition")
        if engage:
            limit = C.num(stop.get("limit_frac"), 0.12)
            require(C.num(C.mget(rec, "open_frac", 0.0)) <= limit + 1e-6, "Lower the sash to the stop height first", "precondition")
        stop["engaged"] = engage
        mech["child_stop"] = stop
        return "Child window stop {}".format("engaged" if engage else "released")
    if part in ("guard", "window_guard"):
        guard = dict(C.mget(rec, "guard") or {})
        require(guard, "This window has no guard", "precondition")
        guard["engaged"] = engage
        mech["guard"] = guard
        return "Window guard {}".format("locked in place" if engage else "released with its quick-release")
    raise ActionError("Unknown window part " + part, "argument")


def _child_lock(ctx, eid, engage):
    w = ctx.w
    rec = w.ent(eid)
    kind = C.mget(rec, "child_lock_kind")
    require(kind, "No child lock is installed here", "precondition")
    require(not C.mget(rec, "child_lock_broken"), "The child lock is broken", "precondition")
    if not engage and kind == "magnetic":
        key = C.held_tool(ctx, kinds=("magnetic_lock_key",), affordances=("magnetic_key",))
        require(key is not None, "A magnetic lock releases only with its magnetic key held to the door", "precondition")
    rm = w.mut(eid)
    rm["child_lock"] = engage
    return "Child lock {}".format("engaged" if engage else "released")


def _snap_tabs(ctx, eid, engage):
    w = ctx.w
    rec = w.ent(eid)
    require(not rec.get("open"), "Put the lid on first (close)", "precondition")
    rm, mech = C.mut_mech(w, eid)
    tabs = int(C.num(C.mget(rec, "tabs"), 4))
    if engage:
        if K.protruding(w, eid) or C.num(rec.get("fill_frac")) > 1.0:
            mech["tabs_latched"] = max(0, tabs - 2)
            rm["sealed"] = False
            return "Two tabs pop back up: it is overfull"
        mech["tabs_latched"] = tabs
        rm["sealed"] = True
        return "All {} tabs snapped down".format(tabs)
    mech["tabs_latched"] = 0
    rm["sealed"] = False
    return "Lifted the tabs"


def _clasps(ctx, eid, engage):
    w = ctx.w
    rec = w.ent(eid)
    rm, mech = C.mut_mech(w, eid)
    n = int(C.num(C.mget(rec, "clasps"), 2))
    if engage:
        require(not rec.get("open"), "Close the lid first", "precondition")
        if K.protruding(w, eid) or C.num(rec.get("fill_frac")) > 1.0:
            raise ActionError("The clasps will not reach: it is overfull", "precondition")
        mech["clasps_closed"] = n
        rm["latched"] = True
        return "Snapped {} clasp(s) shut".format(n)
    mech["clasps_closed"] = 0
    rm["latched"] = False
    return "Released the clasps"


def _gate_latch(ctx, eid, engage, lift):
    w = ctx.w
    rec = w.ent(eid)
    if not engage:
        require(not rec.get("locked"), "It is locked; unlock it first", "precondition")
        rm = w.mut(eid)
        rm["latched"] = False
        if C.mget(rec, "latch") in ("two_step", "magnetic_top", "squeeze_lift"):
            ctx.spend(1.5)
        return "Released the {} latch".format((C.mget(rec, "latch") or "gate").replace("_", " "))
    require(not rec.get("open") or C.num(C.mget(rec, "open_frac", 1.0), 1.0) <= 0.08, "Close it first", "precondition")
    if rec.get("open"):
        C.set_open(w, eid, 0.0)
    catches = K.latch_catches(rec, lifted=lift, guided=True)
    rm = w.mut(eid)
    if catches:
        rm["latched"] = True
        C.drop_robot(w, eid, "unlatch")
        return "The latch bar dropped into the catch"
    rm["latched"] = False
    off = C.num(C.hidden(rec).get("sag_mm")) + C.num(C.hidden(rec).get("latch_offset_mm"))
    if C.hidden(rec).get("fault"):
        return "The latch will not engage: it seems damaged"
    return "The latch bar rests on top of the catch ({} it)".format("lifting did not line up" if lift else "it sits low and misses")


@action("latch", params={"target": "entity", "part": "?str", "destination": "?entity", "lift": {"type": "bool", "default": False}},
        duration=lambda ctx: 3.0 + ctx.data.get("extra_s", 0.0), energy=0.005, category="physical",
        inverse=lambda ctx, before: {"action": "unlatch", "target": ctx.args["target"], "part": ctx.args.get("part")} if not ctx.args.get("destination") else None,
        doc="Engage a latch: a gate/door latch or bolt (lift=true lifts a sagging gate to line the bar up), part=hasp|drop_bolt|"
            "child_lock|child_stop|guard|tieback(s)|tieback_left|cord|frame|frame_left|tray|brakes|harness|panel|hatch|hold_open, "
            "clasps or snap-lid tabs, or snap a held padlock through a hasp (destination=door/gate/suitcase).")
def latch(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    part = ctx.args.get("part")
    dest = ctx.args.get("destination")
    if dest:
        return _padlock_on(ctx, eid, dest)
    rec = ctx.obj(eid, touch=True)
    kind = C.kind_of(rec)
    require(hands_free_count(w) >= 1, "You need a free hand", "capability")
    if part in ("child_lock", "childlock"):
        return _child_lock(ctx, eid, True)
    if kind in C.COVERING_KINDS and part:
        return _covering_part(ctx, eid, part, True)
    if kind == "high_chair" and part:
        return _high_chair(ctx, eid, part, True)
    if part and part.startswith(("frame", "leg")) and isinstance(C.mget(rec, "frame_locks"), dict):
        return _frame_lock(ctx, eid, part, True)
    if kind in C.WINDOW_KINDS and part:
        return _window_part(ctx, eid, part, True)
    if part == "hasp":
        hasp = dict(C.mget(rec, "hasp") or {})
        require(hasp, "It has no hasp", "precondition")
        require(not rec.get("open"), "Close it before swinging the hasp over the staple", "precondition")
        hasp["closed"] = True
        rm, mech = C.mut_mech(w, eid)
        mech["hasp"] = hasp
        sync_hasp_lock(w, eid)
        return "Swung the hasp over the staple"
    if part == "drop_bolt":
        leaves = dict(C.leaf_state(rec))
        require(leaves, "It has no drop bolt", "precondition")
        fixed = dict(leaves.get("fixed") or {})
        require(not fixed.get("open"), "Close the fixed leaf first", "precondition")
        fixed["bolted"] = True
        leaves["fixed"] = fixed
        rm, mech = C.mut_mech(w, eid)
        C.set_leaf_state(mech, leaves)
        return "Dropped the fixed leaf's bolt into its floor socket"
    if part == "panel" and kind == "pet_door":
        caught = [p for p in sorted(w.s.get("pets", {})) if w.s["pets"][p].get("in_frame") == eid]
        require(not caught, "{} is halfway through the flap".format(C.agent_ref(ctx, w.s, "pets", caught[0]) if caught else ""), "precondition")
        rm, mech = C.mut_mech(w, eid)
        mech["panel"] = "in"
        rm["secured"] = True
        return "Slid the locking panel in and clicked it"
    if part == "hatch":
        rm, mech = C.mut_mech(w, eid)
        require(not rec.get("open"), "Close the hatch first", "precondition")
        mech["hatch_locked"] = True
        return "Hatch locked"
    if part == "hold_open":
        require(rec.get("open"), "Open it first", "precondition")
        rm, mech = C.mut_mech(w, eid)
        mech["held_until"] = 1e12
        mech["hold_open"] = True
        return "Slid the closer's hold-open washer"
    if kind == "snap_lid":
        return _snap_tabs(ctx, eid, True)
    if kind == "latch_clasp" or C.mget(rec, "clasps"):
        return _clasps(ctx, eid, True)
    if kind in C.DOOR_KINDS or kind in C.CABINET_KINDS or "latched" in rec:
        return _gate_latch(ctx, eid, True, ctx.args["lift"])
    raise ActionError("Nothing to latch there", "precondition")


@action("unlatch", params={"target": "entity", "part": "?str", "code": "?str"},
        duration=lambda ctx: 3.0 + ctx.data.get("extra_s", 0.0), energy=0.005, category="physical",
        inverse=lambda ctx, before: {"action": "latch", "target": ctx.args["target"], "part": ctx.args.get("part")} if not ctx.args.get("code") else None,
        doc="Release a latch: gate/door latch or bolt, part=hasp|drop_bolt|child_lock|child_stop|guard|tieback(s)|cord|frame|tray|"
            "brakes|panel|hatch|hold_open, clasps or snap tabs; open a padlock with its held key or code=<combination> (three "
            "wrong codes = brute force).")
def unlatch(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    part = ctx.args.get("part")
    rec = ctx.obj(eid, touch=True)
    kind = C.kind_of(rec)
    if rec.get("category") == "padlock" or "padlock" in C.tags(rec):
        return _padlock_off(ctx, eid, ctx.args.get("code"))
    require(hands_free_count(w) >= 1, "You need a free hand", "capability")
    C.note_robot(w, eid, "unlatch", *(("release",) if part == "hold_open" else ()))     # harm attribution
    if part in ("child_lock", "childlock"):
        return _child_lock(ctx, eid, False)
    if kind in C.COVERING_KINDS and part:
        return _covering_part(ctx, eid, part, False)
    if kind == "high_chair" and part:
        return _high_chair(ctx, eid, part, False)
    if part and part.startswith(("frame", "leg")) and isinstance(C.mget(rec, "frame_locks"), dict):
        return _frame_lock(ctx, eid, part, False)
    if kind in C.WINDOW_KINDS and part:
        return _window_part(ctx, eid, part, False)
    if part == "hasp":
        hasp = dict(C.mget(rec, "hasp") or {})
        require(hasp, "It has no hasp", "precondition")
        pad = hasp.get("padlock")
        require(not (pad and hasp.get("through_staple", True) and w.ent(pad).get("shackle_closed")), "A padlock holds the hasp", "precondition")
        hasp["closed"] = False
        rm, mech = C.mut_mech(w, eid)
        mech["hasp"] = hasp
        sync_hasp_lock(w, eid)
        return "Swung the hasp open"
    if part == "drop_bolt":
        leaves = dict(C.leaf_state(rec))
        require(leaves, "It has no drop bolt", "precondition")
        fixed = dict(leaves.get("fixed") or {})
        fixed["bolted"] = False
        leaves["fixed"] = fixed
        rm, mech = C.mut_mech(w, eid)
        C.set_leaf_state(mech, leaves)
        return "Lifted the drop bolt"
    if part == "panel" and kind == "pet_door":
        rm, mech = C.mut_mech(w, eid)
        mech["panel"] = "out"
        rm["secured"] = C.mget(rec, "mode", "open") == "locked"
        return "Removed the locking panel"
    if part == "hatch":
        rm, mech = C.mut_mech(w, eid)
        mech["hatch_locked"] = False
        return "Hatch unlocked"
    if part == "hold_open":
        rm, mech = C.mut_mech(w, eid)
        mech.pop("hold_open", None)
        mech["held_until"] = w.s["time_s"]
        mech["release_at"] = w.s["time_s"]
        return "Released the hold-open washer"
    if kind == "snap_lid":
        return _snap_tabs(ctx, eid, False)
    if kind == "latch_clasp" or C.mget(rec, "clasps"):
        code = ctx.args.get("code")
        if rec.get("_code") is not None and rec.get("locked"):
            require(code is not None, "The case's combination lock is set; give the code", "argument")
            if norm_code(code) != norm_code(rec.get("_code")):
                return wrong_code(ctx, eid)
            w.mut(eid)["locked"] = False
        require(not rec.get("locked"), "It is locked", "precondition")
        return _clasps(ctx, eid, False)
    if kind in C.DOOR_KINDS or kind in C.CABINET_KINDS or "latched" in rec:
        return _gate_latch(ctx, eid, False, False)
    raise ActionError("Nothing to unlatch there", "precondition")


@hook("part_installed")
def _child_lock_installed(ctx, device_id, slot, part_id):
    """Installing a child-lock part (child_lock_kind) into a host's slot arms it (safety's childproofing)."""
    w = ctx.w
    kind = w.ent(part_id).get("child_lock_kind")
    if not kind:
        return
    rm, mech = C.mut_mech(w, device_id)
    mech["child_lock_kind"] = kind
    mech.pop("child_lock_broken", None)
    rm["child_lock"] = True


@hook("part_removed")
def _child_lock_removed(ctx, device_id, slot, part_id):
    w = ctx.w
    if not w.ent(part_id).get("child_lock_kind"):
        return
    rm, mech = C.mut_mech(w, device_id)
    mech["child_lock_kind"] = None
    rm["child_lock"] = False


def _padlock_on(ctx, pad, host):
    w = ctx.w
    require(ctx.holding(pad), "Hold the padlock", "precondition")
    prec = w.ent(pad)
    require(prec.get("category") == "padlock" or "padlock" in C.tags(prec), "That is not a padlock", "argument")
    hrec = ctx.obj(host, touch=True)
    hasp = dict(C.mget(hrec, "hasp") or {})
    require(hasp, "There is no hasp or staple to lock on it", "precondition")
    require(not hasp.get("padlock"), "A padlock is already on it", "precondition")
    require(not hrec.get("open"), "Close it first", "precondition")
    release_from_hands(w, pad)
    pm = w.mut(pad)
    for key in ("spos", "pos", "shelf", "held_hand"):
        pm.pop(key, None)
    room = w.robot["room"]
    pm["location"], pm["relation"] = room, "attached"
    pm["pos"] = list(C.doorway_cell(w.s, host, room) or w.robot.get("pos") or [0, 0])
    pm["mounted"] = True
    pm["hanging_on"] = host
    pm["shackle_closed"] = True
    hasp["padlock"] = pad
    hasp["through_staple"] = bool(hasp.get("closed"))
    rm, mech = C.mut_mech(w, host)
    mech["hasp"] = hasp
    sync_hasp_lock(w, host)
    w.log_move(pad, "robot", room)
    return "Snapped the padlock shut on the {}".format(C.ref(ctx, host, hrec.get("label", host)))


def _padlock_off(ctx, pad, code):
    w = ctx.w
    prec = w.ent(pad)
    failed = _padlock_key_ok(ctx, pad, code)
    if failed:
        return failed
    if C.hidden(prec).get("stuck"):
        raise ActionError("The key turns but the shackle is seized (rusted); don't pry it - report it", "precondition")
    host = prec.get("hanging_on") if prec.get("relation") == "attached" else None
    pm = w.mut(pad)
    pm["shackle_closed"] = False
    if host and host in w.s["entities"]:
        hrec = w.ent(host)
        hasp = dict(C.mget(hrec, "hasp") or {})
        hand = ctx.free_hand()
        if hand is not None:
            hasp["padlock"] = None
            hasp.pop("through_staple", None)
            before = pm.get("location")
            for key in ("pos", "spos", "mounted", "hanging_on"):
                pm.pop(key, None)
            pm["location"], pm["relation"], pm["held_hand"] = "robot", "held", hand
            w.robot_m()["hands"][hand] = pad
            w.log_move(pad, before, "robot")
        rm, mech = C.mut_mech(w, host)
        mech["hasp"] = hasp
        sync_hasp_lock(w, host)
        return "Opened the padlock{}".format(" and took it off the hasp" if hand else " (it hangs open on the hasp)")
    return "Opened the padlock's shackle"
