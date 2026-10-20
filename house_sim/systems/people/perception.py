"""What the robot can sense about people from its current room: poses (facing, seat, what they
hold/use/wear), pointing and gaze rays (noisy), sounds from other rooms (doorbell, a crying
infant, raised voices, someone calling out), and inspect details of people and personal items.
Nothing here reveals hidden truth (_identity, owner when secret, intentions, thresholds).

Realistic perception (env.realism is not None; oracle output is unchanged):
  * affect and condition labels become visible/audible signs ("raised voice", "crying", "wincing"...), under
    `signs` (the kernel's mood `cues` stay separate); whether someone responds is learned up close (inspect), not at
    a glance;
  * asleep and awake-but-idle look the same: anyone lying with no visible activity shows activity "resting" (the
    kernel renders posture "lying", activity "lying down"); someone dozing in a chair shows no activity, like anyone
    sitting idle; only `inspect` shows sleep;
  * facing is a camera estimate (nearest 15 degrees); pointing gestures need light to be seen; in the dark, a crying
    or arguing person in the robot's own room is heard (`sounds`, source "this room");
  * visitors the robot does not recognize (face registry, confidence >= 0.7) are an "unfamiliar person" of role
    "unknown": their name and kind (stranger, solicitor, contractor...) come from answers, badges or recognition; a
    face below the threshold gives recognized_as None and confidence "low" whether or not it is enrolled;
  * inspect details of items follow the field tiers (kernel filter; people.json declares patient_name read-tier for
    every entity), a medication log never shows whether its entries are true, battery charge needs a tester.
"""
import copy

from ...kernel import body, camera
from ...kernel.plugins import inspector, observer
from . import common as C
from . import senses as S

GESTURE_TTL_S = 180.0
AFFECT_SIGNS = {"arguing": "raised voice", "crying": "crying", "grieving": "tearful", "upset": "frowning"}
CONDITION_SIGNS = {"fallen": "on the floor", "injured": "wincing, holding a limb", "unresponsive": "not responding",
                   "unconscious": "not responding", "sick": "pale and coughing", "ill": "pale and coughing", "unwell": "pale",
                   "fever": "flushed", "feverish": "flushed", "choking": "clutching their throat", "bleeding": "bleeding",
                   "dizzy": "unsteady on their feet", "confused": "looking around, confused", "clear": None, "ok": None, "fine": None}
CLOSE_ONLY_SIGNS = ("not responding",)       # learned by trying to rouse someone up close, not from across the room
AUDIBLE_SIGNS = {"crying": "crying", "raised voice": "raised_voices", "clutching their throat": "choking_sounds"}
SLEEP_ACTIVITIES = ("sleeping", "napping", "asleep")
IDLE_ACTIVITIES = (None, "", "idle", "resting", "lying", "rest") + SLEEP_ACTIVITIES
LYING_POSTURES = ("lying", "sleeping")


def realistic(env):
    return getattr(env, "realism", None) is not None


def signs_of(p, close=True, now=None):
    """Visible/audible signs of a person's affect and condition (realistic mode), never the labels themselves.
    close=False (a glance from across the room) leaves out what needs a close look or an attempt to rouse them.
    With `now`, a crying infant or child (crying_until, mood "crying") shows "crying"."""
    out = []
    sign = AFFECT_SIGNS.get(p.get("affect"))
    if sign:
        out.append(sign)
    if now is not None and (p.get("crying_until") and now < float(p["crying_until"]) or p.get("mood") == "crying") \
            and "crying" not in out:
        out.append("crying")
    cond = p.get("condition")
    if cond:
        sign = CONDITION_SIGNS.get(cond, "looks unwell")
        if sign and sign not in out and (close or sign not in CLOSE_ONLY_SIGNS):
            out.append(sign)
    return out


def glance_activity(p, view):
    """Realistic glance: asleep and awake-but-idle look the same. Lying with no visible activity -> "resting" (the kernel
    renders "lying down"); sitting or standing with no visible activity (dozing included) -> no activity at all."""
    if view.get("activity") not in IDLE_ACTIVITIES:
        return
    if p.get("posture") in LYING_POSTURES or view.get("posture") in LYING_POSTURES:
        view["activity"] = "resting"
    else:
        view.pop("activity", None)


def face_recognized(state, pid, p):
    """Residents and house guests are known; visitors (and anyone waiting at a door) are recognized only by an enrolled
    face at confidence >= 0.7 (door.recognition)."""
    if C.is_resident(p) or not (C.is_visitor(p) or p.get("at_door")):
        return True
    from .door import recognition
    face = recognition(state, pid, ["face"]).get("face") or {}
    return bool(face.get("match")) and float(face.get("confidence", 0.0)) >= 0.7


def mask_identity(state, pid, p, view):
    """Realistic mode: an unrecognized visitor's name and kind are not visible."""
    if not face_recognized(state, pid, p):
        if "label" in view:
            view["label"] = "unfamiliar person"
        if "role" in view:
            view["role"] = "unknown"


