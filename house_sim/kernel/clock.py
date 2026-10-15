"""Simulated calendar time. Nothing here reads the real clock.

state["clock"] = {"start": "2026-10-09T07:00:00", "hemisphere": "north"}
state["time_s"] = seconds since the episode's start time.

The simulator's clock is monotonic local STANDARD time: now(state) never jumps. Generation-2 houses carry clock model 2
(`clock.model` 2, `clock.region`, `clock.latitude`; generator/climate.py): their describe() shows the day's holiday,
and sun_times() follows the latitude (geometric day length 24/pi * acos(-tan(lat) tan(decl)), Cooper's declination,
no refraction, solar noon at 12:00 standard time). Daylight saving time is applied only when the clock also says
`"dst": true` (wall(state), dst_active(state), describe(), parse_when's wall-clock times): the domains still run on
standard time (people's schedules, the house's clock displays, collection days), so a model-2 clock without the flag
shows standard time and everything the agent reads agrees; the flag is set once the domains move to wall time
(adoption round, TIER1_INTEGRATION 6). Clock model 1 (every other state) keeps today's output: no DST, the fixed +-3 h
daylight curve around 12:30.

Calendar helpers (kernel v3.1, TIER1_SWEEP 11.1; data in data/calendar/holidays.json and dst.json; the region is
clock.region, else metadata.region, else "us"):

    holiday(state, date=None) -> name | None        holidays_between(state, d0, d1) -> [(iso date, name)]
    dst_active(state) -> bool                       wall(state) -> datetime (the wall clock)
    today(state), tomorrow(state) -> date            weekday(state, date=None) -> "Monday" ...
    is_weekend(state, date=None) -> bool             parse_when(state, text) -> seconds (the time_s coordinate)
    occurrences(event, d0, d1) -> [{"start", "end"}] format_date(state, date, style=None) -> str
"""
import datetime
import json
import math
import re
from pathlib import Path

DEFAULT_START = "2026-10-09T07:00:00"
SEASONS_NORTH = {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
                 6: "summer", 7: "summer", 8: "summer", 9: "autumn", 10: "autumn", 11: "autumn"}
FLIP = {"winter": "summer", "summer": "winter", "spring": "autumn", "autumn": "spring"}
CALENDAR_DATA = Path(__file__).resolve().parent.parent / "data" / "calendar"
WEEKDAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
DEFAULT_REGION = "us"
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")
DATE_ORDER = {"us": "MDY", "uk": "DMY", "eu": "DMY", "ca": "DMY", "jp": "YMD"}
DATE_SEP = {"us": "/", "uk": "/", "eu": ".", "ca": "/", "jp": "/"}
MAX_OCCURRENCE_STEPS = 20000


def start_of(state):
    return datetime.datetime.fromisoformat(state.get("clock", {}).get("start", DEFAULT_START))


def now(state):
    return start_of(state) + datetime.timedelta(seconds=float(state.get("time_s", 0)))


def season(state):
    value = SEASONS_NORTH[now(state).month]
    return FLIP[value] if state.get("clock", {}).get("hemisphere") == "south" else value


def hour(state):
    current = now(state)
    return current.hour + current.minute / 60.0 + current.second / 3600.0


def model(state):
    """The clock model: 2 for generation-2 houses (holidays and latitude daylight in describe), 1 otherwise."""
    try:
        return int((state.get("clock") or {}).get("model", 1) or 1)
    except (TypeError, ValueError):
        return 1


def dst_enabled(state):
    """True when the household clock follows daylight saving time: clock model 2 with "dst": true (the flag waits for
    the domains to keep wall time too, so the agent's clock and the house's agree)."""
    return model(state) >= 2 and (state.get("clock") or {}).get("dst") is True


def declination_deg(day_of_year):
    """Solar declination (Cooper 1969), degrees; the same formula as generator/climate.py."""
    return 23.44 * math.sin(2.0 * math.pi * (284 + day_of_year) / 365.0)


