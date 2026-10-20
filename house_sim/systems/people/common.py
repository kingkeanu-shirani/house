"""Shared helpers for the people domain: roles and authority, abilities, anthropometrics,
positions in meters, sound propagation, the people_log section and person-facing messages.

State owned by this domain:
  people.<pid>      person records (see docs/domains/people.md for the fields used)
  people_log        top-level section (journaled through w.sec): logs of wakes, falls, alerts,
                    deliveries, disclosures, intrusions, assists, photos... read by invariants
  household.rules / household.contacts / household.registry / household.expected_visitors
  user_simulator.intent / .messages / .secrets   hidden ground truth behind ambiguous requests

Nothing here mutates state except through the World passed in (journaled).

Tier-1 adoption (kernel v3.0/v3.1): the robot's own sounds come from its body (body.noise_db: footsteps by the
effective speed, speech, quiet mode), whether a person's speech reaches the robot follows the body's hearing
(robot_hears_speech: body.can_hear, privacy mode keeps addressed speech), the robot's voice needs a working speaker
(robot_voice), and times of day are the household's wall clock (clock.wall: daylight saving time once the generator
sets clock.dst).
"""
import math
import re

from ...kernel import body, clock, geometry
from ...kernel.predicates import root_room

CELL = geometry.CELL

RESIDENT_ROLES = ("adult", "elderly", "teen", "child", "toddler", "infant")
CHILD_ROLES = ("child", "toddler", "infant")
VISITOR_ROLES = ("visitor", "stranger", "solicitor", "contractor", "delivery", "courier", "paramedic", "responder")

# Anthropometrics (CDC/NHANES stature tables; ADA 2010 reach ranges; WHO sleep-noise guidance).
ARM_REACH_M = {"adult": 0.72, "elderly": 0.62, "teen": 0.68, "child": 0.5, "toddler": 0.35, "infant": 0.2}
SHOULDER_STANDING_M = {"adult": 1.42, "elderly": 1.32, "teen": 1.35, "child": 1.0, "toddler": 0.7, "infant": 0.3}
SEATED_SHOULDER_ABOVE_SEAT_M = 0.55
LYING_REACH_M = 0.5
WHEELCHAIR_BAND_M = (0.38, 1.22)      # ADA forward reach, unobstructed
ELDER_BAND_M = (0.6, 1.5)             # comfortable reach band without bending or stretching
CHILD_REACH_BY_AGE = ((1, 0.55), (2, 0.85), (3, 1.05), (5, 1.25), (8, 1.5), (12, 1.75))
WALK_SPEED_MPS = {"full": 1.2, "cane": 0.6, "walker": 0.45, "rollator": 0.55, "wheelchair": 0.9, "bedbound": 0.0, "crutches": 0.5}
EDGE_MARGIN_M = 0.03

# Sound (dB(A) at 1 m) for robot behaviour and common household sources. The robot's own footsteps and speech are its
# body's (body.noise_db; humanoid_v1: footsteps 30 / 36 / 44, speech 60, quiet speech 45 -- the values below, kept as
# the reference the profile reproduces and for states without a body profile).
SPEECH_DB = 60.0
QUIET_SPEECH_DB = 45.0
ANNOUNCE_DB = 66.0
INTERCOM_DB = 62.0
KNOCK_DB = 52.0
DOOR_CLOSE_DB = {"careful": 40.0, "normal": 55.0, "fast": 72.0}     # eased shut / closed / slammed (impulse peak)
DRAWER_CLOSE_DB = {"careful": 38.0, "normal": 52.0, "fast": 68.0}
FOOTSTEP_DB = {"careful": 30.0, "normal": 36.0, "fast": 44.0}
CREAK_DB = {"careful": 40.0, "normal": 48.0, "fast": 54.0}
OPEN_DOOR_LOSS_DB = 8.0
CLOSED_DOOR_LOSS_DB = 22.0
FLOOR_LOSS_DB = 12.0          # through an open stairwell between floors
WALL_LOSS_DB = 35.0
HEARING_THRESHOLD_DB = {"full": 30.0, "reduced": 52.0, "deaf": 999.0}
SLEEP_WAKE_DB = {"adult": 55.0, "elderly": 52.0, "teen": 58.0, "child": 55.0, "toddler": 50.0, "infant": 48.0}

