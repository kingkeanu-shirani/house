"""Canonical questions to people: the `ask` schema, subject resolution, facts, routes and necessity
(docs/design/TIER1_SWEEP.md sections 4 and 16.1; docs/DIALOGUE.md).

    ask(target=<person>, about=<subject>, aspect=<aspect>, detail=?, act=?, options=?, channel=?, language=?,
        question=?<free text, logged only>, topic=?<legacy>)

The legacy `topic` form is core's unchanged code path (systems/core.py `legacy_answer`); everything here is the
canonical form. One `ask` (answer(ctx, args)) runs, in order:

  1. reachability by channel (voice: same room; phone / text: the robot's network and a phone, wherever the person
     is -- at home or away -- and a person who can hear for a call; written: next to the person, a lit room and
     working cameras; awake), refined by the people hook `dialogue_channel`; a voice or phone question through a
     failed speaker gets no answer (time spent, nothing counted, the fault detected as speech detects it);
  2. language (people.<pid>.languages, default ["en"]; hook `dialogue_comprehends`): no shared language is an
     answer "I don't understand" in their language that counts nothing;
  3. subject resolution (resolve_subject): a handle (realism turned it into the true id), a room / person / pet /
     calendar-event id, "it" / "that" (the last subject discussed with this person), "request" (the current
     request: a subject of kind "request" whose bound target -- the request's subject, else the intent's target --
     is used for fact matching only and never shown), "house", or words matched lexically through types, labels,
     look-alike kinds, aliases and the synonym table of data/vocab/*.json (a word names a thing by the head noun of
     one of its names: "dryer" names a clothes dryer, not dryer sheets; the other words must be its modifiers,
     colour or material words);
  4. fact match (match_fact): user_simulator.facts plus the facts implied by household.calendar events, most
     specific first (entity id > type > room / person / pet / event > kind / topic / alias), then a matching detail,
     then list order;
  5. word subjects: for an entity-directed aspect (owner, where, keep, intended, ok, why, how, status) a word naming
     two or more household things gets a clarify-back (4 s, counts nothing) unless a fact about the word itself
     answers -- decided from the word, whether or not one of the things has a fact (a clarify-back tells nothing about
     what the spec models); `options` narrow the things the word names (one left: the question is about it); a word
     naming exactly one household entity with an entity fact is answered by it;
  6. routes: the canonical question is offered to the registered routes (route()) in order; the first returning a
     legacy topic runs that answerer with today's code, which counts its own question statistics;
  7. unknown: "I don't know.", counted unnecessary (today's rule) and questions_unmatched (diagnostic only);
  8. recording: facts["q:<aspect>:<subject>[:<detail>]"], facts.asked, facts.last_answer, one dialogue_log record
     (the route topic stays in it; the agent's answer has no route), the conversation context dialogue.<pid>.

An answer's "about" is the agent's own argument (its handle, a room / person / pet id, "request", "house", or its
words), never an id the agent did not give. A person outside a fact's known_by says "I don't know." (an author's
"otherwise" text excepted), exactly as for a question nothing models.

Necessity (necessary()) is one framework for every fact: not_known (default) / always / never / ambiguous /
no_history / {"pred": P} / {"unless": [P, ...]}; asking a person outside the fact's known_by is unnecessary.
Matching reads registry data, entity types, labels, look-alike groups and the fact list only, never hidden fields.
The vocabulary (vocabulary()) is loaded once per process from data/vocab/*.json in file-name order; a key defined in
two files fails at load. Deterministic: every draw is a hash of (seed, fact, asker, turn).
"""
import copy
import hashlib
import json
import re
from collections import namedtuple
from pathlib import Path

from .errors import ActionError, require
from .plugins import ANSWERERS, run_hooks
from .predicates import check, root_room

# The resolved subject of a question (sweep 4.4): kind is "entity" | "room" | "person" | "pet" | "event" | "house" |
# "request" | "word"; id is the true id (None for words, the house and an unbound request); words are the normalized
# words of a word subject ("request" marks a subject that came from the current request).
Subject = namedtuple("Subject", ["kind", "id", "words"], defaults=(None, ()))

VOCAB_DIR = Path(__file__).resolve().parents[1] / "data" / "vocab"
ASPECTS = ("which", "owner", "where", "preference", "amount", "when", "ok", "keep", "intended", "why", "how", "status",
           "who", "said")
REQUEST_ASPECTS = ("help",)
CHANNELS = ("voice", "phone", "text", "written")
NOT_AVAILABLE = "That person is not available here"
ASLEEP = "They are asleep"
ASK_TIME_S = 4.0
LOG_CAP = 300
# fact "about" kinds by specificity (sweep 4.3 step 4)
SPECIFICITY = {"id": 5, "request": 5, "type": 4, "room": 3, "person": 3, "pet": 3, "event": 3, "house": 3,
               "kind": 1, "topic": 1, "alias": 1}
NECESSARY_MODES = ("not_known", "always", "never", "ambiguous", "no_history")
KNOWN_BY = ("everyone", "adults", "requester", "residents")
FACT_KEYS = {"id", "about", "aspect", "detail", "value", "said", "known_by", "otherwise", "necessary",
             "observable_via", "aliases", "grants", "sets", "reliability", "wrong_value", "candidates", "doc"}
TIERS = ("glance", "inspect", "read", "measure")
# names too generic to tell a resident which thing is meant (never matched as a word, never a "type:" name)
GENERIC_NAMES = {"object", "portable_object", "item", "thing", "generic_item", "entity", "fixture", "furniture",
                 "device", "appliance", "container", "tool", "part", "structure", "surface"}
VISITOR_ROLES = ("visitor", "stranger", "solicitor", "contractor", "delivery", "courier", "paramedic", "responder",
                 "guest", "worker", "gardener", "plumber")
MINOR_ROLES = ("child", "toddler", "infant", "teen")
IT_WORDS = ("it", "that", "this", "them", "those")
# aspects about one particular thing: a word naming several household things is asked back ("which one?")
ENTITY_ASPECTS = ("owner", "where", "keep", "intended", "ok", "why", "how", "status")
UNKNOWN = "I don't know."
NO_ANSWER = "(no answer)"
HOUSE_WORDS = ("house", "home", "household", "the house", "the home")
CELL_ARG_RE = re.compile(r"^\s*\d+\s*,\s*\d+\s*$")
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
IRREGULAR = {"children": "child", "people": "person", "men": "man", "women": "woman", "mice": "mouse",
             "feet": "foot", "teeth": "tooth", "geese": "goose", "knives": "knife", "wives": "wife", "lives": "life",
             "leaves": "leaf", "shelves": "shelf", "halves": "half", "loaves": "loaf", "scarves": "scarf",
             "wolves": "wolf", "calves": "calf", "thieves": "thief", "dice": "die", "glasses": "glasses",
             "pants": "pants", "trousers": "trousers", "jeans": "jeans", "shorts": "shorts", "scissors": "scissors",
             "tongs": "tongs", "pliers": "pliers", "clothes": "clothes", "news": "news", "series": "series",
             "species": "species", "dishes": "dish", "boxes": "box", "buses": "bus", "potatoes": "potato",
             "tomatoes": "tomato", "heroes": "hero", "echoes": "echo", "volcanoes": "volcano",
             "cookies": "cookie", "movies": "movie", "brownies": "brownie", "smoothies": "smoothie",
             "hoodies": "hoodie", "onesies": "onesie", "veggies": "veggie", "calories": "calorie",
             "beanies": "beanie", "selfies": "selfie", "zombies": "zombie", "goalies": "goalie"}
KEEP_S = ("ss", "us", "is", "os")


# ----------------------------------------------------------------------------------------------------------------
# vocabulary (data/vocab/*.json)

_VOCAB = {}
DICT_SECTIONS = ("aspects", "requests", "details", "synonyms", "issues", "states", "quantities", "severity_aliases",
                 "category_classes", "not_understood", "household_units")
LIST_SECTIONS = ("severities", "issue_classes", "stopwords")


def load_vocabulary(paths=None):
    """Merge vocabulary files (default: data/vocab/core.json, then every other data/vocab/*.json sorted by file name, so
    the kernel's words come first in file order -- suggestion lists, the docs tables -- and a domain's word that
    collides with one of them is the one dropped from the index). Dict sections merge by key and a key defined in two
    files is an error (ValueError); list sections are unioned in file order; "severities" may only be defined once.
    Also builds the lookup indexes ("_index"). Unknown sections are an error."""
    if paths is not None:
        files = [Path(p) for p in paths]
    else:
        found = sorted(VOCAB_DIR.glob("*.json"))
        files = [p for p in found if p.name == "core.json"] + [p for p in found if p.name != "core.json"]
    vocab = {name: {} for name in DICT_SECTIONS}
    vocab.update({name: [] for name in LIST_SECTIONS})
    owner = {}
    sources = []
    for path in files:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        sources.append(Path(path).name)
        for section, value in data.items():
            if section == "about":
                continue
            if section in DICT_SECTIONS:
                if not isinstance(value, dict):
                    raise ValueError("{}: section {} must be an object".format(Path(path).name, section))
                for key, item in value.items():
                    where = (section, key)
                    if where in owner:
                        raise ValueError("vocabulary {}.{} defined in both {} and {}".format(
                            section, key, owner[where], Path(path).name))
                    owner[where] = Path(path).name
                    vocab[section][key] = item
            elif section in LIST_SECTIONS:
                if not isinstance(value, list):
                    raise ValueError("{}: section {} must be a list".format(Path(path).name, section))
                if section == "severities" and vocab["severities"]:
                    raise ValueError("vocabulary severities defined twice ({})".format(Path(path).name))
                for item in value:
                    if item not in vocab[section]:
                        vocab[section].append(item)
            else:
                raise ValueError("{}: unknown vocabulary section {!r}".format(Path(path).name, section))
    vocab["sources"] = sources
    vocab["_index"] = _build_index(vocab)
    return vocab


