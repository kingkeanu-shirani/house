"""Canonical questions to this domain's answerer (kernel v3.1 dialogue routes, CONVENTIONS 8, docs/DIALOGUE.md).

`ask(target=<person>, aspect=..., about=...)` reaches the legacy "pet:<key>[:<id>]" answerer (perception.py) through
these routes, tried after the kernel's people routes and permission route, in registration order:

    which / how, a pet (detail diet, or words such as food, diet, eat)                     pet:diet:<pet>
    amount, a pet, detail treats (or words treat, snack, biscuit)                           pet:treats:<pet>
    amount, a pet, detail meds (or words such as medicine, pill, dose)                      pet:meds:<pet>
    amount, a pet (no detail, or detail portion / food)                                     pet:portion:<pet>
    which / when / how, a pet, detail meds (or medicine words); or a pet medication bottle  pet:meds:<pet>
    status, a pet, detail fed (or words fed, feed, meal, breakfast, dinner)                 pet:fed:<pet>
    status, a pet (no detail, or detail health / condition)                                 pet:health:<pet>
    intended / keep / why, a pet (is it shut in on purpose?)                                pet:confined:<pet>
    where, a pet, detail stash (or words stash, hide, hiding, hoard)                        pet:stash:<pet>
    how / which / when / ok (no act), a pet, detail walk (or words walk, route)             pet:walk:<pet>
    which / how, a cat or a litter box, detail litter (or litter words)                     pet:litter:<pet>
    how / where, a pet-waste bag, dog poop or the words poop / waste / litter / bedding     pet:waste_rules
    which / amount / how, a reptile enclosure (its temperature setpoint)                    pet:setpoint:<enclosure>
    when / how, an aquarium (its light schedule)                                            pet:light:<tank>
    ok (no act) / status, the asked person, detail dogs (or words dog, dogs)                pet:dog_ok

A route returns a topic only for a question the answerer answers (the pet, tank or enclosure exists); the answerer
counts its own question statistics as before. "ok" with an act (ok, act give_treat, a pet) is the kernel's permission
route (permission:give_treat:<pet>, the approval give_treat asks for); "when" about a pet reaches the people domain's
schedule route first when it is loaded, "preference" with a detail its preference route: those stay theirs.
"""
from ...kernel import dialogue


def _norm(words):
    """Normalized forms of keywords, as the dialogue layer normalizes subjects and details (singular, synonyms:
    "litter" becomes "trash" there)."""
    out = set()
    for word in words:
        out.add(dialogue.phrase(word, stop=False))      # a phrase as one key ("cat litter" -> "cat_trash")
    return frozenset(w for w in out if w)


DIET = _norm(("diet", "food", "foods", "eat", "eats", "feed", "kibble", "dietary"))
TREATS = _norm(("treat", "treats", "snack", "biscuit", "chew"))
MEDS = _norm(("meds", "medication", "medicine", "pill", "pills", "dose", "dosage", "tablet", "prescription"))
PORTION = _norm(("portion", "food", "serving", "how much food"))
FED = _norm(("fed", "feed", "feeding", "meal", "breakfast", "dinner", "supper"))
HEALTH = _norm(("health", "condition", "sick", "ill", "well", "unwell", "hurt", "symptom"))
STASH = _norm(("stash", "hide", "hiding", "hides", "hoard", "hidden"))
WALK = _norm(("walk", "walks", "route", "walking"))
LITTER = _norm(("litter", "cat litter", "litter box"))
# "litter" normalizes to "trash" (the dialogue synonyms): only "cat litter" names it among words, or a pet subject
WASTE = _norm(("poop", "dog poop", "pet waste", "cat litter", "used litter", "pet bedding", "droppings", "feces"))
DOGS = _norm(("dog", "dogs", "puppy"))


def _pet(state, subject):
    if subject is not None and subject.kind == "pet" and subject.id in (state.get("pets") or {}):
        return subject.id
    return None


def _entity(state, subject):
    if subject is None or subject.kind != "entity" or not subject.id:
        return None, None
    rec = (state.get("entities") or {}).get(subject.id)
    return (subject.id, rec) if rec is not None else (None, None)


def _words(subject):
    if subject is None or subject.kind == "entity":
        return frozenset()
    return frozenset(subject.words or ())


def _detail(detail, words):
    """True when the canonical detail (its phrase, or one of its words) is one of `words`."""
    if not detail:
        return False
    return detail in words or dialogue.phrase(detail, stop=False) in words or         bool(set(dialogue.words_of(detail, stop=False)) & words)


