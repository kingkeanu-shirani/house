"""Canonical questions to this domain's answerer (kernel v3.1 dialogue routes, docs/DIALOGUE.md "Routes").

`ask(target=<person>, aspect=..., about=...)` reaches the legacy `appliance:` answerer (status.py) through these
routes, tried after the kernel's people routes in registration order:

    intended, an appliance (or anything) whose intent the household knows (`_intended` set)   appliance:intended:<id>
    how or when, an appliance with household appliance rules (a dishwasher's run hours,
        full loads only), or the words "dishwasher"                                           appliance:rules:<kind>

A route returns a topic only for questions the answerer really answers; the answerer counts its own necessary /
unnecessary question statistics as before. Preferences ("preference" with a detail) are the people domain's `pref:`
route, which reads the same preferences.

Questions with an act are permission questions: the kernel's own route turns (ok, act self_clean, oven) into
`permission:self_clean:<oven>` before any domain route is tried, so this domain answers those topics itself through
answerers registered on the longer prefixes `permission:self_clean:` and `permission:defrost:` (status.py): the
household's actual answer (`_oven.self_clean_ok`, `_defrost_ok`) when the scenario sets one, else the generic
permission rule (an adult who holds that permission grants it).
"""
from ...kernel import dialogue

RULE_WORDS = {"dishwasher", "dishwashers"}


def _entity(state, subject):
    if subject is None or subject.kind != "entity" or not subject.id:
        return None
    return (state.get("entities") or {}).get(subject.id)


def route_intended(state, pid, subject, aspect, detail, act):
    rec = _entity(state, subject)
    if rec is not None and "_intended" in rec:
        return "appliance:intended:" + subject.id
    return None


def _rules(state):
    return ((state.get("household") or {}).get("appliance_rules") or {})


def route_rules(state, pid, subject, aspect, detail, act):
    rules = _rules(state)
    if not rules:
        return None
    rec = _entity(state, subject)
    if rec is not None:
        kind = rec.get("ac_kind")
        if kind in rules:
            return "appliance:rules:" + kind
        return None
    words = set(subject.words or ()) if subject is not None else set()
    if words & RULE_WORDS and "dishwasher" in rules:
        return "appliance:rules:dishwasher"
    return None


dialogue.route("appliance.intended", ("intended",), route_intended)
dialogue.route("appliance.rules", ("how", "when"), route_rules)
