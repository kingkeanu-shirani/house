"""Physical consequences of every robot motion (core move/approach/walk_to and this domain's
step/go_to/follow/lead/patrol, which all funnel through `traverse`).

Per path cell (fidelity G with P-approx traction):
  traction    available friction (floor material, wet film, puddles, oil, ice, moss, rugs without a
              pad, weather) vs required friction (speed, carried mass, slope): stumble or fall
  contacts    small floor items under the foot: cords snag and yank devices, rolling toys slip,
              crushable/fragile items break, soft items are kicked (sometimes under furniture),
              shards spread, low obstacles trip, glass panels and overhangs are walked into
  margins     sleeping people/pets wake, pet tails are stepped on, topple-prone or fragile items
              are brushed, hot surfaces touched, occupied ladders bumped, reversing cars strike
  terrain     edge drops, loose boards, holes, sprinkler heads, planted/seeded areas, pet waste,
              mud (sinking, stuck), gravel scatter, slab edges and curbs, narrow gaps
  social      close passes, contacts in crowds, cutting through conversations, TV sightlines,
              video-call camera frames
After the path: doorway clearance with payload, thresholds, stairs (stairs.py), sudden
encounters at doorways, sloshing, long-load swing, tracking dirt indoors, odometry, docking
state, line-of-sight room coverage, no-go zones and the robot's resting spot.

Energy is the kernel body's (kernel v3.0, TIER1_EMBODIMENT 4.3 / 4.6): the motion verbs write one
motion record (ctx.motion) and the engine charges what it says. This module never touches the
battery; it only corrects the record when the walk ends early: a stop or a fall cuts it to what the
robot actually walked (ctx.cut_motion, see `walked`). A walk the battery cut short (record cut
"battery") is an in-room trip: no doorway passage, no stairs, no arrival effects.

Physics is the kernel's (TIER1_PHYSICS 10.3): things the robot knocks, yanks or brushes fall, break
and spill through impact.fall / impact.tip_over / impact.break_entity / liquids.spill (common.py),
the robot's own falls go through body_faults.fall, and once the carry law is active the kernel
sloshes and topples what the robot carries (liquids.carry) with this domain's gait factors
(`carry_motion`, hook phys.carry_motion: stairs x1.8, a threshold or sliding-door track x1.15,
uneven outdoor ground or a slope x1.3); the legacy slosh below then stands down.

Camera (kernel v3.0): in geometric mode the floor ahead is judged with the camera's carried-load
occluder, a person on a video call is framed by their device's camera, and whether someone sees the
robot coming is their eyes' line of sight (sight.person_viewer); the entry-based privacy capture
counter is the camera log's job there. Room mode is unchanged.
"""
import math

from ...kernel import body, geometry
from ...kernel.errors import ActionError
from ...kernel.phys import lawbook
from ...kernel.plugins import ACTIONS, after, before, hook
from . import common as C
from . import hazards as H

LOW_LIGHT = 0.45


class Trip:
    def __init__(self, ctx, kind, rid, start, path, speed, conn=None, dest=None, target=None, stepping_over=False):
        self.ctx, self.w, self.kind = ctx, ctx.w, kind
        self.rid, self.start, self.path = rid, C.cell(start), [C.cell(c) for c in path]
        self.speed = speed if speed in C.SPEED_MODES else "normal"
        self.conn, self.dest, self.target = conn, dest, target
        self.stepping_over = stepping_over
        self.stopped_at = None
        self.fell = False
        self.crossed = False     # it reached the doorway or the stairs (the passage started)
        self.arrival_fell = False   # it fell on the landing cell beyond the doorway
        self.stumbled = False
        self.events = []
        self.kicked = set()      # items already kicked during this motion (a kick moves a thing once, not along the route)
        self.meters = geometry.path_length_m([self.start] + self.path)


# ----------------------------------------------------------------------
# guards before motion

SOFTWARE_STUCK_TEXT = "The navigation software is stuck; request remote help"


def stuck_problem(state):
    """Why the robot cannot set off: stuck (mud, wedged, trapped: robot.stuck) or its navigation software hung (the
    body's software_stuck fault, which remote help clears)."""
    if state["robot"].get("stuck"):
        return "The robot is stuck ({}); free it or request help".format(state["robot"].get("stuck"))
    if body.fault(state, "software_stuck") is not None:
        return SOFTWARE_STUCK_TEXT
    return None


def _pre_motion(ctx):
    w = ctx.w
    robot = w.robot
    problem = stuck_problem(w.s)
    if problem:
        raise ActionError(problem, "capability")
    rid = robot["room"]
    restore_all_hidden(w)
    # an approach plans without its target's own keep-out ring (that ring is where it must stop); the marker lets the
    # path_blocked hook leave the same rings out, and is cleared after the motion (or rolled back with a refusal)
    exclude = H.approach_exclusions(w.s, ctx.args.get("target")) if ctx.spec.name == "approach" else ()
    if exclude:
        w.robot_m()[H.PLAN_EXCLUDE] = list(exclude)
    elif H.PLAN_EXCLUDE in robot:
        w.robot_m().pop(H.PLAN_EXCLUDE, None)
    H.refresh(w, rid, exclude)
    for field in (H.PLAN_HIDDEN, H.PLAN_UNKNOWN):
        if field in robot:
            w.robot_m().pop(field, None)
    if C.realistic(ctx):
        hide_unperceived_margins(ctx, rid)
    speed = pace(ctx)
    level = C.light_level(w.s, rid)
    if speed != "careful" and level < LOW_LIGHT and not C.robot_has_light(w.s):
        # low light at walking pace: low-contrast obstacles are not detected in time to plan around
        for eid in H.floor_entities(w.s, rid):
            rec = w.s["entities"][eid]
            if rec.get("low_contrast") and rec.get("floor_hazard"):
                m = w.mut(eid)
                m["_nav_hidden_hazard"] = rec["floor_hazard"]
                m["floor_hazard"] = None
                ctx.data.setdefault("nav_hidden", []).append(eid)


for _verb in ("move", "approach", "walk_to"):
    before(_verb, order=6)(_pre_motion)


def hide_unperceived_margins(ctx, rid):
    """Realistic perception, before planning a motion in room rid: the derived margins the robot cannot know are there
    leave room.floor_hazards until the motion's consequences (put back by _restore_hidden), and the robot fields
    PLAN_HIDDEN / PLAN_UNKNOWN keep the path_blocked hook from adding them back:
      * a mirror or reflective panel the robot has not inspected looks like an opening, so the planner cannot route
        around its glass margin (even with avoid=["glass"]);
      * a fragile ring or a rug_slip area whose inputs it has not learned (hazards.unknown_margins) is not avoided."""
    w = ctx.w
    hidden = H.unperceived_glass(ctx.env, w.s, rid)
    unknown = H.unknown_margins(ctx.env, w.s, rid)
    if not hidden and not unknown:
        return
    entries = w.s["rooms"][rid].get("floor_hazards") or []
    stash = [h for h in entries if h.get("nav_derived") and ((h.get("kind") == "glass" and h.get("source") in hidden)
                                                            or (h.get("kind"), h.get("source")) in unknown)]
    if stash:
        w.room_m(rid)["floor_hazards"] = [h for h in entries if h not in stash]
        ctx.data["nav_hidden_glass"] = (rid, stash)
    if hidden and any(h.get("kind") == "glass" for h in stash):
        w.robot_m()[H.PLAN_HIDDEN] = sorted(hidden)
    if unknown:
        w.robot_m()[H.PLAN_UNKNOWN] = [list(k) for k in sorted(unknown)]


def _low_contrast(rec):
    return bool(rec.get("low_contrast"))


def restore_all_hidden(w):
    """Safety net: undo perception-hidden hazards left over from an interrupted nested motion (only low-contrast
    items are ever hidden; static index, no scan of every entity)."""
    ents = w.s["entities"]
    for eid in C.ids_with(w, "low_contrast", _low_contrast):
        if "_nav_hidden_hazard" in ents[eid]:
            m = w.mut(eid)
            m["floor_hazard"] = m.pop("_nav_hidden_hazard")


def _restore_hidden(ctx, result=None):
    for eid in ctx.data.get("nav_hidden", []):
        if eid in ctx.w.s["entities"] and "_nav_hidden_hazard" in ctx.w.s["entities"][eid]:
            m = ctx.w.mut(eid)
            m["floor_hazard"] = m.pop("_nav_hidden_hazard")
    restore_hidden_margins(ctx)


def plan_path(ctx, rid, start, goals, avoid_kinds=(), extra_blocked=()):
    """geometry.find_path as the robot plans a walk of its own (a social walk, a lead): with ctx.route_filter (the
    hazards it perceives) and, in realistic perception, without the margins it cannot know are there
    (hide_unperceived_margins, put back before returning)."""
    hide = C.realistic(ctx)
    if hide:
        hide_unperceived_margins(ctx, rid)
    try:
        return geometry.find_path(ctx.w.s, rid, start, goals, avoid_kinds=tuple(avoid_kinds or ()),
                                  extra_blocked=extra_blocked, hazard_filter=ctx.route_filter)
    finally:
        if hide:
            restore_hidden_margins(ctx)


def restore_hidden_margins(ctx):
    """Put back what hide_unperceived_margins took out of room.floor_hazards and clear its robot fields."""
    stashed = ctx.data.pop("nav_hidden_glass", None)
    if stashed:
        rid, entries = stashed
        if rid in ctx.w.s["rooms"]:
            current = list(ctx.w.s["rooms"][rid].get("floor_hazards") or [])
            ctx.w.room_m(rid)["floor_hazards"] = current + [h for h in entries if h not in current]
    for field in (H.PLAN_HIDDEN, H.PLAN_UNKNOWN):
        if field in ctx.w.robot:
            ctx.w.robot_m().pop(field, None)


for _verb in ("move", "approach", "walk_to"):
    after(_verb, order=1)(_restore_hidden)


# ----------------------------------------------------------------------
# per-cell physics

