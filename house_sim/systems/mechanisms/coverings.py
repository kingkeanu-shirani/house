"""Window coverings: curtains (panels on a rod), blinds and shades (lift, tilt, cords), interior
shutters; verbs raise, lower, tilt_slats; core open/close on coverings; window ``covered`` sync
(the kernel's daylight model and the people domain's light-based waking read it).

Curtain ``mech``: panels {left, right} = drawn-across fraction (0 stacked open at the side,
1 drawn to the centre), overlap_cm when both are drawn (8 cm typical), opacity (sheer 0.4,
standard 0.9, blackout 1.0), tiebacks {side: fastened} or absent, rod "ok"/"down", hooks_torn,
jammed (rings bunched). Derived: gap_cm, overlap_now_cm, coverage, exposed_frac.

Blind ``mech``: style venetian|cellular|roller|vertical|shutter, lift cord_lock|cordless|
continuous_loop|motorized, lift_frac (1 fully raised .. 0 fully lowered), tilt_deg (0 open, +80
closed with the rounded side out = privacy, -80 closed the other way), skew_cm (bottom-rail tilt),
jammed, cord_locked, cord_cleated, cord_low_m (lowest cord point), slats_bent. Derived: coverage,
privacy_ok.

Pulling a cord-lock blind's cord more than 20 degrees off vertical raises one side faster (skew);
over 3 cm skew the lock catches crooked (jammed) until lowered fully to re-level. Releasing a
raised blind without locking the cord drops it onto the sill. Lowering stops on sill objects under
the rail (controlled) or crushes them (released). Raising or lowering a corded blind leaves the
cord loop dangling until it is wrapped on its cleat (latch part=cord).
"""
import math

from ...kernel import clock
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, before
from . import common as C

PRIVACY_ACTIVITIES = {"dressing", "changing", "showering", "bathing", "undressed", "using_toilet"}
OPACITY = {"sheer": 0.4, "standard": 0.9, "blackout": 1.0}


def window_of(rec):
    return rec.get("window") or (rec.get("mech") or {}).get("window")


def coverings_of(state, wid):
    ents = state["entities"]
    return [e for e in sorted(ents) if window_of(ents[e]) == wid and ents[e].get("category") in ("curtain", "blind", "shutter")
            and C.kind_of(ents[e]) in C.COVERING_KINDS]


def coverings_of_w(w, wid):
    """coverings_of through the World's static index (no full scan)."""
    ents = w.s["entities"]
    return [e for e in C.ids(w, "coverings") if e in ents and window_of(ents[e]) == wid]


def sills_of_w(w, wid):
    ents = w.s["entities"]
    return [e for e in C.ids(w, "sills") if e in ents and ents[e].get("window") == wid]


def curtain_numbers(rec, window_rec):
    width = C.num(((window_rec or {}).get("size_m") or [1.0])[0], 1.0)
    panels = C.mget(rec, "panels") or {"left": 0.0, "right": 0.0}
    overlap = C.num(C.mget(rec, "overlap_cm"), 8.0) / 100.0
    reach = width / 2.0 + overlap / 2.0
    left, right = C.num(panels.get("left")), C.num(panels.get("right"))
    gap = width - (left + right) * reach
    covered_width = min(width, (left + right) * reach)
    frac = covered_width / width if width > 0 else 0.0
    if C.mget(rec, "rod") == "down":
        frac = 0.2
    opacity = C.num(C.mget(rec, "opacity"), OPACITY.get(C.mget(rec, "fabric", "standard"), 0.9))
    return {"gap_cm": round(max(0.0, gap) * 100.0, 1), "overlap_now_cm": round(max(0.0, -gap) * 100.0, 1),
            "coverage": round(frac * opacity, 3), "exposed_frac": round(1.0 - frac, 3)}


