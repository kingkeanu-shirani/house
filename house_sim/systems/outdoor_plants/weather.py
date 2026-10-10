"""Weather and its effects on outdoor objects (fidelity P-approx).

State: state["weather"] (see common.py). Scenario events change it:
    {"op": "event", "at_s": 3600, "effect": "weather", "args": {"condition": "rain", "rain_mm_h": 6}}
Effects also registered: start_rain, stop_rain, wind, storm, calm, freeze, thaw, snowfall,
heat_wave, outdoor_plants.weather, outdoor_plants.nudge, outdoor_plants.spawn.

Physics per 30 s substep while active:
* Rain wets exposed objects (fabric soaks after ~2 mm; paper/electronics are ruined),
  collects as pooled water on flat unpropped covers, accumulates rain totals.
* Wind: drag F = 0.5 rho v^2 Cd A and lift on flat light objects versus friction
  mu m g; unsecured light objects slide/tumble downwind (distance grows with the
  excess force), open umbrellas tip when the canopy moment exceeds the base +
  table restoring moment, unfastened covers lift off, uncovered leaf piles spread.
* Snow accumulates on exposed ground surfaces; at or below 0 C wet surfaces glaze to ice.
Slow ticks (600 s): drying (sun/wind/temperature dependent), mildew in wet enclosed
textiles, snow melt, rain-total decay.
"""
import copy
import math

from ...kernel.plugins import after, hook, observer, system
from . import common as C

DEFAULTS = {"condition": "clear", "temperature_c": 18.0, "wind_mps": 2.0, "wind_from_deg": 270.0, "rain_mm_h": 0.0,
            "snow_cm_h": 0.0, "uv_index": 4, "humidity": 0.55}
RAIN_DEFAULT = {"rain": 4.0, "storm": 15.0}
FIELDS = ("condition", "temperature_c", "wind_mps", "gust_mps", "wind_from_deg", "rain_mm_h", "snow_cm_h", "uv_index",
          "humidity", "forecast", "alerts", "rain_24h_mm", "rain_7d_mm", "dry_days")


# ---------------------------------------------------------------------------
# events

def apply_weather(w, args):
    wx = w.sec("weather")
    for key, value in sorted((args or {}).items()):
        if key in FIELDS or key.startswith("forecast"):
            wx[key] = copy.deepcopy(value)
    cond = wx.get("condition", "clear")
    if "rain_mm_h" not in (args or {}):
        if cond in RAIN_DEFAULT and float(wx.get("rain_mm_h", 0.0)) <= 0.0:
            wx["rain_mm_h"] = RAIN_DEFAULT[cond]
        elif cond not in RAIN_DEFAULT:
            wx["rain_mm_h"] = 0.0
    if "snow_cm_h" not in (args or {}) and cond != "snow":
        wx["snow_cm_h"] = 0.0
    if cond == "snow" and float(wx.get("snow_cm_h", 0.0)) <= 0.0 and "snow_cm_h" not in (args or {}):
        wx["snow_cm_h"] = 1.5
    if "wind_mps" in (args or {}) and "gust_mps" not in (args or {}):
        wx["gust_mps"] = round(float(wx["wind_mps"]) * 1.5, 2)
    w.emit("weather_changed", condition=cond, rain_mm_h=wx.get("rain_mm_h"), gust_mps=wx.get("gust_mps"))
    return True


@hook("event:weather")
def _ev_weather(w, args, env):
    return apply_weather(w, args)


@hook("event:outdoor_plants.weather")
def _ev_op_weather(w, args, env):
    return apply_weather(w, args)


@hook("event:start_rain")
def _ev_start_rain(w, args, env):
    args = dict(args or {})
    args.setdefault("condition", "storm" if float(args.get("rain_mm_h", 4.0)) >= 12 else "rain")
    args.setdefault("rain_mm_h", 4.0)
    return apply_weather(w, args)


@hook("event:stop_rain")
def _ev_stop_rain(w, args, env):
    args = dict(args or {})
    args.setdefault("condition", "cloudy")
    args["rain_mm_h"] = 0.0
    return apply_weather(w, args)


@hook("event:wind")
def _ev_wind(w, args, env):
    args = dict(args or {})
    args.setdefault("wind_mps", 9.0)
    args.setdefault("gust_mps", round(float(args["wind_mps"]) * 1.6, 2))
    args.setdefault("condition", "wind")
    return apply_weather(w, args)


@hook("event:storm")
def _ev_storm(w, args, env):
    args = dict(args or {})
    args.setdefault("condition", "storm")
    args.setdefault("rain_mm_h", 15.0)
    args.setdefault("wind_mps", 12.0)
    args.setdefault("gust_mps", 22.0)
    return apply_weather(w, args)


