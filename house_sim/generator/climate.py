"""Generation 2: climate normals, the default weather process and the season context (docs/design/TIER1_SWEEP.md
sections 13.1 and 13.2).

The weather is a pure function of (seed_key, region, local standard time): nothing is stored and nothing depends on
call order, so any substep partition, a save/load or a forecast query sees the same weather. Every random number is a
hash draw u(seed_key, purpose, day) (SHA-256, independent of PYTHONHASHSEED); z(...) is its inverse-normal.

    day d (days since 2000-01-01), monthly normals interpolated by day of year, mean-preserving: the curve is
              piecewise linear between knots at the month midpoints, and the knots are solved so that each month's
              average of the curve equals its normal (plain interpolation through the normals themselves flattens
              the annual cycle: Philadelphia's January ran 0.6 C warm, July 0.4 C cool)
    anomaly   a_d = sum_{k=0..20} phi^k sigma sqrt(1 - phi^2) z("T", d - k)        phi 0.7, sigma = temp_sd_c
    high/low  H_d = Nhigh + a_d,  L_d = min(Nlow + a_d + 0.5 z("dL", d), H_d - 0.5)
    diurnal   cosine rise from L_d at sunrise to H_d at 15:00, then linear to L_{d+1} at the next sunrise
    wet days  two-state Markov chain with frequency f and persistence r = 0.3 (p_ww = f + r(1 - f),
              p_wd = f(1 - r)), state_d iterated 30 days from a stationary draw (exact to 0.3^30)
    rain      one spell per wet day: start hour 24 u, duration uniform on [1, 2 M - 1] h (mean M = spell_h),
              intensity exponential with mean (precip_mm / wet_days) / M
    wet-day temperature  wet days run wet_day_offset_c warmer (or colder) than the normal and dry days the opposite
              share (-offset f / (1 - f)): precipitation and temperature are not independent (the US Mid-Atlantic's
              winter precipitation is mostly warm-sector rain and its coldest days are dry; Tokyo's comes with cold
              air from the north-east under south-coast cyclones, its dry winter days are sunny and mild)
    condition storm (>= 15 mm/h), rain, snow (precipitating at <= 1.0 C), cloudy (a wet day outside its spell or
              under a 0.05 mm/h trace, or a dry day with u < cloud share), clear
    wind      mean x exp(0.35 z("Wind", d)) x (1 + 0.3 afternoon) / normaliser; gusts 1.5 x

Deviations from the 13.2 sketch: the normals are interpolated mean-preserving (above); snow falls at or below 1.0 C,
not 0.5 C (the air temperature at which half of land precipitation falls as snow: about 1.0 C over the Northern
Hemisphere, Jennings et al. 2018, about 1.2 C, Dai 2008); wet days carry a temperature offset (above), calibrated so
each region's snow days match its normal under the source's own definition; the wind is divided by E[exp(0.35 z)]
(1 + 0.3 / pi) so its mean is the normal mean wind; the dry-day cloud share is derived from the sunshine fraction, P(cloudy) = f + (1 - f) c = 1 - sunshine;
the spell duration law and the 0.05 mm/h trace are this module's (13.2 leaves them open). Sunrise is the geometric
one at the region's latitude (solar time, no refraction, no longitude or DST offset), like clock model 2 of 11.1.

Generation 1 never calls this module; outdoor_plants owns the runtime weather process (13.2) and can call
weather_at / day_weather, so the initial weather of a generation-2 house is the process's own value at the start.
"""
import datetime
import functools
import hashlib
import json
import math
from pathlib import Path
from statistics import NormalDist

from ..kernel import clock

DATA = Path(__file__).resolve().parent.parent / "data" / "generator" / "climate_normals.json"
MODEL = "normals_v1"
EPOCH = datetime.date(2000, 1, 1)
MONTH_DAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
# the middle of each month on a 365-day year (day-of-year position, Jan 1 00:00 = 0)
MONTH_MID = tuple(sum(MONTH_DAYS[:i]) + MONTH_DAYS[i] / 2.0 for i in range(12))
FIELDS = ("high_c", "low_c", "precip_mm", "wet_days", "snow_days", "wind_mps", "sunshine_frac", "temp_sd_c", "spell_h",
          "wet_day_offset_c")
CONDITIONS = ("clear", "cloudy", "rain", "storm", "snow")
_NORMAL = NormalDist()


