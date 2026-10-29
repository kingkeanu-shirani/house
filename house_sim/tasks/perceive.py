"""Perception evidence: what the robot perceived, when, and whether a report, an act, a decision or a statement rests
on it (docs/design/VERIFY_HARDENING.md 15.2-15.3, WP7).

    enable(env)                                  record what the robot perceives from here on (verification, eval)
    index(env)                                   the episode's time-aware evidence index (Index)
    report_gaps(env, task, answers=None, ...)    scored reports not founded on evidence perceived before them (15.2)
    act_gaps(env, task, answers, ...)            the reference rule: decisive acts before their evidence (15.3)
    claim_founded(env, claim, ...)               a factual claim's subject field perceived before it was said
    agent_labels(env, task, answers, ...)        the agent rule's failure labels, perceive:<kind>[:<id>]

The evidence (kernel/realism.py, outside world state): the recorded observations and action messages
(realism.observed, an ObservationLog: each item stamped with its time and step, and for an observation the levels of
the robot's knowledge of each listed thing and the true values of the fields shown), the grant log (every grant a
successful step applied, with the true values then), the act log (per action, taken before it ran: what it named and
what the robot knew of each named thing), perceived notices and heard messages. Evidence of step k supports an act or
a report of a later step only (the observation at the end of step k is what the agent saw before acting in step k+1).

An observation (e, f, t_o, v_o, level) supports a reported value v_r at t_r when the level reaches the field's tier,
it came before the report, |v_r - v_o| is within max(instrument precision, the predicate's tolerance), and the true
value changed by no more than that tolerance between t_o and t_r -- unless the predicate declares
"as_of": "observed" (the truth is compared at the supporting observation's time). Derived and aggregate answers
(answers.<a>.derive) follow house_sim/data/verify/derivations.json: every input perceived, still valid, and the value
computed from what was perceived within the propagated tolerance of the report; derivations compose.

Decisive acts (15.3): for an entity or set answer, an EFFECT or DEPOSIT command (VERIFY_HARDENING 10.1 command
classes; house_sim/data/verify/actions.json when WP6 has written it) or a report whose target, destination or subject
is a member of the answer's group; for a branch answer, every command of the branch diff set (the caller's, 10.3 B8;
without it every EFFECT, DEPOSIT and report command, the strict default) and, for an agent (agent_labels), every
command that does what an effectful one of them does whatever its verb (puts the thing at the same destination, or
the same kind of effect on the same target and destination: "stack" for "place"); for speech, every factual claim
with a truth mapping (facts.speech_log, WP4). A never-tier fact (an owner) is founded only by a person's own words
(Index.told): never by the robot's own utterances or by a reply quoting them back. An act is founded when the robot perceived the answer's evidence at its tier before it
-- for an entity answer, of the member acted on or, by elimination, of every other member -- and it did not act on a
look-alike or unfamiliar thing it could not tell apart.

Everything here reads hidden state and runs after (or beside) an episode; nothing writes world state.
"""
import copy
import json
import math
import os
import re
from collections import namedtuple

from ..kernel.realism import GLANCE, INSPECT, MEASURE, READ, TOUCH, ObservationLog

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "verify")
DERIVATIONS = os.path.join(DATA, "derivations.json")
ACTION_CLASSES = os.path.join(DATA, "actions.json")

# VERIFY_HARDENING 10.1 command classes (house_sim/data/verify/actions.json, WP6, replaces these once it exists)
PERCEPTION = ("inspect", "look", "peek", "read", "feel", "measure", "listen", "smell", "face")
NAVIGATION = ("go_to", "approach", "turn", "crouch", "kneel", "stand")
COMM = ("speak", "announce", "confirm", "explain", "refuse", "handoff", "alert", "report", "ask", "call", "text",
        "write_note")
DEPOSIT = ("put", "place", "hang", "insert", "load", "empty_into", "dump", "pour", "throw", "dispose", "drop", "store")
WAIT = ("wait",)
REPORT_ACTIONS = ("report",)
# factual claim kinds that need evidence: speech.FACTUAL (WP4-fix added "location", review A-gaming-9; without it a
# guessed "your keys are in the drawer" was founded and could satisfy a said assert: P2-S2 merge gate)
FACTUAL_KINDS = ("state", "issue", "none_found", "quantity", "location")
TIER_LEVELS = {"glance": GLANCE, "inspect": INSPECT, "touch": TOUCH, "read": READ, "measure": MEASURE}
IDENTITY_TIERS = ("recognize", "identify")
UNIDENTIFIED_LABELS = ("dark shape", "object", "unknown object", "person", "animal")

Obs = namedtuple("Obs", "t step level shown truth source")
View = namedtuple("View", "t step identified label")


class DerivationError(ValueError):
    """An answer's derive spec outside the closed table of derivations.json, or malformed."""


_TABLES = {}


def tables():
    """derivations.json (read once)."""
    found = _TABLES.get("derivations")
    if found is None:
        with open(DERIVATIONS, encoding="utf-8") as fh:
            found = _TABLES["derivations"] = json.load(fh)
    return found


def command_class(action):
    """PERCEPTION, NAVIGATION, COMM, DEPOSIT, WAIT or EFFECT for an action name (10.1): from
    house_sim/data/verify/actions.json when it exists ({"classes": {name: class}} or {class: [names]}), else the
    lists of 10.1."""
    found = _TABLES.get("actions", False)
    if found is False:
        found = None
        if os.path.exists(ACTION_CLASSES):
            try:
                with open(ACTION_CLASSES, encoding="utf-8") as fh:
                    raw = json.load(fh)
            except (OSError, ValueError):
                raw = None
            if isinstance(raw, dict):
                table = raw.get("classes") if isinstance(raw.get("classes"), dict) else None
                if table is None:
                    table = {}
                    for cls, names in raw.items():
                        if isinstance(names, list) and cls.upper() == cls:
                            table.update({str(n): cls for n in names})
                found = {str(k): str(v).upper() for k, v in table.items()} or None
        _TABLES["actions"] = found
    if found is not None and action in found:
        return found[action]
    for name, members in (("PERCEPTION", PERCEPTION), ("NAVIGATION", NAVIGATION), ("COMM", COMM),
                          ("DEPOSIT", DEPOSIT), ("WAIT", WAIT)):
        if action in members:
            return name
    return "EFFECT"


def tier_level(tier):
    """The knowledge level an evidence tier word needs (derivations.json "tiers"; a realism level name or number)."""
    if isinstance(tier, int) and not isinstance(tier, bool):
        return tier
    table = tables()["tiers"]
    word = str(tier or "glance").strip().lower()
    if word in table and isinstance(table[word], int):
        return table[word]
    if word in TIER_LEVELS:
        return TIER_LEVELS[word]
    raise DerivationError("unknown evidence tier {!r}".format(tier))


def precision(field):
    """The instrument precision of a perceived number of `field` (derivations.json "precision")."""
    table = tables()["precision"]
    if field in table and field != "about":
        return float(table[field])
    for key, value in table.items():
        if key.startswith("*") and field.endswith(key[1:]):
            return float(value)
    return float(table.get("default", 0.006))


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    out = float(value)
    return out if math.isfinite(out) else None


def _same(a, b, tol=0.0):
    """Equal values: numbers within tol, strings case-insensitively, lists element by element."""
    na, nb = _num(a), _num(b)
    if na is not None and nb is not None:
        return abs(na - nb) <= tol + 1e-9
    if isinstance(a, str) and isinstance(b, str):
        return a.strip().lower() == b.strip().lower()
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y, tol) for x, y in zip(a, b))
    return a == b


def _drift_ok(then, now, tol=None):
    """The true value is still what was perceived. tol None: the knowledge rule (a number within max(0.5, 2 %) of
    what was perceived, derivations.json "drift"; evidence gating of acts); else the predicate's tolerance (reports:
    the truth changed by at most tol between the observation and the report). An unknown truth then is accepted."""
    if then is None:
        return True
    nt, nn = _num(then), _num(now)
    if nt is not None and nn is not None:
        if tol is None:
            rule = tables()["drift"]
            slack = max(float(rule["absolute"]), float(rule["relative"]) * abs(nt))
        else:
            slack = float(tol)
        return abs(nn - nt) <= slack + 1e-9
    return _same(then, now)


# ----- what people told the robot ---------------------------------------------------------------------------------