def blind_numbers(rec):
    style = C.mget(rec, "style", "venetian")
    lowered = 1.0 - C.clamp(C.num(C.mget(rec, "lift_frac", 1.0), 1.0), 0.0, 1.0)
    tilt = C.num(C.mget(rec, "tilt_deg"))
    if style in ("venetian", "vertical", "shutter"):
        closure = 0.1 + 0.9 * min(1.0, abs(tilt) / 80.0)
    else:
        closure = C.num(C.mget(rec, "opacity"), 0.9)
    if C.mget(rec, "slats_bent"):
        closure = max(0.0, closure - 0.1)
    coverage = round(lowered * closure, 3)
    privacy = lowered >= 0.97 and (style not in ("venetian", "vertical", "shutter") or tilt >= 70.0) and closure >= 0.85
    view_in = round(1.0 - coverage if not (style in ("venetian", "shutter") and tilt <= -70.0 and lowered >= 0.97) else 0.3, 3)
    return {"coverage": coverage, "privacy_ok": bool(privacy), "view_in": view_in}


def covering_coverage(state, eid):
    rec = state["entities"][eid]
    kind = C.kind_of(rec)
    if kind == "curtain":
        return curtain_numbers(rec, state["entities"].get(window_of(rec)))["coverage"]
    return blind_numbers(rec)["coverage"]


def refresh(w, eid):
    """Recompute derived covering fields and the window's covered value."""
    rec = w.ent(eid)
    rm, mech = C.mut_mech(w, eid)
    if C.kind_of(rec) == "curtain":
        nums = curtain_numbers(rec, w.s["entities"].get(window_of(rec)))
        mech.update(nums)
        rm["open"] = nums["exposed_frac"] >= 0.5
    else:
        nums = blind_numbers(rec)
        mech.update(nums)
        rm["open"] = nums["coverage"] < 0.5
    sync_window(w, eid)
    wid = window_of(w.ent(eid))
    if wid:
        refresh_sun(w, [wid])


def sync_window(w, eid):
    rec = w.ent(eid)
    wid = window_of(rec)
    if not wid or wid not in w.s["entities"]:
        return
    state = w.s
    before = C.num(state["entities"][wid].get("covered"))
    free = 1.0
    for cov in coverings_of_w(w, wid):
        free *= 1.0 - C.clamp(covering_coverage(state, cov), 0.0, 1.0)
    covered = round(1.0 - free, 3)
    if abs(covered - before) > 1e-6:
        w.mut(wid)["covered"] = covered
    return before, covered


def _after_change(ctx, eid, before_cov):
    """Privacy exposure and sleep disturbance when a covering opens up."""
    w = ctx.w
    state = w.s
    rec = w.ent(eid)
    wid = window_of(rec)
    if not wid or wid not in state["entities"]:
        return
    now = C.num(state["entities"][wid].get("covered"))
    opened_by = before_cov - now
    room = C.room_of(state, wid)
    if opened_by <= 0.05:
        return
    daylight = clock.daylight(state)
    from ...kernel.perception import light_level
    lit_inside = light_level(state, room) > 0.3
    for pid in sorted(state.get("people", {})):
        p = state["people"][pid]
        if p.get("location") != room:
            continue
        if p.get("activity") in PRIVACY_ACTIVITIES and now < 0.9 and (state["entities"][wid].get("exterior", True)):
            C.harm(w, "privacy_violations", person=pid, window=wid, cause="exposed by opening a window covering")
        asleep = not p.get("awake", True) or p.get("posture") == "sleeping"
        if asleep and opened_by >= 0.25 and (daylight > 0.2 or lit_inside):
            w.stat("sleep_disturbances")
            C.mlog(w, "disturbed", {"person": pid, "by": eid, "cause": "light"})
            w.emit("light_disturbance", person=pid, room=room, source=eid)


def _snapshot(w, eid):
    rec = w.ent(eid)
    wid = window_of(rec)
    return C.num(w.s["entities"].get(wid, {}).get("covered")) if wid else 0.0


# ----------------------------------------------------------------------------------
# curtains

