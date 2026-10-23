"""What a robot warning says (independent review H1; VERIFY_HARDENING 13.5, the safety domain's behaviour).

A person is warned of a hazard only by words that warn of it. Before this, any robot utterance heard while any hazard
was active alerted every listener and warned them off every hazard zone ("Hi Ava! Dinner soon!" or "Ava, come and look
at this in the kitchen!" kept a barefoot child away from broken glass). Now an utterance is read clause by clause,
deterministically and lexically (the parser never reads hidden state; it only matches the words against the hazards
that exist, as a listener would against what they know of the house):

* **issue claim**: a hazard word of a class ("broken glass", "fire", "gas", "carbon monoxide"...), not negated
  ("there is no fire" claims nothing). With a place named in the same clause it is about that place only.
* **avoid directive**: "stay out", "keep away", "don't go/come in", "careful", "watch out", "danger", "not safe",
  "shoes on"..., about the places it names (a room by its label or type, "here" / "this room" for the robot's room),
  else about the hazards it names; "get out (of the house)", "evacuate", "everyone out", "go outside" said as an
  imperative are about the whole house. An avoid directive with no subject ("Careful!") warns of nothing.
* **approach directive**: an imperative "come", "come here/in/look/see", "over here", "look at this", "go into"...
  (at the start of a clause or after a comma, past fillers such as "please", "hey" or a name) toward a place or
  hazard is an invitation (negated, "don't come in", or "come out of", it is an avoid directive). A zone the utterance
  invites into is never warned of by it. "I'll come to the kitchen" or "I'll get out the extinguisher" are not
  imperatives and direct nobody.

warning_content(...) -> Content(kinds, zones, invited): the active hazard kinds the listener is alerted to, the hazard
zones (safety.zones) they now keep out of, and the zones they were invited into. people_care applies it to speech,
the alert verb and reports to a person. The kernel's claim model (VERIFY_HARDENING 13.1, kernel/speech.py) will
supersede this parser; the rules above are the ones 13.5 states for it.
"""
import re

from ...kernel.dialogue import singular
from . import common as C
from . import hazards as H

NEGATORS = frozenset(("not", "no", "never", "none", "nothing", "nobody", "without", "cannot", "neither", "nor"))
NEGATION_SPAN = 4           # a negator this many words before the head word negates it (VERIFY_HARDENING 13.1)
CLAUSE_RE = re.compile(r"[.;!?\n]+|\b(?:but|however|although|though)\b")
SEGMENT_RE = re.compile(r"[,:\u2013\u2014]|\s-\s|\band\b|\bthen\b")      # where an imperative may start in a clause
TOKEN_RE = re.compile(r"[a-z0-9]+")
# words that may precede an imperative ("Ava, please come here", "everyone get out now", "we need to evacuate")
FILLERS = frozenset(("please", "hey", "hi", "hello", "ok", "okay", "now", "quick", "quickly", "everyone", "everybody",
                     "all", "you", "guy", "folk", "kid", "child", "we", "need", "to", "must", "have", "should", "let",
                     "lets", "u", "and", "then", "so", "right", "immediately", "hurry", "listen", "attention", "oh",
                     "both", "of", "your", "yourself", "just", "go", "do", "dear", "honey", "sweetie", "buddy",
                     "mom", "dad", "mum", "grandma", "grandpa", "sir", "madam", "ma", "am"))

EVACUATE = ("get out", "everyone out", "everybody out", "all out", "evacuate", "leave the house", "leave the home",
            "leave the building", "leave the apartment", "leave now", "go outside", "get outside", "head outside",
            "go out the front", "go out the back", "exit the house", "meet outside", "out of the house",
            "out of the building", "out of the apartment", "stay outside", "wait outside", "stay out of the house")
AVOID = ("stay out", "keep out", "stay away", "keep away", "stay back", "keep back", "stand back", "stay clear",
         "keep clear", "stay off", "keep off", "avoid", "careful", "watch out", "watch your step", "look out",
         "stay where you are", "stay there", "stay put", "do not go", "do not come", "do not enter", "do not walk",
         "do not step", "do not touch", "do not move", "do not run", "wear shoe", "put on shoe", "put shoe on",
         "shoe on", "danger", "dangerous", "unsafe", "not safe", "hazard", "off limit", "out of bound", "no entry")