@functools.lru_cache(maxsize=1)
def normals():
    """The climate normals file, checked once per process (12 values per field, plausible ranges)."""
    data = json.loads(DATA.read_text(encoding="utf-8"))
    for region, row in data["regions"].items():
        for field in FIELDS:
            values = row.get(field)
            if not isinstance(values, list) or len(values) != 12:
                raise ValueError("climate_normals.json: {}.{} needs 12 monthly values".format(region, field))
        for m in range(12):
            if row["low_c"][m] >= row["high_c"][m]:
                raise ValueError("climate_normals.json: {} month {} low >= high".format(region, m + 1))
            if not 0 < row["wet_days"][m] < MONTH_DAYS[m] or not 0 < row["sunshine_frac"][m] < 1:
                raise ValueError("climate_normals.json: {} month {} wet days or sunshine out of range".format(region, m + 1))
        if not -90 < float(row["latitude"]) < 90:
            raise ValueError("climate_normals.json: {} latitude".format(region))
    return data


def regions():
    """Regions with normals (the generator's regions)."""
    return sorted(normals()["regions"])


def _region(region):
    try:
        return normals()["regions"][region]
    except KeyError:
        raise ValueError("No climate normals for region {!r} (known: {})".format(region, ", ".join(regions())))


def _const(name):
    return normals()["constants"][name]


def latitude(region):
    """The region's representative latitude (degrees north)."""
    return float(_region(region)["latitude"])


def season(start, hemisphere="north"):
    """The meteorological season at an ISO start time, by the kernel clock's own rule (so it always agrees with
    clock.describe)."""
    return clock.season({"clock": {"start": start, "hemisphere": hemisphere}, "time_s": 0})


# ----------------------------------------------------------------------------------------------------------------
# hash draws


def _u(seed_key, *parts):
    """Uniform in (0, 1) from SHA-256 of the key and the parts (52 bits, never exactly 0 or 1)."""
    text = "|".join([str(seed_key)] + [str(p) for p in parts])
    value = int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:13], 16)
    return (value + 0.5) / float(16 ** 13)


def _z(seed_key, *parts):
    return _NORMAL.inv_cdf(_u(seed_key, *parts))


@functools.lru_cache(maxsize=16384)
def _z_temp(seed_key, d):
    return _z(seed_key, "T", d)


# ----------------------------------------------------------------------------------------------------------------
# normals and the sun


def day_number(date):
    """Days since 2000-01-01 (negative before)."""
    return (date - EPOCH).days


def _date_of(d):
    return EPOCH + datetime.timedelta(days=d)


def _year_position(d):
    """Position of day d's noon in a 365-day year (0..365), leap years compressed evenly."""
    date = _date_of(d)
    length = 366.0 if (date.year % 4 == 0 and (date.year % 100 != 0 or date.year % 400 == 0)) else 365.0
    return (date.timetuple().tm_yday - 0.5) * 365.0 / length


@functools.lru_cache(maxsize=1)
def _month_weights():
    """12 x 12 matrix A: A[i][j] = the average over month i of the cyclic piecewise-linear hat function of knot j
    (knots at MONTH_MID, period 365 days). Month i sees only its own knot and its two neighbours."""
    a = [[0.0] * 12 for _ in range(12)]
    for i in range(12):
        half = MONTH_DAYS[i] / 2.0
        before = (MONTH_MID[i] - MONTH_MID[i - 1]) % 365.0          # distance to the previous knot (wraps)
        after = (MONTH_MID[(i + 1) % 12] - MONTH_MID[i]) % 365.0
        # each half month: the trapezoid from the month edge to its midpoint, f(edge) = y_i (1 - t) + y_nb t
        for neighbour, span in (((i - 1) % 12, before), ((i + 1) % 12, after)):
            t = half / span
            a[i][i] += half / 2.0 * (2.0 - t) / MONTH_DAYS[i]
            a[i][neighbour] += half / 2.0 * t / MONTH_DAYS[i]
    return a