_WORD = re.compile(r"[a-z0-9_]+")
ECHO_WORDS = 3          # this many of the robot's own words in a row, repeated by a person, are its words quoted back
# the fields of a person's answer object that carry what they answered (dialogue.answer, core legacy_answer); the
# others carry the question back (about, aspect, detail, key, topic, options of a clarify-back) or who answered (by)
REPLY_KEYS = ("value", "said", "picked", "answer", "reply", "refers_to")


def _words(text):
    """A text's words as told() compares them: lower case, a possessive 's dropped ("Sam's" is "sam")."""
    text = str(text).lower().replace("’", "'")
    return _WORD.findall(re.sub(r"'s\b", "", text))


def _runs(words, want):
    """Start positions where the word list `want` occurs in `words` (None entries, left-out words, never match)."""
    n = len(want)
    return [i for i in range(len(words) - n + 1) if n and words[i:i + n] == want]


def _unechoed(words, spoken, offered):
    """A person's words with what repeats the robot's own words left out (None): every stretch of ECHO_WORDS or more
    words in a row found in one of the robot's utterances or arguments (`spoken`), and every mention of the candidates
    of one list the robot offered (`offered`) when the person names two or more of them (that singles none out)."""
    out = list(words)
    grams = set()
    for said in spoken:
        for i in range(len(said) - ECHO_WORDS + 1):
            grams.add(tuple(said[i:i + ECHO_WORDS]))
    for i in range(len(words) - ECHO_WORDS + 1):
        if tuple(words[i:i + ECHO_WORDS]) in grams:
            out[i:i + ECHO_WORDS] = [None] * ECHO_WORDS
    for items in offered:
        hits = [(i, len(item)) for item in items for i in _runs(words, item)]
        if len({tuple(words[i:i + n]) for i, n in hits}) >= 2:
            for i, n in hits:
                out[i:i + n] = [None] * n
    return out


def _leaves(value, out, depth=0):
    """The scalar leaves (strings, numbers) of a JSON value."""
    if depth > 6 or value is None or isinstance(value, bool):
        return
    if isinstance(value, (str, int, float)):
        out.append(str(value))
    elif isinstance(value, dict):
        for key in sorted(value):
            _leaves(value[key], out, depth + 1)
    elif isinstance(value, (list, tuple)):
        for v in value[:64]:
            _leaves(v, out, depth + 1)


def _reply_statements(text):
    """What a person answered in a COMM command's message: the REPLY_KEYS fields of a JSON answer object that names
    who answered ("by": a person, not the robot), one statement per scalar leaf. Nothing for any other message: a
    speech command's message is the robot's own words, a report's or a call's a receipt."""
    stripped = (text or "").strip()
    if stripped[:1] not in ("{", "["):
        return []
    try:
        data = json.loads(stripped)
    except ValueError:
        return []
    out = []
    for item in (data if isinstance(data, list) else [data])[:16]:
        by = item.get("by") if isinstance(item, dict) else None
        if not isinstance(by, str) or not by.strip() or by.startswith("robot"):
            continue
        for key in REPLY_KEYS:
            _leaves(item.get(key), out)
    return out


# ----- the index -------------------------------------------------------------------------------------------------

class Index:
    """The time-aware evidence index of one episode (15.2). Built by index(env); read-only.

    fields[(id, field)]  [Obs(t, step, level, shown, truth, source)] in time order (source: "obs" an observation
                         listed it, "grant" a step let the robot learn it, "reading" a structured instrument result)
    views[id]            [View(t, step, identified, label)]: every observation that listed the thing
    agents[id]           [(t, step, shown record)]: people and pets observed
    rooms                [(t, step, room id)]: the room of every observation
    readings             [{"t", "step", "subject", "quantity", "value", "unit", "value_si"}] from action messages
    notices              [{"t", "step", "kind", "source"}] perceived (obs.notices)
    heard                [{"t", "step", "speaker", "text", "claims"}] what people told the robot (obs.messages)
    messages             [(t, step, text)]: the action messages (what each command answered)
    acts                 realism's act log
    """

    def __init__(self):
        self.fields = {}
        self.views = {}
        self.agents = {}
        self.rooms = []
        self.readings = []
        self.notices = []
        self.heard = []
        self.acts = []
        self.messages = []
        self.last_step = 0

    # ----- building
    def add(self, eid, field, obs):
        self.fields.setdefault((eid, field), []).append(obs)

    def finish(self):
        for key in self.fields:
            self.fields[key].sort(key=lambda o: (o.step, o.t))
        for key in self.views:
            self.views[key].sort(key=lambda v: (v.step, v.t))

    # ----- queries
    def entries(self, eid, field, before_step=None, level=GLANCE):
        """Observations of (eid, field) at `level` or better, from steps before `before_step` (None: any)."""
        return [o for o in self.fields.get((eid, field), ())
                if o.level >= level and (before_step is None or o.step < before_step)]

    def seen(self, eid, before_step=None, identified=False):
        """Whether an observation listed eid (identified=True: with its own identity, at recognize or better)."""
        return any((before_step is None or v.step < before_step) and (v.identified or not identified)
                   for v in self.views.get(eid, ()))

    def told(self, value, before_step, subject=None):
        """Whether a person told the robot `value` before the step -- how a never-tier fact (an owner, an intent) is
        learned: it appears as a word or phrase in a person's own words, which are what someone said to the robot (a
        heard message) and the answer fields of a person's reply to one of its COMM commands (_reply_statements: an
        ask's value / said / picked, never the fields carrying the question back, nor who answered). The robot's own
        words never tell it anything: a speech command's message (its own utterance echoed), a report's or a call's
        receipt are not a person's words, and in a person's words a stretch repeating ECHO_WORDS or more of the
        robot's own words in a row, or naming two or more of the candidates it offered in one list (ask's options),
        is its words quoted back and founds nothing (review A-gaming-1). subject: the thing the fact is about -- a
        reply to a question about another thing (it named other things, not this one) does not tell it."""
        want = _words(value)
        if not want:
            return False
        for step, text in self.person_statements(before_step, subject):
            spoken, offered = self._robot_words(step)
            if _runs(_unechoed(_words(text), spoken, offered), want):
                return True
        return False

    def person_statements(self, before_step, subject=None):
        """[(step, text)] of what people said to the robot before the step: heard messages and the answer fields of
        the replies to its COMM commands (with a subject: not those of a question naming other things only), in step
        order."""
        out = [(m["step"], m.get("text") or "") for m in self.heard if m["step"] < before_step]
        comm = {a.get("step") for a in self.acts if a.get("ok") and command_class(str(a.get("action") or "")) == "COMM"
                and (subject is None or not a.get("ids") or subject in a["ids"])}
        for _t, step, text in self.messages:
            if step < before_step and step in comm:
                out.extend((step, leaf) for leaf in _reply_statements(text))
        return sorted(out, key=lambda item: item[0])

    def _robot_words(self, step):
        """What the robot itself said or offered by its commands up to and including `step`: (word lists of every
        string argument, candidate lists: list arguments of two or more strings, each a list of word lists)."""
        spoken, offered = [], []
        for act in self.acts:
            if int(act.get("step", 0) or 0) > step:
                continue
            for value in (act.get("args") or {}).values():
                if isinstance(value, str):
                    spoken.append(_words(value))
                elif isinstance(value, (list, tuple)):
                    items = [_words(v) for v in value[:64] if isinstance(v, str)]
                    spoken.extend(items)
                    items = [w for w in items if w]
                    if len(items) >= 2:
                        offered.append(items)
        return spoken, offered

    def act_at(self, step):
        return next((a for a in self.acts if a.get("step") == step), None)

    def step_of_time(self, t, action=None):
        """The step whose action began at time t: the `action` command that began then, else the last act at or before
        it, else the last step + 1."""
        found = None
        for act in self.acts:
            at = float(act.get("t", 0.0))
            if action is not None and act.get("action") == action and abs(at - float(t)) <= 1e-6:
                return act.get("step")
            if at <= float(t) + 1e-9:
                found = act.get("step")
        return found if found is not None else self.last_step + 1


def enable(env):
    """Record what the robot perceives from here on: realism's observation log on, and the observation an agent
    sees now recorded as the first one (observing changes no state). No-op without realistic perception."""
    realism = getattr(env, "realism", None)
    if realism is None:
        return False
    if not isinstance(realism.observed, ObservationLog):
        realism.observed = ObservationLog(realism)
    realism.record_observations = True
    env.observe()
    return True


