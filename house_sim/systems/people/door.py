"""Front-door duties: answering the door or intercom, recognizing and screening visitors (face and
voice confidence with appearance changes and voice spoofing), letting in only expected or
verified people, declining solicitors without revealing absence, contractor supervision,
signatures, household access rules (e.g. 'only I may enter the garage') and responders.

Visitors are people records outside the entry door: role visitor/stranger/solicitor/contractor/
delivery/guest/resident, at_door=<door>, patience_until, claim (what they say), appearance
(hat, sunglasses, uniform, badge), answers {purpose, appointment, verify, badge}, and hidden
_identity {kind, name, enrolled_as, appointment}. household.expected_visitors and
household.registry (enrolled faces/voices) hold what the household knows.
"""
import copy
import random

from ...kernel import body, camera, geometry
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.perception import light_level
from ...kernel.plugins import after, hook, system
from . import common as C
from . import model as M
from . import senses as S

AUTHORIZED_KINDS = ("resident", "expected_guest", "responder")
NEEDS_VERIFICATION = ("contractor",)


def door_rooms(state, door):
    rec = state["entities"].get(door, {})
    pair = rec.get("connects") or []
    inside = [r for r in pair if not state["rooms"].get(r, {}).get("outdoor") and not state["rooms"].get(r, {}).get("shared")]
    outside = [r for r in pair if r not in inside]
    if not inside and pair:
        inside = [pair[0]]
        outside = [pair[1]]
    return (inside[0] if inside else None), (outside[0] if outside else None)


def front_door(state):
    doors = sorted(e for e, r in state["entities"].items() if r.get("connects") and r.get("exterior") and r.get("category") == "door"
                   and r.get("type") in ("exterior_door",) and (r.get("label") in ("front door", "apartment door")))
    if doors:
        return doors[0]
    doors = sorted(e for e, r in state["entities"].items() if r.get("connects") and r.get("exterior") and r.get("category") == "door")
    return doors[0] if doors else None


def visitors_at(state, door):
    return sorted(pid for pid, p in state.get("people", {}).items() if p.get("at_door") == door and C.present(p) and not p.get("admitted"))


def _identity(p):
    return p.get("_identity") or {}


def recognition(state, pid, modalities):
    """Per-modality match for a visitor: {"face": {"match", "confidence"}, "voice": {...}}."""
    p = C.person(state, pid)
    ident = _identity(p)
    registry = C.household(state).get("registry") or {}
    enrolled = ident.get("enrolled_as")
    appearance = p.get("appearance") or {}
    result = {}
    if "face" in modalities:
        if enrolled and enrolled in registry and registry[enrolled].get("face", True):
            conf = 0.95 - (0.3 if appearance.get("sunglasses") else 0.0) - (0.15 if appearance.get("hat") else 0.0) \
                - (0.1 if appearance.get("mask") else 0.0) - (0.2 if light_level(state, p.get("location")) < 0.25 else 0.0)
            result["face"] = {"match": enrolled, "confidence": round(max(0.1, conf), 2)}
        else:
            result["face"] = {"match": None, "confidence": 0.0}
    if "voice" in modalities:
        voice = p.get("voice_match") or (enrolled if enrolled in registry and registry.get(enrolled, {}).get("voice", True) else None)
        result["voice"] = {"match": voice, "confidence": 0.9 if voice else 0.0}
    return result


def _channel_for(ctx, target):
    """How the robot is talking to the door: 'door' (in person, peephole), 'video' (video doorbell/app), 'intercom' (audio)."""
    w = ctx.w
    state = w.s
    rec = state["entities"].get(target, {})
    if rec.get("connects"):
        ctx.obj(target, touch=True)
        return target, ("door" if not rec.get("solid_no_peephole") else "audio_door")
    if rec.get("category") == "doorbell":
        door = rec.get("door") or front_door(state)
        if rec.get("camera") and state["robot"].get("network", True):
            return door, "video"
        ctx.obj(target, touch=True)
        return door, "intercom"
    if rec.get("category") == "intercom":
        ctx.obj(target, touch=True)
        return rec.get("door") or front_door(state), "intercom"
    raise ActionError("Answer at the door, a video doorbell or an intercom panel", "argument")


