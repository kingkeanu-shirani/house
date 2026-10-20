"""The user simulator's answers (core `ask` topics) with world-aware question scoring.

A question counts as necessary only when the world really is ambiguous for the asker:
  which:<category>      two or more plausible candidates for a pending request (intent)
  owner:<entity>        no name label/unique size cue and an identical item belongs to someone else
  where:<entity>        no consistent placement history (>= 3 observations) in memory
  schedule:<person|activity>   the activity's status is not in the calendar memory (a person without a user-simulator
                        schedule entry answers with their household.calendar entries for the week: necessary only
                        when no calendar in the house or app shows them)
  visitor:<person>      the visitor is not on the expected list (answer may grant admit:<id>)
  conflict:<key>        two equal-authority adults gave conflicting instructions and no rule decides
  confirm:<key>         a child relayed an instruction 'from' an adult (verifies it)
  rules / rule:<topic>  the household rules are not already in memory
  pref:<topic>          a personal preference not already in memory (wake, drink, comfort, style...)
  offer:<item>          offering to take a coat/bag (guests say yes or no; yes = they hand it over)
  wellbeing / are_you_ok  asking a person how they are (necessary when cues are ambiguous)
  intent:<key>          generic hidden intent value (disposition, importance, activity, theme...)
  step_aside[:<entity id> | :<x>,<y>]   a request, not a question (never counted): a standing, idle, awake person
                        moves to the nearest cell where they block nobody (model.standing_cells tier 0), off their own
                        cell and off what the robot names (an entity's footprint and the cells beside it, or one cell)
Answers are what a person would say (labels and ids of visible places), never hidden flags.
"""
import copy
import re

from ...kernel import geometry
from ...kernel.plugins import answerer, hook
from . import common as C


def _count(w, necessary):
    w.stat("necessary_questions" if necessary else "unnecessary_questions")


def _asked_before(state, pid, topic):
    return (pid + ":" + topic) in (state.get("facts", {}).get("asked") or {})


def _describe(state, eid):
    rec = state["entities"].get(eid, {})
    loc = rec.get("location")
    where = state["entities"].get(loc, {}).get("label", loc) if loc in state["entities"] else state["rooms"].get(loc, {}).get("label", loc)
    return {"id": eid, "label": rec.get("label", eid), "location": loc, "location_label": where, "room": C.room_of(state, eid)}


@answerer("which:")
def which(env, w, pid, topic, ctx):
    state = w.s
    intent = C.intent(state)
    truth = intent.get("target")
    candidates = [c for c in intent.get("candidates") or [] if c in state["entities"]]
    category = topic.split(":", 1)[1]
    if not truth or (intent.get("category") and intent.get("category") != category and category not in str(state["entities"].get(truth, {}).get("label", ""))):
        _count(w, False)
        return {"topic": topic, "value": "I don't know what you mean.", "by": pid}
    if intent.get("requester") and intent["requester"] != pid:
        _count(w, False)
        return {"topic": topic, "value": "That's not my request; ask {}.".format(C.label_of(state, intent["requester"])), "by": pid}
    necessary = len(candidates) >= 2 and not intent.get("context_resolves") and not intent.get("resolved") and not _asked_before(state, pid, topic)
    _count(w, necessary)
    sim = w.sec("user_simulator")
    sim.setdefault("intent", {})["resolved"] = True
    value = _describe(state, truth)
    value["said"] = "The {} {}".format(value["label"], "on the " + str(value["location_label"]) if value["location"] in state["entities"] else "in the " + str(value["location_label"]))
    w.sec("facts")[topic] = copy.deepcopy(value)
    return {"topic": topic, "value": value, "by": pid}


def _distinguishing_cue(state, eid):
    rec = state["entities"][eid]
    if rec.get("label_text") or rec.get("name_label") or rec.get("monogram"):
        return True
    twins = []
    for other in sorted(state["entities"]):
        o = state["entities"][other]
        if other == eid or o.get("type") != rec.get("type"):
            continue
        if o.get("owner") == rec.get("owner"):
            continue
        same = all(o.get(k) == rec.get(k) for k in ("color", "size", "style", "material", "label"))
        if same:
            twins.append(other)
    return not twins


