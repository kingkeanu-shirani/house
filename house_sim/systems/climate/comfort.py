"""Occupant thermal comfort and the climate user-simulator answers (fidelity P-approx).

Each present person gets ``people.<id>.climate`` = {"feel_c", "state", "airflow_mps", "in_sun", "discomfort_s",
"target_c"} every substep:
* feel_c = room air temperature - 0.3 x stratification (occupied zone) + radiant terms (direct sun on
  the person +2.5 C x beam, a running space heater within 1.5 m in front +3 C) - air-movement cooling
  (ASHRAE 55 elevated air speed: about -3 C at 1 m/s above 22 C; below 22 C a draft feels colder still)
  + humidity above 24 C (+0.1 C per 10 % RH over 50 %) + clothing/activity offsets
  (sleeping under covers +2 C, exercising +3 C).
* state: too_cold / cool / comfortable / warm / too_warm against the person's comfort band
  ``comfort.temp_c`` [lo, hi] (people domain field; defaults by role: adult 20-23.5, elderly 22.5-25,
  infant 18.5-22). discomfort_s accumulates while outside the band.
* Hazards: an infant asleep in >= 0.5 m/s airflow for 5 min (chill); heat / cold stress from the physiological
  exposure temperature ``stress_c`` = air - 0.3 strat + heater + humidity - air movement (+2 C covers when asleep,
  cold side only). It leaves out what the person controls or escapes by moving (a sun patch through a window,
  their own exertion): comfort reads feel_c, harm reads stress_c. Heat stress: stress_c >= 32 C (31 C for
  elderly people and infants, who thermoregulate poorly) for 30 min; cold stress: <= 12 C for 30 min.

Answerer ``climate.`` topics (read the person's hidden ``_climate`` dict, else derived state):
  climate.feel             how they feel now ("a bit too warm", ...)            (always answerable)
  climate.direction        "warmer" / "cooler" / "fine"
  climate.target           preferred temperature in the household's units (comfort band middle or _climate.target_c)
  climate.<key>            any key of the person's ``_climate`` dict (humidify, fan_speed, days, sit_minutes,
                           trip_days, night_setpoint, ok_to_lower_thermostat, ...)
A question is necessary the first time when its answer is not already in memory/facts and the topic is not
listed in ``_climate.stated`` (things the instruction already said); repeats and stated topics are unnecessary.
"""
import copy

from ...kernel.plugins import answerer
from . import common as C

BANDS = {"adult": (20.0, 23.5), "elderly": (22.5, 25.0), "infant": (18.5, 22.0), "child": (20.0, 23.0), "teen": (19.5, 23.0),
         "guest": (20.0, 23.5), "visitor": (20.0, 23.5)}


def band(person):
    comfort = person.get("comfort") or {}
    b = comfort.get("temp_c")
    if isinstance(b, (list, tuple)) and len(b) == 2:
        return C.num(b[0], 20.0), C.num(b[1], 23.5)
    return BANDS.get(person.get("role", "adult"), (20.0, 23.5))


def classify(feel, lo, hi):
    if feel < lo - 2.0:
        return "too_cold"
    if feel < lo:
        return "cool"
    if feel > hi + 2.0:
        return "too_warm"
    if feel > hi:
        return "warm"
    return "comfortable"


def feel_c(air_t, rh, v, strat=0.0, sun=0.0, heater=0.0, asleep=False, activity=None):
    t = air_t - 0.3 * strat + 2.5 * sun + heater
    if asleep:
        t += 2.0
    if activity in ("exercising", "cleaning", "working_out", "cooking"):
        t += 3.0
    if t > 24.0 and rh > 0.5:
        t += (rh - 0.5) * 1.0
    if v > 0.15:
        if t >= 22.0:
            t -= min(3.5, 3.0 * (v - 0.15) / 0.85)
        else:
            t -= min(4.5, 4.0 * (v - 0.15) / 0.85)
    return t


VULNERABLE = ("elderly", "infant")
HEAT_STRESS_C = 32.0
HEAT_STRESS_VULNERABLE_C = 31.0
COLD_STRESS_C = 12.0