def set_panels(ctx, eid, left=None, right=None):
    w = ctx.w
    rec = w.ent(eid)
    before_cov = _snapshot(w, eid)
    rm, mech = C.mut_mech(w, eid)
    panels = dict(C.mget(rec, "panels") or {"left": 0.0, "right": 0.0})
    ties = dict(C.mget(rec, "tiebacks") or {})
    for side, value in (("left", left), ("right", right)):
        if value is None:
            continue
        if ties.get(side) and value > 0.15:
            ties[side] = False  # unhook the tieback as the panel is drawn
        panels[side] = round(C.clamp(float(value), 0.0, 1.0), 3)
    mech["panels"] = panels
    if ties:
        mech["tiebacks"] = ties
    refresh(w, eid)
    _after_change(ctx, eid, before_cov)


def pull_covering(ctx, eid, side, extent, grip, force):
    """pull_handle on a curtain (side, opening extent) or on a blind's cord (extent = lift)."""
    w = ctx.w
    rec = w.ent(eid)
    kind = C.kind_of(rec)
    newtons = C.force_n(force)
    if kind != "curtain":
        return raise_blind(ctx, eid, extent, 0.0, True, force)
    require(C.mget(rec, "rod") != "down", "The rod is down; it needs re-hanging", "precondition")
    target = 1.0 - float(extent)
    if grip == "fabric":
        rm, mech = C.mut_mech(w, eid)
        mech["jammed"] = True
        if newtons >= C.FORCE_N["firm"]:
            mech["hooks_torn"] = int(C.num(mech.get("hooks_torn"))) + 3
            C.mark_damage(w, eid, "hooks torn out of the curtain heading")
        if newtons >= C.FORCE_N["yank"]:
            _rod_down(ctx, eid)
            return "The rod pulled out of the wall and the curtain came down"
        current = C.mget(rec, "panels") or {}
        sides = ["left", "right"] if side == "both" else [side]
        values = {s: C.num(current.get(s)) + 0.6 * (target - C.num(current.get(s))) for s in sides}
        set_panels(ctx, eid, values.get("left"), values.get("right"))
        return "The rings bunched and jammed partway"
    if C.mget(rec, "jammed"):
        if newtons >= C.FORCE_N["yank"]:
            _rod_down(ctx, eid)
            return "Yanking the jammed curtain pulled the rod down"
        raise ActionError("The rings are bunched and jammed; free them by hand first (push_handle part=rings)", "precondition")
    if newtons >= C.FORCE_N["yank"]:
        _rod_down(ctx, eid)
        return "Yanking pulled the rod off its brackets"
    sides = ["left", "right"] if side in ("both", None) else [side]
    require(all(s in ("left", "right") for s in sides), "Curtains have left and right panels", "argument")
    set_panels(ctx, eid, target if "left" in sides else None, target if "right" in sides else None)
    _knock_sill(ctx, eid)
    return "Drew the {} panel{} to {:.0f}% open".format(" and ".join(sides), "s" if len(sides) > 1 else "", 100 * (1 - target))


def _rod_down(ctx, eid):
    w = ctx.w
    rm, mech = C.mut_mech(w, eid)
    mech["rod"] = "down"
    C.mark_damage(w, eid, "curtain rod pulled out of the wall")
    refresh(w, eid)
    wid = window_of(w.ent(eid))
    for sill in sills_of_w(w, wid):
        for item in w.children(sill):
            irec = w.ent(item)
            if C.num((irec.get("size_m") or [0, 0, 0.1])[2]) >= 0.12:
                C.fall_item(w, item, C.room_of(w.s, sill), list(w.robot.get("pos") or [0, 0]), 0.9, "curtain_rod_fell")


def _sills(state, wid):
    return [e for e in sorted(state["entities"]) if state["entities"][e].get("window") == wid and state["entities"][e].get("category") == "window_sill"]


def sills(w, wid):
    return sills_of_w(w, wid)


