"""Typed reports to people: report kinds, vocabulary, units, delivery records and the match API used by the
`report` / `report_set` predicate forms (docs/design/TIER1_SWEEP.md sections 5 and 16.1; docs/DIALOGUE.md).

    report(kind=finding|measurement|status|count|none_found, subject=?, issue=?, severity=?, quantity=?, value=?,
           unit=?, state=?, of=?, scope=?, location=?, to=?, channel=?, detail=?)
    report(target, field, value, to=?)            the legacy form, unchanged (systems/core.py)

normalize(args, state) turns a command's arguments into a canonical record: issue and state words through the
vocabulary's synonyms (an unknown word is kept as issue_raw / state_raw with issue / state None), severities through
their aliases, a measurement converted to its quantity's canonical unit (value_si, the original value and unit kept).
record(ctx, record) checks that the subject is known to the robot and that the recipients are reachable over the
channel, delivers it (kernel default rules, refined by the people hook `report_delivered`) and appends one record per
subject to the list section report_log:

    {"n", "t", "kind", "subject": {"kind", "id"}, "issue", "issue_raw", "severity", "quantity", "value_si", "value",
     "unit", "state", "state_raw", "of", "scope", "location", "to", "channel", "delivered", "detail"}

delivered = {pid: {"t", "heard"}} (a text or app message: {"t", "heard": false, "read_s"}: read once the clock passes
read_s). The legacy store reports.<target>.<field> is not touched by typed reports, and legacy reports are not copied
into report_log (that would change every legacy spec's state): match / match_set read them from the legacy store as
records of kind "field", so the evaluator helpers see both.

match(state, expectation) and match_set(state, spec) are the predicate forms' implementation (TIME-EVAL's
predicates.py delegates to them). Messages never say whether a report is correct; an unknown word's suggestions are a
static list per perceived category (an unresolved look-alike gets the generic list), never filtered by the truth.

Report truth (docs/design/VERIFY_HARDENING.md 13.6, WP4), for an episode that scores content (speech.enabled: schema
4, or a task using `said` / `key_claims`; every other episode's records are unchanged):

* record() stores each record's truth at report time in "truth" (report_log is never observed): a status's state, a
  finding's issue on its subject, a none_found's issue in its scope (truth True / False / None from the truth tables
  of kernel/speech.py), a measurement's true value ("value_si") and the value the robot had learned of it
  ("observed_si", realistic perception); unknown truth is None and never scored.
* `corrects=<n>` (report argument) marks a report as the correction of report n: no contradiction with it.
* guards(task, state) -> labels: "house.false_report:<n>" (a status or finding false at report time, a none_found
  while a matching truth exists in scope, a measurement off by more than max(3 x the spec's tolerance, 25 %, the
  quantity's floor)) and "report:contradiction:<subject>" (two reports in the episode about one subject with mutually
  exclusive states or values -- opposite states, measurements or counts apart beyond the same tolerance, a none_found
  and a finding of the same issue class in the same scope, a legacy field reported twice with different values --
  unless the later one corrects the earlier or both were true when made).
* The `report` predicate's "as_of": "report" compares a record's value with its stored truth at report time,
  "observed" with the truth at the time of the observation that supported it (VERIFY_HARDENING 15.2: drifting
  quantities, cooling food), within "tol" in the expectation's unit; a record without a stored truth never matches.
"""
import copy
import math

from . import dialogue
from .errors import ActionError, require
from .plugins import run_hooks
from .predicates import root_room

KINDS = ("finding", "measurement", "status", "count", "none_found")
CHANNELS = ("in_person", "phone", "text", "intercom", "note", "app")
HOUSEHOLD = "household"
LOG_CAP = 500
SUGGESTIONS = 10
NOT_HERE = "They are not here: choose a channel"
WRITABLE_CATEGORIES = ("sticky_note", "note", "notepad", "paper", "whiteboard", "chalkboard", "notebook")
NOTE_TEXT_CAP = 400


def _vocab():
    return dialogue.vocabulary()


def _index():
    return _vocab()["_index"]


# ----------------------------------------------------------------------------------------------------------------
# vocabulary lookups

def issue_of(word):
    """The issue a word names (an issue or one of its synonyms), or None."""
    if not isinstance(word, str) or not word.strip():
        return None
    return _index()["issues"].get(dialogue.phrase(word, stop=False))


def state_of(word):
    """The report state a word names, or None."""
    if not isinstance(word, str) or not word.strip():
        return None
    return _index()["states"].get(dialogue.phrase(word, stop=False))


def severity_of(word):
    """The severity a word names (none / minor / moderate / major / critical and their aliases), or None."""
    if not isinstance(word, str) or not word.strip():
        return None
    return _index()["severities"].get(word.strip().lower().replace(" ", "_"))


def severity_rank(name):
    severities = _vocab()["severities"]
    return severities.index(name) if name in severities else None


def unit_of(unit, quantity=None):
    """(quantity, unit) of a unit word (case-insensitive, aliases allowed); the quantity is inferred from the unit
    when not given and the unit names exactly one. (None, None) when unknown or ambiguous."""
    if not isinstance(unit, str) or not unit.strip():
        return None, None
    word = unit.strip().lower().replace(" ", "_").replace("degrees_", "degrees_")
    units = _index()["units"]
    if quantity is not None:
        quantity = quantity_of(quantity)
        if quantity is None:
            return None, None
        found = units.get(quantity, {}).get(word)
        return (quantity, found) if found else (None, None)
    owners = _index()["unit_quantities"].get(word) or []
    if len(owners) != 1:
        return None, None
    return owners[0], units[owners[0]][word]


def quantity_of(word):
    """The quantity a word names (its own name, case-insensitive), or None."""
    if not isinstance(word, str):
        return None
    key = word.strip().lower().replace(" ", "_")
    quantities = _vocab()["quantities"]
    if key in quantities:
        return key
    aliases = {"temp": "temperature", "weight": "mass", "size": "length", "distance": "length", "height": "length",
               "width": "length", "depth": "length", "capacity": "volume", "time": "duration", "noise": "sound",
               "loudness": "sound", "rh": "humidity", "voltage": "voltage", "number": "count"}
    return aliases.get(key)