def _identified(shown):
    """An observed record shows the thing with its own identity (recognize or better): not a depth shape, a bare
    detection, an unfamiliar thing or an unresolved look-alike."""
    if not isinstance(shown, dict):
        return False
    if shown.get("candidates") or str(shown.get("kind") or "") in ("unknown",) or \
            str(shown.get("kind") or "").startswith("unidentified"):
        return False
    return str(shown.get("label") or "").strip().lower() not in UNIDENTIFIED_LABELS


def _walk_readings(value, out, depth=0):
    if depth > 5:
        return
    if isinstance(value, dict):
        if "quantity" in value and _num(value.get("value")) is not None:
            out.append(value)
        for v in value.values():
            _walk_readings(v, out, depth + 1)
    elif isinstance(value, list):
        for v in value[:64]:
            _walk_readings(v, out, depth + 1)


def _parse_claims(text, env=None, room=None):
    """What a person's message to the robot claims: WP4's speech.parse (house_sim/kernel/speech.py; subjects resolved
    against the house). WP4 has landed: imported directly, called with its real signature (P2-S2 merge gate)."""
    from ..kernel import speech
    try:
        state = env.world.s if env is not None else None
        found = speech.parse(text, state=state, env=env, room=room)
    except Exception:  # noqa: BLE001  (a heard message the parser cannot read asserts nothing)
        return []
    return list(found or ()) if isinstance(found, (list, tuple)) else []


def index(env):
    """The evidence index of env's episode so far (Index). Reads realism's logs; empty without realistic perception.
    The observation part needs recording on from the start (enable(env); the verifier sets record_observations)."""
    out = Index()
    realism = getattr(env, "realism", None)
    if realism is None:
        return out
    settle = getattr(realism, "settle_moves", None)
    if callable(settle):
        settle()
    state = env.world.s
    out.last_step = int(state.get("steps", 0) or 0)
    log = realism.observed
    stamps = getattr(log, "stamps", None) or []
    evidence = getattr(log, "evidence", None) or []
    last = (0.0, 0)
    for i, item in enumerate(log):
        stamp = stamps[i] if i < len(stamps) else None
        if stamp is None:
            t = item.get("time_s") if isinstance(item, dict) and _num(item.get("time_s")) is not None else last[0]
            stamp = (float(t), last[1])
        last = stamp
        t, step = stamp
        snap = evidence[i] if i < len(evidence) and isinstance(evidence[i], dict) else {}
        item = realism.from_handles(item)
        if not isinstance(item, dict):
            continue
        if "visible_entities" in item:
            _index_observation(out, item, snap, t, step, env)
        elif isinstance(item.get("message"), str):
            _index_message(out, item["message"], t, step)
    for grant in getattr(realism, "grants_log", None) or ():
        for field, value in sorted((grant.get("values") or {}).items()):
            out.add(grant["id"], field, Obs(float(grant["t"]), int(grant["step"]), int(grant["level"]), None, value,
                                            "grant"))
    out.acts = list(getattr(realism, "acts", None) or ())
    out.finish()
    return out


build_index = index       # the builder under a name the functions' own "index" arguments do not shadow


def _index_observation(out, item, snap, t, step, env=None):
    rid = item.get("room_id")
    if isinstance(rid, str):
        out.rooms.append((t, step, rid))
    for eid, shown in sorted((item.get("visible_entities") or {}).items()):
        if not isinstance(shown, dict):
            continue
        known = snap.get(eid) or {}
        levels, truth = known.get("levels") or {}, known.get("truth") or {}
        out.views.setdefault(eid, []).append(View(t, step, _identified(shown), shown.get("label")))
        for field, value in shown.items():
            if field in ("view",):
                continue
            out.add(eid, field, Obs(t, step, int(levels.get(field, GLANCE)), value, truth.get(field), "obs"))
    for group in ("people", "pets"):
        for aid, shown in sorted((item.get(group) or {}).items()):
            if isinstance(shown, dict):
                out.agents.setdefault(aid, []).append((t, step, shown))
    for notice in item.get("notices") or ():
        if isinstance(notice, dict):
            out.notices.append({"t": float(notice.get("t", t) or t), "step": step, "kind": notice.get("kind"),
                                "source": notice.get("source")})
    for message in item.get("messages") or ():
        if isinstance(message, dict) and isinstance(message.get("text"), str) and message.get("speaker") != "robot":
            out.heard.append({"t": t, "step": step, "speaker": message.get("speaker"), "text": message["text"],
                              "claims": _parse_claims(message["text"], env, item.get("room_id"))})


def _index_message(out, text, t, step):
    out.messages.append((t, step, text))
    stripped = text.strip()
    if stripped[:1] not in ("{", "["):
        return
    try:
        data = json.loads(stripped)
    except ValueError:
        return
    found = []
    _walk_readings(data, found)
    from ..kernel import reports
    for rec in found:
        quantity, unit, value = rec.get("quantity"), rec.get("unit"), _num(rec.get("value"))
        value_si = None
        try:
            q, u = reports.unit_of(unit, quantity) if unit else (reports.quantity_of(quantity), None)
            if q is not None and u is not None:
                value_si = reports.convert(value, u, q)
            quantity = q or quantity
        except (ValueError, TypeError, KeyError):
            value_si = None
        subject = rec.get("subject")
        if isinstance(subject, dict):
            subject = subject.get("id")
        reading = {"t": t, "step": step, "subject": subject, "quantity": quantity, "value": value, "unit": unit,
                   "value_si": value_si}
        out.readings.append(reading)
        field = rec.get("field")
        if isinstance(subject, str) and isinstance(field, str):
            out.add(subject, field, Obs(t, step, MEASURE, value, None, "reading"))


# ----- support of one value ---------------------------------------------------------------------------------------

def _truth_at(idx, env, eid, field, step):
    """The true value of eid.field when the action of `step` began (the act log's record of a named thing), else the
    latest truth the index holds before it, else the current value."""
    act = idx.act_at(step) if step is not None else None
    if act is not None:
        values = ((act.get("targets") or {}).get(eid) or {}).get("values") or {}
        if field in values:
            return values[field]
    best = None
    for o in idx.fields.get((eid, field), ()):
        if o.truth is not None and (step is None or o.step < step):
            best = o.truth
    if best is not None:
        return best
    record = env.world.s.get("entities", {}).get(eid)
    if record is None:
        return None
    from ..kernel.realism import _evidence_value, _field_value
    return _evidence_value(_field_value(record, field))


def _field_tier(env, eid, field):
    realism = getattr(env, "realism", None)
    record = env.world.s.get("entities", {}).get(eid)
    if realism is None or record is None:
        return GLANCE
    from ..kernel.realism import TIER_NEED
    tier = realism.tier(record, field.split(".")[0])
    return TIER_NEED.get(tier, 99) if tier != "never" else 99


def supports(idx, env, eid, field, value, before_step, tol=0.0, as_of=None, need=None):
    """The observation that founds reporting `value` for eid.field before step `before_step` (15.2), or None. need: the
    level required (default: the field's own tier)."""
    need = _field_tier(env, eid, field) if need is None else need
    if need >= 99:
        # a never-tier fact (who owns it, what is it for) is not perceived: it is learned by being told (a person's own
        # words, never the robot's: Index.told)
        if value is not None and not isinstance(value, (dict, list)) and idx.told(value, before_step, subject=eid):
            return Obs(0.0, -1, GLANCE, value, None, "told")
        return None
    slack = max(precision(field), float(tol or 0.0)) if _num(value) is not None else float(tol or 0.0)
    now = _truth_at(idx, env, eid, field, before_step)
    best = None
    for o in idx.entries(eid, field, before_step, need):
        seen = _seen_value(o)
        if seen is None or not _same(value, seen, slack):
            continue
        if as_of != "observed" and not _drift_ok(o.truth, now, float(tol or 0.0)):
            continue
        best = o
    if best is None and _num(value) is not None:
        for r in idx.readings:
            if r.get("subject") == eid and r["step"] < before_step and r.get("value_si") is not None and \
                    _quantity_reads(r.get("quantity"), field) is not None:
                factor = _quantity_reads(r.get("quantity"), field)
                if _same(float(value), r["value_si"] / factor, slack):
                    return Obs(r["t"], r["step"], MEASURE, r["value"], None, "reading")
    return best