@hook("event:calm")
def _ev_calm(w, args, env):
    args = dict(args or {})
    args.setdefault("condition", "clear")
    args.setdefault("wind_mps", 1.5)
    args.setdefault("gust_mps", 3.0)
    args.setdefault("rain_mm_h", 0.0)
    return apply_weather(w, args)


@hook("event:freeze")
def _ev_freeze(w, args, env):
    args = dict(args or {})
    args.setdefault("temperature_c", -4.0)
    return apply_weather(w, args)


@hook("event:thaw")
def _ev_thaw(w, args, env):
    args = dict(args or {})
    args.setdefault("temperature_c", 6.0)
    return apply_weather(w, args)


@hook("event:snowfall")
def _ev_snow(w, args, env):
    args = dict(args or {})
    args.setdefault("condition", "snow")
    args.setdefault("snow_cm_h", 2.0)
    args.setdefault("temperature_c", min(0.0, float(args.get("temperature_c", -2.0))))
    return apply_weather(w, args)


@hook("event:heat_wave")
def _ev_heat(w, args, env):
    args = dict(args or {})
    args.setdefault("condition", "clear")
    args.setdefault("temperature_c", 36.0)
    args.setdefault("uv_index", 10)
    return apply_weather(w, args)


@hook("event:outdoor_plants.nudge")
def _ev_nudge(w, args, env):
    """Perturbation test: push on stacks/structures (force_n) to check stability."""
    from . import patio
    patio.perturb(w, args or {})
    return True


@hook("event:outdoor_plants.spawn")
def _ev_spawn(w, args, env):
    """Spawn storm debris mid-episode: args {type, location, props, count}."""
    args = dict(args or {})
    count = int(args.get("count", 1))
    for _ in range(count):
        rid = args["location"]
        props = copy.deepcopy(args.get("props", {}))
        if rid in w.s["rooms"] and "pos" not in props:
            props["pos"] = C.drop_cell(w, rid, near=args.get("near"))
        C.spawn(w, args["type"], rid, **props)
    return True


# ---------------------------------------------------------------------------
# observation: forecast (weather app over the network, or seen outdoors)

@observer("outdoor_plants.forecast", order=60)
def forecast_observer(env, w, obs):
    state = w.s
    robot = state["robot"]
    room = state["rooms"].get(robot["room"], {})
    if not (robot.get("network", True) or room.get("outdoor")):
        return
    wx = state.get("weather", {}) or {}
    if C.env_realistic(env) and not robot.get("network", True):
        # realistic perception without the weather app: no forecast, only what being outdoors shows
        obs["weather_now"] = sky_now(wx)
        return
    now = float(state.get("time_s", 0.0))
    items = []
    for item in wx.get("forecast", []) or []:
        t = float(item.get("t_s", now))
        hours = float(item.get("hours", 1.0))
        if t + hours * 3600 < now:
            continue
        entry = {k: copy.deepcopy(v) for k, v in item.items() if k != "t_s" and not str(k).startswith("_")}
        entry["in_h"] = round((t - now) / 3600.0, 2)
        items.append(entry)
    obs["forecast"] = items
    obs["weather_now"] = {k: copy.deepcopy(wx.get(k)) for k in ("condition", "temperature_c", "wind_mps", "gust_mps", "rain_mm_h",
                                                                  "snow_cm_h", "uv_index", "rain_24h_mm", "rain_7d_mm", "alerts") if k in wx}


def sky_now(wx):
    """What a robot standing outdoors perceives of the weather (no numbers): sky, precipitation, how warm it feels,
    the wind as a Beaufort-style word and whether it gusts."""
    wind = float(wx.get("wind_mps", 0.0) or 0.0)
    gust = float(wx.get("gust_mps", wind) or wind)
    out = {"condition": wx.get("condition"), "temperature": C.temp_feel(float(wx.get("temperature_c", 15.0) or 15.0)),
           "wind": "calm" if wind < 1.5 else "light breeze" if wind < 5.5 else "windy" if wind < 10.8 else "gale",
           "gusty": gust >= wind + 3.0}
    if float(wx.get("rain_mm_h", 0.0) or 0.0) > 0:
        out["precipitation"] = "heavy rain" if float(wx["rain_mm_h"]) >= 4.0 else "rain"
    elif float(wx.get("snow_cm_h", 0.0) or 0.0) > 0:
        out["precipitation"] = "snow"
    return out