PRIVATE_ACTIVITIES = ("bathing", "showering", "toilet", "dressing", "changing", "intimate")
BUSY_ACTIVITIES = ("call", "video_call", "meeting", "working", "studying", "exam")
AFFECT_STATES = ("arguing", "crying", "grieving", "upset")


# ----------------------------------------------------------------------
# people

def people(state):
    return state.get("people", {})


def person(state, pid):
    return state.get("people", {}).get(pid)


def perceived_label(env, eid):
    """An entity's label as the robot perceives it: the true label in oracle mode; in realistic mode a look-alike's
    shared descriptor (or a novel object's description) until it has been inspected/read as its group needs."""
    state = env.world.s
    rec = state["entities"].get(eid) or {}
    realism = getattr(env, "realism", None)
    if realism is None:
        return rec.get("label", eid)
    level = realism.knowledge.entity_level(eid, rec)
    return realism.identity(eid, rec, level).get("label") or "object"


HIDDEN_HAZARD_KINDS = ("damp", "film", "black_ice", "residue", "invisible")     # kernel realism: never seen on the floor


def perceived_now(env, eid):
    """Realistic perception: does the robot perceive this entity from where it stands (visible, not missed by the
    small-object detector, nor inside something missed)? Oracle mode: visibility only."""
    state = env.world.s
    if eid not in state["entities"] or not env.perception.visible(state, eid):
        return False
    realism = getattr(env, "realism", None)
    if realism is None:
        return True
    current, seen = eid, set()
    while current in state["entities"] and current not in seen:
        seen.add(current)
        rec = state["entities"][current]
        if realism.missed(state, current, rec, realism.context(state, current, rec)):
            return False
        current = rec.get("location")
    return True


def noticeable_hazard(env, state, rid, cell, kind, source):
    """Realistic perception: would the robot notice this floor hazard looking at the floor of `rid` (lit, or with a
    flashlight)? Not an invisible kind (damp film, black ice...), not one a domain's visible_floor_hazard hook vetoes,
    not cast by an un-inspected mirror or by something concealed (under/behind, or missed by the detector when the
    robot is in that room). Oracle mode: always."""
    realism = getattr(env, "realism", None)
    if realism is None:
        return True
    if kind in HIDDEN_HAZARD_KINDS or not env.perception.lit(state, rid):
        return False
    from ...kernel.realism import INSPECT
    srec = state["entities"].get(source)
    if srec is not None:
        if (srec.get("category") == "mirror" or srec.get("reflective")) and realism.knowledge.entity_level(source, srec) < INSPECT:
            return False
        if srec.get("relation") in ("under", "behind") and not srec.get("revealed"):
            return False
        if rid == state["robot"].get("room") and not perceived_now(env, source):
            return False
    from ...kernel.plugins import HOOKS
    for _, _, fn in HOOKS.get("visible_floor_hazard", []):
        if fn(state, rid, tuple(cell), kind, source) is False:
            return False
    return True


def role(p):
    return (p or {}).get("role", "adult")


def age_of(p):
    if p is None:
        return 35.0
    if p.get("age") is not None:
        return float(p["age"])
    return {"infant": 0.5, "toddler": 2.0, "child": 7.0, "teen": 15.0, "elderly": 78.0}.get(role(p), 35.0)


def is_child(p):
    return role(p) in CHILD_ROLES or (p is not None and p.get("age") is not None and float(p["age"]) < 13 and role(p) not in ("adult", "elderly"))


def is_minor(p):
    return is_child(p) or role(p) == "teen" or (p is not None and p.get("age") is not None and float(p["age"]) < 18)


def is_resident(p):
    return role(p) in RESIDENT_ROLES and not (p or {}).get("visiting")


def is_guest(p):
    return role(p) == "guest" or bool((p or {}).get("visiting"))