def _index_words(table, owner_name, words, target, problems, vocab):
    for word in words:
        key = phrase(word, stop=False, vocab=vocab)
        if not key:
            continue
        found = table.get(key)
        if found is not None and found != target:
            problems.append("{} word {!r} names both {} and {}".format(owner_name, key, found, target))
            continue
        table[key] = target


def _build_index(vocab):
    """word -> canonical name tables for aspects, details, issues, states, severities and units. A word naming two
    different things in one table is recorded in index["problems"] (tools/vocab.py --check fails on it; the first
    definition wins at run time)."""
    problems = []
    index = {"aspects": {}, "requests": {}, "details": {}, "issues": {}, "states": {}, "severities": {},
             "units": {}, "unit_quantities": {}, "problems": problems}
    for table, section in (("aspects", "aspects"), ("requests", "requests"), ("details", "details"),
                           ("issues", "issues"), ("states", "states")):
        for name in vocab[section]:
            _index_words(index[table], section, [name], name, problems, vocab)
        for name, spec in vocab[section].items():
            _index_words(index[table], section, (spec or {}).get("synonyms") or (), name, problems, vocab)
    for name in vocab["severities"]:
        index["severities"][name] = name
    for word, name in vocab["severity_aliases"].items():
        if name not in vocab["severities"]:
            problems.append("severity alias {!r} names unknown severity {!r}".format(word, name))
        index["severities"][str(word).lower()] = name
    for quantity, spec in vocab["quantities"].items():
        units = {}
        for unit in (spec.get("units") or {}):
            units[unit.lower()] = unit
        for alias, unit in (spec.get("aliases") or {}).items():
            if unit not in (spec.get("units") or {}):
                problems.append("unit alias {!r} of {} names unknown unit {!r}".format(alias, quantity, unit))
                continue
            units[str(alias).lower()] = unit
        if spec.get("canonical") not in (spec.get("units") or {}):
            problems.append("quantity {} has no canonical unit among its units".format(quantity))
        index["units"][quantity] = units
        for word in units:
            index["unit_quantities"].setdefault(word, []).append(quantity)
    for name, spec in vocab["issues"].items():
        if (spec or {}).get("class") not in vocab["issue_classes"]:
            problems.append("issue {} has unknown class {!r}".format(name, (spec or {}).get("class")))
    return index


def vocabulary():
    """The loaded vocabulary (aspects, requests, details, synonyms, issues, states, quantities, severities, ...; and
    "_index", the word lookup tables). Loaded once per process; callers must not mutate it."""
    if not _VOCAB:
        _VOCAB["v"] = load_vocabulary()
    return _VOCAB["v"]


# ----------------------------------------------------------------------------------------------------------------
# lexical normalization (sweep 4.4)

def singular(word):
    """A word's singular by the plain English rules (ies -> y, irregular plurals, es after s/x/ch/sh/z/o, s)."""
    w = word
    if w in IRREGULAR:
        return IRREGULAR[w]
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("es") and (w[:-2].endswith(("s", "x", "z", "ch", "sh")) and not w.endswith("ses")):
        return w[:-2]
    if len(w) > 4 and w.endswith("sses"):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith(KEEP_S):
        return w[:-1]
    return w


def words_of(text, stop=True, vocab=None):
    """The normalized words of a text: lower case, punctuation and underscores as spaces, singular, stop words
    dropped (stop=True), then the vocabulary's synonym table applied to the longest matching run of words."""
    if text is None:
        return []
    raw = str(text).lower().replace("_", " ").replace("-", " ")
    tokens = [singular(t.replace("'", "")) for t in _TOKEN_RE.findall(raw)]
    vocab = vocab if vocab is not None else vocabulary()
    if stop:
        stops = vocab["stopwords"]
        tokens = [t for t in tokens if t not in stops]
    synonyms = vocab["synonyms"]
    out, i = [], 0
    while i < len(tokens):
        for n in (3, 2, 1):
            if i + n <= len(tokens):
                key = "_".join(tokens[i:i + n])
                if key in synonyms:
                    out.extend(str(synonyms[key]).split("_"))
                    i += n
                    break
        else:
            out.append(tokens[i])
            i += 1
    return out


def phrase(text, stop=True, vocab=None):
    """The normalized phrase of a text: its normalized words joined by "_" ("Washing machines" -> "washing_machine",
    "the couch" -> "sofa")."""
    return "_".join(words_of(text, stop=stop, vocab=vocab))


def ngrams(words):
    """Every contiguous run of words joined by "_" (["comfort", "blanket"] -> comfort, blanket, comfort_blanket)."""
    out = set()
    for i in range(len(words)):
        for j in range(i + 1, len(words) + 1):
            out.add("_".join(words[i:j]))
    return out


def canonical_aspect(word):
    """The aspect a word names (the 14 aspects, their synonyms, or "help"), or None."""
    if not isinstance(word, str) or not word.strip():
        return None
    index = vocabulary()["_index"]
    key = phrase(word, stop=False)
    return index["aspects"].get(key) or index["requests"].get(key)


def canonical_detail(word):
    """The detail slot a word names (vocabulary details and synonyms), else the word's own phrase; None for none."""
    if word is None or (isinstance(word, str) and not word.strip()):
        return None
    key = phrase(word, stop=False)
    return vocabulary()["_index"]["details"].get(key, key) or None


def canonical_act(word):
    """An act verb as a scope word: lower case, spaces as underscores ("pick up" -> "pick_up")."""
    if word is None or (isinstance(word, str) and not word.strip()):
        return None
    return "_".join(re.findall(r"[a-z0-9]+", str(word).lower())) or None


# ----------------------------------------------------------------------------------------------------------------
# who knows what, requests and the household

def _people(state):
    return state.get("people") or {}


def authority(person):
    if not person:
        return 0
    if "authority" in person:
        try:
            return int(person["authority"])
        except (TypeError, ValueError):
            return 0
    return {"adult": 2, "elderly": 1, "guest": 1}.get(person.get("role", "adult"), 0)


def is_minor(person):
    role = (person or {}).get("role", "adult")
    age = (person or {}).get("age")
    return role in MINOR_ROLES or (isinstance(age, (int, float)) and age < 18)


def is_adult(person):
    """A resident adult or caregiver (authority >= 2, or a caregiver), not a minor, not a visitor (the people domain's
    is_authorized_adult, computed here so the kernel works without that domain)."""
    return bool(person) and (authority(person) >= 2 or bool(person.get("caregiver"))) and not is_minor(person) \
        and person.get("role", "adult") not in VISITOR_ROLES


def is_resident(person):
    return bool(person) and person.get("role", "adult") not in VISITOR_ROLES and not person.get("visiting")


def current_request(state):
    """The current request record (perception.current_request: the active phase's latest, else the last), or None."""
    from .perception import current_request as current
    return current(state)


def requester(state):
    """Who gave the current instruction (the one rule for known_by "requester" and the evaluation's ask_whom
    "requester", kernel/evaluation.py): the current request's "from" (perception.current_request: the active phase's
    request in a multi-phase episode), else user_simulator.intent.requester, else the state's "requester" (the
    household's default requester, schema.normalize; people's helpers set it)."""
    request = current_request(state)
    if request and request.get("from"):
        return request["from"]
    intent = (state.get("user_simulator") or {}).get("intent") or {}
    return intent.get("requester") or state.get("requester")


def knows(state, pid, known_by):
    """Whether person pid is among a fact's known_by: "everyone", "adults" (default), "requester", "residents" or a
    list of person ids."""
    person = _people(state).get(pid)
    if known_by is None:
        known_by = "adults"
    if isinstance(known_by, (list, tuple)):
        return pid in known_by
    if known_by == "everyone":
        return True
    if known_by == "requester":
        return requester(state) == pid
    if known_by == "residents":
        return is_resident(person)
    return is_adult(person)


def _calendar_events(state):
    calendar = (state.get("household") or {}).get("calendar") or {}
    events = calendar.get("events") if isinstance(calendar, dict) else None
    return [e for e in events or [] if isinstance(e, dict) and isinstance(e.get("id"), str)]


# ----------------------------------------------------------------------------------------------------------------
# entity names as a resident understands them

_NAMES_MEMO = {}


def _registry(env):
    return getattr(env, "registry", None) if env is not None else None


def _group_words(env, record):
    """Descriptor words of the look-alike group a record's type belongs to (what an unresolved look-alike shows)."""
    reg = _registry(env)
    if reg is None or not hasattr(reg, "lookalike_types"):
        return ()
    memo = _NAMES_MEMO.setdefault(("groups", id(reg)), {})
    if not memo:
        groups = reg.lookalike_groups()
        for name, types in reg.lookalike_types().items():
            descriptor = (groups.get(name) or {}).get("descriptor") or name.replace("_", " ")
            for t in types:
                memo.setdefault(t, []).append(descriptor)
        memo["__built__"] = True
    return tuple(memo.get(record.get("type"), ()))


def entity_names(env, record):
    """(phrases, words) a resident understands as naming this thing: its type, its non-generic is_a names, its label,
    colour and material words and its look-alike group's descriptor (memoized per name signature)."""
    is_a = record.get("is_a") or ()
    key = (record.get("type"), tuple(is_a) if isinstance(is_a, (list, tuple)) else (), record.get("label"),
           record.get("color"), record.get("material"), record.get("category"), id(_registry(env)))
    found = _NAMES_MEMO.get(key)
    if found is not None:
        return found
    phrases, words = set(), set()
    sources = [record.get("type"), record.get("label")]
    sources += [n for n in (is_a if isinstance(is_a, (list, tuple)) else ()) if isinstance(n, str)]
    sources += list(_group_words(env, record))
    for source in sources:
        if not isinstance(source, str) or source in GENERIC_NAMES:
            continue
        p = phrase(source)
        if p and p not in GENERIC_NAMES:
            phrases.add(p)
            words.update(p.split("_"))
    for extra in (record.get("color"), record.get("material")):
        if isinstance(extra, str) and extra.strip():
            words.update(words_of(extra))
    words -= set(vocabulary()["stopwords"])
    found = (frozenset(phrases), frozenset(words))
    if len(_NAMES_MEMO) > 20000:
        _NAMES_MEMO.clear()
    _NAMES_MEMO[key] = found
    return found