def day_length_h(latitude_deg, day_of_year):
    """Geometric day length 24/pi acos(-tan(lat) tan(decl)), no refraction: 0 in polar night, 24 in polar day."""
    x = -math.tan(math.radians(latitude_deg)) * math.tan(math.radians(declination_deg(day_of_year)))
    return 24.0 / math.pi * math.acos(max(-1.0, min(1.0, x)))


def sun_times(state):
    """Approximate sunrise/sunset hours (standard time). Clock model 2 with a latitude: the geometric day length at
    that latitude around solar noon 12:00; otherwise a mid-latitude home by day of year (+-3 h around 12:30)."""
    day = now(state).timetuple().tm_yday
    clk = state.get("clock", {}) or {}
    if model(state) >= 2 and isinstance(clk.get("latitude"), (int, float)):
        length = day_length_h(float(clk["latitude"]), day)
        return 12.0 - length / 2.0, 12.0 + length / 2.0
    shift = 1 if clk.get("hemisphere") != "south" else -1
    daylight = 12.0 + shift * 3.0 * math.sin(2 * math.pi * (day - 80) / 365.0)
    return 12.5 - daylight / 2.0, 12.5 + daylight / 2.0


def daylight(state):
    """0..1 outdoor illumination; overcast and rain reduce it."""
    rise, set_ = sun_times(state)
    h = hour(state)
    if h <= rise - 0.5 or h >= set_ + 0.5:
        level = 0.0
    elif h < rise + 0.5:
        level = (h - (rise - 0.5))
    elif h > set_ - 0.5:
        level = ((set_ + 0.5) - h)
    else:
        level = 1.0
    condition = state.get("weather", {}).get("condition", "clear")
    level *= {"clear": 1.0, "cloudy": 0.7, "rain": 0.5, "storm": 0.3, "snow": 0.6, "fog": 0.5, "wind": 0.9}.get(condition, 0.8)
    return round(max(0.0, min(1.0, level)), 3)


def _part_of(h):
    if h < 5: return "night"
    if h < 12: return "morning"
    if h < 17: return "afternoon"
    if h < 21: return "evening"
    return "night"


def part_of_day(state):
    return _part_of(hour(state))


def describe(state):
    """The calendar view of the observation. Clock model 2: the wall clock (DST applied when the clock enables it),
    plus "dst" and the day's "holiday" (None on other days); model 1 exactly as before."""
    if model(state) >= 2:
        current = wall(state)
        h = current.hour + current.minute / 60.0 + current.second / 3600.0
        return {"datetime": current.isoformat(timespec="minutes"), "date": current.date().isoformat(),
                "weekday": current.strftime("%A"), "hour": round(h, 2), "part_of_day": _part_of(h),
                "season": season(state), "daylight": daylight(state), "dst": dst_active(state),
                "holiday": holiday(state, current.date())}
    current = now(state)
    return {"datetime": current.isoformat(timespec="minutes"), "date": current.date().isoformat(),
            "weekday": current.strftime("%A"), "hour": round(hour(state), 2), "part_of_day": part_of_day(state),
            "season": season(state), "daylight": daylight(state)}


def days_until(state, iso_date):
    """Whole days from the current simulated date to iso_date (negative if past)."""
    target = datetime.date.fromisoformat(iso_date[:10])
    return (target - now(state).date()).days


# ----------------------------------------------------------------------------------------------------------------
# regions, holidays and daylight saving time (TIER1_SWEEP 11.1)

_DATA = {}
_YEARS = {}


def _load(name):
    if name not in _DATA:
        _DATA[name] = json.loads((CALENDAR_DATA / (name + ".json")).read_text(encoding="utf-8"))
    return _DATA[name]