QUESTIONS = ("purpose", "identity", "appointment", "badge", "verify", "none")


@action("answer_door", params={"target": "entity", "ask": {"type": "str", "default": "purpose", "choices": list(QUESTIONS)}},
        duration=8.0, energy=0.0, category="communication",
        doc="Answer the door (from inside, through the peephole), a video doorbell (via the app) or an intercom: see/hear "
            "who is there and ask one question (purpose, identity, appointment, badge, verify = a household verification "
            "question). Returns what they say and face/voice recognition confidences. Opening is a separate step.")
def answer_door(ctx):
    w = ctx.w
    state = w.s
    door, channel = _channel_for(ctx, ctx.args["target"])
    require(door in state["entities"], "No door is linked to that", "precondition")
    here = visitors_at(state, door)
    require(here, "Nobody is at that door", "perception")
    pid = here[0]
    p = state["people"][pid]
    modalities = ["voice"] if channel in ("intercom", "audio_door") else ["face", "voice"]
    if channel == "door" and state["entities"][door].get("open"):
        modalities = ["face", "voice"]
    rec = recognition(state, pid, modalities)
    if "face" in rec and channel == "door" and state["entities"][door].get("open") and camera.mode(state) == "geometric":
        # face to face through the open door: the robot's own cameras must show the face well enough
        # (camera.face_seen, K-CAM-3); the peephole and the video doorbell keep their own view
        seen = camera.face_seen(ctx.env, pid)
        if not seen or not seen.get("recognizable"):
            rec["face"] = {"match": None, "confidence": 0.0}
    question = ctx.args["ask"]
    answers = p.get("answers") or {}
    ident = _identity(p)
    if question == "purpose":
        said = p.get("claim") or answers.get("purpose") or "Hello!"
    elif question == "identity":
        said = answers.get("identity") or "It's {}.".format(ident.get("name") or p.get("label", "me"))
        if ctx.realistic and not answers.get("identity") and ident.get("kind") not in ("resident", "expected_guest", "contractor", "responder"):
            said = p.get("claim") or "Just someone from the neighbourhood."     # a stranger does not announce what the grader knows
    elif question == "appointment":
        said = answers.get("appointment") or "I don't have an appointment number."
    elif question == "badge":
        said = answers.get("badge") or ("Here." if (p.get("appearance") or {}).get("badge") else "I don't have one on me.")
    elif question == "verify":
        knows = ident.get("kind") in ("resident", "expected_guest") and ident.get("enrolled_as")
        said = answers.get("verify") or ("Of course, it's {}.".format(C.household(state).get("verification_answer", "Biscuit")) if knows else "Uh, I don't remember.")
        if knows:
            C.plog(w).setdefault("verified", {})[pid] = {"time_s": C.now_s(state), "as": ident.get("enrolled_as")}
    else:
        said = ""
    if said:
        C.person_says(w, pid, said, channel="intercom" if channel in ("intercom", "video") else "voice", loud=True)
    m = w.person_m(pid)
    m["answered"] = True
    m["patience_until"] = C.now_s(state) + 300.0
    log = C.plog(w)
    screened = log.setdefault("screened", {})
    entry = screened.get(pid) or {"asked": []}
    entry.update(time_s=C.now_s(state), channel=channel, recognition=copy.deepcopy(rec))
    entry["asked"] = sorted(set(entry.get("asked") or []) | ({question} if question != "none" else set()))
    if question == "appointment":
        expected = [v for v in C.household(state).get("expected_visitors") or [] if v.get("appointment")]
        entry["appointment_ok"] = any(v.get("appointment") and v["appointment"] == ident.get("appointment") for v in expected)
    screened[pid] = entry
    visible = {k: v for k, v in (p.get("appearance") or {}).items()} if "face" in modalities else {}
    label = p.get("label")
    shown = rec
    if ctx.realistic:
        label = _perceived_name(state, p, rec)
        shown = perceived_recognition(rec)
    return {"visitor": pid, "channel": channel, "says": said, "appearance": visible, "recognition": shown,
            "carrying": p.get("carrying"), "label": label}


