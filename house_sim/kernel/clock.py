"""Simulated calendar time. Nothing here reads the real clock.

state["clock"] = {"start": "2026-10-09T07:00:00", "hemisphere": "north"}
state["time_s"] = seconds since the episode's start time.
"""
import datetime
import math

DEFAULT_START = "2026-10-09T07:00:00"
SEASONS_NORTH = {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
                 6: "summer", 7: "summer", 8: "summer", 9: "autumn", 10: "autumn", 11: "autumn"}
FLIP = {"winter": "summer", "summer": "winter", "spring": "autumn", "autumn": "spring"}


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


def sun_times(state):
    """Approximate sunrise/sunset hours for a mid-latitude home by day of year."""
    day = now(state).timetuple().tm_yday
    shift = 1 if state.get("clock", {}).get("hemisphere") != "south" else -1
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


def part_of_day(state):
    h = hour(state)
    if h < 5: return "night"
    if h < 12: return "morning"
    if h < 17: return "afternoon"
    if h < 21: return "evening"
    return "night"


def describe(state):
    current = now(state)
    return {"datetime": current.isoformat(timespec="minutes"), "date": current.date().isoformat(),
            "weekday": current.strftime("%A"), "hour": round(hour(state), 2), "part_of_day": part_of_day(state),
            "season": season(state), "daylight": daylight(state)}


def days_until(state, iso_date):
    """Whole days from the current simulated date to iso_date (negative if past)."""
    target = datetime.date.fromisoformat(iso_date[:10])
    return (target - now(state).date()).days