def _quantity_reads(quantity, field):
    """The factor from field's unit to quantity's canonical unit when a `quantity` reading reads `field`, else None."""
    for name, factor in tables()["quantity_fields"].get(str(quantity), ()) if quantity else ():
        if name == field or (name.startswith("*") and field.endswith(name[1:])):
            return float(factor)
    return None


def _perceived(idx, env, eid, field, need, before_step, act=None):
    """eid.field was perceived at level `need` before step `before_step` and is still what was perceived: the act's
    own record of what the robot knew then (a grant still holding), else an index observation whose truth has not
    changed since (glance-tier fields: shown in an observation)."""
    if act is not None:
        target = (act.get("targets") or {}).get(eid) or {}
        if int((target.get("fields") or {}).get(field, -1)) >= need and need > GLANCE:
            return True
    now = _truth_at(idx, env, eid, field, before_step)
    for o in reversed(idx.entries(eid, field, before_step, need)):
        if _drift_ok(o.truth, now):
            return True
    return False


# ----- derivations ------------------------------------------------------------------------------------------------

def _members(spec):
    over = spec.get("over")
    if isinstance(over, str):
        return [over]
    return [m for m in (over or []) if isinstance(m, str)]


def _input_parts(item):
    if isinstance(item, dict):
        if "derive" in item:
            return None, None, item
        return item.get("entity") or item.get("id"), item.get("field"), None
    if isinstance(item, str) and "." in item:
        eid, field = item.split(".", 1)
        return eid, field, None
    raise DerivationError("derive input {!r} is not entity.field or a derive spec".format(item))


def derive_support(idx, env, spec, value, before_step, tol=0.0, as_of=None):
    """(founded, reason) for a derived value (answers.<a>.derive; derivations.json), reported as `value` before step
    `before_step`. Raises DerivationError for an op outside the table."""
    found = _derived(idx, env, spec, before_step, as_of)
    if not found["ok"]:
        return False, found["why"]
    est, slack = found["value"], max(float(tol or 0.0), found["tol"])
    rule = tables()["derivations"][spec["op"]]
    if rule["tolerance"] == "digit":
        slack = max(float(tol or 0.0), _half_digit(value))
    if value is None or est is None:
        return True, ""
    if isinstance(est, (list, set, tuple, frozenset)) and not isinstance(value, (list, tuple)):
        return (value in est, "" if value in est else "the perceived inputs give {!r}".format(sorted(est)))
    if not _same(value, est, slack):
        return False, "the perceived inputs give {!r}, not {!r} (tolerance {})".format(est, value, round(slack, 6))
    return True, ""


def _half_digit(value):
    n = _num(value)
    if n is None:
        return 0.0
    text = repr(float(n)) if isinstance(value, float) else str(value)
    decimals = len(text.split(".", 1)[1].rstrip("0")) if "." in text and "e" not in text else 0
    return 0.5 * 10 ** -decimals


def _seen_value(o):
    """What an observation let the robot know: the value shown (a bucket word stays a word), or, for a grant or a
    reading that shows nothing itself, the true value it taught."""
    return o.shown if o.shown is not None else o.truth


def _observed_value(idx, env, eid, field, before_step, as_of=None, numeric=False):
    """(value, precision, Obs) of the last observation of eid.field at its tier before the step that is still valid
    (numeric: the last that showed a number -- a glance bucket founds no number)."""
    need = _field_tier(env, eid, field)
    if need >= 99:
        return None, 0.0, None
    now = _truth_at(idx, env, eid, field, before_step)
    for o in reversed(idx.entries(eid, field, before_step, need)):
        if as_of != "observed" and not _drift_ok(o.truth, now):
            continue
        value = _seen_value(o)
        if numeric and _num(value) is None:
            continue
        return value, precision(field), o
    return None, 0.0, None


def _derived(idx, env, spec, before_step, as_of=None):
    if not isinstance(spec, dict) or "op" not in spec:
        raise DerivationError("derive needs an op")
    op = spec["op"]
    rule = tables()["derivations"].get(op)
    if rule is None:
        raise DerivationError("derive op {!r} is not in derivations.json".format(op))
    if rule["inputs"] == "over":
        members = _members(spec)
        field = spec.get("field")
        if not members:
            return {"ok": False, "why": "no members to {}".format(op)}
        if rule["support"] == "identified":
            missing = [m for m in members if not idx.seen(m, before_step, identified=True)]
            if missing:
                return {"ok": False, "why": "not seen identified before the report: {}".format(", ".join(missing[:5]))}
            return {"ok": True, "value": float(len(members)), "tol": 0.0}
        if not field:
            raise DerivationError("derive {} over members needs a field".format(op))
        values, precs = [], []
        for m in members:
            v, p, o = _observed_value(idx, env, m, field, before_step, as_of,
                                      numeric=rule["value"] not in ("any", "all", "none"))
            if o is None:
                return {"ok": False, "why": "{}.{} not perceived at its tier before the report".format(m, field)}
            values.append(v)
            precs.append(p)
        nums = [_num(v) for v in values]
        if rule["value"] in ("any", "all", "none"):
            return {"ok": True, "value": None, "tol": 0.0}
        if any(n is None for n in nums):
            return {"ok": False, "why": "{} over non-numeric perceived values".format(op)}
        if rule["value"] == "sum":
            return {"ok": True, "value": sum(nums), "tol": sum(precs)}
        if rule["value"] == "mean":
            return {"ok": True, "value": sum(nums) / len(nums), "tol": sum(precs) / len(precs)}
        order = sorted(range(len(nums)), key=lambda i: nums[i], reverse=rule["value"] in ("max", "argmax"))
        if len(order) >= 2 and abs(nums[order[0]] - nums[order[1]]) <= precs[order[0]] + precs[order[1]]:
            return {"ok": False, "why": "the perceived values do not separate {} from {}".format(
                members[order[0]], members[order[1]])}
        win = order[0]
        if rule["value"] in ("argmin", "argmax"):
            return {"ok": True, "value": {members[win]}, "tol": 0.0}
        return {"ok": True, "value": nums[win], "tol": precs[win]}
    items = spec.get("of")
    if not isinstance(items, list) or len(items) != int(rule.get("arity", len(items or []))):
        raise DerivationError("derive {} needs {} input(s) in of".format(op, rule.get("arity")))
    parts = []
    for item in items:
        if isinstance(item, dict) and item.get("notice"):
            # a perceived notice (an alarm starting, a door slamming): its time is the input
            heard = [n for n in idx.notices if n.get("kind") == item["notice"] and n["step"] < before_step]
            if not heard:
                return {"ok": False, "why": "no {} notice perceived before the report".format(item["notice"])}
            n = heard[0] if not parts else heard[-1]
            parts.append((n["t"], 1.0, Obs(n["t"], n["step"], GLANCE, None, None, "notice")))
            continue
        eid, field, nested = _input_parts(item)
        if nested is not None:
            sub = _derived(idx, env, nested["derive"], before_step, as_of)
            if not sub["ok"]:
                return sub
            parts.append((sub["value"], sub["tol"], None))
            continue
        v, p, o = _observed_value(idx, env, eid, field, before_step, as_of, numeric=rule["value"] != "duration")
        if o is None:
            return {"ok": False, "why": "{}.{} not perceived at its tier before the report".format(eid, field)}
        parts.append((v, p, o))
    if rule["value"] == "convert":
        v, p, _o = parts[0]
        n = _num(v)
        if n is None:
            return {"ok": False, "why": "convert of a non-numeric perceived value"}
        return {"ok": True, "value": _convert(n, spec), "tol": 0.0}
    (a, pa, oa), (b, pb, ob) = parts
    if rule["support"] in ("timed", "timed_or_notices"):
        if oa is not None and ob is not None and abs(oa.t - ob.t) < 1e-9:
            # a rate or duration needs two observations at different times
            alt = _second_time(idx, env, items, before_step)
            if alt is None:
                return {"ok": False, "why": "{} needs two observations at different times".format(op)}
            (a, pa, oa), (b, pb, ob) = alt
    na, nb = _num(a), _num(b)
    if rule["value"] == "duration":
        if oa is None or ob is None:
            return {"ok": True, "value": None, "tol": 0.0}
        return {"ok": True, "value": abs(ob.t - oa.t), "tol": 1.0}
    if na is None or nb is None:
        return {"ok": False, "why": "{} of non-numeric perceived values".format(op)}
    if rule["value"] == "diff":
        return {"ok": True, "value": na - nb, "tol": pa + pb}
    dt = abs(ob.t - oa.t) if oa is not None and ob is not None else 0.0
    if dt <= 0.0:
        return {"ok": False, "why": "rate needs two timed observations"}
    return {"ok": True, "value": (nb - na) / (ob.t - oa.t), "tol": (pa + pb) / dt}