APPROACH = ("come", "over here", "look at this", "look at that", "check this out", "check it out", "go to", "go into",
            "go in", "walk to", "walk into", "walk in", "head to", "head into", "join me", "in here", "run to",
            "run into")
HERE = ("here", "this room", "in here")
HOUSE = ("house", "home", "building", "apartment", "flat", "inside")

# words of a hazard class (hazards.active_hazards kinds); the kinds a hazard marker or zone names are split into
# words too (class_words), with the vocabulary's synonyms (data/vocab/safety.json issues)
CLASS_WORDS = {
    "fire": ("fire", "flame", "burning", "blaze", "on fire", "smoke"),
    "smoke": ("smoke", "smoky", "fire", "burning"),
    "co": ("carbon monoxide", "monoxide", "co", "co alarm", "fume"),
    "gas": ("gas", "gas leak", "propane", "smell of gas"),
    "flood": ("flood", "flooding", "flooded", "water", "leak", "leaking"),
    "chemical": ("chemical", "fume", "vapour", "vapor", "toxic", "poison", "bleach", "ammonia"),
    "electrical": ("electrical", "electric", "spark", "sparking", "shock", "live wire", "wire", "outlet"),
    "structural": ("structure", "structural", "collapse", "collapsing", "rotten", "crack", "cracked", "giving way"),
    "broken_glass": ("glass", "shard", "sliver", "sharp"),           # not "broken": "the dishwasher is broken"
    "medical": ("hurt", "injured", "fallen", "fell", "fall", "ambulance", "emergency", "unconscious", "not breathing",
                "bleeding", "collapsed"),
    "intruder": ("intruder", "stranger", "burglar", "break in", "prowler", "police", "someone outside"),
    "severe_weather": ("storm", "tornado", "hurricane", "thunderstorm", "weather", "flash flood", "shelter"),
    # the hidden hazard markers safety's helpers set (anomaly markers name their own kind)
    "water_on_floor": ("water", "wet", "slippery", "puddle", "spill", "spilled", "leak", "leaking"),
    "fallen_furniture": ("fallen", "tipped", "toppled", "fell", "furniture", "bookcase", "dresser", "shelf"),
    "overheating": ("overheating", "overheated", "hot", "burning", "smell of burning"),
    "rodent_droppings": ("dropping", "mouse", "mice", "rodent", "rat"),
    "submerged_electrical": ("electrical", "electric", "electricity", "shock", "live", "flood", "water"),
}
ZONE_CLASS = {"sharp": "broken_glass"}       # a hazard zone's kind -> its hazard class (hazards.active_hazards)
GENERIC = frozenset(("the", "of", "a", "an", "and", "in", "on", "risk", "hazard", "issue", "problem", "is", "floor",
                     "room", "thing", "item", "general"))


class Content(object):
    """What an utterance warned of: kinds (active hazard kinds, sorted), zones (hazard zone names it warns off,
    sorted), invited (zone names it invites the listener into, sorted)."""
    __slots__ = ("kinds", "zones", "invited")

    def __init__(self, kinds=(), zones=(), invited=()):
        self.kinds, self.zones, self.invited = sorted(kinds), sorted(zones), sorted(invited)

    def __bool__(self):
        return bool(self.kinds or self.zones)

    __nonzero__ = __bool__

    def alert_kind(self):
        """The kind an alerted person remembers: the first one that is not smoke (a fire's smoke is the fire)."""
        kinds = [k for k in self.kinds if k != "smoke"] or list(self.kinds)
        return kinds[0] if kinds else None


def tokens(text):
    """Lower-case singular words: "n't" becomes " not" first ("don't" -> do not, "can't" -> can not)."""
    raw = str(text or "").lower().replace("\u2019", "'").replace("can't", "can not").replace("won't", "will not")
    raw = raw.replace("n't", " not").replace("'", "").replace("_", " ").replace("-", " ")
    return [singular(t) for t in TOKEN_RE.findall(raw)]


def clauses(text):
    return [part for part in CLAUSE_RE.split(str(text or "").lower()) if part and part.strip()]


