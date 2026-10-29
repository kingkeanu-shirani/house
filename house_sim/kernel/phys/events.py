"""Perturbation events: the honest test of "stable", "secured", "heavy enough" and "packed safely"
(docs/design/TIER1_PHYSICS.md 7.4; fidelity P-approx).

Schedule them with the existing ``event`` setup op ({"op": "event", "at_s" | "at_step" | "when": ..., "effect":
"physics.bump", "args": {...}, "repeat_s": 300}). kernel/physics.py registers each as the hook ``event:<effect>``;
every effect is ``fn(w, args, env)`` and returns True. They call the kernel's consequence functions directly, so
they work whatever the laws' states. Attribution follows 6.4 (impact.robot_caused): an event is exogenous, so a
consequence counts against the robot only when the robot arranged what it hit (the _robot_moved marker on the item or
on a support in its location chain) or a support failed under load the robot added; ``"by": "robot"`` or
``"by": "exogenous"`` in args overrides that. A missing target does nothing (the scene changed).

    physics.bump        target (an item, or a surface: every free-standing item on it) or room (every free-standing
                        item on a surface there); g (default 0.3: a dog or a person brushing past; a cat's tail about
                        0.3 at most 0.6 m up); direction "toward_edge" (default) or room-frame degrees; max_h_m (only
                        items whose base is at most this high)
    physics.nudge       target, newtons, height_m (default its centre of mass), direction: a push on one item (a cat
                        pushing a cup): it tips when the push's moment beats its weight's (F h > m g b / 2) before
                        the push beats friction (F > mu m g), else it slides
    physics.pull        target furniture, kg (x g) or newtons, height_m or at "top_drawer" (default its top), on top of
                        the open drawers the phys.tip_loads hooks report: a child pulling or climbing (27 kg, ASTM
                        F2057) tips an unanchored dresser when the margin goes negative
    physics.load_test   target support, slot ("surface" | "in" | "hung") or shelf (index), kg: a test load or a person
                        leaning: the slot gives way when its load plus kg passes its limit (the scene allowance of the
                        overload law included)
    physics.wind_gust   room, or outdoor: true (every outdoor room); mps: the gust as the weather states it (10 m open
                        terrain), scaled to each item's height and shelter (environment.object_wind_mps), or at the item
                        with local: true (an indoor room is always local); sustained_mps (default mps / 1.5);
                        direction_deg (default the weather's); every exposed free-standing item feels it once
    physics.rain        mm_h, seconds, room (default every outdoor room): exposed items get wet (environment.rain_wet);
                        the weather state is not changed
    physics.transport   target (a packed container), drop_m (default 0.3: a house move; ISTA 1A parcels 0.76), drops
                        (default 1): the container and everything in it take each drop onto a hard floor through
                        their own padding (bubble wrap); jostle (g, default 0) also knocks what stands inside
    physics.brake       target (a surface, a trunk or a cart), accel_g (default 0.4: a car braking), direction: every
                        free-standing item on or in it is knocked forward
    physics.tilt        target, deg (tilt_deg), direction_deg (tilt_dir_deg, optional): what rests on it slides, tips or
                        rolls and open containers on it spill (the slope and open_spill rules applied now)
    physics.drop        target, height_m (default its height above the floor): someone drops it
"""
from .. import geometry
from ..predicates import root_room
from . import environment, impact, liquids, statics, support

G = statics.G
DEFAULT_BUMP_G = 0.3
DEFAULT_BRAKE_G = 0.4
DEFAULT_TRANSPORT_DROP_M = 0.3
DEFAULT_GUST_FACTOR = environment.DEFAULT_GUST_FACTOR
TOP_DRAWER_BELOW_TOP_M = 0.1        # the top drawer's handle sits about 10 cm below a dresser's top


def _num(value, default):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if out == out and out not in (float("inf"), float("-inf")) else default


def _by(args):
    by = (args or {}).get("by")
    return by if by in ("robot", "exogenous") else None


def _ids(value):
    """A target argument as a list of ids (a single id or a list)."""
    if isinstance(value, (list, tuple)):
        return [v for v in value if isinstance(v, str)]
    return [value] if isinstance(value, str) else []


def _direction(value):
    if value is None or value == "toward_edge":
        return None
    return _num(value, None)


def _riders(state, sid, cmap, relations=("on",)):
    """Free-standing items resting on (or in) a support, sorted."""
    entities = state["entities"]
    return [c for c in cmap.get(sid, ()) if entities[c].get("relation", "on") in relations
            and statics.free_standing(state, c)]