def _named(subject, words):
    """True when a word subject's phrase, or one of its words, is one of `words`."""
    found = _words(subject)
    return bool(found & words) or "_".join(subject.words or ()) in words if found else False


def route_feeding(state, pid, subject, aspect, detail, act):
    pet = _pet(state, subject)
    if pet is None:
        return None
    if aspect in ("which", "how"):
        if _detail(detail, MEDS):
            return "pet:meds:" + pet
        if _detail(detail, LITTER):
            return "pet:litter:" + pet
        if _detail(detail, WALK):
            return "pet:walk:" + pet
        if detail is None or _detail(detail, DIET) or detail == "diet":
            return "pet:diet:" + pet
        return None
    if aspect == "amount":
        if _detail(detail, TREATS):
            return "pet:treats:" + pet
        if _detail(detail, MEDS):
            return "pet:meds:" + pet
        if detail is None or detail == "portion" or _detail(detail, PORTION):
            return "pet:portion:" + pet
        return None
    if aspect == "when":
        if _detail(detail, MEDS):
            return "pet:meds:" + pet
        if _detail(detail, WALK):
            return "pet:walk:" + pet
        return None
    return None


def route_status(state, pid, subject, aspect, detail, act):
    pet = _pet(state, subject)
    if pet is not None:
        if _detail(detail, FED):
            return "pet:fed:" + pet
        if detail is None or _detail(detail, HEALTH):
            return "pet:health:" + pet
        return None
    return _dog_ok(state, pid, subject, detail)


def route_confined(state, pid, subject, aspect, detail, act):
    pet = _pet(state, subject)
    return "pet:confined:" + pet if pet is not None else None


def route_stash(state, pid, subject, aspect, detail, act):
    pet = _pet(state, subject)
    if pet is not None and (detail is None or _detail(detail, STASH)):
        return "pet:stash:" + pet
    return None


def route_walk(state, pid, subject, aspect, detail, act):
    if act:
        return None             # ok with an act is the kernel's permission route (permission:walk_pet:<pet>)
    pet = _pet(state, subject)
    if pet is not None and _detail(detail, WALK):
        return "pet:walk:" + pet
    if aspect == "ok":
        return _dog_ok(state, pid, subject, detail)
    return None


def route_things(state, pid, subject, aspect, detail, act):
    """Questions about the domain's things: a medication bottle (whose pills, when), a litter box or bag (which
    litter), waste (how or where it goes), an enclosure's setpoint, a tank's light."""
    eid, rec = _entity(state, subject)
    if rec is not None:
        if rec.get("pet_medication") and aspect in ("which", "when", "amount", "how"):
            owner = rec.get("prescribed_for")
            return "pet:meds:" + owner if owner in (state.get("pets") or {}) else None
        if rec.get("category") in ("litter_box", "litter") and aspect in ("which", "how"):
            owner = rec.get("owner") or next((p for p, r in sorted((state.get("pets") or {}).items())
                                              if r.get("species") == "cat"), None)
            if rec.get("category") == "litter_box" and aspect == "how":
                return "pet:waste_rules"
            return "pet:litter:" + owner if owner else None
        if (rec.get("category") == "pet_waste" or rec.get("pet_waste")) and aspect in ("how", "where"):
            return "pet:waste_rules"
        if rec.get("category") == "reptile_enclosure" and aspect in ("which", "amount", "how"):
            return "pet:setpoint:" + eid
        if rec.get("category") == "aquarium" and aspect in ("when", "how"):
            return "pet:light:" + eid
        return None
    if aspect in ("how", "where") and _named(subject, WASTE):
        return "pet:waste_rules"
    return None


def _dog_ok(state, pid, subject, detail):
    """Is the asked person comfortable with dogs (a guest)?"""
    about_them = subject is None or (subject.kind == "person" and subject.id == pid)
    if (about_them and _detail(detail, DOGS)) or (_named(subject, DOGS) and not _pet(state, subject)):
        return "pet:dog_ok"
    return None


for _name, _aspects, _fn in (
        ("pets.feeding", ("which", "how", "amount", "when"), route_feeding),
        ("pets.status", ("status",), route_status),
        ("pets.confined", ("intended", "keep", "why"), route_confined),
        ("pets.stash", ("where",), route_stash),
        ("pets.walk", ("how", "which", "when", "ok"), route_walk),
        ("pets.things", ("which", "when", "amount", "how", "where"), route_things)):
    dialogue.route(_name, _aspects, _fn)