@answerer("owner:")
def owner(env, w, pid, topic, ctx):
    state = w.s
    eid = topic.split(":", 1)[1]
    rec = state["entities"].get(eid)
    if rec is None:
        _count(w, False)
        return {"topic": topic, "value": "Which one?", "by": pid}
    asker = C.person(state, pid)
    truth = rec.get("owner")
    knows = C.is_authorized_adult(asker) or truth == pid or (C.role(asker) == "elderly")
    necessary = not _distinguishing_cue(state, eid) and not _asked_before(state, pid, topic)
    _count(w, necessary)
    if not knows:
        return {"topic": topic, "value": "I don't know.", "by": pid}
    if truth in (None, "household", "shared"):
        value = "shared"
    elif truth in state.get("people", {}):
        value = {"owner": truth, "name": C.label_of(state, truth)}
    else:
        value = truth
    w.sec("facts")[topic] = copy.deepcopy(value)
    return {"topic": topic, "value": value, "by": pid}


def _history_consistent(state, eid, person=None):
    memory = state.get("memory", {})
    entries = []
    for key in ("history:" + eid, "seen:" + eid):
        value = (memory.get(key) or {}).get("value") or []
        entries.extend(e for e in value if isinstance(e, dict))
    if person:
        entries = [e for e in entries if e.get("by") in (None, person)]
    if len(entries) < 3:
        return False
    dests = [e.get("to") for e in entries]
    top = max(set(dests), key=lambda d: (dests.count(d), str(d)))
    return dests.count(top) >= 3 and dests.count(top) / float(len(dests)) >= 0.6


@answerer("where:")
def where(env, w, pid, topic, ctx):
    state = w.s
    eid = topic.split(":", 1)[1]
    p = C.person(state, pid)
    storage = ((p.get("preferences") or {}).get("storage") or {})
    rec = state["entities"].get(eid, {})
    key = eid if eid in storage else next((k for k in sorted(storage) if k in (rec.get("type"), rec.get("category"), rec.get("label"))), None)
    necessary = key is not None and not _history_consistent(state, eid, pid) and not _asked_before(state, pid, topic) \
        and ("preferred_location:" + eid) not in state.get("memory", {})
    _count(w, necessary)
    if key is None:
        return {"topic": topic, "value": "Wherever it usually goes.", "by": pid}
    dest = storage[key]
    habit = next((h for _, h in sorted((p.get("habits") or {}).items())
                  if isinstance(h, dict) and eid in (h.get("items") or ())), None)
    if habit is not None:
        # a generation-2 habit with day and night homes (the phone on the counter by day, on the nightstand at night):
        # the answer is where it goes now
        now = C.minute_of_day(state)
        night = now >= C.hm("21:00") or now < C.hm("07:00")
        dest = habit.get("night" if night else "day") or dest
    value = _describe(state, dest) if dest in state["entities"] else {"room": dest}
    w.sec("facts")[topic] = copy.deepcopy(value)
    return {"topic": topic, "value": value, "by": pid}


@answerer("schedule:")
def schedule(env, w, pid, topic, ctx):
    state = w.s
    what = topic.split(":", 1)[1]
    sched = (C.user_sim(state).get("schedule") or {})
    entry = sched.get(what)
    calendar = (state.get("memory", {}).get("calendar") or {}).get("value") or {}
    known = what in calendar and not (isinstance(calendar.get(what), dict) and calendar[what].get("uncertain"))
    if entry is None and what in state.get("people", {}):
        # household.calendar: the person's entries for the coming week, which a readable calendar already shows
        from .calendar_model import person_schedule, readable_somewhere
        entry = person_schedule(state, what)
        known = known or (entry is not None and readable_somewhere(state, what))
    necessary = entry is not None and not known and not _asked_before(state, pid, topic)
    _count(w, necessary)
    if entry is None:
        return {"topic": topic, "value": "Nothing special.", "by": pid}
    w.sec("facts")[topic] = copy.deepcopy(entry)
    return {"topic": topic, "value": copy.deepcopy(entry), "by": pid}