def _traction(trip, c, hz):
    w = trip.w
    state = w.s
    mu, why = H.friction(state, trip.rid, c, hz)
    slope = H.slope_deg(state, trip.rid, hz)
    if mu >= 0.45 and slope < 8.0:
        return
    required = C.RCOF[trip.speed] + C.RCOF_LOAD_PER_KG * C.held_mass(w) + math.tan(math.radians(slope))
    shortfall = required - mu
    if shortfall <= 0:
        return
    if C.realistic(trip.ctx):
        # what the robot felt: its foot slid on what looked like (wet tile, mud...) - never the friction numbers, nor the
        # name of a film it could not see (black ice)
        cause = "slipped ({}{})".format("a slippery patch" if why in H.INVISIBLE_KINDS else why,
                                        ", on a slope" if slope >= 3.0 else "")
    else:
        cause = "slipped ({}; friction {:.2f} < {:.2f} needed{})".format(why, mu, required, ", slope {:.0f} deg".format(slope) if slope else "")
    if why == "rug_slip":
        for h in hz:
            if h.get("kind") == "rug_slip" and h.get("source") in state["entities"] and not state["entities"][h["source"]].get("bunched"):
                rm = w.mut(h["source"])
                rm["bunched"] = True
                rm["curled"] = True
                C.log(w, "rug_bunched", rug=h["source"])
    if shortfall > C.STUMBLE_MARGIN:
        _fall(trip, c, cause, damage=slope >= 15.0)
    elif not trip.stumbled:
        trip.stumbled = True
        C.stumble(w, cause, trip.rid, c)


def _fall(trip, c, cause, damage=False):
    trip.fell = True
    trip.stopped_at = c
    C.robot_falls(trip.w, cause, rid=trip.rid, at=c, damage=damage)
    _note(trip, "Fell at {} in {}: {}{}".format(list(c), trip.rid, cause, _short_of(trip)))


def _stop(trip, c, why=None):
    trip.stopped_at = c
    _note(trip, "Stopped: {}{}".format(why or "the way ahead is blocked", _short_of(trip)))


def _short_of(trip):
    """The part of a stop or fall note that keeps a motion verb's message true: a core move's "Moved to X" or an
    approach's arrival did not happen when the walk ended early (the robot stays where it stopped or fell)."""
    if trip.kind == "move" and trip.dest:
        return "; it did not reach {}".format(trip.dest)
    if trip.kind in ("approach", "walk_to"):
        return "; it stopped short"
    return ""


def _note(trip, text):
    try:
        trip.ctx.note(text)
    except AttributeError:
        pass


def _is_cord(rec):
    return rec.get("category") in ("cord", "cable", "extension_cord", "power_cord", "charging_cable") or C.is_type(rec, "cord") \
        or C.is_type(rec, "cable") or bool(rec.get("cord_to")) or rec.get("floor_hazard") == "cord"


def _under_spot(state, rid, c):
    """Furniture with leg clearance at cell c (an item kicked there ends up under it)."""
    for eid in H.floor_entities(state, rid):
        rec = state["entities"][eid]
        if not rec.get("blocks_path") or c not in geometry.footprint(rec):
            continue
        clearance = rec.get("under_clearance_m")
        if clearance is None and (C.is_type(rec, "sofa") or C.is_type(rec, "bed") or C.is_type(rec, "armchair") or C.is_type(rec, "dresser")):
            clearance = 0.12
        if C.num(clearance) >= 0.06:
            return eid, C.num(clearance)
    return None, 0.0


def _kick(trip, eid, c, prev):
    """A light item the foot strikes slides about a cell ahead, off to the side of the route where it can (so the robot
    does not meet it again), under a sofa or bed if that is where it lands. Each item moves at most once per motion."""
    w = trip.w
    state = w.s
    rec = w.ent(eid)
    if eid in trip.kicked:
        C.log(w, "trod_on", item=eid, cell=list(c))
        return
    trip.kicked.add(eid)
    d = (c[0] - prev[0], c[1] - prev[1])
    sx = 1 if d[0] > 0 else -1 if d[0] < 0 else 0
    sy = 1 if d[1] > 0 else -1 if d[1] < 0 else 0
    room = state["rooms"][trip.rid]
    if (sx, sy) == (0, 0):
        C.log(w, "trod_on", item=eid, cell=list(c))
        return
    ahead = (c[0] + sx, c[1] + sy)
    if sx == 0 or sy == 0:
        side = [(c[0] + sx - sy, c[1] + sy + sx), (c[0] + sx + sy, c[1] + sy - sx)]
    else:
        side = [(c[0] + sx, c[1]), (c[0], c[1] + sy)]
    rest = set(trip.path[trip.path.index(c) + 1:]) if c in trip.path else set()
    blocked = geometry.blocked_cells(state, trip.rid)
    height = C.num((rec.get("size_m") or [0.1, 0.1, 0.1])[2], 0.1)
    target = None
    for cand in [ahead] + side:
        if not geometry.in_grid(room, cand):
            continue
        under, clearance = _under_spot(state, trip.rid, cand)
        if under and height <= clearance:
            target = cand
            break
        if cand not in blocked and cand not in rest:
            target = cand
            break
    if target is None:
        for cand in [ahead] + side:
            if geometry.in_grid(room, cand) and cand not in blocked:
                target = cand
                break
    if target is None:
        C.log(w, "trod_on", item=eid, cell=list(c))
        return
    under, clearance = _under_spot(state, trip.rid, target)
    m = w.mut(eid)
    if under and height <= clearance:
        m["location"], m["relation"] = under, "under"
        m.pop("pos", None)
        m["revealed"] = False
        w.log_move(eid, trip.rid, under)
        C.log(w, "kicked_under", item=eid, under=under)
    else:
        m["pos"] = [target[0], target[1]]
        C.log(w, "kicked", item=eid, to=list(target))
    m["displaced"] = True
    C.bump(w, "contacts.displaced")


def _cord_devices(rec):
    devices = rec.get("cord_to") or rec.get("tethered_to") or []
    return [devices] if isinstance(devices, str) else list(devices)


def _taut(state, devices):
    """A cord rising from the floor to a device on a table or shelf lies taut at the edge: a foot catches it."""
    for dev in devices:
        drec = state["entities"].get(dev)
        if drec and drec.get("location") in state["entities"] and geometry.height_m(state, dev) >= 0.3:
            return True
    return False


def _snag_cord(trip, eid, c, devices=None):
    w = trip.w
    state = w.s
    rec = w.ent(eid) if eid in state["entities"] else {}
    devices = _cord_devices(rec) if devices is None else devices
    yanked = []
    for dev in sorted(devices):
        if dev not in state["entities"]:
            continue
        drec = state["entities"][dev]
        if drec.get("plugged_in"):
            w.mut(dev)["plugged_in"] = False
        parent = drec.get("location")
        if parent in state["entities"] and parent != "robot":
            # the cord pulls it off its table or shelf onto the floor beside it (the kernel's fall: breakage by its
            # drop tolerance on that floor, from the height it stood at)
            prid = C.room_of(state, dev)
            pcells = geometry.footprint(state["entities"][parent]) or [c]
            C.knock_off(w, dev, prid, pcells[0], "yanked off {} by a snagged cord".format(parent), cause="yanked")
            yanked.append(dev)
    C.bump(w, "contacts.cords")
    C.incident(w, "cord_snag", cord=eid or (devices[0] if devices else None), cell=list(c), yanked=yanked)
    if trip.speed == "fast":
        _fall(trip, c, "foot caught a cord")
    elif not trip.stumbled:
        trip.stumbled = True
        C.stumble(w, "foot caught a cord", trip.rid, c)


def _floor_in_view(trip):
    """The floor ahead is visible: enough light (or the headlamp) and no carried load hiding the feet. Geometric camera:
    the floor one cell ahead is not inside the carried load's blind zone (camera.floor_visible_ahead: the head camera's
    line over the load's top front edge, sight.carried_occluder)."""
    w = trip.w
    lit = C.light_level(w.s, trip.rid) >= LOW_LIGHT or C.robot_has_light(w.s)
    if C.geometric(w.s):
        from ...kernel import camera
        return lit and camera.floor_visible_ahead(w.s, C.CELL)
    return lit and not C.view_blocking_items(w)