def region(state):
    """The household's calendar region: clock.region, else metadata.region (generation-1 houses), else "us"."""
    for value in ((state.get("clock") or {}).get("region"), (state.get("metadata") or {}).get("region")):
        if isinstance(value, str) and value in _load("holidays")["regions"]:
            return value
    return DEFAULT_REGION


def _as_date(value, state=None):
    if value is None:
        return today(state)
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value)[:10])


def _nth_weekday(year, month, nth, weekday):
    """The nth (1..5, -1 = last) weekday ("MO".."SU") of a month, or None when the month has no such day."""
    wd = WEEKDAYS.index(weekday)
    if nth > 0:
        first = datetime.date(year, month, 1)
        day = first + datetime.timedelta(days=(wd - first.weekday()) % 7 + 7 * (nth - 1))
        return day if day.month == month else None
    last = (datetime.date(year + (month == 12), month % 12 + 1, 1) - datetime.timedelta(days=1))
    day = last - datetime.timedelta(days=(last.weekday() - wd) % 7 + 7 * (-nth - 1))
    return day if day.month == month else None


def easter(year):
    """Easter Sunday by the Anonymous Gregorian computus (Meeus/Jones/Butcher)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return datetime.date(year, month, day)


def _equinox(year, which):
    """Japan's equinox days (the National Astronomical Observatory's approximation, 1980-2099)."""
    base = 20.8431 if which == "vernal" else 23.2488
    day = int(base + 0.242194 * (year - 1980) - int((year - 1980) / 4))
    return datetime.date(year, 3 if which == "vernal" else 9, day)


def _rule_date(rule, year):
    if rule.get("since") is not None and year < int(rule["since"]):
        return None
    if rule.get("until") is not None and year > int(rule["until"]):
        return None
    if "easter" in rule:
        return easter(year) + datetime.timedelta(days=int(rule["easter"]))
    if "equinox" in rule:
        return _equinox(year, rule["equinox"])
    if "on_or_before" in rule:
        spec = rule["on_or_before"]
        limit = datetime.date(year, int(spec["month"]), int(spec["day"]))
        return limit - datetime.timedelta(days=(limit.weekday() - WEEKDAYS.index(spec["weekday"])) % 7)
    if "nth" in rule:
        return _nth_weekday(year, int(rule["month"]), int(rule["nth"]), rule["weekday"])
    return datetime.date(year, int(rule["month"]), int(rule["day"]))


def _base(name, year):
    """{date: holiday name} of the region's rules for one year, without observance shifts."""
    out = {}
    for rule in _load("holidays")["regions"][name]["holidays"]:
        day = _rule_date(rule, year)
        if day is not None:
            out.setdefault(day, rule["name"])
    return out


def _observed(name, year):
    """{date: holiday name} for a calendar year with the region's weekend rule applied (an observed day of a holiday
    in a neighbouring year that lands in this year is included)."""
    key = (name, year)
    if key in _YEARS:
        return _YEARS[key]
    data = _load("holidays")["regions"][name]
    rule = data.get("observed", "none")
    out = {}
    for y in (year - 1, year, year + 1):
        base = _base(name, y)
        moving = {r["name"] for r in data["holidays"] if r.get("observed")}
        days = dict(base)
        if rule == "jp":
            for day in sorted(base):        # citizens' holiday: a weekday between two national holidays
                gap = day + datetime.timedelta(days=2)
                middle = day + datetime.timedelta(days=1)
                if gap in base and middle not in base and middle.weekday() != 6:
                    days[middle] = "Citizens' Holiday"
            for day in sorted(base):        # substitute holiday: a holiday on a Sunday moves to the next free day
                if day.weekday() == 6:
                    nxt = day + datetime.timedelta(days=1)
                    while nxt in days:
                        nxt += datetime.timedelta(days=1)
                    days[nxt] = "Substitute Holiday ({})".format(base[day])
        elif rule == "nearest_weekday":
            for day in sorted(base):
                if base[day] in moving and day.weekday() >= 5:
                    shift = -1 if day.weekday() == 5 else 1         # Saturday -> Friday, Sunday -> Monday
                    shifted = day + datetime.timedelta(days=shift)
                    days.setdefault(shifted, base[day] + " (observed)")
        elif rule == "next_free_weekday":
            taken = {d for d in base if d.weekday() < 5}
            for day in sorted(base):
                if base[day] in moving and day.weekday() >= 5:
                    nxt = day + datetime.timedelta(days=1)
                    while nxt.weekday() >= 5 or nxt in taken:
                        nxt += datetime.timedelta(days=1)
                    taken.add(nxt)
                    days.setdefault(nxt, base[day] + " (substitute day)")
        for day, label in days.items():
            if day.year == year:
                out.setdefault(day, label)
    _YEARS[key] = out
    return out


