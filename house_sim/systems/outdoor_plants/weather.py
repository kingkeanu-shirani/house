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

Tier-1 adoption (kernel v3.0 physics and camera, v3.1 sweep; docs/domains/outdoor_plants.md section 0b):
* Physics (TIER1_PHYSICS 5.10, 10.3): the kernel's environment system applies wind and rain to generic outdoor
  objects once its `wind` / `rain` laws are active; this domain's copies step aside then (`_object_wind`, the rain
  soak of things it does not own, the paper/electronics ruin). Umbrella canopies, covers laid over things, plant
  covers and leaf piles stay this domain's at any law state (the kernel models none of them). The `phys.exposed`
  hook keeps plants and the soil-water holders (pots, saucers, trays, beds, compost, pool, fire pit, lawns) out of
  the kernel's environment law: their rain is this domain's own water balance.
* Camera: wind and rain occurrences are visual events (camera.visual_event); in geometric mode falling rain and snow
  give outdoor rooms a `fog` haze (Koschmieder extinction), room mode is untouched.
* Sweep: the stateless default weather process of generation-2 houses (TIER1_SWEEP 13.2, generator/climate.py)
  drives `state.weather` unless a scripted weather sets `override_until_s`; forecasts come from it (issued 06:00 and
  18:00, errors growing with the lead); `forecast_for(state, location, dates)` serves trip destinations; rain
  starting and thunder are notices.
