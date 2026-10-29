"""Joining and closing: connectors, threads and caps, clips, zippers, snaps, hook-and-loop,
buttons/buckles, knots and tangles, seals.

Verbs: attach, detach, fasten, unfasten, twist, untwist, screw_together, unscrew_apart, clip,
unclip, zip, unzip, snap, unsnap, velcro, unvelcro, tie, untie, seal, unseal.

Models:
* Connectors: compatibility by mate tags, keying, engagement force (partial engagement
  falls off under the next pull/use test), release actuation (yanking a latched joint
  snaps its tab).
* Threads: right/left hand, turns to seat, torque rising after seating (spec vs crack
  limit), cross-threading when started misaligned, breakaway torque vs the robot's wrist
  torque scaled by grip friction, child-resistant push-and-turn, carbonation venting.
* Knots: holding load by knot type (twist 0.3 kg, overhand 6, granny 6 slips, bow 10,
  double 20, square 25); bags need headroom to tie; pulling standing ends tightens knots.
* Tangles: crossings/knots counts; picking apart removes them, pulling tightens them.
* Zippers: closed fraction, separating pins, fabric snags when the fabric is not held taut,
  overfull bags need compression (and crush fragile contents if compressed hard).
"""
import math

from ...kernel import body as kbody
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import hook
from ..core import check_handling, release_from_hands
from . import common as C

KNOTS = {   # type -> (holds kg, releasable by tail, slips under cyclic load)
    "twist": (0.3, True, True), "overhand": (6.0, False, False), "granny": (6.0, False, True), "bow": (10.0, True, False),
    "double_bow": (15.0, True, False), "double": (20.0, False, False), "square": (25.0, False, False), "slip": (4.0, True, False),
    "figure_eight": (10.0, False, False), "surgeon": (25.0, False, False), "zip_tie": (30.0, False, False),
}


def _tags(rec):
    return {rec.get("type"), rec.get("category"), rec.get("part_type")} | set(rec.get("is_a", [])) | set(rec.get("tags", []))


# ----------------------------------------------------------------------------------
# connectors

@action("attach", params={"target": "entity", "destination": "entity", "force_n": {"type": "number", "default": 30.0, "min": 0.0, "max": 300.0},
                          "orientation": "?str", "where": "?str", "points": "?int", "layer": "?str:inner|outer"},
        duration=4.0, energy=0.01, category="physical",
        inverse=lambda ctx, before: {"action": "detach", "target": ctx.args["target"], "release": True},
        doc="Join a held part to its mate with the designed connector (bayonet, click, hook, magnet, key ring, hose coupling, "
            "felt pads or sliders under furniture legs). Needs the right mate and keying; too little force leaves it unlatched.")
def attach(ctx):
    w = ctx.w
    eid, dest = ctx.args["target"], ctx.args["destination"]
    C.held_or_fail(ctx, eid)
    drec = ctx.obj(dest, touch=True)
    rec = w.ent(eid)
    if rec.get("pad_kind") or C.is_kind(rec, "felt_pads", "furniture_slider_set", "furniture_slider", "slider_set"):
        return _attach_pads(ctx, eid, dest, rec, drec)
    if rec.get("grommets") and (drec.get("rings") or drec.get("hooks")):
        return _hang_on_rings(ctx, eid, dest, rec, drec)
    conn = rec.get("connector") or {}
    require(conn, "It has no connector to attach with", "precondition")
    mates = set(conn.get("mates", []))
    accepts = set((drec.get("mount") or {}).get("accepts", [])) | _tags(drec)
    require(mates & accepts, "Those parts are not compatible", "precondition")
    force = C.commanded_force(ctx, "force_n", 30.0)
    key = conn.get("key")
    if key and ctx.args.get("orientation") not in (None, key):
        if force > C.num(conn.get("engage_force_n"), 25.0) * 1.5:
            C.mark_damage(w, eid, "forced", "forced on misaligned", broken=False)
            C.mark_damage(w, dest, "forced", "a misaligned part was forced on", broken=False)
        raise ActionError("The keying does not line up in that orientation", "precondition")
    engaged = force >= C.num(conn.get("engage_force_n"), 25.0)
    if force > C.num(conn.get("max_force_n"), 200.0):
        C.mark_damage(w, eid, "cracked", "attached with excessive force", broken=True)
    release_from_hands(w, eid)
    m = w.mut(eid)
    for key_ in ("spos", "pos", "shelf", "held_hand", "xy_m"):
        m.pop(key_, None)
    m["location"], m["relation"] = dest, "attached"
    m["connector"] = dict(conn, engaged=engaged, attached_to=dest)
    w.log_move(eid, "robot", dest)
    dm = w.mut(dest)
    dm["attached_parts"] = sorted(set(drec.get("attached_parts") or []) | {eid})
    if conn.get("completes"):
        dm["assembled"] = engaged
    return "Attached {} to {}{}".format(eid, dest, "" if engaged else " but it did not click home")


def _hang_on_rings(ctx, eid, dest, rec, drec):
    """Hook a curtain/liner through its grommets onto a rod's rings (inner layer hangs inside the tub)."""
    w = ctx.w
    rings = int(C.num(drec.get("rings") or drec.get("hooks")))
    grommets = int(C.num(rec.get("grommets")))
    points = ctx.args.get("points")
    points = min(grommets, rings) if points is None else int(points)
    require(1 <= points <= min(grommets, rings), "It has {} grommets and the rod {} rings".format(grommets, rings), "argument")
    layer = ctx.args.get("layer") or ("inner" if C.is_kind(rec, "shower_liner", "liner") else "outer")
    layers = dict(drec.get("layers") or {})
    require(not layers.get(layer) or layers.get(layer) == eid, "{} already hangs on the {} side of the rings".format(layers.get(layer), layer), "precondition")
    if rec.get("folded"):
        raise ActionError("Unfold it first; folded it cannot hang straight", "precondition")
    release_from_hands(w, eid)
    m = w.mut(eid)
    for key in ("spos", "pos", "shelf", "held_hand", "xy_m"):
        m.pop(key, None)
    m["location"], m["relation"] = dest, "attached"
    m["hung_points"] = points
    m["layer"] = layer
    m["sagging"] = points < grommets
    m["hem_inside_tub"] = layer == "inner"
    layers[layer] = eid
    dm = w.mut(dest)
    dm["layers"] = layers
    dm["attached_parts"] = sorted(set(drec.get("attached_parts") or []) | {eid})
    w.log_move(eid, "robot", dest)
    ctx.spend(4.0 * points)
    return "Hung {} on {} of {} grommets ({} layer){}".format(eid, points, grommets, layer, "; it sags where grommets were skipped" if points < grommets else "")


def _attach_pads(ctx, eid, dest, rec, drec):
    w = ctx.w
    require(C.is_kind(drec, "furniture", "seat", "table") or drec.get("legs"), "Pads go under furniture legs", "precondition")
    legs = int(C.num(drec.get("legs"), 4))
    accessible = drec.get("legs_accessible") or drec.get("location") == "robot" or drec.get("tilt_deg", 0) > 0
    require(accessible, "Lift or tilt it first so the feet are reachable", "precondition")
    count = int(C.num(rec.get("count"), 4))
    kind = rec.get("pad_kind") or ("slider" if C.is_kind(rec, "furniture_slider_set", "furniture_slider", "slider_set") else "felt")
    leg_soil = (drec.get("soil") or {}) if isinstance(drec.get("soil"), dict) else {}
    clean = sum(C.num(v) for v in leg_soil.values()) < 0.2 and C.num(drec.get("wetness")) < 0.1
    use = min(count, legs)
    release_from_hands(w, eid)
    m = w.mut(eid)
    m["location"], m["relation"] = dest, "attached"
    m.pop("held_hand", None)
    dm = w.mut(dest)
    if kind == "slider":
        dm["on_sliders"] = use >= legs
        dm["sliders"] = {"count": use, "legs": legs}
    else:
        dm["pads"] = {"kind": kind, "count": use, "legs": legs, "adhered": clean, "centered": True}
        if not clean:
            dm["pads_fall_off_at_s"] = w.s["time_s"] + 3600
            C.watch(w, dest)
    w.log_move(eid, "robot", dest)
    return "Fitted {} {} under {} of {} legs{}".format(use, kind, dest, legs, "" if clean else " (dirty feet: poor adhesion)")