def holiday(state, date=None):
    """The public holiday (or its observed / substitute day) of the household's region on `date` (default: today),
    or None."""
    day = _as_date(date, state)
    return _observed(region(state), day.year).get(day)


def holidays_between(state, d0, d1):
    """[(iso date, name)] of the region's holidays from d0 to d1 inclusive, in date order."""
    a, b = _as_date(d0, state), _as_date(d1, state)
    name = region(state)
    out = []
    for year in range(a.year, b.year + 1):
        for day, label in sorted(_observed(name, year).items()):
            if a <= day <= b:
                out.append((day.isoformat(), label))
    return out


def _parse_hm(text):
    h, m = text.split(":")
    return int(h), int(m)


def _dst_bounds(name, year):
    """(start, end) of daylight saving time in local standard time for one year, or None."""
    rule = _load("dst")["regions"].get(name)
    if not rule:
        return None
    bounds = []
    for key in ("start", "end"):
        spec = rule[key]
        day = _nth_weekday(year, int(spec["month"]), int(spec["nth"]), spec["weekday"])
        h, m = _parse_hm(spec["time"])
        bounds.append(datetime.datetime(day.year, day.month, day.day, h, m))
    return bounds[0], bounds[1]


def _dst_at(name, standard):
    bounds = _dst_bounds(name, standard.year)
    return bounds is not None and bounds[0] <= standard < bounds[1]


def dst_active(state):
    """True while daylight saving time is in effect for the household's region (clock model 2 with "dst": true only:
    other clocks keep standard time)."""
    if not dst_enabled(state):
        return False
    return _dst_at(region(state), now(state))


def _dst_offset(name):
    rule = _load("dst")["regions"].get(name)
    return datetime.timedelta(hours=float(rule.get("offset_h", 1))) if rule else datetime.timedelta(0)


def wall(state):
    """The wall-clock reading: now(state) plus the DST offset while it is in effect (clock model 2 with "dst": true);
    otherwise now(state)."""
    current = now(state)
    if dst_enabled(state) and _dst_at(region(state), current):
        return current + _dst_offset(region(state))
    return current


def _local(state):
    return wall(state) if dst_enabled(state) else now(state)


def today(state):
    """The household's current (wall-clock) date."""
    return _local(state).date()


def tomorrow(state):
    return today(state) + datetime.timedelta(days=1)


def weekday(state, date=None):
    """"Monday" .. "Sunday" of `date` (default today)."""
    return _as_date(date, state).strftime("%A")


def is_weekend(state, date=None):
    return _as_date(date, state).weekday() >= 5


def _to_standard(state, local_dt):
    """A wall-clock datetime of the household as standard time (DST removed when in effect then; a clock with DST)."""
    if not dst_enabled(state):
        return local_dt
    name = region(state)
    offset = _dst_offset(name)
    if offset and _dst_at(name, local_dt - offset):
        return local_dt - offset
    return local_dt


def _seconds(state, standard_dt):
    return round((standard_dt - start_of(state)).total_seconds(), 3)