def _head_names(phrases, head):
    """True when one of the phrases has `head` as its head noun (its last word): "dryer" heads "clothes_dryer", not
    "dryer_sheet"."""
    return any(p == head or p.endswith("_" + head) for p in phrases)


def names_entity(env, record, subject_words):
    """Whether the words of a subject name this thing for a resident: the whole phrase is one of its names, or the
    subject's head noun (its last word) is the head noun of one of its names and every other subject word is one of
    its words -- a modifier, colour or material ("blue mug" names a blue mug, not every mug; "dryer" a clothes dryer,
    not dryer sheets; "bed" a bed, not a bed pillow)."""
    if not subject_words:
        return False
    phrases, words = entity_names(env, record)
    if "_".join(subject_words) in phrases:
        return True
    return _head_names(phrases, subject_words[-1]) and all(w in words for w in subject_words[:-1])


def named_entities(env, state, subject_words):
    """Sorted ids of the household entities the subject words name (people's understanding; parts and debris
    excluded)."""
    out = []
    for eid, record in state.get("entities", {}).items():
        if record.get("relation") == "part" or record.get("category") in ("debris", "dirt", "spill"):
            continue
        if names_entity(env, record, subject_words):
            out.append(eid)
    return sorted(out)


def describe(state, eid):
    """How a person describes a thing: {"desc": "mug (on the kitchen counter, kitchen)", "room": room id}."""
    entities = state.get("entities", {})
    record = entities.get(eid) or {}
    label = str(record.get("label") or record.get("type") or "thing").replace("_", " ")
    room = root_room(state, eid)
    room_label = str((state.get("rooms", {}).get(room) or {}).get("label") or room or "").replace("_", " ")
    where = []
    parent = record.get("location")
    if parent in entities:
        relation = record.get("relation") or "on"
        word = {"in": "in", "on": "on", "under": "under", "behind": "behind", "hung": "hanging on",
                "part": "part of"}.get(relation, relation)
        where.append("{} the {}".format(word, str(entities[parent].get("label") or "thing").replace("_", " ")))
    elif parent in _people(state):
        where.append("with {}".format(_people(state)[parent].get("label") or parent))
    if room_label:
        where.append(room_label)
    return {"desc": "{} ({})".format(label, ", ".join(where)) if where else label, "room": room}


def appearances(state, eids):
    """How a person tells several things apart without saying where they are now (they know their things, not where
    each one was left): the label, then the colour, then the material, as far as needed to tell them apart."""
    entities = state.get("entities", {})
    rows = []
    for eid in eids:
        record = entities.get(eid) or {}
        color, material = record.get("color"), record.get("material")
        rows.append((str(record.get("label") or record.get("type") or "thing").replace("_", " "),
                     color if isinstance(color, str) else "", material if isinstance(material, str) else ""))
    out = []
    for label, color, material in rows:
        desc = label
        if sum(1 for r in rows if r[0] == label) > 1 and color and color.lower() not in label.lower():
            desc = "{} {}".format(color, label)
            if sum(1 for r in rows if r[:2] == (label, color)) > 1 and material:
                desc += " ({})".format(material)
        out.append(desc)
    return out