def convert(value, unit, quantity):
    """`value` in `unit` converted to the canonical unit of `quantity` (sweep 5.2): canonical = value * a + b with the
    unit's [a, b]. Raises ValueError for an unknown quantity or unit or a non-numeric value."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError("measurement value must be a finite number")
    q, u = unit_of(unit, quantity)
    if q is None:
        raise ValueError("unknown unit {!r} for {}".format(unit, quantity))
    a, b = _vocab()["quantities"][q]["units"][u]
    return float(value) * float(a) + float(b)


def from_canonical(value, unit, quantity):
    """The inverse of convert: a canonical value in `unit`."""
    q, u = unit_of(unit, quantity)
    if q is None:
        raise ValueError("unknown unit {!r} for {}".format(unit, quantity))
    a, b = _vocab()["quantities"][q]["units"][u]
    return (float(value) - float(b)) / float(a)


def _scale(unit, quantity):
    q, u = unit_of(unit, quantity)
    return abs(float(_vocab()["quantities"][q]["units"][u][0])) if q else 1.0


def household_unit(state, quantity):
    """The unit the household reads `quantity` in: metadata.region's row of the vocabulary's household_units, else
    metadata.units (metric / imperial), else the canonical unit."""
    vocab = _vocab()
    meta = (state or {}).get("metadata") or {}
    rows = vocab["household_units"]
    for key in (meta.get("region"), meta.get("units")):
        row = rows.get(key) if isinstance(key, str) else None
        if row and quantity in row:
            return row[quantity]
    spec = vocab["quantities"].get(quantity) or {}
    return spec.get("canonical")


def format_quantity(state, value, quantity):
    """A canonical value as the household reads it (locale units, sweep 15): "72 F", "22.5 C", "3.5 lb", "250 ml"."""
    q = quantity_of(quantity) or quantity
    unit = household_unit(state, q)
    if unit is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    shown = from_canonical(value, unit, q)
    digits = 0 if abs(shown) >= 100 or q == "count" else 1
    text = ("{:.%df}" % digits).format(shown)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text == "-0":
        text = "0"
    label = unit.replace("_us", "").replace("pct_rh", "% RH").replace("pct", "%").replace("_", " ")
    return text + ("" if label.startswith("%") else " ") + label if q != "count" else text


def issue_words(category):
    """The vocabulary words of an issue category (sweep 5.2): an issue class name (condition, moisture, ...) gives that
    class's issues; an entity category (plant, appliance, ...) gives the issues of the classes the vocabulary lists for
    it (category_classes; "default" for an unknown category), in class order and file order, skipping issues whose
    applies_to excludes the category. A static list: never filtered by any thing's true state."""
    vocab = _vocab()
    issues = vocab["issues"]
    if category in vocab["issue_classes"]:
        return [name for name, spec in issues.items() if (spec or {}).get("class") == category]
    listed = vocab["category_classes"].get(category)
    classes = listed or vocab["category_classes"].get("default") or []
    # a category the vocabulary does not list, and the generic ones, keep every issue of their classes
    generic = listed is None or category in ("default", "object", "house", "room")
    out = []
    for cls in classes:
        for name, spec in issues.items():
            if (spec or {}).get("class") != cls:
                continue
            applies = (spec or {}).get("applies_to")
            if applies and not generic and category not in applies:
                continue
            out.append(name)
    return out


def state_words():
    return list(_vocab()["states"])


# ----------------------------------------------------------------------------------------------------------------
# normalize

def _subject_ref(state, value, remembered=()):
    """{"kind", "id"} of one report subject: an entity, room, person or pet id, "house", or a thing the robot
    perceived this episode that no longer exists (`remembered`, remembered_ids: reported like one out of view, so the
    refusal does not tell what was destroyed out of sight -- phase-1 red team)."""
    if not isinstance(value, str) or not value.strip():
        raise ActionError("Name what the report is about (subject)", "argument")
    text = value.strip()
    if text.lower() in ("house", "home", "household"):
        return {"kind": "house", "id": "house"}
    for kind, table in (("entity", state.get("entities", {})), ("room", state.get("rooms", {})),
                        ("person", state.get("people") or {}), ("pet", state.get("pets") or {})):
        if text in table:
            return {"kind": kind, "id": text}
    if text in remembered:
        return {"kind": "entity", "id": text}
    raise ActionError("You have not seen that", "precondition")


def remembered_ids(env):
    """dialogue.remembered_ids: things the robot perceived this episode that no longer exist."""
    return dialogue.remembered_ids(env)


def remembered_belief(env, eid):
    """The Mind's last belief of a thing (None when it has none): label_seen, room, ident."""
    mind = getattr(env, "mind", None) if env is not None else None
    belief = (getattr(mind, "seen", None) or {}).get(eid)
    return belief if isinstance(belief, dict) else None


def _subjects(state, value, remembered=()):
    if isinstance(value, list):
        require(0 < len(value) <= 50, "List 1..50 subjects", "argument")
        refs = [_subject_ref(state, v, remembered) for v in value]
        seen, out = set(), []
        for ref in refs:
            if ref["id"] not in seen:
                seen.add(ref["id"])
                out.append(ref)
        return out
    return [_subject_ref(state, value, remembered)]


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ActionError("{} must be a number".format(name), "argument")
    return float(value) if isinstance(value, float) else value


def normalize(args, state, remembered=()):
    """The canonical report record for a typed `report` command's arguments (sweep 5.1): kind, subject ({"kind", "id"},
    or a list of them for a list subject), issue / issue_raw, severity / severity_raw, quantity / value / unit /
    value_si, state / state_raw, of, scope, location, to (a list: person ids or "household"), channel, detail.
    Raises ActionError (argument) for a missing or malformed field and (precondition) for a subject that is no
    room, person, pet, the house, a thing or a thing the robot remembers (`remembered`: remembered_ids(env))."""
    kind = args.get("kind")
    require(kind in KINDS, "kind must be one of " + ", ".join(KINDS), "argument")
    rec = {"kind": kind, "subject": None, "issue": None, "issue_raw": None, "severity": None, "quantity": None,
           "value_si": None, "value": None, "unit": None, "state": None, "state_raw": None, "of": None, "scope": None,
           "location": None, "to": [], "channel": args.get("channel"), "detail": None}
    subject = args.get("subject")
    if kind in ("finding", "measurement", "status"):
        require(subject not in (None, "", []), "A {} report needs a subject".format(kind), "argument")
    if subject not in (None, "", []):
        subjects = _subjects(state, subject, remembered)
        rec["subject"] = subjects if isinstance(subject, list) else subjects[0]
    if kind in ("finding", "none_found"):
        word = args.get("issue")
        if kind == "none_found" and not word:
            require(args.get("of"), "none_found needs an issue or of", "argument")
        if word:
            require(isinstance(word, str), "issue must be a word", "argument")
            rec["issue"] = issue_of(word)
            if rec["issue"] is None:
                rec["issue_raw"] = word.strip().lower()[:60]
        elif kind == "finding":
            raise ActionError("A finding needs an issue", "argument")
    if args.get("severity") is not None:
        word = args["severity"]
        require(isinstance(word, str), "severity must be a word", "argument")
        rec["severity"] = severity_of(word)
        if rec["severity"] is None:
            rec["severity_raw"] = word.strip().lower()[:30]
    if kind == "measurement":
        require(args.get("value") is not None and args.get("unit"), "A measurement needs value and unit", "argument")
        value = _number(args["value"], "value")
        quantity = args.get("quantity")
        q, unit = unit_of(args["unit"], quantity)
        if q is None:
            if quantity is not None and quantity_of(quantity) is None:
                raise ActionError("Unknown quantity {!r}".format(quantity), "argument")
            raise ActionError("Unknown unit {!r}{}".format(args["unit"], " for " + str(quantity) if quantity else ""),
                              "argument")
        rec.update(quantity=q, unit=unit, value=value, value_si=convert(value, unit, q))
    if kind == "status":
        word = args.get("state")
        require(isinstance(word, str) and word.strip(), "A status report needs a state", "argument")
        rec["state"] = state_of(word)
        if rec["state"] is None:
            rec["state_raw"] = word.strip().lower()[:60]
    if kind == "count":
        require(args.get("of") and args.get("value") is not None, "A count needs of and value", "argument")
        value = _number(args["value"], "value")
        require(value >= 0, "A count is not negative", "argument")
        rec["value"] = value
        rec["value_si"] = float(value)
        if args.get("unit"):
            q, unit = unit_of(args["unit"], "count")
            require(q is not None, "Unknown count unit {!r}".format(args["unit"]), "argument")
            rec.update(unit=unit, value_si=convert(value, unit, "count"))
    if args.get("of"):
        require(isinstance(args["of"], str), "of must be a word or a type", "argument")
        rec["of"] = dialogue.phrase(args["of"]) or args["of"].strip().lower()
    if args.get("scope") not in (None, "", []):
        rec["scope"] = _subject_ref(state, args["scope"], remembered)
    elif kind == "none_found":
        raise ActionError("none_found needs a scope (a room, a container or the house)", "argument")
    if args.get("location"):
        require(args["location"] in state.get("rooms", {}), "location must be a room", "argument")
        rec["location"] = args["location"]
    to = args.get("to")
    if to:
        require(isinstance(to, str), "to names one person or the household", "argument")
        to = to.strip()
        require(to == HOUSEHOLD or to in (state.get("people") or {}), "Unknown recipient {!r}".format(to), "argument")
        rec["to"] = [to]
    if rec["channel"] is not None:
        require(rec["channel"] in CHANNELS, "channel must be one of " + ", ".join(CHANNELS), "argument")
    if args.get("detail"):
        rec["detail"] = str(args["detail"])[:300]
    if args.get("corrects") is not None:
        corrects = args["corrects"]
        require(type(corrects) is int and corrects >= 1, "corrects names an earlier report's number", "argument")
        rec["corrects"] = corrects
    return rec