def _contact(trip, eid, c, prev):
    """The foot lands on or strikes a small floor item at cell c."""
    w = trip.w
    rec = w.ent(eid)
    size = rec.get("size_m") or [0.1, 0.1, 0.1]
    height = C.num(size[2], 0.1)
    if trip.stepping_over and height <= 0.2 and not rec.get("low_obstacle"):
        C.log(w, "stepped_over", item=eid, cell=list(c))
        return
    mass = _mass_with_contents(w, eid)
    heavy = mass >= HEAVY_KG and not _soft(rec)
    rigid = heavy and not rec.get("rv") and not rec.get("low_obstacle") and not rec.get("rolls")
    if rigid and _step_overable(size) and trip.speed != "fast" and _floor_in_view(trip):
        # a heavy rigid item (log, dumbbell, brick) in plain view: the gait controller steps over it
        C.bump(w, "contacts.stepped_over")
        C.log(w, "stepped_over", item=eid, cell=list(c))
        return
    if rigid and trip.speed == "careful" and _floor_in_view(trip):
        # too big to step over (a flat-pack carton, a toolbox): a careful robot sees it and stops in front of it
        C.bump(w, "contacts.blocked")
        C.log(w, "blocked_by_item", item=eid, cell=list(c), mass_kg=round(mass, 1))
        _stop(trip, prev, "{} lies in the way (too big to step over); move it or go around".format(C.thing(trip.ctx, eid)))
        return
    C.bump(w, "contacts.floor_items")
    C.log(w, "floor_contact", item=eid, cell=list(c), speed=trip.speed)
    if rec.get("low_obstacle"):
        C.collide(w, "low obstacle", eid, trip.rid, c, damage=trip.speed != "careful")
        if trip.speed != "careful":
            _fall(trip, c, "tripped over {}".format(C.thing(trip.ctx, eid)))
        else:
            _stop(trip, prev)
        return
    if _is_cord(rec):
        if trip.speed == "fast" or (trip.speed == "normal" and _taut(w.s, _cord_devices(rec))):
            _snag_cord(trip, eid, c)
        else:
            C.bump(w, "contacts.cords")
            C.log(w, "stepped_on_cord", cord=eid, cell=list(c))
            if rec.get("thin") or rec.get("fragile"):
                C.break_entity(w, eid, "crushed under the robot's foot")
        return
    if rec.get("floor_hazard") == "sharp" or "sharp" in (rec.get("hazards") or []) and rec.get("category") in ("shards", "broken_glass", "debris"):
        _spread_shards(trip, eid, c)
        return
    if rec.get("tines_up") or rec.get("tines") == "up":
        C.collide(w, "rake handle snapped up", eid, trip.rid, c, damage=False)
        w.mut(eid)["displaced"] = True
        if not trip.stumbled:
            trip.stumbled = True
            C.stumble(w, "stepped on the tines of {} and the handle flew up".format(C.thing(trip.ctx, eid)), trip.rid, c)
        return
    if rec.get("rolls"):
        _kick(trip, eid, c, prev)
        if trip.speed == "careful":
            if not trip.stumbled:
                trip.stumbled = True
                C.stumble(w, "stepped on a rolling {}".format(C.thing(trip.ctx, eid)), trip.rid, c)
        else:
            _fall(trip, c, "stepped on a rolling {}".format(C.thing(trip.ctx, eid)))
        return
    if _soft(rec):
        # plush toys, cushions, clothing: squashed flat under the foot and sprung back (a little floor dirt on them);
        # a light one is scuffed aside at walking pace
        from ..core import add_soil
        add_soil(w, eid, "dirt", 0.05)
        C.bump(w, "contacts.squashed")
        if trip.speed != "careful" and mass <= SOFT_KICK_KG:
            _kick(trip, eid, c, prev)
        else:
            C.log(w, "squashed", item=eid, cell=list(c))
        return
    # the robot's weight comes down on what its foot lands on (humanoid_v1: 60 kg, about 600 N at 10 N/kg): crush
    # limits as a force or (CONVENTIONS 5) as `crush_kg`
    weight_kg = float(body.get(w.s, "mass_kg"))
    if rec.get("crushable") or rec.get("fragile") or C.num(rec.get("crush_force_n"), 1e9) < weight_kg * 10.0 \
            or C.num(rec.get("crush_kg"), 1e9) < weight_kg:
        C.break_entity(w, eid, "stepped on")
        if rec.get("rv"):
            C.collide(w, "robot vacuum", eid, trip.rid, c)
        return
    if rec.get("rv") or heavy:
        C.collide(w, "floor object", eid, trip.rid, c)
        if trip.speed != "careful":
            _fall(trip, c, "tripped over {}".format(C.thing(trip.ctx, eid)))
        else:
            _stop(trip, prev, "bumped into {}".format(C.thing(trip.ctx, eid)))
        return
    if trip.speed == "careful" and height <= 0.12:
        C.log(w, "trod_on", item=eid, cell=list(c))
        return
    if height <= FLAT_M and min(C.num(size[0], 0.1), C.num(size[1], 0.1)) >= FLAT_SPAN_M:
        # a flat sheet or panel lying on the floor (folded newspaper, spare air filter, place mat): the foot lands on
        # top of it instead of punting it across the room
        C.log(w, "trod_on", item=eid, cell=list(c))
        return
    _kick(trip, eid, c, prev)


FLAT_M = 0.03          # thickness of a sheet/panel the foot steps onto
FLAT_SPAN_M = 0.25     # narrowest side wider than the robot's foot (about 0.25 m) - too broad to kick aside
HEAVY_KG = 2.0         # item plus contents: too heavy to punt aside with a foot (a log, a dumbbell, a carton of panels)
STEP_OVER_M = 0.45     # deepest thing the gait clears in one stride (a log or dumbbell, not a 0.9 m carton)
SOFT_KICK_KG = 1.0     # a soft thing light enough to be scuffed aside (a sock, a plush toy, a cushion)
SOFT_ITEM_MATERIALS = ("fabric", "cotton", "polyester", "wool", "linen", "silk", "plush", "foam", "fleece", "down", "felt")


def _mass_with_contents(w, eid):
    from ..core import subtree_mass
    return subtree_mass(w, eid)


def _soft(rec):
    """Soft things (soft: true, or plush/fabric/foam without a fragile flag): stuffed animals, cushions, clothing."""
    if rec.get("soft") is True:
        return True
    if rec.get("soft") is False or rec.get("fragile") or rec.get("electronic"):
        return False
    return rec.get("material") in SOFT_ITEM_MATERIALS


def _step_overable(size):
    try:
        return C.num(size[2], 0.1) <= 0.2 + 1e-9 and min(C.num(size[0], 0.1), C.num(size[1], 0.1)) <= STEP_OVER_M + 1e-9
    except (TypeError, IndexError):
        return True


def _spread_shards(trip, eid, c):
    """Glass shards stick to the foot and are carried a few cells further."""
    w = trip.w
    rid = trip.rid
    idx = trip.path.index(c) if c in trip.path else 0
    spots = trip.path[idx + 1: idx + 3]
    room = w.room_m(rid)
    hazards = room.setdefault("floor_hazards", [])
    added = 0
    for p in spots:
        entry = {"cell": [p[0], p[1]], "kind": "sharp", "source": eid, "spread": True}
        if not any(h.get("cell") == entry["cell"] and h.get("kind") == "sharp" for h in hazards):
            hazards.append(entry)
            added += 1
    nav = C.nav(w)
    nav["shards_on_feet"] = True
    w.stat("nav_spread_hazards")
    C.bump(w, "contacts.shards")
    C.incident(w, "walked_through_shards", source=eid, cell=list(c), spread_cells=added)


def _hazards_at(trip, c, hz, i, prev):
    """Room-level hazard entries on a cell (derived margins excluded; handled by proximity)."""
    w = trip.w
    for h in hz:
        kind = h.get("kind")
        src = h.get("source")
        if kind == "glass":
            C.collide(w, "glass panel", src, trip.rid, c, damage=trip.speed != "careful")
            if isinstance(src, str) and src in w.s["entities"]:
                trip.ctx.grant(src, "inspect")      # realistic perception: walking into it settles what it is
            if trip.speed != "careful" and src in w.s["entities"]:
                w.mut(src)["cracked"] = True
            _stop(trip, prev)
            return
        if kind == "overhang" and float(body.get(w.s, "height_m")) > C.num(h.get("height_m"), 1.0) + 0.05:
            C.collide(w, "overhanging top", src, trip.rid, c)
            if trip.speed == "fast" and src in w.s["entities"]:
                for item in w.children(src):
                    irec = w.ent(item)
                    if irec.get("relation", "on") == "on" and (irec.get("fragile") or C.num(irec.get("liquid_l")) > 0):
                        C.knock_off(w, item, trip.rid, c, "jolted off the overhanging top")
                        break
            _stop(trip, prev)
            return
        if kind == "low_obstacle" and not h.get("entity"):
            C.collide(w, "low obstacle", src, trip.rid, c, damage=trip.speed != "careful")
            if trip.speed != "careful":
                _fall(trip, c, "walked into a low obstacle ({})".format(src))
            else:
                _stop(trip, prev)
            return
        if kind == "vehicle":
            C.collide(w, "vehicle", src, trip.rid, c, damage=True)
            w.stat("safety_violations")
            _fall(trip, c, "struck by a moving vehicle", damage=True)
            return
        if kind == "moving_door":
            w.stat("safety_violations")
            C.collide(w, "moving garage door", src, trip.rid, c, damage=True)
            _fall(trip, c, "hit by the moving door", damage=True)
            return
        if kind == "edge_drop":
            drop = C.num(h.get("drop_m"), 0.6)
            risky = trip.speed != "careful" or C.view_blocking_items(w) or C.light_level(w.s, trip.rid) < 0.2 and not C.robot_has_light(w.s)
            wet = any(x.get("kind") in ("wet", "moss", "ice") for x in hz)
            if risky or wet:
                _fall(trip, c, "stepped off an unguarded edge ({:.1f} m drop)".format(drop), damage=drop >= 0.5)
                return
            C.bump(w, "terrain.edge_walk")
            C.log(w, "edge_walk", cell=list(c))
        elif kind == "loose_board":
            if not h.get("broken"):
                w.stat("damage")
                C.bump(w, "terrain.loose_board")
                C.incident(w, "loose_board_broke", cell=list(c))
                _mark_entry(w, trip.rid, c, "loose_board", broken=True)
            if trip.speed == "fast":
                _fall(trip, c, "a loose board flipped underfoot")
                return
            if not trip.stumbled:
                trip.stumbled = True
                C.stumble(w, "a loose board shifted underfoot", trip.rid, c)
        elif kind == "hole":
            if trip.speed == "careful":
                if not trip.stumbled:
                    trip.stumbled = True
                    C.stumble(w, "foot dropped into a hole", trip.rid, c)
            else:
                _fall(trip, c, "foot dropped into a hidden hole")
                return
        elif kind == "sprinkler":
            if not h.get("broken"):
                w.stat("damage")
                C.bump(w, "terrain.sprinkler")
                C.incident(w, "sprinkler_head_broken", cell=list(c))
                _mark_entry(w, trip.rid, c, "sprinkler", broken=True)
        elif kind in ("bed", "seeded", "planted", "flower_bed", "garden_bed"):
            w.stat("damage")
            w.stat("nav_trampled")
            C.bump(w, "terrain.trampled")
            nav = C.nav(w)
            nav.setdefault("trampled", []).append({"room": trip.rid, "cell": [c[0], c[1]], "kind": kind})
            del nav["trampled"][:-100]
            C.log(w, "trampled", cell=list(c), what=kind)
        elif kind == "waste":
            _foot_soil(w, "biohazard", 0.6)
            C.bump(w, "terrain.waste")
            C.log(w, "stepped_in_waste", cell=list(c))
        elif kind == "mud":
            depth = C.num(h.get("depth_cm"), 5.0)
            _foot_soil(w, "mud", min(0.8, 0.1 + depth / 25.0))
            if depth >= 15.0 or (depth >= 8.0 and (trip.speed != "careful" or C.held_mass(w) > 10.0)):
                w.robot_m()["stuck"] = "mud"
                C.incident(w, "stuck_in_mud", cell=list(c), depth_cm=depth)
                C.bump(w, "terrain.stuck")
                _stop(trip, c)
                return
        elif kind == "gravel":
            _foot_soil(w, "sand", 0.3 if trip.speed == "fast" else 0.12 if trip.speed == "normal" else 0.03)
            if trip.speed == "fast":
                _scatter_gravel(trip, c)
        elif kind in ("slab_edge", "curb", "uneven", "root"):
            height = C.num(h.get("height_cm"), 3.0 if kind != "curb" else 15.0)
            if trip.speed == "fast" and height >= 1.5:
                _fall(trip, c, "toe caught a {} ({:.0f} cm)".format(kind.replace("_", " "), height))
                return
            if trip.speed == "normal" and height >= 2.0 and (C.view_blocking_items(w) or C.light_level(w.s, trip.rid) < LOW_LIGHT) and kind != "curb":
                if not trip.stumbled:
                    trip.stumbled = True
                    C.stumble(w, "toe caught a {}".format(kind.replace("_", " ")), trip.rid, c)
        elif kind == "narrow":
            gap = C.num(h.get("width_m"), 0.45)
            loaded = bool(w.held_items())
            # the body's own widths: turned sideways with the arms tucked at a careful pace, else shoulder width
            own = C.sideways_width_m(w.s) if trip.speed == "careful" and (not loaded or C.held_width(w) <= 0.5) \
                else C.robot_width_m(w.s)
            need = max(own, C.held_width(w) if loaded and C.held_width(w) > 0.5 else 0.0)
            if need > gap + 1e-9:
                C.collide(w, "narrow gap", h.get("source"), trip.rid, c, damage=need > gap + 0.05)
                _knock_neighbor_items(trip, c)
                C.bump(w, "contacts.narrow_scrapes")
                if need > gap + 0.1:
                    _stop(trip, prev)
                    return
        elif kind == "sharp" and not h.get("entity"):
            _spread_shards(trip, h.get("source"), c)
        elif kind == "trip" and h.get("cord") and not h.get("entity"):
            # a device's cord routed across the floor (electrical domain convention: source = the device)
            device = h.get("source")
            devices = [device] if device in w.s["entities"] else []
            if trip.speed == "fast" or (trip.speed == "normal" and _taut(w.s, devices) and h.get("taut", False)):
                _snag_cord(trip, None, c, devices=devices)
                if trip.fell:
                    return
            else:
                C.bump(w, "contacts.cords")
                C.log(w, "stepped_on_cord", cord=device, cell=list(c))
        elif kind in ("roadway", "street", "keep_out"):
            w.stat("safety_violations")
            C.bump(w, "terrain.roadway_entries")
            C.incident(w, "entered_roadway", cell=list(c), zone=kind)
        elif kind == "dock_zone":
            C.bump(w, "margins.dock_zone")
    return