def _refers_to(state, value, out=None, depth=0):
    """Descriptions of the entities a fact value names (people speak in descriptions, never handles)."""
    out = [] if out is None else out
    if depth > 4:
        return out
    if isinstance(value, str):
        if value in state.get("entities", {}) and len(out) < 8 and all(o.get("_id") != value for o in out):
            item = describe(state, value)
            item["_id"] = value
            out.append(item)
    elif isinstance(value, dict):
        for key in sorted(value):
            _refers_to(state, value[key], out, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _refers_to(state, item, out, depth + 1)
    return out


# ----------------------------------------------------------------------------------------------------------------
# subjects (sweep 4.4)

def _subject_dict(subject):
    return {"kind": subject.kind, "id": subject.id, "words": list(subject.words)}


def _subject_from(data):
    if not isinstance(data, dict) or data.get("kind") not in ("entity", "room", "person", "pet", "event", "house",
                                                              "request", "word"):
        return None
    return Subject(data["kind"], data.get("id"), tuple(data.get("words") or ()))


def subject_text(subject):
    """The subject part of a canonical key: the id, "house", "request" (with the request record's id: "request:r2"), or
    the words joined by "_"."""
    if subject.kind == "request":
        return "request" + (":" + str(subject.id) if subject.id is not None else "")
    if subject.id is not None:
        return str(subject.id)
    if subject.kind == "house":
        return subject.kind
    return "_".join(subject.words) or "unknown"


def _request_subject(state):
    """The subject "request": kind "request", id the current request record's id (None for a task that only has an
    intent). What it stands for (request_target) is used to match facts and routes, never shown."""
    request = current_request(state) or {}
    if request or ((state.get("user_simulator") or {}).get("intent") or {}):
        rid = request.get("id")
        return Subject("request", rid if isinstance(rid, str) and rid else None, ("request",))
    return None


def request_target(state, subject):
    """The id a request subject stands for: its request record's subject, else user_simulator.intent.target (hidden
    truth: for fact matching and routes only)."""
    record = None
    if subject.id is not None:
        record = next((r for r in state.get("requests") or () if isinstance(r, dict) and r.get("id") == subject.id),
                      None)
    if record is None:
        record = current_request(state) or {}
    target = record.get("subject")
    if not isinstance(target, str) or not target:
        target = ((state.get("user_simulator") or {}).get("intent") or {}).get("target")
    return target if isinstance(target, str) and target else None


def bound_subject(state, subject):
    """A request subject as the thing it stands for (entity / room / person / pet, words ("request",)) for matching;
    any other subject as it is."""
    if subject is None or subject.kind != "request":
        return subject
    target = request_target(state, subject)
    if target is not None:
        for kind, table in (("entity", state.get("entities", {})), ("room", state.get("rooms", {})),
                            ("person", _people(state)), ("pet", state.get("pets") or {})):
            if target in table:
                return Subject(kind, target, ("request",))
    return subject


def _unique_named(table, words_wanted, keys):
    """The one id of table whose label (or another listed field) normalizes to the wanted phrase, else None."""
    hits = []
    for rid, record in table.items():
        for key in keys:
            value = (record or {}).get(key)
            if isinstance(value, str) and phrase(value) == words_wanted:
                hits.append(rid)
                break
    return hits[0] if len(hits) == 1 else None


def resolve_subject(env, pid, about):
    """The Subject of `about` as person `pid` understands it: an entity id (realistic perception already turned the
    agent's handle into it), a room / person / pet / calendar-event id, "it" / "that" (the last subject discussed with
    this person: refused "Ask about something first" if none), "request" (the current request), "house",
    or words (a word phrase naming exactly one room by label or type, one person or one pet by label, is that room,
    person or pet; any other words stay a word subject). Raises ActionError (argument) for an empty subject."""
    state = env.world.s
    if isinstance(about, (list, tuple)):
        about = " ".join(str(a) for a in about)
    if about is None or not str(about).strip():
        raise ActionError("Say what you are asking about", "argument")
    text = str(about).strip()
    low = text.lower()
    if low in IT_WORDS:
        last = ((state.get("dialogue") or {}).get(pid) or {}).get("last") or {}
        subject = _subject_from(last.get("subject"))
        require(subject is not None, "Ask about something first", "argument")
        return subject
    if low == "request":
        subject = _request_subject(state)
        require(subject is not None, "There is no request to ask about", "argument")
        return subject
    if low in HOUSE_WORDS:
        return Subject("house")
    for kind, table in (("entity", state.get("entities", {})), ("room", state.get("rooms", {})),
                        ("person", _people(state)), ("pet", state.get("pets") or {})):
        if text in table:
            return Subject(kind, text)
    for event in _calendar_events(state):
        if event["id"] == text:
            return Subject("event", text)
    words = tuple(words_of(text))
    require(bool(words), "Say what you are asking about", "argument")
    wanted = "_".join(words)
    room = _unique_named(state.get("rooms", {}), wanted, ("label", "type"))
    if room is not None:
        return Subject("room", room, words)
    person = _unique_named(_people(state), wanted, ("label",))
    if person is not None:
        return Subject("person", person, words)
    pet = _unique_named(state.get("pets") or {}, wanted, ("label",))
    if pet is not None:
        return Subject("pet", pet, words)
    return Subject("word", None, words)


# ----------------------------------------------------------------------------------------------------------------
# facts (sweep 4.2) and matching (sweep 4.4)

def _fact_about(fact):
    """(kind, value) of a fact's "about": ("id", eid) | ("type", T) | ("kind", W) | ("topic", W) | ("room", R) |
    ("person", P) | ("pet", P) | ("event", E) | ("request", None) | ("house", None)."""
    about = fact.get("about")
    if not isinstance(about, str) or not about:
        return None, None
    if about in ("request", "house"):
        return about, None
    head, sep, rest = about.partition(":")
    if sep and head in ("type", "kind", "topic", "room", "person", "pet", "event"):
        return head, rest
    return "id", about


def calendar_facts(state):
    """The facts household.calendar implies (sweep 11.2: asking `when` is answered from the events, their `hidden`
    keys from the event's hidden values): one "when" fact per event (about event:<id>; aliases the title and kind
    words; known by the people the event's known_by names, default the adults and the people it is for) and one
    "status" fact per hidden key (detail = the key)."""
    out = []
    for event in _calendar_events(state):
        eid = event["id"]
        known_by = event.get("known_by")
        if known_by is None:
            who = [w for w in event.get("who") or [] if isinstance(w, str)]
            known_by = sorted(set(who) | {p for p, rec in _people(state).items() if is_adult(rec)}) if who else "adults"
        title = str(event.get("title") or event.get("kind") or eid)
        value = {k: copy.deepcopy(event[k]) for k in ("title", "kind", "start", "end", "status", "location",
                                                      "recurrence", "who") if k in event}
        when = str(event.get("start") or "").replace("T", " ")
        said = "{} is {}{}.".format(title, "on " + when if when else "planned",
                                    " (not confirmed yet)" if event.get("status") == "tentative" else
                                    " (cancelled)" if event.get("status") == "cancelled" else "")
        aliases = [title] + ([event["kind"]] if isinstance(event.get("kind"), str) else [])
        out.append({"id": "calendar:" + eid, "about": "event:" + eid, "aspect": "when", "value": value, "said": said,
                    "known_by": known_by, "aliases": aliases, "necessary": "not_known", "_calendar": True})
        for key, hidden in sorted((event.get("hidden") or {}).items()):
            out.append({"id": "calendar:{}:{}".format(eid, key), "about": "event:" + eid, "aspect": "status",
                        "detail": key, "value": copy.deepcopy(hidden), "known_by": known_by, "aliases": aliases,
                        "necessary": "not_known", "_calendar": True})
    return out


def facts_of(state):
    """The fact list a question is matched against: user_simulator.facts, then the calendar's implied facts."""
    facts = [f for f in ((state.get("user_simulator") or {}).get("facts") or []) if isinstance(f, dict)]
    return facts + calendar_facts(state)


def _type_names(env, type_name):
    """Phrases a "type:T" fact answers to: T, its label and its non-generic is_a names."""
    out = {phrase(type_name)}
    reg = _registry(env)
    if reg is not None and hasattr(reg, "known") and reg.known(type_name):
        label = (reg.resolve(type_name) or {}).get("label")
        if isinstance(label, str):
            out.add(phrase(label))
        for parent in reg.ancestors(type_name):
            if parent not in GENERIC_NAMES:
                out.add(phrase(parent))
    out.discard("")
    return out - GENERIC_NAMES


def _matches(env, state, fact, subject):
    """Whether a fact is about `subject` (sweep 4.4 matches()); returns the specificity rank, or 0."""
    kind, value = _fact_about(fact)
    if kind is None:
        return 0
    if kind == "request":
        return SPECIFICITY["request"] if (subject.kind == "request" or "request" in subject.words) else 0
    if kind == "house":
        return SPECIFICITY["house"] if subject.kind == "house" else 0
    entities = state.get("entities", {})
    if kind == "id":
        if subject.id is not None and value == subject.id:
            return SPECIFICITY["id"]
        return 0
    if kind in ("room", "person", "pet", "event"):
        return SPECIFICITY[kind] if subject.kind == kind and subject.id == value else 0
    if subject.kind == "entity":
        if kind == "type":
            record = entities.get(subject.id) or {}
            return SPECIFICITY["type"] if value == record.get("type") or value in (record.get("is_a") or ()) else 0
        return 0
    if subject.kind not in ("word", "request"):
        return 0
    grams = ngrams(list(subject.words))
    if kind == "type" and subject.words:
        names = _type_names(env, value)
        # the subject's head noun is the type's ("comfort blanket" asks about blankets; "dryer sheets" is not a
        # question about the clothes dryer)
        if "_".join(subject.words) in names or _head_names(names, subject.words[-1]):
            return SPECIFICITY["type"]
    if kind in ("kind", "topic") and phrase(value) in grams:
        return SPECIFICITY[kind]
    return 0


def _alias_match(fact, subject):
    if subject.kind != "word":
        return False
    grams = ngrams(list(subject.words))
    return any(isinstance(a, str) and phrase(a) in grams for a in fact.get("aliases") or ())


def _detail_rank(fact, detail):
    fdetail = canonical_detail(fact.get("detail")) if fact.get("detail") is not None else None
    if fdetail is None:
        return 1
    if detail is None:
        return 0
    return 2 if fdetail == detail else -1


def _rank_facts(env, state, facts, subject, aspect, detail):
    ranked = []
    for order, fact in enumerate(facts):
        if canonical_aspect(fact.get("aspect")) != aspect:
            continue
        drank = _detail_rank(fact, detail)
        if drank < 0:
            continue
        spec = _matches(env, state, fact, subject)
        if not spec and _alias_match(fact, subject):
            spec = SPECIFICITY["alias"]
        if spec:
            ranked.append((-spec, -drank, order, fact))
    ranked.sort(key=lambda t: t[:3])
    return [t[3] for t in ranked]


def match_fact(state, subject, aspect, detail, env=None):
    """The most specific fact answering (subject, aspect, detail), or None (sweep 4.3 step 4): entity id > type >
    room / person / pet / event > kind / topic / alias, then a fact whose detail matches, then list order. A fact
    with a detail answers only that detail (a question without a detail may get it); a fact without one answers any
    detail of its aspect. `env` gives registry names for type facts (labels, is_a)."""
    aspect = canonical_aspect(aspect) or aspect
    detail = canonical_detail(detail)
    ranked = _rank_facts(env, state, facts_of(state), subject, aspect, detail)
    return ranked[0] if ranked else None


# ----------------------------------------------------------------------------------------------------------------
# necessity (sweep 4.5)

def canonical_key(aspect, subject, detail):
    """The fact key "q:<aspect>:<subject>[:<detail>]" of a question (sweep 4.3 step 8)."""
    key = "q:{}:{}".format(aspect, subject_text(subject) if isinstance(subject, Subject) else subject)
    return key + (":" + str(detail) if detail else "")


def _asked_before(state, key, fact_id):
    """The robot already has this answer: a person answered the same canonical question (its value is in facts) or
    the same fact (dialogue_log). Asking someone who did not know ("otherwise") does not count."""
    if key in (state.get("facts") or {}):
        return True
    if fact_id is None:
        return False
    return any(isinstance(r, dict) and r.get("matched") == fact_id and r.get("answered") and not r.get("otherwise")
               for r in state.get("dialogue_log") or [])


def _segments(text):
    return ":" + str(text).replace(".", ":") + ":"


def _in_memory(state, key, fact_id):
    """A memory key names the fact id or the canonical key as whole ':' / '.'-separated segments ("pref:tea" names the
    fact "tea"; "steak_timer" does not)."""
    wanted = [_segments(key)] + ([_segments(fact_id)] if fact_id else [])
    for mkey in (state.get("memory") or {}):
        padded = _segments(mkey)
        if any(w in padded for w in wanted):
            return True
    return False


def history_consistent(state, eid, person=None):
    """Three or more consistent placements of eid in memory (history:<id> / seen:<id> entries, 60 % to one place;
    the people domain's rule)."""
    memory = state.get("memory") or {}
    entries = []
    for key in ("history:" + str(eid), "seen:" + str(eid)):
        value = (memory.get(key) or {}).get("value") if isinstance(memory.get(key), dict) else None
        entries.extend(e for e in value or [] if isinstance(e, dict))
    if person:
        entries = [e for e in entries if e.get("by") in (None, person)]
    if len(entries) < 3:
        return False
    dests = [str(e.get("to")) for e in entries]
    top = max(set(dests), key=lambda d: (dests.count(d), d))
    return dests.count(top) >= 3 and dests.count(top) / float(len(dests)) >= 0.6


def _mind(env):
    return getattr(env, "mind", None)


def perceived_ever(env, eid):
    """Whether the robot has perceived eid this episode (or knew it from prior knowledge): the Mind's beliefs, else
    (before the engine keeps a Mind) a realistic handle issued or the thing visible now."""
    mind = _mind(env)
    if mind is not None and eid in getattr(mind, "seen", {}):
        return True
    realism = getattr(env, "realism", None)
    if realism is not None:
        return eid in realism.handles.by_id
    state = env.world.s
    if eid in ((state.get("facts") or {}).get("inspected") or {}):
        return True
    try:
        return bool(env.perception.visible(state, eid))
    except Exception:  # noqa: BLE001
        return False


def evidence_known(env, item):
    """Whether one observable_via item ({"entity", "field", "tier"}) is known to the robot: realistic perception
    compares realism knowledge with the tier (glance: perceived once); oracle: perceived once and, for the
    inspect / read / measure tiers, facts.inspected / facts.read holds the entity."""
    state = env.world.s
    eid = item.get("entity")
    record = state.get("entities", {}).get(eid)
    if record is None:
        return False
    tier = item.get("tier", "glance")
    if not perceived_ever(env, eid):
        return False
    if tier == "glance":
        return True
    realism = getattr(env, "realism", None)
    if realism is not None:
        from .realism import TIER_NEED
        field = item.get("field")
        if field:
            return realism.knowledge.level(eid, record, field) >= TIER_NEED.get(tier, 1)
        return realism.knowledge.entity_level(eid, record) >= TIER_NEED.get(tier, 1)
    facts = state.get("facts") or {}
    if tier == "read":
        return eid in (facts.get("read") or {})
    return eid in (facts.get("inspected") or {})


def _candidates_seen(env, subject):
    """The things matching the subject's type the robot perceives now or has seen this episode."""
    state = env.world.s
    entities = state.get("entities", {})
    if subject.kind == "entity":
        wanted = (entities.get(subject.id) or {}).get("type")
        pool = [e for e, r in entities.items() if r.get("type") == wanted]
    elif subject.kind == "word":
        pool = named_entities(env, state, list(subject.words))
    else:
        return []
    return [e for e in pool if perceived_ever(env, e)]


def necessary(env, pid, fact, subject):
    """Whether asking `pid` this was necessary (sweep 4.5). Asking twice (same canonical key or fact id, any person)
    is never necessary, nor is asking someone outside the fact's known_by. Modes (fact["necessary"]): not_known
    (default: not in memory, no observable_via evidence known), always, never, ambiguous (two or more candidates
    perceived now or seen this episode, or two or more of the fact's "candidates" still present), no_history (fewer
    than three consistent placements of the subject in memory), {"pred": P} (P holds), {"unless": [P, ...]}
    (not_known and none of the predicates hold)."""
    state = env.world.s
    if not knows(state, pid, fact.get("known_by")):
        return False
    aspect = canonical_aspect(fact.get("aspect")) or fact.get("aspect")
    key = canonical_key(aspect, subject, canonical_detail(fact.get("detail")))
    fact_id = fact.get("id")
    mode = fact.get("necessary", "not_known")
    if mode == "never":
        return False
    if _asked_before(state, key, fact_id):
        return False
    if mode == "always":
        return True
    if isinstance(mode, dict) and "pred" in mode:
        return bool(check(state, mode["pred"]))
    if mode == "ambiguous":
        listed = [c for c in fact.get("candidates") or [] if c in state.get("entities", {})]
        return len(listed) >= 2 or len(_candidates_seen(env, subject)) >= 2
    if mode == "no_history":
        target = subject.id if subject.id is not None else None
        return target is None or not history_consistent(state, target, pid)
    # not_known (and the base of "unless")
    if _in_memory(state, key, fact_id):
        return False
    if any(evidence_known(env, item) for item in fact.get("observable_via") or [] if isinstance(item, dict)):
        return False
    if isinstance(mode, dict) and "unless" in mode:
        return not any(check(state, p.get("pred", p)) for p in mode["unless"] if isinstance(p, dict))
    return True


# ----------------------------------------------------------------------------------------------------------------
# routes (sweep 4.9)

ROUTES = []          # [(name, aspects frozenset or None, fn)] in registration order
_ROUTE_NAMES = {}


class WeakTopic(str):
    """A route's topic that answers only when no other route does (weak(topic)): a catch-all's answer."""
    __slots__ = ()


def weak(topic):
    """Mark a route's topic as a fallback: find_route keeps trying the later routes and uses it only when none of
    them answers. For catch-all routes (any preference detail, any status of a person, any entity's place) so that a
    domain route which knows the specific answer -- registered later, since the kernel's people routes register
    first -- is not shadowed (adoption-round requests climate 1, food 2, pets 6, safety 2, mechanisms 7,
    textiles_laundry 6). A domain's own catch-all can return weak(topic) too."""
    return WeakTopic(topic) if topic else topic


def route(name, aspects, fn):
    """Register a route from canonical questions to a legacy answerer topic: fn(state, pid, subject, aspect, detail,
    act) -> topic or None, offered only for `aspects` (None: every aspect). Routes are tried in registration order;
    the first topic whose answerer exists (or a permission:) wins, except that a weak(topic) is only a fallback: the
    first of those answers when no route gives a plain topic. Registering a name twice with another function is an
    error."""
    if name in _ROUTE_NAMES:
        if _ROUTE_NAMES[name] is not fn:
            raise ValueError("dialogue route {!r} registered twice".format(name))
        return fn
    _ROUTE_NAMES[name] = fn
    ROUTES.append((name, frozenset(aspects) if aspects is not None else None, fn))
    return fn


def answerer_for(topic):
    """The legacy answerer prefix that would answer `topic` (longest prefix first), or None."""
    for prefix in sorted(ANSWERERS, key=len, reverse=True):
        if topic.startswith(prefix):
            return prefix
    return None


def _subject_words(state, subject):
    """The words a subject goes by for the routes (its own words, or an entity's type and label words)."""
    if subject.kind == "word":
        return list(subject.words)
    if subject.kind == "entity":
        record = state.get("entities", {}).get(subject.id) or {}
        return words_of(record.get("type")) + words_of(record.get("label"))
    return list(subject.words)


def _route_help(state, pid, subject, aspect, detail, act):
    if act not in (None, "step_aside", "move", "step_away", "make_room"):
        return None
    if subject is None or subject.kind in ("house", "request") or subject.id == pid:
        return "step_aside"
    if subject.kind == "entity":
        return "step_aside:" + subject.id
    if subject.kind == "word" and CELL_ARG_RE.match(",".join(subject.words)):
        return "step_aside:" + ",".join(subject.words)
    return "step_aside"


def _route_offer(state, pid, subject, aspect, detail, act):
    if act in ("take", "carry", "hold", "hang_up", "take_coat") and subject is not None and subject.kind == "entity" \
            and (state.get("entities", {}).get(subject.id) or {}).get("location") == pid:
        return "offer:" + subject.id
    return None


def _legacy_key(keys, detail):
    """The legacy key among `keys` a canonical detail names (the same normalized phrase), else the detail itself."""
    for key in sorted(k for k in keys if isinstance(k, str)):
        if phrase(key, stop=False) == phrase(detail, stop=False) or canonical_detail(key) == detail:
            return key
    return detail


def _route_confirm(state, pid, subject, aspect, detail, act):
    if not detail:
        return None
    claims = (state.get("user_simulator") or {}).get("relayed_claims") or {}
    return "confirm:" + _legacy_key(claims, detail)


def _route_wellbeing(state, pid, subject, aspect, detail, act):
    # a fallback: a domain that knows the person's state (safety.condition for a fallen or hurt person) answers first
    if subject is None or (subject.kind == "person" and subject.id == pid):
        return weak("wellbeing")
    return None


def _route_visitor(state, pid, subject, aspect, detail, act):
    # find_route lets this route answer only about a visitor the robot has perceived (VETTED_ROUTES)
    people = _people(state)
    if subject is not None and subject.kind == "person" and subject.id in people and subject.id != pid:
        if people[subject.id].get("at_door") or people[subject.id].get("role") in VISITOR_ROLES:
            return "visitor:" + subject.id
    if subject is not None and subject.kind == "word" and set(subject.words) & {"door", "guest", "visitor", "caller"}:
        at_door = sorted(p for p, rec in people.items() if rec.get("at_door"))
        if len(at_door) == 1:
            return "visitor:" + at_door[0]
    return None


def _route_conflict(state, pid, subject, aspect, detail, act):
    conflicts = (state.get("user_simulator") or {}).get("conflicts") or {}
    if not conflicts or subject is None:
        return None
    words = set(_subject_words(state, subject))
    grams = ngrams(_subject_words(state, subject))
    for key in sorted(conflicts):
        if phrase(key) in grams or (detail and phrase(detail) == phrase(key)) or set(words_of(key)) <= words:
            return "conflict:" + key
    return None


def _route_hint(state, pid, subject, aspect, detail, act):
    if subject is None or subject.kind != "entity":
        return None
    hidden = ((state.get("people_log") or {}).get("hidden_items") or {}).get(subject.id)
    if isinstance(hidden, dict) and hidden.get("by") == pid:
        return "hint:" + subject.id
    return None


def _route_entity(prefix):
    def fn(state, pid, subject, aspect, detail, act):
        if subject is not None and subject.kind == "entity":
            # with a detail ("where is the key for it?") a domain route that knows that detail answers first
            return weak(prefix + subject.id) if detail else prefix + subject.id
        return None
    fn.__name__ = "_route_" + prefix.rstrip(":")
    return fn


def _route_which(state, pid, subject, aspect, detail, act):
    if subject is not None and subject.kind in ("word", "request") and subject.words:
        words = [w for w in subject.words if w != "request"] if subject.kind == "request" else list(subject.words)
        if words:
            return weak("which:" + "_".join(words)) if detail else "which:" + "_".join(words)
    if subject is not None and subject.kind == "request":
        intent = (state.get("user_simulator") or {}).get("intent") or {}
        category = intent.get("category")
        if isinstance(category, str) and category:
            return "which:" + category
        target = (state.get("entities") or {}).get(request_target(state, subject) or "")
        if target is not None and isinstance(target.get("type"), str):
            return "which:" + target["type"]       # "which one did you mean?": the requester's own words for it
    return None


def _route_schedule(state, pid, subject, aspect, detail, act):
    if subject is None:
        return None
    # a person's or an event's schedule is the people answerer's; when about a pet, a room or words, a domain route
    # that knows (pets' walks and meals) answers first
    if subject.kind in ("person", "event") and subject.id:
        return "schedule:" + subject.id
    if subject.kind in ("pet", "room") and subject.id:
        return weak("schedule:" + subject.id)
    if subject.kind == "word" and subject.words:
        return weak("schedule:" + "_".join(subject.words))
    return None


def _route_rules(state, pid, subject, aspect, detail, act):
    words = set(subject.words) if subject is not None else set()
    if "rule" in words or "rules" in words or detail in ("rules", "rule") or (subject is not None and subject.kind == "house"
                                                                              and detail is None):
        if detail and detail not in ("rules", "rule"):
            rules = ((state.get("household") or {}).get("rules") or [])
            keys = [r.get(k) for r in rules if isinstance(r, dict) for k in ("id", "kind")]
            return "rule:" + _legacy_key(keys, detail)
        return "rules"
    return None


def _route_pref(state, pid, subject, aspect, detail, act):
    if not detail:
        return None
    person = _people(state).get(pid) or {}
    hidden = ((state.get("user_simulator") or {}).get("by_person") or {}).get(pid) or {}
    keys = list(person.get("preferences") or {}) + [k[len("pref:"):] for k in hidden if str(k).startswith("pref:")]
    key = _legacy_key(keys, detail)
    # a detail the person has no preference for ("No preference.") is only a fallback: a domain route that knows that
    # detail (climate's fan speed, food's diet from the profile) answers first
    return "pref:" + key if key in keys else weak("pref:" + key)


def _route_intent(state, pid, subject, aspect, detail, act):
    hidden = (state.get("user_simulator") or {}).get("hidden") or {}
    if not hidden or subject is None:
        return None
    words = set(_subject_words(state, subject))
    for key in sorted(hidden):
        if detail and phrase(detail) == phrase(key):
            return "intent:" + key
    if subject.id is not None and subject.kind != "request":
        for key in sorted(hidden):
            if subject.id in re.split(r"[:,]", key):
                return "intent:" + key
    for key in sorted(hidden):
        key_words = set(words_of(key))
        subject_part = {w for w in words if w != "request"}
        if subject_part and subject_part <= key_words:
            return "intent:" + key
    return None


def _route_permission(state, pid, subject, aspect, detail, act):
    if not act or subject is None or subject.kind == "word":
        return None                     # words: the thing they name (answer() retries with it), else the word scope
    if subject.kind == "request":
        target = request_target(state, subject)
        return "permission:{}:{}".format(act, target) if target else None
    return "permission:{}:{}".format(act, subject_text(subject))


def register_people_routes():
    """The people prefixes of sweep 4.9 (data-only mapping) and the kernel's permission route, in the order they are
    tried. Idempotent."""
    table = (
        ("people.step_aside", ("help",), _route_help),
        ("people.offer", ("ok",), _route_offer),
        ("people.confirm", ("said",), _route_confirm),
        ("people.wellbeing", ("status",), _route_wellbeing),
        ("people.visitor", ("who",), _route_visitor),
        ("people.conflict", ("which", "ok", "preference", "when", "amount"), _route_conflict),
        ("people.hint", ("where",), _route_hint),
        ("people.where", ("where",), _route_entity("where:")),
        ("people.owner", ("owner",), _route_entity("owner:")),
        ("people.which", ("which",), _route_which),
        ("people.schedule", ("when",), _route_schedule),
        ("people.rules", ("how",), _route_rules),
        ("people.pref", ("preference",), _route_pref),
        ("people.intent", ("keep", "intended", "amount", "when", "which", "how", "why", "status", "preference",
                           "where", "ok"), _route_intent),
        ("kernel.permission", ("ok",), _route_permission),
    )
    for name, aspects, fn in table:
        route(name, aspects, fn)


register_people_routes()


def _perceived_visitor(env, topic):
    """people.visitor answers only about a visitor the robot has perceived (perceived_ever: the Mind's beliefs) --
    "who is at the door?" before anyone looked would hand over the visitor's hidden identity."""
    return env is None or perceived_ever(env, topic.split(":", 1)[1])


VETTED_ROUTES = {"people.visitor": _perceived_visitor}     # route name -> fn(env, topic) -> may it answer


def find_route(state, pid, subject, aspect, detail, act, env=None):
    """(route name, legacy topic) of the first route that answers, or (None, None). A route's topic counts only if
    an answerer exists for it (or it is a permission: topic, which core answers itself) and, with env, its vetting
    rule allows it (VETTED_ROUTES). A weak topic (weak()) counts only when no route gives a plain one."""
    fallback = None
    for name, aspects, fn in ROUTES:
        if aspects is not None and aspect not in aspects:
            continue
        topic = fn(state, pid, subject, aspect, detail, act)
        if not topic:
            continue
        vet = VETTED_ROUTES.get(name)
        if env is not None and vet is not None and not vet(env, topic):
            continue
        if topic.startswith("permission:") or answerer_for(topic) is not None:
            if isinstance(topic, WeakTopic):
                if fallback is None:
                    fallback = (name, str(topic))   # a catch-all: only if no later route answers
                continue
            return name, str(topic)
    return fallback if fallback is not None else (None, None)


# ----------------------------------------------------------------------------------------------------------------
# the answer pipeline (sweep 4.3)

def _draw(*parts):
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return int(digest[:13], 16) / float(16 ** 13)


def _first(results):
    for value in results:
        if value is not None:
            return value
    return None


def _say(value):
    """What a person says for a structured value without its own phrasing."""
    if value is None:
        return "I don't know."
    if isinstance(value, bool):
        return "Yes." if value else "No."
    if isinstance(value, (int, float)):
        return "{:g}.".format(value)
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if isinstance(value.get("said"), str):
            return value["said"]
        parts = ["{}: {}".format(str(k).replace("_", " "), v if not isinstance(v, (dict, list)) else json.dumps(v, sort_keys=True))
                 for k, v in sorted(value.items())]
        return "; ".join(parts) + "."
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) + "."
    return str(value)