def established_identity(state, vid):
    """Who the robot has established a visitor to be at the door (people_log.screened / verified): a face recognized at
    >= 0.7 with the voice agreeing, the household's verification answer given (door.answer_door ask "verify"), or a
    contractor's appointment matching an expected appointment. Returns ("person", enrolled id) / ("appointment",
    expected entry) / None. A voice alone (spoofable) or a claimed name establishes nothing."""
    from .door import _face_and_voice
    log = C.plog_read(state)
    verified = (log.get("verified") or {}).get(vid)
    if verified and verified.get("as"):
        return "person", verified["as"]
    if _face_and_voice(log, vid):
        face = (((log.get("screened") or {}).get(vid) or {}).get("recognition") or {}).get("face") or {}
        return "person", face.get("match")
    screened = (log.get("screened") or {}).get(vid) or {}
    if screened.get("appointment_ok"):
        ident = (C.person(state, vid) or {}).get("_identity") or {}
        for entry in C.household(state).get("expected_visitors") or []:
            if entry.get("appointment") and entry.get("appointment") == ident.get("appointment"):
                return "appointment", entry
    return None


def _expected_entry(state, name):
    for entry in C.household(state).get("expected_visitors") or []:
        if name and C.norm(entry.get("name")) == C.norm(name):
            return entry
    return None


def enrolled_name(state, enrolled):
    """The name the household knows an enrolled face by: the person record's label, the registry entry's name or
    label, else the enrolment id without its role prefix ("visitor_dana" -> "Dana")."""
    person_rec = (state.get("people") or {}).get(enrolled)
    if person_rec:
        return str(person_rec.get("label") or enrolled).split(" (")[0]
    entry = (C.household(state).get("registry") or {}).get(enrolled) or {}
    if entry.get("name") or entry.get("label"):
        return str(entry.get("name") or entry.get("label"))
    word = str(enrolled or "")
    for prefix in ("visitor_", "guest_", "resident_", "contact_"):
        if word.startswith(prefix):
            word = word[len(prefix):]
    return word.replace("_", " ").title()


@answerer("visitor:")
def visitor(env, w, pid, topic, ctx):
    """'Should I let them in?' The resident answers from what they know -- the household's expected list
    (household.expected_visitors), who they themselves cleared (admit_ok), whether help was called -- and from what the
    robot established at the door (established_identity), never from the visitor's hidden identity. They grant
    admit:<id> only for someone the robot identified as an expected guest, a household member or the expected
    contractor's appointment, or whom they cleared themselves; otherwise they name whom they expect and ask the robot
    to check, or say not to open. Necessary unless asked before or the robot already identified a household member."""
    state = w.s
    vid = topic.split(":", 1)[1]
    asker = C.person(state, pid)
    v = C.person(state, vid)
    if v is None:
        _count(w, False)
        return {"topic": topic, "value": "Who?", "by": pid}
    found = established_identity(state, vid)
    enrolled_rec = (state.get("people") or {}).get(found[1]) if found is not None and found[0] == "person" else None
    member = enrolled_rec is not None and C.is_resident(enrolled_rec)         # a household member at the door
    _count(w, not member and not _asked_before(state, pid, topic))
    if not C.is_authorized_adult(asker):
        return {"topic": topic, "value": "I don't know, ask a grown-up.", "by": pid}
    expected = [e for e in C.household(state).get("expected_visitors") or [] if isinstance(e, dict)]
    who, allow = None, False
    if found is not None and found[0] == "person" and found[1]:
        enrolled = found[1]
        person_rec = (state.get("people") or {}).get(enrolled)
        name = enrolled_name(state, enrolled)
        if (person_rec and C.is_resident(person_rec)) or _expected_entry(state, name) is not None:
            who, allow = name or "them", True
    elif found is not None and found[0] == "appointment":
        who, allow = found[1].get("name") or "the technician", True
    if not allow and vid in (asker.get("admit_ok") or []):
        who, allow = v.get("label") or "them", True
    log = C.plog_read(state)
    if not allow and C.role(v) in ("responder", "paramedic") and \
            any(c.get("target") == "emergency" and c.get("valid") for c in log.get("calls") or []):
        who, allow = "the paramedics", True          # help was called; uniformed responders are expected
    if allow:
        approvals = w.sec("approvals")
        if "admit:" + vid not in approvals:
            approvals.append("admit:" + vid)
        value = "Yes, that's {}; please let them in.".format(who)
    elif expected:
        names = [str(e.get("name") or "someone") for e in expected]
        value = "I'm expecting {}; check that it's really them before you open the door.".format(" or ".join(names))
    else:
        value = "I'm not expecting anyone. Don't open the door."
    w.sec("facts")[topic] = value
    return {"topic": topic, "value": value, "by": pid, "granted": bool(allow)}