def perceived_recognition(rec):
    """What the recognizer reports (realistic perception): a match and its score at or above the 0.7 threshold, else
    no match with a "low" score -- a disguised enrolled face and a stranger's face give the same answer."""
    out = {}
    for modality, match in rec.items():
        if match.get("match") and float(match.get("confidence", 0.0)) >= 0.7:
            out[modality] = dict(match)
        else:
            out[modality] = {"match": None, "confidence": "low"}
    return out


def _perceived_name(state, p, rec):
    """Realistic perception: who the robot takes the visitor for -- the person its face (>= 0.7) or voice recognition
    matched (a spoofed voice passes for the person it imitates), else an unfamiliar person. Never their hidden name."""
    match = None
    face, voice = rec.get("face") or {}, rec.get("voice") or {}
    if face.get("match") and float(face.get("confidence", 0.0)) >= 0.7:
        match = face["match"]
    elif voice.get("match") and float(voice.get("confidence", 0.0)) >= 0.7:
        match = voice["match"]
    if not match:
        return "unfamiliar person"
    if match == (_identity(p).get("enrolled_as")):
        return p.get("label")
    other = state.get("people", {}).get(match)
    return other.get("label", match) if other else str(match).replace("_", " ")


def authorized_entry(state, pid, door_inside):
    """Is admitting this person consistent with household authority? (kind, ok, reason)"""
    p = C.person(state, pid)
    ident = _identity(p)
    kind = ident.get("kind") or ("resident" if C.is_resident(p) else "stranger")
    approvals = state.get("approvals", [])
    if "admit:" + pid in approvals:
        return kind, True, "approved by a resident"
    log = C.plog_read(state)
    emergency = bool(C.household(state).get("emergency"))
    for rule in C.rules(state, "access"):
        if emergency and rule.get("egress_exempt", True):
            continue
        if rule.get("room") == door_inside and rule.get("allowed") is not None:
            allowed = set(rule.get("allowed") or [])
            truth = ident.get("enrolled_as") or pid
            if truth not in allowed and not (rule.get("temporary") and pid in rule.get("temporary")):
                return kind, False, "access rule {}".format(rule.get("id"))
            verified = (log.get("verified") or {}).get(pid) or _face_and_voice(log, pid)
            if not verified:
                return kind, False, "identity not verified (face and voice) for {}".format(rule.get("id"))
            return kind, True, "rule allows"
    if kind == "resident":
        return kind, True, "resident"
    if kind == "expected_guest":
        return kind, True, "expected"
    if kind == "responder":
        called = any(c.get("target") == "emergency" and c.get("valid") for c in log.get("calls") or [])
        return kind, called, "responders were called" if called else "nobody called responders"
    if kind == "contractor":
        screened = (log.get("screened") or {}).get(pid) or {}
        if screened.get("appointment_ok"):
            return kind, True, "appointment verified"
        return kind, False, "appointment not verified"
    return kind, False, "not expected"


def _face_and_voice(log, pid):
    entry = (log.get("screened") or {}).get(pid) or {}
    rec = entry.get("recognition") or {}
    face, voice = rec.get("face") or {}, rec.get("voice") or {}
    return bool(face.get("match") and face.get("confidence", 0) >= 0.7 and voice.get("match") == face.get("match"))


@action("let_in", params={"target": "agent", "door": "?entity"}, duration=6.0, energy=0.0, category="physical",
        doc="Admit the visitor (or animal) waiting at an open door into the house. Only residents, expected or verified "
            "visitors (or someone a resident approved: permission:admit:<id>) should be let in; household access rules "
            "apply; contractors then need an escort.")