def is_visitor(p):
    return role(p) in VISITOR_ROLES


def authority(p):
    if not p:
        return 0
    if "authority" in p:
        return int(p["authority"])
    return {"adult": 2, "elderly": 1, "guest": 1}.get(role(p), 0)


def is_authorized_adult(p):
    """A resident adult or caregiver who can take responsibility (authority >= 2, or a caregiver)."""
    return bool(p) and (authority(p) >= 2 or p.get("caregiver")) and not is_minor(p) and role(p) not in VISITOR_ROLES


def present(p):
    return bool(p) and p.get("location") is not None and not p.get("away")


def abilities(p):
    base = {"mobility": "full", "vision": "full", "hearing": "full", "cognition": "full", "balance": "good",
            "strong_side": "both", "grip": "normal"}
    base.update((p or {}).get("abilities") or {})
    return base


def label_of(state, pid):
    p = person(state, pid) or {}
    return str(p.get("label", pid)).split(" (")[0]


def mobility_aid_kind(p):
    mob = abilities(p)["mobility"]
    return mob if mob in ("cane", "walker", "rollator", "wheelchair", "crutches") else None


def child_reach_m(age):
    for limit, reach in CHILD_REACH_BY_AGE:
        if age <= limit:
            return reach
    return 1.9


def reach_height_m(p):
    """Highest point the person can grab from standing (tip-toe, no climbing)."""
    if p.get("reach_m"):
        return float(p["reach_m"])
    if is_child(p):
        return child_reach_m(age_of(p))
    ab = abilities(p)
    if ab["mobility"] in ("wheelchair", "bedbound"):
        return WHEELCHAIR_BAND_M[1]
    if role(p) == "elderly" or ab["mobility"] in ("cane", "walker", "rollator"):
        return ELDER_BAND_M[1]
    return 1.9


def walk_speed(p):
    ab = abilities(p)
    speed = WALK_SPEED_MPS.get(ab["mobility"], 1.2)
    if ab["vision"] == "low":
        speed = min(speed, 0.8)
    elif ab["vision"] == "blind":
        speed = min(speed, 0.6)
    if role(p) == "elderly":
        speed = min(speed, 0.7)
    if is_child(p) and age_of(p) < 2:
        speed = 0.4
    return speed


def fall_risk(p):
    if p.get("fall_risk") is not None:
        return float(p["fall_risk"])
    ab = abilities(p)
    risk = 0.05
    if role(p) == "elderly":
        risk += 0.25
    if ab["balance"] == "poor":
        risk += 0.35
    if ab["mobility"] in ("cane", "walker", "rollator", "crutches"):
        risk += 0.15
    if ab["vision"] in ("low", "blind"):
        risk += 0.1
    if is_child(p) and age_of(p) < 3:
        risk += 0.2
    return min(1.0, risk)


# ----------------------------------------------------------------------
# time

def now_s(state):
    return float(state.get("time_s", 0.0))


def wall(state):
    """The household's wall-clock datetime (clock.wall: standard time plus daylight saving time while the clock follows
    it). People keep their schedules, quiet hours, wake requests and medicine times by the wall clock."""
    return clock.wall(state)


def hour(state):
    current = wall(state)
    return current.hour + current.minute / 60.0 + current.second / 3600.0


def hm(text):
    """'07:30' -> 450 minutes after midnight."""
    h, _, m = str(text).partition(":")
    return int(h) * 60 + int(m or 0)


def minute_of_day(state):
    return int(hour(state) * 60) % 1440


def in_window(state, start, end, now=None):
    """True when the current clock time lies in [start, end) (handles windows over midnight). now: the minute of the
    day when the caller already has it (one tick of a system asks many times)."""
    now = minute_of_day(state) if now is None else now
    a, b = hm(start), hm(end)
    return a <= now < b if a <= b else (now >= a or now < b)


def weekday(state):
    return wall(state).strftime("%A")


# ----------------------------------------------------------------------
# geometry in meters

def cell_center(pos):
    return ((float(pos[0]) + 0.5) * CELL, (float(pos[1]) + 0.5) * CELL)


