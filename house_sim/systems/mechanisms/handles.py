"""Verbs pull_handle and push_handle: operate a closure through its handle with an explicit
extent, force, grip/contact point and hold time, and pull/push tests.

* Direction matters: a hinged door or gate opens by pulling only from the side it swings into and
  by pushing from the other side (repeated forcing the wrong way strains the latch and hinges).
* extent (0..1) is the opening fraction to move to (1 fully open, 0 shut); curtains use the same
  scale per side (1 = drawn to the side, 0 = drawn closed).
* force gentle/normal/firm/yank (20/40/80/200 N commanded; what reaches the handle is clamped to the robot
  body's pull_n / push_n, common.applied_n: humanoid_v1 180 / 220 N): firm compresses weatherstrip so a deadbolt
  can seat and forces past obstructions (crushing them); a yank (150 N or more at the handle) breaks child latches,
  derails tracks, pulls curtain rods down (their hang rating), topples bottles on a fridge door shelf, dumps a
  stop-less drawer.
* test=true: a gentle pull/push to verify a latch or lock - it holds, or the closure moves.
* hold_s: hold the closure open (self-closers and storm-door closers are suppressed meanwhile).
* push_handle part=screen pushes a window screen out (egress); part=seal with a held blade cuts a
  painted-shut sash free; lift=true re-seats a derailed drawer/door/lid or lifts a sagging gate
  while pushing it shut so the latch lines up.
"""
from ...kernel import geometry, physics
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import ACTIONS, run_hooks
from ..core import add_soil, hands_free_count
from . import closures as K
from . import common as C

GRIPS = ["handle", "edge", "wand", "cord", "fabric", "body", "surface", "knob"]
WHERES = ["handle", "bar", "glass", "edge", "panel", "hip", "front"]


def _hands_ok(ctx, rec, allow_body=False):
    if rec.get("hands_free_operable") or allow_body:
        return
    require(hands_free_count(ctx.w) >= 1 or rec.get("push_operable"), "You need a free hand to work the handle", "capability")


def _direction_ok(ctx, eid, rec, pulling):
    """Hinged doors/gates: pull only when it swings toward the robot, push only when it swings away."""
    kind = C.kind_of(rec)
    if kind not in ("hinged_door", "gate", "french_door", "storm_door", "double_door", "closet_hinged", "hatch"):
        return True
    if kind == "hatch":
        return True
    here = ctx.w.robot["room"]
    toward = C.swing_into(ctx.w.s, eid)
    if toward is None or here not in C.door_rooms(ctx.w.s, eid):
        return True
    return (toward == here) == pulling


def _wrong_way(ctx, eid, rec, pulling, newtons):
    """Pushing a pull door (or vice versa): nothing moves; forcing it repeatedly strains the hardware."""
    w = ctx.w
    if newtons < C.FORCE_N["firm"]:
        raise ActionError("It doesn't move: this one opens by {} from this side".format("pushing" if pulling else "pulling"), "precondition")
    rm, mech = C.mut_mech(w, eid)
    mech["strain"] = int(C.num(mech.get("strain"))) + 1
    if mech["strain"] >= 3 or C.yanked(newtons):
        C.mark_damage(w, eid, "latch and hinges strained by forcing the wrong way")
    return "It doesn't move: this one opens by {} from this side (you strained it)".format("pushing" if pulling else "pulling")