_REL = re.compile(r"^([+-])\s*(\d+(?:\.\d+)?)\s*(s|sec|secs|seconds?|m|min|mins|minutes?|h|hr|hrs|hours?|d|days?)$")
_DAY_AT = re.compile(r"^(?:([+-]\d+)\s*d|day\s*([+-]\d+))\s+(\d{1,2}:\d{2})$")
_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_when(state, text):
    """A time in the time_s coordinate (seconds since the episode start; negative is before it) from author text:
    a number (seconds) as is; "+2h" / "-30m" / "+90s" / "-1d" relative to now (state time_s); "07:30" the first such
    wall-clock time at or after now; "today 07:30", "tomorrow 07:30", "yesterday 18:40"; "-1d 18:40" / "+2d 07:30"
    (days from today, at a wall-clock time); "day+1 07:30" (days from the episode's start date); an ISO date or
    datetime ("2026-10-12", "2026-10-12T08:15"). Wall-clock times follow DST on a clock with DST (model 2 and "dst":
    true). Raises ValueError."""
    if isinstance(text, bool):
        raise ValueError("Not a time: {!r}".format(text))
    if isinstance(text, (int, float)):
        return float(text)
    raw = str(text).strip()
    low = raw.lower()
    current = float(state.get("time_s", 0.0))
    m = _REL.match(low)
    if m:
        value = float(m.group(2)) * _UNIT[m.group(3)[0]]
        return round(current + (value if m.group(1) == "+" else -value), 3)
    local_now = _local(state)
    m = _DAY_AT.match(low)
    if m:
        if m.group(1) is not None:
            base = local_now.date() + datetime.timedelta(days=int(m.group(1)))
        else:
            base = _local(dict(state, time_s=0.0)).date() + datetime.timedelta(days=int(m.group(2)))
        h, mi = _parse_hm(m.group(3))
        return _seconds(state, _to_standard(state, datetime.datetime(base.year, base.month, base.day, h, mi)))
    words = low.split()
    if len(words) == 2 and words[0] in ("today", "tomorrow", "yesterday") and ":" in words[1]:
        shift = {"today": 0, "tomorrow": 1, "yesterday": -1}[words[0]]
        base = local_now.date() + datetime.timedelta(days=shift)
        h, mi = _parse_hm(words[1])
        return _seconds(state, _to_standard(state, datetime.datetime(base.year, base.month, base.day, h, mi)))
    if re.match(r"^\d{1,2}:\d{2}$", low):
        h, mi = _parse_hm(low)
        candidate = datetime.datetime(local_now.year, local_now.month, local_now.day, h, mi)
        if candidate < local_now:
            candidate += datetime.timedelta(days=1)
        return _seconds(state, _to_standard(state, candidate))
    try:
        if len(raw) == 10:
            day = datetime.date.fromisoformat(raw)
            stamp = datetime.datetime(day.year, day.month, day.day)
        else:
            stamp = datetime.datetime.fromisoformat(raw.replace(" ", "T", 1))
    except ValueError:
        raise ValueError("Cannot read the time {!r}".format(text))
    return _seconds(state, _to_standard(state, stamp))


def _iso_dt(value):
    if isinstance(value, datetime.datetime):
        return value
    text = str(value)
    if len(text) == 10:
        day = datetime.date.fromisoformat(text)
        return datetime.datetime(day.year, day.month, day.day)
    return datetime.datetime.fromisoformat(text.replace(" ", "T", 1))


def _add_months(dt, months):
    month = dt.month - 1 + months
    year = dt.year + month // 12
    return year, month % 12 + 1