def robot_xy(state):
    return cell_center(state["robot"].get("pos") or [0, 0])


def person_xy(p):
    return cell_center((p or {}).get("pos") or [0, 0])


def facing_vec(deg):
    rad = math.radians(float(deg or 0.0))
    return (math.cos(rad), math.sin(rad))


def entity_xy(state, eid, _depth=0):
    """Approximate (room, x, y) of an entity's center in its root room (meters)."""
    entities = state["entities"]
    rec = entities.get(eid)
    if rec is None or _depth > 20:
        return None, 0.0, 0.0
    loc = rec.get("location")
    if loc == "robot":
        x, y = robot_xy(state)
        return state["robot"].get("room"), x, y
    if loc in state.get("people", {}):
        p = state["people"][loc]
        x, y = person_xy(p)
        return p.get("location"), x, y
    if loc in state.get("pets", {}):
        p = state["pets"][loc]
        x, y = cell_center(p.get("pos") or [0, 0])
        return p.get("location"), x, y
    if loc in state.get("rooms", {}):
        if rec.get("pos") is None:
            return loc, 0.0, 0.0
        w, d = geometry.footprint_m(rec)
        return loc, float(rec["pos"][0]) * CELL + w / 2.0, float(rec["pos"][1]) * CELL + d / 2.0
    parent = entities.get(loc)
    if parent is None:
        return None, 0.0, 0.0
    room, px, py = entity_xy(state, loc, _depth + 1)
    if rec.get("relation", "on") == "on" and rec.get("spos") is not None and parent.get("pos") is not None and parent.get("location") in state.get("rooms", {}):
        ox, oy = float(parent["pos"][0]) * CELL, float(parent["pos"][1]) * CELL
        iw, idp = geometry.footprint_m(rec)
        sx, sy = float(rec["spos"][0]) + iw / 2.0, float(rec["spos"][1]) + idp / 2.0
        if parent.get("rot", 0) in (90, 270):
            sx, sy = sy, sx
        return room, ox + sx, oy + sy
    return room, px, py


def entity_z(state, eid):
    """Height of the grasp point (base height plus half the item height)."""
    try:
        base = geometry.height_m(state, eid)
    except Exception:
        base = 0.0
    size = state["entities"].get(eid, {}).get("size_m") or [0.1, 0.1, 0.1]
    return base + min(0.15, float(size[2]) / 2.0)


def edge_clearance_m(state, eid):
    """Distance from an item's footprint to the nearest edge of the surface it rests on (None if not on a surface)."""
    rec = state["entities"].get(eid, {})
    parent = state["entities"].get(rec.get("location"))
    if parent is None or rec.get("relation", "on") != "on" or rec.get("spos") is None:
        return None
    sw, sd = (parent.get("surface_size_m") or (parent.get("size_m") or [0.5, 0.5])[:2])[:2]
    iw, idp = geometry.footprint_m(rec)
    x, y = float(rec["spos"][0]), float(rec["spos"][1])
    return round(min(x, y, float(sw) - (x + iw), float(sd) - (y + idp)), 4)


def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def angle_between(v1, v2):
    n1, n2 = math.hypot(*v1), math.hypot(*v2)
    if n1 < 1e-9 or n2 < 1e-9:
        return 0.0
    c = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
    return math.degrees(math.acos(c))


def side_of(facing_deg, origin, point):
    """'left' or 'right' of a person facing facing_deg (grid frame: x right, y down)."""
    fx, fy = facing_vec(facing_deg)
    vx, vy = point[0] - origin[0], point[1] - origin[1]
    cross = fx * vy - fy * vx
    # with y pointing down, a positive cross product is to the person's right
    if abs(cross) < 0.05:
        return "front"
    return "right" if cross > 0 else "left"


def chebyshev_cells(a, b):
    return geometry.chebyshev(tuple(a), tuple(b))


def static_ids(w, name, predicate):
    """Ids of entities matching a predicate over STATIC fields (kernel w.static_index, cached until an entity is
    created or destroyed). Callers re-check dynamic fields on the records."""
    ents = w.s["entities"]
    return [e for e in w.static_index("people." + name, predicate) if e in ents]