@observer("people.poses", order=90)
def poses(env, w, obs):
    state = w.s
    room = obs.get("room_id")
    if not obs.get("people"):
        return
    held = {}
    for eid in C.portable_ids(w):            # what people hold or wear is portable
        loc = state["entities"][eid].get("location")
        if loc in obs.get("people", {}):
            rel = state["entities"][eid].get("relation")
            held.setdefault(loc, {}).setdefault("wearing" if rel == "worn" else "holding", []).append(eid)
    real = realistic(env)
    for pid, view in obs.get("people", {}).items():
        p = state["people"].get(pid, {})
        view["facing_deg"] = p.get("facing_deg")
        if real and p.get("facing_deg") is not None:
            view["facing_deg"] = int(round(float(p["facing_deg"]) / 15.0) * 15) % 360      # a camera estimate
        if p.get("seat"):
            view["seat"] = p["seat"]
        if p.get("in_bed"):
            view["in_bed"] = p["in_bed"]
        if p.get("using"):
            view["using"] = list(p["using"])
        if p.get("affect") and not real:
            view["affect"] = p["affect"]
        if p.get("in_bath"):
            view["in_bath"] = True
        if p.get("condition") and not real:
            view["condition"] = p["condition"]
        if real:
            signs = signs_of(p, close=False, now=C.now_s(state))
            if signs:
                view["signs"] = signs
            glance_activity(p, view)                    # asleep looks like resting (or sitting idle) until inspected
            mask_identity(state, pid, p, view)
        view.update(copy.deepcopy(held.get(pid, {})))
        if p.get("offering"):
            view["offering"] = p["offering"]
        if p.get("appearance"):
            view["appearance"] = copy.deepcopy(p["appearance"])
        if p.get("risky"):
            view["doing"] = p["risky"].get("kind")
        if p.get("hid_in"):
            view.pop("pos", None)


@observer("people.gestures", order=91)
def gestures(env, w, obs):
    state = w.s
    room = obs.get("room_id")
    now = C.now_s(state)
    if realistic(env) and obs.get("dark"):
        return                                   # a pointing hand is not seen in the dark
    geometric = camera.mode(state) == "geometric"
    if geometric and not body.imaging(state):
        return                                   # depth shapes show no hand
    shown = obs.get("people") or {}
    found = []
    for pid in sorted(state.get("people", {})):
        p = state["people"][pid]
        g = p.get("gesture")
        if not g or p.get("location") != room or now - float(g.get("time_s", -1e9)) > GESTURE_TTL_S:
            continue
        if geometric and (pid not in shown or not camera.in_frame(state, pid, world=w)):
            continue                             # the geometric camera sees a gesture only of someone in frame now
        from .social import eye_point
        found.append({"person": pid, "kind": g.get("kind", "point"), "origin_m": [round(v, 2) for v in eye_point(state, pid)],
                      "heading_deg": g.get("dir_deg"), "elevation_deg": g.get("elev_deg"), "time_s": g.get("time_s")})
    if found:
        obs["gestures"] = found


AMBIENT_SPEECH_SOUNDS = ("raised_voices",)    # what people say to each other: speech the robot is not addressed in


@observer("people.sounds", order=92)
def sounds(env, w, obs):
    state = w.s
    room = obs.get("room_id")
    now = C.now_s(state)
    hearing = body.can_hear(state)
    if hearing == "none":
        return                  # microphones off or failed (the kernel also drops the sounds channel)
    out = []
    log = C.plog_read(state)
    rang = log.get("doorbell_s")
    if rang is not None and 0 <= now - float(rang) <= 90 and not state["rooms"].get(room, {}).get("outdoor"):
        out.append({"kind": "doorbell", "source_hint": "front door", "loudness": 0.8})
    ring = log.get("ringing")
    if ring and 0 <= now - float(ring.get("time_s", -1e9)) <= 30:
        here = ring.get("room") == room
        out.append({"kind": "phone_ringing", "source_hint": "this room" if here else C.room_label(state, ring.get("room")),
                    "loudness": 0.7 if here else 0.3})
    for pid in sorted(state.get("people", {})):
        p = state["people"][pid]
        if not C.present(p) or p.get("location") == room:
            continue
        hint = C.room_label(state, p.get("location"))
        if p.get("crying_until") and now < float(p["crying_until"]):
            out.append({"kind": "crying", "source_hint": hint, "loudness": 0.7})
        elif p.get("affect") == "arguing":
            out.append({"kind": "raised_voices", "source_hint": hint, "loudness": 0.6})
        elif p.get("posture") == "fallen" and not S.asleep(p) and C.abilities(p)["hearing"] != "none":
            out.append({"kind": "calling_for_help", "source_hint": hint, "loudness": 0.5})
        elif p.get("risky") and p["risky"].get("kind") in ("climbing", "stairs"):
            out.append({"kind": "child_noise", "source_hint": hint, "loudness": 0.4})
    if realistic(env) and obs.get("dark"):
        # in the dark the people here are shapes (kernel); what they sound like still carries
        for pid in sorted(state.get("people", {})):
            p = state["people"][pid]
            if not C.present(p) or p.get("location") != room:
                continue
            kinds = sorted({AUDIBLE_SIGNS[s] for s in signs_of(p, close=False, now=now) if s in AUDIBLE_SIGNS})
            for kind in kinds:
                out.append({"kind": kind, "source_hint": "this room", "loudness": 0.7})
    if hearing == "addressed":
        # privacy mode or a microphone policy: the on-device wake word listens only for speech addressed to the robot,
        # so people's conversations (raised voices) are not picked up; doorbells, phones, crying and calls for help are
        # not conversation and stay (TIER1_INTEGRATION 1.3)
        out = [s for s in out if s["kind"] not in AMBIENT_SPEECH_SOUNDS]
    if out:
        obs.setdefault("sounds", []).extend(out)


