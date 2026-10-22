"""Canonical questions to this domain's answerer (kernel v3.1 dialogue routes, CONVENTIONS 8, docs/DIALOGUE.md "Routes").

`ask(target=<person>, aspect=..., about=..., detail=...)` reaches the legacy `climate.` answerer (comfort.py) through
these routes, tried after the kernel's people routes in registration order:

    status or how, about the temperature or the air (words such as warm, cold, hot, chilly,
        temperature, comfortable, stuffy, draft) or about the room the asked person is in             climate.feel
    preference or amount, about the temperature, the heating or cooling (words), or a thermostat,
        air conditioner, radiator, space heater or the HVAC unit (no detail, or detail temperature /
        target / setpoint / degrees)                                                                    climate.target
    preference or amount, words warmer / cooler (or detail direction, warmer, cooler)                  climate.direction
    keep / intended / amount / which / how / why / status / preference / ok / when / where: a detail
        naming one of the asked person's hidden climate preferences (people.<id>._climate: humidify,
        fan_speed, trip_days, night_setpoint, ok_to_lower_thermostat, open_anyway, close_more_vents,
        sit_minutes, days ...), or a subject whose words all belong to such a key                      climate.<key>

A route returns a topic only for questions the answerer really answers (it counts its own necessary / unnecessary
question statistics, as before). The people routes come first: with the people domain loaded a `preference` question
with a detail is its `pref:` route (ask `amount`, or `preference` without a detail, for the temperature), `status`
about the asked person is its `wellbeing` route (ask about the temperature or the room instead), `which` about words
is its `which:` route, `when` about anything is its `schedule:` route, and an `ok` question with an act is the kernel's
permission route.
"""
from ...kernel import dialogue
from . import common as C

TEMP_WORDS = {"temperature", "temp", "warm", "warmth", "cold", "hot", "heat", "heating", "chilly", "chill", "cool",
              "cooling", "comfortable", "comfort", "stuffy", "draft", "draught", "drafty", "air", "degree", "degrees",
              "thermostat", "setpoint"}
FEEL_WORDS = TEMP_WORDS - {"thermostat", "setpoint", "heating", "cooling"}
DIRECTION_WORDS = {"warmer", "cooler", "colder", "hotter", "direction"}
TARGET_DETAILS = ("temperature", "target", "target_temperature", "setpoint", "degrees", "target_c")
CLIMATE_KINDS_FOR_TARGET = ("thermostat", "hvac_system", "mini_split", "radiator", "space_heater")


def _words(state, subject):
    """The words a question's subject goes by: its own words, or a thing's type and label words."""
    if subject is None:
        return set()
    words = set(subject.words or ())
    if subject.kind == "entity":
        record = (state.get("entities") or {}).get(subject.id) or {}
        words |= set(dialogue.words_of(record.get("type"))) | set(dialogue.words_of(record.get("label")))
    return {dialogue.singular(w) for w in words} | words


def _entity(state, subject):
    if subject is None or subject.kind != "entity" or not subject.id:
        return None
    return (state.get("entities") or {}).get(subject.id)


def _detail_phrase(detail):
    return dialogue.phrase(detail, stop=False) if detail else None


def route_feel(state, pid, subject, aspect, detail, act):
    """status / how, about the temperature or the air, or the room the asked person is in: how warm they feel."""
    if subject is None:
        return None
    if subject.kind == "room":
        person = (state.get("people") or {}).get(pid) or {}
        return "climate.feel" if subject.id == person.get("location") else None
    if subject.kind == "word" and set(subject.words or ()) & FEEL_WORDS:
        return "climate.feel"
    return None


def route_target(state, pid, subject, aspect, detail, act):
    """preference / amount, about the temperature or a heating / cooling device: the temperature they want (or which
    way they would like it to go)."""
    words = _words(state, subject)
    phrase = _detail_phrase(detail)
    if (phrase and set(phrase.split()) & DIRECTION_WORDS) or (words & DIRECTION_WORDS and not detail):
        return "climate.direction"
    rec = _entity(state, subject)
    about_temp = (rec is not None and C.kind(rec) in CLIMATE_KINDS_FOR_TARGET) or \
        (subject is not None and subject.kind == "word" and bool(words & TEMP_WORDS))
    if not about_temp:
        return None
    if detail is None or phrase in {_detail_phrase(d) for d in TARGET_DETAILS}:
        return "climate.target"
    return None


def route_hidden(state, pid, subject, aspect, detail, act):
    """A detail, or the subject's words, naming one of the asked person's hidden climate preferences."""
    hidden = ((state.get("people") or {}).get(pid) or {}).get("_climate") or {}
    keys = sorted(k for k in hidden if isinstance(k, str) and k != "stated")
    if not keys:
        return None
    if detail:
        phrase = _detail_phrase(detail)
        for key in keys:
            if dialogue.phrase(key, stop=False) == phrase or dialogue.canonical_detail(key) == detail:
                return "climate." + key
        return None
    if subject is None or subject.kind in ("request", "house"):
        return None
    words = {w for w in _words(state, subject) if w != "request"}
    if not words:
        return None
    # words: every word must belong to the key ("trip days"); a thing: one of its type or label words will do (an
    # "ultrasonic humidifier" is a humidifier: the humidify preference)
    need = any if subject.kind == "entity" else all
    for key in keys:
        key_words = set(dialogue.words_of(key, stop=False))
        if need(any(_same_stem(w, k) for k in key_words) for w in words):
            return "climate." + key
    return None


STEM_MIN = 7        # "humidifier" and "humidify" share "humidif": the same preference


def _same_stem(word, key_word):
    if word == key_word:
        return True
    n = 0
    for a, b in zip(word, key_word):
        if a != b:
            break
        n += 1
    return n >= STEM_MIN


dialogue.route("climate.feel", ("status", "how"), route_feel)
dialogue.route("climate.target", ("preference", "amount"), route_target)
dialogue.route("climate.hidden", ("keep", "intended", "amount", "which", "how", "why", "status", "preference", "ok",
                                  "when", "where"), route_hidden)