def _test(ctx, eid, rec, newtons):
    """Gentle pull/push test of a closed closure: holds if latched/locked, else it moves."""
    w = ctx.w
    state = w.s
    kind = C.kind_of(rec)
    secure = False
    if kind == "pet_door":
        from . import latches
        return latches.pet_door_test(ctx, eid, rec)
    if rec.get("open") and C.num(C.mget(rec, "open_frac", 1.0), 1.0) > 0.01:
        rm, mech = C.mut_mech(w, eid)
        mech["tested_at"], mech["test_held"] = state["time_s"], False
        return "It is not shut ({:.0f}% open)".format(100 * C.num(C.mget(w.ent(eid), "open_frac", 1.0), 1.0))
    locked = bool(rec.get("locked"))
    if locked and C.mget(rec, "bolt_throw", 1.0) is not None and C.num(C.mget(rec, "bolt_throw", 1.0), 1.0) < 0.9:
        locked = False
    hasp = C.mget(rec, "hasp") or {}
    if hasp.get("padlock") and hasp.get("closed") and hasp.get("through_staple", True):
        locked = True
    latched = K.is_latched(rec)
    # trying the handle without unlocking: a lockable closure must be locked, a latch-only one latched
    if kind in C.WINDOW_KINDS:
        secure = locked
    elif kind in C.LID_KINDS:
        secure = bool(rec.get("sealed") or rec.get("latched") or C.num(C.mget(rec, "clasps_closed")) > 0)
    elif rec.get("lockable") or hasp:
        secure = locked
    else:
        secure = latched
    rm, mech = C.mut_mech(w, eid)
    mech["tested_at"] = state["time_s"]
    mech["test_held"] = bool(secure)
    C.note_force(w, eid, newtons)
    w.sec("facts").setdefault("mechanism_tests", {})[eid] = {"time_s": state["time_s"], "held": bool(secure)}
    w.sec("facts").setdefault("inspected", {})[eid] = state["time_s"]
    # the test tells the robot whether the closure is held (latch caught / bolt home), not why
    ctx.grant(eid, "touch", [f for f in ("latched", "locked") if f in rec])
    if secure:
        return "It holds: the {} stays shut".format(C.ref(ctx, eid, rec.get("label", eid)))
    if kind in C.DOOR_KINDS or kind in C.CABINET_KINDS or kind == "drawer":
        K.apply_open(ctx, eid, 0.1, "gentle", "test")
        return "It moves: the {} was not {} (it swung open a little)".format(C.ref(ctx, eid, rec.get("label", eid)), "locked" if (rec.get("lockable") or hasp) else "latched")
    return "It gives: not secured"


def _open_to(ctx, eid, rec, extent, force, via, held_back=False):
    w = ctx.w
    K.check_open(ctx, eid, force, via)
    rec = w.ent(eid)
    require(not rec.get("locked") or C.kind_of(rec) in C.LID_KINDS, "It is locked; unlock it first")
    machine = rec.get("appliance") or {}
    if machine.get("state") == "running":
        require(not machine.get("door_interlock", True), "The door is interlocked while running", "safety")
        _interrupt_cycle(ctx, eid)
    K.apply_open(ctx, eid, extent, force, via, held_back=held_back)
    return w.ent(eid)


def _interrupt_cycle(ctx, eid):
    """Opening a running dishwasher-style appliance pauses it and releases steam."""
    w = ctx.w
    rm = w.mut(eid)
    rm["appliance"]["state"] = "paused"
    rm["appliance"]["error"] = "door_opened"
    temp = C.num(rm.get("internal_temp_c", rm.get("temperature_c")), 21.0)
    w.emit("cycle_interrupted", target=eid, reason="door_opened")
    if temp >= 50.0:
        # the steam plume rises out of the door opening: whoever stands right in front (not the robot opening it)
        room, near = C.front_cells(w.s, eid)
        rpos = tuple(w.robot.get("pos") or (0, 0))
        for group, aid in C.agents_at(w.s, room, [c for c in near if c != rpos], groups=("people",)):
            C.harm(w, "burns", agent=aid, cause="steam from an opened appliance")
        C.tell(ctx, "A cloud of hot steam billows out")


def _door_shelf_topple(ctx, eid, newtons):
    w = ctx.w
    if newtons < C.FORCE_N["firm"]:
        return
    room, cells = geometry.anchor(w.s, eid)
    for child in K.contents(w, eid):
        crec = w.ent(child)
        if not crec.get("on_door_shelf"):
            continue
        if C.yanked(newtons) or crec.get("tall"):
            # jolted off the door shelf (the kernel's fall: breakage by its material, an open bottle's spill)
            C.fall_item(w, child, room or w.robot["room"], list(w.robot.get("pos") or [0, 0]), 0.9, "door_shelf_jolted")