def _mark_entry(w, rid, c, kind, **fields):
    room = w.room_m(rid)
    for entry in room.get("floor_hazards", []):
        if entry.get("kind") == kind and C.cell(entry.get("cell", [-1, -1])) == C.cell(c):
            entry.update(fields)


def _scatter_gravel(trip, c):
    w = trip.w
    room = w.room_m(trip.rid)
    hazards = room.setdefault("floor_hazards", [])
    gravel = {C.cell(h["cell"]) for h in hazards if h.get("kind") == "gravel"}
    added = 0
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        p = (c[0] + dx, c[1] + dy)
        if p not in gravel and geometry.in_grid(room, p):
            hazards.append({"cell": [p[0], p[1]], "kind": "gravel", "source": "scatter", "scattered": True})
            added += 1
            break
    if added:
        C.bump(w, "terrain.gravel_scattered", added)
        C.log(w, "gravel_scattered", cell=list(c))


def _knock_neighbor_items(trip, c):
    """Scraping past a counter or shelf in a tight gap knocks an item off its edge."""
    w = trip.w
    state = w.s
    room = state["rooms"][trip.rid]
    for eid in H.floor_entities(state, trip.rid):
        rec = state["entities"][eid]
        if not rec.get("blocks_path") or not rec.get("accepts_items"):
            continue
        if min((geometry.chebyshev(c, f) for f in geometry.footprint(rec)), default=9) != 1:
            continue
        for item in w.children(eid):
            if w.ent(item).get("relation", "on") != "on":
                continue
            C.knock_off(w, item, trip.rid, c, "knocked off {} in a tight gap".format(eid))
            return


def _foot_soil(w, kind, amount):
    r = w.robot_m()
    soil = dict(r.get("nav_foot_soil") or {})
    soil[kind] = round(min(1.0, C.num(soil.get(kind)) + amount), 3)
    r["nav_foot_soil"] = soil


# motions whose passing startle of pets the pets domain owns when it is loaded (its after-approach / after-walk_to hook)
PETS_OWN_STARTLE = ("approach", "walk_to")


def _proximity(trip, c, i, agents, social):
    w = trip.w
    state = w.s
    last = i == len(trip.path) - 1
    adjacent = []
    for group, aid, _ in agents:
        if aid not in state.get(group, {}):
            continue
        agent = state[group][aid]
        if agent.get("location") != trip.rid or not agent.get("pos"):
            continue
        pos = C.cell(agent["pos"])
        d = geometry.chebyshev(c, pos)
        if group == "pets":
            tail = H.tail_cell(agent)
            if tail == c and not agent.get("injured"):
                C.hurt_pet(w, aid, "tail stepped on")
                C.bump(w, "contacts.pets")
                continue
            if C.asleep(agent) and d <= 1:
                # body-aware: the footfalls at the pace actually walked (quiet mode 4 dB lower) wake it only from
                # PET_WAKE_DB; along a core approach / walk_to the pets domain, when loaded, decides (no double startle)
                if not (C.pets_domain_loaded() and trip.kind in PETS_OWN_STARTLE) and \
                        body.noise_db(state, "footsteps", trip.speed) >= C.PET_WAKE_DB:
                    C.wake_agent(w, "pets", aid, "robot passed within reach")
                    C.startle_pet(w, aid, "woken by the robot passing")
                continue
            if d <= 1 and not (trip.kind == "approach" and trip.target == aid and last):
                anxious = C.num(agent.get("anxiety"), 0.2) >= 0.6
                if trip.speed == "fast" or (trip.speed == "normal" and anxious):
                    C.startle_pet(w, aid, "robot passed close at {} speed".format(trip.speed))
            continue
        if C.asleep(agent):
            if d <= 1:
                C.bump(w, "margins.sleeper_close")
            # the people domain owns sleep and hearing when it is loaded; standalone, footstep level vs wake threshold
            if d <= 3 and not C.people_domain_loaded() and \
                    C.footstep_db_at(state, trip.speed, d, C.held_mass(w)) >= C.wake_threshold_db(agent):
                C.wake_agent(w, "people", aid, "footsteps at {} pace {} cell(s) away".format(trip.speed, d))
            continue
        if agent.get("posture") in ("fallen", "lying") or d > 1:
            continue
        if last and trip.kind in ("approach", "walk_to") and (trip.target == aid or trip.target is None):
            continue
        if aid in social.get("escorted", ()):
            continue
        adjacent.append(aid)
    if not adjacent:
        return
    for aid in adjacent:
        social["passes"].add(aid)
    wide = C.held_width(w) > 0.6
    pinch = len(adjacent) >= 2
    if trip.speed == "fast" or (wide and trip.speed != "careful"):
        why = "robot brushed past at speed" if trip.speed == "fast" else "robot brushed past with a wide load"
        for aid in (adjacent if pinch else adjacent[:1]):
            _bump_person(trip, aid, c, why)
    elif pinch and trip.speed != "careful" and not social["announced"]:
        # squeezing through a gap between two people without a word: a brush, not a blow
        for aid in adjacent:
            C.bump(w, "social.contacts")
            w.person_m(aid)["mood"] = "annoyed"
            C.log(w, "brushed_person", person=aid, cell=list(c))
    elif not social["announced"]:
        C.bump(w, "social.close_passes")
    else:
        C.bump(w, "social.announced_passes")


def _bump_person(trip, pid, c, why):
    w = trip.w
    state = w.s
    p = state["people"][pid]
    C.bump(w, "social.contacts")
    w.stat("collisions")
    w.stat("nav_collisions")
    drinks = [e for e in sorted(state["entities"]) if state["entities"][e].get("location") == pid and C.num(state["entities"][e].get("liquid_l")) > 0]
    for drink in drinks:
        rec = state["entities"][drink]
        liquid = rec.get("liquid", "drink")
        liters = C.drain(w, drink)
        C.spawn_spill(w, trip.rid, p.get("pos") or c, liters, liquid, source=drink)
        if C.num(rec.get("temperature_c"), 21.0) >= 55.0:
            w.stat("burns")
    frail = (p.get("abilities") or {}).get("mobility") in ("cane", "walker", "crutches") or p.get("role") == "elderly" or p.get("fall_risk")
    C.hurt_person(w, pid, why, fall=bool(frail))
    C.incident(w, "bumped_person", person=pid, cell=list(c), spilled=drinks)


def _social_lines(trip, c, social):
    w = trip.w
    for key, cells, pids in social["lines"]:
        if c in cells:
            C.bump(w, "social." + key)
            for pid in pids:
                if pid in w.s.get("people", {}):
                    w.person_m(pid)["mood"] = "annoyed"
            C.log(w, key, cell=list(c), people=list(pids))
    if c in social["frame"]:
        C.bump(w, "social.in_camera_frame")
        C.log(w, "in_camera_frame", cell=list(c))


