"""Window mechanics: sash travel (lower and upper sash), casement cranks (turn), sliders, child
window stops and guards, screens, stuck sashes (paint seal, swollen wood, debris in the track),
misaligned keepers that stop the cam lock, AC units in the opening, egress clear opening.

Derived ``mech`` fields refreshed whenever a window moves: clear_opening_m2 (lower sash raised
fully opens half the frame; a casement opens all of it; a guard blocks it), egress_ready (clear
opening >= 0.53 m2 = 5.7 sq ft, IRC R310, guard released, screen out of the way).
"""
from ...kernel.errors import ActionError, require
from ...kernel.plugins import hook
from . import closures as K
from . import common as C

EGRESS_MIN_M2 = 0.53


def egress(rec):
    kind = C.kind_of(rec)
    size = rec.get("size_m") or [1.0, 0.1, 1.2]
    width, height = C.num(size[0], 1.0), C.num(size[2], 1.2)
    frac = C.num(C.mget(rec, "open_frac", 1.0 if rec.get("open") else 0.0))
    portion = 0.5 if kind in ("sash_window", "slider_window") else (0.6 if kind == "hopper_window" else 1.0)
    clear = width * height * portion * frac
    guard = C.mget(rec, "guard") or {}
    if guard.get("engaged"):
        clear = 0.0
    screen = C.mget(rec, "screen", "in_place")
    ready = clear >= EGRESS_MIN_M2 and screen != "in_place"
    return round(clear, 3), bool(ready)


def refresh(w, eid):
    rec = w.ent(eid)
    if C.kind_of(rec) not in C.WINDOW_KINDS:
        return
    clear, ready = egress(rec)
    m = rec.get("mech") or {}
    if m.get("clear_opening_m2") != clear or m.get("egress_ready") != ready:
        rm, mech = C.mut_mech(w, eid)
        mech["clear_opening_m2"] = clear
        mech["egress_ready"] = ready


@hook("mechanisms.opened")
def _opened(ctx, eid, extent):
    refresh(ctx.w, eid)


@hook("mechanisms.closed")
def _closed(ctx, eid):
    refresh(ctx.w, eid)


def _work_swollen(ctx, eid, force):
    w = ctx.w
    h = C.hidden(w.ent(eid))
    if h.get("stuck") != "swollen" or w.ent(eid).get("open"):
        return None
    need = C.num(h.get("work_needed"), 3)
    hh = C.mut_hidden(w, eid)
    if force in ("gentle", "normal"):
        hh["worked"] = int(C.num(hh.get("worked"))) + 1
        if hh["worked"] < need:
            return "It moves a few millimetres and binds; keep working it gently"
        return None
    return None


def raise_sash(ctx, eid, to_frac, part, force):
    w = ctx.w
    rec = w.ent(eid)
    kind = C.kind_of(rec)
    if part == "upper_sash":
        require(kind == "sash_window", "Only a double-hung window has an upper sash", "precondition")
        rm, mech = C.mut_mech(w, eid)
        value = 0.0 if to_frac is None else C.clamp(float(to_frac), 0.0, 1.0)
        mech["upper_open_frac"] = round(value, 3)
        rm["open"] = value > 0.005 or C.num(mech.get("open_frac")) > 0.005
        refresh(w, eid)
        return "Pushed the upper sash up to {}".format("fully closed" if value <= 0.005 else "{:.0f}% open".format(100 * value))
    require(kind in ("sash_window", "hopper_window"), "This window does not open by lifting: {}".format(
        "crank it (turn)" if kind in ("casement_window", "skylight") else "slide it (pull_handle)"), "precondition")
    target = 1.0 if to_frac is None else C.clamp(float(to_frac), 0.0, 1.0)
    current = C.num(C.mget(rec, "open_frac", 1.0 if rec.get("open") else 0.0))
    if target < current:
        return lower_sash(ctx, eid, target, None, force, "controlled")
    require(not rec.get("locked"), "The sash lock is engaged; unlock it first", "precondition")
    note = _work_swollen(ctx, eid, force)
    if note:
        return note
    K.check_open(ctx, eid, force, "raise")
    K.apply_open(ctx, eid, target, force, "raise")
    after = C.num(C.mget(w.ent(eid), "open_frac", 1.0))
    if after + 1e-6 < target:
        return "The child window stop holds the sash at {:.0f}%".format(100 * after)
    return "Raised the sash to {:.0f}%".format(100 * after)