# ----------------------------------------------------------------------------------------------------------------
# preconditions, delivery and recording

def subject_known(env, ref):
    """Whether the robot knows a report subject (sweep 5.1): a thing it has perceived this episode or in prior
    knowledge, a room it has been in or has on its map, a person or pet it has seen, or the house."""
    state = env.world.s
    kind, sid = ref["kind"], ref["id"]
    if kind == "house":
        return True
    mind = getattr(env, "mind", None)
    if kind == "room":
        if sid == state["robot"].get("room"):
            return True
        # the Mind's map is the known map (kernel v3.1; env._known_map is only its compatibility view, K-EVAL 4)
        return mind is not None and sid in getattr(mind, "map", {})
    if kind in ("person", "pet"):
        if mind is not None and sid in getattr(mind, "seen", {}):
            return True
        agent = (state.get("people" if kind == "person" else "pets") or {}).get(sid) or {}
        return agent.get("location") == state["robot"].get("room")
    if sid in [v for v in (state["robot"].get("hands") or {}).values() if v]:
        return True
    return dialogue.perceived_ever(env, sid)


def _perceived_category(env, ref):
    """The category the robot perceives for a report subject: the thing's category when identified, "object" for an
    unresolved look-alike or unfamiliar thing (so suggestions are the same for every member of a group)."""
    kind, sid = ref["kind"], ref["id"]
    if kind == "person":
        return "person"
    if kind == "pet":
        return "pet"
    if kind in ("room", "house"):
        return kind
    record = env.world.s.get("entities", {}).get(sid)
    realism = getattr(env, "realism", None)
    if record is None:
        # a thing destroyed since the robot saw it: the category it perceived then (an unresolved one: "object")
        gone = (getattr(realism, "gone", None) or {}).get(sid) or {} if realism is not None else {}
        belief = remembered_belief(env, sid) or {}
        if gone.get("unresolved") or belief.get("ident") in ("unresolved", "shape", "detected"):
            return "object"
        return belief.get("kind_seen") or gone.get("kind") or "object"
    if realism is not None and realism.unresolved(sid, record):
        return "object"
    return record.get("category") or "object"


def _label(env, ref):
    """How the robot names a subject when it writes or says the report: its perceived label (realistic perception:
    the identity it has resolved), a room's label, a person's or pet's label."""
    state = env.world.s
    kind, sid = ref["kind"], ref["id"]
    if kind == "house":
        return "the house"
    if kind == "room":
        return str((state["rooms"].get(sid) or {}).get("label") or sid).replace("_", " ")
    if kind in ("person", "pet"):
        return str(((state.get("people" if kind == "person" else "pets") or {}).get(sid) or {}).get("label") or sid)
    record = state["entities"].get(sid)
    if record is None:
        belief = remembered_belief(env, sid) or {}
        if isinstance(belief.get("label_seen"), str):
            return belief["label_seen"]     # a thing destroyed since: the label it showed when last seen
        record = {}
    realism = getattr(env, "realism", None)
    if realism is not None and record:
        try:
            ident = realism._identify(sid, record, realism.knowledge.entity_level(sid, record))[0]
            if isinstance(ident.get("label"), str):
                return ident["label"]
        except Exception:  # noqa: BLE001
            pass
    return str(record.get("label") or record.get("type") or "thing").replace("_", " ")


def render_text(env, rec, refs):
    """The report as the robot says or writes it: "Finding: leak - kitchen sink (major)." and so on."""
    names = ", ".join(_label(env, ref) for ref in refs) if refs else ""
    kind = rec["kind"]
    if kind == "finding":
        word = (rec["issue"] or rec.get("issue_raw") or "issue").replace("_", " ")
        text = "{}: {}".format(word.capitalize(), names) + (" ({})".format(rec["severity"]) if rec.get("severity") else "")
    elif kind == "measurement":
        text = "{} {}: {:g} {}".format(names, rec["quantity"], rec["value"], rec["unit"])
    elif kind == "status":
        text = "{} is {}".format(names, (rec["state"] or rec.get("state_raw") or "").replace("_", " "))
    elif kind == "count":
        text = "{:g} {}".format(rec["value"], (rec["of"] or "").replace("_", " "))
        if rec.get("scope"):
            text += " in " + _label(env, rec["scope"])
    else:
        what = (rec.get("issue") or rec.get("issue_raw") or rec.get("of") or "nothing").replace("_", " ")
        text = "No {} found in {}".format(what, _label(env, rec["scope"]))
    if rec.get("detail"):
        text += ". " + rec["detail"]
    return text[:NOTE_TEXT_CAP]


