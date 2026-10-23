"""Wind and rain on objects (docs/design/TIER1_PHYSICS.md 5.10 and 6.3; laws wind and rain, both deferred at landing;
fidelity P-approx).

outdoor_plants keeps owning the weather (``weather.wind_mps``, ``gust_mps``, ``wind_from_deg``, ``rain_mm_h``); this
module applies its effects to generic objects with outdoor_plants' drag model, the canonical formula:

    drag   F = 0.5 rho_air Cd A v^2          rho_air 1.2 kg/m3, Cd 1.2 (or drag_cd: the record's, else its
                                             supports.json row: open frames such as chairs and racks 0.7),
                                             A = frontal area
    lift   flat sheets lift at v_lift = sqrt(2 g sigma / (rho_air C_eff)), sigma = mass / top area,
           C_eff 0.033 for flexible sheets (a paper napkin, about 3.5 m/s), 0.3 for rigid light shapes (a paper
           plate, about 3.6 m/s); an explicit wind_lift_mps wins
    slide  F > mu (M g - L) + secured_n       M = the item with everything on it (ballast counts)
    tip    F h / 2 > M g b / 2                 b = the narrower base side; secured items never tip

Wind at the item. The weather's wind_mps is the mean at 10 m over open ground (an airport normal) and gust_mps its
3-second gust. Near the ground in a built-up suburb both are far lower: the mean at about 1 m is 0.4 of the 10 m open
value (ASCE 7 power law, exposure B against C: b 0.45 / 0.65 x (1 m / 10 m)^(1/4) = 0.39) and the gust 0.6 of the 10 m
gust (turbulence intensity about 0.4 near the ground: a local peak factor of about 2.2 on that mean). An elevated
balcony is more exposed (mean 0.6, gust 0.75). A room's ``wind_exposure`` ([mean, gust]) overrides them. The sustained
(mean) wind slides items; a gust lifts sheets off and tips things over; a gust's brief shove of something that would
slide is not modelled (no trajectories), so light boxes and cans stay put on ordinary windy days.

Blown items (lifted, or slid off their support) move downwind by min(room edge, d) cells, d = 1 + 2.5 x excess m
(clamped to 0.5-12 m, outdoor_plants' displacement); they fall when they leave a support, show ``blown_away`` and
count once per move (stat ``phys_blown``, event ``phys.blown``); a light item keeps blowing while the wind lasts until
it rests against the room's downwind edge. Rain wets exposed items' surfaces to 1.0 within 120 s, soaks absorbent
ones at rain_mm_h x top area / absorb_l per hour (paper reaching moisture 0.3 is soaked), and fills open holders
through their mouth.

An item is exposed when its root room is outdoor and not roofed (a porch), it is not sheltered by a closed or covered
container, a cover or a table above it, and no ``phys.exposed`` hook shelters it. ``run(w, dt, tick)`` is the body of
the system ``physics.environment`` (order 47, max_dt 30), registered by PHYS-INT; it does nothing while both laws are
deferred, and nothing while the wind is below 2.5 m/s and no rain falls.
"""
import math
import weakref

from .. import geometry
from ..plugins import run_hooks
from ..predicates import root_room
from . import impact, lawbook, liquids, processes, props, support
from .processes import blame, dims, finite, num, top_area_m2