def forecast_window(w, hours, key="rain_mm"):
    """Sum/max of a forecast field over the next `hours` hours (used by helpers and skills)."""
    wx = w.s.get("weather", {}) or {}
    now = float(w.s.get("time_s", 0.0))
    total, peak = 0.0, 0.0
    for item in wx.get("forecast", []) or []:
        t = float(item.get("t_s", now))
        if now - 3600 * float(item.get("hours", 1.0)) <= t <= now + hours * 3600:
            value = float(item.get(key, 0.0) or 0.0)
            total += value
            peak = max(peak, value)
    return total, peak


# ---------------------------------------------------------------------------
# wind physics

def wind_effects(w, dt):
    g = C.gust(w)
    if g < 6.0:
        return
    vx, vy = C.wind_vector(w)
    state = w.s
    rooms_of = C.outdoor_map(w)
    if not rooms_of:
        return
    kids = {}
    for eid in sorted(rooms_of):
        loc = state["entities"].get(eid, {}).get("location")
        kids.setdefault(loc, []).append(eid)
    for eid in sorted(rooms_of):
        record = state["entities"].get(eid)
        if record is None:
            continue
        kind = None
        if C.is_type(record, "patio_umbrella"):
            kind = "umbrella"
        elif C.is_type(record, "furniture_cover") and record.get("covers"):
            kind = "cover"
        elif record.get("category") == "plant_cover" and record.get("covers"):
            kind = "plant_cover"
        elif C.is_type(record, "leaf_pile"):
            kind = "pile"
        elif record.get("portable") or C.is_type(record, "chair") or C.is_type(record, "table"):
            kind = "object" if record.get("movable", True) else None
        if kind is None:
            continue
        rid = rooms_of.get(eid)
        if rid is None or C.is_roofed(w, rid) and g < 15.0:
            continue
        if kind == "umbrella":
            _umbrella_wind(w, eid, g)
        elif kind == "cover":
            _cover_wind(w, eid, g)
        elif kind == "pile":
            _pile_wind(w, eid, g, dt)
        elif kind == "plant_cover":
            _plant_cover_wind(w, eid, g, kids)
        elif g >= 8.0:
            _object_wind(w, eid, rid, g, (vx, vy), kids)


def _current_kids(w, kids, eid):
    """Children of eid from the outdoor children map, re-checked against current locations."""
    ents = w.s["entities"]
    return [c for c in kids.get(eid, ()) if c in ents and ents[c].get("location") == eid]


def _subtree_mass(w, kids, eid):
    """core.subtree_mass without walking every entity: descendants of an outdoor object are outdoors too."""
    ents = w.s["entities"]
    total, stack, seen = 0.0, [eid], set()
    while stack:
        cur = stack.pop()
        if cur in seen or cur not in ents:
            continue
        seen.add(cur)
        rec = ents[cur]
        total += float(rec.get("mass_kg", 0.0)) + float(rec.get("liquid_l", 0.0)) * float(rec.get("liquid_density", 1.0))
        stack.extend(_current_kids(w, kids, cur))
    return total


def _secured_rating_n(record):
    sec = record.get("secured")
    if not sec:
        return 0.0
    if isinstance(sec, dict):
        return float(sec.get("rating_n", 400.0))
    return 400.0


def _object_wind(w, eid, rid, g, vec, kids=None):
    state = w.s
    record = state["entities"][eid]
    if record.get("location") == "robot" or record.get("relation") in ("in", "part", "attached", "held", "hung", "under", "behind"):
        if record.get("relation") != "hung":
            return
    if C.enclosed(w, eid) or C.covering(w, eid):
        return
    if record.get("blown_away"):
        return
    if kids is None:
        from ..core import subtree_mass
        mass = max(0.05, subtree_mass(w, eid))
        children = w.children(eid)
    else:
        mass = max(0.05, _subtree_mass(w, kids, eid))
        children = _current_kids(w, kids, eid)
    size = record.get("size_m") or [0.1, 0.1, 0.1]
    w_m, d_m, h_m = float(size[0]), float(size[1]), float(size[2]) if len(size) > 2 else 0.1
    drag = C.drag_force(g, max(w_m, d_m) * h_m, 1.1)
    lift = C.drag_force(g, w_m * d_m, 0.5) if h_m < 0.25 else 0.0
    loc = record.get("location")
    mu = 0.45 if loc in state["entities"] else 0.55
    if loc in state["entities"]:
        parent = state["entities"][loc]
        if C.is_type(parent, "chair") or C.is_type(parent, "outdoor_chair") or C.is_type(parent, "sofa"):
            mu = 0.6
    resist = mu * max(0.0, mass * C.G - lift) + _secured_rating_n(record)
    weight_held = sum(float(state["entities"][c].get("mass_kg", 0.0)) for c in children if state["entities"][c].get("ballast"))
    resist += weight_held * C.G * mu
    force = drag + lift * 0.6
    if force <= resist or (lift > mass * C.G * 0.98 and record.get("tied")):
        return
    excess = (force - resist) / max(1.0, mass * C.G * mu)
    distance = C.clamp(1.0 + 2.5 * excess, 0.5, 12.0)
    _displace(w, eid, rid, vec, distance, mass)


