"""Canonical questions to this domain's answerer (kernel v3.1 dialogue routes, CONVENTIONS 8, docs/DIALOGUE.md).

`ask(target=<person>, aspect=..., about=...)` reaches the legacy "cleaning.<topic>" answerer (perception.py) through
these routes, tried after the kernel's people routes in registration order:

    keep, a waste item (waste_kind / a hidden disposition / something in a bin)        cleaning.keep:<id>
    which or status, a bottle or refill that holds a cleaning product                 cleaning.product:<id>
    how, a counter, floor or anything with a finish                                    cleaning.finish:<id>
    how, a bin, cart, the recycling guide or a waste item; or words such as
         recycling, trash, compost, bins, sorting                                      cleaning.rules
    how, words such as quiet, noise, vacuum (or detail noise)                          cleaning.quiet_hours
    how, words such as cloth, rag, colour (or detail color)                            cleaning.cloth_colors

A route returns a topic only for questions the answerer really answers; the answerer counts its own necessary /
unnecessary question statistics as before. ("when" questions about quiet hours reach the people domain's schedule
route first, and "which" questions in words the people's which route: those stay theirs.)
"""
from ...kernel import dialogue
from . import state as S

WASTE_WORDS = {"recycling", "recycle", "recyclable", "trash", "waste", "compost", "composting", "bin", "sorting", "sort",
               "organic", "organics", "landfill", "cart"}
QUIET_WORDS = {"quiet", "noise", "noisy", "loud", "vacuum", "vacuuming", "hoover", "hoovering"}
CLOTH_WORDS = {"cloth", "rag", "microfiber", "microfibre", "color", "colour", "coding"}


def _entity(state, subject):
    if subject is None or subject.kind != "entity" or not subject.id:
        return None
    return (state.get("entities") or {}).get(subject.id)


def _words(subject):
    if subject is None or subject.kind == "entity":
        return set()
    return set(subject.words or ())


def route_keep(state, pid, subject, aspect, detail, act):
    rec = _entity(state, subject)
    if rec is not None and (rec.get("waste_kind") or rec.get("_disposition") or rec.get("in_stream") is not None):
        return "cleaning.keep:" + subject.id
    return None


def route_product(state, pid, subject, aspect, detail, act):
    rec = _entity(state, subject)
    if rec is not None and (rec.get("product") or (rec.get("cl_chem") is not None and rec.get("liquid_container"))):
        return "cleaning.product:" + subject.id
    return None


def route_how(state, pid, subject, aspect, detail, act):
    rec = _entity(state, subject)
    if rec is not None:
        if rec.get("finish") or rec.get("_finish") or "finish" in (rec.get("secret_fields") or ()):
            return "cleaning.finish:" + subject.id
        if S.is_waste_container(rec) or rec.get("type") == "recycling_guide" or rec.get("waste_kind"):
            return "cleaning.rules"
        return None
    words = _words(subject)
    if detail == "noise" or words & QUIET_WORDS:
        return "cleaning.quiet_hours"
    if detail == "color" or words & CLOTH_WORDS:
        return "cleaning.cloth_colors"
    if words & WASTE_WORDS:
        return "cleaning.rules"
    return None


dialogue.route("cleaning.keep", ("keep",), route_keep)
dialogue.route("cleaning.product", ("which", "status"), route_product)
dialogue.route("cleaning.how", ("how",), route_how)