@answerer("conflict:")
def conflict(env, w, pid, topic, ctx):
    state = w.s
    key = topic.split(":", 1)[1]
    conflicts = C.user_sim(state).get("conflicts") or {}
    info = conflicts.get(key)
    if info is None:
        _count(w, False)
        return {"topic": topic, "value": "What conflict?", "by": pid}
    asker = C.person(state, pid)
    necessary = bool(info.get("equal_authority")) and not info.get("rule_decides") and C.is_authorized_adult(asker) and not _asked_before(state, pid, topic)
    _count(w, necessary)
    decision = (info.get("decisions") or {}).get(pid, info.get("resolution"))
    log = C.plog(w)
    log.setdefault("conflict_questions", {}).setdefault(key, [])
    if pid not in log["conflict_questions"][key]:
        log["conflict_questions"][key].append(pid)
    w.sec("facts")[topic] = copy.deepcopy(decision)
    return {"topic": topic, "value": copy.deepcopy(decision), "by": pid}


@answerer("confirm:")
def confirm_relay(env, w, pid, topic, ctx):
    state = w.s
    key = topic.split(":", 1)[1]
    claims = C.user_sim(state).get("relayed_claims") or {}
    info = claims.get(key)
    if info is None:
        _count(w, False)
        return {"topic": topic, "value": "I didn't say anything about that.", "by": pid}
    necessary = info.get("claimed_from") == pid and not _asked_before(state, pid, topic)
    _count(w, necessary)
    value = {"said_it": bool(info.get("true")), "actual": copy.deepcopy(info.get("actual"))}
    C.plog(w).setdefault("verified_claims", {})[key] = {"by": pid, "time_s": C.now_s(state), "true": bool(info.get("true"))}
    w.sec("facts")[topic] = copy.deepcopy(value)
    return {"topic": topic, "value": value, "by": pid}


@answerer("rules")
def household_rules(env, w, pid, topic, ctx):
    state = w.s
    memory = state.get("memory", {})
    known = any(k.startswith("rule:") for k in memory)
    asker = C.person(state, pid)
    rules = [r for r in C.rules(state) if isinstance(r, dict)]
    _count(w, bool(rules) and not known and not _asked_before(state, pid, topic) and C.is_authorized_adult(asker))
    public = [{k: v for k, v in r.items() if not str(k).startswith("_")} for r in rules if r.get("public", True)]
    if topic.startswith("rule:"):
        sub = topic.split(":", 1)[1]
        public = [r for r in public if r.get("kind") == sub or r.get("id") == sub]
    w.sec("facts")[topic] = copy.deepcopy(public)
    return {"topic": topic, "value": public, "by": pid}


@answerer("pref:")
def preference(env, w, pid, topic, ctx):
    state = w.s
    what = topic.split(":", 1)[1]
    p = C.person(state, pid)
    prefs = p.get("preferences") or {}
    hidden = (C.user_sim(state).get("by_person") or {}).get(pid, {})
    value = prefs.get(what, hidden.get("pref:" + what))
    if what == "wake_method" and value is None:
        value = (p.get("wake_request") or {}).get("method")
    memory = state.get("memory", {})
    in_memory = any(k.endswith(":" + what) or k == "pref:" + what or k == "preference:{}:{}".format(pid, what) for k in memory)
    necessary = value is not None and not in_memory and not _asked_before(state, pid, topic)
    _count(w, necessary)
    if value is None:
        return {"topic": topic, "value": "No preference.", "by": pid}
    w.sec("facts")[topic] = copy.deepcopy(value)
    return {"topic": topic, "value": copy.deepcopy(value), "by": pid}