def _displace(w, eid, rid, vec, distance, mass):
    from ...kernel import geometry
    state = w.s
    record = state["entities"][eid]
    x, y = C.center_m(w, eid)
    room = state["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    nx, ny = x + vec[0] * distance, y + vec[1] * distance
    lim_x, lim_y = cols * C.CELL - 0.25, rows * C.CELL - 0.25
    hit_edge = nx < 0.25 or ny < 0.25 or nx > lim_x or ny > lim_y
    nx, ny = C.clamp(nx, 0.25, lim_x), C.clamp(ny, 0.25, lim_y)
    moved = math.hypot(nx - x, ny - y)
    on_something = record.get("location") in state["entities"]
    if moved < 0.25 and not on_something:
        return
    moved = max(moved, 0.5 if on_something else moved)
    cell = [int(nx // C.CELL), int(ny // C.CELL)]
    for child in w.descendants(eid):
        cm = w.mut(child)
        cm["_wind_moved_m"] = round(float(cm.get("_wind_moved_m", 0.0)) + moved, 2)
        if cm["_wind_moved_m"] > 1.0 and not cm.get("_counted_blown"):
            cm["_counted_blown"] = True
            C.stat(w, "blown_away")
    m = w.mut(eid)
    m["location"], m["relation"], m["pos"] = rid, "on", cell
    for key in ("spos", "shelf"):
        m.pop(key, None)
    m["_wind_moved_m"] = round(float(m.get("_wind_moved_m", 0.0)) + moved, 2)
    C.stat(w, "wind_displaced")
    if m["_wind_moved_m"] > 1.0 and not m.get("_counted_blown"):
        m["_counted_blown"] = True
        C.stat(w, "blown_away")
    edge = C.house_wall_edge(w, rid)
    if hit_edge and mass >= 1.0 and C.distance_to_edge_m(room, edge, (nx, ny)) < 0.6:
        C.stat(w, "wind_impacts")
        C.note(w, "wind_impact", target=eid, room=rid)
    if record.get("fragile") and mass >= 0.3:
        m["broken"] = True
        C.stat(w, "breakages")
    C.note(w, "blown", target=eid, distance_m=round(moved, 2))


def umbrella_tip_check(w, eid, g):
    """Returns (tips, moment_wind_Nm, moment_resist_Nm) for an umbrella at gust g (m/s).

    Canopy drag acts ~2 m up (open, Cd 0.25 + 0.025/deg of tilt over a 2.7 m canopy), a closed
    strapped canopy is a 0.25 m2 bluff body; the base (+ballast) resists with m g r and a table it
    passes through adds m_table g half-width.
    """
    from . import patio
    state = w.s
    record = state["entities"][eid]
    open_ = bool(record.get("open"))
    strapped = bool(record.get("strapped"))
    canopy = math.pi * (float(record.get("canopy_d_m", 2.7)) / 2.0) ** 2
    tilt = float((record.get("settings") or {}).get("tilt_deg", 0))
    if open_:
        area, cd, h = canopy, 0.25 + 0.025 * tilt, 2.0
    elif strapped:
        area, cd, h = 0.25, 1.2, 1.4
    else:
        area, cd, h = 0.6, 1.2, 1.5
    moment = C.drag_force(g, area, cd) * h
    base = patio.umbrella_base_of(w, eid)
    if base:
        brec = state["entities"][base]
        resist = patio.ballast_kg(w, base) * C.G * float(brec.get("radius_m", 0.25))
        table = patio.table_of_base(w, base)
        if table:
            trec = state["entities"][table]
            half = min(float(v) for v in (trec.get("size_m") or [1.0, 1.0])[:2]) / 2.0
            resist += float(trec.get("mass_kg", 20.0)) * C.G * half
        return moment > resist, moment, resist
    if record.get("orientation") == "flat" or record.get("relation") == "in" or C.enclosed(w, eid) or record.get("location") == "robot":
        return False, moment, 1e9
    resist = float(record.get("mass_kg", 8.5)) * C.G * 0.1
    return moment > resist, moment, resist


def _umbrella_wind(w, eid, g):
    record = w.s["entities"][eid]
    if record.get("tipped") or record.get("location") == "robot":
        return
    if not record.get("open") and not record.get("strapped") and g >= 12.0:
        m = w.mut(eid)
        m["open"] = True
        m["fabric_torn"] = True
        C.note(w, "umbrella_whipped_open", target=eid)
    tips, moment, resist = umbrella_tip_check(w, eid, g)
    if not tips:
        return
    m = w.mut(eid)
    m["tipped"] = True
    m["pole_bent"] = True
    C.stat(w, "tip_overs")
    C.stat(w, "wind_damage")
    base = record.get("in_base")
    if base and base in w.s["entities"]:
        table = w.s["entities"][base].get("under_table")
        if table and table in w.s["entities"]:
            trec = w.s["entities"][table]
            tm = w.mut(table)
            tm["tipped"] = True
            if trec.get("material") == "glass" or trec.get("glass_top"):
                tm["broken"] = True
                C.stat(w, "breakages")
    C.note(w, "umbrella_tipped", target=eid, moment=round(moment, 1), resist=round(resist, 1))


def _cover_wind(w, eid, g):
    record = w.s["entities"][eid]
    if record.get("fastened") or record.get("anchored"):
        return
    area = 1.5
    target = record.get("covers")
    if target and target in w.s["entities"]:
        size = w.s["entities"][target].get("size_m") or [1.0, 1.0, 1.0]
        area = float(size[0]) * float(size[1])
    lift = C.drag_force(g, area, 0.6)
    weight = (float(record.get("mass_kg", 1.0)) + float(record.get("pooled_water_l", 0.0))) * C.G
    if lift <= weight + 15.0:
        return
    from . import patio
    patio.remove_cover(w, eid, reason="wind")
    rid = C.room_of(w, eid)
    if rid:
        _displace(w, eid, rid, C.wind_vector(w), 6.0, 1.0)
    C.stat(w, "covers_blown_off")


def _plant_cover_wind(w, eid, g, kids=None):
    """Frost cloth / sheeting over a bed or plant: lifts off unless pinned, staked or weighted."""
    record = w.s["entities"][eid]
    children = w.children(eid) if kids is None else _current_kids(w, kids, eid)
    if record.get("pinned") or record.get("fastened") or record.get("staked") or any(w.s["entities"][c].get("ballast") for c in children):
        return
    target = record.get("covers")
    size = (w.s["entities"].get(target) or {}).get("size_m") or [1.0, 1.0]
    lift = C.drag_force(g, float(size[0]) * float(size[1]), 0.5)
    if lift <= float(record.get("mass_kg", 0.3)) * C.G + 5.0:
        return
    from . import garden
    garden.uncover_bed(w, eid)
    rid = C.room_of(w, eid)
    m = w.mut(eid)
    if m.get("relation") in ("on", "over") and rid:
        m["location"], m["relation"] = rid, "on"
        m["pos"] = list(m.get("pos") or [0, 0])
        m.pop("spos", None)
        _displace(w, eid, rid, C.wind_vector(w), 6.0, 1.0)
    C.stat(w, "covers_blown_off")
    C.note(w, "plant_cover_blown_off", cover=eid, bed=target)


def _pile_wind(w, eid, g, dt=30.0):
    record = w.s["entities"][eid]
    if g < 7.0 or C.covering(w, eid) or float(record.get("wetness", 0.0)) > 0.6:
        return
    kg = float(record.get("leaves_kg", 0.0)) + float(record.get("clippings_kg", 0.0))
    if kg <= 0.05:
        return
    # 15 % of the pile per 30 s per 6 m/s of gust above 6 m/s, scaled by the substep length
    loss = min(kg, kg * 0.15 * (g - 6.0) / 6.0 * min(1.0, float(dt) / 30.0))
    frac = loss / kg
    m = w.mut(eid)
    m["leaves_kg"] = round(float(m.get("leaves_kg", 0.0)) * (1 - frac), 3)
    m["clippings_kg"] = round(float(m.get("clippings_kg", 0.0)) * (1 - frac), 3)
    m["mass_kg"] = round(m["leaves_kg"] + m["clippings_kg"], 3)
    from . import lawn
    lawn.scatter_leaves(w, eid, loss)


# ---------------------------------------------------------------------------
# rain, snow, freezing

def precip_effects(w, dt):
    wx = w.s.get("weather", {}) or {}
    rain = float(wx.get("rain_mm_h", 0.0))
    snow = float(wx.get("snow_cm_h", 0.0))
    if rain <= 0.0 and snow <= 0.0:
        return
    mm = rain * dt / 3600.0
    cm = snow * dt / 3600.0
    sec = w.sec("weather")
    if mm > 0:
        sec["rain_period_mm"] = round(float(sec.get("rain_period_mm", 0.0)) + mm, 4)
        sec["rain_24h_mm"] = round(float(sec.get("rain_24h_mm", 0.0)) + mm, 3)
        sec["rain_7d_mm"] = round(float(sec.get("rain_7d_mm", 0.0)) + mm, 3)
        sec["dry_days"] = 0
    if cm > 0:
        sec["snow_period_cm"] = round(float(sec.get("snow_period_cm", 0.0)) + cm, 4)
    state = w.s
    rooms_of = C.outdoor_map(w)
    exposed_rooms = {rid for rid in set(rooms_of.values()) if not C.is_roofed(w, rid)}
    for eid in sorted(e for e, rid in rooms_of.items() if rid in exposed_rooms):
        record = state["entities"].get(eid)
        if record is None:
            continue
        if record.get("ground_layer") and not (C.is_type(record, "walkway") or record.get("category") == "floor"):
            continue
        exposure = C.rain_exposed(w, eid)
        if exposure <= 0:
            continue
        if record.get("category") == "floor" or C.is_type(record, "walkway"):
            m = w.mut(eid)
            if mm > 0:
                m["wetness"] = round(min(1.0, float(m.get("wetness", 0.0)) + mm * 0.5), 3)
            if cm > 0:
                m["snow_cm"] = round(float(m.get("snow_cm", 0.0)) + cm * exposure, 3)
                m.setdefault("snow_density", 100.0)
            continue
        if C.is_type(record, "plant") or C.is_type(record, "plant_pot") or C.is_type(record, "lawn") or C.is_type(record, "garden_bed"):
            continue  # soil water handled by plants/lawn systems from rain_period_mm
        if C.is_type(record, "furniture_cover") and record.get("covers") and mm > 0:
            _cover_pooling(w, eid, mm)
        if C.is_type(record, "plant_saucer") and mm > 0:
            continue
        if record.get("plug_required") and not own_drying(record) and C.weather_rated(record):
            continue  # sealed outdoor-rated mains equipment (robot mower dock, irrigation controller...) sheds rain
        soak_mm = 4.0 if record.get("quick_dry") else 2.0
        gain = exposure * (mm + cm * 3.0) / soak_mm
        if gain <= 0:
            continue
        before = float(record.get("wetness", 0.0))
        after = round(min(1.0, before + gain), 3)
        if after == before:
            continue
        m = w.mut(eid)
        m["wetness"] = after
        m["_wx_wet"] = True
        if own_drying(record):
            m["_wet_mine"] = after  # single-sourced drying: generic evaporation models skip records marked _wet_mine
            if m.get("drying_owner") is None:
                m["drying_owner"] = "outdoor_plants"
        if C.water_sensitive(record) and record.get("category") not in ("cushion",) and not record.get("fabric_item") and after >= 0.3 and not m.get("ruined"):
            if str(record.get("material")) not in ("polyester_foam", "nylon", "polyester", "cotton", "wool", "fabric") or record.get("plug_required") or record.get("category") in ("electronics", "book", "document", "phone", "laptop", "artwork"):
                m["ruined"] = True
                m["broken"] = True
                C.stat(w, "weather_damage")
                C.note(w, "rain_ruined", target=eid)


def _cover_pooling(w, eid, mm):
    record = w.s["entities"][eid]
    if record.get("propped") or record.get("vented_dome"):
        return
    target = record.get("covers")
    trec = w.s["entities"].get(target, {})
    flat = C.is_type(trec, "table") or C.is_type(trec, "pool") or trec.get("category") in ("pool", "grill") or "table" in str(trec.get("type", ""))
    if not flat:
        return
    size = trec.get("size_m") or [1.0, 1.0]
    area = float(size[0]) * float(size[1])
    retain = 0.5 if C.is_type(trec, "pool") else 0.25
    cap = 2000.0 if C.is_type(trec, "pool") else 40.0
    m = w.mut(eid)
    m["pooled_water_l"] = round(min(cap, float(m.get("pooled_water_l", 0.0)) + mm * area * retain), 3)
    if m["pooled_water_l"] >= cap * 0.98 and not C.is_type(trec, "pool") and not m.get("torn"):
        m["torn"] = True
        C.stat(w, "cover_damage")
        C.note(w, "cover_torn_by_pooling", target=eid)


# ---------------------------------------------------------------------------
# slow tick: totals, drying, mildew, freezing, melting

OWN_DRYING = ("outdoor_cushion", "furniture_cover", "grill_cover", "pool_cover", "tarp", "pool_float", "pool_toy", "life_jacket", "ring_buoy",
              "outdoor_decoration", "inflatable_decoration", "lawn_sign", "leaf_bag", "plastic_yard_bag", "frost_cloth", "shade_cloth",
              "plastic_sheet", "garden_gloves", "deck_box", "firewood_log", "garden_tool", "garden_product", "garden_gnome", "sand_bag")


def own_drying(record):
    return any(C.is_type(record, t) for t in OWN_DRYING)


_GENERIC = []


def generic_drying_present():
    """True when another loaded domain runs a generic evaporation system (cleaning.drying)."""
    if not _GENERIC:
        from ...kernel.plugins import SYSTEMS
        _GENERIC.append(any(s[1] == "cleaning.drying" for s in SYSTEMS))
    return _GENERIC[0]


def _surface_like(record):
    return record.get("category") == "floor" or C.is_type(record, "walkway")


def slow_candidates(w):
    """Entities the slow weather tick can change: this domain's own-drying types and ground surfaces (static
    indexes). Without a generic drying domain loaded it also dries foreign things outdoors or rained on."""
    ents = w.s["entities"]
    ids = set(C.static_ids(w, "own_drying", own_drying))
    ids.update(C.static_ids(w, "surfaces", _surface_like))
    if not generic_drying_present():
        ids.update(C.outdoor_map(w))
        ids.update(e for e, r in ents.items() if r.get("_wx_wet"))
    return sorted(e for e in ids if e in ents)


def foreign_dried(record):
    """Another domain owns this record's drying (conventions 5: drying_owner / _wet_mine)."""
    owner = record.get("drying_owner")
    return (owner is not None and owner != "outdoor_plants") or (record.get("_wet_mine") is not None and not own_drying(record))


def slow_weather(w, periods):
    dt = C.SLOW_S * periods
    wx = w.s.get("weather", {}) or {}
    sec = w.sec("weather")
    sec["rain_last_period_mm"] = round(float(sec.get("rain_period_mm", 0.0)), 4)
    sec["rain_period_mm"] = 0.0
    sec["snow_last_period_cm"] = round(float(sec.get("snow_period_cm", 0.0)), 4)
    sec["snow_period_cm"] = 0.0
    for key, tau in (("rain_24h_mm", 86400.0), ("rain_7d_mm", 7 * 86400.0)):
        if float(sec.get(key, 0.0)) > 0:
            sec[key] = round(float(sec[key]) * math.exp(-dt / tau), 3)
    t_air = float(wx.get("temperature_c", 18.0))
    raining = float(wx.get("rain_mm_h", 0.0)) > 0.05
    sun = C.daylight(w)
    wind = float(wx.get("wind_mps", 2.0))
    state = w.s
    for eid in slow_candidates(w):
        record = state["entities"][eid]
        wet = float(record.get("wetness", 0.0) or 0.0)
        snow = float(record.get("snow_cm", 0.0) or 0.0)
        ice = float(record.get("ice", 0.0) or 0.0)
        if wet <= 0.001 and snow <= 0.001 and ice <= 0.001:
            if record.get("_mildew_h"):
                w.mut(eid).pop("_mildew_h", None)
            continue
        rid = C.room_of(w, eid)
        room = state["rooms"].get(rid, {})
        outdoor = bool(room.get("outdoor"))
        if not (outdoor or record.get("_wx_wet") or any(C.is_type(record, t) for t in OWN_DRYING)):
            continue  # indoor drying of other domains' things is theirs
        box = C.enclosed(w, eid)
        m = None
        # freezing / melting of surfaces
        if record.get("category") == "floor" or C.is_type(record, "walkway"):
            m = w.mut(eid)
            if outdoor and t_air <= 0.0 and wet > 0.05:
                m["ice"] = round(max(ice, min(1.0, wet)), 3)
                m["wetness"] = 0.0
            if outdoor and t_air > 0.5 and snow > 0:
                melt = min(snow, 0.25 * t_air * dt / 3600.0)
                m["snow_cm"] = round(snow - melt, 3)
                m["wetness"] = round(min(1.0, float(m.get("wetness", 0.0)) + melt * 0.2), 3)
            if outdoor and t_air > 1.0 and ice > 0 and not m.get("deicer"):
                m["ice"] = round(max(0.0, ice - 0.15 * t_air * dt / 3600.0), 3)
            continue
        if C.is_type(record, "plant") or C.is_type(record, "lawn") or C.is_type(record, "garden_bed") or C.is_type(record, "plant_pot"):
            continue
        mine = own_drying(record)
        if mine and record.get("_wet_mine") != wet:
            m = m or w.mut(eid)
            m["_wet_mine"] = wet
        if not mine and (generic_drying_present() or foreign_dried(record)):
            continue  # another domain dries foreign things (generic evaporation or a declared drying_owner)
        # drying
        if wet > 0.001 and not (outdoor and raining and C.rain_exposed(w, eid) > 0):
            if box:
                tau = None
            elif outdoor:
                tau = (2.0 if record.get("quick_dry") else 6.0) * 3600.0 / max(0.2, (0.4 + sun) * (0.6 + 0.08 * wind) * max(0.3, (t_air + 5) / 25.0))
            else:
                tau = (4.0 if record.get("quick_dry") else 10.0) * 3600.0
                if record.get("relation") == "hung" or record.get("spread_out"):
                    tau *= 0.6
            if tau:
                m = m or w.mut(eid)
                m["wetness"] = round(wet * math.exp(-dt / tau), 4) if wet > 0.01 else 0.0
                if mine:
                    m["_wet_mine"] = m["wetness"]
                if m["wetness"] <= 0.0:
                    m.pop("_wx_wet", None)
        stored_wet = wet > 0.2 and (box is not None or not outdoor or C.is_roofed(w, rid)) and record.get("location") != "robot"
        if stored_wet or (record.get("_op_status") or {}).get("stored_wet"):
            C.set_status(w, eid, stored_wet=stored_wet)
        # mildew in wet enclosed or plastic-bagged textiles
        bagged = box and str(state["entities"][box].get("material")) in ("plastic", "polyethylene", "resin") and not state["entities"][box].get("ventilated")
        if wet > 0.2 and (bagged or (box and not state["entities"][box].get("ventilated"))):
            m = m or w.mut(eid)
            m["_mildew_h"] = round(float(m.get("_mildew_h", 0.0)) + dt / 3600.0, 3)
            if m["_mildew_h"] >= 48.0 and float((m.get("soil") or {}).get("mildew", 0.0)) < 0.3:
                from ..core import add_soil
                add_soil(w, eid, "mildew", 0.4)
                C.stat(w, "mildew")
                C.note(w, "mildew", target=eid)
    if not raining:
        sec["dry_days"] = round(float(sec.get("dry_days", 0.0)) + dt / 86400.0, 4)


def mildew_risk(w, eid):
    record = w.s["entities"].get(eid, {})
    box = C.enclosed(w, eid)
    if float(record.get("wetness", 0.0)) <= 0.2 or not box:
        return False
    return not w.s["entities"][box].get("ventilated")


@system("outdoor_plants.weather", order=45, max_dt=30.0)
def weather_system(w, dt, tick):
    """Event-sensitive weather (rain/snow wetting, wind gusts) every substep; O(1) in calm dry weather and
    O(outdoor entities) otherwise (C.outdoor_map)."""
    wx = w.s.get("weather")
    if not wx:
        return
    precip_effects(w, dt)
    wind_effects(w, dt)


@system("outdoor_plants.weather_slow", order=46, max_dt=C.SLOW_S, period=C.SLOW_S)
def weather_slow_system(w, dt, tick):
    """Rain-total decay, drying, freezing/melting, mildew: once per 600 s with dt = accumulated time."""
    if not w.s.get("weather"):
        return
    slow_weather(w, dt / C.SLOW_S)


# ---------------------------------------------------------------------------
# dripping: carrying rain-soaked or snowy outdoor things indoors leaves water on the floor

@after("move", order=160)
def _after_move_drip(ctx, result):
    w = ctx.w
    rid = w.robot.get("room")
    room = w.s["rooms"].get(rid or "", {})
    if not rid or room.get("outdoor"):
        return
    for eid in list(w.held_items()):
        rec = w.s["entities"].get(eid) or {}
        if not (rec.get("_wx_wet") or any(C.is_type(rec, t) for t in OWN_DRYING)):
            continue
        wet = float(rec.get("wetness", 0.0) or 0.0)
        snow = float(rec.get("snow_cm", 0.0) or 0.0)
        pooled = float(rec.get("pooled_water_l", 0.0) or 0.0)
        if wet <= 0.6 and snow <= 0.5 and pooled <= 0.02:
            continue
        liters = 0.08 * max(0.0, wet - 0.5) * max(1.0, float(rec.get("mass_kg", 1.0))) + 0.02 * snow + pooled
        m = w.mut(eid)
        m["wetness"] = min(wet, 0.55)
        m["snow_cm"] = 0.0 if snow else m.get("snow_cm", 0.0)
        if pooled:
            m["pooled_water_l"] = 0.0
        C.add_spill(w, rid, list(w.robot.get("pos") or [0, 0]), round(max(0.02, liters), 3))
        C.stat(w, "dripped_indoors")
        C.note(w, "dripped_indoors", item=eid, room=rid)