def _solve(a, b):
    """Gaussian elimination with partial pivoting (a 12 x 12, diagonally dominant system)."""
    n = len(b)
    m = [list(row) + [b[k]] for k, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(col + 1, n):
            f = m[r][col] / m[col][col]
            if f:
                for c in range(col, n + 1):
                    m[r][c] -= f * m[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - sum(m[r][c] * x[c] for c in range(r + 1, n))) / m[r][r]
    return x


@functools.lru_cache(maxsize=256)
def _knots(region, field):
    """The 12 knot values of a field whose piecewise-linear curve averages to each month's normal (per-day rates for
    the monthly counts)."""
    values = _region(region)[field]
    if field in ("wet_days", "snow_days", "precip_mm"):
        values = [v / float(MONTH_DAYS[m]) for m, v in enumerate(values)]
    return tuple(_solve(_month_weights(), [float(v) for v in values]))


def normal_value(region, field, d):
    """A monthly normal at day d: the mean-preserving piecewise-linear curve through knots at the month midpoints
    (wrapping December to January), whose average over each month is that month's normal. Counts per month
    (wet_days, snow_days, precip_mm) are per-day rates, returned per day and never below 0."""
    values = _knots(region, field)
    x = _year_position(d)
    hi = next((i for i in range(12) if MONTH_MID[i] > x), 12)
    lo = hi - 1
    x0 = MONTH_MID[lo] if lo >= 0 else MONTH_MID[11] - 365.0
    x1 = MONTH_MID[hi] if hi < 12 else MONTH_MID[0] + 365.0
    v0, v1 = values[lo % 12], values[hi % 12]
    value = v0 + (v1 - v0) * (x - x0) / (x1 - x0)
    return max(0.0, value) if field in ("wet_days", "snow_days", "precip_mm") else value


def declination_deg(d):
    """Solar declination (Cooper 1969), degrees."""
    doy = _date_of(d).timetuple().tm_yday
    return 23.44 * math.sin(2.0 * math.pi * (284 + doy) / 365.0)


def day_length_h(lat_deg, d):
    """Geometric day length 24/pi acos(-tan(lat) tan(decl)), no refraction; 0 or 24 in polar night or day."""
    x = -math.tan(math.radians(lat_deg)) * math.tan(math.radians(declination_deg(d)))
    return 24.0 / math.pi * math.acos(max(-1.0, min(1.0, x)))


def sunrise_h(lat_deg, d):
    """Sunrise in local solar time (solar noon at 12:00), clamped so the diurnal curve stays defined."""
    return min(11.0, max(1.0, 12.0 - day_length_h(lat_deg, d) / 2.0))


# ----------------------------------------------------------------------------------------------------------------
# the daily weather


@functools.lru_cache(maxsize=8192)
def _wet(seed_key, region, d):
    """Wet-day state of day d: a stationary draw 30 days earlier, then the Markov transitions."""
    r = _const("wet_persistence")
    burn = int(_const("markov_burn_in_days"))
    state = _u(seed_key, "W0", d) < normal_value(region, "wet_days", d - burn)
    for k in range(burn - 1, -1, -1):
        f = normal_value(region, "wet_days", d - k)
        p = f + r * (1.0 - f) if state else f * (1.0 - r)
        state = _u(seed_key, "W", d - k) < p
    return state


@functools.lru_cache(maxsize=8192)
def day_weather(seed_key, region, d):
    """Everything about day d that does not depend on the hour (a dict; cached, never mutate it)."""
    phi = _const("anomaly_phi")
    sigma = normal_value(region, "temp_sd_c", d)
    gain = sigma * math.sqrt(1.0 - phi * phi)
    anomaly = sum(phi ** k * gain * _z_temp(seed_key, d - k) for k in range(int(_const("anomaly_terms"))))
    wet = _wet(seed_key, region, d)
    f = normal_value(region, "wet_days", d)
    # wet days run warmer or colder than dry ones (wet_day_offset_c); dry days take the opposite share, so the
    # monthly normals still hold on average
    offset = normal_value(region, "wet_day_offset_c", d)
    shift = offset if wet else -offset * f / max(1e-6, 1.0 - f)
    high = normal_value(region, "high_c", d) + anomaly + shift
    low = normal_value(region, "low_c", d) + anomaly + shift + _const("low_noise_c") * _z(seed_key, "dL", d)
    low = min(low, high - 0.5)
    spell_mean = max(1.0, normal_value(region, "spell_h", d))
    per_wet_day = normal_value(region, "precip_mm", d) / max(1e-6, f)
    sun = normal_value(region, "sunshine_frac", d)
    cloud_share = max(0.0, min(1.0, (1.0 - sun - f) / max(1e-6, 1.0 - f)))
    wind_norm = math.exp(_const("wind_log_sigma") ** 2 / 2.0) * (1.0 + _const("afternoon_wind_boost") / math.pi)
    return {
        "day": d, "date": _date_of(d).isoformat(), "high_c": high, "low_c": low, "anomaly_c": anomaly,
        "sunrise_h": sunrise_h(latitude(region), d), "wet": wet,
        "spell_start_h": 24.0 * _u(seed_key, "spell", d) if wet else None,
        "spell_h": 1.0 + (2.0 * spell_mean - 2.0) * _u(seed_key, "dur", d) if wet else 0.0,
        "intensity_mm_h": -per_wet_day / spell_mean * math.log(1.0 - _u(seed_key, "I", d)) if wet else 0.0,
        "cloudy": wet or _u(seed_key, "cloud", d) < cloud_share,
        "wind_mps": normal_value(region, "wind_mps", d) * math.exp(_const("wind_log_sigma") * _z(seed_key, "Wind", d)) / wind_norm,
    }


def temperature_c(seed_key, region, d, h):
    """Air temperature at hour h (0..24, local solar time) of day d."""
    today = day_weather(seed_key, region, d)
    peak = _const("warmest_hour")
    rise = today["sunrise_h"]
    if rise <= h <= peak:
        return today["low_c"] + (today["high_c"] - today["low_c"]) * 0.5 * (1.0 - math.cos(math.pi * (h - rise) / (peak - rise)))
    if h > peak:
        nxt = day_weather(seed_key, region, d + 1)
        return today["high_c"] + (nxt["low_c"] - today["high_c"]) * (h - peak) / (24.0 - peak + nxt["sunrise_h"])
    prev = day_weather(seed_key, region, d - 1)
    return prev["high_c"] + (today["low_c"] - prev["high_c"]) * (h + 24.0 - peak) / (24.0 - peak + rise)


def precipitation_mm_h(seed_key, region, d, h):
    """Liquid-equivalent precipitation rate at hour h of day d: day d's spell, or day d-1's running past midnight."""
    today = day_weather(seed_key, region, d)
    if today["wet"] and today["spell_start_h"] <= h < today["spell_start_h"] + today["spell_h"]:
        return today["intensity_mm_h"]
    prev = day_weather(seed_key, region, d - 1)
    if prev["wet"] and h + 24.0 < prev["spell_start_h"] + prev["spell_h"]:
        return prev["intensity_mm_h"]
    return 0.0


def wind_mps(seed_key, region, d, h):
    afternoon = max(0.0, math.cos(2.0 * math.pi * (h - _const("warmest_hour")) / 24.0))
    return day_weather(seed_key, region, d)["wind_mps"] * (1.0 + _const("afternoon_wind_boost") * afternoon)


def weather_at(seed_key, region, when):
    """The weather at a local time (datetime or ISO string): the fields domains read from state["weather"]."""
    if isinstance(when, str):
        when = datetime.datetime.fromisoformat(when)
    d = day_number(when.date())
    h = when.hour + when.minute / 60.0 + when.second / 3600.0
    temp = temperature_c(seed_key, region, d, h)
    rate = precipitation_mm_h(seed_key, region, d, h)
    if rate < _const("trace_mm_h"):
        rate = 0.0          # a trace is not rain: the exponential intensity has a long thin tail near zero
    snowing = rate > 0 and temp <= _const("snow_at_or_below_c")
    if rate > 0:
        condition = "snow" if snowing else ("storm" if rate >= _const("storm_mm_h") else "rain")
    else:
        condition = "cloudy" if day_weather(seed_key, region, d)["cloudy"] else "clear"
    wind = wind_mps(seed_key, region, d, h)
    return {"condition": condition, "temperature_c": round(temp, 1), "wind_mps": round(wind, 1),
            "gust_mps": round(wind * _const("gust_factor"), 1),
            "rain_mm_h": round(rate, 2) if rate > 0 and not snowing else 0.0,
            "snow_cm_h": round(rate * _const("snow_cm_per_mm"), 2) if snowing else 0.0}


# ----------------------------------------------------------------------------------------------------------------
# generation-2 config


def seed_key(seed):
    """The weather process key of a house: an opaque hash of the house seed. state["weather"] is copied into
    observations whole (perception.py), so the key must not reveal the seed every other generator stream derives from."""
    return "wx-" + hashlib.sha256("weather:{}".format(seed).encode("utf-8")).hexdigest()[:16]


def apply(config, seed, region, start):
    """Generation-2 weather and clock (13.1): the initial weather is the process's value at the start time;
    clock.model 2 with the region and its latitude; outdoor rooms take the outdoor air temperature. Returns the
    season at the start."""
    key = seed_key(seed)
    weather = weather_at(key, region, start)
    weather["process"] = {"model": MODEL, "region": region, "seed_key": key}
    config["weather"] = weather
    clk = config.setdefault("clock", {})
    clk.update(model=2, region=region, latitude=latitude(region))
    for room in config["rooms"].values():
        if room.get("outdoor"):
            room["temperature_c"] = weather["temperature_c"]
    return season(start, clk.get("hemisphere", "north"))