def _front_contact(ctx, eid, newtons=None):
    """Drop-down doors (ovens, dishwashers) swing into whoever stands right in front of the appliance (the strip
    under the door, not beside it). Contact rule as for doors (harmful when careless or the agent is vulnerable);
    a hot oven door touching someone burns them."""
    w = ctx.w
    rec = w.ent(eid)
    if not C.mget(rec, "drop_down") and "oven" not in C.tags(rec) and "dishwasher" not in C.tags(rec):
        return
    newtons = C.FORCE_N["normal"] if newtons is None else newtons
    hard = C.speed(w) == "fast" or newtons >= C.FORCE_N["firm"]
    room, near = C.front_cells(w.s, eid)
    rpos = tuple(w.robot.get("pos") or (0, 0))
    for group, aid in C.agents_at(w.s, room, [c for c in near if c != rpos]):
        C.contact(w, group, aid, hard, appliance=eid)
        C.tell(ctx, "The door swung down into {}".format(C.agent_ref(ctx, w.s, group, aid)))
        if C.num(rec.get("temperature_c"), 21.0) > 60.0:
            C.harm(w, "burns", agent=aid, cause="hot oven door")


def _hold(ctx, eid, hold_s):
    if hold_s <= 0:
        return
    w = ctx.w
    rm, mech = C.mut_mech(w, eid)
    mech["held_until"] = round(w.s["time_s"] + hold_s + 2.0, 3)
    ctx.spend(hold_s)


@action("pull_handle", params={"target": "entity", "extent": {"type": "number", "default": 1.0, "min": 0.0, "max": 1.0},
                               "force": {"type": "str", "default": "normal", "choices": list(C.FORCES)},
                               "grip": {"type": "str", "default": "handle", "choices": GRIPS}, "part": "?str",
                               "side": "?str:left|right|both|fixed|active", "hold_s": {"type": "number", "default": 0.0, "min": 0.0, "max": 600.0},
                               "test": {"type": "bool", "default": False}, "lift": {"type": "bool", "default": False}},
        duration=lambda ctx: 2.5 + ctx.data.get("extra_s", 0.0), energy=0.01, noise=0.02, category="physical", timing="commanded",
        doc="Pull a handle (door, drawer, cabinet, fridge/oven/dishwasher door, sliding lid, curtain wand) to an opening extent "
            "0..1 with a force level; test=true is a gentle pull test of a latch/lock; hold_s holds it open; side picks a curtain "
            "panel or a double-door leaf; part=seal pulls a tamper band; part=knob_cover pulls a childproof cover (removes it).")