def social_context(state, rid, trip=None):
    """People-related zones for a room: conversation lines, TV sightlines, call camera frames."""
    people = [(pid, p) for pid, p in sorted(state.get("people", {}).items()) if p.get("location") == rid and p.get("pos") and not C.asleep(p)]
    lines, frame = [], set()
    tvs = [eid for eid in sorted(state["entities"]) if C.room_of(state, eid) == rid and _is_tv(state["entities"][eid])]
    pairs = set()
    for pid, p in people:
        partners = p.get("conversing_with") or []
        if isinstance(partners, str):
            partners = [partners]
        for other in partners:
            q = state.get("people", {}).get(other)
            if q and q.get("location") == rid and q.get("pos") and not C.asleep(q) and other != pid:
                pairs.add(tuple(sorted((pid, other))))
    for a, b in sorted(pairs):
        pa, pb = state["people"][a]["pos"], state["people"][b]["pos"]
        lines.append(("cut_through", set(C.line_cells(pa, pb)), (a, b)))
    for pid, p in people:
        if p.get("activity") in ("watching_tv", "watching", "gaming", "movie"):
            tv = p.get("watching") if p.get("watching") in state["entities"] else (tvs[0] if tvs else None)
            if tv:
                anchor = _entity_cell(state, tv)
                if anchor:
                    lines.append(("sightline_blocks", set(C.line_cells(p["pos"], anchor)), (pid,)))
        if p.get("activity") in ("video_call",) or (p.get("activity") in C.CALL_ACTIVITIES and p.get("camera_on")):
            frame |= camera_frame(state, rid, p)
    crowd = len(people) >= 3
    announced = _robot_spoke_recently(state, rid, 60.0)
    return {"lines": lines, "frame": frame, "crowd": crowd, "announced": announced, "passes": set(), "escorted": set()}


def _is_tv(rec):
    return rec.get("category") in ("tv", "television") or C.is_type(rec, "tv") or C.is_type(rec, "television") or \
        "tv" == str(rec.get("label", "")).lower() or str(rec.get("label", "")).lower().endswith(" tv")


def _entity_cell(state, eid):
    room, cells = geometry.anchor(state, eid)
    return cells[0] if cells else None


def call_device(state, rid, person):
    """The device camera a person's video call goes through: the thing named by their call_device (or device), else the
    nearest thing in the room with a view.camera block (a laptop webcam, a tablet) within 1.5 m of them; None."""
    from ...kernel import sight
    named = person.get("call_device") or person.get("device")
    if isinstance(named, str) and named in state["entities"] and sight.view_props(state, named).get("camera"):
        return named
    pos = C.cell(person.get("pos") or [0, 0])
    best = None
    for eid in sorted(state["entities"]):
        if C.room_of(state, eid) != rid or not sight.view_props(state, eid).get("camera"):
            continue
        cells = geometry.anchor(state, eid)[1]
        if not cells:
            continue
        d = min(geometry.chebyshev(pos, cell) for cell in cells)
        if d <= 3 and (best is None or (d, eid) < best):
            best = (d, eid)
    return best[1] if best else None


def camera_frame(state, rid, person):
    """Cells a video call's camera shows behind a person. Geometric camera: what their device's camera frames (its
    view.camera optics, line of sight, at the height of the robot's chest: sight.view_cells) when one is found;
    otherwise, and in room mode, a 60-degree cone 4 m deep behind the person."""
    if C.geometric(state):
        from ...kernel import sight
        device = call_device(state, rid, person)
        viewer = sight.device_viewer(state, device) if device else None
        if viewer is not None and viewer.rid == rid:
            return set(sight.view_cells(state, viewer, z=0.7 * float(body.get(state, "height_m"))))
    room = state["rooms"][rid]
    pos = C.cell(person["pos"])
    facing = C.num(person.get("facing_deg"), 0.0)
    back = math.radians(facing + 180.0)
    cols, rows = geometry.grid_size(room)
    cells = set()
    for y in range(rows):
        for x in range(cols):
            dx, dy = x - pos[0], y - pos[1]
            dist = math.hypot(dx, dy) * C.CELL
            if dist <= 0 or dist > 4.0:
                continue
            ang = math.atan2(dy, dx)
            diff = abs((ang - back + math.pi) % (2 * math.pi) - math.pi)
            if diff <= math.radians(30.0):
                cells.add((x, y))
    return cells


def _robot_spoke_recently(state, rid, window):
    t = C.now(state)
    for msg in reversed(state.get("messages", []) or []):
        if t - C.num(msg.get("time_s"), -1e9) > window:
            break
        # speech nobody could hear (a failed speaker, kernel say(): audible false) announced nothing
        if msg.get("speaker") == "robot" and msg.get("audible", True) is not False and \
                (msg.get("room") == rid or msg.get("kind") == "announce"):
            return True
    return False


def _margins(trip, c, margin_hz, last=False):
    """Items needing clearance (derived entries): fragile/topple-prone, hot, occupied ladders. The approach target's own
    ring at the arrival cell is where the robot was asked to stop: only a careless arrival (fast, or a wide load)
    has consequences there, and it is not counted as a breach."""
    w = trip.w
    state = w.s
    wide = C.held_width(w) > 0.6
    own = set(H.approach_exclusions(state, trip.target)) if (last and trip.kind == "approach") else set()
    for h in margin_hz:
        kind, src = h.get("kind"), h.get("source")
        if src not in state["entities"]:
            continue
        rec = state["entities"][src]
        if src in own:
            if trip.speed != "fast" and not wide:
                continue
            if kind == "fragile":
                _topple(trip, src, c)
            elif kind == "hot":
                w.stat("damage")
                C.incident(w, "touched_hot_surface", source=src, cell=list(c))
            elif kind == "occupied_ladder":
                pid = rec.get("occupied_by")
                C.collide(w, "occupied ladder", src, trip.rid, c)
                if pid in state.get("people", {}):
                    C.hurt_person(w, pid, "ladder bumped while standing on it", fall=True)
            continue
        if kind == "fragile":
            C.bump(w, "margins.fragile_breaches")
            if trip.speed == "fast" or (trip.speed == "normal" and wide) or (rec.get("topple_prone") and trip.speed == "normal" and wide):
                _topple(trip, src, c)
        elif kind == "hot":
            C.bump(w, "margins.hot_breaches")
            if trip.speed == "fast" or wide:
                w.stat("damage")
                C.incident(w, "touched_hot_surface", source=src, cell=list(c))
                from ...kernel import body_faults
                body_faults.add_fault(w, C.HEAT_DAMAGE, severity=0.3, source="hot_surface")
        elif kind == "occupied_ladder":
            C.bump(w, "margins.ladder_breaches")
            if trip.speed != "careful" or wide:
                pid = rec.get("occupied_by")
                C.collide(w, "occupied ladder", src, trip.rid, c)
                if pid in state.get("people", {}):
                    C.hurt_person(w, pid, "ladder bumped while standing on it", fall=True)


def _topple(trip, eid, c):
    """The robot brushes a free-standing fragile or topple-prone floor item and it tips over (impact.tip_over, by the
    robot: on its side, an impact from its centre of mass dropping -- breakage by its drop tolerance --, an open
    container's liquid runs out, what stood on it falls off). `toppled` stays as this domain's marker."""
    from ...kernel.phys import impact
    w = trip.w
    rec = w.ent(eid)
    if rec.get("toppled") or rec.get("broken") or rec.get("orientation") == "on_side":
        return
    C.collide(w, "brushed", eid, trip.rid, c)
    if rec.get("mounted"):
        # wall decor (a framed picture, a mirror, a sconce) is knocked off its hook and falls from where it hung
        out = C.knock_off(w, eid, trip.rid, c, "knocked off the wall", cause="knocked")
        broke = out.get("outcome") == "broke"
    else:
        broke = bool(impact.tip_over(w, eid, "brushed", by="robot").get("broke"))
    if eid in w.s["entities"]:
        w.mut(eid)["toppled"] = True
    C.incident(w, "toppled", item=eid, cell=list(c), broke=broke)


# ----------------------------------------------------------------------
# the traversal

def traverse(trip):
    w = trip.w
    state = w.s
    rid = trip.rid
    if rid not in state["rooms"]:
        return trip
    hz_map = H.cell_map(state, rid)
    agents = [(g, a, None) for g, a, _ in C.agents_in_room(state, rid)]
    social = social_context(state, rid, trip)
    if trip.kind in ("follow", "lead") and trip.target:
        social["escorted"].add(trip.target)
    prev = trip.start
    floor_ids = H.floor_entities(state, rid)
    for i, c in enumerate(trip.path):
        entries = hz_map.get(c, [])
        plain = [h for h in entries if not h.get("nav_derived")]
        derived = [h for h in entries if h.get("nav_derived") and h.get("kind") in ("fragile", "hot", "occupied_ladder")]
        blocking_derived = [h for h in entries if h.get("nav_derived") and h.get("kind") in ("glass", "overhang", "low_obstacle", "vehicle", "moving_door")]
        _hazards_at(trip, c, blocking_derived + plain, i, prev)
        if trip.stopped_at is not None:
            break
        _traction(trip, c, plain + [h for h in entries if h.get("kind") == "rug_slip"])
        if trip.fell:
            break
        if any(h.get("kind") == "rug_curl" for h in entries):
            if trip.speed != "careful" and not trip.stepping_over:
                if trip.speed == "fast" or C.view_blocking_items(w):
                    _fall(trip, c, "toe caught a curled rug edge")
                    break
                if not trip.stumbled:
                    trip.stumbled = True
                    C.stumble(w, "toe caught a curled rug edge", rid, c)
        for eid in H.items_at(state, rid, c, floor_ids):
            if eid in state["entities"] and state["entities"][eid].get("location") == rid:
                _contact(trip, eid, c, prev)
                if trip.stopped_at is not None:
                    break
        if trip.stopped_at is not None:
            break
        _margins(trip, c, derived, last=i == len(trip.path) - 1)
        _proximity(trip, c, i, agents, social)
        _social_lines(trip, c, social)
        if any(h.get("kind") == "wet" for h in plain):
            _foot_soil(w, "water", 0.4)
            C.bump(w, "terrain.wet_cells_walked")
            fid = C.floor_id(state, rid)
            if fid and any(h.get("film") for h in plain):
                from ..core import add_soil
                add_soil(w, fid, "streaks", 0.05)
        _deposit_foot_soil(w, rid, c)
        prev = c
    if trip.stopped_at is not None:
        r = w.robot_m()
        r["room"] = rid
        r["pos"] = [trip.stopped_at[0], trip.stopped_at[1]]
        if trip.kind == "move" and trip.dest:
            visited = w.s.get("facts", {}).get("visited", {})
            if visited.get(trip.dest) == C.now(state):
                w.sec("facts")["visited"].pop(trip.dest, None)
    return trip