def _second_time(idx, env, items, before_step):
    """For a rate or duration over one field read twice: its first and last valid observations."""
    eid, field, nested = _input_parts(items[0])
    if nested is not None or not eid:
        return None
    need = _field_tier(env, eid, field)
    found = idx.entries(eid, field, before_step, need)
    if len(found) < 2 or abs(found[-1].t - found[0].t) < 1e-9:
        return None
    first, last = found[0], found[-1]

    return (_seen_value(first), precision(field), first), (_seen_value(last), precision(field), last)


def _convert(value, spec):
    """value (in the input field's unit, canonical for its quantity) in spec["to_unit"]."""
    from ..kernel import reports
    unit = spec.get("to_unit")
    quantity = spec.get("quantity")
    q, u = reports.unit_of(unit, quantity)
    if q is None:
        raise DerivationError("convert to unknown unit {!r}".format(unit))
    return reports.from_canonical(value, u, q)


# ----- answers ----------------------------------------------------------------------------------------------------

def resolve_answers(answers, bindings=None):
    """answers with every "{name}" reference replaced by its binding (oracle bindings), lists of ids kept as lists."""
    bindings = bindings or {}

    def sub(value):
        if isinstance(value, str):
            m = re.match(r"^\{([A-Za-z0-9_]+)\}$", value)
            if m and m.group(1) in bindings:
                return copy.deepcopy(bindings[m.group(1)])
            return re.sub(r"\{([A-Za-z0-9_]+)\}", lambda mm: str(bindings.get(mm.group(1), mm.group(0))), value)
        if isinstance(value, list):
            out = []
            for v in value:
                r = sub(v)
                out.append(r)
            return out
        if isinstance(value, dict):
            return {k: sub(v) for k, v in value.items()}
        return value
    out = {}
    for name, spec in (answers or {}).items():
        if not isinstance(spec, dict):
            continue
        spec = sub(spec)
        if "bind" in spec and "value" not in spec and spec["bind"] in bindings:
            spec["value"] = copy.deepcopy(bindings[spec["bind"]])
        out[name] = spec
    return out


def _group(spec):
    group = spec.get("group")
    if isinstance(group, str):
        return [group]
    return [g for g in (group or []) if isinstance(g, str)]


def _evidence_items(spec):
    evidence = spec.get("evidence")
    if isinstance(evidence, dict):
        return [evidence]
    return [e for e in (evidence or []) if isinstance(e, dict)]


# ----- reports (15.2) ---------------------------------------------------------------------------------------------

def _success_rules(task):
    rules = list(task.get("success") or [])
    for phase in task.get("phases") or []:
        rules.extend(phase.get("success") or [])
    return rules


def _walk_rules(rule, out):
    if not isinstance(rule, dict) or "not" in rule:
        return
    for key in ("all", "any"):
        for sub in rule.get(key) or []:
            _walk_rules(sub, out)
    out.append(rule)


def _answer_for(rule, expected, answers):
    if isinstance(rule.get("answer"), str) and rule["answer"] in answers:
        return answers[rule["answer"]]
    for spec in answers.values():
        if spec.get("derive") and "value" in spec and _same(spec["value"], expected):
            return spec
    return None


def _legacy_report_step(idx, target, field):
    """The step of the last successful legacy report(target, field, value) in the act log."""
    found = None
    for act in idx.acts:
        args = act.get("args") or {}
        if act.get("ok") and act.get("action") in REPORT_ACTIONS and args.get("target") == target and \
                args.get("field") == field:
            found = act.get("step")
    return found


def report_gaps(env, task, answers=None, index=None, bindings=None):
    """The scored reports of env's run not founded on evidence perceived before them (15.2): legacy reports a success
    predicate compares (path reports.<target>.<field>), typed finding / status / measurement / count / none_found
    records a report or report_set predicate scores, each with the drift rule, as_of and the derivations of its
    answer. Returns [{"report", "reason"}]. The reference fails on any (perceivability_gaps)."""
    from ..kernel.predicates import MISSING, get_path
    idx = index if index is not None else build_index(env)
    answers = resolve_answers(answers, bindings) if answers else {}
    state = env.world.s
    gaps, rules = [], []
    for rule in _success_rules(task or {}):
        _walk_rules(rule, rules)
    for rule in rules:
        path = rule.get("path", "")
        if isinstance(path, str) and path.startswith("reports.") and "._to" not in path and not path.endswith("._to"):
            gap = _legacy_gap(idx, env, state, rule, path, answers, get_path, MISSING)
            if gap:
                gaps.append(gap)
    for rec in _scored_records(env, rules):
        gap = _typed_gap(idx, env, rec, answers)
        if gap:
            gaps.append(gap)
    return gaps


def _legacy_gap(idx, env, state, rule, path, answers, get_path, missing):
    parts = path.split(".")
    if len(parts) < 3:
        return None
    target, field = parts[1], ".".join(parts[2:])
    reported = get_path(state, path)
    if reported is missing or reported is None or isinstance(reported, bool):
        return None
    expected = get_path(state, rule["ref"]) if "ref" in rule else rule.get("value", missing)
    tol = float(rule.get("tolerance", rule.get("tol", 0.0)) or 0.0)
    if rule.get("op") == "approx" and isinstance(expected, list) and len(expected) == 2:
        tol = float(expected[1])
    step = _legacy_report_step(idx, target, field)
    step = idx.last_step + 1 if step is None else step
    answer = _answer_for(rule, expected, answers) if expected is not missing else None
    name = "{}.{}".format(target, field)
    if answer is not None and isinstance(answer.get("derive"), dict):
        ok, why = derive_support(idx, env, answer["derive"], reported, step, max(tol, float(answer.get("tol") or 0.0)),
                                 rule.get("as_of"))
        return None if ok else {"report": name, "reason": why}
    if answer is not None and _evidence_items(answer):
        # a word or verdict the robot judged (no field holds it): founded on the answer's declared evidence
        missing = [item for item in _evidence_items(answer) if not _item_perceived(idx, env, item, target, step)]
        if not missing:
            return None
        return {"report": name, "reason": "the evidence {} was not perceived before the report".format(
            ", ".join("{}.{}".format(i.get("entity") or target, i.get("field")) for i in missing))}
    subject = target if target in state.get("entities", {}) else None
    if subject is None:
        return None             # a report about a room, person or the house: recipients and words, not a reading
    if field.split(".")[0] not in state["entities"][subject]:
        return {"report": name, "reason": "{} is not a field of {}: declare the answer's evidence or derive".format(
            field, subject)}
    if supports(idx, env, subject, field, reported, step, tol, rule.get("as_of")) is not None:
        return None
    return {"report": name, "reason": "{}.{} = {!r} not perceived at its tier before the report".format(
        subject, field, reported)}


def _item_perceived(idx, env, item, default_entity, step):
    """An evidence item {"entity", "field", "tier"} (entity default: the report's target) perceived before `step`."""
    eids = item.get("entity") or default_entity
    eids = [eids] if isinstance(eids, str) else [e for e in eids if isinstance(e, str)]
    field = item.get("field")
    need = tier_level(item.get("tier", "inspect"))
    for eid in eids:
        if not field:
            if not idx.seen(eid, step, identified=item.get("tier") in IDENTITY_TIERS):
                return False
        elif not _perceived(idx, env, eid, field, need, step):
            return False
    return True


