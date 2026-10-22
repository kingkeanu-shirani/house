"""Static stability: tip, slide and roll thresholds, overhang, stacks, furniture tip margins, slot overload, carried
handles and crush limits (tier-1 physics, docs/design/TIER1_PHYSICS.md 5.2-5.4; fidelity P-approx: lumped rigid
bodies and quasi-static thresholds, no dynamics).

Everything here is a pure function of the state: nothing writes. impact.py applies the consequences (the settle laws
live there). Thresholds, in units of g for accelerations:

* tip under a horizontal acceleration a when a > b / (2 z) (b: base width along the push, z: composite COM height);
  slide when a > mu. The smaller threshold wins. Anchors: an empty wine glass (b 7 cm, z 11 cm) tips at 0.32 g; a mug
  (b 8 cm, z 5 cm) would need 0.8 g, so with mu 0.5 it slides first.
* on a slope or tilted support of angle theta: slide when tan(theta) > mu, tip when tan(theta) > b / (2 z), and a
  rollable item rolls when tan(theta) > c_rr (rolling resistance).
* a stack (two or more portable items, each resting on the one below) topples when an upper item's footprint is more
  than 1.25x the one below it, when it sits more than 0.3 of the lower footprint off centre, when its height exceeds 6x
  its base or the clearance above it; above 4x its base it is wobbly (manipulation's rules, unchanged).
* furniture tips forward when the moments about its front bottom edge (open drawers through ``phys.tip_loads``, a
  pull at a height, a climber) exceed the righting moment of its own mass at half its depth; ``anchored`` never tips.
* a rated slot fails when its load exceeds max(rating, baseline load + 0.5 kg) (a staged support: its rating, once its
  creep time has passed), and sags visibly above 0.9 x its rating.
* soaked (wetness >= 0.5) corrugated board keeps 40 % of its strength (Phys.wet_strength), for crushing and handles.
* only crushable items (Phys.crushable: manipulation's max_load rule) have a crush limit; a stack of books or bins
  never crushes its bottom member, and a stack of its own kind (plates on plates) bears 5x a point load.
"""
import math

from .. import geometry
from ..plugins import run_hooks
from . import support

G = 9.81
INF = float("inf")
SAG_FRACTION = 0.9            # load above this fraction of the rating shows as a sagging shelf (glance cue)
STACK_OVERSIZE = 1.25         # an upper item this much larger than the one below it rocks off
STACK_OFFSET_FRAC = 0.3       # centre offset beyond this fraction of the lower footprint topples
STACK_WOBBLY = 4.0            # height / base beyond which a stack is wobbly (a glance cue)
STACK_TOPPLE = 6.0            # height / base beyond which a stack topples
SHELF_GAP_MARGIN_M = 0.02     # a stack must clear the shelf above by this much (manipulation's clearance_for)
LOW_BASE_M = 0.015            # a flat base this thin (a charging pad 12 mm, a watch puck, a coaster, a trivet, a
                              # cutting board) is part of the surface: an item centred over it rests on both, it does
                              # not rock off (a plate, 25 mm, or a book is a stack member as before)
NEST_STEP_M = 0.02            # height a nested item adds (bowls in bowls)
# rolling resistance on a support (floors.json c_rr): a hard finished top rolls like tile or wood (0.01), a soft one
# (cushion, mattress, upholstery) like carpet (0.06)
C_RR_HARD, C_RR_SOFT, SOFT_SUPPORT = 0.01, 0.06, 0.5
SOAKED_WETNESS = 0.5          # wetness at which a material is at its wet strength (Phys.wet_strength)
PLATE_EDGE_FACTOR = 3.0       # plates packed on edge or in dividers carry 3x the load flat plates do
PLATE_KINDS = ("plate", "dish", "platter")
# an item under a stack of its own kind (plates on plates, bowls in bowls) carries the load on its whole foot ring:
# kitchen cupboards and restaurant dispensers hold stacks of 20-40 dinner plates (12-25 kg) without breaking any
LIKE_STACK_FACTOR = 5.0


def _rounded(value, places=4):
    """A float rounded for storage and reports; None for infinite (never written into JSON state)."""
    return None if value is None or not math.isfinite(value) else round(value, places)