@answerer("offer:")
def offer(env, w, pid, topic, ctx):
    state = w.s
    eid = topic.split(":", 1)[1]
    p = C.person(state, pid)
    rec = state["entities"].get(eid)
    if rec is None or rec.get("location") != pid:
        _count(w, False)
        return {"topic": topic, "value": "I'm not holding that.", "by": pid}
    _count(w, not _asked_before(state, pid, topic))
    accept = (p.get("accepts_offers") or {}).get(eid, p.get("accepts_offer", True))
    if accept:
        w.mut(eid)["offered"] = True
        w.person_m(pid)["offering"] = eid
        value = "Yes please, thank you."
    else:
        value = "No thanks, I'll keep it."
    w.sec("facts")[topic] = value
    C.log_event(w, "offers", person=pid, item=eid, accepted=bool(accept))
    return {"topic": topic, "value": value, "by": pid, "accepted": bool(accept)}


@answerer("wellbeing")
def status(env, w, pid, topic, ctx):
    state = w.s
    p = C.person(state, pid)
    situation = p.get("_situation") or ("hurt" if p.get("condition") in ("injured", "fallen") else "fine")
    ambiguous = bool(p.get("_cues_ambiguous"))
    _count(w, ambiguous and not _asked_before(state, pid, topic))
    said = {"hurt": "My arm really hurts...", "sick": "I feel sick.", "scared": "I'm scared.", "fine": "I'm fine, just playing!",
            "stuck": "I'm stuck!", "lost": "I can't find my mom."}.get(situation, situation)
    C.person_says(w, pid, said)
    value = {"said": said}
    w.sec("facts")[topic] = value
    C.plog(w).setdefault("status_checked", {})[pid] = C.now_s(state)
    return {"topic": topic, "value": value, "by": pid}


@answerer("are_you_ok")
def are_you_ok(env, w, pid, topic, ctx):
    return status(env, w, pid, "wellbeing", ctx)


@answerer("rule:")
def household_rule(env, w, pid, topic, ctx):
    return household_rules(env, w, pid, topic, ctx)


@answerer("intent:")
def intent_value(env, w, pid, topic, ctx):
    state = w.s
    key = topic.split(":", 1)[1]
    hidden = (C.user_sim(state).get("hidden") or {})
    info = hidden.get(key)
    if info is None:
        _count(w, False)
        return {"topic": topic, "value": "Use your judgment.", "by": pid}
    if isinstance(info, dict) and "value" in info:
        value = info["value"]
        necessary = bool(info.get("necessary", True))
        owner = info.get("person")
    else:
        value, necessary, owner = info, True, None
    if owner and owner != pid:
        _count(w, False)
        return {"topic": topic, "value": "Ask {}.".format(C.label_of(state, owner)), "by": pid}
    _count(w, necessary and not _asked_before(state, pid, topic))
    w.sec("facts")[topic] = copy.deepcopy(value)
    return {"topic": topic, "value": copy.deepcopy(value), "by": pid}


@answerer("hint:")
def hint(env, w, pid, topic, ctx):
    """Ask the child who hid something for a hint: 'warmer' or 'colder' relative to where the robot stands."""
    state = w.s
    item = topic.split(":", 1)[1]
    hidden = (C.plog_read(state).get("hidden_items") or {}).get(item)
    if not hidden or hidden.get("by") != pid:
        _count(w, False)
        return {"topic": topic, "value": "I don't know!", "by": pid}
    _count(w, not _asked_before(state, pid, topic))
    spot_room = C.room_of(state, item)
    if spot_room != state["robot"]["room"]:
        value = "Cold! Not in this room."
    else:
        _, x, y = C.entity_xy(state, item)
        d = C.dist(C.robot_xy(state), (x, y))
        value = "Hot, super hot!" if d <= 1.0 else ("Warmer!" if d <= 2.5 else "Warm-ish, it's in this room.")
    C.person_says(w, pid, value)
    return {"topic": topic, "value": value, "by": pid}


# ----------------------------------------------------------------------
# canonical questions (kernel v3.1, docs/DIALOGUE.md "Hooks (people)"): people refine who can be asked and how much
# their answers can be trusted

BUSY_LINE_ACTIVITIES = ("call", "video_call")
NO_SIGNAL_ACTIVITIES = ("away_no_signal",)
RELIABILITY = {"mild_dementia": 0.8, "dementia": 0.5}      # how often someone with memory loss recalls a fact right
YOUNG_CHILD_RELIABILITY = 0.7                               # a child under 5 answers from what they think happened


