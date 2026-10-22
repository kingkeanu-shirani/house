"""Canonical questions to this domain's answerer (kernel v3.1 dialogue routes, docs/DIALOGUE.md "Routes").

`ask(target=<person>, aspect=..., about=...)` reaches the legacy "admin:<kind>:<arg>" answerer (knowledge.py) through
these routes, tried after the kernel's people routes in registration order:

    keep, a thing (detail "sentimental" or a word for it: is it special?)          admin:sentimental:<id>
    keep, a thing                                                                   admin:keep:<id> (grants donate:/discard:)
    intended, a thing                                                               admin:intentional:<id>
    when / amount, a thing, detail usage (or words such as often, use, used)        admin:usage:<id>
    where, a thing                                                                  admin:home:<id>
    where, a thing, detail last_seen                                                admin:last_seen:<id>
    owner, a thing                                                                  admin:owner:<id>
    where, words naming a household.designated key ("bills", "keys")              admin:designated:<key>
    how / preference, a room or words naming a household.schemes key               admin:scheme:<key>
    amount, a thing or words naming a household.par_levels key (its sku)           admin:par:<sku>
    who, words of a name on the household's mail or parcels or its roster          admin:who:<name>
    when / how / amount, the words trip, flight, baggage, luggage, allowance        admin:trip
    how / preference, detail count or words such as count, units, packs            admin:unit_preference

A question about a thing is routed here only when the thing carries what this answerer answers from (the facts a
decluttering, tidying or filing scene states: `_decision` for keep, `_sentimental` / `sentimental`, `_intentional` /
`_intended_by` / `work_in_progress`, `_uses_per_month` / `uses_per_month`, a `home` / home hint, a last-seen hint, an
owner), so the other domains' routes registered after this one (cleaning's keep for waste, manipulation's items, ...)
keep their questions. The people routes come first: with the people domain loaded its where: and owner: answerers
take every question about a thing, so admin:home / admin:owner / admin:last_seen are reached only without it (kernel
request filed: the people where: route answers only from its own storage preferences and habits). A route returns a
topic only for questions the answerer really answers; the answerer counts its own necessary / unnecessary statistics.
"""
from ...kernel import dialogue
from .common import roster, slug

USAGE_WORDS = {"often", "use", "used", "usage", "frequency", "frequently"}
SENTIMENTAL_WORDS = {"sentimental", "special", "memento", "keepsake"}
TRIP_WORDS = {"trip", "flight", "baggage", "luggage", "allowance", "airline", "travel"}
COUNT_WORDS = {"count", "units", "unit", "packs", "pack", "rolls"}


def _entity(state, subject):
    if subject is None or subject.kind != "entity" or not subject.id:
        return None
    return (state.get("entities") or {}).get(subject.id)


def _words(subject):
    if subject is None or subject.kind == "entity":
        return set()
    return set(subject.words or ())


def _detail_words(detail):
    return set(dialogue.words_of(detail)) if detail else set()


def _keyed(table, subject):
    """The key of a household table (designated spots, schemes, par levels) the subject names: an entity's sku,
    type or category, a room's id or type, or words equal to the key's words."""
    if not isinstance(table, dict) or not table or subject is None:
        return None
    keys = sorted(k for k in table if isinstance(k, str))
    if subject.kind == "entity":
        return None
    if subject.kind == "room" and subject.id:
        for key in keys:
            if key == subject.id:
                return key
    words = set(subject.words or ())
    for key in keys:
        if words and set(dialogue.words_of(key)) == words:
            return key
    return None


KEEP_FIELDS = ("_decision", "decision_obvious")
SENTIMENTAL_FIELDS = ("_sentimental", "sentimental")
INTENDED_FIELDS = ("_intentional", "_intended_by", "work_in_progress", "intentional_obvious")
USAGE_FIELDS = ("_uses_per_month", "uses_per_month", "usage_obvious")
HOME_FIELDS = ("home", "_home_hint", "home_hint", "home_obvious")
LAST_SEEN_FIELDS = ("_last_seen_hint",)
OWNER_FIELDS = ("owner", "_owner", "owner_obvious")


def _carries(rec, fields):
    """True when a record states one of these facts (a value other than None)."""
    return rec is not None and any(rec.get(f) is not None for f in fields)