def world_yaw(state, eid):
    """An entity's yaw in its room frame (degrees): its own yaw plus that of everything it rests on."""
    entities = state["entities"]
    total, current, seen = 0.0, eid, set()
    while current in entities and current not in seen:
        seen.add(current)
        total += support.yaw_of(entities[current])
        current = entities[current].get("location")
    return total % 360.0


def free_standing(state, eid):
    """Whether gravity, slopes and pushes can move eid: it rests on something (support_of), is not secured,
    anchored, rooted or fixed in place, and is portable, on wheels or casters, or free-standing furniture. Installed
    fixtures (fences, tubs, sinks), plants in the ground and built-in appliances stay where they are."""
    rec = state["entities"][eid]
    if support.support_of(state, eid) is None:
        return False
    if rec.get("secured") or rec.get("anchored") or rec.get("rooted") or rec.get("fixed_in_place"):
        return False
    return bool(rec.get("portable") or rec.get("casters") or rec.get("wheeled")) or \
        rec.get("category") == "furniture" or "furniture" in (rec.get("is_a") or ())


def footprint_wd(state, eid):
    """(w, d) of an entity's resting footprint in its parent's frame (a quarter-turn rot swaps them)."""
    w, d, _ = support.resting_size(state, eid)
    if int(round(support.yaw_of(state["entities"][eid]) / 90.0)) % 2 == 1:
        w, d = d, w
    return w, d


# ----- tip, slide, roll ----------------------------------------------------------------------------------------------

def base_width_m(state, eid, b):
    """The base width an upright item pivots on: b (its footprint side), or the narrower foot it stands on (Phys.base_m:
    a wine glass's 7 cm foot under its 9 cm bowl, a lamp's base under its shade). Lying on its side it rests on its
    whole length."""
    if state["entities"][eid].get("orientation") not in (None, "upright"):
        return b
    foot = support.finite(support.phys(state, eid).base_m)
    return min(b, foot) if foot is not None and foot > 0 else b


def tip_accel_g(state, eid, axis=None, cmap=None):
    """Horizontal acceleration (g) that tips eid: b / (2 z). axis None takes the narrow side of the base (the weakest
    direction); otherwise axis is a push direction in degrees in the room frame (0 = +col, 90 = +row). An upright item
    on a foot narrower than its bounding box (Phys.base_m) pivots on the foot."""
    w, d, _ = support.resting_size(state, eid)
    z = support.com_height(state, eid, cmap)
    if axis is None:
        b = min(w, d)
    else:
        rel = math.radians(float(axis) - world_yaw(state, eid))
        b = w if abs(math.cos(rel)) >= abs(math.sin(rel)) else d
    b = base_width_m(state, eid, b)
    return b / (2.0 * z) if z > 1e-9 else INF


def slide_accel_g(state, eid):
    """Horizontal acceleration (g) that makes eid slide on its support: the contact friction coefficient."""
    return support.contact_mu(state, eid)


def support_tilt_deg(state, eid):
    """Tilt (degrees) of what eid rests on: the support entity's tilt_deg, or the room's slope_deg on a floor."""
    entities = state["entities"]
    loc = entities[eid].get("location")
    if loc in entities:
        return max(0.0, support.num(entities[loc].get("tilt_deg")))
    if loc in state["rooms"]:
        return max(0.0, support.num(state["rooms"][loc].get("slope_deg")))
    return 0.0


def rolling_resistance(state, eid):
    """Rolling-resistance coefficient c_rr under eid: floors.json on a floor; on a support, hard finished tops as hard
    floors (0.01) and soft ones (cushions, mattresses, upholstery: softness >= 0.5) as carpet (0.06)."""
    entities = state["entities"]
    loc = entities[eid].get("location")
    if loc in state["rooms"]:
        return support.num(support.floor_at(state, loc, entities[eid].get("pos")).get("c_rr"), C_RR_HARD)
    if loc in entities and support.num(support.phys(state, loc).softness) >= SOFT_SUPPORT:
        return C_RR_SOFT
    return C_RR_HARD


def roll_accel_mps2(state, eid):
    """Downhill acceleration (m/s2) of eid rolling on its incline, 0 when it stays put: the rolling process's own rule
    (processes.roll_accel: a sphere, a cylinder on its side or a wheeled item, not secured or chocked, rolls when
    tan(theta) beats rolling_resistance)."""
    from .processes import roll_accel
    return roll_accel(state, eid)