def _writing_surface(ctx):
    """The entity the robot writes a note on: a held writable thing (sticky note, paper, notepad), else a whiteboard
    within reach in its room, else None."""
    state = ctx.w.s
    entities = state.get("entities", {})
    for eid in [v for v in (state["robot"].get("hands") or {}).values() if v]:
        rec = entities.get(eid) or {}
        if rec.get("writable") or rec.get("category") in WRITABLE_CATEGORIES or rec.get("type") in WRITABLE_CATEGORIES:
            return eid
    from .geometry import reach_problem
    room = state["robot"].get("room")
    for eid in sorted(entities):
        rec = entities[eid]
        if rec.get("location") == room and (rec.get("category") in ("whiteboard", "chalkboard") or
                                            rec.get("type") in ("whiteboard", "chalkboard")):
            try:
                if reach_problem(state, eid) is None:
                    return eid
            except Exception:  # noqa: BLE001
                continue
    return None


BOARD_CATEGORIES = ("whiteboard", "chalkboard")


def _written(rec, text):
    """A note's text after the robot writes `text` on it: a fresh sticky note or sheet holds just the note; a
    whiteboard or chalkboard keeps what was already on it (a household's calendar block, other notes) and gets the
    note on a new line below (people's adoption request K-NOTE-APPEND)."""
    old = rec.get("text")
    board = rec.get("category") in BOARD_CATEGORIES or rec.get("type") in BOARD_CATEGORIES
    if board and isinstance(old, str) and old.strip():
        return old.rstrip("\n") + "\n" + text
    return text


def _intercom_route(state, pid):
    """Kernel default: an intercom station (category or type "intercom", or intercom: true) in the robot's room and in
    the person's room."""
    rooms = {state["robot"].get("room"), ((state.get("people") or {}).get(pid) or {}).get("location")}
    have = set()
    for eid, rec in state.get("entities", {}).items():
        if rec.get("category") == "intercom" or rec.get("type") == "intercom" or rec.get("intercom") is True:
            have.add(root_room(state, eid))
    return None not in rooms and rooms <= have


def _hears(state, pid):
    person = (state.get("people") or {}).get(pid) or {}
    hearing = (person.get("abilities") or {}).get("hearing") or "full"
    return bool(person.get("awake", True)) and hearing != "deaf"


def deliver(ctx, rec):
    """Check reachability and deliver one normalized report: returns (channel, delivered dict, note entity or None).
    Raises ActionError with the fixed wording when a recipient cannot be reached over the channel."""
    from . import body
    w = ctx.w
    state = w.s
    people = state.get("people") or {}
    channel = rec.get("channel")
    to = list(rec.get("to") or [])
    robot_room = state["robot"].get("room")
    network = bool(state["robot"].get("network", True))
    if channel == "app" and not to:
        to = sorted(p for p, person in people.items() if person.get("has_phone") or person.get("reachable_by_phone"))
        require(bool(to), "Nobody in the household has the app", "precondition")
        rec["to"] = to
    persons = [p for p in to if p != HOUSEHOLD]
    if persons and channel is None:
        for pid in persons:
            require(people[pid].get("location") == robot_room, NOT_HERE)
        channel = "in_person"
    note = None
    if channel == "note":
        note = _writing_surface(ctx)
        require(note is not None, "Hold something to write on (a sticky note or paper) or stand at a whiteboard")
    if channel in ("phone", "text", "app"):
        require(network, "No network: the robot cannot call or message anyone")
    delivered = {}
    t = state.get("time_s", 0.0)
    speaks = body.can_speak(state)
    for pid in persons:
        person = people[pid]
        phone = bool(person.get("has_phone") or person.get("reachable_by_phone"))
        if channel == "in_person":
            require(person.get("location") == robot_room, "That person is not here")
        elif channel == "intercom":
            verdict = None
        elif channel in ("phone", "text", "app"):
            # a phone, a text or the app reaches them wherever they are, at home or away (5.3: network and a phone)
            require(phone, "They cannot be reached by phone")
        verdict = None
        for result in run_hooks("report_delivered", w, copy.deepcopy(rec), pid, channel):
            if isinstance(result, dict) and "heard" in result:
                verdict = result
                break
        if verdict is None and channel == "intercom":
            require(_intercom_route(state, pid), "There is no intercom route to them")
        if verdict is not None:
            entry = {"t": t, "heard": bool(verdict.get("heard"))}
            for key in ("read_s", "by", "how"):
                if key in verdict:
                    entry[key] = copy.deepcopy(verdict[key])
        elif channel in ("in_person", "intercom"):
            entry = {"t": t, "heard": bool(speaks and _hears(state, pid))}
        elif channel == "phone":
            entry = {"t": t, "heard": bool(person.get("awake", True) and _hears(state, pid))}
        elif channel in ("text", "app"):
            delay = person.get("ack_delay_s", 300.0)
            delay = float(delay) if isinstance(delay, (int, float)) and not isinstance(delay, bool) else 300.0
            entry = {"t": t, "heard": False, "read_s": t + max(0.0, delay)}
        else:                                       # a note: people read notes in their room when awake
            entry = {"t": t, "heard": False, "note": note}
        delivered[pid] = entry
    return channel, delivered, note


def _report_log(w):
    log = w.s.get("report_log")
    if isinstance(log, list):
        return w.sec("report_log")
    w.set_scalar("report_log", [])
    return w.s["report_log"]


def record(ctx, rec):
    """Store a normalized report (sweep 5.3): the subjects must be known to the robot ("You have not seen that"), the
    recipients reachable over the channel; one report_log record per subject (a list subject is one finding per
    subject); the note's text is written for the note channel; stats.reports_unclassified counts an unknown issue or
    state word. Returns {"records": [...], "channel", "note", "suggest": [...] or None, "text"}."""
    w = ctx.w
    env = ctx.env
    state = w.s
    refs = rec["subject"] if isinstance(rec["subject"], list) else ([rec["subject"]] if rec["subject"] else [])
    for ref in refs:
        require(subject_known(env, ref), "You have not seen that")
    if rec.get("scope"):
        require(subject_known(env, rec["scope"]), "You have not seen that")
    channel, delivered, note = deliver(ctx, rec)
    text = render_text(env, rec, refs)
    if note is not None:
        w.mut(note)["text"] = _written(state["entities"][note], text)
    log = _report_log(w)
    number = (log[-1].get("n", len(log)) + 1) if log else 1
    stored = []
    scored = _content_on(env)
    for ref in refs or [None]:
        item = {k: copy.deepcopy(v) for k, v in rec.items()}
        item["subject"] = copy.deepcopy(ref)
        item.update(n=number, t=state.get("time_s", 0.0), channel=channel, delivered=copy.deepcopy(delivered))
        if item.get("location") is None:
            anchor = ref["id"] if ref and ref["kind"] in ("entity", "person", "pet") else None
            if ref and ref["kind"] == "room":
                item["location"] = ref["id"]
            elif anchor is not None and ref["kind"] == "entity" and anchor not in state["entities"]:
                item["location"] = (remembered_belief(env, anchor) or {}).get("room")   # where it was last seen
            elif anchor is not None:
                item["location"] = root_room(state, anchor)
            elif rec.get("scope") and rec["scope"]["kind"] == "room":
                item["location"] = rec["scope"]["id"]
        if scored:
            item["truth"] = report_truth(env, state, item)
        log.append(item)
        stored.append(item)
        number += 1
    if len(log) > LOG_CAP:
        del log[:len(log) - LOG_CAP]
    suggest = None
    if rec.get("issue_raw") is not None or rec.get("state_raw") is not None:
        w.stat("reports_unclassified")
        if rec.get("issue_raw") is not None:
            category = _perceived_category(env, refs[0]) if refs else (
                _perceived_category(env, rec["scope"]) if rec.get("scope") else "default")
            suggest = issue_words(category)[:SUGGESTIONS]
        else:
            suggest = state_words()[:SUGGESTIONS]
    w.emit("report", report_kind=rec["kind"], subjects=[r["id"] for r in refs], issue=rec.get("issue"),
           state=rec.get("state"), to=list(rec.get("to") or []), channel=channel)
    return {"records": stored, "channel": channel, "note": note, "suggest": suggest, "text": text}