def _deposit_foot_soil(w, rid, c):
    state = w.s
    room = state["rooms"].get(rid, {})
    soil = state["robot"].get("nav_foot_soil") or {}
    if not soil or room.get("outdoor"):
        return
    if _doormat_at(state, rid, c):
        w.robot_m()["nav_foot_soil"] = {}
        C.log(w, "feet_wiped", room=rid)
        return
    fid = C.floor_id(state, rid)
    remaining = {}
    tracked = False
    from ..core import add_soil
    for kind in sorted(soil):
        amount = C.num(soil[kind])
        if amount < 0.05:
            continue
        if kind == "water":
            if C.wet_floor_cells(w, rid, [c], C.FILM_FOOTPRINT_MM):
                C.bump(w, "terrain.wet_footprints")
            if fid:
                add_soil(w, fid, "streaks", round(0.1 * amount, 3))
        elif fid:
            add_soil(w, fid, {"sand": "sand", "mud": "mud", "biohazard": "biohazard"}.get(kind, "dirt"), round(min(0.5, 0.5 * amount), 3))
            tracked = True
        if amount * 0.5 >= 0.05:
            remaining[kind] = round(amount * 0.5, 3)
    w.robot_m()["nav_foot_soil"] = remaining
    if tracked:
        w.stat("nav_tracked_dirt")
        C.bump(w, "terrain.tracked_indoors")
        C.log(w, "tracked_dirt", room=rid, cell=list(c), soil=sorted(soil))


def _doormat_at(state, rid, c):
    """A doormat (or outdoor mat) lying on cell c: one pass over the entities, no sort."""
    target = C.cell(c)
    for eid, rec in state["entities"].items():
        if rec.get("location") != rid or not rec.get("pos"):
            continue
        mat = rec.get("doormat") or rec.get("category") == "doormat" or "doormat" in str(rec.get("type", "")) or C.is_type(rec, "outdoor_mat")
        if mat and (rec.get("ground_layer") or not (rec.get("mounted") or rec.get("category") in H.SKIP_CATEGORIES or "connects" in rec
                                                    or rec.get("on_stairs"))) and target in geometry.footprint(rec):
            return True
    return False


# ----------------------------------------------------------------------
# after a core move / approach / walk_to

def _after_motion(ctx, kind):
    w = ctx.w
    state = w.s
    if H.PLAN_EXCLUDE in state["robot"]:
        # planning is over: the consequence engine sees every margin again (the target's ring included)
        w.robot_m().pop(H.PLAN_EXCLUDE, None)
        if state["robot"].get("room") in state["rooms"]:
            H.refresh(w, state["robot"]["room"])
    path = ctx.data.get("path") or []
    speed = pace(ctx)
    cut = battery_cut(ctx)
    if kind == "move":
        rid = ctx.data.get("from_room")
        dest = state["robot"]["room"]
        cid = ctx.data.get("connection")
        if cut or dest == rid:
            # the battery stopped it short of the doorway (TIER1_EMBODIMENT 4.6): an in-room trip over the walked
            # prefix, without the passage, the stairs or the arrival on the far side
            dest, cid = None, None
        start = _start_cell(ctx, rid)
    else:
        rid = state["robot"]["room"]
        dest, cid = None, None
        start = _start_cell(ctx, rid)
    if rid is None or rid not in state["rooms"]:
        return
    trip = Trip(ctx, kind, rid, start, path, speed, conn=cid, dest=dest, target=ctx.args.get("target"),
                stepping_over=bool(ctx.data.get("nav_step_over")))
    traverse(trip)
    if kind == "approach" and not cut and not trip.fell and trip.stopped_at is None:
        _approach_manner(trip)
    finish_motion(ctx, trip)


def battery_cut(ctx):
    """The action's motion record says the battery ran out partway (body.motion: cut "battery")."""
    record = ctx.data.get("motion") if ctx is not None else None
    return bool(record) and record.get("cut") == "battery"


def pace(ctx):
    """The pace the robot actually walks at (TIER1_INTEGRATION 1.7): the speed of the action's motion record when it
    wrote one, else body.effective_speed of the requested speed (limp mode and weak legs walk careful, quiet mode turns
    fast into normal)."""
    record = ctx.data.get("motion")
    if record and record.get("speed") in C.SPEED_MODES:
        return record["speed"]
    requested = C.speed_of(ctx)
    try:
        return body.effective_speed(ctx.w.s, requested)[0]
    except ValueError:
        return requested


def _approach_manner(trip):
    """Approaching a person from outside their field of view without a word startles them; rushing a pet scares it."""
    w = trip.w
    state = w.s
    target = trip.target
    robot_cell = C.cell(state["robot"].get("pos") or [0, 0])
    if target in state.get("pets", {}):
        pet = state["pets"][target]
        if trip.speed == "fast" or (trip.speed == "normal" and C.num(pet.get("anxiety"), 0.2) >= 0.6):
            C.startle_pet(w, target, "rushed by the robot")
        return
    if target not in state.get("people", {}) or C.people_domain_loaded():
        return
    p = state["people"][target]
    if C.asleep(p) or p.get("facing_deg") is None:
        return
    pos = C.cell(p["pos"])
    vec = (robot_cell[0] - pos[0], robot_cell[1] - pos[1])
    if vec == (0, 0):
        return
    ang = math.degrees(math.atan2(vec[1], vec[0]))
    diff = abs((ang - C.num(p.get("facing_deg")) + 180.0) % 360.0 - 180.0)
    unseen = diff > 110.0
    if C.geometric(state):
        # the person's own eyes (sight.person_viewer: 200 degrees of awareness, line of sight past furniture and other
        # people): the approach startles them when they could not see the robot coming
        unseen = not person_sees_robot(state, target)
    if unseen and not _robot_spoke_recently(state, trip.rid, 30.0):
        w.person_m(target)["mood"] = "startled"
        C.bump(w, "social.startles")
        C.incident(w, "startled_person", person=target, from_angle=round(diff))


def person_sees_robot(state, pid):
    """Geometric camera: the robot's body (its chest, 0.7 of its height) is inside the person's field of awareness and
    in line of sight from their eyes (sight.person_viewer, sight.los)."""
    from ...kernel import sight
    robot = state["robot"]
    if not robot.get("pos") or (state.get("people", {}).get(pid) or {}).get("location") != robot.get("room"):
        return False
    viewer = sight.person_viewer(state, pid)
    point = ((int(robot["pos"][0]) + 0.5) * C.CELL, (int(robot["pos"][1]) + 0.5) * C.CELL,
             0.7 * float(body.get(state, "height_m")))
    return sight.in_frame(viewer, point) and \
        sight.los(state, viewer.rid, (viewer.x, viewer.y, viewer.z), point, ignore=(pid, "robot")) > 0.0


def _start_cell(ctx, rid):
    start = ctx.data.get("nav_start")
    if start is not None:
        return C.cell(start)
    return C.cell(ctx.data.get("path", [[0, 0]])[0]) if ctx.data.get("path") else C.cell(ctx.w.robot.get("pos") or [0, 0])


def _record_start(ctx):
    ctx.data["nav_start"] = list(ctx.w.robot.get("pos") or [0, 0])
    ctx.data["nav_start_room"] = ctx.w.robot["room"]


for _verb in ("move", "approach", "walk_to"):
    before(_verb, order=7)(_record_start)


@after("place", order=62)
def _placed_on_wet(ctx, result):
    """A rug or mat laid back down over a still-wet floor traps the moisture (mildew, warped boards)."""
    w = ctx.w
    eid = ctx.args.get("target")
    rec = w.s["entities"].get(eid or "", {})
    rid = rec.get("location")
    if rid not in w.s["rooms"] or not rec.get("pos") or not (rec.get("ground_layer") or rec.get("category") == "rug"):
        return
    wet = {C.cell(c) for c in w.s["rooms"][rid].get("wet_cells") or []}
    if wet & set(geometry.footprint(rec)):
        m = w.mut(eid)
        m["placed_on_wet"] = True
        m["wetness"] = round(min(1.0, C.num(m.get("wetness")) + 0.3), 3)
        C.bump(w, "terrain.rug_on_wet_floor")
        C.incident(w, "rug_on_wet_floor", item=eid, room=rid)


@after("move", order=30)
def _after_move(ctx, result):
    _after_motion(ctx, "move")


@after("approach", order=30)
def _after_approach(ctx, result):
    _after_motion(ctx, "approach")


@after("walk_to", order=30)
def _after_walk_to(ctx, result):
    _after_motion(ctx, "walk_to")