def bump(w, args, env=None):
    """physics.bump: a jolt of g on one item, on everything on a surface, or on everything on the surfaces of a room."""
    state = w.s
    entities = state["entities"]
    accel = _num(args.get("g"), DEFAULT_BUMP_G)
    direction = _direction(args.get("direction", "toward_edge"))
    max_h = _num(args.get("max_h_m"), None)
    cmap = support.children_map(state)
    items = []
    for target in _ids(args.get("target")):
        if target not in entities:
            continue
        riders = _riders(state, target, cmap)
        items.extend(riders if riders else [target])
    room = args.get("room")
    if isinstance(room, str) and room in state["rooms"]:
        for sid in sorted(entities):
            if root_room(state, sid) == room and entities[sid].get("location") != "robot":
                items.extend(_riders(state, sid, cmap))
    for eid in sorted(set(items)):
        if eid not in entities:
            continue
        if max_h is not None and geometry.height_m(state, eid) > max_h + 1e-9:
            continue
        impact.knock(w, eid, accel, direction=direction, cause="physics.bump", by=_by(args), cmap=cmap)
    return True


def nudge(w, args, env=None):
    """physics.nudge: a push of `newtons` on one item at height_m: it tips when the push's moment beats the weight's
    (F h > m g b / 2) before the push beats friction, else it slides."""
    state = w.s
    by = _by(args)
    force = _num(args.get("newtons"), 0.0)
    direction = _direction(args.get("direction"))
    for eid in _ids(args.get("target")):
        if eid not in state["entities"] or force <= 0 or not statics.free_standing(state, eid):
            continue
        mass = max(1e-6, support.subtree_mass(state, eid))
        weight = mass * G
        if direction is None:
            direction = statics.overhang_dir_deg(state, eid)
        lying = state["entities"][eid].get("orientation") == "on_side"
        z = max(1e-3, support.com_height(state, eid))
        h = max(1e-3, _num(args.get("height_m"), z))
        tip_g = statics.INF if lying else statics.tip_accel_g(state, eid, axis=direction)
        # tip_g = b / 2z, so the push that tips it at height h is m g (b / 2) / h = weight x tip_g x z / h
        tip_n = weight * tip_g * z / h if tip_g != statics.INF else statics.INF
        slide_n = weight * statics.slide_accel_g(state, eid)
        if force <= min(tip_n, slide_n) + 1e-9:
            continue
        if tip_n <= slide_n:
            impact.tip_over(w, eid, "physics.nudge", direction=direction, by=by)
        else:
            impact.slide(w, eid, force / weight, direction=direction, cause="physics.nudge", by=by)
    return True


def pull(w, args, env=None):
    """physics.pull: a pull (kg x g, or newtons) at a height on a piece of furniture; it tips when the tip margin
    (statics.tip_margin_nm, open drawers included through phys.tip_loads) goes negative."""
    state = w.s
    force = _num(args.get("newtons"), None)
    if force is None:
        force = _num(args.get("kg"), 0.0) * G
    for fid in _ids(args.get("target")):
        rec = state["entities"].get(fid)
        if rec is None or force <= 0:
            continue
        top = support.surface_height(rec)
        if args.get("at") == "top_drawer":
            height = max(0.05, top - TOP_DRAWER_BELOW_TOP_M)
        else:
            height = _num(args.get("height_m"), top)
        if statics.tip_margin_nm(state, fid, pull_n=force, pull_h_m=height) < 0:
            impact.tip_over(w, fid, "physics.pull", direction=_direction(args.get("direction")), by=_by(args))
    return True


def _slot(rec, args):
    if args.get("shelf") is not None:
        return ("shelf", int(_num(args.get("shelf"), 0)))
    slot = args.get("slot") or ("in" if rec.get("container") else "surface")
    return (slot,) if slot in ("surface", "in", "hung") else ("surface",)


def load_test(w, args, env=None):
    """physics.load_test: kg more on one slot of a support; it gives way when the total passes its limit."""
    state = w.s
    extra = _num(args.get("kg"), 0.0)
    for sid in _ids(args.get("target")):
        rec = state["entities"].get(sid)
        if rec is None or extra <= 0:
            continue
        slot = _slot(rec, args)
        status = statics.overload(state, sid, slot, env)
        if status is not None and status["load"] + extra > status["limit"] + 1e-9:
            impact.collapse(w, sid, slot, by=_by(args))
    return True


def _outdoor_rooms(state):
    return sorted(rid for rid, room in state["rooms"].items() if room.get("outdoor"))


def wind_gust(w, args, env=None):
    """physics.wind_gust: one gust over a room (or every outdoor room) on every exposed free-standing item."""
    state = w.s
    rooms = _outdoor_rooms(state) if args.get("outdoor") else [r for r in _ids(args.get("room")) if r in state["rooms"]]
    gust = _num(args.get("mps"), 0.0)
    if gust <= 0 or not rooms:
        return True
    sustained = _num(args.get("sustained_mps"), gust / DEFAULT_GUST_FACTOR)
    weather = state.get("weather") or {}
    direction = _num(args.get("direction_deg"), environment.wind_direction_deg(weather))
    cmap = support.children_map(state)
    for eid in sorted(state["entities"]):
        rid = root_room(state, eid)
        if rid not in rooms or eid not in state["entities"] or not environment.free_to_move(state, eid):
            continue
        outdoor = bool(state["rooms"][rid].get("outdoor"))
        if outdoor and not environment.exposed(state, eid):
            continue
        if not outdoor and environment._sheltered_by(state, eid):
            continue
        if outdoor and not args.get("local"):
            v, g = environment.object_wind_mps(state, eid, {"wind_mps": sustained, "gust_mps": gust})
        else:
            v, g = min(sustained, gust), gust
        environment.wind_effect(w, eid, v, direction, by=_by(args), cmap=cmap, gust_mps=g)
    return True