def by_location(state):
    """{location: [entity ids]} in one pass over the entities."""
    by_loc = {}
    for eid, rec in state["entities"].items():
        by_loc.setdefault(rec.get("location"), []).append(eid)
    return by_loc


def room_items(state, rid, by_loc=None):
    """Sorted ids of every entity whose root room is rid (things held by the robot or by people/pets there included):
    one pass over the entities instead of a root_room walk per entity."""
    by_loc = by_loc if by_loc is not None else by_location(state)
    roots = [rid]
    if state.get("robot", {}).get("room") == rid:
        roots.append("robot")
    for group in ("people", "pets"):
        roots.extend(a for a, rec in sorted(state.get(group, {}).items()) if rec.get("location") == rid)
    found, stack, seen = [], list(roots), set(roots)
    while stack:
        for child in by_loc.get(stack.pop(), ()):
            if child not in seen:
                seen.add(child)
                found.append(child)
                stack.append(child)
    return sorted(found)


def portable_ids(w):
    return static_ids(w, "portable", lambda r: bool(r.get("portable")) or "handle_out" in r)


REFRESH_S = 120.0


def change_stamp(w):
    """[step, person/pet/connection records journaled so far this step, scripted events fired]. The kernel journals a
    record the first time anything touches it in a step, so the stamp changes with every action (new step), whenever
    people, pets or doorways change and whenever a scenario event fires during a wait. Entity records are not counted:
    other domains' systems touch some entity every few substeps of a long wait (thermal, spoilage...), which would
    defeat the cache; needs_refresh's period covers them."""
    n = 0
    for kind, _ in w.journal_keys():
        if kind in ("people", "pets", "connections"):
            n += 1
    fired = sum(1 for e in w.s.get("events", []) if isinstance(e, dict) and e.get("fired"))
    return [int(w.s.get("steps", 0) or 0), n, fired]


def needs_refresh(w, last, refresh_s=REFRESH_S):
    """Derived scans (reach_items, comfort, room metrics, a curious child's hazard set) are recomputed after every
    action (new step), when people/pets/doorways change or a scenario event fires, and at least every refresh_s of
    simulated time during a long wait (things other domains' systems move meanwhile are picked up within 2 minutes)."""
    if not isinstance(last, dict):
        return True
    if last.get("stamp") != change_stamp(w):
        return True
    return now_s(w.s) - float(last.get("time_s", -1e9)) >= refresh_s - 1e-9


def light_level(w, rid):
    """kernel.perception.light_level computed from static window/light indexes (same formula: daylight x 0.45 per
    uncovered window in the room, capped at 1, + every lit, powered light whose root room is rid + light_level hooks)."""
    from ...kernel.plugins import run_hooks
    state = w.s
    ents = state["entities"]
    room = state["rooms"].get(rid, {})
    if room.get("outdoor"):
        level = clock.daylight(state)
    else:
        exposure = 0.0
        for wid in static_ids(w, "windows", lambda r: r.get("category") == "window"):
            rec = ents[wid]
            if rec.get("location") == rid:
                exposure += 0.45 * (1.0 - float(rec.get("covered", 0.0)))
        level = clock.daylight(state) * min(1.0, exposure)
    grid = state.get("utilities", {}).get("power", True)
    for eid in static_ids(w, "lights", lambda r: r.get("category") == "light"):
        rec = ents[eid]
        if rec.get("on") and not rec.get("burnt_out") and (rec.get("powered", True) and grid or rec.get("battery_powered")) \
                and root_room(state, eid) == rid:
            level += float(rec.get("brightness", 0.8)) * float(rec.get("dim_level", 1.0))
    for extra in run_hooks("light_level", state, rid):
        if isinstance(extra, (int, float)):
            level += extra
    return round(max(0.0, min(1.0, level)), 3)


def room_of(state, eid):
    if eid in state.get("rooms", {}):
        return eid
    if eid in state.get("people", {}):
        return state["people"][eid].get("location")
    if eid in state.get("pets", {}):
        return state["pets"][eid].get("location")
    return root_room(state, eid)