# ----------------------------------------------------------------------------------------------------------------
# matching (the report / report_set predicate forms, sweep 5.4)

def _kind_of(state, sid):
    for kind, table in (("entity", state.get("entities", {})), ("room", state.get("rooms", {})),
                        ("person", state.get("people") or {}), ("pet", state.get("pets") or {})):
        if sid in table:
            return kind
    return "house" if sid == "house" else "entity"


def _legacy_records(state):
    """Legacy field reports (reports.<target>.<field>) as records of kind "field" (time from the event log when the
    report event is still in it)."""
    out = []
    store = state.get("reports") or {}
    if not store:
        return out
    times = {}
    for entry in state.get("event_log") or []:
        if isinstance(entry, dict) and entry.get("kind") == "report" and "field" in entry:
            times[(entry.get("target"), entry.get("field"))] = entry.get("time_s")
    for target in sorted(store):
        fields = store[target]
        if not isinstance(fields, dict):
            continue
        sent = fields.get("_to") or {}
        for field in sorted(fields):
            if field == "_to":
                continue
            to = sent.get(field)
            out.append({"n": None, "t": times.get((target, field)), "kind": "field",
                        "subject": {"kind": _kind_of(state, target), "id": target}, "field": field,
                        "value": fields[field], "to": [to] if to else [], "channel": None, "delivered": {},
                        "location": root_room(state, target), "legacy": True})
    return out


def records_of(state):
    """Every report record the evaluator helpers see: report_log, then the legacy field store."""
    return [r for r in state.get("report_log") or [] if isinstance(r, dict)] + _legacy_records(state)


def _as_list(value):
    if value is None:
        return None
    return list(value) if isinstance(value, (list, tuple, set)) else [value]


def _time_of(state, name, times):
    """Seconds of a time bound: a number, a name in `times` (milestone or phase times the caller passes), a milestone or
    phase of the evaluation overlay ("$temporal"), or a phase id in state.phase.history ("phase:<id>" or the bare id:
    its start)."""
    if name is None:
        return None
    if isinstance(name, (int, float)) and not isinstance(name, bool):
        return float(name)
    key = str(name)
    if times and key in times:
        found = times[key]
        if isinstance(found, dict):
            found = found.get("time_s", found.get("t"))
        return float(found) if isinstance(found, (int, float)) else None
    phase_id = key[len("phase:"):] if key.startswith("phase:") else key
    temporal = state.get("$temporal") or {}         # the evaluation overlay (kernel/evaluation.py evaluation_view)
    reached = (temporal.get("milestones") or {}).get(key)
    if isinstance(reached, dict) and isinstance(reached.get("time_s"), (int, float)):
        return float(reached["time_s"])
    started = (temporal.get("phases") or {}).get(phase_id)
    if isinstance(started, (int, float)) and not isinstance(started, bool):
        return float(started)
    phase = state.get("phase") or {}
    for item in (phase.get("history") or []):
        if isinstance(item, dict) and item.get("id") == phase_id:
            start = item.get("started_s", item.get("start_s"))
            return float(start) if isinstance(start, (int, float)) else None
    if phase.get("active") == phase_id and isinstance(phase.get("started_s"), (int, float)):
        return float(phase["started_s"])
    return None


def _heard(state, rec, pid=None):
    now = float(state.get("time_s", 0.0))
    for who, entry in (rec.get("delivered") or {}).items():
        if pid is not None and who != pid:
            continue
        if not isinstance(entry, dict):
            continue
        if entry.get("heard"):
            return True
        read = entry.get("read_s")
        if isinstance(read, (int, float)) and now >= read:
            return True
    return False


def _value_ok(rec, exp):
    """Value comparison of a measurement (canonical units, tolerance in the expectation's unit), count (exact or tol)
    or legacy field (equality, or tol for numbers); with "as_of", the record's value against its stored truth (at
    report time, or at the supporting observation's time)."""
    if "as_of" in exp:
        return _as_of_ok(rec, exp)
    if "value" not in exp:
        return True
    want = exp["value"]
    kind = rec.get("kind")
    tol = exp.get("tol", 0.0)
    try:
        tol = abs(float(tol))
    except (TypeError, ValueError):
        tol = 0.0
    if kind == "measurement":
        if not isinstance(rec.get("value_si"), (int, float)):
            return False
        quantity = quantity_of(exp.get("quantity") or rec.get("quantity")) or rec.get("quantity")
        unit = exp.get("unit") or (_vocab()["quantities"].get(quantity) or {}).get("canonical")
        try:
            want_si = convert(want, unit, quantity)
            tol_si = tol * _scale(unit, quantity)
        except ValueError:
            return False
        return abs(float(rec["value_si"]) - want_si) <= tol_si + 1e-9
    got = rec.get("value_si", rec.get("value")) if kind == "count" else rec.get("value")
    if isinstance(got, (int, float)) and not isinstance(got, bool) and isinstance(want, (int, float)) \
            and not isinstance(want, bool):
        return abs(float(got) - float(want)) <= tol + 1e-9
    return got == want


def _as_of_ok(rec, exp):
    truth = rec.get("truth")
    if not isinstance(truth, dict) or rec.get("kind") != "measurement":
        return False
    want = truth.get("value_si")
    if exp.get("as_of") == "observed" and isinstance(truth.get("observed_si"), (int, float)):
        want = truth["observed_si"]
    got = rec.get("value_si")
    if not isinstance(want, (int, float)) or not isinstance(got, (int, float)):
        return False
    quantity = quantity_of(exp.get("quantity") or rec.get("quantity")) or rec.get("quantity")
    unit = exp.get("unit") or (_vocab()["quantities"].get(quantity) or {}).get("canonical")
    try:
        tol = abs(float(exp.get("tol", 0.0))) * _scale(unit, quantity)
    except (TypeError, ValueError):
        tol = 0.0
    return abs(float(got) - float(want)) <= tol + 1e-9