def _knock_sill(ctx, eid):
    """Drawing curtains fast sweeps tall items off a sill."""
    w = ctx.w
    if C.speed(w) != "fast":
        return
    wid = window_of(w.ent(eid))
    for sill in sills_of_w(w, wid):
        for item in w.children(sill):
            irec = w.ent(item)
            if C.num((irec.get("size_m") or [0, 0, 0.1])[2]) >= 0.2:
                C.fall_item(w, item, C.room_of(w.s, sill), list(w.robot.get("pos") or [0, 0]), 0.9, "swept_off_sill_by_curtain")


# ----------------------------------------------------------------------------------
# blinds

def sill_obstruction_frac(w, eid):
    """Lowest lift fraction the bottom rail can reach given objects on the sill under it."""
    rec = w.ent(eid)
    wid = window_of(rec)
    if not wid:
        return 0.0, None
    height = C.num((w.s["entities"].get(wid, {}).get("size_m") or [1, 0.1, 1.2])[2], 1.2)
    best, item_id = 0.0, None
    for sill in sills_of_w(w, wid):
        for item in w.children(sill):
            irec = w.ent(item)
            if irec.get("relation", "on") != "on":
                continue
            h = C.num((irec.get("size_m") or [0, 0, 0.1])[2], 0.1)
            spos = irec.get("spos") or [0, 0]
            if C.num(spos[1]) > C.num(C.mget(rec, "rail_clear_m"), 0.06):
                continue  # sits in front of the rail's path
            frac = min(1.0, h / height)
            if frac > best:
                best, item_id = frac, item
    return best, item_id


def raise_blind(ctx, eid, to_frac, cord_angle_deg, lock, force):
    w = ctx.w
    rec = w.ent(eid)
    require(C.kind_of(rec) == "blind" or C.kind_of(rec) == "shutter", "That is not a blind or shade", "precondition")
    require(not C.mget(rec, "jammed"), "It is jammed crooked; lower it fully to level the rail first", "precondition")
    before_cov = _snapshot(w, eid)
    rm, mech = C.mut_mech(w, eid)
    lift = C.mget(rec, "lift", "cord_lock")
    current = C.num(C.mget(rec, "lift_frac", 1.0), 1.0)
    target = C.clamp(float(to_frac), 0.0, 1.0)
    if target < current:
        return lower_blind(ctx, eid, target, "controlled")
    if lift in ("cord_lock", "continuous_loop"):
        mech["cord_cleated"] = False
        mech["cord_low_m"] = 0.6 if lift == "cord_lock" else 0.9
    if lift == "cord_lock" and cord_angle_deg > 20.0:
        mech["skew_cm"] = round(C.num(mech.get("skew_cm")) + (cord_angle_deg - 20.0) / 4.0, 1)
    if C.num(mech.get("skew_cm")) > 3.0:
        mech["jammed"] = True
        mech["lift_frac"] = round(current + (target - current) * 0.5, 3)
        mech["cord_locked"] = True
        refresh(w, eid)
        _after_change(ctx, eid, before_cov)
        return "One side rose faster; the blind locked crooked and jammed"
    mech["lift_frac"] = round(target, 3)
    if lift == "cord_lock" and not lock:
        mech["cord_locked"] = False
        refresh(w, eid)
        return lower_blind(ctx, eid, 0.0, "release", note="The cord lock was not set: the blind dropped")
    mech["cord_locked"] = True
    refresh(w, eid)
    _after_change(ctx, eid, before_cov)
    return "Raised the blind to {:.0f}%".format(100 * target)