G = 9.81
RHO_AIR = 1.2                   # kg/m3 (5.10; 1.225 at 15 degC sea level)
CD_DEFAULT = 1.2                # a bluff body (flat plate broadside 1.17-1.28)
C_EFF_FLEXIBLE = 0.033          # flexible sheets peel up from an edge: a 4 g, 40 cm paper napkin lifts at 3.5 m/s
C_EFF_RIGID = 0.3               # rigid light shapes: a 13 g, 23 cm paper plate lifts at about 3.6 m/s
FLAT_RATIO = 0.1                # a sheet: height at most 10 % of its smaller footprint side
FLEXIBLE_M = 0.005              # sheets thinner than 5 mm flex (napkins, paper, cloth); thicker ones are rigid
SECURED_N = 400.0               # a tie-down, clip or strap without a stated rating (outdoor_plants' default)
MU_LOWEST = 0.1                 # no resting contact grips less (furniture sliders, 0.08, are feet, not items)
CASTER_CRR_LOWEST = 0.005       # free-rolling casters on the smoothest floor (the cheap bound for things on casters)
TYRE_FOOT = "rubber"            # floors.json feet kind of a wheel's tyre: rubber grips (floor mu x 2, capped at 1.0)
CALM_MPS = 2.5                  # below this wind nothing outdoors moves (6.3)
GROUND_EXPOSURE = (0.4, 0.6)    # (mean, gust) at about 1 m in a suburban yard, of the weather's 10 m open values
ROOM_EXPOSURE = {"balcony": (0.6, 0.75)}      # elevated, on the building's face
DEFAULT_GUST_FACTOR = 1.5       # a weather without gust_mps: gusts 1.5 x the mean (the generator's gust_factor)
BLOWN_MIN_M, BLOWN_MAX_M = 0.5, 12.0
RAIN_WET_S = 120.0              # exposed surfaces are fully wet after two minutes of rain
SOAKED_MOISTURE = 0.3           # paper and cardboard are soaked through at this moisture (5.10)
SOAKS_THROUGH = 0.9             # water sensitivity at and above which a soaked body is water-damaged
ROOFED_ROOM_TYPES = ("porch",)  # outdoor rooms under the house roof (outdoor_plants ROOFED_ROOMS; or "roofed": true)
SHELTER_RELATIONS = ("under", "behind", "attached", "part", "worn")
FIXED_RELATIONS = ("in", "under", "behind", "attached", "part", "worn", "hung")


# ----- weather ------------------------------------------------------------------------------------------------------

def wind_speed_mps(weather):
    """Wind speed objects respond to: the gust speed when it is higher than the mean wind."""
    weather = weather or {}
    return max(num(weather.get("wind_mps")), num(weather.get("gust_mps")))


def exposure(state, rid):
    """(mean, gust) factors from the weather's 10 m open-terrain wind to the wind at an item in outdoor room rid:
    the room's wind_exposure ([mean, gust]) when it states one, else ROOM_EXPOSURE by type, else GROUND_EXPOSURE."""
    room = state["rooms"].get(rid) or {}
    stated = room.get("wind_exposure")
    if isinstance(stated, (list, tuple)) and len(stated) == 2:
        mean, gust = finite(stated[0]), finite(stated[1])
        if mean is not None and gust is not None and mean >= 0 and gust >= 0:
            return mean, gust
    return ROOM_EXPOSURE.get(room.get("type"), GROUND_EXPOSURE)


def object_wind_mps(state, eid, weather=None):
    """(sustained, gust) wind speed in m/s at an outdoor item: the weather's mean and gust (gust_mps, else 1.5 x the
    mean) times its room's exposure. The gust is never below the sustained wind."""
    weather = state.get("weather") if weather is None else weather
    weather = weather or {}
    mean = max(0.0, num(weather.get("wind_mps")))
    gust = finite(weather.get("gust_mps"))
    gust = max(mean, mean * DEFAULT_GUST_FACTOR if gust is None else gust)
    factor_mean, factor_gust = exposure(state, root_room(state, eid))
    sustained = mean * factor_mean
    return sustained, max(sustained, gust * factor_gust)


def wind_direction_deg(weather):
    """Direction the wind blows toward, in grid degrees (0 = +x east, 90 = +y south), from the meteorological
    wind_from_deg (the compass direction it comes from; default west, i.e. blowing east)."""
    frm = num((weather or {}).get("wind_from_deg"), 270.0)
    return ((frm + 180.0) - 90.0) % 360.0