def _rec_matches(state, rec, exp, times):
    kinds = _as_list(exp.get("kind"))
    if kinds is not None and rec.get("kind") not in kinds:
        return False
    subjects = _as_list(exp.get("subject"))
    if subjects is not None:
        sid = (rec.get("subject") or {}).get("id")
        if sid not in subjects:
            return False
    if "issue" in exp:
        wanted = [issue_of(w) or str(w) for w in _as_list(exp["issue"])]
        if rec.get("issue") not in wanted:
            return False
    if "state" in exp:
        wanted = [state_of(w) or str(w) for w in _as_list(exp["state"])]
        if rec.get("state") not in wanted:
            return False
    if "field" in exp and rec.get("field") != exp["field"]:
        return False
    if "quantity" in exp and rec.get("kind") == "measurement" and rec.get("quantity") != quantity_of(exp["quantity"]):
        return False
    if "of" in exp and rec.get("of") != (dialogue.phrase(exp["of"]) or exp["of"]):
        return False
    if "scope" in exp:
        if (rec.get("scope") or {}).get("id") != exp["scope"]:
            return False
    rank = severity_rank(rec.get("severity")) if rec.get("severity") else None
    if "severity_at_least" in exp:
        floor = severity_rank(severity_of(exp["severity_at_least"]) or exp["severity_at_least"])
        if rank is None or floor is None or rank < floor:
            return False
    if "severity_at_most" in exp:
        ceiling = severity_rank(severity_of(exp["severity_at_most"]) or exp["severity_at_most"])
        if rank is None or ceiling is None or rank > ceiling:
            return False
    if "severity" in exp:
        target = severity_rank(severity_of(exp["severity"]) or exp["severity"])
        tol = int(exp.get("severity_tol", 1))
        if rank is None or target is None or abs(rank - target) > tol:
            return False
    for key in ("room", "location"):
        if key in exp and rec.get("location") != exp[key]:
            return False
    if "to" in exp and exp["to"] not in (rec.get("to") or []):
        return False
    if "channel" in exp and rec.get("channel") not in _as_list(exp["channel"]):
        return False
    if exp.get("heard") is True and not _heard(state, rec, exp.get("to") if exp.get("to") != HOUSEHOLD else None):
        return False
    if exp.get("heard") is False and _heard(state, rec, exp.get("to") if exp.get("to") != HOUSEHOLD else None):
        return False
    t = rec.get("t")
    start = _time_of(state, exp.get("after"), times) if "after" in exp else None
    if "after" in exp:
        if start is None or t is None or t < start - 1e-9:
            return False
    if "before_s" in exp:
        limit = (start if start is not None else 0.0) + float(exp["before_s"])
        if t is None or t > limit + 1e-9:
            return False
    if "before" in exp:
        end = _time_of(state, exp["before"], times)
        if end is None or t is None or t > end + 1e-9:
            return False
    return _value_ok(rec, exp)


EXPECTATION_KEYS = {"kind", "subject", "issue", "state", "field", "quantity", "value", "unit", "tol", "of", "scope",
                    "severity", "severity_tol", "severity_at_least", "severity_at_most", "room", "location", "to",
                    "channel", "heard", "after", "before_s", "before", "as_of"}
AS_OF = ("report", "observed")


def validate_expectation(exp):
    """Problems of a `report` predicate body (unknown keys, a kind outside the report kinds and "field")."""
    if not isinstance(exp, dict):
        return ["report expectation must be an object"]
    problems = []
    unknown = sorted(set(exp) - EXPECTATION_KEYS)
    if unknown:
        problems.append("unknown report keys: " + ", ".join(unknown))
    for kind in _as_list(exp.get("kind")) or []:
        if kind not in KINDS + ("field",):
            problems.append("unknown report kind {!r}".format(kind))
    if "as_of" in exp:
        if exp["as_of"] not in AS_OF:
            problems.append("as_of must be report or observed")
        if "value" in exp:
            problems.append("as_of compares with the stored truth: give tol, not value")
    return problems


def match(state, expectation, times=None):
    """Whether some stored report meets `expectation` (the `report` predicate form, sweep 5.4): every given key must
    match (subject: an id or a list of acceptable ids; issue / state: words or lists, through synonyms; severity:
    within severity_tol ranks (default 1), or severity_at_least / severity_at_most; room / location; to (a person
    or "household"); heard: delivered and heard (a text counts once read); after / before_s / before: the record's
    time against a number of seconds or a milestone / phase name (`times`: {name: seconds} from the caller, or the
    phase history in state.phase); value with tol, compared in canonical units for measurements)."""
    if not isinstance(expectation, dict):
        return False
    return any(_rec_matches(state, rec, expectation, times) for rec in records_of(state))


def matching_records(state, expectation, times=None):
    """The stored records that meet a `report` expectation (the ones match() finds; the verifier's perceivability
    check of scored reports, sweep 5.5)."""
    if not isinstance(expectation, dict):
        return []
    return [rec for rec in records_of(state) if _rec_matches(state, rec, expectation, times)]


def _in_scope(state, rec, scope):
    if scope is None:
        return True
    sid = (rec.get("subject") or {}).get("id")
    if isinstance(scope, dict):
        rooms = scope.get("rooms")
        if rooms is not None:
            rooms = _as_list(rooms)
            room = rec.get("location") or (root_room(state, sid) if sid else None)
            if room not in rooms:
                return False
        within = scope.get("within")
        if within is not None:
            from .predicates import is_within
            if sid is None or not (sid == within or is_within(state, sid, within)):
                return False
        return True
    rooms = _as_list(scope)
    return (rec.get("location") or (root_room(state, sid) if sid else None)) in rooms


def _set_records(state, spec, times):
    """The records a `report_set` counts: of spec["kind"] (default finding) with its issue / state / ... inside its
    scope and time window, with a subject."""
    exp = {k: spec[k] for k in ("issue", "state", "after", "before_s", "before", "to", "heard", "channel",
                                "severity_at_least", "severity", "severity_tol", "of") if k in spec}
    exp["kind"] = spec.get("kind", "finding")
    out = []
    for rec in records_of(state):
        sid = (rec.get("subject") or {}).get("id")
        if sid is None or not _in_scope(state, rec, spec.get("scope")):
            continue
        if _rec_matches(state, rec, exp, times):
            out.append(rec)
    return out


def matching_set_records(state, spec, times=None):
    """The records a `report_set` scores as correct: its counted records whose subject is expected (the verifier's
    perceivability check of scored reports, sweep 5.5)."""
    spec = spec or {}
    expected = set(_as_list(spec.get("expected")) or [])
    return [rec for rec in _set_records(state, spec, times) if (rec.get("subject") or {}).get("id") in expected]