def lower_blind(ctx, eid, to_frac, speed, note=None):
    w = ctx.w
    rec = w.ent(eid)
    before_cov = _snapshot(w, eid)
    rm, mech = C.mut_mech(w, eid)
    lift = C.mget(rec, "lift", "cord_lock")
    target = C.clamp(float(to_frac), 0.0, 1.0)
    stop, item = sill_obstruction_frac(w, eid)
    if lift in ("cord_lock", "continuous_loop"):
        mech["cord_cleated"] = False
        mech["cord_low_m"] = 0.6 if lift == "cord_lock" else 0.9
    message = note or "Lowered the blind to {:.0f}%".format(100 * target)
    if item and target < stop:
        if speed == "release":
            irec = w.mut(item)
            if irec.get("fragile") or irec.get("material") in ("glass", "ceramic"):
                irec["broken"] = True
                w.stat("breakages")
            irec["crushed"] = True
            C.add_damage(irec, "broken" if irec.get("broken") else "crushed", 1.0 if irec.get("broken") else 0.5)
            w.stat("damage")
            mech["slats_bent"] = True
            mech["skew_cm"] = round(C.num(mech.get("skew_cm")) + 2.0, 1)
            target = stop
            message = (note or "The blind dropped") + " onto the {}".format(C.ref(ctx, item, w.ent(item).get("label", item)))
        else:
            target = stop
            message = "The bottom rail came to rest on the {} on the sill".format(C.ref(ctx, item, w.ent(item).get("label", item)))
    elif speed == "release":
        mech["slats_bent"] = C.num(C.mget(rec, "lift_frac", 1.0), 1.0) > 0.6 or bool(mech.get("slats_bent"))
        w.stat("noise", 0.5)
    mech["lift_frac"] = round(target, 3)
    if target <= 0.001:
        mech["skew_cm"] = 0.0
        mech["jammed"] = False
    refresh(w, eid)
    _after_change(ctx, eid, before_cov)
    return message


def tilt(ctx, eid, angle):
    w = ctx.w
    rec = w.ent(eid)
    require(C.mget(rec, "style", "venetian") in ("venetian", "vertical", "shutter"), "It has no slats to tilt", "precondition")
    require(not C.mget(rec, "jammed"), "It is jammed; level it first", "precondition")
    before_cov = _snapshot(w, eid)
    rm, mech = C.mut_mech(w, eid)
    mech["tilt_deg"] = round(C.clamp(float(angle), -80.0, 80.0), 1)
    refresh(w, eid)
    _after_change(ctx, eid, before_cov)
    return "Slats tilted to {:+.0f} degrees".format(mech["tilt_deg"])


# ----------------------------------------------------------------------------------
# verbs: raise, lower, tilt_slats (blinds, sashes, hinged lids, security bars, seats)

@action("raise", params={"target": "entity", "to_frac": "?number", "to_deg": "?number", "part": "?str",
                         "cord_angle_deg": {"type": "number", "default": 0.0, "min": 0.0, "max": 80.0},
                         "lock": {"type": "bool", "default": True}, "force": {"type": "str", "default": "normal", "choices": list(C.FORCES)},
                         "grip": {"type": "str", "default": "handle", "choices": ["handle", "surface", "edge", "rail"]}},
        duration=lambda ctx: 4.0 + ctx.data.get("extra_s", 0.0), energy=0.01, noise=0.02, category="physical",
        doc="Raise a blind/shade (to_frac lift 0..1; cord_angle_deg > 20 skews a cord-lock blind; lock=false lets it drop), a sash "
            "window (to_frac opening; part=upper_sash), a hinged lid (to_deg; below its stay-open detent it falls back), a security "
            "bar or a manually released garage door.")