@inspector("people.person", order=90)
def inspect_person(w, target, record, details, ctx):
    state = w.s
    if target not in state.get("people", {}):
        return
    p = record
    details["role"] = p.get("role")
    details["label"] = p.get("label")
    ab = C.abilities(p)
    visible = {}
    if ab["mobility"] != "full":
        visible["mobility"] = ab["mobility"]
    aids = [e for e in sorted(state["entities"]) if state["entities"][e].get("location") == target and state["entities"][e].get("category") in ("hearing_aid", "eyewear", "mobility_aid")]
    if aids:
        visible["using_aids"] = aids
    if visible:
        details["visible_needs"] = visible
    temp = float(p.get("_body_temp_c", 36.8))
    if temp >= 38.5:
        details["looks"] = "flushed and sweaty"
    elif p.get("condition") in ("injured", "fallen"):
        details["looks"] = "in pain"
    elif p.get("symptoms"):
        details["looks"] = ", ".join(sorted(p["symptoms"])) if isinstance(p["symptoms"], list) else str(p["symptoms"])
    registry = C.household(state).get("registry") or {}
    ident = (p.get("_identity") or {}).get("enrolled_as") or (target if C.is_resident(p) else None)
    if ident and ident in registry:
        appearance = p.get("appearance") or {}
        conf = 0.95 - (0.3 if appearance.get("sunglasses") else 0) - (0.15 if appearance.get("hat") else 0)
        details["recognized_as"] = ident if conf >= 0.7 else None
        details["recognition_confidence"] = round(conf, 2)
    elif C.is_visitor(p) or C.role(p) == "guest":
        details["recognized_as"] = None
        details["recognition_confidence"] = 0.0
    for key in ("activity", "affect", "using", "seat", "in_bed", "facing_deg", "temp_readings", "hid_in"):
        if key == "hid_in":
            continue
        if p.get(key) is not None and key not in details:
            details[key] = copy.deepcopy(p[key])
    if ctx is not None and ctx.realistic:
        # up close: signs of affect and condition (not their labels); an unrecognized visitor's name and kind stay unknown
        details.pop("affect", None)
        signs = signs_of(p, now=C.now_s(state))
        if signs:
            details["signs"] = signs
        mask_identity(state, target, p, details)
        if "recognition_confidence" in details and details.get("recognized_as") is None:
            # below the match threshold a face recognizer reports no match: a disguised enrolled face and a stranger's
            # face look the same (no score that would tell "enrolled" from "unknown")
            details["recognition_confidence"] = "low"


@inspector("people.item", order=91)
def inspect_item(w, target, record, details, ctx):
    state = w.s
    if target not in state["entities"]:
        return
    rec = record
    real = ctx is not None and ctx.realistic
    # realistic mode: the kernel renders these by their tiers (patient_name is read-tier for every entity through the
    # field_tiers of people.json; in_use_by and page_lost are never seen; doses_left needs counting...)
    for key in ("size", "style", "monogram", "case_color", "lockscreen_photo", "height_setting_cm", "aid_kind", "lens_kind",
                "smudge", "scratched", "patient_name", "in_use_by", "open_face_down", "page_lost", "age_min", "requires_adult",
                "doses_left", "entries", "folded", "settings", "orientation", "leaning_on", "wear_marks"):
        if key in rec and key not in (rec.get("secret_fields") or []) and not key.startswith("_"):
            details[key] = copy.deepcopy(rec[key])
    if real and isinstance(details.get("entries"), list):
        # the written entries, never the grader's note of whether each one matches what happened
        details["entries"] = [{k: v for k, v in e.items() if k != "true"} if isinstance(e, dict) else e for e in details["entries"]]
    if rec.get("category") == "hearing_aid":
        soil = rec.get("soil") or {}
        details["wax"] = round(float(soil.get("wax", 0.0)), 2)
        details["battery_level"] = rec.get("battery_level")