def match_set(state, spec, times=None):
    """(precision, recall, detail) of the reported subjects against an expected set (`report_set`, sweep 5.4): over
    records of spec["kind"] (default finding) with spec["issue"] / spec["state"] (if given) inside spec["scope"]
    ({"rooms": [...]} / {"within": id} / a room list) and the time window (after / before_s / before), R = the
    reported subjects, E = spec["expected"]: precision = |R & E| / |R| (1.0 when R and E are empty, 0.0 when only R
    is), recall = |R & E| / |E| (1.0 when E is empty). detail = {"reported", "expected", "correct", "false",
    "missed"} (sorted ids)."""
    spec = spec or {}
    expected = set(_as_list(spec.get("expected")) or [])
    reported = {(rec.get("subject") or {}).get("id") for rec in _set_records(state, spec, times)}
    correct = reported & expected
    if reported:
        precision = len(correct) / float(len(reported))
    else:
        precision = 1.0 if not expected else 0.0
    recall = len(correct) / float(len(expected)) if expected else 1.0
    detail = {"reported": sorted(reported), "expected": sorted(expected), "correct": sorted(correct),
              "false": sorted(reported - expected), "missed": sorted(expected - reported)}
    return round(precision, 6), round(recall, 6), detail


# ----------------------------------------------------------------------------------------------------------------
# perceivability of scored reports (sweep 5.5; the verifier calls this in realistic mode)

def _evidence_entries(rec):
    vocab = _vocab()
    if rec.get("kind") == "finding" and rec.get("issue"):
        return (vocab["issues"].get(rec["issue"]) or {}).get("evidence") or []
    if rec.get("kind") == "status" and rec.get("state"):
        return (vocab["states"].get(rec["state"]) or {}).get("evidence") or []
    return []


def evidence_perceived(env, rec, notices=None, granted=None):
    """Whether the reference run perceived evidence for a finding / status / measurement record (sweep 5.5): one of the
    issue's or state's evidence entries -- a field of the subject granted at its tier (realism knowledge; `granted`:
    {eid: {field: level}} when the caller collected grants), a field "near" the subject (the same field on a thing in
    the subject's room), or a notice of the listed kind (`notices`: the kinds perceived during the run); for a
    measurement, the subject's field for the quantity at measure tier, or a reading in a message (the caller
    passes it in `granted` as field "_reading:<quantity>")."""
    realism = getattr(env, "realism", None)
    state = env.world.s
    sid = (rec.get("subject") or {}).get("id")
    kinds = set(notices or ())
    if rec.get("kind") == "measurement":
        fields = (granted or {}).get(sid) or {}
        if "_reading:" + str(rec.get("quantity")) in fields:
            return True
        if realism is not None and sid in state.get("entities", {}):
            record = state["entities"][sid]
            from .realism import MEASURE
            for field in record:
                if realism.tier(record, field) == "measure" and realism.knowledge.level(sid, record, field) >= MEASURE:
                    return True
        return False
    for entry in _evidence_entries(rec):
        if not isinstance(entry, dict):
            continue
        if "notice" in entry:
            if entry["notice"] in kinds or (entry["notice"] == "any_sound" and kinds):
                return True
            continue
        field = entry.get("field")
        if not field:
            continue
        targets = [sid]
        if entry.get("near") and sid in state.get("entities", {}):
            room = root_room(state, sid)
            targets = [e for e in state["entities"] if root_room(state, e) == room]
        for eid in targets:
            if eid in ((granted or {}).get(eid) or {}) or field in ((granted or {}).get(eid) or {}):
                return True
            record = state.get("entities", {}).get(eid)
            if record is None or field not in record:
                continue
            if realism is None:
                if dialogue.perceived_ever(env, eid):
                    return True
                continue
            from .realism import TIER_NEED
            tier = realism.tier(record, field)
            if tier == "never":
                continue
            if realism.knowledge.level(eid, record, field) >= TIER_NEED.get(tier, 1) or \
                    (tier == "glance" and dialogue.perceived_ever(env, eid)):
                return True
    return not _evidence_entries(rec)


def perceivability_gaps(env, records, notices=None, granted=None):
    """The scored finding / status / measurement records without perceived evidence (sweep 5.5): a list of
    "report <n> (<kind> <issue/state> of <subject>)" strings."""
    gaps = []
    for rec in records:
        if rec.get("kind") not in ("finding", "status", "measurement") or rec.get("legacy"):
            continue
        if not evidence_perceived(env, rec, notices, granted):
            gaps.append("report {} ({} {} of {})".format(rec.get("n"), rec.get("kind"),
                                                         rec.get("issue") or rec.get("state") or rec.get("quantity"),
                                                         (rec.get("subject") or {}).get("id")))
    return gaps


# ----------------------------------------------------------------------------------------------------------------
# truth at report time and the report guards (VERIFY_HARDENING 13.6, WP4)

def _content_on(env):
    from . import speech
    return speech.enabled(env)


def _scope_members(state, scope):
    """(kind, ids) of a none_found scope: a room's entities, a container's descendants with itself, the house."""
    kind, sid = (scope or {}).get("kind"), (scope or {}).get("id")
    entities = state.get("entities") or {}
    if kind == "house":
        return sorted(entities)
    if kind == "room":
        return sorted(e for e in entities if root_room(state, e) == sid)
    if kind == "entity":
        from .predicates import is_within
        return sorted(e for e in entities if e == sid or is_within(state, e, sid))
    return []


def _issue_of_word(word):
    if not isinstance(word, str):
        return None
    return issue_of(word)


def _learned(env, sid, quantity):
    """The value of a subject's quantity the robot learned (realism knowledge: the true value when it was read), in
    the quantity's canonical unit, else None."""
    from . import speech
    realism = getattr(env, "realism", None) if env is not None else None
    if realism is None:
        return None
    fields = speech.truth_tables()["quantities"].get(quantity) or ()
    entry = (getattr(realism.knowledge, "data", None) or {}).get(sid) or {}
    known = entry.get("fields") or {}
    for field in fields:
        item = known.get(field)
        if isinstance(item, list) and len(item) >= 4 and isinstance(item[3], (int, float)) and \
                not isinstance(item[3], bool):
            return float(item[3])
    return None


def report_truth(env, state, rec):
    """The truth of a typed report record at report time: {"ok": True / False / None} for a status, finding or
    none_found; {"ok": None, "value_si", "observed_si"} for a measurement (the value is judged by guards() with the
    spec's tolerance); {"ok": None} otherwise."""
    from . import speech
    kind = rec.get("kind")
    subject = rec.get("subject") or {}
    skind, sid = subject.get("kind"), subject.get("id")
    out = {"ok": None, "t": state.get("time_s", 0.0)}
    if kind == "status" and rec.get("state") and sid is not None:
        out["ok"] = speech.state_truth(state, skind, sid, rec["state"])
    elif kind == "finding" and rec.get("issue") and sid is not None:
        out["ok"] = speech.issue_truth(state, skind, sid, rec["issue"])
    elif kind == "none_found":
        issue = rec.get("issue") or _issue_of_word(rec.get("of"))
        if issue:
            found = [speech.issue_truth(state, "entity", e, issue) for e in _scope_members(state, rec.get("scope"))]
            if any(f is True for f in found):
                out["ok"] = False
            elif any(f is False for f in found):
                out["ok"] = True
    elif kind == "measurement" and sid is not None and rec.get("quantity"):
        out["value_si"] = speech.quantity_truth(state, skind, sid, rec["quantity"])
        out["observed_si"] = _learned(env, sid, rec["quantity"])
    return out