def _phrase(words):
    return tuple(tokens(words))


def _find(toks, phrase):
    """Start indexes of the phrase (a tuple of words) in toks."""
    n = len(phrase)
    return [i for i in range(len(toks) - n + 1) if tuple(toks[i:i + n]) == phrase] if n else []


def _negated(toks, i):
    return any(t in NEGATORS for t in toks[max(0, i - NEGATION_SPAN):i])


_EVACUATE = tuple(_phrase(p) for p in EVACUATE)
_AVOID = tuple(_phrase(p) for p in AVOID)
_APPROACH = tuple(_phrase(p) for p in APPROACH)
_HERE = tuple(_phrase(p) for p in HERE)
_HOUSE = tuple(_phrase(p) for p in HOUSE)


_CLASS_WORDS = {}


def class_words(kind):
    """The words that name a hazard class: CLASS_WORDS, else the kind's own words (an "electrical_burning" marker:
    electrical, burning), plus its vocabulary synonyms (data/vocab issues). Cached per kind (static data)."""
    if kind in _CLASS_WORDS:
        return _CLASS_WORDS[kind]
    out = [_phrase(w) for w in CLASS_WORDS.get(kind, ())]
    if kind not in CLASS_WORDS:
        out.extend((w,) for w in tokens(kind) if w not in GENERIC)
    try:
        from ...kernel import dialogue
        entry = (dialogue.vocabulary().get("issues") or {}).get(kind) or {}
        out.extend(_phrase(s) for s in entry.get("synonyms") or ())
    except (ImportError, AttributeError, KeyError, TypeError, ValueError):     # no vocabulary: the words above stay
        pass
    _CLASS_WORDS[kind] = tuple(p for p in out if p)
    return _CLASS_WORDS[kind]


def room_phrases(state, rid):
    room = state["rooms"].get(rid) or {}
    out = set()
    for name in (room.get("label"), room.get("type"), rid):
        if name:
            words = tuple(t for t in tokens(name) if not t.isdigit())
            if words and words != ("room",):
                out.add(words)
    return out


def _places(state, toks, speaker_room):
    """Rooms the clause names, and whether it names the whole house."""
    rooms = set()
    for rid in sorted(state.get("rooms") or {}):
        if any(_find(toks, p) for p in room_phrases(state, rid)):
            rooms.add(rid)
    if speaker_room in (state.get("rooms") or {}) and any(_find(toks, p) for p in _HERE):
        rooms.add(speaker_room)
    house = any(_find(toks, p) for p in _HOUSE)
    return rooms, house


def names_of(state):
    """Words naming the people and pets of the house (they may open an imperative: "Ava, come here")."""
    out = set()
    for group in ("people", "pets"):
        for aid, rec in sorted((state.get(group) or {}).items()):
            for name in (rec.get("label"), rec.get("name"), aid):
                out.update(t for t in tokens(name) if not t.isdigit())
    return out


def _imperative(seg, i, names):
    """The phrase at index i of a segment is said as an imperative: only fillers, names and negators precede it."""
    return all(t in FILLERS or t in names or t in NEGATORS for t in seg[:i])


def _directive(clause, names):
    """(directive, target words) for one clause: directive "evacuate" | "avoid" | "approach" | None (an un-negated
    invitation wins over a warning in the same clause: "careful, come and look!" sends the listener in); target words:
    for an invitation, the clause's words from its first approach phrase on (the place or thing it points to)."""
    approach = negated_approach = evacuate = avoid = False
    target = []
    parts = [tokens(part) for part in SEGMENT_RE.split(clause)]
    for k, seg in enumerate(parts):
        for phrase in _APPROACH:
            for i in _find(seg, phrase):
                if not _imperative(seg, i, names):
                    continue
                nxt = seg[i + len(phrase):i + len(phrase) + 1]
                if _negated(seg, i) or nxt == ["out"]:
                    negated_approach = True         # "don't come in", "come out of the kitchen"
                elif not approach:
                    approach = True
                    target = seg[i:] + [t for later in parts[k + 1:] for t in later]
        for phrase in _EVACUATE:
            for i in _find(seg, phrase):
                if not _negated(seg, i) and (_imperative(seg, i, names) or any(_find(seg, h) for h in _HOUSE)):
                    evacuate = True
        if any(_find(seg, phrase) for phrase in _AVOID):
            avoid = True
    if approach:
        return "approach", target
    if evacuate:
        return "evacuate", []
    if negated_approach or avoid:
        return "avoid", []
    return None, []