# rain rate (mm/h) of a weather that names its condition but gives no rain_mm_h (the setup weather op and
# generation-1 weather; generation-2 weather always states its rate): drizzle up to about 0.5 mm/h, light-to-moderate
# rain and sleet about 2.5 mm/h (the AMS light/moderate boundary), a storm at the generator's storm threshold
# (climate_normals storm_mm_h 15); snow wets nothing until it melts (adoption requests admin_inventory 2,
# textiles_laundry 4: the rain law rained nothing for condition "rain" without a rate)
RAIN_BY_CONDITION = {"drizzle": 0.5, "rain": 2.5, "showers": 2.5, "sleet": 2.5, "storm": 15.0, "thunderstorm": 15.0}


def rain_mm_h(weather):
    """The rain rate on things outdoors: the weather's rain_mm_h when positive, else its condition word's
    RAIN_BY_CONDITION rate (0 for clear, cloudy, snow or an unknown word). A rain word with a zero rate (a spec's
    condition merged over a dry generated day) rains at the word's rate."""
    weather = weather or {}
    stated = num(weather.get("rain_mm_h"))
    if stated > 0:
        return stated
    condition = weather.get("condition")
    return RAIN_BY_CONDITION.get(condition, 0.0) if isinstance(condition, str) else 0.0


# ----- forces -------------------------------------------------------------------------------------------------------

def _resting_dims(rec):
    w, d, h = dims(rec)
    if rec.get("orientation") == "on_side":
        if w >= d:
            w, h = h, w
        else:
            d, h = h, d
    return w, d, h


def frontal_area_m2(rec):
    """Area the wind meets: the wider footprint side x the height, as the item rests."""
    w, d, h = _resting_dims(rec)
    return max(w, d) * h


def drag_cd(rec):
    """Drag coefficient of an item over its outline: its drag_cd, else its supports.json row's (props: open-frame
    chairs and racks 0.7), else the bluff-body CD_DEFAULT."""
    explicit = finite(rec.get("drag_cd"))
    if explicit is not None and explicit > 0:
        return explicit
    row = finite(props.props_of(rec).drag_cd)
    return row if row is not None and row > 0 else CD_DEFAULT


def wind_force_n(rec, v):
    """Drag (N) on an item at wind speed v (m/s): 0.5 rho_air Cd A v^2."""
    return 0.5 * RHO_AIR * drag_cd(rec) * frontal_area_m2(rec) * v * v


def is_sheet(rec):
    """A flat sheet or a flat light shape the wind can get under (height <= 10 % of the smaller footprint side)."""
    w, d, h = _resting_dims(rec)
    return min(w, d) > 0 and h <= FLAT_RATIO * min(w, d)


def lift_coefficient(rec):
    """C_eff of the lift model: flexible sheets thinner than 5 mm peel up (0.033), rigid shapes lift whole (0.3)."""
    return C_EFF_FLEXIBLE if _resting_dims(rec)[2] < FLEXIBLE_M else C_EFF_RIGID


def lift_speed_mps(rec):
    """Wind speed (m/s) at which an item lifts off: its wind_lift_mps, else sqrt(2 g sigma / (rho_air C_eff)) for a
    sheet (sigma = mass per top area), else inf (it can only slide or tip)."""
    explicit = finite(rec.get("wind_lift_mps"))
    if explicit is not None:
        return explicit
    if not is_sheet(rec):
        return math.inf
    w, d, _ = _resting_dims(rec)
    sigma = max(0.0, num(rec.get("mass_kg"))) / max(1e-6, w * d)
    return math.sqrt(2.0 * G * sigma / (RHO_AIR * lift_coefficient(rec)))


def lift_force_n(rec, v):
    """Lift (N) on a sheet at wind speed v: its own weight times (v / v_lift)^2 (0 for non-sheets)."""
    v_lift = lift_speed_mps(rec)
    if not math.isfinite(v_lift) or v_lift <= 0:
        return 0.0
    return max(0.0, num(rec.get("mass_kg"))) * G * (v / v_lift) ** 2


def secured_n(rec):
    """Hold-down force (N) of a tie-down: secured {"rating_n": N} or 400 N; 0 when not secured."""
    value = rec.get("secured")
    if not value:
        return 0.0
    if isinstance(value, dict):
        return num(value.get("rating_n"), SECURED_N)
    return SECURED_N