def _scored_records(env, rules):
    from ..kernel import reports
    from ..kernel.evaluation import evaluation_view
    from ..kernel.predicates import report_bounds
    view = evaluation_view(env.world.s, getattr(env, "_monitor", None), getattr(env, "mind", None))
    out, keys = [], set()
    for rule in rules:
        for key in ("report", "report_set"):
            if not isinstance(rule.get(key), dict):
                continue
            spec = report_bounds(view, rule[key])
            if spec is None:
                continue
            found = reports.matching_records(view, spec) if key == "report" else reports.matching_set_records(view, spec)
            for rec in found:
                ident = (rec.get("n"), (rec.get("subject") or {}).get("id"))
                if ident not in keys and not rec.get("legacy"):
                    keys.add(ident)
                    out.append(dict(rec, _tol=float(spec.get("tol") or 0.0) if isinstance(spec, dict) else 0.0,
                                    _as_of=rule[key].get("as_of"), _rule=rule[key], _spec=spec))
    return out


def _typed_gap(idx, env, rec, answers=None):
    kind = rec.get("kind")
    step = idx.step_of_time(rec.get("t", 0.0), action="report")
    subject = rec.get("subject") or {}
    sid = subject.get("id") if isinstance(subject, dict) else None
    name = "report {}".format(rec.get("n"))
    state = env.world.s
    rule = rec.get("_rule") or {}
    expected = (rec.get("_spec") or {}).get("value", rule.get("value"))
    answer = _answer_for(rule, expected, answers or {}) if expected is not None or rule.get("answer") else None
    if answer is not None and isinstance(answer.get("derive"), dict) and kind in ("measurement", "count"):
        derive = answer["derive"]
        reported = rec.get("value_si")
        if kind == "measurement" and derive.get("op") == "convert":
            reported = rec.get("value") if derive.get("to_unit") in (None, rec.get("unit")) else \
                _convert(float(rec.get("value_si") or 0.0), derive)
        ok, why = derive_support(idx, env, derive, reported, step, max(rec.get("_tol", 0.0),
                                                                       float(answer.get("tol") or 0.0)),
                                 rec.get("_as_of"))
        return None if ok else {"report": name, "reason": why}
    if kind == "measurement":
        fields = [f for f in _subject_fields(state, sid, rec.get("quantity"))]
        value = _num(rec.get("value_si"))
        for field, factor in fields:
            if supports(idx, env, sid, field, value / factor if value is not None else None, step,
                        rec.get("_tol", 0.0) / factor, rec.get("_as_of"), need=MEASURE) is not None:
                return None
        for r in idx.readings:
            if r.get("subject") == sid and r.get("quantity") == rec.get("quantity") and r["step"] < step and \
                    r.get("value_si") is not None and value is not None and \
                    _same(value, r["value_si"], max(rec.get("_tol", 0.0), precision(""))):
                return None
        return {"report": name, "reason": "no {} of {} measured before the report".format(rec.get("quantity"), sid)}
    if kind in ("finding", "status"):
        if _evidence_before(idx, env, rec, sid, step):
            return None
        return {"report": name, "reason": "no evidence of {} {} perceived before the report".format(
            rec.get("issue") or rec.get("state"), sid)}
    if kind == "count":
        members = _count_members(env, rec)
        if members is None:
            return None
        missing = [m for m in members if not idx.seen(m, step, identified=True)]
        if missing:
            return {"report": name, "reason": "counted members not seen before the report: {}".format(
                ", ".join(sorted(missing)[:5]))}
        return None
    if kind == "none_found":
        members = _scope_members(env, rec.get("scope"))
        missing = [m for m in members if not idx.seen(m, step)]
        if missing:
            return {"report": name, "reason": "scope not looked over before the report: {}".format(
                ", ".join(sorted(missing)[:5]))}
    return None


def _subject_fields(state, sid, quantity):
    record = state.get("entities", {}).get(sid) or {}
    out = []
    for name, factor in tables()["quantity_fields"].get(str(quantity), ()):
        if name.startswith("*"):
            out.extend((f, float(factor)) for f in sorted(record) if f.endswith(name[1:]) and not f.startswith("_"))
        elif name in record:
            out.append((name, float(factor)))
    return out


def _evidence_before(idx, env, rec, sid, step, entries=None):
    """An evidence entry of a finding / status (the vocabulary's issue / state evidence) perceived before `step`: a
    field of the subject at its tier, the same field near it (its room), or a notice of the listed kind."""
    from ..kernel import reports
    from ..kernel.predicates import root_room
    state = env.world.s
    entries = reports._evidence_entries(rec) if entries is None else entries
    if not entries:
        return True
    kinds = {n.get("kind") for n in idx.notices if n["step"] < step}
    for entry in entries:
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
            targets = [e for e in sorted(state["entities"]) if root_room(state, e) == room]
        for eid in targets:
            if not isinstance(eid, str) or field not in (state.get("entities", {}).get(eid) or {}):
                continue
            need = _field_tier(env, eid, field)
            if need < 99 and idx.entries(eid, field, step, need):
                return True
    return False


def _count_members(env, rec):
    """The true members a count report of `of` in its scope counts (reports' own matching), or None when the kernel
    cannot list them."""
    from ..kernel import reports
    match = getattr(reports, "count_members", None)
    if callable(match):
        try:
            return list(match(env.world.s, rec))
        except Exception:  # noqa: BLE001
            return None
    of = str(rec.get("of") or "").strip().lower()
    if not of:
        return None
    members = []
    for eid in _scope_members(env, rec.get("scope")):
        record = env.world.s["entities"].get(eid) or {}
        words = {str(record.get(k) or "").lower().replace("_", " ") for k in ("type", "category", "label")}
        if of in words or of.rstrip("s") in words:
            members.append(eid)
    return members


def _scope_members(env, scope):
    from ..kernel.predicates import root_room
    state = env.world.s
    if isinstance(scope, dict):
        sid, skind = scope.get("id"), scope.get("kind")
    else:
        sid, skind = scope, None
    entities = state.get("entities", {})
    if skind == "room" or sid in state.get("rooms", {}):
        return [e for e in sorted(entities) if root_room(state, e) == sid]
    if sid in entities:
        return [e for e in sorted(entities) if entities[e].get("location") == sid]
    return sorted(entities) if sid in (None, "house", "household") else []


# ----- acts (15.3) ------------------------------------------------------------------------------------------------

def _decisive(act):
    if not act.get("ok"):
        return False
    action = str(act.get("action") or "")
    return action in REPORT_ACTIONS or command_class(action) in ("EFFECT", "DEPOSIT")


def _canon(command):
    """(action, sorted (key, value) pairs) of a command dict for comparing with the branch diff set."""
    action = command.get("action")
    args = command.get("args") if isinstance(command.get("args"), dict) else \
        {k: v for k, v in command.items() if k != "action"}
    return action, tuple(sorted((k, json.dumps(v, sort_keys=True, default=str)) for k, v in args.items()
                                if v not in (None, "", [], {})))


def _matches(act, command):
    action, pairs = _canon(command)
    if act.get("action") != action:
        return False
    args = act.get("args") or {}
    return all(json.dumps(args.get(k), sort_keys=True, default=str) == v for k, v in pairs)


ROLE_KEYS = ("target", "destination")


def _role(value):
    return value if isinstance(value, str) and value.strip() else None


def _command_args(command):
    return command.get("args") if isinstance(command.get("args"), dict) else \
        {k: v for k, v in command.items() if k != "action"}


def _effect_kind(action, args):
    """The kind of effect a command has, for matching an agent's act with a branch diff command: "report", "deposit"
    (a DEPOSIT command, or an EFFECT command that names a destination: stack, insert ... onto or into it), "effect"
    (any other EFFECT command), or None (perception, navigation, speech and waiting decide nothing by themselves)."""
    action = str(action or "")
    if action in REPORT_ACTIONS:
        return "report"
    cls = command_class(action)
    if cls == "DEPOSIT" or (cls == "EFFECT" and _role(args.get("destination"))):
        return "deposit"
    return "effect" if cls == "EFFECT" else None


def _does_what(act, command):
    """The agent rule (15.3): the act does what an effectful branch diff command does, whatever its verb -- it put the
    command's thing at the command's destination (the act log's "placed"), or it is the same kind of effect
    (_effect_kind) on the same target and destination with no argument both name set to another value (a common
    set(thermostat, 20) does not stand for a branch's set(thermostat, 22)) (review A-gaming-4: "stack" for "place")."""
    cargs = _command_args(command)
    kind = _effect_kind(command.get("action"), cargs)
    aargs = act.get("args") or {}
    akind = _effect_kind(act.get("action"), aargs)
    if kind is None or akind is None:
        return False
    target, dest = _role(cargs.get("target")), _role(cargs.get("destination"))
    if kind == "deposit" and target and dest and (act.get("placed") or {}).get(target) == dest:
        return True
    if akind != kind or (target is None and dest is None):
        return False
    if target != _role(aargs.get("target")) or dest != _role(aargs.get("destination")):
        return False
    for key in sorted(set(cargs) & set(aargs)):
        if key in ROLE_KEYS or cargs[key] in (None, "", [], {}) or aargs[key] in (None, "", [], {}):
            continue
        if not _same(cargs[key], aargs[key]):
            return False
    return True