def rain(w, args, env=None):
    """physics.rain: rain at mm_h for seconds on the exposed items of a room (or every outdoor room)."""
    state = w.s
    rooms = [r for r in _ids(args.get("room")) if r in state["rooms"]] or _outdoor_rooms(state)
    mm_h, seconds = _num(args.get("mm_h"), 0.0), _num(args.get("seconds"), 0.0)
    if mm_h <= 0 or seconds <= 0:
        return True
    for eid in sorted(state["entities"]):
        if eid in state["entities"] and root_room(state, eid) in rooms and environment.exposed(state, eid):
            environment.rain_wet(w, eid, mm_h, seconds)
    return True


def transport(w, args, env=None):
    """physics.transport: a packed container is carried and dropped `drops` times from drop_m onto a hard floor;
    it and everything in it take each impact through their own padding (and the container's); jostle (g) knocks what
    stands inside."""
    state = w.s
    drop = max(0.0, _num(args.get("drop_m"), DEFAULT_TRANSPORT_DROP_M))
    count = max(1, int(_num(args.get("drops"), 1)))
    jostle = max(0.0, _num(args.get("jostle"), 0.0))
    by = _by(args)
    for box in _ids(args.get("target")):
        if box not in state["entities"]:
            continue
        for _ in range(count):
            if drop > 0:
                outer = statics.padding_factor(state["entities"][box])
                impact.impact(w, box, drop / outer, "physics.transport", by=by)
                for item in support.descendants(state, box):
                    rec = state["entities"].get(item)
                    if rec is None or rec.get("relation") not in ("in", "on"):
                        continue
                    h_eff = drop / outer / statics.padding_factor(rec)
                    impact.impact(w, item, h_eff, "physics.transport", by=by)
            if jostle > 0:
                cmap = support.children_map(state)
                for item in _riders(state, box, cmap, ("in", "on")):
                    impact.knock(w, item, jostle, cause="physics.transport", by=by, cmap=cmap)
    return True


def brake(w, args, env=None):
    """physics.brake: a sudden stop of accel_g knocks every free-standing item on or in the target forward."""
    state = w.s
    accel = _num(args.get("accel_g"), DEFAULT_BRAKE_G)
    direction = _direction(args.get("direction"))
    cmap = support.children_map(state)
    for sid in _ids(args.get("target")):
        if sid not in state["entities"]:
            continue
        for item in _riders(state, sid, cmap, ("on", "in")):
            impact.knock(w, item, accel, direction=direction, cause="physics.brake", by=_by(args), cmap=cmap)
    return True


def tilt(w, args, env=None):
    """physics.tilt: the target tilts to deg; what rests on it slides, tips or rolls and open containers spill (the
    slope and open_spill rules applied now, whatever the laws' states)."""
    state = w.s
    deg = _num(args.get("deg"), 0.0)
    for sid in _ids(args.get("target")):
        if sid not in state["entities"]:
            continue
        m = w.mut(sid)
        m["tilt_deg"] = round(deg, 4)
        if args.get("direction_deg") is not None:
            m["tilt_dir_deg"] = round(_num(args.get("direction_deg"), 0.0) % 360.0, 4)
        cmap = support.children_map(state)
        for item in [sid] + _riders(state, sid, cmap, ("on", "in")):
            if item in state["entities"]:
                liquids.law_open_spill(w, item, env=env, cmap=cmap)
            if item != sid and item in state["entities"]:
                impact.slope_law(w, item, env=env, cmap=cmap)
    return True


def drop(w, args, env=None):
    """physics.drop: someone drops the target from height_m (default its height above the floor)."""
    state = w.s
    for eid in _ids(args.get("target")):
        if eid not in state["entities"]:
            continue
        height = _num(args.get("height_m"), None)
        impact.fall(w, eid, height_m=height, cause="physics.drop", by=_by(args))
    return True


# effect name -> function; kernel/physics.py registers each as the hook "event:<name>"
EFFECTS = (("physics.bump", bump), ("physics.nudge", nudge), ("physics.pull", pull), ("physics.load_test", load_test),
           ("physics.wind_gust", wind_gust), ("physics.rain", rain), ("physics.transport", transport),
           ("physics.brake", brake), ("physics.tilt", tilt), ("physics.drop", drop))