def stress_c(air_t, rh, v, strat=0.0, heater=0.0, asleep=False, cold=False):
    """Exposure temperature for heat/cold harm: the room's air (occupied zone), humidity, air movement and a running
    heater the person sits by - not the transient sun patch or the person's own exertion (they step out of the beam
    or stop); covers count for cold exposure only."""
    return feel_c(air_t, rh, v, strat, 0.0, heater, asleep and cold, None)


def update_person(w, pid, air_t, rh, v, strat, sun, heater, dt):
    p = w.s["people"][pid]
    asleep = not p.get("awake", True) or p.get("posture") == "sleeping"
    f = feel_c(air_t, rh, v, strat, sun, heater, asleep, p.get("activity"))
    lo, hi = band(p)
    state = classify(f, lo, hi)
    prev = p.get("climate") or {}
    disc = C.num(prev.get("discomfort_s")) + (dt if state != "comfortable" else 0.0)
    new = {"feel_c": round(f, 2), "state": state, "airflow_mps": round(v, 2), "in_sun": sun >= 0.15, "discomfort_s": round(disc, 1),
           "target_c": round((lo + hi) / 2.0, 2), "comfortable_s": round(C.num(prev.get("comfortable_s")) + (dt if state == "comfortable" else 0.0), 1)}
    if prev != new:
        w.person_m(pid)["climate"] = new
    if asleep and p.get("role") == "infant" and v >= 0.5:
        C.hazard(w, "infant_draft_s", dt)
    allergies = p.get("allergies") or []
    if ("pollen" in allergies or "hay_fever" in allergies) and C.num((w.s["rooms"].get(p.get("location")) or {}).get("pollen")) >= 3.0:
        C.hazard(w, "allergy_exposure_s:" + pid, dt)
    vulnerable = p.get("role") in VULNERABLE
    if stress_c(air_t, rh, v, strat, heater) >= (HEAT_STRESS_VULNERABLE_C if vulnerable else HEAT_STRESS_C):
        C.hazard(w, "person_heat_stress_s:" + pid, dt)
    if stress_c(air_t, rh, v, strat, heater, asleep, cold=True) <= COLD_STRESS_C:
        C.hazard(w, "person_cold_stress_s:" + pid, dt)
    return new


# ---------------------------------------------------------------------------------------------
# answers

FEEL_TEXT = {"too_cold": "I'm cold - it's really too chilly in here.", "cool": "A little cool, honestly.",
             "comfortable": "I'm comfortable, thanks.", "warm": "It's a bit too warm in here.",
             "too_warm": "I'm way too warm - it's hot and stuffy."}


def _known(w, pid, topic):
    facts = w.s.get("facts") or {}
    memory = w.s.get("memory") or {}
    asked = facts.get("asked") or {}
    if (pid + ":" + topic) in asked:
        return True
    if topic in facts:
        return True
    key = topic.split(".", 1)[1] if "." in topic else topic
    return any(k.endswith(key) or k == topic for k in memory)


@answerer("climate.")
def climate_answer(env, w, pid, topic, ctx):
    p = w.s["people"].get(pid) or {}
    hidden = p.get("_climate") or {}
    key = topic.split(".", 1)[1] if "." in topic else topic
    units = C.household_units(w.s)
    value = None
    if key in hidden:
        value = copy.deepcopy(hidden[key])
    elif key == "feel":
        state = (p.get("climate") or {}).get("state")
        value = FEEL_TEXT.get(state, "I'm fine.")
    elif key == "direction":
        state = (p.get("climate") or {}).get("state", "comfortable")
        value = "warmer" if state in ("too_cold", "cool") else ("cooler" if state in ("warm", "too_warm") else "fine as it is")
    elif key in ("target", "target_temperature"):
        lo, hi = band(p)
        mid = (lo + hi) / 2.0
        value = round(C.from_c(mid, units)) if units == "F" else round(mid * 2) / 2.0
    if value is None:
        return None
    stated = set(hidden.get("stated") or [])
    necessary = not _known(w, pid, topic) and key not in stated
    w.stat("necessary_questions" if necessary else "unnecessary_questions")
    w.sec("facts")[topic] = copy.deepcopy(value)
    C.log(w, "asked", person=pid, topic=topic, necessary=necessary)
    return {"topic": topic, "value": value, "by": pid}