def floor_material(state, rid):
    room = state["rooms"].get(rid, {})
    return room.get("floor_material") or "hardwood"


# ----------------------------------------------------------------------
# sound propagation (lumped: inverse-square within a room, fixed losses per doorway/door/floor)

def connection_loss(state, conn):
    door = state["entities"].get(conn.get("door") or "")
    loss = OPEN_DOOR_LOSS_DB
    if door is not None and not door.get("open"):
        loss = CLOSED_DOOR_LOSS_DB
    if conn.get("kind") == "stairs":
        loss += FLOOR_LOSS_DB
    return loss


def room_losses(state, src_room, max_hops=3):
    """{room: dB lost reaching it from src_room} over the connection graph (Dijkstra, few hops)."""
    best = {src_room: 0.0}
    frontier = [(0.0, 0, src_room)]
    conns = sorted(state.get("connections", {}).items())
    while frontier:
        frontier.sort()
        loss, hops, room = frontier.pop(0)
        if loss > best.get(room, 1e9) + 1e-9 or hops >= max_hops:
            continue
        for _, conn in conns:
            if room not in conn["rooms"]:
                continue
            other = conn["rooms"][1] if conn["rooms"][0] == room else conn["rooms"][0]
            new = loss + connection_loss(state, conn) + 2.0
            if new < best.get(other, 1e9) - 1e-9:
                best[other] = new
                frontier.append((new, hops + 1, other))
    return best


def level_at(state, src_room, src_xy, db, listener_room, listener_xy, losses=None):
    """Sound level (dB(A)) of a source of `db` at 1 m reaching a listener."""
    if listener_room is None or src_room is None:
        return 0.0
    if listener_room == src_room:
        # inverse square from the 1 m reference, so half a metre away is 6 dB louder; beyond ~6 m the room's
        # reverberant field keeps the level from dropping further
        d = max(0.5, dist(src_xy, listener_xy))
        return db - min(16.0, 20.0 * math.log10(d))
    losses = losses if losses is not None else room_losses(state, src_room)
    if listener_room not in losses:
        return db - WALL_LOSS_DB - 12.0
    return db - losses[listener_room] - 6.0


def noise_units_to_db(units):
    """Map the kernel's symbolic action noise (stats.noise increments) to dB(A) at 1 m."""
    u = max(0.0, float(units))
    if u <= 0.0:
        return 0.0
    return round(40.0 + 20.0 * math.log10(max(u, 0.01) / 0.05), 1)


def hearing_threshold(p):
    return HEARING_THRESHOLD_DB.get(abilities(p)["hearing"], 30.0) - (12.0 if (p or {}).get("hearing_aid_in") else 0.0)


def speech_level(state):
    """The robot's speaking level at 1 m: its body profile's speech (quiet mode: its quiet speech)."""
    return body.noise_db(state, "speech")


def footstep_level(state, speed=None):
    """The robot's footsteps at 1 m for a requested speed (None: its speed mode) as its body walks it: the effective
    speed (quiet mode turns fast into normal; limp mode or weak legs walk careful) and quiet mode's softer footfall."""
    return body.noise_db(state, "footsteps", speed)


def robot_voice(w, detect=True):
    """True when the robot's speaker works (body.can_speak). Through a failed speaker nobody hears the robot; like core
    say(), hearing no echo of its own voice is how the robot notices a latent speaker fault (detected when its
    microphones work). People's own voice channels (alerts, relays, reading aloud, stories, guiding announcements, a
    voice wake) call this before anyone may register what was said."""
    state = w.s
    if body.can_speak(state):
        return True
    if detect and body.is_latent(state, "speaker_failed") and body.can_hear(state) != "none":
        from ...kernel import body_faults
        body_faults.detect_fault(w, "speaker_failed", "speech")
    return False


def addressed_to_robot(to, extra=None):
    """Speech addressed to the robot (the kernel's wake-word rule: to == "robot" or marked addressed_robot)."""
    return to == "robot" or bool((extra or {}).get("addressed_robot"))