# ----- exposure -----------------------------------------------------------------------------------------------------

def _sheltered_by(state, eid):
    """Something between eid and the sky: a closed or covered container it is in, a cover on it or on what holds it,
    or a relation that puts it under or inside something."""
    entities = state["entities"]
    current, seen = eid, set()
    while current in entities and current not in seen:
        seen.add(current)
        rec = entities[current]
        if rec.get("covered") or rec.get("covered_by"):
            return True
        relation = rec.get("relation", "on")
        if relation in SHELTER_RELATIONS:
            return True
        parent = rec.get("location")
        if relation == "in" and parent in entities and liquids.container_closed(entities[parent]):
            return True
        current = parent
    return False


def exposed(state, eid):
    """eid is out in the weather: its root room is outdoor and not roofed, nothing shelters it and no phys.exposed
    hook returns False (porch roofs, umbrellas, covers outdoor_plants knows about)."""
    rid = root_room(state, eid)
    room = state["rooms"].get(rid)
    if not room or not room.get("outdoor") or room.get("roofed") or room.get("type") in ROOFED_ROOM_TYPES:
        return False
    if _sheltered_by(state, eid):
        return False
    return not any(verdict is False for verdict in run_hooks("phys.exposed", state, eid))


# ----- wind ---------------------------------------------------------------------------------------------------------

def _free_to_move(state, eid):
    """Free-standing: resting on a support or the ground (not held, worn, hung, inside or fixed: fixed in place,
    mounted, anchored, rooted, a ground layer, a doorway or gate in a wall), and something that can move at all:
    portable, on wheels or casters, free-standing furniture, or a cover (statics.free_standing's rule). Fences,
    gates, doors, lawns, soil beds, walkways, drains, hedges, trees and installed equipment never blow away."""
    entities = state["entities"]
    rec = entities[eid]
    loc = rec.get("location")
    if rec.get("relation", "on") in FIXED_RELATIONS or rec.get("fixed_in_place") or rec.get("mounted") or \
            rec.get("anchored") or rec.get("rooted") or rec.get("ground_layer") or "connects" in rec:
        return False
    if not (rec.get("portable") or rec.get("wheeled") or rec.get("casters") or rec.get("category") == "furniture"
            or "furniture" in (rec.get("is_a") or ()) or _is_cover(rec)):
        return False
    return loc in entities or loc in state["rooms"]


def _is_cover(rec):
    """A cover laid over something (the wind gets under tarps and furniture covers)."""
    return rec.get("category") == "cover" or bool(rec.get("covering"))


def _downwind_cell(state, rid, cell, direction_deg, distance_m, eid=None):
    """Where a thing blown distance_m downwind of cell comes to rest: along the downwind line, the last cell before the
    room's edge, a doorway's clearance (geometry.door_clearance) or a path-blocking footprint (geometry.blocks; the
    thing itself and what it rests on excepted), else distance_m away. The cell itself when the first cell on the line
    is already one of those: it is pressed against it (phase 1, outdoor_plants' request: with the wind law active a
    blown thing came to rest on a doorway cell and cut every route through it)."""
    cols, rows = geometry.grid_size(state["rooms"][rid])
    rad = math.radians(direction_deg)
    steps = max(1, int(round(distance_m / geometry.CELL)))
    start = (int(cell[0]), int(cell[1]))
    entities = state["entities"]
    own = set()
    cur = eid
    while cur in entities and cur not in own:
        own.add(cur)
        cur = entities[cur].get("location")
    stops = None
    here, last = start, start
    for k in range(1, steps + 1):
        col = int(round(start[0] + math.cos(rad) * k))
        row = int(round(start[1] + math.sin(rad) * k))
        if not (0 <= col < cols and 0 <= row < rows):
            break
        nxt = (col, row)
        if nxt == last:
            continue                # the rounding repeats a cell on a diagonal line
        last = nxt
        if stops is None:
            stops = set(geometry.door_clearance(state, rid))
            for other, rec in entities.items():
                if other not in own and rec.get("location") == rid and geometry.blocks(rec):
                    stops.update(tuple(c) for c in geometry.footprint(rec))
        if nxt in stops:
            break
        here = nxt
    return [here[0], here[1]]