def slope_verdict(state, eid, cmap=None):
    """What eid does on its tilted support or sloped floor: "roll", "tip", "slide" or None (stays). Rolling wins
    when it applies; otherwise the smaller of the tip (b / 2z) and slide (mu) thresholds against tan(theta).
    Only free-standing things move (free_standing)."""
    if not free_standing(state, eid):
        return None
    theta = support_tilt_deg(state, eid)
    if theta <= 0.0:
        return None
    if roll_accel_mps2(state, eid) > 0.0:
        return "roll"
    t = math.tan(math.radians(min(theta, 89.9)))
    tip, slide = tip_accel_g(state, eid, cmap=cmap), slide_accel_g(state, eid)
    if t > min(tip, slide) + 1e-9:
        return "tip" if tip <= slide else "slide"
    return None


# ----- overhang -------------------------------------------------------------------------------------------------------

def surface_frame(state, eid):
    """(sw, sd, x0, y0, fw, fd) of an item placed on a surface at spos, in the surface's corner frame; None when eid
    does not rest on a surface at a known spot."""
    entities = state["entities"]
    rec = entities[eid]
    parent = entities.get(rec.get("location"))
    spos = rec.get("spos")
    if parent is None or rec.get("relation", "on") != "on" or not isinstance(spos, (list, tuple)) or len(spos) < 2:
        return None
    size = parent.get("surface_size_m") or (parent.get("size_m") or [0.5, 0.5])[:2]
    sw, sd = support.num(size[0], 0.5), support.num(size[1], 0.5)
    fw, fd = footprint_wd(state, eid)
    return sw, sd, support.num(spos[0]), support.num(spos[1]), fw, fd


def edge_margin_m(state, eid):
    """Distance (m) from eid's centre of mass, projected down, to the nearest edge of its surface: negative once the
    COM is past the edge (it falls). None when eid is not placed on a surface."""
    frame = surface_frame(state, eid)
    if frame is None:
        return None
    sw, sd, x0, y0, fw, fd = frame
    cx, cy = x0 + fw / 2.0, y0 + fd / 2.0
    return min(cx, sw - cx, cy, sd - cy)


def overhang_frac(state, eid):
    """Fraction (0..1) of eid's footprint beyond the edges of its surface (0 when not placed on one)."""
    frame = surface_frame(state, eid)
    if frame is None:
        return 0.0
    sw, sd, x0, y0, fw, fd = frame
    area = fw * fd
    if area <= 0:
        return 0.0
    overlap = max(0.0, min(x0 + fw, sw) - max(x0, 0.0)) * max(0.0, min(y0 + fd, sd) - max(y0, 0.0))
    frac = 1.0 - overlap / area
    return 0.0 if frac < 1e-9 else min(1.0, frac)


def room_point(state, eid, _depth=0):
    """(x, y) in metres, in its room's frame, of an entity's centre: a floor item's footprint centre (its xy_m when a
    domain placed it continuously), an item on a surface its spot turned with the surface, anything else (in a
    container, hung, held) the point of what holds it. None when it is not in a room."""
    entities = state["entities"]
    rec = entities.get(eid)
    if rec is None or _depth > 30:
        return None
    loc = rec.get("location")
    if loc in state["rooms"]:
        pos = rec.get("pos")
        if not pos:
            return None
        w, d = footprint_wd(state, eid)
        xy = rec.get("xy_m")
        x0, y0 = (support.num(xy[0]), support.num(xy[1])) if isinstance(xy, list) and len(xy) == 2 else \
            (pos[0] * geometry.CELL, pos[1] * geometry.CELL)
        return x0 + w / 2.0, y0 + d / 2.0
    holder = state["robot"] if loc == "robot" else state.get("people", {}).get(loc) or state.get("pets", {}).get(loc)
    if holder is not None:
        pos = holder.get("pos")
        return ((pos[0] + 0.5) * geometry.CELL, (pos[1] + 0.5) * geometry.CELL) if pos else None
    base = room_point(state, loc, _depth + 1)
    frame = surface_frame(state, eid)
    if base is None or frame is None:
        return base
    sw, sd, x0, y0, fw, fd = frame
    rad = math.radians(world_yaw(state, loc))
    u, v = x0 + fw / 2.0 - sw / 2.0, y0 + fd / 2.0 - sd / 2.0
    return base[0] + u * math.cos(rad) - v * math.sin(rad), base[1] + u * math.sin(rad) + v * math.cos(rad)


