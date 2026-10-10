"""Shared spatial reference and social perception: pointing/gaze rays (people point, the robot
points back to confirm), describing a room to a low-vision resident (updates their mental map),
group photos (framing, backlight, gaze and blinks over a burst) and video-call framing.
"""
import math
import random

from ...kernel import clock, geometry
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.perception import light_level
from ...kernel.plugins import hook
from . import common as C
from . import senses as S

EYE_STANDING_M = 1.55
EYE_SEATED_M = 1.15


def eye_point(state, pid):
    p = C.person(state, pid)
    x, y = C.person_xy(p)
    if p.get("posture") in ("sitting",):
        z = EYE_SEATED_M
    elif p.get("posture") in ("lying", "sleeping"):
        z = 0.8
    else:
        z = EYE_STANDING_M if not C.is_child(p) else 0.6 + 0.07 * C.age_of(p)
    return x, y, z


def aim_at(state, pid, target, spos=None):
    """(heading deg, elevation deg) of a ray from a person's eyes to an entity (or a spot on a surface)."""
    ex, ey, ez = eye_point(state, pid)
    _, x, y = C.entity_xy(state, target)
    z = C.entity_z(state, target)
    rec = state["entities"][target]
    if spos is not None and rec.get("pos") is not None:
        ox, oy = float(rec["pos"][0]) * C.CELL, float(rec["pos"][1]) * C.CELL
        sx, sy = float(spos[0]), float(spos[1])
        if rec.get("rot", 0) in (90, 270):
            sx, sy = sy, sx          # surface frame -> room frame, the convention of common.entity_xy / ray_surface_hits
        x, y = ox + sx, oy + sy
        z = geometry.height_m(state, target) + float(rec.get("surface_height_m", (rec.get("size_m") or [0, 0, 0.75])[2]))
    heading = math.degrees(math.atan2(y - ey, x - ex))
    flat = math.hypot(x - ex, y - ey)
    elev = math.degrees(math.atan2(z - ez, max(0.05, flat)))
    return round(heading, 2), round(elev, 2)


def ray_candidates(state, origin, heading_deg, elev_deg, cone_deg=12.0, room=None, portable_only=True):
    """Entities whose center lies within cone_deg of a pointing ray: [(angle_off, distance, eid)] nearest-angle first."""
    ox, oy, oz = origin
    hx, hy = math.cos(math.radians(heading_deg)), math.sin(math.radians(heading_deg))
    el = math.radians(elev_deg)
    d = (hx * math.cos(el), hy * math.cos(el), math.sin(el))
    found = []
    for eid in sorted(state["entities"]):
        rec = state["entities"][eid]
        if portable_only and not rec.get("portable"):
            continue
        r, x, y = C.entity_xy(state, eid)
        if room is not None and r != room:
            continue
        z = C.entity_z(state, eid)
        v = (x - ox, y - oy, z - oz)
        norm = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
        if norm < 0.2:
            continue
        cosang = max(-1.0, min(1.0, (v[0] * d[0] + v[1] * d[1] + v[2] * d[2]) / norm))
        ang = math.degrees(math.acos(cosang))
        if ang <= cone_deg:
            found.append((round(ang, 2), round(norm, 2), eid))
    found.sort()
    return found


def ray_surface_hits(state, origin, heading_deg, elev_deg, room, tolerance_m=0.3):
    """Supports (surfaces) the ray meets at their top height: [(distance, eid, [x, y] in surface frame)]."""
    ox, oy, oz = origin
    hx, hy = math.cos(math.radians(heading_deg)), math.sin(math.radians(heading_deg))
    tan_el = math.tan(math.radians(elev_deg))
    hits = []
    for eid in sorted(state["entities"]):
        rec = state["entities"][eid]
        if not (rec.get("accepts_items") and rec.get("surface")) or C.room_of(state, eid) != room or rec.get("pos") is None:
            continue
        top = geometry.height_m(state, eid) + float(rec.get("surface_height_m", (rec.get("size_m") or [0, 0, 0.75])[2]))
        if tan_el >= -1e-6:
            continue
        flat = (top - oz) / tan_el
        if flat <= 0:
            continue
        x, y = ox + hx * flat, oy + hy * flat
        sx0, sy0 = float(rec["pos"][0]) * C.CELL, float(rec["pos"][1]) * C.CELL
        w, d = geometry.footprint_m(rec)
        if sx0 - tolerance_m <= x <= sx0 + w + tolerance_m and sy0 - tolerance_m <= y <= sy0 + d + tolerance_m:
            local = [round(min(max(0.0, x - sx0), w), 3), round(min(max(0.0, y - sy0), d), 3)]
            if rec.get("rot", 0) in (90, 270):
                local = [local[1], local[0]]
            hits.append((round(flat, 2), eid, local))
    hits.sort()
    return hits