def contact_mu(state, eid):
    """Friction the wind has to overcome to slide eid. Something on wheels standing on a floor grips with its tyres:
    two-wheel bins and carts, barrows and hand trucks stand on rubber tyres plus feet or legs, so they skid only at
    rubber's friction on that floor (floors.json mu x the rubber foot factor, capped at 1.0) and a tall one blows over
    in a gust long before it would skid (wheelie bins blow over, they do not slide away); free-rolling casters (not
    chocked or braked) roll at the floor's c_rr. Anything else slides at its own material's friction (props mu)."""
    rec = state["entities"][eid]
    loc = rec.get("location")
    if loc in state["rooms"] and (rec.get("wheeled") or rec.get("casters")):
        floor = props.floor_props(state, loc, rec.get("pos"))
        if rec.get("casters") and not (rec.get("chocked") or rec.get("brakes_on") or rec.get("secured")):
            return max(CASTER_CRR_LOWEST, num(floor.get("c_rr"), 0.01))
        kinds = (props.FLOORS.get("feet") or {}).get("kinds") or {}
        factor = num((kinds.get(TYRE_FOOT) or {}).get("mu_factor"), 2.0)
        return min(1.0, num(floor.get("mu"), 0.5) * factor)
    return num(props.props(state, eid).mu, 0.5)


def _cannot_move(rec, v, gust=None):
    """A cheap bound before any property lookup: with its own mass only (the load on it can only add), the lowest
    friction and the largest drag coefficient it can have (supports.json rows only lower Cd below CD_DEFAULT),
    neither the gust can lift or tip it nor the sustained wind v slide it."""
    gust = v if gust is None else max(v, gust)
    mass = num(rec.get("mass_kg"))
    weight = mass * G
    if lift_force_n(rec, gust) > weight:
        return False
    cd = max(CD_DEFAULT, finite(rec.get("drag_cd")) or 0.0)
    area = 0.5 * RHO_AIR * cd * frontal_area_m2(rec)
    w_, d_, h_ = _resting_dims(rec)
    tip = weight * min(w_, d_) / h_ if h_ > 0 else math.inf
    lowest = CASTER_CRR_LOWEST if rec.get("casters") else MU_LOWEST
    return area * v * v <= lowest * (weight - lift_force_n(rec, v)) and area * gust * gust <= tip