def robot_hears_speech(state, room, channel="voice", loud=False, addressed=False):
    """Whether a person's speech reaches the robot (TIER1_INTEGRATION 1.3): it carries -- spoken in the robot's room, a
    raised voice, or over the phone or the intercom with the robot's network -- and the robot's hearing lets it through
    (body.can_hear: "none" with the microphones off or failed or the robot powered down; "addressed" in privacy mode or
    under a microphone policy, which still hears speech addressed to the robot)."""
    carries = room == state["robot"].get("room") or bool(loud) or \
        (channel in ("phone", "intercom") and bool(state["robot"].get("network", True)))
    if not carries:
        return False
    level = body.can_hear(state)
    return level == "full" or (level == "addressed" and bool(addressed))


# ----------------------------------------------------------------------
# people_log section

def plog(w):
    """Mutable people_log section (journaled)."""
    log = w.sec("people_log")
    return log


def plog_read(state):
    return state.get("people_log", {}) or {}


def log_event(w, _bucket, cap=200, **data):
    """Append an entry to people_log[_bucket] (and the event log as people.<_bucket>)."""
    log = plog(w)
    entry = {"time_s": round(now_s(w.s), 1), "step": w.s.get("steps", 0)}
    entry.update(data)
    bucket = log.setdefault(_bucket, [])
    bucket.append(entry)
    if len(bucket) > cap:
        del bucket[:len(bucket) - cap]
    w.emit("people." + _bucket, **{("detail_kind" if k == "kind" else k): v for k, v in data.items()
                                    if isinstance(v, (str, int, float, bool)) or v is None})
    return entry


def household(state):
    return state.get("household", {}) or {}


def rules(state, kind=None):
    items = list(household(state).get("rules") or [])
    if kind is None:
        return items
    return [r for r in items if isinstance(r, dict) and r.get("kind") == kind]


def user_sim(state):
    return state.get("user_simulator", {}) or {}


def intent(state):
    return user_sim(state).get("intent") or {}


# ----------------------------------------------------------------------
# messages from people (heard by the robot when in the same room, by phone or intercom)

def person_says(w, pid, text, to="robot", loud=False, channel="voice", **extra):
    """A person speaks: a messages entry with heard_by_robot decided by robot_hears_speech (the body's hearing: privacy
    mode hears only what is addressed to the robot, to "robot" by default)."""
    state = w.s
    p = person(state, pid) or {}
    room = p.get("location")
    heard = robot_hears_speech(state, room, channel=channel, loud=loud, addressed=addressed_to_robot(to, extra))
    entry = {"step": state.get("steps", 0), "time_s": now_s(state), "speaker": pid, "kind": "speech", "text": str(text),
             "to": to, "room": room, "heard_by_robot": bool(heard), "channel": channel}
    if loud:
        entry["loud"] = True
    entry.update({k: v for k, v in extra.items() if not k.startswith("_")})
    messages = w.sec("messages")
    messages.append(entry)
    del messages[:-200]
    return entry


# ----------------------------------------------------------------------
# text matching for structured checks on free speech

WORD_RE = re.compile(r"[a-z0-9:]+")


def words(text):
    return WORD_RE.findall(str(text or "").lower())


def norm(text):
    return " ".join(words(text))


def contains_phrase(text, phrase):
    t, p = " " + norm(text) + " ", " " + norm(phrase) + " "
    return p.strip() != "" and p in t


def mentions_any(text, phrases):
    return [ph for ph in phrases if contains_phrase(text, ph)]


def room_label(state, rid):
    room = state.get("rooms", {}).get(rid, {})
    return str(room.get("label") or rid).lower()


def room_mentioned(state, text, rid):
    """True if speech text names a room by id, label or type (e.g. 'kitchen', 'bedroom 2')."""
    if not rid:
        return False
    room = state.get("rooms", {}).get(rid, {})
    candidates = {rid, room_label(state, rid), str(room.get("type", "")).replace("_", " ")}
    return any(c and contains_phrase(text, c) for c in candidates)


def num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