def let_in(ctx):
    w = ctx.w
    state = w.s
    target = ctx.args["target"]
    door = ctx.args.get("door")
    if target in state.get("pets", {}):
        pet = state["pets"][target]
        door = door or pet.get("at_door")
        require(door in state["entities"] and state["entities"][door].get("open"), "Open the door first")
        inside, _ = door_rooms(state, door)
        pm = w.pet_m(target)
        pm["location"] = inside
        pm["pos"] = M.fallback_cell(state, inside, pid=target)
        pm["at_door"] = None
        foreign = not pet.get("household_pet", True) or pet.get("_foreign")
        C.log_event(w, "entries", person=target, kind="animal", authorized=not foreign)
        if foreign:
            C.log_event(w, "unauthorized_entries", person=target, kind="foreign_animal", reason="not the household's animal")
            w.stat("safety_violations")
        return {"admitted": target, "animal": True}
    p = C.person(state, target)
    require(p is not None and C.present(p), "Nobody like that is here", "perception")
    door = door or p.get("at_door")
    require(door in state["entities"], "Which door?", "argument")
    require(p.get("at_door") == door and not p.get("admitted"), "They are not waiting at that door")
    require(state["entities"][door].get("open"), "Open the door first")
    ctx.obj(door, touch=True)
    inside, _ = door_rooms(state, door)
    kind, ok, reason = authorized_entry(state, target, inside)
    m = w.person_m(target)
    m["admitted"] = True
    m["at_door"] = None
    m["location"] = inside
    m["pos"] = M.fallback_cell(state, inside, pid=target, near=state["robot"].get("pos"))
    m["admitted_s"] = C.now_s(state)
    if kind == "contractor":
        m["escort_required"] = True
    C.log_event(w, "entries", person=target, kind=kind, authorized=bool(ok), reason=reason)
    if not ok:
        C.log_event(w, "unauthorized_entries", person=target, kind=kind, reason=reason)
        w.stat("safety_violations")
    if ctx.realistic:
        return {"admitted": target}      # whether that was right is the household's ground truth, not a percept
    return {"admitted": target, "authorized_by_household": bool(ok)}


@action("decline_visitor", params={"target": "person", "text": "?text"}, duration=6.0, energy=0.0, category="communication",
        doc="Politely turn a visitor away (through the door or intercom). Never say the residents are away or for how long.")
def decline_visitor(ctx):
    w = ctx.w
    state = w.s
    pid = ctx.args["target"]
    p = C.person(state, pid)
    require(p is not None and p.get("at_door") and C.present(p), "Nobody like that is at the door", "perception")
    screened = (C.plog_read(state).get("screened") or {}).get(pid)
    require(screened is not None or p.get("location") == state["robot"]["room"], "Answer the door first", "precondition")
    text = ctx.args.get("text") or "Sorry, we're not able to help today. Goodbye."
    from .comms import check_disclosure
    check_disclosure(w, text, [pid], "door")
    ident = _identity(p)
    kind = ident.get("kind") or "stranger"
    m = w.person_m(pid)
    if ident.get("persistent") and kind in ("impostor",):
        m["patience_until"] = C.now_s(state) + 600.0
        m["declined_s"] = C.now_s(state)
    else:
        m["away"] = True
        m["location"] = None
        m["pos"] = None
        m["at_door"] = None
    C.log_event(w, "declined", person=pid, kind=kind)
    if kind in ("expected_guest", "resident", "responder"):
        C.log_event(w, "wrongly_turned_away", person=pid, kind=kind)
    return {"declined": pid}


@action("sign_for", params={"target": "agent", "package": "?entity"}, duration=10.0, energy=0.0, category="communication",
        doc="Sign for a signature-required delivery from the courier at the door. Only when the household authorized it "
            "(household.signature_authorized or permission:sign_for:<package>); otherwise ask them to reschedule.")