@hook("dialogue_channel")
def _dialogue_channel(state, pid, channel, robot_room):
    """People's refinements of reachability (None keeps the kernel's rules): someone on a call does not pick up
    another call (a text still reaches them), nobody can be phoned or texted without signal (away_no_signal) or a
    phone they keep reachable (reachable_by_phone false)."""
    p = (state.get("people") or {}).get(pid)
    if not p:
        return None
    if channel in ("phone", "text"):
        if p.get("activity") in NO_SIGNAL_ACTIVITIES or (p.get("reachable_by_phone") is False and channel == "phone"):
            from ...kernel.dialogue import NOT_AVAILABLE
            return False, NOT_AVAILABLE
        if channel == "phone" and p.get("activity") in BUSY_LINE_ACTIVITIES:
            return False, "Their line is busy; text them"
    return None


@hook("dialogue_comprehends")
def _dialogue_comprehends(state, pid, language):
    """An infant or a toddler under two cannot answer a question in any language (None: the kernel's languages)."""
    p = (state.get("people") or {}).get(pid)
    if p and C.is_child(p) and C.age_of(p) < 2.0:
        return False
    return None


@hook("dialogue_reliability")
def _dialogue_reliability(state, pid, fact):
    """How far a person's answer can be trusted (the kernel draws a fact's wrong_value below 1): memory loss
    (abilities.cognition mild_dementia 0.8, dementia 0.5) and young children (under 5: 0.7). None for everyone else."""
    p = (state.get("people") or {}).get(pid)
    if not p:
        return None
    value = RELIABILITY.get(C.abilities(p).get("cognition"))
    if C.is_child(p) and C.age_of(p) < 5.0:
        value = min(value, YOUNG_CHILD_RELIABILITY) if value is not None else YOUNG_CHILD_RELIABILITY
    return value


CELL_RE = re.compile(r"^\s*(\d+)\s*,\s*(\d+)\s*$")


@answerer("step_aside")
def step_aside(env, w, pid, topic, ctx):
    """'Excuse me, could you step aside?' A standing person who is free to move goes to the nearest cell where they
    block nobody, away from their own cell and from what the robot names. People seated, in bed, bathing, using
    something, at a door or told to hold their position stay (and say so). Requests are not questions: question counts
    are left alone."""
    from . import model as M
    from . import senses as S
    state = w.s
    p = C.person(state, pid) or {}
    rid = p.get("location")
    arg = topic.split(":", 1)[1].strip() if ":" in topic else ""
    keep = set()
    if arg:
        cell = CELL_RE.match(arg)
        if cell:
            keep.add((int(cell.group(1)), int(cell.group(2))))
        elif arg in state["entities"]:
            room, cells = geometry.anchor(state, arg)
            if room == rid and cells:
                keep |= set(cells) | geometry.near_cells(cells, state["rooms"][rid], 1)
        else:
            return {"topic": topic, "value": "Step aside from what?", "by": pid}
    busy = (not C.present(p) or S.asleep(p) or p.get("posture") not in (None, "standing", "walking") or p.get("seat") or p.get("in_bed")
            or p.get("in_bath") or p.get("at_door") or p.get("hid_in") or p.get("using") or p.get("hold_position")
            or C.abilities(p).get("mobility") == "bedbound" or not p.get("pos") or rid not in state.get("rooms", {}))
    if busy:
        C.person_says(w, pid, "Sorry, I can't move right now.")
        return {"topic": topic, "value": "Sorry, I can't move right now.", "by": pid, "moved": False}
    here = tuple(p["pos"])
    tiers = M.standing_cells(state, rid, pid, near=here)
    options = sorted((geometry.chebyshev(c, here), c[1], c[0]) for c, t in tiers.items() if t == 0 and c != here and c not in keep)
    if not options:
        C.person_says(w, pid, "There's nowhere for me to go.")
        return {"topic": topic, "value": "There's nowhere for me to go.", "by": pid, "moved": False}
    cell = [options[0][2], options[0][1]]
    w.person_m(pid)["pos"] = cell
    C.log_event(w, "stepped_aside", person=pid, frm=list(here), to=cell)
    C.person_says(w, pid, "Sure.")
    return {"topic": topic, "value": "Sure.", "by": pid, "moved": True}