def wind_effect(w, eid, v, direction_deg, by=None, cmap=None, gust_mps=None):
    """Apply wind blowing toward direction_deg (grid degrees) to a free-standing item; the caller decides exposure and
    passes speeds at the item (object_wind_mps): v the sustained wind (m/s), gust_mps its gust (ADDITION; default v).
    A gust whose lift beats weight plus tie-down blows it away; a gust that would tip it before it slides lays it on
    its side (impact.tip_over); a sustained wind that beats the smaller of the slide and tip forces slides it
    downwind (off its support) or tips it. cmap: a children map shared by a caller visiting many items. Returns
    {"effect": "lift" | "slide" | "tip" | None, "force_n", "lift_n", "resist_n", "moved_to"}."""
    state = w.s
    result = {"effect": None, "force_n": 0.0, "lift_n": 0.0, "resist_n": 0.0, "moved_to": None}
    gust = v if gust_mps is None else max(v, num(gust_mps))
    if eid not in state["entities"] or gust <= 0 or not _free_to_move(state, eid):
        return result
    rec = state["entities"][eid]
    if _cannot_move(rec, v, gust):
        return result
    mass = max(1e-6, num(support.subtree_mass(state, eid, cmap)))
    weight = mass * G
    drag = wind_force_n(rec, v)
    lift = lift_force_n(rec, v)
    gust_drag, gust_lift = wind_force_n(rec, gust), lift_force_n(rec, gust)
    hold = secured_n(rec)
    mu = contact_mu(state, eid)
    slide_resist = mu * max(0.0, weight - lift) + hold
    gust_resist = mu * max(0.0, weight - gust_lift) + hold
    w_, d_, h_ = _resting_dims(rec)
    lying = rec.get("orientation") in ("on_side", "upside_down", "flat")
    tip_force = math.inf if hold > 0 or h_ <= 0 or lying else weight * min(w_, d_) / h_
    result.update(force_n=round(drag, 3), lift_n=round(lift, 3), resist_n=round(slide_resist, 3))
    if gust_lift > weight + hold:
        effect, push = "lift", gust_drag + 0.6 * gust_lift
        result.update(force_n=round(gust_drag, 3), lift_n=round(gust_lift, 3), resist_n=round(gust_resist, 3))
    elif gust_drag > tip_force and tip_force < gust_resist:
        effect, push = "tip", gust_drag
        result.update(force_n=round(gust_drag, 3), lift_n=round(gust_lift, 3), resist_n=round(gust_resist, 3))
    elif drag > min(slide_resist, tip_force):
        effect, push = ("tip" if tip_force < slide_resist else "slide"), drag
    else:
        return result
    result["effect"] = effect
    if effect == "tip":
        impact.tip_over(w, eid, cause="wind", direction=direction_deg, by=by)
        return result
    rid, cell = processes.floor_cell(state, eid)
    excess = (push - slide_resist) / max(1.0, weight * mu)
    distance = min(BLOWN_MAX_M, max(BLOWN_MIN_M, 1.0 + 2.5 * excess))
    target = _downwind_cell(state, rid, cell or (0, 0), direction_deg, distance, eid)
    on_support = rec.get("location") in state["entities"]
    if not on_support and list(rec.get("pos") or ()) == target:
        result["effect"] = None             # already pressed against the room's downwind edge
        return result
    who = blame(w, eid, by)
    if on_support:
        impact.fall(w, eid, cause="wind", at=(rid, target), by=by)
    else:
        w.mut(eid)["pos"] = target
    if not state["entities"][eid].get("blown_away"):
        w.mut(eid)["blown_away"] = True
    w.stat("phys_blown")
    processes.consequence(w, "phys.blown", eid, who, "{} blew away".format(eid), ids=[eid], room=rid, cell=target,
                          wind_mps=round(v, 2), effect=effect, distance_m=round(distance, 2))
    result["moved_to"] = [rid, target]
    return result


# ----- rain ---------------------------------------------------------------------------------------------------------

def rain_wet(w, eid, mm_h, dt):
    """dt seconds of rain at mm_h on an exposed item: its surface wetness rises to 1.0 within 120 s; an absorbent body
    gains moisture at mm_h x top area / absorb_l per hour, and paper-like ones (water sensitivity >= 0.9) soaked to
    moisture 0.3 are water-damaged (MDF swells) once; an open holder collects the rain falling into its mouth.
    phys.wetted runs when the surface first gets fully wet and when the body soaks through."""
    mm_h, dt = num(mm_h), num(dt)
    if mm_h <= 0 or dt <= 0 or eid not in w.s["entities"]:
        return
    state = w.s
    rec = state["entities"][eid]
    p = props.props(state, eid)
    liters = mm_h * top_area_m2(rec) * dt / 3600.0          # 1 mm over 1 m2 is 1 L
    wetness = num(rec.get("wetness"))
    if wetness < 1.0:
        w.mut(eid)["wetness"] = round(min(1.0, wetness + dt / RAIN_WET_S), 4)
        if wetness + dt / RAIN_WET_S >= 1.0:
            run_hooks("phys.wetted", w, eid, round(liters, 4), "water", None)
    cap = liquids.absorb_capacity_l(p)
    if cap > 0:
        moisture = num(rec.get("moisture"))
        after = round(min(1.0, moisture + liters / cap), 4)
        if after > moisture:
            w.mut(eid)["moisture"] = after
        if moisture < SOAKED_MOISTURE <= after and num(p.wsens) >= SOAKS_THROUGH and not rec.get("soaked"):
            m = w.mut(eid)
            m["soaked"] = True
            kind = soak_damage_kind(p.cls)
            impact.damage(w, eid, kind, round(after, 4), detail="soaked by rain")
            run_hooks("phys.wetted", w, eid, round(liters, 4), "water", None)
    elif liquids.holds_liquid(state, eid, p):
        mouth = top_area_m2(rec) * liquids.mouth_frac(rec, p.mouth_frac)
        caught = mm_h * mouth * dt / 3600.0
        if caught > 0:
            liquids.pour_in(w, eid, caught, "water", source=None, by="exogenous")