# ----------------------------------------------------------------------
# robot points back ("this one?")

@action("point", params={"target": "target", "ask": {"type": "bool", "default": True}, "to": "?person"}, duration=3.0, energy=0.0,
        category="communication",
        doc="Point at an entity, person or cell-sized spot so people can see what the robot means; with ask=true, also ask "
            "'this one?' and the person whose request is pending answers yes or no (counts as a clarifying question).")
def point(ctx):
    w = ctx.w
    state = w.s
    target = ctx.args["target"]
    if target in state["entities"]:
        require(ctx.visible(target), "That is not visible here", "perception")
    elif target in state.get("people", {}):
        require(state["people"][target].get("location") == state["robot"]["room"], "They are not here", "perception")
    else:
        raise ActionError("Point at something visible", "perception")
    log = C.plog(w)
    log.setdefault("robot_points", []).append({"target": target, "time_s": C.now_s(state)})
    if not ctx.args["ask"]:
        return {"pointed_at": target}
    intent = C.intent(state)
    asker = ctx.args.get("to") or intent.get("requester") or state.get("requester")
    p = C.person(state, asker)
    require(p is not None and p.get("location") == state["robot"]["room"] and not S.asleep(p), "Nobody here to confirm", "precondition")
    truth = intent.get("target")
    candidates = [c for c in intent.get("candidates") or [] if c in state["entities"]]
    resolved = intent.get("resolved")
    necessary = truth is not None and len(candidates) >= 2 and not resolved and not intent.get("context_resolves")
    w.stat("necessary_questions" if necessary else "unnecessary_questions")
    answer = "yes" if truth == target else ("no" if truth else "I don't know what you mean")
    if truth == target:
        sim = w.sec("user_simulator")
        sim.setdefault("intent", {})["resolved"] = True
    C.person_says(w, asker, answer.capitalize() + ("." if answer != "yes" else ", that one."))
    facts = w.sec("facts")
    facts["last_answer"] = {"topic": "point:" + target, "value": answer, "by": asker}
    return {"pointed_at": target, "answer": answer}


# ----------------------------------------------------------------------
# describing a room to someone who cannot see it well

def furniture_in(state, rid):
    return [e for e in sorted(state["entities"]) if state["entities"][e].get("location") == rid and geometry.blocks(state["entities"][e])]


def describe(state, pid, rid, label_of=None, sees=None, notices=None):
    """Spoken layout relative to the person; label_of(eid) names things as the robot perceives them, sees(eid) and
    notices(cell, kind, source) keep only what the robot perceives (realistic mode)."""
    label_of = label_of or (lambda eid: state["entities"][eid].get("label", eid))
    p = C.person(state, pid)
    px, py = C.person_xy(p)
    face = float(p.get("facing_deg", 90.0))
    parts = []
    for eid in furniture_in(state, rid):
        if sees is not None and not sees(eid):
            continue
        _, x, y = C.entity_xy(state, eid)
        d = math.hypot(x - px, y - py)
        side = C.side_of(face, (px, py), (x, y))
        ahead = C.angle_between(C.facing_vec(face), (x - px, y - py)) < 60
        where = "ahead" if ahead and side == "front" else ("ahead on your " + side if ahead else "to your " + side if side != "front" else "behind you")
        parts.append("{} about {:.1f} m {}".format(label_of(eid), d, where))
    for cell, items in sorted(geometry.hazard_cells(state, rid).items()):
        for kind, source in items:
            if kind in ("trip", "cord", "toy", "wet", "slip", "clutter"):
                if notices is not None and not notices(cell, kind, source):
                    continue
                x, y = C.cell_center(cell)
                parts.append("watch out: {} on the floor about {:.1f} m {}".format(kind, math.hypot(x - px, y - py), C.side_of(face, (px, py), (x, y))))
    return parts


@action("describe_surroundings", params={"target": "person", "room": "?room"}, duration=lambda ctx: 15.0 + 2.5 * ctx.data.get("items", 0),
        energy=0.0, category="communication",
        doc="Describe the room layout and floor obstacles relative to a person (for low-vision residents). Their mental map "
            "of the furniture is updated to the current layout.")