def overhang_dir_deg(state, eid):
    """Room-frame direction (degrees) from the surface's centre toward eid's centre: where an item past the edge
    goes down. None when eid is not placed on a surface."""
    frame = surface_frame(state, eid)
    if frame is None:
        return None
    sw, sd, x0, y0, fw, fd = frame
    u, v = x0 + fw / 2.0 - sw / 2.0, y0 + fd / 2.0 - sd / 2.0
    if abs(u) < 1e-9 and abs(v) < 1e-9:
        return None
    parent = state["entities"][eid]["location"]
    return (math.degrees(math.atan2(v, u)) + world_yaw(state, parent)) % 360.0


# ----- stacks ---------------------------------------------------------------------------------------------------------

def stack_base(state, eid):
    """The bottom member of the stack eid belongs to: walk down while eid rests on a portable item."""
    entities = state["entities"]
    current, seen = eid, {eid}
    while True:
        found = support.support_of(state, current)
        if found is None or found[1] != ("surface",) or not entities[found[0]].get("portable") or found[0] in seen:
            return current
        seen.add(found[0])
        current = found[0]


def stack_chain(state, base_id, cmap=None):
    """[base, item on it, item on that, ...]: the chain follows an item while exactly one portable item rests on it
    (several items side by side on a tray are not a stack) and stops below a secured item (fastened to what is under
    it, like a pole in its base: not loosely stacked)."""
    cmap = support._map(state, cmap)
    entities = state["entities"]
    chain, current, seen = [base_id], base_id, {base_id}
    while True:
        above = [c for c in support.slot_children(state, current, ("surface",), cmap) if entities[c].get("portable")]
        if len(above) != 1 or above[0] in seen or entities[above[0]].get("secured"):
            return chain
        chain.append(above[0])
        seen.add(above[0])
        current = above[0]


def _nests(upper, lower):
    group = upper.get("nest_group")
    if not group or group != lower.get("nest_group"):
        return False
    uw, ud, _ = support.dims(upper)
    lw, ld, _ = support.dims(lower)
    return uw <= lw + 1e-6 and ud <= ld + 1e-6


def _centre_offset(state, upper):
    """(dx, dy) of an upper member's centre from the centre of the one below it: manipulation's stack_offset_m when
    set, else from its spos on the lower item's surface."""
    entities = state["entities"]
    rec = entities[upper]
    offset = rec.get("stack_offset_m")
    if isinstance(offset, (list, tuple)) and len(offset) >= 2:
        return support.num(offset[0]), support.num(offset[1])
    frame = surface_frame(state, upper)
    if frame is None:
        return 0.0, 0.0
    sw, sd, x0, y0, fw, fd = frame
    return x0 + fw / 2.0 - sw / 2.0, y0 + fd / 2.0 - sd / 2.0


def _rack_slots(state, parent_id, cmap=None):
    """The shelf positions taken inside a parent whose shelves are removable racks in rack guides (an oven's or a
    toaster oven's: a portable surface sits in it at a shelf position), else None (fixed shelf boards)."""
    entities = state["entities"]
    kids = support._map(state, cmap).get(parent_id, ())
    inside = [c for c in kids if entities[c].get("relation") == "in"]
    if not any(entities[c].get("portable") and entities[c].get("surface") and entities[c].get("accepts_items")
               for c in inside):
        return None
    return {support._shelf_index(entities[c]) for c in inside}