def route_keep(state, pid, subject, aspect, detail, act):
    rec = _entity(state, subject)
    if (detail == "sentimental" or _detail_words(detail) & SENTIMENTAL_WORDS) and _carries(rec, SENTIMENTAL_FIELDS + KEEP_FIELDS):
        return "admin:sentimental:" + subject.id
    return "admin:keep:" + subject.id if _carries(rec, KEEP_FIELDS) else None


def route_intended(state, pid, subject, aspect, detail, act):
    return "admin:intentional:" + subject.id if _carries(_entity(state, subject), INTENDED_FIELDS) else None


def route_usage(state, pid, subject, aspect, detail, act):
    if _carries(_entity(state, subject), USAGE_FIELDS) and (detail == "usage" or _detail_words(detail) & USAGE_WORDS):
        return "admin:usage:" + subject.id
    return None


def route_where(state, pid, subject, aspect, detail, act):
    rec = _entity(state, subject)
    if rec is not None:
        if detail in ("last_seen", "last seen") or {"last", "seen"} <= _detail_words(detail):
            return "admin:last_seen:" + subject.id if _carries(rec, LAST_SEEN_FIELDS) else None
        return "admin:home:" + subject.id if _carries(rec, HOME_FIELDS) else None
    key = _keyed((state.get("household") or {}).get("designated"), subject)
    return "admin:designated:" + key if key else None


def route_owner(state, pid, subject, aspect, detail, act):
    return "admin:owner:" + subject.id if _carries(_entity(state, subject), OWNER_FIELDS) else None


def route_scheme(state, pid, subject, aspect, detail, act):
    key = _keyed((state.get("household") or {}).get("schemes"), subject)
    if key:
        return "admin:scheme:" + key
    if detail == "count" or (_detail_words(detail) | _words(subject)) & COUNT_WORDS:
        return "admin:unit_preference"
    if _words(subject) & TRIP_WORDS:
        return "admin:trip"
    return None


def route_amount(state, pid, subject, aspect, detail, act):
    pars = (state.get("household") or {}).get("par_levels") or {}
    rec = _entity(state, subject)
    if rec is not None:
        sku = rec.get("sku") or rec.get("type")
        if isinstance(sku, str) and sku in pars:
            return "admin:par:" + sku
        return route_usage(state, pid, subject, aspect, detail, act)
    key = _keyed(pars, subject)
    if key:
        return "admin:par:" + key
    if _words(subject) & TRIP_WORDS:
        return "admin:trip"
    return None


def route_when(state, pid, subject, aspect, detail, act):
    if _words(subject) & TRIP_WORDS:
        return "admin:trip"
    return route_usage(state, pid, subject, aspect, detail, act)


def known_names(state):
    """Normalized names a who-question about mail can be about: the printed addressee of every mail piece, envelope
    and parcel in the house (its recipient, the first line of its written address), the roster (residents' names,
    aliases and maiden names), former occupants and the household's collective names."""
    names = set()
    for rec in (state.get("entities") or {}).values():
        if rec.get("mail_piece") or rec.get("envelope") or rec.get("package"):
            for key in ("recipient", "to_text"):
                value = rec.get(key)
                if isinstance(value, str) and value.strip():
                    names.add(slug(value.strip().splitlines()[0]))
    for values in roster(state).values():
        names.update(values)
    hh = state.get("household") or {}
    names.update(slug(v) for v in hh.get("former_occupants", []) if isinstance(v, str))
    names.update(slug(v) for v in hh.get("household_names", ["current resident", "the household"]) if isinstance(v, str))
    names.discard("")
    return names


def route_who(state, pid, subject, aspect, detail, act):
    """Who someone named on the household's mail is ("who is K. Patel?"): only words that are such a name (a question
    about who is at the door or on the phone is not this answerer's)."""
    if subject is not None and subject.kind == "word" and subject.words:
        key = slug(" ".join(subject.words))
        if key in known_names(state):
            return "admin:who:" + key
    return None


dialogue.route("admin.keep", ("keep",), route_keep)
dialogue.route("admin.intended", ("intended",), route_intended)
dialogue.route("admin.when", ("when",), route_when)
dialogue.route("admin.amount", ("amount",), route_amount)
dialogue.route("admin.where", ("where",), route_where)
dialogue.route("admin.owner", ("owner",), route_owner)
dialogue.route("admin.scheme", ("how", "preference"), route_scheme)
dialogue.route("admin.who", ("who",), route_who)