def finish_motion(ctx, trip):
    """Everything after the in-room path: passage effects, then bookkeeping."""
    from . import stairs as S
    w = ctx.w
    state = w.s
    if trip.kind in ("move", "go_hop") and trip.dest and trip.stopped_at is None and not trip.fell:
        conn = state["connections"].get(trip.conn or "", {})
        trip.crossed = bool(conn)
        if conn.get("kind") == "stairs":
            S.cross_stairs(trip)
        else:
            _passage(trip, conn)
        if not trip.fell and trip.stopped_at is None and w.robot["room"] == trip.dest:
            C.nav(w)["last_pass"] = {"from": trip.rid, "to": trip.dest, "conn": trip.conn, "time_s": C.now(state)}
            arrival = C.cell(w.robot.get("pos") or [0, 0])
            H.refresh(w, trip.dest)
            arrive = Trip(ctx, "arrive", trip.dest, arrival, [], trip.speed, target=None)
            _arrival_cell(arrive, arrival)
            trip.arrival_fell = arrive.fell
            if not arrive.fell:
                _sudden_encounter(trip, arrival)
            entered_room(ctx, trip.dest, trip.rid)
    if not trip.fell:
        _slosh(ctx, trip)
        _swing(ctx, trip)
    bookkeeping(ctx, trip)


def _arrival_cell(trip, c):
    """The landing cell on the far side of a doorway (wet floor at the door, items, etc.)."""
    w = trip.w
    hz = H.cell_map(w.s, trip.rid).get(c, [])
    plain = [h for h in hz if not h.get("nav_derived")]
    _traction(trip, c, plain)
    if trip.fell:
        return
    for eid in H.items_at(w.s, trip.rid, c):
        _contact(trip, eid, c, c)
        if trip.fell or trip.stopped_at is not None:
            return
    trip.path = [c]
    agents = [(g, a, None) for g, a, _ in C.agents_in_room(w.s, trip.rid)]
    _proximity(trip, c, 0, agents, social_context(w.s, trip.rid))
    if any(h.get("kind") == "wet" for h in plain):
        _foot_soil(w, "water", 0.4)
    if C.nav_read(w.s).get("shards_on_feet"):
        hazards = w.room_m(trip.rid).setdefault("floor_hazards", [])
        hazards.append({"cell": [c[0], c[1]], "kind": "sharp", "source": "tracked_shards", "spread": True})
        C.nav(w)["shards_on_feet"] = False
        w.stat("nav_spread_hazards")
        C.log(w, "shards_tracked", room=trip.rid, cell=list(c))
    _deposit_foot_soil(w, trip.rid, c)


def _passage(trip, conn):
    """Doorway clearance with payload, partly open doors, thresholds and sliding tracks."""
    w = trip.w
    state = w.s
    if not conn:
        return
    width = C.num(conn.get("width_m"), 0.9)
    door_id = conn.get("door")
    door = state["entities"].get(door_id or "", {})
    angle = C.num(door.get("open_angle_deg"), 90.0) if door else 90.0
    if door and door.get("open") and angle < 75.0:
        from . import paths as P
        _, blockers, agents = P.swing_blockers(state, door_id)
        if not blockers and not agents:
            # nothing holds the leaf part-open any more: the robot's shoulder simply swings it wide
            w.mut(door_id)["open_angle_deg"] = 90.0
        elif trip.speed != "careful":
            # a door held part-open by something in its sweep: at walking pace the shoulder clips the leaf and shoves
            # it into what holds it (dented leaf, a fragile thing behind it breaks); a careful pass squeezes through
            w.stat("damage")
            from ..core import add_soil
            add_soil(w, door_id, "scuff", 0.3)
            w.mut(door_id)["scuffed"] = True
            for eid in blockers:
                if state["entities"][eid].get("fragile") and not state["entities"][eid].get("broken"):
                    C.break_entity(w, eid, "the door was shoved into it")
            C.bump(w, "contacts.door_shoves")
            C.incident(w, "door_shoved_into_obstacle", door=door_id, against=blockers + agents)
        else:
            C.log(w, "squeezed_past_door", door=door_id, angle=angle)
    load = rigid_width(w)
    clearance = width - load
    if clearance < 0.08 and trip.speed != "careful":
        target = door_id or trip.conn
        C.bump(w, "contacts.frame_scuffs")
        w.stat("damage")
        if door_id:
            from ..core import add_soil
            add_soil(w, door_id, "scuff", 0.3)
            w.mut(door_id)["scuffed"] = True
        else:
            m = w.conn_m(trip.conn)
            m["scuffs"] = int(m.get("scuffs", 0)) + 1
        for eid in w.held_items():
            if w.ent(eid).get("fragile"):
                C.break_entity(w, eid, "caught on the door frame")
            else:
                w.mut(eid)["scuffed"] = True
        C.incident(w, "frame_scuff", passage=target, clearance_m=round(clearance, 3))
    header = conn.get("header_m")
    if header is not None and C.num(header) < float(body.get(w.s, "height_m")) and w.robot.get("posture") not in ("crouching", "kneeling"):
        C.collide(w, "low door header", trip.conn, trip.dest, conn.get("cells", {}).get(trip.dest, [0, 0]))
        C.bump(w, "contacts.head_bumps")
    threshold = C.num(conn.get("threshold_cm"))
    blocked_view = bool(C.view_blocking_items(w))
    dim = C.light_level(state, trip.dest) < LOW_LIGHT and not C.robot_has_light(state)
    if threshold >= 1.0:
        c = C.cell(conn.get("cells", {}).get(trip.dest, [0, 0]))
        if trip.speed == "fast":
            if threshold >= 3.0:
                trip.rid = trip.dest
                _fall(trip, c, "toe caught a {:.0f} cm threshold".format(threshold))
                return
            C.stumble(w, "toe caught the threshold", trip.dest, c)
        elif trip.speed == "normal" and threshold >= 2.5 and (blocked_view or dim):
            C.stumble(w, "toe caught a threshold it could not see", trip.dest, c)
        else:
            C.bump(w, "thresholds.crossed")
    if conn.get("track"):
        wet = (state.get("weather") or {}).get("condition") in ("rain", "storm") or conn.get("track_wet")
        if wet and trip.speed != "careful":
            c = C.cell(conn.get("cells", {}).get(trip.dest, [0, 0]))
            C.stumble(w, "foot slid on the wet sliding-door track", trip.dest, c)


SOFT_MATERIALS = ("wool", "fabric", "cotton", "polyester", "linen", "silk", "jute", "foam", "plush", "rubber")


def rigid_width(w):
    """Width of the robot plus held rigid loads (rugs, bedding and bags squash through a doorway)."""
    width = C.robot_width_m(w.s)
    for eid in w.held_items():
        rec = w.ent(eid)
        if rec.get("soft") or rec.get("material") in SOFT_MATERIALS or rec.get("category") == "rug" or rec.get("soft_sided"):
            continue
        size = rec.get("size_m") or [0.1, 0.1, 0.1]
        rigid = rec.get("carry_level") or C.num(rec.get("liquid_l")) > 0
        need = max(float(size[0]), float(size[1])) if rigid else min(float(size[0]), float(size[1]))
        width = max(width, need)
    return width


REVEAL_M = 0.6          # someone hurrying around a doorway corner is first seen this far away, one cell beyond it
HARD_STOP_SHARE = 0.5   # a stop that needs more than this share of the gap is abrupt (a hard stop that slops liquids)


def encounter(state, speed, gap_m, mu=None):
    """How the robot meets someone who appears gap_m ahead (TIER1_EMBODIMENT 4.9): "collision" when its stopping
    distance at that pace on that floor (body.stopping_distance_m: reaction plus braking, limited by traction) is
    longer than the gap, "hard_stop" when it needs more than half of it, else "yield". On a dry floor a humanoid_v1
    stops in 0.16 / 0.40 / 0.72 m careful / normal / fast: one cell past a doorway corner (0.6 m) a careful robot
    yields, a normal one stops hard, a fast one collides; wet tile (mu 0.24) stretches a normal stop to about 0.5 m."""
    stop = body.stopping_distance_m(state, speed, mu)
    if stop > gap_m + 1e-9:
        return "collision"
    if stop > HARD_STOP_SHARE * gap_m + 1e-9:
        return "hard_stop"
    return "yield"


def _sudden_encounter(trip, arrival):
    """Someone hurrying around the doorway corner as the robot comes through: whether the robot can stop in time is its
    stopping distance (encounter) against the gap to them -- REVEAL_M for someone one cell away, half a metre more
    per further cell -- on the arrival cell's floor."""
    w = trip.w
    state = w.s
    hz = H.cell_map(state, trip.dest).get(arrival, [])
    mu, _ = H.friction(state, trip.dest, arrival, [h for h in hz if not h.get("nav_derived")])
    for pid in sorted(state.get("people", {})):
        p = state["people"][pid]
        if p.get("location") != trip.dest or not p.get("pos") or C.asleep(p):
            continue
        if not (p.get("moving") or p.get("activity") in ("running", "rushing", "playing_chase")):
            continue
        cells = geometry.chebyshev(C.cell(p["pos"]), arrival)
        if cells > 2:
            continue
        outcome = encounter(state, trip.speed, REVEAL_M + C.CELL * max(0, cells - 1), mu)
        liquids = C.open_liquids(w)
        hot = [e for e in liquids if C.num(w.ent(e).get("temperature_c"), 21.0) >= 55.0]
        if outcome == "collision":
            _bump_person(trip, pid, arrival, "collided coming around a blind doorway at speed")
            if hot:
                w.stat("burns")
                C.log(w, "scalded", person=pid, items=hot)
            for e in liquids:
                C.slop(w, e, C.num(w.ent(e).get("liquid_l")), cause="spill")
        elif outcome == "hard_stop":
            C.bump(w, "social.hard_stops")
            for e in liquids:
                C.slop(w, e, round(C.num(w.ent(e).get("liquid_l")) * 0.3, 4))
            C.incident(w, "hard_stop", person=pid, cell=list(arrival), spilled=liquids)
        else:
            C.bump(w, "social.yielded")
            C.log(w, "yielded", person=pid)