def clearance_above_m(state, base_id, cmap=None):
    """Vertical room (m) above a stack base: the gap to the next shelf, a container's inner height or a surface's
    clearance_above_m; None when unlimited. In a parent with removable racks the next shelf is the next position a
    rack (or anything) takes, else the cavity top: an empty rack position is no shelf (adoption request
    appliances_cooking 1: a dish on an oven rack was toppled by the empty slot 6 cm above)."""
    entities = state["entities"]
    rec = entities[base_id]
    parent = entities.get(rec.get("location"))
    if parent is None:
        return None
    if rec.get("relation") == "in":
        heights = parent.get("shelf_heights")
        inner = parent.get("inner_size_m")
        if heights:
            k = support._shelf_index(rec)
            taken = _rack_slots(state, rec.get("location"), cmap)
            if taken is not None and 0 <= k < len(heights):
                above = [j for j in taken if k < j < len(heights)]
                if above:
                    return support.num(heights[min(above)]) - support.num(heights[k]) - SHELF_GAP_MARGIN_M
                if inner and len(inner) > 2:
                    return support.num(inner[2]) - support.num(heights[k])
                return None
            if 0 <= k and k + 1 < len(heights):
                return support.num(heights[k + 1]) - support.num(heights[k]) - SHELF_GAP_MARGIN_M
            if inner and len(inner) > 2 and parent.get("openable") and 0 <= k < len(heights):
                return support.num(inner[2]) - support.num(heights[k])
            return None
        if inner and len(inner) > 2:
            return support.num(inner[2])
    return support.finite(parent.get("clearance_above_m"))


def stack_height_m(state, members):
    """Height (m) of a stack from its base's bottom to its top member's top (nested members add NEST_STEP_M)."""
    entities = state["entities"]
    height = 0.0
    for i, eid in enumerate(members):
        h = support.resting_size(state, eid)[2]
        if i + 1 < len(members) and _nests(entities[members[i + 1]], entities[eid]):
            height += NEST_STEP_M
        else:
            height += h
    return height