def occurrences(event, d0, d1):
    """The occurrences of a calendar event (household.calendar, TIER1_SWEEP 11.2) whose start date lies in [d0, d1]
    (dates, inclusive): [{"start": iso, "end": iso}] in order. event: {"start", "end"?, "recurrence"?: {"freq":
    daily | weekly | monthly, "interval" (1), "byday" (["MO", ...]: weekly; default the start's weekday),
    "bymonthday" ([1..31]: monthly; default the start's day), "until" (date, inclusive), "count" (occurrences from the
    start, excluded dates included), "except" ([dates])}}. Without a recurrence the event occurs once."""
    start = _iso_dt(event["start"])
    end = _iso_dt(event["end"]) if event.get("end") else start
    length = end - start
    a, b = _as_date(d0), _as_date(d1)
    rec = event.get("recurrence")
    out = []

    def emit(begin):
        if a <= begin.date() <= b:
            out.append({"start": begin.isoformat(timespec="minutes"),
                        "end": (begin + length).isoformat(timespec="minutes")})

    if not rec:
        emit(start)
        return out
    freq = rec.get("freq", "weekly")
    if freq not in ("daily", "weekly", "monthly"):
        raise ValueError("recurrence freq must be daily, weekly or monthly")
    interval = max(1, int(rec.get("interval", 1)))
    until = _as_date(rec["until"]) if rec.get("until") else None
    count = int(rec["count"]) if rec.get("count") is not None else None
    excluded = {_as_date(x) for x in rec.get("except", [])}
    last = min(b, until) if until else b
    seen = 0
    candidates = []
    if freq == "daily":
        day, step = start, 0
        while day.date() <= last and step < MAX_OCCURRENCE_STEPS:
            candidates.append(day)
            day += datetime.timedelta(days=interval)
            step += 1
    elif freq == "weekly":
        days = sorted(WEEKDAYS.index(d) for d in (rec.get("byday") or [WEEKDAYS[start.weekday()]]))
        week = start - datetime.timedelta(days=start.weekday())
        step = 0
        while week.date() <= last and step < MAX_OCCURRENCE_STEPS:
            for wd in days:
                day = week + datetime.timedelta(days=wd)
                if day >= start and day.date() <= last:
                    candidates.append(day)
            week += datetime.timedelta(weeks=interval)
            step += 1
    else:
        mdays = sorted(int(x) for x in (rec.get("bymonthday") or [start.day]))
        step = 0
        while step < MAX_OCCURRENCE_STEPS:
            year, month = _add_months(start, step * interval)
            if datetime.date(year, month, 1) > last:
                break
            for md in mdays:
                try:
                    day = start.replace(year=year, month=month, day=md)
                except ValueError:
                    continue            # the month has no such day
                if day >= start and day.date() <= last:
                    candidates.append(day)
            step += 1
    for day in candidates:
        if count is not None and seen >= count:
            break
        seen += 1
        if day.date() not in excluded:
            emit(day)
    return out


def format_date(state, date=None, style=None):
    """A date as the household writes it (labels, notes): style None / "numeric" in the region's order (us
    10/09/2026, uk and ca 09/10/2026, eu 09.10.2026, jp 2026/10/09), "long" (October 9, 2026 / 9 October 2026 /
    2026 October 9), "iso" (2026-10-09), or a forced numeric order "MDY" / "DMY" / "YMD"."""
    day = _as_date(date, state)
    name = region(state)
    order = DATE_ORDER.get(name, "MDY")
    sep = DATE_SEP.get(name, "/")
    style = style or "numeric"
    if style == "iso":
        return day.isoformat()
    if style in ("MDY", "DMY", "YMD"):
        order, style = style, "numeric"
    if style == "numeric":
        parts = {"M": "{:02d}".format(day.month), "D": "{:02d}".format(day.day), "Y": "{:04d}".format(day.year)}
        return sep.join(parts[c] for c in order)
    if style == "long":
        month = MONTHS[day.month - 1]
        if order == "MDY":
            return "{} {}, {}".format(month, day.day, day.year)
        if order == "DMY":
            return "{} {} {}".format(day.day, month, day.year)
        return "{} {} {}".format(day.year, month, day.day)
    raise ValueError("Unknown date style {!r} (numeric, long, iso, MDY, DMY, YMD)".format(style))