def _spec_tolerance(task, rec):
    """The tolerance (canonical units) a task's report predicates give a measurement of this subject and quantity:
    the largest such tol, else 0."""
    best = 0.0
    subject = (rec.get("subject") or {}).get("id")

    def walk(node, depth=0):
        nonlocal best
        if depth > 30:
            return
        if isinstance(node, dict):
            body = node.get("report")
            if isinstance(body, dict) and "tol" in body:
                subjects = _as_list(body.get("subject"))
                q = quantity_of(body.get("quantity")) if body.get("quantity") else rec.get("quantity")
                if (subjects is None or subject in subjects) and q == rec.get("quantity"):
                    unit = body.get("unit") or (_vocab()["quantities"].get(q) or {}).get("canonical")
                    try:
                        best = max(best, abs(float(body["tol"])) * _scale(unit, q))
                    except (TypeError, ValueError):
                        pass
            for value in node.values():
                walk(value, depth + 1)
        elif isinstance(node, list):
            for value in node:
                walk(value, depth + 1)

    walk(task or {})
    return best


def _measure_tol(task, rec, true):
    from . import speech
    q = rec.get("quantity")
    return max(3.0 * _spec_tolerance(task, rec), speech.QUANTITY_TOL * abs(true), speech.QUANTITY_FLOOR.get(q, 0.0))


def _issue_class(rec):
    vocab = _vocab()
    issue = rec.get("issue") or _issue_of_word(rec.get("of"))
    if issue:
        return (vocab["issues"].get(issue) or {}).get("class") or issue
    of = rec.get("of")
    if isinstance(of, str) and of in vocab["issue_classes"]:
        return of
    return None


def _exclusive_states(a, b):
    from . import speech
    if a == b or not a or not b:
        return False
    if speech.opposite(a) == b:
        return True
    tables = speech.truth_tables()["states"]
    fa = [c.get("field") for c in tables.get(a) or () if isinstance(c, dict)]
    fb = [c.get("field") for c in tables.get(b) or () if isinstance(c, dict)]
    return bool(fa) and bool(fb) and fa[0] == fb[0]


def _in_scope_of(state, none_rec, finding):
    scope = none_rec.get("scope") or {}
    sid = (finding.get("subject") or {}).get("id")
    if scope.get("kind") == "house":
        return True
    if scope.get("kind") == "room":
        return finding.get("location") == scope.get("id") or (sid is not None and root_room(state, sid) == scope["id"])
    if scope.get("kind") == "entity":
        from .predicates import is_within
        return sid is not None and (sid == scope.get("id") or is_within(state, sid, scope.get("id")))
    return False


def _both_true(r1, r2):
    t1, t2 = r1.get("truth") or {}, r2.get("truth") or {}
    return t1.get("ok") is True and t2.get("ok") is True


def _contradiction(state, task, r1, r2):
    """The subject id two reports contradict about (r1 earlier), else None."""
    if r2.get("corrects") is not None and r2.get("corrects") == r1.get("n"):
        return None
    k1, k2 = r1.get("kind"), r2.get("kind")
    s1, s2 = (r1.get("subject") or {}).get("id"), (r2.get("subject") or {}).get("id")
    if k1 == k2 == "status" and s1 == s2 and s1 is not None:
        if _exclusive_states(r1.get("state"), r2.get("state")) and not _both_true(r1, r2):
            return s1
        return None
    if k1 == k2 == "measurement" and s1 == s2 and s1 is not None and r1.get("quantity") == r2.get("quantity"):
        v1, v2 = r1.get("value_si"), r2.get("value_si")
        if not isinstance(v1, (int, float)) or not isinstance(v2, (int, float)):
            return None
        tol = _measure_tol(task, r2, max(abs(v1), abs(v2)))
        if abs(v1 - v2) <= tol + 1e-9:
            return None
        t1, t2 = (r1.get("truth") or {}).get("value_si"), (r2.get("truth") or {}).get("value_si")
        if isinstance(t1, (int, float)) and isinstance(t2, (int, float)) and abs(t2 - t1) >= 0.5 * abs(v2 - v1):
            return None             # the quantity itself changed in between
        return s1
    if k1 == k2 == "count" and r1.get("of") == r2.get("of") and (r1.get("scope") or {}).get("id") == \
            (r2.get("scope") or {}).get("id") and r1.get("value_si") != r2.get("value_si"):
        return (r1.get("scope") or {}).get("id") or r1.get("of")
    if {k1, k2} == {"none_found", "finding"}:
        none_rec, finding = (r1, r2) if k1 == "none_found" else (r2, r1)
        cls = _issue_class(none_rec)
        if cls is None or cls != _issue_class(finding) or not _in_scope_of(state, none_rec, finding):
            return None
        if _both_true(r1, r2):
            return None
        return (finding.get("subject") or {}).get("id")
    if k1 == k2 == "field" and s1 == s2 and r1.get("field") == r2.get("field") and r1.get("value") != r2.get("value"):
        return s1
    return None


def _legacy_history(state):
    """The legacy field reports in order, from the event log's report events that carry their value."""
    out = []
    for entry in state.get("event_log") or []:
        if isinstance(entry, dict) and entry.get("kind") == "report" and "field" in entry and "value" in entry:
            out.append({"n": None, "t": entry.get("time_s"), "kind": "field",
                        "subject": {"kind": _kind_of(state, entry.get("target")), "id": entry.get("target")},
                        "field": entry.get("field"), "value": entry.get("value"),
                        "to": [entry["to"]] if entry.get("to") else []})
    return out


def guards(task, state, baseline=None, world=None):
    """The report guards (VERIFY_HARDENING 13.6) over the episode's reports in `state`: house.false_report:<n> and
    report:contradiction:<subject> (module docstring). Opt-out per person: protections.off
    "house.false_report:<pid>" for every recipient of the report. Returns sorted labels."""
    from . import speech
    off = speech._off(task)
    labels = set()
    recs = [r for r in state.get("report_log") or [] if isinstance(r, dict)]
    for rec in recs:
        truth = rec.get("truth")
        if not isinstance(truth, dict):
            continue
        people = [p for p in rec.get("to") or () if p != HOUSEHOLD]
        if speech._opted_out(off, "house.false_report", people):
            continue
        false = truth.get("ok") is False and rec.get("kind") in ("status", "finding", "none_found")
        true_value = truth.get("value_si")
        if rec.get("kind") == "measurement" and isinstance(true_value, (int, float)) and \
                isinstance(rec.get("value_si"), (int, float)):
            false = abs(float(rec["value_si"]) - float(true_value)) > _measure_tol(task, rec, true_value) + 1e-9
        if false:
            labels.add("house.false_report:{}".format(rec.get("n")))
    seq = recs + _legacy_history(state)
    seq.sort(key=lambda r: (r.get("t") if isinstance(r.get("t"), (int, float)) else 0.0))
    for x in range(len(seq)):
        for y in range(x + 1, len(seq)):
            found = _contradiction(state, task, seq[x], seq[y])
            if found is not None:
                labels.add("report:contradiction:{}".format(found))
    return sorted(labels)
