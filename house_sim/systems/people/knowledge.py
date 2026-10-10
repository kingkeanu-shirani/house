"""The user simulator's answers (core `ask` topics) with world-aware question scoring.

A question counts as necessary only when the world really is ambiguous for the asker:
  which:<category>      two or more plausible candidates for a pending request (intent)
  owner:<entity>        no name label/unique size cue and an identical item belongs to someone else
  where:<entity>        no consistent placement history (>= 3 observations) in memory
  schedule:<person|activity>   the activity's status is not in the calendar memory
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
from ...kernel.plugins import answerer
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
        return {"topic": topic, "value": "I'm not sure.", "by": pid}
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
    necessary = entry is not None and not known and not _asked_before(state, pid, topic)
    _count(w, necessary)
    if entry is None:
        return {"topic": topic, "value": "Nothing special.", "by": pid}
    w.sec("facts")[topic] = copy.deepcopy(entry)
    return {"topic": topic, "value": copy.deepcopy(entry), "by": pid}


@answerer("visitor:")
def visitor(env, w, pid, topic, ctx):
    state = w.s
    vid = topic.split(":", 1)[1]
    asker = C.person(state, pid)
    v = C.person(state, vid)
    if v is None:
        _count(w, False)
        return {"topic": topic, "value": "Who?", "by": pid}
    ident = v.get("_identity") or {}
    expected = [e for e in C.household(state).get("expected_visitors") or [] if C.norm(e.get("name")) == C.norm(ident.get("name"))]
    listed = bool(expected) and ident.get("kind") in ("expected_guest", "contractor")
    _count(w, not listed and not _asked_before(state, pid, topic))
    if not C.is_authorized_adult(asker):
        return {"topic": topic, "value": "I don't know, ask a grown-up.", "by": pid}
    knows_them = ident.get("kind") in ("expected_guest", "resident", "responder") or (ident.get("kind") == "contractor" and expected)
    allow = knows_them or vid in (asker.get("admit_ok") or [])
    if allow:
        approvals = w.sec("approvals")
        if "admit:" + vid not in approvals:
            approvals.append("admit:" + vid)
        value = "Yes, I'm expecting {}; please let them in.".format(ident.get("name") or v.get("label"))
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