def soak_damage_kind(cls):
    """Damage a soaking does to a material: MDF swells, everything else is water-damaged."""
    return "swollen" if cls == "engineered_wood" else "water_damage"


# ----- the physics.environment system -------------------------------------------------------------------------------

_OUTDOOR = weakref.WeakKeyDictionary()      # World -> (location fingerprint, ids in outdoor rooms)


def _holder_rooms(state):
    """Where the robot, the people and the pets are: what they hold is wherever they are."""
    return (state["robot"].get("room"),
            tuple((pid, p.get("location")) for pid, p in state.get("people", {}).items()),
            tuple((pid, p.get("location")) for pid, p in state.get("pets", {}).items()))


def outdoor_ids(w):
    """Sorted ids of the entities whose root room is outdoor (not ground layers or puddles). Recomputed only when an
    entity is created, destroyed or moved, or a holder (robot, person, pet) changes room: the fingerprint is every
    entity's location plus where the holders are, so the result is always a pure function of the state and replays
    and loaded states see the same set."""
    state = w.s
    entities, rooms = state["entities"], state["rooms"]
    fingerprint = (tuple(entities), tuple(rec.get("location") for rec in entities.values()), _holder_rooms(state))
    cached = _OUTDOOR.get(w)
    if cached is not None and cached[0] == fingerprint:
        return cached[1]
    outdoor = set(rid for rid, room in rooms.items() if room.get("outdoor"))
    ids = []
    if outdoor:
        for eid in sorted(entities):
            rec = entities[eid]
            if rec.get("ground_layer") or rec.get("category") in ("spill", "floor_surface"):
                continue
            if root_room(state, eid) in outdoor:
                ids.append(eid)
    _OUTDOOR[w] = (fingerprint, ids)
    return ids


def run(w, dt, tick):
    """System physics.environment (registered by PHYS-INT: order 47, max_dt 30). While the wind law is active and the
    weather's wind (gusts included) is at least 2.5 m/s, every exposed free-standing item (wind_effect's own gate:
    portable, on wheels or casters, free-standing furniture, or a cover) feels the wind at its height and shelter
    (object_wind_mps); while the rain law is active and rain falls, every exposed item gets wet. Ids in sorted order; an
    item blown this substep keeps moving each substep until it rests against the room's downwind edge."""
    wind_on = bool(lawbook.active("wind"))
    rain_on = bool(lawbook.active("rain"))
    if not (wind_on or rain_on):
        return
    weather = w.s.get("weather") or {}
    speed = wind_speed_mps(weather) if wind_on else 0.0
    rain = rain_mm_h(weather) if rain_on else 0.0
    if speed < CALM_MPS and rain <= 0:
        return
    direction = wind_direction_deg(weather)
    entities = w.s["entities"]
    cmap = None
    for eid in list(outdoor_ids(w)):
        rec = entities.get(eid)
        if rec is None:
            continue
        windy = False
        if speed >= CALM_MPS and _free_to_move(w.s, eid):
            sustained, gust = object_wind_mps(w.s, eid, weather)
            windy = not _cannot_move(rec, sustained, gust)
        if not (windy or rain > 0) or not exposed(w.s, eid):
            continue
        if windy:
            if cmap is None:
                cmap = support.children_map(w.s)
            wind_effect(w, eid, sustained, direction, cmap=cmap, gust_mps=gust)
        if rain > 0 and eid in entities:
            rain_wet(w, eid, rain, dt)