def lower_sash(ctx, eid, to_frac, part, force, speed):
    w = ctx.w
    rec = w.ent(eid)
    if part == "upper_sash":
        require(C.kind_of(rec) == "sash_window", "Only a double-hung window has an upper sash", "precondition")
        rm, mech = C.mut_mech(w, eid)
        value = 0.3 if to_frac is None else C.clamp(float(to_frac), 0.0, 1.0)
        mech["upper_open_frac"] = round(value, 3)
        rm["open"] = value > 0.005 or C.num(mech.get("open_frac")) > 0.005
        refresh(w, eid)
        return "Lowered the upper sash to {:.0f}% open".format(100 * value)
    target = 0.0 if to_frac is None else C.clamp(float(to_frac), 0.0, 1.0)
    if target > 0.0:
        require(rec.get("open"), "It is shut; raise it instead", "precondition")
        C.set_open(w, eid, target)
        refresh(w, eid)
        return "Lowered the sash to {:.0f}%".format(100 * target)
    if not rec.get("open") or C.num(C.mget(rec, "open_frac", 1.0)) <= 0.005:
        return "The lower sash is already down"
    newtons = C.force_n(force)
    if speed in ("release", "drop"):
        obstacles = K.close_obstructions(w, eid)
        K.apply_close(ctx, eid, "yank", "lower", obstacles=obstacles, guided=False)
        return "The sash dropped and banged shut"
    obstacles = K.check_close(ctx, eid, force, "lower")
    K.apply_close(ctx, eid, force, "lower", obstacles=obstacles)
    upper = C.num(C.mget(w.ent(eid), "upper_open_frac"))
    if upper > 0.005:
        w.mut(eid)["open"] = True
        return "Lowered the lower sash; the upper sash is still open {:.0f}%".format(100 * upper)
    return "Closed the sash"


def crank(ctx, eid, turns, direction, force):
    """Casement/skylight crank: ccw opens (outswing), cw closes; ~12 turns from shut to fully open."""
    w = ctx.w
    rec = w.ent(eid)
    full = C.num(C.mget(rec, "turns_full"), 12.0)
    current = C.num(C.mget(rec, "open_frac", 1.0 if rec.get("open") else 0.0))
    delta = float(turns) / full
    if direction == "ccw":
        require(not rec.get("locked"), "The locking handle is engaged; unlock it first", "precondition")
        if current <= 0.005:
            K.check_open(ctx, eid, force, "crank")
        target = min(1.0, current + delta)
        K.apply_open(ctx, eid, target, force, "crank")
        return "Cranked it open to {:.0f}%".format(100 * C.num(C.mget(w.ent(eid), "open_frac")))
    target = max(0.0, current - delta)
    if target <= 0.005:
        obstacles = K.check_close(ctx, eid, force, "crank")
        K.apply_close(ctx, eid, force, "crank", obstacles=obstacles)
        over = current - delta
        if over < -0.25 and C.force_n(force) >= C.FORCE_N["firm"]:
            C.mark_damage(w, eid, "crank operator stripped by over-cranking")
        return "Cranked it shut"
    C.set_open(w, eid, target)
    refresh(w, eid)
    return "Cranked it to {:.0f}% open".format(100 * target)


def push_out_screen(ctx, eid):
    w = ctx.w
    rec = w.ent(eid)
    require(C.kind_of(rec) in C.WINDOW_KINDS, "Only windows have screens", "precondition")
    require(C.mget(rec, "screen", "in_place") == "in_place", "The screen is already out", "precondition")
    require(C.num(C.mget(rec, "open_frac", 1.0 if rec.get("open") else 0.0)) >= 0.3, "Open the window wide first", "precondition")
    rm, mech = C.mut_mech(w, eid)
    mech["screen"] = "removed"
    refresh(w, eid)
    w.emit("screen_removed", window=eid)
    return "Pushed the screen out of the frame"


def cut_seal(ctx, eid, tool):
    w = ctx.w
    rec = w.ent(eid)
    blade = C.held_tool(ctx, tool, kinds=("putty_knife", "utility_knife", "paint_zipper", "box_cutter"), affordances=("cut_seal", "scrape"))
    pry = C.held_tool(ctx, tool, kinds=("screwdriver", "pry_bar", "chisel"), affordances=("pry",))
    require(blade or pry, "Hold a thin blade (putty knife / utility knife) to cut along the seam", "capability")
    h = C.hidden(rec)
    ctx.spend(40.0)
    if h.get("stuck") != "paint_seal":
        if pry and not blade:
            C.mark_damage(w, eid, "frame gouged by prying", stat="damage")
        return "No paint seal along this seam"
    hh = C.mut_hidden(w, eid)
    hh["stuck"] = None
    hh["fixed"] = "paint_seal_cut"
    if pry and not blade:
        C.mark_damage(w, eid, "frame gouged by prying the sash")
        return "Pried the sash free, gouging the frame"
    return "Ran the blade along the painted seam; the seal is cut"


def keeper_misaligned(rec):
    return C.num(C.hidden(rec).get("keeper_offset_mm")) > 2.5