def stack_verdict(state, base_id, cmap=None):
    """[(eid, "topple" | "wobbly")] for the stack whose bottom member is base_id (a portable item): every member from
    the first unstable one up topples; otherwise every member of a stack taller than 4x its base is wobbly. [] for a
    sound stack or a non-portable base."""
    entities = state["entities"]
    if not entities[base_id].get("portable"):
        return []           # a table or shelf with one item on it is a support, not a stack
    members = stack_chain(state, base_id, cmap)
    unstable_at = None
    for i in range(1, len(members)):
        upper, lower = entities[members[i]], entities[members[i - 1]]
        uw, ud = footprint_wd(state, members[i])
        lw, ld = footprint_wd(state, members[i - 1])
        dx, dy = _centre_offset(state, members[i])
        if i == 1 and support.resting_size(state, members[0])[2] <= LOW_BASE_M + 1e-9 and                 abs(dx) <= lw / 2.0 + 1e-9 and abs(dy) <= ld / 2.0 + 1e-9:
            # a low flat base under an item centred over it (a phone on a Qi pad, a watch on its puck): the item rests
            # on the base and the surface around it, so neither the oversize nor the offset rule applies (adoption
            # request electrical 1)
            continue
        u_long, u_short = sorted((uw, ud), reverse=True)
        l_long, l_short = sorted((lw, ld), reverse=True)
        if not _nests(upper, lower) and (u_long > l_long * STACK_OVERSIZE + 1e-6 or u_short > l_short * STACK_OVERSIZE + 1e-6):
            unstable_at = i
            break
        inside = abs(dx) + uw / 2.0 <= lw / 2.0 + 1e-9 and abs(dy) + ud / 2.0 <= ld / 2.0 + 1e-9
        if not inside and (abs(dx) > STACK_OFFSET_FRAC * lw + 1e-9 or abs(dy) > STACK_OFFSET_FRAC * ld + 1e-9):
            # an off-centre member topples only when it reaches past the one below: a receipt anywhere on a tray
            # lies wholly on it (adoption request textiles_laundry 1)
            unstable_at = i
            break
    height = stack_height_m(state, members)
    slender = height / max(0.05, min(footprint_wd(state, members[0])))
    if unstable_at is None and len(members) > 1 and slender > STACK_TOPPLE:
        unstable_at = max(1, len(members) // 2)
    clearance = clearance_above_m(state, members[0], cmap)
    if clearance is not None and len(members) > 1 and height > clearance + 1e-6:
        unstable_at = len(members) - 1 if unstable_at is None else min(unstable_at, len(members) - 1)
    if unstable_at is not None:
        return [(eid, "topple") for eid in members[unstable_at:]]
    if len(members) > 1 and slender > STACK_WOBBLY:
        return [(eid, "wobbly") for eid in members]
    return []


def stability(state, eid, cmap=None):
    """{"overhang", "tip_g", "slide_g", "rolls", "wobbly"} for one entity: its footprint fraction past the edge, its
    tip and slide accelerations (g; None when unlimited), whether it rolls on its current slope, and whether the stack
    it belongs to is slender enough to wobble (a single tall item is not a stack)."""
    base = stack_base(state, eid)
    verdict = stack_verdict(state, base, cmap)
    return {"overhang": round(overhang_frac(state, eid), 4),
            "tip_g": _rounded(tip_accel_g(state, eid, cmap=cmap)),
            "slide_g": _rounded(slide_accel_g(state, eid)),
            "rolls": slope_verdict(state, eid, cmap) == "roll",
            "wobbly": any(member == eid and kind == "wobbly" for member, kind in verdict)}


# ----- furniture tip --------------------------------------------------------------------------------------------------

def tip_loads(state, furniture_id):
    """[(mass_kg, x_m, h_m)] the phys.tip_loads hooks report for a piece of furniture (open drawers: mechanisms),
    x measured outward from the front edge."""
    out = []
    for loads in run_hooks("phys.tip_loads", state, furniture_id):
        for load in loads or ():
            mass, x = support.num(load[0]), support.num(load[1])
            h = support.num(load[2]) if len(load) > 2 else 0.0
            out.append((mass, x, h))
    return out


def tip_margin_nm(state, furniture_id, pull_n=0.0, pull_h_m=0.0, extra=(), cmap=None):
    """Righting minus overturning moment (N m) about the front bottom edge of a piece of furniture; it tips when this
    is negative. Righting: its mass and everything it carries (minus the hook-reported loads, which are counted where
    they are) at half its depth. Overturning: each hook or extra load (mass_kg, x_m[, h_m]) at x beyond the front edge,
    plus a horizontal pull of pull_n newtons at pull_h_m. anchored furniture never tips (inf)."""
    rec = state["entities"][furniture_id]
    if rec.get("anchored") or support.phys(state, furniture_id).anchored:
        return INF
    depth = support.dims(rec)[1]
    hooked = tip_loads(state, furniture_id)
    loads = hooked + [(support.num(e[0]), support.num(e[1]), support.num(e[2]) if len(e) > 2 else 0.0) for e in extra]
    body = max(0.0, support.subtree_mass(state, furniture_id, cmap) - sum(mass for mass, _, _ in hooked))
    righting = body * G * depth / 2.0
    overturning = sum(mass * G * x for mass, x, _ in loads) + support.num(pull_n) * support.num(pull_h_m)
    return righting - overturning


# ----- rated slots, handles and crushing -----------------------------------------------------------------------------

def creep_pending(state, sid):
    """True while a staged support's creep time (phys_creep_s, absolute simulated seconds) is still ahead."""
    due = support.finite(state["entities"][sid].get("phys_creep_s"))
    return due is not None and support.num(state.get("time_s")) < due


def overload(state, sid, slot, env=None, cmap=None):
    """{"load", "rating", "limit", "sagging", "fails"} of one rated slot, or None when the slot is unrated. The limit
    is max(rating, baseline load + 0.5 kg) when the scene already loaded the slot (weight the generator or a setup put
    there is never charged to the robot), else the rating. A staged support (_phys_staged) has no allowance, except
    while its creep time is pending: it holds its staged load until the creep check decides."""
    rated = support.rating(state, sid, slot)
    if rated is None:
        return None
    load = support.load_on(state, sid, slot, cmap)
    limit = rated
    if not state["entities"][sid].get("_phys_staged") or creep_pending(state, sid):
        allowance = support.baseline_load(env, sid, slot)
        if allowance > 0:
            limit = max(rated, allowance + support.BASELINE_MARGIN_KG)
    return {"load": load, "rating": rated, "limit": limit, "sagging": load > SAG_FRACTION * rated + 1e-9,
            "fails": load > limit + 1e-9}


def handle_status(state, eid, cmap=None):
    """{"contents_kg", "limit", "ratio"} of a container while someone carries it (its handle or bottom holds its
    contents), or None when it rests or has no handle rating. A soaked handle or bottom has its wet strength (wet
    cardboard: 40 %)."""
    found = support.support_of(state, eid)
    if found is None or found[1] != ("held",):
        return None
    p = support.phys(state, eid)
    rated = support.finite(p.handle_kg)
    if rated is None:
        return None
    rec = state["entities"][eid]
    rated *= _wet_factor(p, rec)
    contents = max(0.0, support.subtree_mass(state, eid, cmap) - support.num(rec.get("mass_kg")))
    return {"contents_kg": contents, "limit": rated, "ratio": contents / rated if rated > 0 else INF}


def _wet_factor(p, rec):
    """Strength factor of a soaked item (Phys.wet_strength; 1 while it is drier than SOAKED_WETNESS)."""
    if support.num(rec.get("wetness")) >= SOAKED_WETNESS:
        return support.num(p.wet_strength, 1.0)
    return 1.0


def padding_mm(rec):
    """Cushioning (mm) around an item: a full wrap's padding (coverage >= 95 %), halved for unstuffed hollow items,
    plus 6 mm in a cell divider (manipulation's rule, unchanged)."""
    wrapped = rec.get("wrapped") if isinstance(rec.get("wrapped"), dict) else {}
    padding = support.num(wrapped.get("padding_mm")) if support.num(wrapped.get("coverage")) >= 0.95 else 0.0
    if rec.get("hollow") and not wrapped.get("stuffed"):
        padding *= 0.5
    if rec.get("in_divider"):
        padding += 6.0
    return padding


def padding_factor(rec):
    """Cushioning lengthens the stopping distance: a survivable drop scales with 1 + padding / 3 mm."""
    return 1.0 + padding_mm(rec) / 3.0


def _like_stack(state, eid, cmap):
    """True when everything resting directly on eid is its own kind (same type, or same nest group; the placeholder
    generic_item is no kind) and no wider than it: plates on a plate, bowls in a bowl. Such a load spreads over the
    whole foot ring instead of pressing on one spot."""
    rec = state["entities"][eid]
    above = support.slot_children(state, eid, ("surface",), cmap)
    if not above:
        return False
    kind = rec.get("type") if rec.get("type") != "generic_item" else None
    group = rec.get("nest_group") or support.phys(state, eid).nest_group
    long_side, short_side = sorted(footprint_wd(state, eid), reverse=True)
    for other in above:
        orec = state["entities"][other]
        same = (kind is not None and orec.get("type") == kind) or \
            (group and (orec.get("nest_group") or support.phys(state, other).nest_group) == group)
        o_long, o_short = sorted(footprint_wd(state, other), reverse=True)
        if not same or o_long > long_side + 1e-6 or o_short > short_side + 1e-6:
            return False
    return True


def crush_limit_kg(state, eid, cmap=None):
    """Load (kg) eid bears on top before it is crushed, or None when it cannot be crushed (manipulation's max_load
    rule, TIER1_PHYSICS 5.3: only Phys.crushable items, those that declare a crush or load limit, are fragile or
    crushable, get a crush_kg row or crumple by class): crush_kg (variation applied) x (1 + min(1.5, padding / 10 mm))
    for a full wrap, x3 for plates on edge or in dividers, x5 under a stack of its own kind (plates on plates: the load
    spreads over the foot ring), x its wet strength when soaked (wet cardboard 0.4)."""
    p = support.phys(state, eid)
    if not p.crushable:
        return None
    base = support.finite(p.crush_kg)
    if base is None:
        return None
    rec = state["entities"][eid]
    wrapped = rec.get("wrapped") if isinstance(rec.get("wrapped"), dict) else {}
    if support.num(wrapped.get("coverage")) >= 0.99:
        base *= 1.0 + min(1.5, support.num(wrapped.get("padding_mm")) / 10.0)
    kinds = {rec.get("type"), rec.get("category")} | set(rec.get("is_a") or ())
    if rec.get("in_divider") or (rec.get("pack_orientation") == "on_edge" and kinds & set(PLATE_KINDS)):
        base *= PLATE_EDGE_FACTOR
    elif _like_stack(state, eid, support._map(state, cmap)):
        base *= LIKE_STACK_FACTOR
    return base * _wet_factor(p, rec)
