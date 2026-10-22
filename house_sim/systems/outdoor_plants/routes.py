"""Canonical questions to this domain's answerer (kernel v3.1 dialogue routes, CONVENTIONS 8, docs/DIALOGUE.md).

`ask(target=<person>, aspect=..., about=...)` reaches the legacy "plants:" answerer (plants.plants_answer) through these
routes, tried after the kernel's people routes in registration order:

    how / when (detail none, care, schedule, light or a care word), a plant   plants:care:<id>
    how / when, words naming a species ("peace lily", "basil")                plants:care:<species>
    keep / intended, a plant (a weed, a volunteer, a seedling in a bed)       plants:keep:<id>

("when" about a person's or the household's schedule, "how" about the house rules and anything a spec's
user_simulator names reach the kernel's people routes first: they stay theirs.)

A route returns a topic only for questions the answerer really answers; the answerer counts its own necessary /
unnecessary question statistics as before (the first ask of a topic is necessary, a repeat unnecessary).
"""
from ...kernel import dialogue
from . import plants as P
from .species import SPECIES

CARE_DETAILS = {None, "care", "schedule", "light_level", "temperature"}
CARE_WORDS = {"water", "watering", "light", "sun", "sunlight", "fertilizer", "fertiliser", "feed", "feeding", "care",
              "humidity", "repot", "repotting"}


def _plant(state, subject):
    if subject is None or subject.kind != "entity" or not subject.id:
        return None
    rec = (state.get("entities") or {}).get(subject.id)
    return rec if rec is not None and P.is_plant(rec) else None


def _species(subject):
    """The species a word subject names ("peace lily" -> peace_lily, "basil plant" -> basil), or None."""
    if subject is None or subject.kind != "word":
        return None
    words = [w for w in (subject.words or ()) if w not in ("plant", "plants", "the", "my", "a")]
    for n in range(len(words), 0, -1):
        for i in range(0, len(words) - n + 1):
            key = "_".join(words[i:i + n])
            if key in SPECIES and key != "artificial":
                return key
    return None


def route_care(state, pid, subject, aspect, detail, act):
    if detail not in CARE_DETAILS and str(detail or "").replace(" ", "_") not in CARE_WORDS:
        return None
    rec = _plant(state, subject)
    if rec is not None:
        return "plants:care:" + subject.id
    species = _species(subject)
    if species is not None:
        return "plants:care:" + species
    return None


def route_keep(state, pid, subject, aspect, detail, act):
    if _plant(state, subject) is not None:
        return "plants:keep:" + subject.id
    return None


dialogue.route("plants.care", ("how", "when"), route_care)
dialogue.route("plants.keep", ("keep", "intended"), route_keep)