"""
import copy
import datetime
import hashlib
import math
from statistics import NormalDist

from ...kernel import camera, clock, timeline
from ...kernel.phys import lawbook
from ...kernel.plugins import after, hook, observer, system
from . import common as C

DEFAULTS = {"condition": "clear", "temperature_c": 18.0, "wind_mps": 2.0, "wind_from_deg": 270.0, "rain_mm_h": 0.0,
            "snow_cm_h": 0.0, "uv_index": 4, "humidity": 0.55}
RAIN_DEFAULT = {"rain": 4.0, "storm": 15.0}
FIELDS = ("condition", "temperature_c", "wind_mps", "gust_mps", "wind_from_deg", "rain_mm_h", "snow_cm_h", "uv_index",
          "humidity", "forecast", "alerts", "rain_24h_mm", "rain_7d_mm", "dry_days")
PROCESS_FIELDS = ("condition", "temperature_c", "wind_mps", "gust_mps", "rain_mm_h", "snow_cm_h")
SCRIPTED_S = 1.0e9      # override_until_s of a weather the scenario scripted: past any episode (climate's value too)

# notices (TIER1_SWEEP 6.1): rain heard on the windows or the roof, thunder from a storm
timeline.register_notice_kind("rain", "rain starts falling (heard on the windows and the roof)", ("any_sound",), 50.0)
timeline.register_notice_kind("thunder", "thunder rumbles (a thunderstorm nearby)", ("any_sound",), 100.0)


# ---------------------------------------------------------------------------
# events

def apply_weather(w, args):
    """A scripted weather change (setup events and the weather effects below). On a generation-2 house the default
    weather process yields to it (TIER1_SWEEP 13.2): `override_until_s` is set for the rest of the episode, or for
    `override_h` hours when the event says so. Rain starting and a storm arriving are posted as notices."""
    before = _wx_snapshot(w.s)
    wx = w.sec("weather")
    for key, value in sorted((args or {}).items()):
        if key in FIELDS or key.startswith("forecast"):
            wx[key] = copy.deepcopy(value)
    if process_of(wx) is not None:
        hours = (args or {}).get("override_h")
        if isinstance(hours, (int, float)) and not isinstance(hours, bool):
            wx["override_until_s"] = round(float(w.s.get("time_s", 0.0)) + float(hours) * 3600.0, 1)
        else:
            wx["override_until_s"] = SCRIPTED_S
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
    weather_notices(w, before)
    return True


# ---------------------------------------------------------------------------
# notices: rain starting (on the windows / the roof) and thunder

def _wx_snapshot(state):
    wx = state.get("weather") or {}
    return {"rain": float(wx.get("rain_mm_h", 0.0) or 0.0), "condition": wx.get("condition")}


def _rain_db(mm_h):
    """Sound of rain at 1 m outdoors: light (< 1 mm/h) about 45 dB, moderate 55, heavy (>= 8 mm/h) 65, a downpour
    70 dB (rain-noise surveys put moderate rain at 50-60 dBA)."""
    return 45.0 if mm_h < 1.0 else 55.0 if mm_h < 8.0 else 65.0 if mm_h < 15.0 else 70.0


def _windows_by_room(w):
    ents = w.s["entities"]
    out = {}
    for eid in C.static_ids(w, "windows", lambda r: r.get("category") == "window"):
        rec = ents.get(eid)
        if rec is not None:
            out.setdefault(rec.get("location"), True)
    return out


def _first_outdoor_room(w):
    rooms = w.s["rooms"]
    here = w.s["robot"].get("room")
    for cid, conn in sorted(w.s.get("connections", {}).items()):
        pair = conn.get("rooms") or ()
        if here in pair:
            other = pair[1] if pair[0] == here else pair[0]
            if (rooms.get(other) or {}).get("outdoor"):
                return other
    outdoor = sorted(r for r, room in rooms.items() if room.get("outdoor"))
    return outdoor[0] if outdoor else None


def weather_notices(w, before):
    """Notices for a weather change (from the process, a scripted event): rain starting is heard where the robot is
    when it is outdoors or its room has a window (the rain on the glass, 10 dB under the outdoor level), otherwise it
    comes in from the nearest outdoor room (the timeline's propagation decides); a storm arriving brings thunder
    (100 dB at the source: heard through closed doors)."""
    if not timeline.active(w):
        return
    now = _wx_snapshot(w.s)
    here = w.s["robot"].get("room")
    room = (w.s["rooms"].get(here) or {})
    if now["rain"] > 0.0 and before["rain"] <= 0.0:
        if room.get("outdoor"):
            C.notice(w, "rain", room=here, db=_rain_db(now["rain"]), data={"intensity": "heavy" if now["rain"] >= 8.0 else "light" if now["rain"] < 1.0 else "moderate"})
        elif _windows_by_room(w).get(here):
            C.notice(w, "rain", room=here, db=_rain_db(now["rain"]) - 10.0, data={"heard_on": "window"})
        else:
            out = _first_outdoor_room(w)
            if out is not None:
                C.notice(w, "rain", room=out, db=_rain_db(now["rain"]))
    if now["condition"] == "storm" and before["condition"] != "storm":
        out = here if room.get("outdoor") else _first_outdoor_room(w)
        if out is not None:
            C.notice(w, "thunder", room=out)


# ---------------------------------------------------------------------------
# the default weather process (generation 2; TIER1_SWEEP 13.2): state.weather follows generator/climate.py

def process_of(wx):
    """The weather's process record {"model", "region", "seed_key"} (generation-2 houses), or None."""
    proc = (wx or {}).get("process")
    if isinstance(proc, dict) and proc.get("seed_key") and proc.get("region"):
        return proc
    return None


def process_running(state):
    """True while the default weather process drives the weather: a generation-2 house whose scripted override (if
    any) has run out."""
    wx = state.get("weather") or {}
    if process_of(wx) is None:
        return False
    until = wx.get("override_until_s")
    return until is None or float(state.get("time_s", 0.0)) >= float(until)


def process_value(state, when=None):
    """The process's weather at a local standard time (default: now): the fields domains read."""
    from ...generator import climate
    proc = process_of(state.get("weather"))
    return climate.weather_at(proc["seed_key"], proc["region"], clock.now(state) if when is None else when)


def mark_scripted(w):
    """Episode start: a generation-2 weather that is not the process's value at the start time was scripted by the
    scenario (a weather setup op, a helper, a scripted forecast): the process yields for the whole episode
    (override_until_s), so a scripted calm stays calm. Returns True when it marked it."""
    state = w.s
    wx = state.get("weather")
    if not isinstance(wx, dict) or process_of(wx) is None or wx.get("override_until_s") is not None:
        return False
    value = process_value(state)
    if all(_same(wx.get(key), value.get(key)) for key in PROCESS_FIELDS) and not wx.get("forecast"):
        return False
    w.sec("weather")["override_until_s"] = SCRIPTED_S
    return True


@hook("episode_start")
def _scripted_weather_at_start(w, env):
    mark_scripted(w)


def _same(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(float(a) - float(b)) < 1e-6
    return a == b


def _process_idle(w):
    """Exact idle probe of outdoor_plants.weather_process: no process, or a scripted override in force."""
    return not process_running(w.s)


@system("outdoor_plants.weather_process", order=43, max_dt=30.0, idle=_process_idle)
def weather_process_system(w, dt, tick):
    """Every substep while the process runs: copy its value at the current time into state.weather when it changed
    (temperatures move by 0.1 C every few minutes, rain spells start and stop), post the notices of the change."""
    state = w.s
    if not process_running(state):
        return
    value = process_value(state)
    wx = state["weather"]
    changed = {key: value[key] for key in PROCESS_FIELDS if not _same(wx.get(key), value.get(key))}
    if not changed:
        return
    before = _wx_snapshot(state)
    sec = w.sec("weather")
    sec.update(changed)
    sec.pop("override_until_s", None)
    weather_notices(w, before)


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

# ---------------------------------------------------------------------------
# forecasts from the process (TIER1_SWEEP 13.2): issued at 06:00 and 18:00 local time; a pure function of the
# process key, the region and the issue time, so every observation, replay and process sees the same forecast.
#
#   temperature   T_fc = T + e, e = (1.0 + 0.3 L / 24) z("fcT", issue, block): sigma 1.3 C a day ahead and 2.5 C at
#                 five days, a mean absolute error of about 1.0 / 2.0 C (MAE = 0.8 sigma), the order of the US National
#                 Weather Service's published max/min temperature verification (about 2-3 F one day out, 4-5 F at day
#                 five)
#   precipitation probability of a block: wet 0.85 - 0.08 L / 24, dry 0.10 + 0.05 L / 24 (L = lead in hours), shown in
#                 10 % steps like a weather app; condition rain / snow / storm at 50 % and above
#   amounts       a wet block's rain (mm) x exp(0.3 z), wind x exp(0.15 z): forecasts of amounts are less certain
FORECAST_BLOCK_H = 6
FORECAST_BLOCKS = 8                 # 48 h of 6-hour blocks
FORECAST_DAYS = 3                   # then daily blocks to day five
_NORMAL = NormalDist()
_FORECASTS = {}                     # (seed_key, region, issue iso) -> [block dicts with t_issue_h]


def _fc_z(seed_key, *parts):
    """A standard normal draw from SHA-256 of the key and the parts (stateless, PYTHONHASHSEED-independent)."""
    text = "|".join(["fc", str(seed_key)] + [str(p) for p in parts])
    value = int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:13], 16)
    return _NORMAL.inv_cdf((value + 0.5) / float(16 ** 13))