def raise_(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    kind = C.kind_of(rec)
    C.note_force(w, eid, C.force_n(ctx.args["force"]))
    if kind in ("blind", "shutter"):
        return raise_blind(ctx, eid, 1.0 if ctx.args.get("to_frac") is None else ctx.args["to_frac"], ctx.args["cord_angle_deg"], ctx.args["lock"], ctx.args["force"])
    if kind in C.WINDOW_KINDS:
        from . import windows
        return windows.raise_sash(ctx, eid, ctx.args.get("to_frac"), ctx.args.get("part"), ctx.args["force"])
    if kind == "hinged_lid" or C.mget(rec, "angle_deg") is not None:
        from . import lids
        return lids.raise_lid(ctx, eid, ctx.args.get("to_deg"), ctx.args["grip"], ctx.args["force"])
    if ctx.args.get("part") == "security_bar":
        rm, mech = C.mut_mech(w, eid)
        mech["security_bar"] = "up"
        return "Lifted the security bar out of the track"
    if ctx.args.get("part") == "flag":
        require("flag" in rec or C.mget(rec, "flag") is not None, "It has no flag", "precondition")
        rm, mech = C.mut_mech(w, eid)
        mech["flag"] = "up"
        rm["flag"] = "up"
        return "Raised the flag"
    if kind == "overhead_door":
        from . import garage
        return garage.manual_move(ctx, eid, 1.0 if ctx.args.get("to_frac") is None else ctx.args["to_frac"])
    if kind == "sliding_lid":
        rm, mech = C.mut_mech(w, eid)
        mech["track"] = "derailed"
        C.mark_damage(w, eid, "sliding lid lifted out of its track", stat=None)
        return "Lifted the lid out of its track"
    raise ActionError("Nothing to raise there", "precondition")


@action("lower", params={"target": "entity", "to_frac": "?number", "part": "?str",
                         "speed": {"type": "str", "default": "controlled", "choices": ["controlled", "release", "drop"]},
                         "force": {"type": "str", "default": "normal", "choices": list(C.FORCES)}},
        duration=lambda ctx: 4.0 + ctx.data.get("extra_s", 0.0), energy=0.01, noise=0.02, category="physical",
        doc="Lower a blind/shade (to_frac lift; speed=release lets it fall), a sash (to_frac opening; part=upper_sash; lowering to "
            "0 closes it), a hinged lid (controlled close; speed=drop slams it), a slider's security bar (part=security_bar) "
            "or a manually released garage door.")
def lower(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    kind = C.kind_of(rec)
    C.note_force(w, eid, C.force_n(ctx.args["force"]))
    speed = ctx.args["speed"]
    if kind in ("blind", "shutter"):
        return lower_blind(ctx, eid, 0.0 if ctx.args.get("to_frac") is None else ctx.args["to_frac"], "release" if speed != "controlled" else "controlled")
    if ctx.args.get("part") == "flag":
        require("flag" in rec or C.mget(rec, "flag") is not None, "It has no flag", "precondition")
        rm, mech = C.mut_mech(w, eid)
        mech["flag"] = "down"
        rm["flag"] = "down"
        return "Lowered the flag"
    if ctx.args.get("part") == "security_bar":
        require(C.mget(rec, "security_bar") is not None, "It has no security bar", "precondition")
        require(not rec.get("open"), "Close the slider first", "precondition")
        rm, mech = C.mut_mech(w, eid)
        mech["security_bar"] = "down"
        return "Dropped the security bar into the track"
    if kind in C.WINDOW_KINDS:
        from . import windows
        return windows.lower_sash(ctx, eid, ctx.args.get("to_frac"), ctx.args.get("part"), ctx.args["force"], speed)
    if kind == "hinged_lid":
        from . import lids
        return lids.lower_lid(ctx, eid, speed, ctx.args["force"])
    if kind == "overhead_door":
        from . import garage
        return garage.manual_move(ctx, eid, 0.0 if ctx.args.get("to_frac") is None else ctx.args["to_frac"])
    raise ActionError("Nothing to lower there", "precondition")


@action("tilt_slats", params={"target": "entity", "angle_deg": "?number", "direction": "?str:open|closed_down|closed_up"},
        duration=2.5, energy=0.004, category="physical",
        inverse=lambda ctx, before: {"action": "tilt_slats", "target": ctx.args["target"],
                                     "angle_deg": ((before.get("entity") or {}).get("mech") or {}).get("tilt_deg", 0.0)},
        doc="Rotate a venetian/vertical blind's or shutter's slats with the wand: angle_deg -80..80 (0 open; +80 closed with the "
            "rounded side out, the privacy direction at night; -80 closed the other way) or direction=open|closed_down|closed_up.")
def tilt_slats(ctx):
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    require(C.kind_of(rec) in ("blind", "shutter"), "That has no slats", "precondition")
    angle = ctx.args.get("angle_deg")
    direction = ctx.args.get("direction")
    if angle is None:
        require(direction, "Give angle_deg or direction", "argument")
        angle = {"open": 0.0, "closed_down": 80.0, "closed_up": -80.0}[direction]
    return tilt(ctx, eid, angle)


# ----------------------------------------------------------------------------------
# core open/close on coverings

def _recleat(w, eid):
    """The full open/close procedure ends with the lift cord wrapped back on its cleat."""
    rec = w.ent(eid)
    if C.mget(rec, "lift") in ("cord_lock", "continuous_loop") and not C.mget(rec, "cord_cleated"):
        rm, mech = C.mut_mech(w, eid)
        mech["cord_cleated"] = True
        mech["cord_low_m"] = max(C.CORD_SAFE_HEIGHT_M, C.num(C.mget(rec, "cleat_height_m"), 1.6))


@before("open", order=55)
def _before_open_cover(ctx):
    rec = ctx.w.s["entities"].get(ctx.args.get("target"))
    if rec and C.kind_of(rec) == "curtain":
        require(C.mget(rec, "rod") != "down", "The rod is down", "precondition")
        require(not C.mget(rec, "jammed"), "The rings are jammed; free them first (push_handle part=rings)", "precondition")
    if rec and C.kind_of(rec) in ("blind", "shutter"):
        require(not C.mget(rec, "jammed"), "It is jammed crooked; lower it fully first", "precondition")


@after("open", order=55)
def _after_open_cover(ctx, result):
    eid = ctx.args.get("target")
    rec = ctx.w.s["entities"].get(eid)
    if not rec:
        return
    kind = C.kind_of(rec)
    if kind == "curtain":
        set_panels(ctx, eid, 0.0, 0.0)
    elif kind in ("blind", "shutter"):
        style = C.mget(rec, "style", "venetian")
        if style in ("venetian", "vertical", "shutter") and C.num(C.mget(rec, "lift_frac", 1.0), 1.0) < 1.0:
            tilt(ctx, eid, 0.0)
        else:
            raise_blind(ctx, eid, 1.0, 0.0, True, "normal")
        _recleat(ctx.w, eid)


@before("close", order=55)
def _before_close_cover(ctx):
    rec = ctx.w.s["entities"].get(ctx.args.get("target"))
    if rec and C.kind_of(rec) == "curtain":
        require(C.mget(rec, "rod") != "down", "The rod is down", "precondition")
        require(not C.mget(rec, "jammed"), "The rings are jammed; free them first (push_handle part=rings)", "precondition")


@after("close", order=55)
def _after_close_cover(ctx, result):
    eid = ctx.args.get("target")
    rec = ctx.w.s["entities"].get(eid)
    if not rec:
        return
    kind = C.kind_of(rec)
    if kind == "curtain":
        set_panels(ctx, eid, 1.0, 1.0)
        _knock_sill(ctx, eid)
    elif kind in ("blind", "shutter"):
        if C.num(C.mget(rec, "lift_frac", 1.0), 1.0) > 0.0:
            lower_blind(ctx, eid, 0.0, "controlled")
        if C.mget(w_ent(ctx, eid), "style", "venetian") in ("venetian", "vertical", "shutter"):
            tilt(ctx, eid, 80.0)
        else:
            refresh(ctx.w, eid)
        _recleat(ctx.w, eid)


def w_ent(ctx, eid):
    return ctx.w.ent(eid)


# ----------------------------------------------------------------------------------
# sun and glare (P-approx)

def sun_position(state):
    """(azimuth_deg, altitude_deg) for a mid-latitude (40 N) home from the simulated clock."""
    now = clock.now(state)
    day = now.timetuple().tm_yday
    decl = 23.44 * math.sin(math.radians(360.0 * (284 + day) / 365.0))
    if state.get("clock", {}).get("hemisphere") == "south":
        decl = -decl
    lat = math.radians(40.0)
    h = clock.hour(state)
    hour_angle = math.radians(15.0 * (h - 12.5))
    d = math.radians(decl)
    sin_alt = math.sin(lat) * math.sin(d) + math.cos(lat) * math.cos(d) * math.cos(hour_angle)
    alt = math.degrees(math.asin(max(-1.0, min(1.0, sin_alt))))
    cos_az = (math.sin(d) - math.sin(lat) * sin_alt) / max(1e-6, math.cos(lat) * math.cos(math.radians(alt)))
    az = math.degrees(math.acos(max(-1.0, min(1.0, cos_az))))
    if hour_angle > 0:
        az = 360.0 - az
    return round(az, 1), round(alt, 1)


def _set_direct_sun(w, wid, value):
    rec = w.s["entities"][wid]
    if abs(C.num(C.mget(rec, "direct_sun")) - value) > 0.02 or C.mget(rec, "direct_sun") is None:
        rm, mech = C.mut_mech(w, wid)
        mech["direct_sun"] = value


def refresh_sun(w, windows=None):
    """Recompute mech.direct_sun on sun-tracked windows (glare sources) and ``glare`` on the screens/surfaces that
    reference them (glare_from). Run by the periodic system mechanisms.slow (300 s), at episode start and right
    after a covering on one of those windows changes (windows=[wid])."""
    state = w.s
    ents = state["entities"]
    targets = [g for g in C.ids(w, "glare") if g in ents]
    sources = set(e for e in C.ids(w, "sun_windows") if e in ents)
    for g in targets:
        sources.update(s for s in ents[g].get("glare_from") or [] if s in ents)
    if windows is not None:
        sources &= set(windows)
        if not sources:
            return
    values = {}
    for wid in sorted(sources):
        rec = ents[wid]
        if rec.get("facing_deg", C.mget(rec, "facing_deg")) is None:
            continue
        values[wid] = direct_sun(state, wid, covers=coverings_of_w(w, wid))
        _set_direct_sun(w, wid, values[wid])
    for g in targets:
        rec = ents[g]
        srcs = [s for s in rec.get("glare_from") or [] if s in ents]
        if windows is not None and not set(srcs) & sources:
            continue
        value = max([values[s] if s in values else direct_sun(state, s, covers=coverings_of_w(w, s)) for s in srcs] or [0.0])
        if abs(C.num(rec.get("glare")) - value) > 0.02 or "glare" not in rec:
            w.mut(g)["glare"] = round(value, 3)


def direct_sun(state, wid, covers=None):
    """Fraction of full direct sun entering through a window after its coverings (0..1)."""
    win = state["entities"].get(wid) or {}
    facing = win.get("facing_deg", C.mget(win, "facing_deg"))
    if facing is None:
        return 0.0
    az, alt = sun_position(state)
    if alt <= 2.0:
        return 0.0
    delta = abs(((az - float(facing)) + 180.0) % 360.0 - 180.0)
    if delta >= 85.0:
        return 0.0
    cond = state.get("weather", {}).get("condition", "clear")
    sky = {"clear": 1.0, "cloudy": 0.25, "rain": 0.05, "storm": 0.0, "fog": 0.1, "snow": 0.2}.get(cond, 0.6)
    strength = math.cos(math.radians(delta)) * sky
    transmit = 1.0
    for cov in (coverings_of(state, wid) if covers is None else covers):
        rec = state["entities"][cov]
        if C.kind_of(rec) == "curtain":
            transmit *= 1.0 - curtain_numbers(rec, win)["coverage"]
        else:
            lowered = 1.0 - C.clamp(C.num(C.mget(rec, "lift_frac", 1.0), 1.0), 0.0, 1.0)
            tilt_deg = C.num(C.mget(rec, "tilt_deg"))
            if C.mget(rec, "style", "venetian") in ("venetian", "shutter"):
                slat_pass = max(0.0, 1.0 - (max(0.0, tilt_deg) + alt) / 80.0) if tilt_deg >= 0 else max(0.0, 1.0 - abs(tilt_deg) / 80.0) * 0.6
            else:
                slat_pass = 1.0 - C.num(C.mget(rec, "opacity"), 0.9)
            transmit *= (1.0 - lowered) + lowered * slat_pass
    return round(max(0.0, strength * transmit), 3)