def _branch_decisive(act, branch_diff, agent):
    """Whether an act decides a branch (15.3): without a diff set every EFFECT, DEPOSIT and report command; with one,
    a successful command of the set (the reference rule: the reference's own branch-specific commands) and, for an
    agent, any successful command that does what an effectful one of them does (_does_what)."""
    if branch_diff is None:
        return _decisive(act)
    if not act.get("ok"):
        return False
    commands = [c for c in branch_diff if isinstance(c, dict)]
    if any(_matches(act, c) for c in commands):
        return True
    return bool(agent) and any(_does_what(act, c) for c in commands)


def act_gaps(env, task, answers, index=None, branch_diff=None, bindings=None, agent=False):
    """The decisive acts of env's run taken before their evidence was perceived at its tier (15.3): [{"kind":
    "acted_unresolved" | "acted_unperceived" | "branch_unperceived" | "claim_unperceived", "answer", "entity",
    "field", "step", "action"}]. For the reference (S3) any gap fails it: it may not pick the right look-alike or
    branch by its privileged knowledge. branch_diff: the commands of the branch diff set (10.3 B8), each {"action",
    "args"} (or a plain command dict) with true ids; None takes every EFFECT, DEPOSIT and report command as decisive.
    agent: the agent rule (agent_labels) -- a branch is also decided by any command that does what an effectful diff
    command does, whatever its verb (_does_what); the reference rule matches the reference's own commands."""
    idx = index if index is not None else build_index(env)
    answers = resolve_answers(answers, bindings) if answers else {}
    gaps = []
    for name in sorted(answers):
        spec = answers[name]
        kind = spec.get("kind")
        if kind in ("entity", "set") and _group(spec):
            gaps.extend(_member_gaps(idx, env, name, spec))
        elif kind == "branch":
            gaps.extend(_branch_gaps(idx, env, name, spec, branch_diff, agent))
    gaps.extend(_claim_gaps(idx, env))
    return gaps


def _member_gaps(idx, env, name, spec):
    group = _group(spec)
    items = _evidence_items(spec)
    out, flagged = [], set()
    for act in idx.acts:
        if not _decisive(act):
            continue
        step = int(act.get("step", 0))
        for eid in act.get("ids") or ():
            if eid not in group:
                continue
            target = (act.get("targets") or {}).get(eid) or {}
            founded = True
            for item in items:
                field = item.get("field")
                if not field:
                    continue
                need = tier_level(item.get("tier", "inspect"))
                ok = _perceived(idx, env, eid, field, need, step, act)
                if not ok and spec.get("kind") == "entity":
                    # by elimination: the evidence of every other member says this one is the answer
                    others = [m for m in group if m != eid]
                    ok = bool(others) and all(_perceived(idx, env, m, field, need, step) for m in others)
                if ok and item.get("tier") in IDENTITY_TIERS:
                    ok = idx.seen(eid, step, identified=True)
                founded = founded and ok
                if not ok and ("unperceived", eid, field) not in flagged:
                    flagged.add(("unperceived", eid, field))
                    out.append({"kind": "acted_unperceived", "answer": name, "entity": eid, "field": field,
                                "step": step, "action": act.get("action")})
            # a look-alike or unfamiliar thing the robot could not tell apart, unless the answer's evidence (its own
            # or, for an entity answer, every other member's) told it which one this is
            if target.get("unresolved") and not (items and founded) and ("unresolved", eid) not in flagged:
                flagged.add(("unresolved", eid))
                out.append({"kind": "acted_unresolved", "answer": name, "entity": eid, "field": None, "step": step,
                            "action": act.get("action")})
    return out


def _branch_gaps(idx, env, name, spec, branch_diff, agent=False):
    items = _evidence_items(spec)
    if not items:
        return []
    for act in idx.acts:
        if not _branch_decisive(act, branch_diff, agent):
            continue
        step = int(act.get("step", 0))
        missing = []
        for item in items:
            eids = item.get("entity")
            eids = [eids] if isinstance(eids, str) else [e for e in (eids or []) if isinstance(e, str)]
            field = item.get("field")
            need = tier_level(item.get("tier", "inspect"))
            for eid in eids:
                if not field:
                    if not idx.seen(eid, step, identified=item.get("tier") in IDENTITY_TIERS):
                        missing.append(eid)
                elif not _perceived(idx, env, eid, field, need, step, act):
                    missing.append("{}.{}".format(eid, field))
        if missing:
            return [{"kind": "branch_unperceived", "answer": name, "entity": None, "field": ", ".join(missing),
                     "step": step, "action": act.get("action"), "branch": spec.get("value")}]
        return []           # the evidence came before the first decisive act
    return []


def _speech_log(env):
    log = (env.world.s.get("facts") or {}).get("speech_log")
    return [u for u in log if isinstance(u, dict)] if isinstance(log, list) else []


def _claim_gaps(idx, env):
    out = []
    for utterance in _speech_log(env):
        step = utterance.get("step")
        if step is None and _num(utterance.get("t")) is not None:
            step = idx.step_of_time(utterance["t"])
        for i, claim in enumerate(utterance.get("claims") or ()):
            if not isinstance(claim, dict) or claim.get("truth") is None:
                continue          # no truth mapping: neither scored nor gated (13.1)
            if "founded" in claim:
                # judged when it was said (WP4's speech log): None is "could not be judged", never a failure
                founded = claim["founded"] is not False
            else:
                founded = claim_founded(env, dict(claim, step=step), index=idx)
            if not founded:
                out.append({"kind": "claim_unperceived", "answer": None, "entity": _subject_id(claim.get("subject")),
                            "field": None, "step": step, "action": "say",
                            "n": utterance.get("n", "{}.{}".format(step, i))})
    return out


def _subject_id(subject):
    if isinstance(subject, dict):
        return subject.get("id")
    return subject if isinstance(subject, str) else None


# ----- claims (13.2 condition 4, 15.3) ----------------------------------------------------------------------------

def claim_founded(env, claim, index=None, step=None):
    """Whether a factual claim (speech.parse's Claim: kind, subject, value, certainty...) rests on evidence perceived
    before it was said (claim "step", or "t", or the `step` argument; none: any time so far). Uncertain claims and
    claims that are not factual (commitments, directives, refusals, attributions) are always founded (13.1): they
    assert nothing about the house. A state or issue claim needs one of the vocabulary's evidence entries for that
    state or issue (claim "fields" overrides them) perceived at its tier -- on the subject, near it, or a notice of the
    kind; a quantity claim a measure-tier reading of the subject's field for the quantity within its precision; a
    none_found claim every thing in its scope seen. A person telling the robot the same thing first founds it, and so
    does seeing a person or pet the claim is about."""
    if not isinstance(claim, dict) or claim.get("certainty", "asserted") != "asserted":
        return True
    kind = claim.get("kind")
    if kind not in FACTUAL_KINDS:
        return True
    idx = index if index is not None else build_index(env)
    if step is None:
        step = claim.get("step")
    if step is None and _num(claim.get("t")) is not None:
        step = idx.step_of_time(claim["t"])
    if step is None:
        # judged while the robot says it (WP4's core say, mid-step): evidence of the steps before this one; after
        # the episode: everything perceived so far
        realism = getattr(env, "realism", None)
        step = idx.last_step if getattr(realism, "_act_open", False) else idx.last_step + 1
    state = env.world.s
    subject = claim.get("subject")
    if _heard_before(idx, claim, step):
        return True
    if isinstance(subject, list):
        if kind == "none_found" or claim.get("quant") == "all":
            return all(claim_founded(env, dict(claim, subject=s, kind=kind if kind != "none_found" else "state"),
                                     index=idx, step=step) for s in subject if isinstance(s, str)) if subject else False
        return any(claim_founded(env, dict(claim, subject=s), index=idx, step=step) for s in subject if isinstance(s, str))
    sid = _subject_id(subject)
    if sid is None:
        return kind != "none_found"
    if kind == "location":
        return _location_founded(idx, env, claim, sid, step)
    if sid in (state.get("people") or {}) or sid in (state.get("pets") or {}):
        return any(s < step for _t, s, _shown in idx.agents.get(sid, ()))
    if sid in (state.get("rooms") or {}):
        if kind == "none_found":
            members = _scope_members(env, {"id": sid, "kind": "room"})
            return all(idx.seen(m, step) for m in members) and any(s < step and r == sid for _t, s, r in idx.rooms)
        if claim.get("fields") or _vocab_evidence(claim):
            return _claim_evidence(idx, env, claim, sid, step) or any(s < step and r == sid for _t, s, r in idx.rooms)
        return any(s < step and r == sid for _t, s, r in idx.rooms)
    if sid not in state.get("entities", {}):
        # a thing destroyed since: what the robot did with it or saw of it before
        return any(sid in (a.get("ids") or ()) for a in idx.acts if a.get("step", 0) < step) or idx.seen(sid, step)
    if kind == "none_found":
        members = _scope_members(env, {"id": sid, "kind": "entity"}) or [sid]
        return all(idx.seen(m, step) for m in members)
    if kind == "quantity":
        return _quantity_founded(idx, env, claim, sid, step)
    return _claim_evidence(idx, env, claim, sid, step)