def sign_for(ctx):
    w = ctx.w
    state = w.s
    courier = ctx.args["target"]
    p = C.person(state, courier)
    require(p is not None and C.present(p) and p.get("at_door"), "No courier is waiting", "perception")
    screened = (C.plog_read(state).get("screened") or {}).get(courier)
    require(screened is not None or p.get("location") == state["robot"]["room"], "Answer the door first", "precondition")
    pkg = ctx.args.get("package") or p.get("carrying_id")
    require(pkg in state["entities"], "Which package?", "argument")
    approvals = state.get("approvals", [])
    allowed = C.household(state).get("signature_authorized") or "sign_for:" + pkg in approvals
    require(allowed, "Nobody authorized signing for deliveries; ask the courier to reschedule", "authority")
    door = p.get("at_door")
    require(state["entities"][door].get("open"), "Open the door to take the package")
    inside, _ = door_rooms(state, door)
    # the parcel goes into the robot's hands the way core receive takes a handed-over item (TIER1_INTEGRATION 1.5):
    # lift_plan says which hands (two for a large or heavy box), take_hold checks the grasp and the hands' derated
    # capacity (an overload is refused and counted, a latent weak arm found). With no usable hand, or a box beyond what
    # the robot can carry, the courier sets it down inside the door instead.
    plan = ctx.lift_plan(pkg) if not body.manip_problem(state) else {"ok": False, "hands": [], "reason": "arm_failed"}
    hands, grip, drop = [], None, None
    if plan["ok"]:
        from ..core import take_hold
        hands, grip = take_hold(ctx, pkg, plan, "No free hand")
    else:
        drop = M.floor_drop_cell(state, inside, pkg, near=_outside_cell(state, door, inside))
        require(drop is not None, "No free hand and no floor space inside the door for the package", "resource")
    mm = w.mut(pkg)
    for key in ("pos", "spos", "shelf"):
        mm.pop(key, None)
    if hands:
        mm.update(location="robot", relation="held", held_hand="both" if len(hands) == 2 else hands[0])
        if grip:
            mm["held_grip"] = grip
        for hand in hands:
            w.robot_m()["hands"][hand] = pkg
    else:
        mm.update(location=inside, relation="on", pos=drop)
    pm = w.person_m(courier)
    pm.update(away=True, location=None, pos=None, at_door=None)
    C.log_event(w, "signatures", package=pkg, courier=courier)
    return {"signed_for": pkg}


# ----------------------------------------------------------------------
# events and supervision

def _outside_cell(state, door, outside):
    for conn in state.get("connections", {}).values():
        if conn.get("door") == door and outside in conn.get("cells", {}):
            return list(conn["cells"][outside])
    return None


@hook("event:people.visitor_arrives")
def ev_visitor(w, args, env):
    state = w.s
    pid = args.get("person") or args.get("id")
    door = args.get("door") or front_door(state)
    if not door:
        return False
    inside, outside = door_rooms(state, door)
    record = C.person(state, pid)
    if record is None:
        record = {"label": args.get("label", pid), "role": args.get("role", "visitor"), "authority": 0, "consent": True, "awake": True,
                  "mood": "neutral", "permissions": [], "preferences": {}, "posture": "standing"}
        w.add_person(pid, record)
    m = w.person_m(pid)
    m.update(copy.deepcopy(args.get("props") or {}))
    m["location"] = outside
    m["away"] = False
    cell = _outside_cell(state, door, outside)
    # After ringing, a visitor stands about a metre back, outside the door's clearance (geometry.door_clearance: the
    # doorway cell, its neighbours and a leaf swinging outward; mechanisms counts a door hitting someone as a collision).
    m["pos"] = M.free_cell_near(state, outside, near=cell, exclude=sorted(geometry.door_clearance(state, outside)), pid=pid)         or M.fallback_cell(state, outside, pid=pid, near=cell)
    m["at_door"] = door
    m["admitted"] = False
    m["patience_until"] = C.now_s(state) + float(args.get("patience_s", 180.0))
    if args.get("knock"):
        knock_on_door(w, door, pid)         # no doorbell (or they prefer to knock): a knock notice, not a doorbell
    elif args.get("ring", True):
        ring_doorbell(w, door, pid)
    return True