def _not_understood(pid, person):
    vocab = vocabulary()
    langs = (person or {}).get("languages") or ["en"]
    first = str(langs[0]) if langs else "en"
    return vocab["not_understood"].get(first, "(" + first + ") ...")


def _adjacent(state, pid):
    robot = state.get("robot") or {}
    person = _people(state).get(pid) or {}
    a, b = robot.get("pos"), person.get("pos")
    if not (isinstance(a, list) and isinstance(b, list) and len(a) >= 2 and len(b) >= 2):
        return False
    return max(abs(int(a[0]) - int(b[0])), abs(int(a[1]) - int(b[1]))) <= 1


def reach(ctx, pid, channel):
    """Check that `pid` can be asked over `channel` now (sweep 4.3 step 1) and return the channel used (the default:
    voice in the robot's room, else phone when they have one and the robot's network is up). Phone and text reach a
    person wherever they are, at home or away (the robot's network and their phone; a call also needs someone who can
    hear it: a deaf person reads texts). Raises ActionError with the fixed wording: "That person is not available
    here" for anyone not reachable (asleep elsewhere and not home alike), "They are asleep" (social) for a sleeper in
    the robot's room."""
    w = ctx.w
    state = w.s
    people = _people(state)
    require(pid in people, NOT_AVAILABLE)
    person = people[pid]
    robot_room = state["robot"].get("room")
    here = person.get("location") == robot_room
    network = bool(state["robot"].get("network", True))
    phone = bool(person.get("has_phone") or person.get("reachable_by_phone"))
    if channel is None:
        channel = "voice" if here else ("phone" if network and phone else "voice")
    if channel in ("voice", "phone"):
        # the answer is speech addressed to the robot: refused only when the microphones hear nothing
        ctx.require_hearing(addressed_ok=True)
    verdict = _first(run_hooks("dialogue_channel", state, pid, channel, robot_room))
    if verdict is not None:
        ok, reason = (verdict if isinstance(verdict, (tuple, list)) else (bool(verdict), None))[:2]
        require(bool(ok), reason or NOT_AVAILABLE)
    elif channel == "voice":
        require(here, NOT_AVAILABLE)
        hearing = ((person.get("abilities") or {}).get("hearing") or "full")
        require(hearing != "deaf", "They did not hear you")
    elif channel in ("phone", "text"):
        require(network and phone, NOT_AVAILABLE)
        if channel == "phone":
            hearing = ((person.get("abilities") or {}).get("hearing") or "full")
            require(hearing != "deaf", "They cannot hear a call; text them")
    elif channel == "written":
        require(here, NOT_AVAILABLE)
        require(_adjacent(state, pid), "A written question needs them right next to you")
        ctx.require_imaging("Cameras are off; a written question needs the cameras")
        require(ctx.env.perception.lit(state, robot_room), "It is too dark here to read or write")
        require(((person.get("abilities") or {}).get("vision") or "full") != "blind", "They cannot read it")
    if not person.get("awake", True):
        if here and channel in ("voice", "written"):
            raise ActionError(ASLEEP, "social")
        raise ActionError(NOT_AVAILABLE, "precondition")
    return channel