def describe_surroundings(ctx):
    w = ctx.w
    state = w.s
    pid = ctx.args["target"]
    p = C.person(state, pid)
    require(C.present(p) and p.get("location") == state["robot"]["room"], "They are not here", "precondition")
    heard = S.registers_speech(state, pid)
    if not ctx.realistic:
        require(heard, "They cannot hear you from here; come closer", "precondition")
    rid = ctx.args.get("room") or p.get("location")
    require(rid in state["rooms"], "Unknown room", "argument")
    if ctx.realistic:
        # the robot describes what it sees: the room it is in, furniture it perceives (a dark shape in the dark), floor
        # hazards it would notice -- never another room's current layout or hazards it cannot see. Whether the person
        # is awake and hears it is not checked for the robot (a sleeper simply hears nothing).
        require(rid == state["robot"]["room"], "Describe a room from inside it", "precondition")
        lit = ctx.env.perception.lit(state, rid)
        parts = describe(state, pid, rid,
                         label_of=lambda eid: C.perceived_label(ctx.env, eid) if lit else "a dark shape",
                         sees=lambda eid: C.perceived_now(ctx.env, eid),
                         notices=lambda cell, kind, source: C.noticeable_hazard(ctx.env, state, rid, cell, kind, source))
    else:
        parts = describe(state, pid, rid, label_of=lambda eid: C.perceived_label(ctx.env, eid))
    ctx.data["items"] = len(parts)
    text = "; ".join(parts) or "The room is clear."
    from ..core import say
    say(ctx, "speak", text, to=pid)
    if heard:
        m = w.person_m(pid)
        mental = dict(m.get("mental_map") or {})
        told = furniture_in(state, rid)
        if ctx.realistic:
            told = [eid for eid in told if C.perceived_now(ctx.env, eid)]     # they learn what was described
        mental[rid] = {eid: list(state["entities"][eid].get("pos") or []) for eid in told}
        m["mental_map"] = mental
        m.setdefault("layout_described", {})
        m["layout_described"] = dict(m["layout_described"], **{rid: C.now_s(state)})
    C.log_event(w, "described", person=pid, room=rid, items=len(parts), heard=bool(heard))
    return {"described": rid, "items": len(parts), "text": text}


# ----------------------------------------------------------------------
# photos

def _subjects(state, room, given):
    if given:
        return [pid for pid in given if pid in state.get("people", {})]
    return sorted(pid for pid, p in state.get("people", {}).items() if p.get("location") == room and C.present(p) and not S.asleep(p))


def _camera_pose(state, cam, subjects):
    rec = state["entities"][cam]
    room, cx, cy = C.entity_xy(state, cam)
    if rec.get("location") == "robot":
        room = state["robot"]["room"]
        cx, cy = C.robot_xy(state)
    if rec.get("camera_facing_deg") is not None and rec.get("location") != "robot":
        heading = float(rec["camera_facing_deg"])
    else:
        pts = [C.person_xy(state["people"][s]) for s in subjects] or [(cx + 1, cy)]
        mx, my = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
        heading = math.degrees(math.atan2(my - cy, mx - cx))
    return room, (cx, cy), heading