def issue_time(now):
    """The latest forecast issue (06:00 or 18:00 local standard time) at or before `now` (a datetime)."""
    day = datetime.datetime(now.year, now.month, now.day)
    for hour in (18, 6):
        issue = day + datetime.timedelta(hours=hour)
        if issue <= now:
            return issue
    return day - datetime.timedelta(hours=6)


def _block(seed_key, region, issue, start, hours):
    """One forecast block: the truth sampled hourly from the process, perturbed by the lead-dependent errors."""
    from ...generator import climate
    lead = max(0.0, (start - issue).total_seconds() / 3600.0)
    samples = [climate.weather_at(seed_key, region, start + datetime.timedelta(hours=h, minutes=30)) for h in range(hours)]
    temps = [s["temperature_c"] for s in samples]
    wet_hours = [s for s in samples if s["rain_mm_h"] > 0 or s["snow_cm_h"] > 0]
    err = (1.0 + 0.3 * lead / 24.0) * _fc_z(seed_key, "T", issue.isoformat(), start.isoformat())
    prob = (0.85 - 0.08 * lead / 24.0) if wet_hours else (0.10 + 0.05 * lead / 24.0)
    prob = round(min(0.95, max(0.0, prob)) * 10.0) / 10.0
    low, high = round(min(temps) + err), round(max(temps) + err)
    rain = sum(s["rain_mm_h"] + s["snow_cm_h"] for s in wet_hours)
    peak = max([s["rain_mm_h"] for s in wet_hours] or [0.0])
    if prob >= 0.5:
        condition = "snow" if high + low <= 2.0 else ("storm" if peak >= 15.0 else "rain")
        rain_mm = round(rain * math.exp(0.3 * _fc_z(seed_key, "R", issue.isoformat(), start.isoformat())) * 2.0) / 2.0
    else:
        cloudy = sum(1 for s in samples if s["condition"] != "clear")
        condition = "cloudy" if cloudy * 2 >= len(samples) else "clear"
        rain_mm = 0.0
    wind = sum(s["wind_mps"] for s in samples) / len(samples) * math.exp(0.15 * _fc_z(seed_key, "W", issue.isoformat(), start.isoformat()))
    return {"t_issue_h": round((start - issue).total_seconds() / 3600.0, 3), "hours": float(hours), "condition": condition,
            "precip_prob": prob, "rain_mm": rain_mm, "low_c": low, "high_c": high, "wind_mps": round(wind, 1),
            "gust_mps": round(wind * 1.5, 1)}