@action("detach", params={"target": "entity", "release": {"type": "bool", "default": False}, "force_n": {"type": "number", "default": 30.0, "min": 0.0, "max": 300.0},
                          "hand": "?str:left|right"},
        duration=3.0, energy=0.01, category="physical",
        doc="Separate an attached part using its release (release=true presses the button / twists the bayonet / squeezes the clip). "
            "Yanking a latched joint snaps its tab; a pressurised hose sprays.")
def detach(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    if rec.get("over_rim"):
        w.mut(eid)["over_rim"] = False
        return "Unhooked {} from the rim of {}".format(eid, rec.get("location"))
    require(rec.get("relation") == "attached", "It is not attached to anything")
    base = rec.get("location")
    if rec.get("hung_points"):
        brec = w.ent(base)
        require(len(C.free_hands(w)) >= 1, "Free a hand first", "capability")
        layers = {k: v for k, v in sorted((brec.get("layers") or {}).items()) if v != eid}
        bm = w.mut(base)
        bm["layers"] = layers
        bm["attached_parts"] = [p for p in (brec.get("attached_parts") or []) if p != eid]
        ctx.spend(3.0 * int(C.num(rec.get("hung_points"))))
        m = w.mut(eid)
        for key in ("hung_points", "layer", "sagging", "hem_inside_tub"):
            m.pop(key, None)
        C.take_into_hand(ctx, eid, ctx.args.get("hand"))
        return "Unhooked {} from every ring of {}".format(eid, base)
    conn = rec.get("connector") or {}
    release = ctx.args.get("release")
    latched = conn.get("engaged") and conn.get("release") in ("button", "twist", "squeeze", "lever", "latch")
    if latched and not release:
        if C.commanded_force(ctx, "force_n", 30.0, "pull_n") >= C.num(conn.get("break_force_n"), 60.0):
            C.mark_damage(w, eid, "tab_snapped", "yanked off without pressing the release")
        else:
            raise ActionError("It is latched; press its release (release=true)", "precondition")
    if base in w.s["entities"] and (w.ent(base).get("pressurized") or rec.get("pressurized")) and w.ent(base).get("water_running", True) and w.ent(base).get("pressurized"):
        C.make_spill(w, C.room_of(w, base), w.ent(base).get("pos"), "water", 0.5, source=base)
        C.note_event(w, "sprayed", eid)
    hands = C.free_hands(w)
    require(hands, "No free hand", "capability")
    m = w.mut(eid)
    m["connector"] = dict(conn, engaged=False, attached_to=None)
    C.take_into_hand(ctx, eid, ctx.args.get("hand"))
    if base in w.s["entities"]:
        bm = w.mut(base)
        bm["attached_parts"] = [p for p in (bm.get("attached_parts") or []) if p != eid]
        if conn.get("completes"):
            bm["assembled"] = False
        if C.is_kind(rec, "felt_pads", "furniture_slider_set", "slider_set") or rec.get("pad_kind"):
            bm["on_sliders"] = False
            bm.pop("sliders", None)
    return "Detached {}".format(eid)


@hook("event:manipulation.pull_test")
def _pull_test(w, args, env):
    """A normal-use tug (~20 N): unlatched attachments and weak fastenings come apart."""
    target = args.get("target")
    if target not in w.s["entities"]:
        return True
    for eid in [target] + w.children(target):
        rec = w.ent(eid)
        conn = rec.get("connector") or {}
        if rec.get("relation") == "attached" and conn and not conn.get("engaged"):
            C.move_to_floor(w, eid, C.room_of(w, eid))
            w.mut(eid)["connector"] = dict(conn, attached_to=None)
            C.note_event(w, "came_apart", eid)
        velcro = rec.get("velcro") or {}
        if velcro.get("engaged") and C.num(velcro.get("overlap_frac")) < 0.6:
            w.mut(eid)["velcro"] = dict(velcro, engaged=False, popped=True)
            C.note_event(w, "velcro_popped", eid)
        snaps = rec.get("snaps") or {}
        if snaps and any(p.get("weak") for p in (snaps.get("pairs") or {}).values()):
            pairs = {k: v for k, v in snaps["pairs"].items() if not v.get("weak")}
            w.mut(eid)["snaps"] = dict(snaps, pairs=pairs)
    return True


# ----------------------------------------------------------------------------------
# buttons, buckles, clasps, straps

def _fastener(rec, which):
    fasteners = rec.get("fasteners") or {}
    require(fasteners, "It has no buttons, buckles or straps", "precondition")
    if which:
        require(which in fasteners, "It has no " + which, "argument")
        return which, fasteners[which]
    name = sorted(fasteners)[0]
    return name, fasteners[name]


@action("fasten", params={"target": "entity", "which": "?str", "start": {"type": "int", "default": 0, "min": 0, "max": 20},
                          "destination": "?entity", "where": "?str:handle|zipper_pull|strap|ring|loop", "force_n": {"type": "number", "default": 25.0, "min": 0, "max": 200}},
        duration=lambda ctx: ctx.data.get("manip_s", 4.0), energy=0.005, category="physical",
        inverse=lambda ctx, before: {"action": "unfasten", "target": ctx.args["target"], "which": ctx.args.get("which")},
        doc="Close a garment's or item's fasteners (buttons in order starting from hole 'start', buckle until it clicks, clasp, strap), "
            "or strap a held tag/strap to destination (where=handle is secure; a zipper pull is not).")
def fasten(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    dest = ctx.args.get("destination")
    if dest:
        C.held_or_fail(ctx, eid)
        ctx.obj(dest, touch=True)
        release_from_hands(w, eid)
        m = w.mut(eid)
        m["location"], m["relation"] = dest, "attached"
        m.pop("held_hand", None)
        where = ctx.args.get("where") or "handle"
        m["fastened_at"] = where
        m["secure"] = where in ("handle", "strap", "ring", "loop")
        w.log_move(eid, "robot", dest)
        return "Strapped {} to the {} of {}".format(eid, where.replace("_", " "), dest)
    name, info = _fastener(rec, ctx.args.get("which"))
    m = w.mut(eid)
    fasteners = m["fasteners"]
    count = int(C.num(info.get("count"), 1))
    if count > 1:
        start = int(ctx.args.get("start") or 0)
        fasteners[name] = dict(info, engaged=True, offset=start, closed_count=count - start)
        ctx.data["manip_s"] = 3.0 + 3.0 * count
        if start:
            m["misfastened"] = True
            return "Fastened {} {} starting {} off: they are misaligned".format(count - start, name, start)
        m["misfastened"] = False
        return "Fastened all {} {}".format(count, name)
    clicked = C.commanded_force(ctx, "force_n", 25.0) >= C.num(info.get("click_force_n"), 15.0)
    fasteners[name] = dict(info, engaged=clicked)
    ctx.data["manip_s"] = 3.0
    return "{} the {}".format("Fastened" if clicked else "Pressed but did not latch", name)


@action("unfasten", params={"target": "entity", "which": "?str", "method": {"type": "str", "default": "gentle", "choices": ["gentle", "yank"]}},
        duration=4.0, energy=0.005, category="physical",
        doc="Open buttons/buckles/clasps/straps fully and gently before the next step; yanking tears buttons off or rips fabric.")
def unfasten(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    if rec.get("relation") == "attached" and rec.get("fastened_at"):
        C.take_into_hand(ctx, eid)
        w.mut(eid).pop("fastened_at", None)
        return "Unstrapped " + eid
    name, info = _fastener(rec, ctx.args.get("which"))
    m = w.mut(eid)
    m["fasteners"][name] = dict(info, engaged=False, closed_count=0, offset=0)
    if ctx.args.get("method") == "yank":
        C.mark_damage(w, eid, "torn", "{} yanked open".format(name), broken=False)
    m["misfastened"] = False
    return "Undid the {}".format(name)


# ----------------------------------------------------------------------------------
# threads: caps, lids, threaded parts

def _thread(rec):
    thread = rec.get("thread")
    require(isinstance(thread, dict), "It has no thread", "precondition")
    return thread


def _cap_and_container(w, eid):
    rec = w.ent(eid)
    if isinstance(rec.get("thread"), dict) and rec.get("relation") == "attached" and rec.get("location") in w.s["entities"]:
        return eid, rec["location"]
    cap = rec.get("cap") or {}
    if isinstance(cap, dict) and cap.get("id") in w.s["entities"]:
        return cap["id"], eid
    for child in w.children(eid):
        crec = w.ent(child)
        if crec.get("relation") == "attached" and isinstance(crec.get("thread"), dict):
            return child, eid
    raise ActionError("Nothing threaded is fitted on it", "precondition")


def update_seal(w, container):
    if container not in w.s["entities"]:
        return
    crec = w.ent(container)
    cap_id = None
    for child in w.children(container):
        rec = w.ent(child)
        if rec.get("relation") == "attached" and (isinstance(rec.get("thread"), dict) or rec.get("snap_cap") or rec.get("lid_fit")):
            cap_id = child
            break
    m = w.mut(container)
    if cap_id is None:
        m["cap"] = {"id": None, "on": False, "loose": False}
        m["sealed"] = False
        return
    cap = w.ent(cap_id)
    thread = cap.get("thread") or {}
    if thread:
        seated = C.num(thread.get("engaged_turns")) >= C.num(thread.get("turns_to_seat"), 1.0) - 1e-6
        snug = C.num(thread.get("torque_nm")) >= 0.3 * C.num(thread.get("spec_nm"), 1.0)
        matches = thread.get("mate") in (None, container) and not cap.get("broken") and not crec.get("broken")
        sealed = seated and snug and matches and not thread.get("cross_threaded")
        loose = not (seated and snug) or bool(thread.get("cross_threaded"))
    else:
        matches = cap.get("fits") in (None, container) or cap.get("lid_fit") == crec.get("lid_fit")
        sealed = bool(cap.get("seated")) and matches and not cap.get("broken")
        loose = not sealed
    m["cap"] = {"id": cap_id, "on": True, "loose": loose, "matched": bool(matches)}
    m["sealed"] = bool(sealed)


def _twist_capacity(w, ctx):
    """Torque the robot can put on a cap or screw part: the body's wrist torque (body.limit wrist_nm), 2.5x with a
    jar opener or rubber grip in hand."""
    capacity = C.wrist_limit_nm(w)
    for held in w.held_items():
        rec = w.ent(held)
        if "grip_twist" in (rec.get("affordances") or []) or C.is_kind(rec, "jar_opener", "rubber_grip"):
            capacity *= 2.5
    return capacity


def _vent_or_spray(w, container, vent):
    crec = w.ent(container)
    agitation = C.num(crec.get("agitation"))
    if not crec.get("carbonated") or agitation <= 0.15:
        return None
    m = w.mut(container)
    if vent:
        m["agitation"] = 0.0
        return "vented slowly"
    spray = min(C.num(crec.get("liquid_l")), agitation * 0.25)
    m["agitation"] = 0.0
    if spray > 0:
        C.spill_liquid(w, container, spray, C.room_of(w, container), crec.get("pos"))
    return "it sprayed foam"


def _engage(ctx, part, base, direction, turns, torque_limit, aligned):
    w = ctx.w
    prec = w.ent(part)
    thread = dict(_thread(prec))
    tighten = "cw" if thread.get("hand", "right") == "right" else "ccw"
    require(direction == tighten, "Turning that way only spins it; threads start {}".format("clockwise" if tighten == "cw" else "counter-clockwise"))
    if thread.get("mate") not in (None, base) and thread.get("mate") in w.s["entities"]:
        mate_rec = w.ent(thread["mate"])
        if C.dims(mate_rec)[0] != C.dims(w.ent(base))[0] and thread.get("strict_mate", True):
            raise ActionError("It does not thread onto that; wrong size", "precondition")
    release_from_hands(w, part)
    m = w.mut(part)
    for key in ("spos", "pos", "shelf", "held_hand", "xy_m"):
        m.pop(key, None)
    m["location"], m["relation"] = base, "attached"
    thread["cross_threaded"] = not aligned
    seat = C.num(thread.get("turns_to_seat"), 1.5)
    thread["engaged_turns"] = round(min(turns, 0.5 if not aligned else seat), 3)
    thread["torque_nm"] = 0.0
    m["thread"] = thread
    w.log_move(part, "robot", base)
    if turns > seat and aligned:
        _tighten(ctx, part, turns - seat, torque_limit)
    elif not aligned and turns > 0.5:
        th = dict(w.ent(part)["thread"])
        th["torque_nm"] = round(C.num(th.get("spec_nm"), 1.0) * 1.5, 3)
        w.mut(part)["thread"] = th


def _tighten(ctx, part, turns, torque_limit):
    w = ctx.w
    prec = w.ent(part)
    thread = dict(prec["thread"])
    seat = C.num(thread.get("turns_to_seat"), 1.5)
    engaged = C.num(thread.get("engaged_turns")) + turns
    spec = C.num(thread.get("spec_nm"), 1.0)
    if engaged <= seat or thread.get("cross_threaded"):
        thread["engaged_turns"] = round(min(engaged, seat if not thread.get("cross_threaded") else 0.75), 3)
        if thread.get("cross_threaded"):
            thread["torque_nm"] = round(C.num(thread.get("torque_nm")) + spec * turns * 2.0, 3)
    else:
        extra = engaged - seat
        torque = spec * (0.5 + extra / 0.1)
        if torque_limit is not None:
            torque = min(torque, C.num(torque_limit))
        torque = min(torque, _twist_capacity(w, ctx))
        thread["engaged_turns"] = round(seat, 3)
        thread["torque_nm"] = round(max(C.num(thread.get("torque_nm")), torque), 3)
    w.mut(part)["thread"] = thread
    if C.num(thread["torque_nm"]) > C.num(thread.get("max_nm"), spec * 3.0) + 1e-9:
        C.mark_damage(w, part, "cracked", "overtightened to {:.1f} N m".format(C.num(thread["torque_nm"])), broken=True)
        base = prec.get("location")
        if base in w.s["entities"] and w.ent(base).get("thread_on_body"):
            C.mark_damage(w, base, "cracked", "overtightened threads", broken=True)
    update_seal(w, prec.get("location"))


def _loosen(ctx, part, turns, press, vent):
    w = ctx.w
    prec = w.ent(part)
    thread = dict(prec["thread"])
    base = prec.get("location")
    if thread.get("child_resistant") and not press:
        return "The cap ratchets without turning; it is child-resistant (press while turning)"
    breakaway = max(C.num(thread.get("torque_nm")), C.num(thread.get("stuck_nm")))
    grip_mu = C.grasp_mu(w, part)          # the item's surface and the hand's grasp friction (body.grip_mu)
    capacity = _twist_capacity(w, ctx) * min(1.0, grip_mu / 0.3)
    if breakaway > capacity + 1e-9:
        if C.realistic(ctx):
            return "It will not budge; improve the grip or use an opener"   # the robot feels it slip, not the torque it needs
        return "It will not budge ({:.1f} N m needed); improve the grip or use an opener".format(breakaway)
    if base in w.s["entities"]:
        brec = w.ent(base)
        if brec.get("squeezable") and C.num(brec.get("liquid_l")) > 0 and C.num(brec.get("liquid_l")) >= 0.9 * C.num(brec.get("capacity_l"), 1.0) and breakaway > 1.5:
            C.spill_liquid(w, base, 0.02, C.room_of(w, base), brec.get("pos"))
    thread["torque_nm"] = 0.0
    thread["stuck_nm"] = 0.0
    thread["engaged_turns"] = round(max(0.0, C.num(thread.get("engaged_turns")) - turns), 3)
    w.mut(part)["thread"] = thread
    note = _vent_or_spray(w, base, vent) if base in w.s["entities"] else None
    update_seal(w, base)
    return "Loosened{}".format(" ({})".format(note) if note else "")


@action("twist", params={"target": "entity", "destination": "?entity", "direction": {"type": "str", "default": "cw", "choices": ["cw", "ccw"]},
                         "turns": {"type": "number", "default": 1.0, "min": 0.05, "max": 30.0}, "torque_nm": "?number",
                         "press": {"type": "bool", "default": False}, "aligned": {"type": "bool", "default": True}, "vent": {"type": "bool", "default": False}},
        duration=lambda ctx: 2.0 + 1.5 * C.num(ctx.args.get("turns"), 1.0), energy=0.005, category="physical",
        doc="Turn a threaded cap, lid or part: cw tightens right-hand threads, ccw loosens. With destination, start a held cap/part on "
            "its mate (aligned=false cross-threads). torque_nm caps the tightening torque (overtightening cracks plastic). "
            "Child-resistant caps need press=true; vent=true opens carbonated drinks slowly.")
def twist(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    C.safe_handling(ctx, eid)
    direction, turns = ctx.args["direction"], C.num(ctx.args["turns"], 1.0)
    dest = ctx.args.get("destination")
    if dest:
        C.held_or_fail(ctx, eid)
        ctx.obj(dest, touch=True)
        _engage(ctx, eid, dest, direction, turns, ctx.args.get("torque_nm"), ctx.args.get("aligned", True))
        update_seal(w, dest)
        return "Threaded {} onto {}{}".format(eid, dest, "" if ctx.args.get("aligned", True) else " (cross-threaded)")
    part, base = _cap_and_container(w, eid)
    if _is_live_bulb(w, part, base):
        raise ActionError("Switch the light off (and let the bulb cool) before turning it", "safety")
    thread = _thread(w.ent(part))
    tighten = "cw" if thread.get("hand", "right") == "right" else "ccw"
    if direction == tighten:
        _tighten(ctx, part, turns, ctx.args.get("torque_nm"))
        th = w.ent(part).get("thread") or {}
        return "Tightened {} to {:.1f} N m".format(part, C.num(th.get("torque_nm")))
    return _loosen(ctx, part, turns, ctx.args.get("press"), ctx.args.get("vent"))


def _is_live_bulb(w, part, base):
    prec = w.ent(part)
    if not C.is_kind(prec, "bulb", "light_bulb"):
        return False
    brec = w.ent(base) if base in w.s["entities"] else {}
    return bool(brec.get("on") or prec.get("on"))


@action("untwist", params={"target": "entity", "hand": "?str:left|right", "press": {"type": "bool", "default": False},
                           "vent": {"type": "bool", "default": False}},
        duration=5.0, energy=0.005, category="physical",
        doc="Unscrew a cap/lid/part completely and lift it off into a free hand (target = the cap or its container). The container must "
            "stand on a surface or be held. Child-resistant caps need press=true; vent=true releases carbonation slowly.")
def untwist(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    ctx.obj(eid, touch=True)
    part, base = _cap_and_container(w, eid)
    if _is_live_bulb(w, part, base):
        raise ActionError("Switch the light off (and let the bulb cool) before unscrewing it", "safety")
    C.safe_handling(ctx, part)
    require(C.free_hands(w), "No free hand for the cap", "capability")
    thread = _thread(w.ent(part))
    result = _loosen(ctx, part, C.num(thread.get("engaged_turns"), 1.0) + 0.5, ctx.args.get("press"), ctx.args.get("vent"))
    if "budge" in result or "ratchets" in result:
        raise ActionError(result, "precondition")
    C.take_into_hand(ctx, part, ctx.args.get("hand"))
    update_seal(w, base)
    return "Removed {} from {}{}".format(part, base, "; " + result[len("Loosened"):].strip(" ()") if result != "Loosened" else "")


@action("screw_together", params={"target": "entity", "destination": "entity", "turns": {"type": "number", "default": 4.0, "min": 0.1, "max": 40.0},
                                  "torque_nm": "?number", "aligned": {"type": "bool", "default": True}},
        duration=lambda ctx: 4.0 + 1.5 * C.num(ctx.args.get("turns"), 4.0), energy=0.01, category="physical",
        inverse=lambda ctx, before: {"action": "unscrew_apart", "target": ctx.args["target"]},
        doc="Join two threaded parts (blender blade base onto a jar, filter-housing halves, a furniture leg into its insert, a hose onto a spigot). "
            "Start aligned and turn until seated, to spec torque; cross-threaded joints wobble and leak, overtightening cracks plastic.")
def screw_together(ctx):
    w = ctx.w
    eid, dest = ctx.args["target"], ctx.args["destination"]
    C.held_or_fail(ctx, eid)
    ctx.obj(dest, touch=True)
    turns = C.num(ctx.args["turns"], 4.0)
    thread = _thread(w.ent(eid))
    direction = "cw" if thread.get("hand", "right") == "right" else "ccw"
    _engage(ctx, eid, dest, direction, turns, ctx.args.get("torque_nm"), ctx.args.get("aligned", True))
    prec = w.ent(eid)
    th = prec.get("thread") or {}
    seated = C.num(th.get("engaged_turns")) >= C.num(th.get("turns_to_seat"), 1.5) - 1e-6 and not th.get("cross_threaded")
    m = w.mut(eid)
    m["joined"] = {"to": dest, "seated": seated, "wobble": not seated, "leaks": bool(th.get("cross_threaded")) and bool(thread.get("sealing"))}
    dm = w.mut(dest)
    dm["assembled"] = seated and not prec.get("broken")
    update_seal(w, dest)
    return "Screwed {} into {}{}".format(eid, dest, "" if seated else " (not seated: it wobbles)")


@action("unscrew_apart", params={"target": "entity", "support": {"type": "bool", "default": True}, "hand": "?str:left|right"},
        duration=6.0, energy=0.01, category="physical",
        doc="Separate threaded parts: hold/brace the other part (support=true) and turn the right way; the freed part goes into a free hand "
            "(with no free hand it drops). Live or hot bulbs are refused.")
def unscrew_apart(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    require(rec.get("relation") == "attached", "It is not screwed onto anything")
    base = rec.get("location")
    if _is_live_bulb(w, eid, base):
        raise ActionError("Switch the light off (and let the bulb cool) before unscrewing it", "safety")
    C.safe_handling(ctx, eid)
    if not ctx.args.get("support"):
        brec = w.ent(base)
        if not brec.get("fixed_in_place") and C.subtree_mass(w, base) < 5.0 and not ctx.holding(base):
            return "The other part turned with it; brace it (support=true)"
    thread = _thread(rec)
    result = _loosen(ctx, eid, C.num(thread.get("engaged_turns"), 1.0) + 0.5, True, False)
    if "budge" in result:
        raise ActionError(result, "precondition")
    m = w.mut(eid)
    m.pop("joined", None)
    if base in w.s["entities"]:
        w.mut(base)["assembled"] = False
    if not C.free_hands(w):
        C.drop_item(w, eid, max(0.3, kbody.hand_drop_m(w.s)), C.room_of(w, eid), cause="loosened_part_fell")
        return "It came free and fell: no hand was free to catch it"
    C.take_into_hand(ctx, eid, ctx.args.get("hand"))
    update_seal(w, base)
    return "Unscrewed {} from {}".format(eid, base)


# ----------------------------------------------------------------------------------
# clips

def _clip_spec(rec):
    spec = rec.get("clip") or {}
    require(spec, "That is not a clip", "precondition")
    return spec


@action("clip", params={"target": "entity", "destination": "entity"}, duration=3.0, energy=0.005, category="physical",
        inverse=lambda ctx, before: {"action": "unclip", "target": ctx.args["target"]},
        doc="Close a held spring clip across something: a rolled bag opening (coverage = jaw width / opening width; roll the top first), "
            "a stack of papers (within the jaw capacity), a garment on a line or a ring.")
def clip(ctx):
    w = ctx.w
    eid, dest = ctx.args["target"], ctx.args["destination"]
    C.held_or_fail(ctx, eid)
    spec = _clip_spec(w.ent(eid))
    drec = ctx.obj(dest, touch=True)
    jaw = C.num(spec.get("jaw_width_m"), 0.08)
    capacity_mm = C.num(spec.get("max_thickness_mm"), 12.0)
    result = {}
    if drec.get("opening_width_m"):
        opening = C.num(drec["opening_width_m"])
        coverage = min(1.0, jaw / max(0.01, opening))
        rolled = int(C.num(drec.get("rolled_turns")))
        holds = coverage >= 0.98 and rolled >= 1
        result = {"kind": "clip", "by": eid, "coverage": round(coverage, 2), "holds": holds, "hold_kg": 2.0 if holds else 0.2}
        dm = w.mut(dest)
        dm["closure"] = result
        dm["sealed"] = holds
        dm["open"] = False if drec.get("openable") else dm.get("open")
        dm["exposed_opening_frac"] = round(0.0 if holds else max(0.1, 1.0 - coverage), 2)
    elif C.num(drec.get("sheets")) > 0 or C.is_kind(drec, "papers", "document", "paper_stack"):
        thickness = C.num(drec.get("thickness_mm"), C.num(drec.get("sheets"), 10) * 0.1)
        require(thickness <= capacity_mm + 1e-9, "Too thick for that clip" if C.realistic(ctx) else
                "Too thick for that clip ({:.0f} mm > {:.0f} mm)".format(thickness, capacity_mm), "precondition")
        dm = w.mut(dest)
        dm["clipped_by"] = eid
        dm["bound"] = True
    else:
        dm = w.mut(dest)
        dm["clipped_by"] = eid
    release_from_hands(w, eid)
    m = w.mut(eid)
    m["location"], m["relation"] = dest, "attached"
    m.pop("held_hand", None)
    m["clipped_to"] = dest
    w.log_move(eid, "robot", dest)
    if result and not result["holds"]:
        return "Clipped, but it only covers {:.0%} of the opening{}".format(result["coverage"], "" if int(C.num(drec.get("rolled_turns"))) else " and the top was not rolled")
    return "Clipped {} on {}".format(eid, dest)


@action("unclip", params={"target": "entity", "hand": "?str:left|right"}, duration=2.5, energy=0.005, category="physical",
        doc="Release a clip (target = the clip or the clipped item) into a free hand. Unclipping papers you are holding in the air scatters them.")
def unclip(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    clip_id = eid if rec.get("clipped_to") else rec.get("clipped_by") or (rec.get("closure") or {}).get("by")
    require(clip_id in w.s["entities"] and w.ent(clip_id).get("clipped_to"), "Nothing is clipped there")
    target = w.ent(clip_id)["clipped_to"]
    trec = w.ent(target)
    tm = w.mut(target)
    tm.pop("clipped_by", None)
    if (trec.get("closure") or {}).get("by") == clip_id:
        tm["closure"] = None
        tm["sealed"] = False
    if trec.get("bound"):
        tm["bound"] = False
        if ctx.holding(target):
            tm["order_lost"] = True
            tm["shuffled"] = True
            C.drop_item(w, target, kbody.hand_drop_m(w.s), w.robot["room"], w.robot.get("pos"), cause="papers_scattered")
            C.note_event(w, "papers_scattered", target)
    w.mut(clip_id).pop("clipped_to", None)
    C.take_into_hand(ctx, clip_id, ctx.args.get("hand"))
    return "Unclipped {}".format(clip_id)


# ----------------------------------------------------------------------------------
# zippers

def _zipper(rec):
    zipper = rec.get("zipper")
    require(isinstance(zipper, dict), "It has no zipper", "precondition")
    return dict(zipper)


def _fill_frac(w, eid):
    rec = w.ent(eid)
    cap = C.num(rec.get("capacity_l"))
    if cap <= 0:
        return 0.0
    from .stacking import used_volume
    return used_volume(w, eid) / cap


@action("zip", params={"target": "entity", "to_frac": {"type": "number", "default": 1.0, "min": 0.0, "max": 1.0},
                       "taut": {"type": "bool", "default": True}, "insert_pin": {"type": "bool", "default": True},
                       "compress": {"type": "bool", "default": False}, "force": {"type": "bool", "default": False}},
        duration=lambda ctx: 3.0 + 6.0 * C.num(ctx.args.get("to_frac"), 1.0), energy=0.005, category="physical",
        inverse=lambda ctx, before: {"action": "unzip", "target": ctx.args["target"]},
        doc="Close a zipper to to_frac, holding the fabric taut so nothing snags (separating zippers need the pin in the box first). "
            "An overfull bag needs compress=true (presses the contents; fragile items inside may crush); forcing an overfull or snagged "
            "zipper splits the teeth or tears the fabric.")
def zip_(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    zipper = _zipper(rec)
    to = C.num(ctx.args.get("to_frac"), 1.0)
    if zipper.get("separating") and not zipper.get("pin_in"):
        if not ctx.args.get("insert_pin"):
            zipper.update(closed_frac=to, meshed=False, skewed=True)
            w.mut(eid)["zipper"] = zipper
            return "Zipped, but skewed: the pin was not seated in the box"
        zipper["pin_in"] = True
    fill = _fill_frac(w, eid)
    if fill > 1.15 + 1e-9:
        if ctx.args.get("force"):
            zipper.update(closed_frac=to, teeth_split=True, meshed=False)
            w.mut(eid)["zipper"] = zipper
            C.mark_damage(w, eid, "zipper_burst", "forced closed over {:.0%} fill".format(fill))
            return "The teeth split under the strain"
        raise ActionError("Far too full to close; take something out or repack" if C.realistic(ctx) else
                          "Far too full to close ({:.0%}); take something out or repack".format(fill), "precondition")
    if fill > 1.0 + 1e-9:
        if not ctx.args.get("compress"):
            if ctx.args.get("force"):
                zipper.update(closed_frac=to, teeth_split=True, meshed=False)
                w.mut(eid)["zipper"] = zipper
                C.mark_damage(w, eid, "zipper_burst", "forced without compressing")
                return "The teeth split under the strain"
            raise ActionError("The zipper will not meet; compress the contents (compress=true) or repack", "precondition")
        for child in w.descendants(eid):
            crec = w.ent(child)
            if (C.is_fragile(crec) or crec.get("crushable")) and C.num((crec.get("wrapped") or {}).get("padding_mm")) < 8.0:
                C.break_item(w, child, "compressed_in_overfull_bag")
    snag_risk = rec.get("loose_lining") or any(w.ent(c).get("near_zipper") for c in w.children(eid)) or fill > 0.95
    if snag_risk and not ctx.args.get("taut"):
        zipper.update(closed_frac=round(min(to, 0.6), 2), snagged=True)
        w.mut(eid)["zipper"] = zipper
        if ctx.args.get("force"):
            C.mark_damage(w, eid, "fabric_torn", "zipper forced over caught fabric")
            zipper.update(derailed=True)
            w.mut(eid)["zipper"] = zipper
            return "Fabric was caught and tore; the slider derailed"
        return "Fabric caught in the teeth at {:.0%}".format(zipper["closed_frac"])
    zipper.update(closed_frac=to, snagged=False, meshed=True)
    w.mut(eid)["zipper"] = zipper
    if rec.get("openable"):
        w.mut(eid)["open"] = to < 0.98
    return "Zipped {} to {:.0%}".format(eid, to)


@action("unzip", params={"target": "entity", "to_frac": {"type": "number", "default": 0.0, "min": 0.0, "max": 1.0},
                         "taut": {"type": "bool", "default": True}, "compartment": "?entity"},
        duration=4.0, energy=0.005, category="physical",
        doc="Open a zipper to to_frac (0 = fully open), holding the fabric taut so the slider stays on track. Opening someone's private "
            "compartment needs their permission.")
def unzip(ctx):
    w = ctx.w
    eid = ctx.args.get("compartment") or ctx.args["target"]
    # a private bag or compartment is guarded by the kernel's own permission check (ctx.obj: owner approval, with its
    # generic refusal) and the contents of a private bag are not even visible without approval, so the domain adds
    # no refusal of its own that could name an owner or a privacy flag
    rec = ctx.obj(eid, touch=True)
    zipper = _zipper(rec)
    to = C.num(ctx.args.get("to_frac"), 0.0)
    if not ctx.args.get("taut") and zipper.get("closed_frac", 0) > 0.5:
        zipper.update(derailed=True)
        C.mark_damage(w, eid, "slider_derailed", "unzipped without holding the fabric taut", broken=False)
    zipper.update(closed_frac=to, snagged=False)
    w.mut(eid)["zipper"] = zipper
    if rec.get("openable"):
        w.mut(eid)["open"] = to < 0.98
    return "Unzipped {} to {:.0%} closed".format(eid, to)


# ----------------------------------------------------------------------------------
# snaps (press studs, snap lids and snap caps)

@action("snap", params={"target": "entity", "destination": "?entity", "stud": "?int", "socket": "?int",
                        "force_n": {"type": "number", "default": 40.0, "min": 0.0, "max": 300.0}, "corners": "?int"},
        duration=3.0, energy=0.005, category="physical",
        inverse=lambda ctx, before: {"action": "unsnap", "target": ctx.args["target"]},
        doc="Press a snap closed: a stud into its socket on a garment/cover (stud i belongs in socket i), a held snap lid or marker cap "
            "onto its container (press all corners; only the matching lid seals). Too little force does not engage; off-axis "
            "excess force breaks plastic.")
def snap(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    force = C.commanded_force(ctx, "force_n", 40.0)
    dest = ctx.args.get("destination")
    if dest:
        C.held_or_fail(ctx, eid)
        drec = ctx.obj(dest, touch=True)
        require(rec.get("snap_cap") or rec.get("lid_fit") or rec.get("fits"), "That is not a snap lid or cap", "precondition")
        matches = rec.get("fits") == dest or (rec.get("lid_fit") is not None and rec.get("lid_fit") == drec.get("lid_fit"))
        corners_needed = int(C.num(rec.get("corners"), 1))
        corners = int(ctx.args.get("corners") if ctx.args.get("corners") is not None else corners_needed)
        seated = force >= C.num(rec.get("seat_force_n"), 15.0) and corners >= corners_needed and matches
        if force > C.num(rec.get("max_force_n"), 200.0):
            C.mark_damage(w, eid, "cracked", "pressed with excessive force", broken=True)
        release_from_hands(w, eid)
        m = w.mut(eid)
        m["location"], m["relation"], m["seated"] = dest, "attached", seated
        m.pop("held_hand", None)
        w.log_move(eid, "robot", dest)
        if (drec.get("ink_color") or C.is_kind(drec, "marker", "pen", "glue_stick", "paint_pot")) and not matches:
            # the wrong cap carries the other colour's ink onto this tip
            w.mut(dest)["contaminated_tip"] = True
        update_seal(w, dest)
        if not matches:
            return "It sits on {} but does not belong to it".format(dest)
        return "Snapped {} onto {}{}".format(eid, dest, "" if seated else " (not fully seated)")
    snaps = rec.get("snaps")
    require(isinstance(snaps, dict), "It has no snap fasteners", "precondition")
    stud, socket = ctx.args.get("stud"), ctx.args.get("socket")
    require(stud is not None and socket is not None, "Say which stud and which socket", "argument")
    count = int(C.num(snaps.get("count"), 1))
    require(0 <= stud < count and 0 <= socket < count, "No such stud/socket", "argument")
    pairs = dict(snaps.get("pairs") or {})
    if force > C.num(snaps.get("break_force_n"), 150.0):
        C.mark_damage(w, eid, "snap_broken", "pressed off-axis too hard", broken=False)
        return "The snap cracked"
    engaged = force >= C.num(snaps.get("engage_force_n"), 30.0)
    pairs[str(stud)] = {"socket": socket, "engaged": engaged, "weak": not engaged}
    m = w.mut(eid)
    m["snaps"] = dict(snaps, pairs=pairs)
    m["misaligned_snaps"] = sum(1 for k, v in pairs.items() if int(k) != int(v["socket"]))
    m["snaps_closed"] = sum(1 for v in pairs.values() if v["engaged"])
    return "Snapped stud {} into socket {}{}".format(stud, socket, "" if engaged else " (not engaged)")


@action("unsnap", params={"target": "entity", "stud": "?int", "method": {"type": "str", "default": "near_base", "choices": ["near_base", "pull_fabric", "flex_tab", "pry"]},
                          "hand": "?str:left|right"},
        duration=2.5, energy=0.005, category="physical",
        doc="Release snaps by pulling right at the snap base (fabric) or flexing the lid's tab (plastic). Pulling fabric far from the snap "
            "tears it; prying a tab snaps it off. For snap lids/caps the lid goes into a free hand.")
def unsnap(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    method = ctx.args.get("method")
    lid = eid if rec.get("relation") == "attached" and (rec.get("snap_cap") or rec.get("lid_fit") or rec.get("fits")) else None
    if lid is None:
        for child in w.children(eid):
            crec = w.ent(child)
            if crec.get("relation") == "attached" and (crec.get("snap_cap") or crec.get("lid_fit") or crec.get("fits")):
                lid = child
                break
    if lid is not None:
        base = w.ent(lid)["location"]
        if method == "pry":
            C.mark_damage(w, lid, "tab_broken", "pried off", broken=False)
        m = w.mut(lid)
        m["seated"] = False
        C.take_into_hand(ctx, lid, ctx.args.get("hand"))
        update_seal(w, base)
        return "Lifted {} off".format(lid)
    snaps = rec.get("snaps")
    require(isinstance(snaps, dict), "It has no snaps", "precondition")
    if method == "pull_fabric":
        C.mark_damage(w, eid, "fabric_torn", "pulled away from the snap base", broken=False)
    pairs = dict(snaps.get("pairs") or {})
    stud = ctx.args.get("stud")
    if stud is None:
        pairs = {}
    else:
        pairs.pop(str(stud), None)
    m = w.mut(eid)
    m["snaps"] = dict(snaps, pairs=pairs)
    m["snaps_closed"] = sum(1 for v in pairs.values() if v["engaged"])
    m["misaligned_snaps"] = sum(1 for k, v in pairs.items() if int(k) != int(v["socket"]))
    return "Unsnapped"


# ----------------------------------------------------------------------------------
# hook-and-loop

@action("velcro", params={"target": "entity", "overlap_frac": {"type": "number", "default": 1.0, "min": 0.0, "max": 1.0},
                          "tension": {"type": "str", "default": "snug", "choices": ["loose", "snug", "tight"]}, "aligned": {"type": "bool", "default": True}},
        duration=2.5, energy=0.002, category="physical",
        inverse=lambda ctx, before: {"action": "unvelcro", "target": ctx.args["target"]},
        doc="Fasten a hook-and-loop strap (shoe strap, cable tie, cuff, coiled-cable strap) with full aligned overlap at snug tension. "
            "Under 60% overlap it pops open on a tug; a tight strap on someone's foot is uncomfortable.")
def velcro(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    overlap = C.num(ctx.args.get("overlap_frac"), 1.0)
    tension = ctx.args.get("tension")
    state = {"engaged": True, "overlap_frac": round(overlap, 2), "aligned": bool(ctx.args.get("aligned", True)), "tension": tension}
    m = w.mut(eid)
    # a coil or roll this domain recorded notes the strap; another domain's flag or word (a garden hose's coiled true,
    # an extension cord's "loose", a towel's rolled true) stays as its owner wrote it: the strap is the velcro record
    if isinstance(rec.get("coiled"), dict) and C.coil_of(rec) is not None:
        m["coiled"] = dict(rec["coiled"], secured=overlap >= 0.6)
    if isinstance(rec.get("rolled"), dict) and C.roll_of(rec) is not None:
        m["rolled"] = dict(rec["rolled"], strapped=overlap >= 0.6)
    m["velcro"] = state
    wearer = rec.get("location") if rec.get("location") in w.s.get("people", {}) or rec.get("relation") == "worn" else None
    if wearer and tension == "tight":
        m["too_tight"] = True
        C.note_event(w, "too_tight", eid, person=wearer)
    if rec.get("snag_risk") and not ctx.args.get("aligned", True):
        C.mark_damage(w, eid, "snagged_fabric", "hooks caught on nearby knit", broken=False)
    return "Fastened the hook-and-loop strap ({:.0%} overlap, {})".format(overlap, tension)


@action("unvelcro", params={"target": "entity", "method": {"type": "str", "default": "peel", "choices": ["peel", "shear"]},
                            "speed": {"type": "str", "default": "slow", "choices": ["slow", "fast"]}},
        duration=lambda ctx: 4.0 if ctx.args.get("speed") == "slow" else 1.0, energy=0.002, category="physical",
        doc="Separate hook-and-loop by peeling from one end (slow is quiet, ~45 dB; fast rips at ~70 dB). Pulling in shear rips the stitching.")
def unvelcro(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    state = dict(rec.get("velcro") or {})
    require(state.get("engaged"), "It is not fastened")
    if ctx.args.get("method") == "shear":
        C.mark_damage(w, eid, "stitching_torn", "pulled apart in shear")
    loud = ctx.args.get("speed") == "fast"
    db = 70.0 if loud else 45.0
    C.add_noise(w, 0.5 if loud else 0.05)
    C.note_event(w, "noise", eid, db=db, source="hook_and_loop")
    sleepers = [p for p, person in sorted(w.s.get("people", {}).items()) if person.get("location") == w.robot["room"] and not person.get("awake", True)]
    if loud and sleepers:
        facts = w.sec("facts")
        facts["loud_near_sleeper"] = int(facts.get("loud_near_sleeper", 0)) + 1
    state.update(engaged=False)
    m = w.mut(eid)
    m["velcro"] = state
    if isinstance(rec.get("coiled"), dict) and C.coil_of(rec) is not None:
        m["coiled"] = dict(rec["coiled"], secured=False)
    if isinstance(rec.get("rolled"), dict) and C.roll_of(rec) is not None:
        m["rolled"] = dict(rec["rolled"], strapped=False)
    return "Separated the strap ({:.0f} dB)".format(db)


# ----------------------------------------------------------------------------------
# knots, ties and tangles

@action("tie", params={"target": "entity", "knot": {"type": "str", "default": "double", "choices": sorted(KNOTS)},
                       "destination": "?entity", "tool": "?entity", "tightness": {"type": "str", "default": "snug", "choices": ["loose", "snug", "tight"]},
                       "press_air": {"type": "bool", "default": False}, "force": {"type": "bool", "default": False}},
        duration=lambda ctx: ctx.data.get("manip_s", 10.0), energy=0.005, category="physical",
        inverse=lambda ctx, before: {"action": "untie", "target": ctx.args["target"], "method": "pick_apart"},
        doc="Tie a knot: close a trash/bread bag with its drawstring or flaps (needs headroom), bow laces or a ribbon, bundle papers "
            "with held twine (tool), or tie a plant to a stake (destination; loose figure_eight). Knot strength by type: twist 0.3 kg, "
            "overhand/granny 6 (granny slips), bow 10, double 20, square 25. Pressing air out of a bag with sharp contents punctures it.")
def tie(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    knot = ctx.args["knot"]
    hold, releasable, slips = KNOTS[knot]
    tool = ctx.args.get("tool")
    dest = ctx.args.get("destination")
    ctx.data["manip_s"] = 8.0 + (4.0 if knot in ("double", "square", "surgeon", "double_bow") else 0.0)
    if tool:
        require(ctx.holding(tool), "Hold the twine or ribbon first")
        trec = w.ent(tool)
        need = 2.0 * (sum(C.dims(rec)[:2]) + C.dims(rec)[2]) + 0.4
        require(C.num(trec.get("length_m"), 0.0) >= need, "Not enough cord left to go round it" if C.realistic(ctx) else
                "Not enough cord left ({:.1f} m needed)".format(need), "resource")
        w.mut(tool)["length_m"] = round(C.num(trec.get("length_m")) - need, 2)
        m = w.mut(eid)
        m["bundled"] = True
        m["closure"] = {"kind": "tie", "knot": knot, "hold_kg": hold * (0.5 if slips else 1.0), "holds": True, "releasable": releasable, "by": tool}
        if C.is_kind(rec, "gift", "gift_box") or rec.get("wrapped"):
            m["ribbon"] = {"knot": knot, "material": trec.get("material")}
        return "Tied {} with a {} knot".format(eid, knot.replace("_", " "))
    if dest:
        drec = ctx.obj(dest, touch=True)
        m = w.mut(eid)
        m["tied_to"] = dest
        m["tie"] = {"knot": knot, "tightness": ctx.args.get("tightness")}
        if ctx.args.get("tightness") == "tight" and (rec.get("living") or C.is_kind(rec, "plant")):
            m["girdled"] = True
            C.note_event(w, "girdling", eid)
        return "Tied {} to {}".format(eid, dest)
    kind = rec.get("tie_kind") or ("bag" if C.is_kind(rec, "bag", "trash_bag", "liner") else "cord")
    if kind in ("drawstring", "flaps", "bag", "twist_tie"):
        from .stacking import used_volume
        cap = C.num(rec.get("capacity_l"))
        fill = used_volume(w, eid) / cap if cap > 0 else 0.0
        if fill > 0.9:
            if not ctx.args.get("force"):
                raise ActionError("Not enough headroom to tie it; move some contents to another bag" if C.realistic(ctx) else
                                  "Not enough headroom to tie it ({:.0%} full); move some contents to another bag".format(fill), "precondition")
            m = w.mut(eid)
            m["torn"] = True
            C.mark_damage(w, eid, "torn", "forced closed while overfull", broken=False)
        if ctx.args.get("press_air") and any("sharp" in (w.ent(c).get("hazards") or []) or w.ent(c).get("broken") for c in w.descendants(eid)):
            m = w.mut(eid)
            m["torn"] = True
            m["leaking"] = True
            C.mark_damage(w, eid, "punctured", "squeezed against sharp contents", broken=False)
        parent = w.s["entities"].get(rec.get("location"))
        m = w.mut(eid)
        closure = {"kind": "knot" if knot != "twist" else "twist", "knot": knot, "hold_kg": hold * (0.6 if slips else 1.0), "holds": True,
                   "releasable": releasable}
        if rec.get("over_rim") and parent is not None:
            closure["tied_to"] = rec["location"]
        if rec.get("torn"):
            closure["holds"] = False
            closure["hold_kg"] = 0.0
        m["closure"] = closure
        m["open"] = False if rec.get("openable") else m.get("open")
        return "Tied {} closed with a {} knot{}".format(eid, knot.replace("_", " "), " around the bin rim" if closure.get("tied_to") else "")
    # laces, cords, ribbons on the item itself
    m = w.mut(eid)
    m["knot"] = {"type": knot, "tight": ctx.args.get("tightness") == "tight", "releasable": releasable}
    if C.is_kind(rec, "shoe", "sneaker") or rec.get("laces"):
        m["laces_tied"] = knot in ("bow", "double_bow")
    return "Tied a {} in {}".format(knot.replace("_", " "), eid)


@action("untie", params={"target": "entity", "method": {"type": "str", "default": "pick_apart", "choices": ["pick_apart", "pull_tail", "pull_ends", "cut"]},
                         "tool": "?entity"},
        duration=lambda ctx: ctx.data.get("manip_s", 10.0), energy=0.005, category="physical",
        doc="Undo a knot, tie or tangle: pick_apart works slack into it (slow but safe; tangled cables/chains lose crossings), pull_tail "
            "releases bows and slip knots, pull_ends tightens everything else (bows become hard knots), cut needs a held blade and ruins "
            "cords that must not be cut (ask first).")
def untie(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    method = ctx.args["method"]
    tangle = rec.get("tangle") or {}
    if tangle and (C.num(tangle.get("crossings")) > 0 or C.num(tangle.get("knots")) > 0):
        return _untangle(ctx, eid, rec, dict(tangle), method)
    knot = rec.get("knot") or {}
    closure = rec.get("closure") or {}
    kind = knot.get("type") or closure.get("knot")
    if not kind and tangle:
        ctx.data["manip_s"] = 2.0
        return "It is already free of tangles"
    require(kind, "There is no knot to undo", "precondition")
    m = w.mut(eid)
    if method == "cut":
        C.tool_of(ctx, "cut", ctx.args.get("tool"))
        if rec.get("_cut_ok") is False or C.is_kind(rec, "shoe", "leash", "ribbon_keep") or rec.get("keep_cord"):
            C.mark_damage(w, eid, "cord_cut", "cut instead of untied", broken=False)
        m["cut"] = True
        ctx.data["manip_s"] = 3.0
    elif method == "pull_tail":
        if not (knot.get("releasable") or closure.get("releasable")):
            ctx.data["manip_s"] = 3.0
            if C.realistic(ctx):
                return "Pulling the tail does nothing: the knot holds"
            return "Pulling the tail does nothing on a {} knot".format(kind.replace("_", " "))
        ctx.data["manip_s"] = 2.0
    elif method == "pull_ends":
        tightened = dict(knot or closure)
        if kind in ("bow", "double_bow"):
            tightened["type"] = "hard_knot"
            tightened["knot"] = "hard_knot"
        tightened["tight"] = True
        tightened["releasable"] = False
        if knot:
            m["knot"] = tightened
        else:
            m["closure"] = tightened
        if rec.get("delicate") or C.is_kind(rec, "necklace", "chain"):
            C.mark_damage(w, eid, "link_broken", "pulled tight", broken=True)
        ctx.data["manip_s"] = 3.0
        return "Pulling the standing ends drew the knot tighter"
    else:
        tight = knot.get("tight") or closure.get("tight")
        ctx.data["manip_s"] = 25.0 if tight else 12.0
    if rec.get("drawstring_channel") and method == "pull_ends":
        m["drawstring_pulled_out"] = True
    m["knot"] = None
    if closure:
        m["closure"] = None
        if rec.get("openable"):
            m["open"] = True
    m.pop("bundled", None)
    if C.is_kind(rec, "shoe", "sneaker") or rec.get("laces"):
        m["laces_tied"] = False
    return "Untied {}".format(eid)


def _untangle(ctx, eid, rec, tangle, method):
    w = ctx.w
    group = sorted(set([eid] + list(tangle.get("with") or [])))
    if method == "pull_ends":
        for member in group:
            if member in w.s["entities"]:
                t = dict(w.ent(member).get("tangle") or {})
                t["knots"] = int(C.num(t.get("knots"))) + 1
                t["tight"] = True
                w.mut(member)["tangle"] = t
                mrec = w.ent(member)
                if mrec.get("plugged_in") or mrec.get("tethered_to"):
                    w.mut(member)["plugged_in"] = False
                    C.note_event(w, "unplugged_by_pulling", member)
                if mrec.get("delicate") or C.is_kind(mrec, "necklace", "chain"):
                    C.mark_damage(w, member, "link_broken", "pulled tight", broken=True)
        ctx.data["manip_s"] = 3.0
        return "Pulling tightened the tangle"
    if method == "cut":
        C.tool_of(ctx, "cut", ctx.args.get("tool"))
        C.mark_damage(w, eid, "cord_cut", "cut to free it", broken=True)
        for member in group:
            if member in w.s["entities"]:
                w.mut(member)["tangle"] = {"crossings": 0, "knots": 0, "with": []}
        ctx.data["manip_s"] = 3.0
        return "Cut through the tangle"
    if method == "pull_tail":
        ctx.data["manip_s"] = 3.0
        return "That only shifts the loops around"
    per_call = 3 if not tangle.get("tight") else 1
    crossings = max(0, int(C.num(tangle.get("crossings"))) - per_call)
    knots = max(0, int(C.num(tangle.get("knots"))) - (1 if crossings == 0 or per_call > 1 else 0))
    for member in group:
        if member in w.s["entities"]:
            t = dict(w.ent(member).get("tangle") or {})
            t["crossings"] = crossings
            t["knots"] = knots
            if crossings == 0 and knots == 0:
                t["with"] = []
                t["tight"] = False
            w.mut(member)["tangle"] = t
            mrec = w.ent(member)
            if mrec.get("plugged_in") and mrec.get("pull_limit_n") is not None:
                pass
    ctx.data["manip_s"] = 20.0 if not tangle.get("tight") else 40.0
    if crossings == 0 and knots == 0:
        return "Untangled: the pieces are separate"
    return "Worked loose some loops ({} crossings, {} knots left)".format(crossings, knots)


# ----------------------------------------------------------------------------------
# seals

@action("seal", params={"target": "entity", "clear_track": {"type": "bool", "default": True}, "press_air": {"type": "bool", "default": True},
                        "tool": "?entity", "full_length": {"type": "bool", "default": True}},
        duration=5.0, energy=0.003, category="physical",
        inverse=lambda ctx, before: {"action": "unseal", "target": ctx.args["target"]},
        doc="Seal a closure: press a zip-top bag's track closed along its full length (clear crumbs from the track, press out air), "
            "seal a peel-and-seal or gummed envelope flap (gum needs a damp sponge/moistener tool; a soaked flap warps), or close a "
            "resealable package strip. Check the contents are complete before sealing envelopes.")
def seal(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    closure_kind = rec.get("seal_kind") or ("zip_track" if rec.get("zip_track") else "flap" if C.is_kind(rec, "envelope") else None)
    require(closure_kind, "It has no seal to close", "precondition")
    m = w.mut(eid)
    if closure_kind == "zip_track":
        gap = bool(rec.get("track_contaminated")) and not ctx.args.get("clear_track") or not ctx.args.get("full_length")
        m["track_contaminated"] = bool(rec.get("track_contaminated")) and not ctx.args.get("clear_track")
        m["sealed"] = not gap
        m["air_frac"] = 0.1 if ctx.args.get("press_air") else 0.6
        m["closure"] = {"kind": "zip_track", "holds": not gap, "gap": gap}
        m["open"] = False if rec.get("openable") else m.get("open")
        return "Sealed the bag{}".format("" if not gap else " but the track has a gap")
    if closure_kind == "flap":
        if rec.get("flap") == "gummed":
            tool = ctx.args.get("tool")
            require(tool and ctx.holding(tool), "Moisten the gum with a damp sponge or moistener (tool)", "precondition")
            wet = C.num(w.ent(tool).get("wetness"))
            require(wet > 0.05, "The sponge is dry", "precondition")
            if wet > 0.8:
                m["warped"] = True
                m["ink_run"] = True
                C.mark_damage(w, eid, "soaked", "flap soaked; envelope warped and ink ran", broken=False)
        contents = [c for c in w.children(eid) if w.ent(c).get("relation") == "in"]
        m["sealed"] = True
        m["sealed_contents"] = contents
        m["open"] = False
        m["flap_adhesion"] = 1.0 if ctx.args.get("full_length") else 0.6
        return "Sealed the envelope with {} item(s) inside".format(len(contents))
    m["sealed"] = True
    m["open"] = False if rec.get("openable") else m.get("open")
    m["closure"] = {"kind": closure_kind, "holds": True}
    return "Sealed {}".format(eid)


@action("unseal", params={"target": "entity", "tool": "?entity"}, duration=4.0, energy=0.003, category="physical",
        doc="Open a sealed bag, envelope (with a letter opener tool to avoid tearing contents) or resealable package.")
def unseal(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    # whether there is a seal to break is a fact of the particular product (a pharmacy seal, a resealable strip): an
    # unidentified look-alike is identified first, so every member of its group gets the same answer
    C.require_identified(ctx, eid)
    require(rec.get("sealed") or (rec.get("closure") or {}).get("holds"), "It is not sealed")
    m = w.mut(eid)
    if C.is_kind(rec, "envelope") and not ctx.args.get("tool"):
        m["torn_open"] = True
        for child in w.children(eid):
            if w.ent(child).get("tearable"):
                C.mark_damage(w, child, "torn", "ripped while tearing the envelope open", broken=False)
    m["sealed"] = False
    m["closure"] = None
    if rec.get("openable"):
        m["open"] = True
    return "Opened {}".format(eid)