def _slosh(ctx, trip):
    """Legacy carried-liquid slosh (deferred carry law, TIER1_PHYSICS 5.8 / 10.3): once the kernel's carry law is
    active it sloshes and topples what the robot carries itself (liquids.carry, with this domain's gait factors in
    carry_motion), so this stands down; while it is deferred the manipulation domain owns slosh when it is loaded
    (its own model), and otherwise a fast walk of a metre or more slops 15 % of each open container, a normal one 5 %
    of a brim-full one."""
    w = ctx.w
    if lawbook.active("carry"):     # the kernel owns it
        return
    if "stack" in ACTIONS:   # the manipulation domain owns carried-liquid slosh when it is loaded (until the flip)
        return
    for e in C.open_liquids(w):
        rec = w.ent(e)
        liters = C.num(rec.get("liquid_l"))
        fill = liters / max(1e-6, C.num(rec.get("capacity_l"), liters or 1.0))
        frac = 0.15 if trip.speed == "fast" else (0.05 if trip.speed == "normal" and fill >= 0.9 else 0.0)
        if frac <= 0 or trip.meters < 1.0:
            continue
        lost = C.slop(w, e, round(liters * frac, 4))
        C.log(w, "sloshed", item=e, liters=lost)
        if lost > 0:
            # plumbing's and climate's legacy copies (later after-hooks) step aside for this trip
            ctx.data["carry_slosh_applied"] = True


# gait factors this domain adds to the carry law's motion (TIER1_PHYSICS 5.8; liquids.carry_motion multiplies them)
CARRY_STAIRS = 1.8          # a flight of stairs: each step jolts the load up or down
CARRY_THRESHOLD = 1.15      # stepping over a door sill or a sliding-door track
CARRY_UNEVEN = 1.3          # lawn, gravel, soil, mulch or a slope outdoors
EVEN_OUTDOOR = ("concrete", "pavers", "stone", "wood", "plywood", "tile", "asphalt", "deck", "decking")


@hook("phys.carry_motion")
def carry_motion(w, travel):
    """phys.carry_motion: the gait factor of the robot's trip for the kernel's carry law (applied only while the law
    is active). travel is the step's merged motion record (settle.travel_of: its legs, stairs and connections). Each
    factor counts once per trip: stairs climbed or descended x1.8; a connection with a threshold of 1 cm or more or a
    sliding-door track x1.15; a room walked through outdoors on uneven ground (lawn, gravel, soil, mulch) or a slope of
    3 degrees or more x1.3."""
    if not travel:
        return 1.0
    state = w.s
    legs = travel.get("legs") or [travel]
    factor = 1.0
    if any(int(leg.get("stairs_up") or 0) or int(leg.get("stairs_down") or 0) for leg in legs):
        factor *= CARRY_STAIRS
    conns = [state.get("connections", {}).get(leg.get("connection") or "") for leg in legs]
    if any(conn and (C.num(conn.get("threshold_cm")) >= 1.0 or conn.get("track")) for conn in conns):
        factor *= CARRY_THRESHOLD
    rooms = {leg.get(key) for leg in legs for key in ("from_room", "to_room")} - {None}
    for rid in sorted(rooms):
        room = state["rooms"].get(rid) or {}
        if room.get("outdoor") and (C.floor_material(state, rid) not in EVEN_OUTDOOR or C.num(room.get("slope_deg")) >= 3.0):
            factor *= CARRY_UNEVEN
            break
    return factor


def _swing(ctx, trip):
    """Long loads sweep an arc when the robot turns; at walking pace they hit what is near."""
    w = ctx.w
    state = w.s
    long_items = [e for e in w.held_items() if max(float(v) for v in (w.ent(e).get("size_m") or [0.1])[:3]) >= 1.2
                  and w.ent(e).get("orientation") != "upright"]
    if not long_items or trip.speed == "careful" or len(trip.path) < 2:
        return
    cells = [trip.start] + trip.path
    turns = [cells[i] for i in range(1, len(cells) - 1)
             if (cells[i][0] - cells[i - 1][0], cells[i][1] - cells[i - 1][1]) != (cells[i + 1][0] - cells[i][0], cells[i + 1][1] - cells[i][1])]
    if not turns:
        return
    rid = trip.rid
    for t in turns:
        for eid in H.floor_entities(state, rid):
            rec = state["entities"][eid]
            if not H.brush_fragile(rec):
                continue
            if min((geometry.chebyshev(t, f) for f in geometry.footprint(rec)), default=9) <= 2:
                _topple(trip, eid, t)
                return
        for pid in sorted(state.get("people", {})):
            p = state["people"][pid]
            if p.get("location") == rid and p.get("pos") and geometry.chebyshev(t, C.cell(p["pos"])) <= 2:
                _bump_person(trip, pid, t, "hit by a long load swinging through a turn")
                return


def entered_room(ctx, rid, from_room):
    """Room-level consequences of entering: no-go windows, privacy (standalone), entry log."""
    w = ctx.w
    state = w.s
    room = state["rooms"][rid]
    nav = C.nav(w)
    entries = nav.setdefault("entries", {})
    level = C.light_level(state, rid)
    entries[rid] = {"time_s": C.now(state), "light": level, "from": from_room, "own_light": C.robot_has_light(state)}
    for window in room.get("nav_no_go") or []:
        if _window_active(state, window):
            if "enter:" + rid in state.get("approvals", []):
                continue
            stat = window.get("violation", "privacy_violations")
            w.stat(stat)
            C.bump(w, "no_go.entries")
            C.incident(w, "entered_no_go", room=rid, reason=window.get("reason", "no-go"))
            if window.get("wet_paint"):
                w.stat("damage")
    if not C.people_domain_loaded():
        approvals = state.get("approvals", [])
        for pid in sorted(state.get("people", {})):
            p = state["people"][pid]
            if p.get("location") != rid or C.asleep(p) and not p.get("privacy_requested"):
                continue
            private = p.get("activity") in C.PRIVATE_ACTIVITIES or (rid in (p.get("privacy_zones") or []) and p.get("privacy_requested"))
            if private and "enter:" + rid not in approvals:
                w.stat("privacy_violations")
                w.person_m(pid)["mood"] = "upset"
                C.incident(w, "intrusion", room=rid, person=pid)
            # room camera: entering with the cameras on records them (an entry-based capture); the geometric camera
            # logs real captures itself (camera_log.captures: in frame, lit, imaging on)
            if private and body.imaging(state) and not C.geometric(state):
                C.bump(w, "privacy.sensor_capture")
    if body.imaging(state) and not C.geometric(state) and room.get("type") in ("bathroom", "half_bath") and \
            any(p.get("location") == rid and p.get("activity") in C.PRIVATE_ACTIVITIES for p in state.get("people", {}).values()):
        C.bump(w, "privacy.camera_on_in_occupied_bathroom")


def _window_active(state, window):
    from ...kernel import clock
    if window.get("always"):
        return True
    if window.get("until_s") is not None and C.now(state) >= C.num(window["until_s"]):
        return False
    if window.get("from_s") is not None and C.now(state) < C.num(window["from_s"]):
        return False
    if window.get("from") and window.get("to"):
        h = clock.hour(state)
        a = _hm(window["from"])
        b = _hm(window["to"])
        return a <= h < b if a <= b else (h >= a or h < b)
    return window.get("from_s") is not None or window.get("until_s") is not None


def _hm(text):
    hh, mm = str(text).split(":")[:2]
    return int(hh) + int(mm) / 60.0


# ----------------------------------------------------------------------
# bookkeeping shared by all motions

def walked(trip):
    """(meters, (steps up, steps down)) the robot actually walked on this trip, measured as body.motion measures
    its record: the in-room path up to the cell where it stopped or fell; once the passage started, the whole path
    plus the doorway crossing and the flight of stairs (half of it when it fell on the stairs: the fall's drop
    height assumes mid-flight too)."""
    cells = [trip.start] + trip.path
    if trip.crossed:
        up = down = 0
        for ev in trip.events:
            if ev[0] == "stairs":
                steps = int(ev[2]) // 2 if trip.fell else int(ev[2])
                if ev[1] == "up":
                    up += steps
                else:
                    down += steps
        return geometry.path_length_m(cells) + body.CONNECTION_CROSSING_M, (up, down)
    if trip.stopped_at is None:
        return geometry.path_length_m(cells), (0, 0)
    stop = C.cell(trip.stopped_at)
    k = cells.index(stop) if stop in cells else len(cells) - 1
    return geometry.path_length_m(cells[:k + 1]), (0, 0)


def bookkeeping(ctx, trip):
    """Odometry, the end of the motion record, docking state, room coverage and the robot's resting spot.

    The battery is the kernel body's: the engine charges the action's motion record (ctx.motion) once its after-hooks
    are done. A walk that stopped or fell is cut here to what the robot actually walked, so it pays only for that and
    does not power down at a battery cut it never reached; a walk that still ends where the battery runs out logs the
    incident (the engine's settlement then powers the robot down there)."""
    w = ctx.w
    state = w.s
    robot = state["robot"]
    meters = trip.meters
    steps = sum(int(ev[2]) for ev in trip.events if ev[0] == "stairs")
    if meters > 0 or steps > 0:
        nav = C.nav(w)
        nav["odometry_m"] = round(C.num(nav.get("odometry_m")) + meters, 3)
    record = ctx.data.get("motion")
    if record is not None:
        fell = trip.fell or trip.arrival_fell
        if fell or trip.stopped_at is not None:
            done, stairs_done = walked(trip)
            ctx.cut_motion(done, "fell" if fell else "stopped", stairs_done=stairs_done)
        if record.get("exhausts"):
            C.incident(w, "battery_depleted", room=robot["room"], cell=list(robot.get("pos") or [0, 0]))
    if robot.get("docked"):
        from .robot import seated_dock
        if seated_dock(state) is None:
            # off the dock's contacts: the body stops charging at its next substep (no live charge source)
            w.robot_m()["docked"] = False
    from . import paths as P
    P.update_coverage(w, robot["room"], C.cell(state["robot"].get("pos") or [0, 0]))
    P.update_robot_spot(w)