def _issued(seed_key, region, issue):
    key = (seed_key, region, issue.isoformat())
    blocks = _FORECASTS.get(key)
    if blocks is None:
        blocks = [_block(seed_key, region, issue, issue + datetime.timedelta(hours=FORECAST_BLOCK_H * i), FORECAST_BLOCK_H)
                  for i in range(FORECAST_BLOCKS)]
        first_day = (issue + datetime.timedelta(hours=FORECAST_BLOCK_H * FORECAST_BLOCKS)).replace(hour=0, minute=0, second=0)
        if first_day < issue + datetime.timedelta(hours=FORECAST_BLOCK_H * FORECAST_BLOCKS):
            first_day += datetime.timedelta(days=1)
        blocks += [_block(seed_key, region, issue, first_day + datetime.timedelta(days=i), 24) for i in range(FORECAST_DAYS)]
        if len(_FORECASTS) > 256:
            _FORECASTS.clear()
        _FORECASTS[key] = blocks
    return blocks


def process_forecast(state):
    """The weather app's forecast from the default weather process: the latest issue's blocks that have not ended,
    as {in_h, hours, condition, precip_prob, rain_mm, low_c, high_c, wind_mps, gust_mps}. [] without a process."""
    proc = process_of(state.get("weather"))
    if proc is None:
        return []
    now = clock.now(state)
    issue = issue_time(now)
    since_h = (now - issue).total_seconds() / 3600.0
    out = []
    for block in _issued(proc["seed_key"], proc["region"], issue):
        start_h = block["t_issue_h"] - since_h
        if start_h + block["hours"] <= 0:
            continue
        entry = {k: v for k, v in block.items() if k != "t_issue_h"}
        entry["in_h"] = round(start_h, 2)
        out.append(entry)
    return out