def comprehends(state, pid, language):
    """Whether pid understands `language` (hook dialogue_comprehends, else their languages, default ["en"])."""
    verdict = _first(run_hooks("dialogue_comprehends", state, pid, language))
    if verdict is not None:
        return bool(verdict)
    langs = (_people(state).get(pid) or {}).get("languages") or ["en"]
    return language in [str(lang).lower() for lang in langs]


def _log(w, record):
    log = w.s.get("dialogue_log")
    if isinstance(log, list):
        log = w.sec("dialogue_log")
    else:
        w.set_scalar("dialogue_log", [])
        log = w.s["dialogue_log"]
    log.append(record)
    if len(log) > LOG_CAP:
        del log[:len(log) - LOG_CAP]


def _remember_context(w, pid, subject, aspect, detail):
    """dialogue.<pid>.last: the subject as asked (a request subject stays "the request": "it" later means the request
    again, never the thing it stands for)."""
    if subject is None:
        return
    dialogue = w.sec("dialogue")
    entry = dialogue.setdefault(pid, {"turns": 0})
    entry["last"] = {"subject": _subject_dict(subject), "aspect": aspect, "detail": detail}
    entry["turns"] = int(entry.get("turns", 0)) + 1


def _turns(state, pid):
    return int(((state.get("dialogue") or {}).get(pid) or {}).get("turns", 0))


def _about_shown(subject):
    """The subject as the answer names it, which is the agent's own argument: an id it gave (realism translates it
    back to the agent's handle), "request", "house", or its words -- never the id a request stands for."""
    if subject is None:
        return None
    if subject.kind in ("house", "request"):
        return subject.kind
    if subject.id is not None:
        return subject.id
    return " ".join(subject.words)


def _pick_option(env, state, options, value):
    """Index of the option the fact's value picks (an entity option equal to the value, or a word option naming the
    value's entity), else None."""
    target = value.get("id") if isinstance(value, dict) and isinstance(value.get("id"), str) else value
    target_record = state.get("entities", {}).get(target) if isinstance(target, str) else None
    for i, option in enumerate(options):
        if option.kind == "entity" and option.id == target:
            return i
        if option.kind in ("room", "person", "pet") and option.id == target:
            return i
        if option.kind == "word" and target_record is not None and names_entity(env, target_record, list(option.words)):
            return i
        if option.kind == "word" and isinstance(value, str) and phrase(value) == "_".join(option.words):
            return i
    return None