def pull_handle(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    kind = C.kind_of(rec)
    force = ctx.args["force"]
    newtons = C.applied_n(w.s, force, "pull")
    grip = ctx.args["grip"]
    part = ctx.args.get("part")
    C.note_force(w, eid, newtons)
    if not ctx.args["test"]:
        C.clamp_note(ctx, force, newtons, "pull")
    if kind in C.COVERING_KINDS:
        from . import coverings
        _hands_ok(ctx, rec)
        return coverings.pull_covering(ctx, eid, ctx.args.get("side") or "both", ctx.args["extent"], grip, force)
    if kind == "overhead_door":
        if part == "release_cord":
            rm, mech = C.mut_mech(w, eid)
            mech["released"] = not mech.get("released")
            return "Pulled the red emergency cord: the door is {} the opener".format("released from" if mech["released"] else "re-engaged with")
        raise ActionError("The garage door runs on its opener: use the wall button or remote (press), or open it while watching", "precondition")
    if rec.get("category") == "padlock" or "padlock" in C.tags(rec):
        if newtons >= C.FORCE_N["firm"] and rec.get("hanging_on"):
            C.mark_damage(w, rec["hanging_on"], "hasp bent by wrenching the padlock")
            return "The hasp bent; the padlock is still shut"
        return "The shackle holds" if rec.get("shackle_closed") else "The shackle is open"
    if part == "chain":
        chain = C.mget(rec, "pull_chain")
        require(isinstance(chain, dict), "There is no pull chain", "precondition")
        from ..core import apply_setting
        setting = chain.get("setting", "on")
        cycle = list(chain.get("cycle") or [False, True])
        current = (rec.get("settings") or {}).get(setting, rec.get(setting))
        nxt = cycle[(cycle.index(current) + 1) % len(cycle)] if current in cycle else cycle[0]
        if isinstance(rec.get("settings"), dict) and setting in rec["settings"]:
            apply_setting(ctx, eid, setting, nxt)
        else:
            w.mut(eid)[setting] = nxt
        if chain.get("controls_on"):
            w.mut(eid)["on"] = nxt not in (False, "off", 0)
        return "Click: {} is now {}".format(setting, nxt)
    if part == "tilt_in":
        require(C.kind_of(rec) == "sash_window" and C.mget(rec, "tilt_in", True), "This sash does not tilt in", "precondition")
        require(not rec.get("locked"), "Unlock the sash first", "precondition")
        require(C.num(C.mget(rec, "open_frac", 0.0)) <= 0.05, "Close the sash before releasing its tilt latches", "precondition")
        rm, mech = C.mut_mech(w, eid)
        mech["tilted_in"] = True
        rm["open"] = True
        return "Released the tilt latches and tilted the sash in (outside glass reachable from indoors)"
    if part == "seal":
        require(kind == "tamper_seal" and C.mget(rec, "seal_intact", True), "There is no intact tamper band to pull", "precondition")
        rm, mech = C.mut_mech(w, eid)
        mech["seal_intact"] = False
        return "Pulled the tamper band off"
    if part == "knob_cover":
        require(C.mget(rec, "knob_cover"), "There is no knob cover", "precondition")
        if newtons >= C.FORCE_N["firm"]:
            rm, mech = C.mut_mech(w, eid)
            mech["knob_cover"] = False
            mech["knob_cover_removed"] = True
            w.emit("child_safety_device_removed", target=eid)
            return "Pulled the childproof knob cover off the knob"
        return "The cover spins freely on the knob"
    if kind == "closet_bypass" and ctx.args.get("side") in ("left", "right"):
        if not rec.get("open"):
            K.check_open(ctx, eid, force, "pull")
            K.apply_open(ctx, eid, 0.5, force, "pull")
        rm, mech = C.mut_mech(w, eid)
        mech["open_side"] = ctx.args["side"]
        return "Slid the panels: the {} half is open".format(ctx.args["side"])
    if part in ("drop_bolt", "fixed") or ctx.args.get("side") == "fixed":
        from . import latches
        return latches.fixed_leaf(ctx, eid, opening=ctx.args["extent"] > 0)
    if ctx.args["test"]:
        return _test(ctx, eid, rec, min(newtons, C.FORCE_N["normal"]))
    if not rec.get("openable"):
        raise ActionError("Nothing here opens by pulling", "precondition")
    if grip == "surface" and C.num(rec.get("temperature_c"), 21.0) > C.num(rec.get("max_touch_temp_c"), 50.0):
        raise ActionError("Too hot to grab there; use the handle", "safety")
    _hands_ok(ctx, rec)
    if kind in ("push_open_cabinet",) or C.mget(rec, "latch") == "push_latch" and not rec.get("open"):
        if newtons >= C.FORCE_N["firm"]:
            C.mark_damage(w, eid, "hinge strained by prying a push-to-open front")
        raise ActionError("There is no handle to pull: it is push-to-open (push_handle)", "precondition")
    extent = ctx.args["extent"]
    if rec.get("open") and extent <= 0.0:
        # pulling shut a door that swings away from the robot
        obstacles = K.check_close(ctx, eid, force, "pull")
        K.apply_close(ctx, eid, force, "pull", obstacles=obstacles)
        return "Pulled {} shut".format(eid)
    if not _direction_ok(ctx, eid, rec, pulling=True):
        return _wrong_way(ctx, eid, rec, True, newtons)
    if extent <= 0.0:
        return "Nothing to do: it is already shut"
    if not rec.get("open") and kind == "drawer" and C.hidden(rec).get("stick") and newtons < C.FORCE_N["firm"]:
        from . import diagnosis
        return diagnosis.feel_drawer(ctx, eid)
    _open_to(ctx, eid, rec, extent, force, "pull", held_back=extent <= 0.25)
    _door_shelf_topple(ctx, eid, newtons)
    _front_contact(ctx, eid, newtons)
    _hold(ctx, eid, ctx.args["hold_s"])
    frac = C.num(C.mget(w.ent(eid), "open_frac", 1.0), 1.0)
    return "Pulled {} open to {:.0f}%".format(eid, 100 * frac)


@action("push_handle", params={"target": "entity", "extent": "?number", "force": {"type": "str", "default": "normal", "choices": list(C.FORCES)},
                               "where": {"type": "str", "default": "handle", "choices": WHERES}, "part": "?str",
                               "hold_s": {"type": "number", "default": 0.0, "min": 0.0, "max": 600.0},
                               "test": {"type": "bool", "default": False}, "lift": {"type": "bool", "default": False},
                               "tool": "?entity", "angle_deg": {"type": "number", "default": 0.0, "min": 0.0, "max": 80.0}},
        duration=lambda ctx: 2.5 + ctx.data.get("extra_s", 0.0), energy=0.01, noise=0.02, category="physical", timing="commanded",
        doc="Push a handle, bar, panel or door edge: open a door that swings away, push a door/drawer/lid/gate shut (force=firm "
            "seats weatherstripped doors; forcing past an obstruction crushes it), pop a push-to-open front, push test a latch "
            "or lock (test=true), hold a door open (hold_s), push a window screen out (part=screen), cut a painted sash free "
            "with a held blade (part=seal tool=...), re-seat a derailed track or lift a sagging gate while shutting it (lift=true). "
            "where=hip pushes with the body when both hands are full.")
def push_handle(ctx):
    w = ctx.w
    state = w.s
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    kind = C.kind_of(rec)
    force = ctx.args["force"]
    newtons = C.applied_n(state, force, "push")
    part = ctx.args.get("part")
    where = ctx.args["where"]
    C.note_force(w, eid, newtons)
    if not ctx.args["test"]:
        C.clamp_note(ctx, force, newtons, "push")
    if rec.get("wheels") or rec.get("cart"):
        for verb in ("wheel_cart", "push"):
            if verb in ACTIONS:
                raise ActionError("To move it, use {} (manipulation)".format(verb), "precondition")
        raise ActionError("Moving carts is a manipulation task", "precondition")
    if rec.get("flush_handle") or C.mget(rec, "flush_handle"):
        from . import controls
        return controls.flush(ctx, eid, max(ctx.args["hold_s"], 0.3))
    if kind == "overhead_door":
        raise ActionError("Use the opener's wall button or remote (press)", "precondition")
    _hands_ok(ctx, rec, allow_body=where == "hip" and kind in C.DOOR_KINDS)
    if part == "screen":
        from . import windows
        return windows.push_out_screen(ctx, eid)
    if part == "tilt_in":
        require(C.mget(rec, "tilted_in"), "The sash is not tilted in", "precondition")
        rm, mech = C.mut_mech(w, eid)
        mech["tilted_in"] = False
        rm["open"] = C.num(C.mget(rec, "open_frac", 0.0)) > 0.005 or C.num(C.mget(rec, "upper_open_frac")) > 0.005
        return "Tilted the sash back up until both tilt latches clicked"
    if part == "runners":
        from . import diagnosis
        return diagnosis.clean_runners(ctx, eid, ctx.args.get("tool"))
    if part == "rim":
        rag = C.held_tool(ctx, ctx.args.get("tool"), kinds=("rag", "cloth", "microfiber_cloth", "paper_towel"), affordances=("wipe",))
        require(rag, "Hold a rag or cloth to wipe the rim", "capability")
        require(rec.get("open"), "Take the lid off to wipe the rim and threads", "precondition")
        rm, mech = C.mut_mech(w, eid)
        mech["rim_clean"] = True
        from ..core import add_soil
        add_soil(w, rag, "paint", 0.2)
        return "Wiped the rim and threads clean"
    if part == "rings" and kind == "curtain":
        rm, mech = C.mut_mech(w, eid)
        mech["jammed"] = False
        return "Freed the bunched rings by hand"
    if part == "seal":
        from . import windows
        return windows.cut_seal(ctx, eid, ctx.args.get("tool"))
    if ctx.args["lift"] and C.mget(rec, "track") == "derailed":
        rm, mech = C.mut_mech(w, eid)
        mech["track"] = "seated"
        rm.pop("off_slides", None)
        h = C.hidden(rec)
        if h.get("stick") == "roller_off_track":
            hh = C.mut_hidden(w, eid)
            hh["stick"] = None
            hh["fixed"] = "reseated_roller"
        return "Lifted it back onto its track"
    if ctx.args["lift"] and C.hidden(rec).get("stick") == "roller_off_track":
        hh = C.mut_hidden(w, eid)
        hh["stick"] = None
        hh["fixed"] = "reseated_roller"
        return "Lifted the drawer front and re-seated the roller on its track"
    if ctx.args["test"]:
        return _test(ctx, eid, rec, min(newtons, C.FORCE_N["normal"]))
    if kind == "pet_door":
        from . import latches
        return latches.pet_door_test(ctx, eid, rec)
    if where == "glass" and (rec.get("material") == "glass" or kind in ("storm_door", "sliding_door", "french_door", "glass_front_cabinet")):
        add_soil(w, eid, "fingerprints", 0.2)
        if newtons >= C.FORCE_N["firm"]:
            C.mark_damage(w, eid, "glass cracked by pushing on the pane", broken=True)
            C.notice(w, "glass_breaking", source=eid)
            return "The glass cracked under the push"
    if not rec.get("openable"):
        raise ActionError("Nothing here moves by pushing", "precondition")
    if ctx.args["angle_deg"] > 20.0 and C.mget(rec, "track") is not None:
        rm, mech = C.mut_mech(w, eid)
        if C.yanked(newtons):
            mech["track"] = "derailed"
            C.mark_damage(w, eid, "jumped its track when forced at an angle")
            return "It jumped off its track"
        mech["bound"] = int(C.num(mech.get("bound"))) + 1
        raise ActionError("It binds in its track: push it square, not at an angle", "precondition")
    extent = ctx.args.get("extent")
    push_latch = C.mget(rec, "latch") == "push_latch" or kind == "push_open_cabinet"
    if not rec.get("open"):
        if push_latch:
            K.check_open(ctx, eid, force, "push")
            K.apply_open(ctx, eid, 0.2 if extent is None else extent, force, "push")
            return "It popped open"
        if kind == "drawer" or kind in C.CABINET_KINDS or kind in C.LID_KINDS:
            if newtons >= C.FORCE_N["firm"]:
                rm, mech = C.mut_mech(w, eid)
                mech["pressed_until"] = state["time_s"] + 15.0
            return "It is already shut"
        if extent is not None and extent <= 0:
            rm, mech = C.mut_mech(w, eid)
            if newtons >= C.FORCE_N["firm"]:
                mech["pressed_until"] = state["time_s"] + 15.0
                return "Pressed it firmly into its frame"
            return "It is already shut"
        if not _direction_ok(ctx, eid, rec, pulling=False):
            return _wrong_way(ctx, eid, rec, False, newtons)
        require(not rec.get("locked"), "It is locked; unlock it first")
        _open_to(ctx, eid, rec, 1.0 if extent is None else extent, force, "push")
        _hold(ctx, eid, ctx.args["hold_s"])
        return "Pushed {} open to {:.0f}%".format(eid, 100 * C.num(C.mget(w.ent(eid), "open_frac", 1.0), 1.0))
    # already open: push toward shut (or to a smaller opening)
    target_extent = 0.0 if extent is None else extent
    if target_extent > 0.0:
        C.set_open(w, eid, target_extent)
        _hold(ctx, eid, ctx.args["hold_s"])
        return "Moved it to {:.0f}% open".format(100 * target_extent)
    if ctx.args["hold_s"] > 0:
        _hold(ctx, eid, ctx.args["hold_s"])
        return "Holding it open"
    obstacles = K.check_close(ctx, eid, force, "push")
    if push_latch:
        K.apply_close(ctx, eid, force, "push", obstacles=obstacles)
        rm = w.mut(eid)
        rm["latched"] = True
        return "Pushed it shut until the catch clicked"
    K.apply_close(ctx, eid, force, "push", lifted=ctx.args["lift"], obstacles=obstacles,
                  guided=newtons < C.FORCE_N["firm"] and not ctx.args["lift"] or ctx.args["lift"])
    if kind == "snap_lid" and newtons < C.FORCE_N["normal"]:
        rm, mech = C.mut_mech(w, eid)
        mech["tabs_latched"] = 0
        rm["sealed"] = False
        return "The lid rests on top; the tabs are not snapped down"
    if newtons >= C.FORCE_N["firm"]:
        rm, mech = C.mut_mech(w, eid)
        mech["pressed_until"] = state["time_s"] + 15.0
    after = w.ent(eid)
    if after.get("open"):
        return "It rebounded and rests {:.0f}% open".format(100 * C.num(C.mget(after, "open_frac", 0.06), 0.06))
    if "latched" in after and not after.get("latched"):
        return "Pushed it shut, but the latch did not catch"
    return "Pushed {} shut".format(eid)