def forecast_for(state, location, dates):
    """Forecast for trip destinations (TIER1_SWEEP 13.2, 11.2): per date (ISO strings or dates) {date, high_c, low_c,
    condition, precip_prob}, from the destination region's normals with the same process and error formulas, issued
    at the household's latest 06:00 / 18:00. location: a region code (us, uk, eu, jp, ca), {"region": ...} or
    anything else (the household's region). The destination's weather has its own stream, keyed by the household's
    process key (or the house salt) and the location."""
    from ...generator import climate
    if isinstance(location, dict):
        region = location.get("region")
        name = location.get("name") or region
    else:
        region, name = location, location
    known = set(climate.regions())
    own = process_of(state.get("weather"))
    if region not in known:
        region = (own or {}).get("region") or clock.region(state)
        region = region if region in known else "us"
    base = (own or {}).get("seed_key") or "house-{}".format((state.get("metadata") or {}).get("seed", 0))
    key = "wx-" + hashlib.sha256("{}|trip|{}".format(base, name).encode("utf-8")).hexdigest()[:16]
    issue = issue_time(clock.now(state))
    out = []
    for value in dates or ():
        day = value if isinstance(value, datetime.date) else datetime.date.fromisoformat(str(value)[:10])
        start = datetime.datetime(day.year, day.month, day.day)
        block = _block(key, region, issue, start, 24)
        out.append({"date": day.isoformat(), "region": region, "high_c": block["high_c"], "low_c": block["low_c"],
                    "condition": block["condition"], "precip_prob": block["precip_prob"]})
    return out


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
    if not wx.get("forecast") and process_running(state):
        items = process_forecast(state)          # the weather app's forecast of the default weather process
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
    """This domain's copy of the wind law for loose objects (drag and lift with the weather's gust against friction,
    tie-downs and ballast). Once the kernel's `wind` law is active its environment system moves loose outdoor items
    (TIER1_PHYSICS 5.10: the wind at the item, sustained wind slides, gusts lift and tip) and this copy steps aside."""
    if lawbook.active("wind"):
        return      # the kernel owns loose objects
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
        C.notice(w, "thud", room=rid, source=eid)        # it slams into the house wall
    if record.get("fragile") and mass >= 0.3:
        m["broken"] = True
        C.stat(w, "breakages")
    size = record.get("size_m") or [0.3]
    C.visual(w, "blown", source=eid, room=rid, size_m=max(float(v) for v in size[:3]), target=eid, distance_m=round(moved, 2))


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
    canopy = float(record.get("canopy_d_m", 2.7))
    rid = C.room_of(w, eid)
    if not record.get("open") and not record.get("strapped") and g >= 12.0:
        m = w.mut(eid)
        m["open"] = True
        m["fabric_torn"] = True
        C.visual(w, "umbrella_whipped_open", source=eid, room=rid, size_m=canopy, target=eid)
    tips, moment, resist = umbrella_tip_check(w, eid, g)
    if not tips:
        return
    m = w.mut(eid)
    m["tipped"] = True
    m["pole_bent"] = True
    C.stat(w, "tip_overs")
    C.stat(w, "wind_damage")
    shattered = False
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
                shattered = True
    C.visual(w, "umbrella_tipped", source=eid, room=rid, size_m=canopy, target=eid, moment=round(moment, 1), resist=round(resist, 1))
    # an 8.5 kg pole and canopy going over is loud; a glass table top under it shatters
    C.notice(w, "glass_breaking" if shattered else "crash", room=rid, db=80.0 if shattered else 70.0, source=eid)


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
    C.stat(w, "covers_blown_off")         # _displace logged it as a visual "blown" event


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
    C.visual(w, "plant_cover_blown_off", source=eid, room=rid, size_m=max(float(size[0]), float(size[1]) if len(size) > 1 else 1.0),
             cover=eid, bed=target)


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
            if record.get("connection"):
                from .yard import mirror_steps
                mirror_steps(w, eid)
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
        # the kernel's rain law wets exposed objects itself (environment.rain_wet: surfaces wet within 120 s, absorbent
        # bodies soak, paper is water-damaged); this domain then only adds snow (the kernel has none) and keeps its
        # drying bookkeeping in step with the kernel's wetness
        kernel_rain = mm > 0 and lawbook.active("rain")
        gain = exposure * ((0.0 if kernel_rain else mm) + cm * 3.0) / soak_mm
        before = float(record.get("wetness", 0.0))
        if kernel_rain:
            _mark_kernel_wet(w, eid, record, before)
        if gain <= 0:
            continue
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
        if kernel_rain:
            continue    # what water does to paper and electronics in the rain is the kernel's (soaking, phys.wetted)
        if C.water_sensitive(record) and record.get("category") not in ("cushion",) and not record.get("fabric_item") and after >= 0.3 and not m.get("ruined"):
            if str(record.get("material")) not in ("polyester_foam", "nylon", "polyester", "cotton", "wool", "fabric") or record.get("plug_required") or record.get("category") in ("electronics", "book", "document", "phone", "laptop", "artwork"):
                m["ruined"] = True
                m["broken"] = True
                C.stat(w, "weather_damage")
                C.visual(w, "rain_ruined", source=eid, target=eid)


def _mark_kernel_wet(w, eid, record, wetness):
    """While the kernel's rain law wets things: an own-drying item the rain made wet is marked for this domain's
    single-sourced drying (_wet_mine, drying_owner), and any other wet thing outdoors is flagged _wx_wet so the slow
    tick dries it when no generic drying domain is loaded (conventions 5)."""
    if wetness <= 0.0:
        return
    if own_drying(record):
        if record.get("_wet_mine") != wetness or not record.get("_wx_wet"):
            m = w.mut(eid)
            m["_wet_mine"] = wetness
            m["_wx_wet"] = True
            if m.get("drying_owner") is None:
                m["drying_owner"] = "outdoor_plants"
    elif not record.get("_wx_wet") and record.get("drying_owner") in (None, "outdoor_plants"):
        w.mut(eid)["_wx_wet"] = True


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
            if record.get("connection"):
                from .yard import mirror_steps
                mirror_steps(w, eid)
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


# ---------------------------------------------------------------------------
# what the kernel's environment law (wind, rain) leaves to this domain: phys.exposed (TIER1_PHYSICS 7.3)