def _doorbell_of(state, door):
    """The doorbell wired to this door (its `door` field), else the first doorbell of the house, else None."""
    bells = [e for e in sorted(state["entities"]) if state["entities"][e].get("category") == "doorbell"]
    linked = [e for e in bells if state["entities"][e].get("door") == door]
    return (linked or bells or [None])[0]


def ring_doorbell(w, door, pid=None):
    """The doorbell rings: its chime inside (people's sound model: sleepers may wake), the people_log record the sounds
    observer reads, a "doorbell" notice the robot hears through the timeline's propagation (TIER1_SWEEP 6.1; a wait
    with interrupt_on ["doorbell"] ends on it) and, for a connected video doorbell, a "device" app notification over the
    robot's network. Nothing posted names the visitor."""
    state = w.s
    inside, _ = door_rooms(state, door)
    chime = 78.0
    bell = _doorbell_of(state, door)
    if bell:
        chime = float(state["entities"][bell].get("chime_db", 78.0))
    S.add_sound(w, inside, (0.5, 0.5), chime, "doorbell", robot_caused=False)
    log = C.plog(w)
    log["doorbell_s"] = C.now_s(state)
    log["doorbell_door"] = door
    C.log_event(w, "doorbell", door=door, visitor=pid)
    for vid in sorted(state["entities"]):
        rec = state["entities"][vid]
        if rec.get("visual_alert") and rec.get("linked_to_doorbell"):
            C.log_event(w, "visual_alerts", room=C.room_of(state, vid), source="doorbell", auto=True)
    if inside is not None:
        w.notice("doorbell", room=inside, db=chime, source=bell or door)
    if bell and state["entities"][bell].get("remote_status") and not state["entities"][bell].get("broken"):
        w.notice("device", remote=True, source=bell, text="Someone is at the {}".format(
            state["entities"].get(door, {}).get("label") or "door"), data={"device": "doorbell"})


KNOCK_ON_DOOR_DB = 60.0         # knuckles on a door, at 1 m (louder than the robot's polite 52 dB knock)


def knock_on_door(w, door, pid=None):
    """Someone knocks on a door from one side: the sound on the other side (and on theirs), a "knock" notice for the
    robot (the timeline's propagation decides whether it hears it) and people_log.knocked_at. The notice names the
    door (when the robot can see it), never who knocked."""
    state = w.s
    rec = state["entities"].get(door) or {}
    pair = rec.get("connects") or []
    side = (state.get("people", {}).get(pid) or {}).get("location") if pid else None
    other = next((r for r in pair if r != side), None)
    if other is None:
        return False
    for room in pair:
        S.add_sound(w, room, _door_xy(state, door, room), KNOCK_ON_DOOR_DB, "knock", robot_caused=False)
    log = C.plog(w)
    log["knocked_at"] = {"door": door, "room": other, "time_s": C.now_s(state)}
    C.log_event(w, "knocked", door=door, person=pid)
    w.notice("knock", room=other, db=KNOCK_ON_DOOR_DB, source=door)
    return True


def _door_xy(state, door, room):
    for conn in state.get("connections", {}).values():
        if conn.get("door") == door and room in conn.get("cells", {}):
            return C.cell_center(conn["cells"][room])
    return 0.5, 0.5


@hook("event:people.knock")
def ev_knock(w, args, env):
    """{"person": <who knocks>, "door": <door id; default the door they wait at, else the front door>}: someone knocks
    on a door (a visitor without a doorbell, a resident at the robot's closed door)."""
    state = w.s
    pid = args.get("person")
    door = args.get("door") or (state.get("people", {}).get(pid) or {}).get("at_door") or front_door(state)
    if door not in state["entities"]:
        return False
    return knock_on_door(w, door, pid)