def answer(ctx, args):
    """Run the answer pipeline of one canonical `ask` and return the answer dict (sweep 4.3); the caller serializes
    it. args: target, about, aspect, detail, act, options, channel, language, question (ctx.args after validation).
    Statistics: a matched fact counts necessary_questions or unnecessary_questions (necessary()); a routed question
    counts whatever its legacy answerer counts; an unmatched one counts unnecessary_questions and
    questions_unmatched; a clarify-back, a misunderstood language and a help request count nothing."""
    env, w = ctx.env, ctx.w
    state = w.s
    pid = args["target"]
    aspect = canonical_aspect(args.get("aspect"))
    require(aspect is not None, "Unknown aspect: use one of {} (or help)".format("/".join(ASPECTS)), "argument")
    act = canonical_act(args.get("act"))
    detail = canonical_detail(args.get("detail"))
    if detail is None and act is not None and aspect == "ok":
        detail = act                    # "may I <act> it?": the act is the detail an ok-fact answers
    language = str(args.get("language") or "en").strip().lower()
    channel = reach(ctx, pid, args.get("channel"))
    person = _people(state)[pid]
    if channel in ("voice", "phone") and not _speaker_works(ctx):
        return _no_answer(ctx, pid, aspect, args, channel)
    record = {"t": state.get("time_s", 0.0), "to": pid, "about": None, "kind": "help" if aspect == "help" else "question",
              "aspect": aspect, "detail": detail, "act": act, "matched": None, "route": None, "counted": None,
              "answered": False, "said": None, "channel": channel, "comprehended": True, "key": None}
    if args.get("question"):
        record["question"] = str(args["question"])[:300]
    if not comprehends(state, pid, language):
        said = _not_understood(pid, person)
        record.update(comprehended=False, said=said)
        _log(w, record)
        result = {"by": pid, "aspect": aspect, "about": args.get("about"), "value": None, "said": said,
                  "known": False, "comprehended": False}
        _finish(w, pid, None, result)
        return result
    about = args.get("about")
    if about is None or (isinstance(about, str) and not about.strip()):
        subject = (Subject("person", pid) if aspect == "status" else
                   None if aspect == "help" else _request_subject(state))
        require(subject is not None or aspect == "help", "Say what you are asking about", "argument")
    else:
        subject = resolve_subject(env, pid, about)
    options = [resolve_subject(env, pid, o) for o in (args.get("options") or [])
               if o is not None and (not isinstance(o, str) or o.strip())]
    record["about"] = _subject_dict(subject) if subject is not None else None
    key = canonical_key(aspect, subject, detail) if subject is not None else "q:help"
    record["key"] = key
    shown_about = _about_shown(subject)
    result = None
    used = subject
    if aspect != "help" and subject is not None and subject.kind == "word" and aspect in ENTITY_ASPECTS:
        used, result = _word_subject(ctx, pid, subject, aspect, detail, options, record, shown_about)
        if used is not subject:
            options = []                # the options picked the thing: the question is about it now
    if result is None and aspect != "help":
        result = _answer_from_facts(ctx, pid, used, aspect, detail, options, record, key, shown_about)
    if result is None:
        result = _answer_by_route(ctx, pid, used, aspect, detail, act, record, key, shown_about)
    if result is None:
        if aspect == "help":
            said = "Sorry, I can't help with that right now."
            result = {"by": pid, "aspect": aspect, "about": shown_about, "value": None, "said": said, "known": False}
            record.update(said=said)
        else:
            w.stat("unnecessary_questions")
            w.stat("questions_unmatched")
            result = {"by": pid, "aspect": aspect, "about": shown_about, "value": None, "said": UNKNOWN,
                      "known": False}
            record.update(counted="unnecessary", said=UNKNOWN)
    if detail is not None and "detail" not in result:
        result["detail"] = detail
    render = _first(run_hooks("dialogue_render", state, pid, result, language))
    if isinstance(render, str):
        result["said"] = render
        record["said"] = render
    _log(w, record)
    _remember_context(w, pid, subject, aspect, detail)
    _finish(w, pid, key, result)
    return result


def _speaker_works(ctx):
    """The robot's question is heard only through a working speaker (voice and phone). A failed speaker the robot did
    not know of is detected now, as speech detects it (no echo heard, with working microphones)."""
    from . import body
    state = ctx.w.s
    if body.can_speak(state):
        return True
    if body.is_latent(state, "speaker_failed") and body.can_hear(state) != "none":
        from . import body_faults
        body_faults.detect_fault(ctx.w, "speaker_failed", "speech")
    return False


def _no_answer(ctx, pid, aspect, args, channel):
    """Nobody heard the question (a failed speaker): no answer, the ask's time spent, nothing counted, nothing
    recorded as asked."""
    record = {"t": ctx.w.s.get("time_s", 0.0), "to": pid, "about": None, "kind": "question", "aspect": aspect,
              "detail": None, "act": None, "matched": None, "route": None, "counted": None, "answered": False,
              "said": None, "channel": channel, "comprehended": True, "key": None, "heard": False}
    _log(ctx.w, record)
    result = {"by": pid, "aspect": aspect, "about": args.get("about"), "value": None, "said": NO_ANSWER,
              "known": False, "answered": False}
    ctx.w.sec("facts")["last_answer"] = copy.deepcopy(result)
    return result


def _word_subject(ctx, pid, subject, aspect, detail, options, record, shown_about):
    """Step 5 for an entity-directed aspect (ENTITY_ASPECTS), decided from the word alone: (subject to use, None) or
    (subject, clarify-back answer). `options` (handles or words) narrow the things the word names: one left, the
    question is about that thing; several, the person asks which of them. A word naming two or more household things
    gets the clarify-back unless a fact about the word itself (type / kind / topic / alias) answers -- whether or not
    one of the things has a fact of its own."""
    env, state = ctx.env, ctx.w.s
    named = named_entities(env, state, list(subject.words))
    if options:
        picked = set()
        for option in options:
            if option.kind == "entity":
                picked.add(option.id)
            elif option.kind == "word":
                picked.update(named_entities(env, state, list(option.words)))
        narrowed = sorted(e for e in named if e in picked) if named else sorted(picked)
        if len(narrowed) == 1:
            return Subject("entity", narrowed[0], subject.words), None
        if len(narrowed) >= 2:
            named = narrowed
    if len(named) < 2:
        return subject, None
    facts = facts_of(state)
    entity_fact = any(_fact_about(f)[0] == "id" for e in named
                      for f in _rank_facts(env, state, facts, Subject("entity", e), aspect, detail))
    if not entity_fact and _rank_facts(env, state, facts, subject, aspect, detail):
        return subject, None            # a fact about the word itself answers ("where do books go?")
    return subject, _clarify(ctx, pid, subject, aspect, named, record, shown_about)


def _finish(w, pid, key, result):
    facts = w.sec("facts")
    if key:
        facts.setdefault("asked", {})[pid + ":" + key] = w.s.get("time_s", 0.0)
    facts["last_answer"] = copy.deepcopy(result)


def _answer_from_facts(ctx, pid, subject, aspect, detail, options, record, key, shown_about):
    """Steps 4-5: a matched fact (or a clarify-back); None when no fact answers."""
    env, w = ctx.env, ctx.w
    state = w.s
    facts = facts_of(state)
    fact = None
    used = bound_subject(state, subject)     # a request subject matches as the thing it stands for (never shown)
    if subject.kind == "word" and any(_fact_about(f)[0] == "id" for f in facts):
        # a word naming exactly one household thing with a fact of its own is about that thing (several things:
        # _word_subject asked back already, or the aspect is not about one thing)
        named = named_entities(env, state, list(subject.words))
        if len(named) == 1:
            ranked = _rank_facts(env, state, facts, Subject("entity", named[0]), aspect, detail)
            entity_level = [f for f in ranked if _fact_about(f)[0] == "id"]
            if entity_level:
                fact, used = entity_level[0], Subject("entity", named[0], subject.words)
    if fact is None:
        ranked = _rank_facts(env, state, facts, used, aspect, detail)
        fact = ranked[0] if ranked else None
    if fact is None:
        return None
    record["matched"] = fact.get("id")
    insider = knows(state, pid, fact.get("known_by"))
    counted = necessary(env, pid, fact, used) if insider else False
    w.stat("necessary_questions" if counted else "unnecessary_questions")
    record["counted"] = "necessary" if counted else "unnecessary"
    if not insider:
        # the same words as a question nothing models, unless the author wrote what this person says
        said = fact.get("otherwise") or UNKNOWN
        record.update(said=said, otherwise=True)
        return {"by": pid, "aspect": aspect, "about": shown_about, "value": None, "said": said, "known": False}
    value = copy.deepcopy(fact.get("value"))
    said = fact.get("said")
    reliability = fact.get("reliability", 1.0)
    hooked = _first(run_hooks("dialogue_reliability", state, pid, fact))
    if isinstance(hooked, (int, float)) and not isinstance(hooked, bool):
        reliability = min(float(reliability), float(hooked)) if isinstance(reliability, (int, float)) else float(hooked)
    if isinstance(reliability, (int, float)) and reliability < 1.0 and \
            _draw(getattr(env, "seed", 0), fact.get("id"), pid, _turns(state, pid)) >= float(reliability):
        value = copy.deepcopy(fact.get("wrong_value"))
        said = None
        record["reliable"] = False
    if said is None:
        said = _say(value)
    record.update(said=said, answered=True)
    if value is not None:
        w.sec("facts")[key] = copy.deepcopy(value)
    approvals = [g for g in fact.get("grants") or [] if isinstance(g, str)]
    if approvals and record.get("reliable", True):
        current = w.sec("approvals")
        for scope in approvals:
            if scope not in current:
                current.append(scope)
    if record.get("reliable", True):
        for path, new in sorted((fact.get("sets") or {}).items()):
            w.set_path(path, copy.deepcopy(new))
    result = {"by": pid, "aspect": aspect, "about": shown_about, "value": value, "said": said, "known": True,
              "key": key}
    refers = [{"desc": r["desc"], "room": r["room"]} for r in _refers_to(state, value)]
    if refers:
        result["refers_to"] = refers
    if options:
        picked = _pick_option(env, state, options, value)
        result["picked"] = picked
        if picked is None:
            result["said"] = "Neither of those. " + said
            record["said"] = result["said"]
    return result