OWN_WATER_TYPES = ("plant", "plant_pot", "nursery_pot", "plant_saucer", "pebble_tray", "seed_tray", "garden_bed",
                   "compost_bin", "pool", "fire_pit", "planting_hole", "lawn")


@hook("phys.exposed")
def kernel_exposure(state, eid):
    """False (left alone by the kernel's wind and rain laws) for the plants and the soil-water holders and water bodies
    whose rain is this domain's own water balance: plants (rain reaches their roots through rain_period_mm; wet foliage
    is leaf_wet_h), pots and saucers, pebble and seed trays, beds, compost bins, the pool, the fire pit's ash, planting
    holes and lawns (the kernel would pour the rain into them as free liquid). Their wind is nothing the kernel's
    frontal-area drag would move anyway (a plant sits in its pot or bed, a soil-filled pot is heavy for its outline;
    beds, pools and pits are fixtures). Covers, porch roofs and closed boxes need no hook: the kernel's own exposure
    rule shelters what is covered_by a cover, roofed or closed in. None otherwise (the kernel decides)."""
    rec = state["entities"].get(eid)
    if rec is None:
        return None
    t = rec.get("type")
    if t in OWN_WATER_TYPES or any(name in OWN_WATER_TYPES for name in rec.get("is_a") or ()):
        return False
    return None


def _fog_target(wx):
    """Haze of an outdoor room in falling rain or snow or fog for the geometric camera's k_haze (exp(-0.3 fog d)):
    fog = sigma / 0.3 with the extinction sigma = 3.912 / visibility (Koschmieder). Visibility assumption (typical
    meteorological values): rain sigma 0.3 R^0.6 per km (about 13 km at 1 mm/h, 3 km at 10 mm/h), snow ten times
    rain at the same water-equivalent rate (3.0 S^0.7 per km: about 1.3 km at 1 cm/h), fog 300 m. 0 when clear."""
    rain = float(wx.get("rain_mm_h", 0.0) or 0.0)
    snow = float(wx.get("snow_cm_h", 0.0) or 0.0)
    sigma_km = 0.0
    if rain > 0:
        sigma_km += 0.3 * rain ** 0.6
    if snow > 0:
        sigma_km += 3.0 * snow ** 0.7
    if wx.get("condition") == "fog":
        sigma_km = max(sigma_km, 3.912 / 0.3)
    return round(sigma_km / 1000.0 / 0.3, 5)


def _fog_rooms(w):
    """[(rid, wanted)] for the outdoor rooms whose haze differs from what the weather says (geometric mode only: room
    mode states stay untouched). A room this domain never hazed keeps any fog a scenario gave it."""
    state = w.s
    if camera.mode(state) != "geometric":
        return []
    wanted = _fog_target(state.get("weather") or {})
    out = []
    for rid, room in sorted(state["rooms"].items()):
        if not room.get("outdoor") or C.is_roofed(w, rid):
            continue
        mine = room.get("_op_fog")
        if mine is None and room.get("fog") is not None:
            continue
        if (mine or 0.0) != wanted:
            out.append((rid, wanted))
    return out


def update_fog(w):
    for rid, wanted in _fog_rooms(w):
        m = w.room_m(rid)
        if wanted > 0:
            m["fog"] = wanted
            m["_op_fog"] = wanted
        else:
            m.pop("fog", None)
            m.pop("_op_fog", None)


def _weather_idle(w):
    """Exact idle probe of outdoor_plants.weather: no weather, or dry (no rain or snow falling), gusts under the 6 m/s
    at which this domain's wind effects start, and no outdoor haze to update."""
    wx = w.s.get("weather")
    if not wx:
        return True
    if float(wx.get("rain_mm_h", 0.0) or 0.0) > 0.0 or float(wx.get("snow_cm_h", 0.0) or 0.0) > 0.0:
        return False
    return C.gust(w) < 6.0 and not _fog_rooms(w)


@system("outdoor_plants.weather", order=45, max_dt=30.0, idle=_weather_idle)
def weather_system(w, dt, tick):
    """Event-sensitive weather (rain/snow wetting, wind gusts) every substep; O(1) in calm dry weather and
    O(outdoor entities) otherwise (C.outdoor_map)."""
    wx = w.s.get("weather")
    if not wx:
        return
    precip_effects(w, dt)
    wind_effects(w, dt)
    update_fog(w)


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