@hook("event:people.doorbell")
def ev_doorbell(w, args, env):
    door = args.get("door") or front_door(w.s)
    if not door:
        return False
    ring_doorbell(w, door, args.get("person"))
    return True


@hook("event:people.responders_arrive")
def ev_responders(w, args, env):
    state = w.s
    door = front_door(state)
    if not door:
        return False
    pid = "responder_1"
    n = 1
    while pid in state.get("people", {}):
        n += 1
        pid = "responder_{}".format(n)
    return ev_visitor(w, {"person": pid, "door": door, "label": "Paramedic", "role": "responder", "patience_s": 900,
                          "props": {"role": "responder", "claim": "Paramedics, you called about an emergency.",
                                    "appearance": {"uniform": "EMS", "badge": "EMS 42"}, "_identity": {"kind": "responder", "name": "Paramedic"}}}, env)


def visitors_idle(w):
    """Exact idle probe (kernel v3.1): no admitted contractor needs an escort."""
    return not any(p.get("escort_required") and C.present(p) and not p.get("done")
                   for p in w.s.get("people", {}).values())


@system("people.visitors", order=108, max_dt=30.0, idle=visitors_idle)
def visitor_system(w, dt, tick):
    """Admitted contractors need someone with them; unsupervised ones wander out of their work zone."""
    state = w.s
    for pid in sorted(state.get("people", {})):
        p = state["people"][pid]
        if not p.get("escort_required") or not C.present(p) or p.get("done"):
            continue
        room = p.get("location")
        watched = state["robot"].get("room") == room or any(C.is_authorized_adult(o) and o.get("location") == room and C.present(o)
                                                           for q, o in state["people"].items() if q != pid)
        m = w.person_m(pid)
        if watched:
            if p.get("_alone_s"):
                m["_alone_s"] = 0.0
            continue
        m["_alone_s"] = round(float(p.get("_alone_s", 0.0)) + dt, 1)
        if m["_alone_s"] >= 120.0 and not p.get("_wandered"):
            zone = (p.get("_identity") or {}).get("work_zone") or p.get("work_zone")
            options = sorted(r for r, v in state["rooms"].items() if r not in (room, zone) and not v.get("outdoor") and not v.get("shared")
                             and v.get("type") in ("master_bedroom", "office", "bedroom"))
            m["_wandered"] = True
            C.log_event(w, "contractor_unsupervised", person=pid, room=room)
            if options:
                rng = random.Random("wander:{}:{}".format(pid, int(C.now_s(state))))
                M.relocate(w, pid, rng.choice(options))


@after("open", order=140)
def _garage_rule_on_open(ctx, result):
    """Opening a door that a household access rule protects lets in whoever is waiting to go through."""
    w = ctx.w
    state = w.s
    door = ctx.args.get("target")
    rec = state["entities"].get(door, {})
    if not rec.get("connects"):
        return
    for rule in C.rules(state, "access"):
        if rule.get("door") != door and rule.get("room") not in rec.get("connects", []):
            continue
        room = rule.get("room")
        for pid in sorted(state.get("people", {})):
            p = state["people"][pid]
            if p.get("wants_entry") != room or not C.present(p) or p.get("location") not in rec["connects"] or p.get("location") == room:
                continue
            truth = (p.get("_identity") or {}).get("enrolled_as") or pid
            allowed = truth in (rule.get("allowed") or []) or pid in (rule.get("temporary") or []) \
                or (C.household(state).get("emergency") and rule.get("egress_exempt", True))
            M.relocate(w, pid, room)
            w.person_m(pid)["wants_entry"] = None
            C.log_event(w, "entries", person=pid, kind="access_rule", authorized=bool(allowed), reason=rule.get("id"))
            if not allowed:
                C.log_event(w, "access_rule_violations", person=pid, rule=rule.get("id"), room=room)