def _clarify(ctx, pid, subject, aspect, named, record, shown_about):
    """A clarify-back (sweep 4.3 step 5): the person cannot tell which of the named things the robot means. It costs
    the ask's time and counts nothing; options are descriptions (never handles), sorted by their text."""
    state = ctx.w.s
    counts = {}
    for desc in appearances(state, named):
        counts[desc] = counts.get(desc, 0) + 1
    descriptions = [d if n == 1 else "{}, {} alike".format(d, n) for d, n in sorted(counts.items())][:6]
    noun = " ".join(subject.words) or "one"
    said = "Which {} do you mean?".format(noun)
    record.update(said=said, clarify=True)
    return {"by": pid, "aspect": aspect, "about": shown_about, "value": None, "said": said, "known": False,
            "clarify": said, "options": descriptions}


def _answer_by_route(ctx, pid, subject, aspect, detail, act, record, key, shown_about):
    """Step 6: run the first matching route's legacy answerer (today's code, which counts its own statistics). A word
    subject no route takes is retried as the thing it names when it names exactly one household thing; when it names
    several and a route would take one of them, the person asks back which one is meant (a clarify-back)."""
    env = ctx.env
    state = ctx.w.s
    name, topic = find_route(state, pid, subject, aspect, detail, act, env=env)
    if topic is None and subject is not None and subject.kind == "word":
        named = named_entities(env, state, list(subject.words))
        if len(named) == 1:
            name, topic = find_route(state, pid, Subject("entity", named[0], subject.words), aspect, detail, act,
                                     env=env)
        elif len(named) >= 2 and any(find_route(state, pid, Subject("entity", e, subject.words), aspect, detail, act,
                                                env=env)[1] for e in named):
            return _clarify(ctx, pid, subject, aspect, named, record, shown_about)
        if topic is None and aspect == "ok" and act:
            name, topic = "kernel.permission", "permission:{}:{}".format(act, subject_text(subject))
    if topic is None:
        return None
    from ..systems.core import legacy_answer        # core owns the legacy answer path (imported lazily: core imports us)
    before = dict(state.get("stats") or {})
    # domains' ask hooks read the legacy topic (e.g. an approval policy judging permission questions): a routed
    # question shows them the topic it was answered as
    ctx.args["topic"] = topic
    legacy = legacy_answer(ctx, pid, topic)
    after = state.get("stats") or {}
    if after.get("necessary_questions", 0) > before.get("necessary_questions", 0):
        record["counted"] = "necessary"
    elif after.get("unnecessary_questions", 0) > before.get("unnecessary_questions", 0):
        record["counted"] = "unnecessary"
    ctx.w.sec("facts").setdefault("asked", {})[pid + ":" + topic] = state.get("time_s", 0.0)
    value = copy.deepcopy((legacy or {}).get("value"))
    if value is None and "granted" in (legacy or {}):
        value = bool(legacy["granted"])
    said = value.get("said") if isinstance(value, dict) and isinstance(value.get("said"), str) else _say(value)
    record.update(route=name, said=said, answered=True, topic=topic)
    # the route and its legacy topic stay in dialogue_log: the topic would name what the robot was not told (a
    # visitor's id, the child who hid a thing, the request's category)
    result = {"by": pid, "aspect": aspect, "about": shown_about, "value": value, "said": said, "known": value is not None}
    for extra in ("granted", "accepted", "moved"):
        if extra in (legacy or {}):
            result[extra] = copy.deepcopy(legacy[extra])
    if value is not None and not topic.startswith(("permission:", "step_aside")):
        ctx.w.sec("facts")[key] = copy.deepcopy(value)
        result["key"] = key
    refers = [{"desc": r["desc"], "room": r["room"]} for r in _refers_to(state, value)]
    if refers:
        result["refers_to"] = refers
    return result


# ----------------------------------------------------------------------------------------------------------------
# fact validation and lints (sweep 4.2, 4.11; spec scoring of questions is evaluation.question_failures)

def validate_fact(fact, where="fact"):
    """Problems of one fact (an empty list when it is well formed)."""
    problems = []
    if not isinstance(fact, dict):
        return ["{} must be an object".format(where)]
    unknown = sorted(set(fact) - FACT_KEYS - {k for k in fact if str(k).startswith("_")})
    if unknown:
        problems.append("{} has unknown keys {}".format(where, ", ".join(unknown)))
    if not isinstance(fact.get("id"), str) or not fact["id"]:
        problems.append("{} needs an id".format(where))
    kind, value = _fact_about(fact)
    if kind is None:
        problems.append("{} needs an about".format(where))
    elif kind not in ("request", "house") and not value:
        problems.append("{} about {!r} names nothing".format(where, fact.get("about")))
    if canonical_aspect(fact.get("aspect")) is None:
        problems.append("{} has unknown aspect {!r}".format(where, fact.get("aspect")))
    known_by = fact.get("known_by", "adults")
    if not (known_by in KNOWN_BY or (isinstance(known_by, list) and all(isinstance(p, str) for p in known_by))):
        problems.append("{} known_by must be one of {} or a list of person ids".format(where, "/".join(KNOWN_BY)))
    mode = fact.get("necessary", "not_known")
    if not (mode in NECESSARY_MODES or (isinstance(mode, dict) and set(mode) in ({"pred"}, {"unless"}))):
        problems.append("{} necessary must be one of {} or {{pred}} / {{unless}}".format(where, "/".join(NECESSARY_MODES)))
    for item in fact.get("observable_via") or []:
        if not isinstance(item, dict) or not item.get("entity") or item.get("tier", "glance") not in TIERS:
            problems.append("{} observable_via items need entity and a tier of {}".format(where, "/".join(TIERS)))
            break
    reliability = fact.get("reliability", 1.0)
    if not isinstance(reliability, (int, float)) or isinstance(reliability, bool) or not 0.0 <= reliability <= 1.0:
        problems.append("{} reliability must be within 0..1".format(where))
    if not isinstance(fact.get("grants", []), list) or not isinstance(fact.get("sets", {}), dict):
        problems.append("{} grants must be a list and sets an object".format(where))
    if not isinstance(fact.get("aliases", []), list):
        problems.append("{} aliases must be a list".format(where))
    return problems


def lint_facts(state, env=None, reference_env=None):
    """Sweep 4.11 rule 1: every fact is reachable: an entity-keyed fact's entity was perceived in the reference run
    (reference_env: its Mind or realistic handles) or the fact lists aliases; a word-keyed fact (type / kind / topic)
    normalizes to a non-empty phrase. Returns problem strings."""
    problems = []
    seen_ids = set()
    for fact in facts_of(state):
        problems.extend(validate_fact(fact, "fact {}".format(fact.get("id"))))
        if fact.get("id") in seen_ids:
            problems.append("fact id {} is defined twice".format(fact.get("id")))
        seen_ids.add(fact.get("id"))
        kind, value = _fact_about(fact)
        if kind == "id" and reference_env is not None and not fact.get("aliases") and \
                value in state.get("entities", {}) and not perceived_ever(reference_env, value):
            problems.append("fact {} is about {} which the reference run never perceived (add aliases)".format(
                fact.get("id"), value))
        if kind in ("type", "kind", "topic") and not phrase(value):
            problems.append("fact {} about {!r} has no matchable words".format(fact.get("id"), fact.get("about")))
    return problems


def lint_reference(task, state):
    """Sweep 4.11 rules 2-4 for a schema-3 spec after its reference run: every questions.required id was asked with a
    canonical ask and matched (dialogue_log); a required or min_necessary spec needs a wrong plan (the caller passes
    the task with its "wrong" list). Returns problem strings."""
    problems = []
    questions = (task or {}).get("questions") or {}
    log = [r for r in state.get("dialogue_log") or [] if isinstance(r, dict)]
    for fact_id in questions.get("required") or []:
        if not any(r.get("matched") == fact_id and r.get("answered") for r in log):
            problems.append("questions.required {} is not asked (and answered) by the reference plan".format(fact_id))
    if (questions.get("required") or questions.get("min_necessary", 0) >= 1) and not (task or {}).get("wrong"):
        problems.append("a spec that requires a question needs a wrong plan that skips it")
    return problems


def lint_plan(plan, where="step "):
    """Sweep 4.11 rule 3: a legacy-topic ask in a schema-3 reference plan is an error, inside foreach / repeat blocks
    too (a do step's topic may also come as `destination`, the legacy alias). Returns problem strings."""
    problems = []
    for i, step in enumerate(plan or []):
        if not isinstance(step, dict):
            continue
        topic = step.get("topic") or (step.get("destination") if step.get("do") == "ask" else None)
        if (step.get("do") == "ask" or step.get("skill") == "ask") and topic:
            problems.append("{}{}: legacy ask topic {!r}; use about/aspect".format(where, i, topic))
        if isinstance(step.get("steps"), list):
            problems.extend(lint_plan(step["steps"], "{}{}.".format(where, i)))
    return problems