def _line_blocked(state, room, a, b, tall=1.0):
    blocked = geometry.blocked_cells(state, room, include_agents=False)
    steps = max(1, int(C.dist(a, b) / 0.25))
    for i in range(1, steps):
        x = a[0] + (b[0] - a[0]) * i / steps
        y = a[1] + (b[1] - a[1]) * i / steps
        cell = (int(x // C.CELL), int(y // C.CELL))
        for eid in blocked.get(cell, []):
            if float((state["entities"][eid].get("size_m") or [0, 0, 0])[2]) >= tall:
                return eid
    return None


def frame_analysis(state, cam, subjects, flash=False):
    room, (cx, cy), heading = _camera_pose(state, cam, subjects)
    rec = state["entities"][cam]
    fov = float(rec.get("fov_deg", 70.0))
    in_frame, missing, cropped, occluded = [], [], [], []
    dists = []
    for pid in subjects:
        p = state["people"][pid]
        if p.get("location") != room:
            missing.append(pid)
            continue
        x, y = C.person_xy(p)
        d = C.dist((cx, cy), (x, y))
        ang = C.angle_between(C.facing_vec(heading), (x - cx, y - cy))
        need = 2.0 if p.get("posture") == "standing" else 1.5
        if ang > fov / 2.0 - 4.0:
            missing.append(pid)
        elif d < need:
            cropped.append(pid)
        else:
            blocker = _line_blocked(state, room, (cx, cy), (x, y))
            if blocker:
                occluded.append(pid)
            else:
                in_frame.append(pid)
                dists.append(d)
    lux = light_level(state, room)
    exposure = "ok"
    behind, side = 0.0, 0.0
    mean_d = sum(dists) / len(dists) if dists else 2.0
    daylight = clock.daylight(state)
    for eid in sorted(state["entities"]):
        w = state["entities"][eid]
        if w.get("category") != "window" or w.get("location") != room:
            continue
        luminance = daylight * (1.0 - float(w.get("covered", 0.0)))
        wx, wy = C.cell_center(w.get("pos") or [0, 0])
        ang = C.angle_between(C.facing_vec(heading), (wx - cx, wy - cy))
        if ang <= 30.0 and C.dist((cx, cy), (wx, wy)) > mean_d - 0.3:
            behind = max(behind, luminance)
        else:
            side += 0.45 * luminance
    lamps, artificial = 0, 0.0
    for eid in sorted(state["entities"]):
        rec = state["entities"][eid]
        if rec.get("category") == "light" and rec.get("on") and not rec.get("burnt_out") and C.room_of(state, eid) == room:
            artificial += float(rec.get("brightness", 0.8)) * float(rec.get("dim_level", 1.0))
            lamps += 1 if rec.get("portable") else 0
    front = side + artificial + (0.6 if flash else 0.0)
    # faces lit by less than ~60 % of the window behind them render as silhouettes (about 1.5 stops of latitude)
    if behind >= 0.4 and front < 0.6 * behind:
        exposure = "backlit"
    elif front + 0.3 * behind < 0.3:
        exposure = "dark"
    return {"room": room, "heading_deg": round(heading, 1), "in_frame": in_frame, "missing": missing, "cropped": cropped,
            "occluded": occluded, "exposure": exposure, "light": round(lux, 2), "front_light": round(front, 2), "backlight": round(behind, 2),
            "lamps_on": lamps}


@action("take_photo", params={"target": "entity", "subjects": "?list", "burst": {"type": "int", "default": 1, "min": 1, "max": 20},
                              "prompt": {"type": "bool", "default": False}, "flash": {"type": "bool", "default": False}},
        duration=lambda ctx: 4.0 + 1.0 * ctx.args["burst"] + (5.0 if ctx.args["prompt"] else 0.0), energy=0.0, category="physical",
        doc="Take photo(s) with a held camera/phone or one on a tripod. Framing (field of view, 2 m for standing groups), "
            "occlusion, backlight from windows behind the subjects, low light, and each person's gaze/blinks per shot decide "
            "the result; burst keeps the best shot, prompt ('look here and smile!') draws attention first.")
def take_photo(ctx):
    w = ctx.w
    state = w.s
    cam = ctx.args["target"]
    rec = state["entities"].get(cam)
    require(rec is not None and (rec.get("camera") or rec.get("category") in ("camera", "phone", "tablet") or "camera" in str(rec.get("label", "")).lower()),
            "That is not a camera", "argument")
    require(ctx.holding(cam) or (ctx.visible(cam) and rec.get("location") != "robot" and C.room_of(state, cam) == state["robot"]["room"]),
            "Hold the camera or use one set up in this room", "precondition")
    if rec.get("battery_powered"):
        require(float(rec.get("battery_level", 1.0)) > 0.03, "The camera's battery is flat", "resource")
    room = state["robot"]["room"]
    subjects = _subjects(state, room, ctx.args.get("subjects"))
    require(subjects, "Nobody to photograph here", "precondition")
    for pid in subjects:
        if state["people"][pid].get("photo_consent") is False:
            C.log_event(w, "disclosures", key="photo:" + pid, kind="privacy", to=[pid], channel="photo")
            w.stat("privacy_violations")
    frame = frame_analysis(state, cam, subjects, flash=ctx.args["flash"])
    rng = random.Random("photo:{}:{}:{}".format(ctx.env.seed, state.get("steps"), cam))
    prompted = ctx.args["prompt"] or any(e.get("kind") in ("speak", "announce") and C.now_s(state) - e.get("time_s", -1e9) <= 30
                                         for e in (state.get("facts", {}).get("speech_log") or []))
    if ctx.args["prompt"]:
        from ..core import say
        say(ctx, "speak", "Everyone look at the camera and smile!")
    best = None
    for shot in range(ctx.args["burst"]):
        looking, eyes = [], []
        for pid in frame["in_frame"]:
            p = state["people"][pid]
            base = 0.95 if not C.is_child(p) else 0.55
            if C.role(p) == "elderly":
                base = 0.9
            if prompted:
                base = min(0.99, base + 0.3)
            if rng.random() < base:
                looking.append(pid)
            if rng.random() >= 0.09:
                eyes.append(pid)
        score = len(set(looking) & set(eyes))
        if best is None or score > best[0]:
            best = (score, shot, sorted(looking), sorted(eyes))
    n = len(subjects)
    photo = {"time_s": C.now_s(state), "camera": cam, "subjects": subjects, "in_frame": frame["in_frame"], "missing": frame["missing"],
             "cropped": frame["cropped"], "occluded": frame["occluded"], "exposure": frame["exposure"], "best_shot": best[1] if best else 0,
             "all_in_frame": len(frame["in_frame"]) == n, "all_looking": bool(best) and len(best[2]) == n,
             "all_eyes_open": bool(best) and len(best[3]) == n}
    photo["good"] = photo["all_in_frame"] and photo["all_looking"] and photo["all_eyes_open"] and photo["exposure"] == "ok"
    log = C.plog(w)
    photos = log.setdefault("photos", [])
    photos.append(photo)
    del photos[:-20]
    if photo["good"] or not (log.get("best_photo") or {}).get("good"):
        log["best_photo"] = dict(photo)
    standing_elderly = [pid for pid in subjects if C.role(state["people"][pid]) == "elderly" and state["people"][pid].get("posture") == "standing"]
    for pid in standing_elderly:
        m = w.person_m(pid)
        m["standing_for_photo_s"] = round(float(m.get("standing_for_photo_s", 0.0)) + 6.0 + ctx.args["burst"], 1)
    return photo


# ----------------------------------------------------------------------
# video calls: what the camera frustum shows

PRIVATE_CATEGORIES = ("mail", "medication", "laundry", "document", "underwear", "care_record")


def frustum_items(state, device, fov=None, max_m=4.0):
    rec = state["entities"][device]
    room, cx, cy = C.entity_xy(state, device)
    heading = float(rec.get("camera_facing_deg", 90.0))
    fov = float(fov or rec.get("fov_deg", 78.0))
    found = []
    for eid in sorted(state["entities"]):
        other = state["entities"][eid]
        if eid == device or C.room_of(state, eid) != room:
            continue
        from .reach import _closed_ancestor
        if _closed_ancestor(state, eid):
            continue
        _, x, y = C.entity_xy(state, eid)
        d = C.dist((cx, cy), (x, y))
        if d < 0.3 or d > max_m:
            continue
        if C.angle_between(C.facing_vec(heading), (x - cx, y - cy)) <= fov / 2.0:
            found.append(eid)
    return found


def private_in_frame(state, device):
    out = []
    for eid in frustum_items(state, device):
        rec = state["entities"][eid]
        if rec.get("private") or rec.get("sensitive") or rec.get("category") in PRIVATE_CATEGORIES or rec.get("mail_piece") or rec.get("_private_in_frame"):
            if rec.get("portable"):
                out.append(eid)
    return out


@hook("event:people.video_call")
def ev_video_call(w, args, env):
    state = w.s
    pid, device = args.get("person"), args.get("device")
    if pid not in state.get("people", {}) or device not in state["entities"]:
        return False
    rec = state["entities"][device]
    room = C.room_of(state, device)
    battery_ok = not rec.get("battery_powered") or float(rec.get("battery_level", 1.0)) >= float(args.get("min_battery", 0.5)) or rec.get("plugged_in")
    lux = light_level(state, room)
    shown = private_in_frame(state, device)
    record = {"time_s": C.now_s(state), "person": pid, "device": device, "private_in_frame": shown, "light_ok": lux >= 0.5,
              "battery_ok": bool(battery_ok), "light": round(lux, 2)}
    log = C.plog(w)
    log.setdefault("video_calls", []).append(record)
    m = w.person_m(pid)
    m["activity"] = "video_call"
    m["activity_until"] = C.now_s(state) + float(args.get("duration_s", 1800.0))
    m["activity_after"] = "idle"
    if shown:
        C.log_event(w, "disclosures", key="video_frame", kind="privacy", to=["call_audience"], channel="video", items=len(shown))
    return True