def _location_founded(idx, env, claim, sid, step):
    """A location claim ("the box is in the hall", "Mia is in the kitchen": value the place id or ids, polarity)
    rests on where the robot perceived the subject before `step`: an observation listing the thing with a location
    in or on the place, the robot's own placement of it there (the act log's "placed"), or, for a person or pet, an
    observation of them made in that room. A negated claim rests on having seen the subject elsewhere, or on having
    looked in the room it denies. Having seen the thing somewhere else founds no positive claim (P2-S2 merge gate:
    location claims were always founded before; WP4-fix's request)."""
    from ..kernel.predicates import is_within, root_room
    state = env.world.s
    value = claim.get("value")
    places = [p for p in (value if isinstance(value, list) else [value]) if isinstance(p, str)]
    if not places:
        return False
    rooms = state.get("rooms") or {}
    negative = claim.get("polarity", 1) == -1

    def inside(loc):
        if not isinstance(loc, str):
            return None
        for place in places:
            if loc == place:
                return True
            if place in rooms:
                if loc not in rooms and root_room(state, loc) == place:
                    return True
            elif loc not in rooms and is_within(state, loc, place):
                return True
        return False

    seen = []                                   # (step, t, in the place: True / False / None) per perception
    if sid in (state.get("people") or {}) or sid in (state.get("pets") or {}):
        room_at = {}
        for t, s, rid in idx.rooms:
            room_at.setdefault((t, s), rid)
        for t, s, shown in idx.agents.get(sid, ()):
            if s < step:
                where = shown.get("room") if isinstance(shown, dict) and isinstance(shown.get("room"), str) else \
                    room_at.get((t, s))
                seen.append((s, t, inside(where)))
    else:
        for o in idx.entries(sid, "location", step):
            seen.append((o.step, o.t, inside(o.shown)))
        for act in idx.acts:
            placed = act.get("placed") if isinstance(act, dict) else None
            s = int(act.get("step", 0) or 0) if isinstance(act, dict) else 0
            if isinstance(placed, dict) and sid in placed and s < step:
                seen.append((s, float(_num(act.get("t")) or 0.0), inside(placed[sid])))
    seen = sorted((s for s in seen if s[2] is not None), key=lambda item: (item[0], item[1]))
    last = seen[-1] if seen else None           # where it was last perceived decides
    if not negative:
        return last is not None and last[2] is True
    if last is not None and last[2] is False:
        return True
    # a denied room looked into after the last sighting in the place (or with none): it was not there
    after = last[0] if last is not None else -1
    return any(after < s < step and r in places for _t, s, r in idx.rooms)


def _vocab_evidence(claim):
    from ..kernel import dialogue
    vocab = dialogue.vocabulary()
    table = vocab["states"] if claim.get("kind") == "state" else vocab["issues"] if claim.get("kind") == "issue" else {}
    value = claim.get("value")
    if not isinstance(value, str):
        return []
    entry = table.get(value)
    if entry is None:
        canon = (vocab.get("_index") or {}).get("states" if claim.get("kind") == "state" else "issues", {}).get(value)
        entry = table.get(canon) if canon else None
    return list((entry or {}).get("evidence") or [])


def _claim_evidence(idx, env, claim, sid, step):
    fields = claim.get("fields")
    entries = [{"field": f} for f in fields] if isinstance(fields, list) and fields else _vocab_evidence(claim)
    if not entries:
        return idx.seen(sid, step)       # a state the vocabulary gives no evidence for: the thing was seen
    rec = {"kind": "finding" if claim.get("kind") == "issue" else "status"}
    return _evidence_before(idx, env, rec, sid, step, entries=entries)


def _quantity_founded(idx, env, claim, sid, step):
    from ..kernel import reports
    quantity = claim.get("quantity")
    if quantity is None and claim.get("unit"):
        quantity = reports.unit_of(claim["unit"])[0]
    value = _num(claim.get("value"))
    state = env.world.s
    quantities = [quantity] if quantity else sorted(k for k in tables()["quantity_fields"] if k != "about")
    for q in quantities:
        for field, factor in _subject_fields(state, sid, q):
            if value is None:
                if idx.entries(sid, field, step, MEASURE):
                    return True
            elif supports(idx, env, sid, field, value / factor, step, need=MEASURE) is not None:
                return True
        for r in idx.readings:
            if r.get("subject") == sid and r["step"] < step and (quantity is None or r.get("quantity") == q) and \
                    (value is None or (r.get("value_si") is not None and _same(value, r["value_si"], precision("")))):
                return True
    return False


def _heard_before(idx, claim, step):
    """A person told the robot the same claim (kind, subject, value, polarity) before `step`."""
    want = (claim.get("kind"), json.dumps(claim.get("subject"), sort_keys=True, default=str),
            json.dumps(claim.get("value"), sort_keys=True, default=str), claim.get("polarity", 1))
    for message in idx.heard:
        if message["step"] >= step:
            continue
        for heard in message.get("claims") or ():
            if isinstance(heard, dict) and heard.get("certainty", "asserted") == "asserted" and \
                    (heard.get("kind"), json.dumps(heard.get("subject"), sort_keys=True, default=str),
                     json.dumps(heard.get("value"), sort_keys=True, default=str), heard.get("polarity", 1)) == want:
                return True
    return False


# ----- the agent rule ---------------------------------------------------------------------------------------------

def agent_labels(env, task, answers, index=None, branch_diff=None, bindings=None):
    """The agent rule's failure labels (15.3; schema 4 in the evaluation mode, on by default): one per unfounded
    decisive act, decision, report or claim -- perceive:acted_unresolved:<e>, perceive:acted_unperceived:<e>.<field>,
    perceive:branch_unperceived:<branch>, perceive:report_unperceived:<n>, perceive:claim_unperceived:<n> -- sorted and
    unique. Empty without realistic perception (oracle runs perceive everything). A branch is decided by the diff
    set's commands and by any command that does what an effectful one does (act_gaps(agent=True))."""
    if getattr(env, "realism", None) is None:
        return []
    idx = index if index is not None else build_index(env)
    labels = set()
    for gap in act_gaps(env, task, answers, index=idx, branch_diff=branch_diff, bindings=bindings, agent=True):
        kind = gap["kind"]
        if kind == "acted_unresolved":
            labels.add("perceive:acted_unresolved:{}".format(gap["entity"]))
        elif kind == "acted_unperceived":
            labels.add("perceive:acted_unperceived:{}.{}".format(gap["entity"], gap["field"]))
        elif kind == "branch_unperceived":
            labels.add("perceive:branch_unperceived:{}".format(gap.get("branch") if gap.get("branch") is not None
                                                               else gap["answer"]))
        elif kind == "claim_unperceived":
            labels.add("perceive:claim_unperceived:{}".format(gap.get("n")))
    for gap in report_gaps(env, task, answers, index=idx, bindings=bindings):
        labels.add("perceive:report_unperceived:{}".format(str(gap["report"]).replace("report ", "")))
    return sorted(labels)