def _claims(clause, kinds):
    """The hazard classes a clause claims: a class word not negated within its own segment (a negator before a comma
    does not reach past it: "No entry, there's broken glass" claims glass)."""
    out = set()
    segments = [tokens(part) for part in SEGMENT_RE.split(clause)]
    for kind in kinds:
        for phrase in class_words(kind):
            if any(not _negated(seg, i) for seg in segments for i in _find(seg, phrase)):
                out.add(kind)
                break
    return out


def zone_class(zone):
    kind = str((zone or {}).get("kind") or "")
    return ZONE_CLASS.get(kind, kind)


def warning_content(state, text, speaker_room=None, extra_kinds=(), extra_rooms=()):
    """Content of one robot utterance (see the module docstring). extra_kinds / extra_rooms: hazard classes and rooms
    the act itself names beside its words (an alert's issue entity, a report's target)."""
    hz = H.active_hazards(state)
    zones = {name: z for name, z in sorted((C.ledger_r(state).get("zones") or {}).items()) if not z.get("cleared")}
    classes = set(hz) | {zone_class(z) for z in zones.values()}
    kinds, warned, invited = set(), set(), set()
    if not classes:
        return Content()
    names = names_of(state)
    for n, part in enumerate(clauses(text) or [""]):
        toks = tokens(part)
        rooms, house = _places(state, toks, speaker_room)
        claimed = _claims(part, sorted(classes))
        if n == 0:
            claimed |= set(extra_kinds) & classes
            rooms |= set(extra_rooms) & set(state.get("rooms") or {})
        directive, target = _directive(part, names)
        if directive == "approach":
            # an invitation toward a zone (the room or the hazard its target words name) sends the listener there;
            # what else the clause claims still alerts them ("Fire in the kitchen, come outside with me")
            to_rooms, _ = _places(state, target, speaker_room)
            to_classes = _claims(" ".join(target), sorted(classes))
            invited |= {name for name, z in zones.items() if z.get("room") in to_rooms or zone_class(z) in to_classes}
        if directive == "evacuate" or (directive == "avoid" and house and not rooms):
            kinds |= set(hz)                    # the whole house: every hazard, every zone
            warned |= set(zones)
            continue
        if directive == "avoid" and rooms:
            kinds |= {k for k, where in hz.items() if set(where) & rooms}
            warned |= {name for name, z in zones.items() if z.get("room") in rooms}
        for kind in claimed:
            where = set(hz.get(kind) or ())
            if kind in hz and (not rooms or (where & rooms) or None in where):
                kinds.add(kind)
            warned |= {name for name, z in zones.items() if zone_class(z) == kind and (not rooms or z.get("room") in rooms)}
    warned -= invited
    return Content(kinds, warned, invited)


def entity_classes(state, eid):
    """Hazard classes a thing or person named by an act stands for (an alert's issue, a report's target): a fire,
    broken glass (a shard or a shattered item), a hidden hazard marker's kind, a puddle (flood); a person in a medical
    emergency (medical) or a threatening visitor (intruder), as hazards.active_hazards counts them."""
    person = (state.get("people") or {}).get(eid)
    if person is not None:
        cond = person.get("_condition") or {}
        out = set()
        if cond.get("emergency") or cond.get("responsive") is False or cond.get("breathing") in ("abnormal", "none"):
            out.add("medical")
        if person.get("_threat"):
            out.add("intruder")
        return out
    rec = (state.get("entities") or {}).get(eid)
    if rec is None:
        return set()
    out = set()
    if rec.get("category") == "fire":
        out.add("fire")
    if rec.get("type") == "glass_shard" or "broken_glass" in (rec.get("tags") or []):
        out.add("broken_glass")
    marker = rec.get("_hazard")
    if isinstance(marker, dict) and marker.get("kind"):
        out.add(str(marker["kind"]))
    if rec.get("category") == "puddle":
        out.add("flood")
    return out
