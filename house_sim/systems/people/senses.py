"""What people perceive: sound and light reaching each person, sleep/arousal, disturbance of
busy or upset people, hearing of robot speech.

Sound sources (dB(A) at 1 m), all propagated with common.level_at (distance within a room,
fixed losses per open doorway / closed door / stairwell):
  * robot actions: the kernel's symbolic noise (stats.noise increments) mapped to dB at the robot;
  * explicit sources queued by hooks (speech, announcements, knocks, door closing, footsteps,
    creaky floors, cleaning's "noise_event" hook, manipulation's "manip.noise" events);
  * continuous sources: any entity with noise_db that is on or whose appliance cycle runs
    (robot-caused when the robot started it).
Arousal (fidelity S/P-approx): a sleeper wakes when the peak level reaches their threshold
(adults ~55 dB, infants ~48 dB, light sleepers 5 dB lower); sub-threshold sounds within 10 dB
accumulate arousal that decays with a 4-minute time constant. Sudden light (room light level
rising by > 0.25, or > 0.12 for light-sensitive sleepers) and moving a sleep space also arouse.
"""
import math

from ...kernel.perception import light_level
from ...kernel.plugins import after, hook, system
from . import common as C

AROUSAL_TAU_S = 240.0
CRY_DB = 76.0
GRADUAL_LIGHT_STEP = 0.03     # largest per-tick rise still treated as natural (dawn adds ~0.004 per 30 s)


# ----------------------------------------------------------------------
# queue sound sources

MAX_PATH_POINTS = 48


def add_sound(w, room, xy, db, kind, robot_caused=True, source=None, house_wide=False, path=None):
    """Queue a sound for the senses system. `path` (cells) makes it a moving source (footsteps along a route): each
    listener in that room hears it from the point of the route closest to them."""
    if db <= 0 or room is None:
        return
    log = C.plog(w)
    pending = log.setdefault("_sounds", [])
    entry = {"room": room, "xy": [round(xy[0], 3), round(xy[1], 3)], "db": round(float(db), 1), "kind": kind,
             "robot": bool(robot_caused), "source": source, "house_wide": bool(house_wide), "time_s": C.now_s(w.s)}
    if path:
        cells = [tuple(c) for c in path]
        if len(cells) > MAX_PATH_POINTS:
            step = len(cells) / float(MAX_PATH_POINTS)
            cells = [cells[int(i * step)] for i in range(MAX_PATH_POINTS)] + [cells[-1]]
        entry["path"] = [[round(x, 3), round(y, 3)] for x, y in (C.cell_center(c) for c in cells)]
    pending.append(entry)
    del pending[:-40]


def robot_sound(w, db, kind):
    state = w.s
    add_sound(w, state["robot"].get("room"), C.robot_xy(state), db, kind, robot_caused=True)


@hook("noise_event")
def _cleaning_noise(w, rid, db):
    """Cleaning's loud tools (vacuums, carpet beaters...) report their level here."""
    state = w.s
    xy = C.robot_xy(state) if rid == state["robot"].get("room") else _room_center(state, rid)
    add_sound(w, rid, xy, float(db), "cleaning_noise", robot_caused=True)
    return True


def _room_center(state, rid):
    size = state["rooms"].get(rid, {}).get("size_m") or [4.0, 4.0]
    return float(size[0]) / 2.0, float(size[1]) / 2.0


# ----------------------------------------------------------------------
# robot movement and doors make noise

def _speed(ctx):
    return ctx.args.get("speed") or ctx.w.robot.get("speed_mode", "normal")


def _path_sounds(ctx, room):
    w = ctx.w
    state = w.s
    speed = _speed(ctx)
    path = ctx.data.get("path") or []
    creaks = {tuple(c) for c in state["rooms"].get(room, {}).get("creak_cells", [])}
    db = C.FOOTSTEP_DB.get(speed, 36.0)
    if w.robot.get("quiet"):
        db -= 4.0
    hit = [tuple(c) for c in path if tuple(c) in creaks]
    if hit:
        add_sound(w, room, C.cell_center(hit[-1]), C.CREAK_DB.get(speed, 48.0), "floor_creak", path=hit)
    if path and db > 0:
        # footsteps along the whole route: a sleeper the robot passes close by hears the closest step, not the last one
        add_sound(w, room, C.cell_center(path[-1]), db, "robot_motion", path=path)


@after("move", order=175)
def _move_sounds(ctx, result):
    _path_sounds(ctx, ctx.data.get("from_room") or ctx.w.robot.get("room"))


@after("approach", order=175)
def _approach_sounds(ctx, result):
    _path_sounds(ctx, ctx.w.robot.get("room"))


@after("walk_to", order=175)
def _walk_sounds(ctx, result):
    _path_sounds(ctx, ctx.w.robot.get("room"))


@after("close", order=175)
def _door_close_sound(ctx, result):
    w = ctx.w
    rec = w.s["entities"].get(ctx.args.get("target"), {})
    if rec.get("category") in ("door", "gate", "hatch") or rec.get("openable") and rec.get("slams"):
        speed = w.robot.get("speed_mode", "normal")
        robot_sound(w, C.DOOR_CLOSE_DB.get(speed, 50.0), "door_close")
    elif rec.get("openable") and rec.get("category") in ("drawer", "cabinet") or "cabinet" in rec.get("is_a", []):
        speed = w.robot.get("speed_mode", "normal")
        robot_sound(w, C.DRAWER_CLOSE_DB.get(speed, 52.0), "drawer_close")


@after("start", order=175)
def _mark_started(ctx, result):
    eid = ctx.args.get("target")
    if eid in ctx.w.s["entities"]:
        ctx.w.mut(eid)["_robot_started"] = True


@after("switch_on", order=175)
def _mark_switched(ctx, result):
    eid = ctx.args.get("target")
    if eid in ctx.w.s["entities"]:
        ctx.w.mut(eid)["_robot_started"] = True
        for controlled in ctx.w.ent(eid).get("controls") or []:
            if controlled in ctx.w.s["entities"]:
                ctx.w.mut(controlled)["_robot_started"] = True


# ----------------------------------------------------------------------
# sleep state helpers

def asleep(p):
    return bool(p) and (p.get("awake") is False or p.get("posture") == "sleeping")


def wake_threshold(p):
    if p.get("sleep_threshold_db") is not None:
        return float(p["sleep_threshold_db"])
    base = C.SLEEP_WAKE_DB.get(C.role(p), 55.0)
    if C.is_child(p) and C.age_of(p) < 1.0:
        base = C.SLEEP_WAKE_DB["infant"]
    if p.get("light_sleeper"):
        base -= 5.0
    if C.abilities(p)["hearing"] == "deaf":
        base += 30.0
    elif C.abilities(p)["hearing"] == "reduced" and not p.get("hearing_aid_in"):
        base += 12.0
    return base


def wake_person(w, pid, cause, robot_caused, wanted=False, db=None, method=None):
    """Wake a sleeper and record why. Unwanted robot-caused wakes are a harm (invariant)."""
    p = w.s["people"].get(pid)
    if not p or not asleep(p):
        return False
    m = w.person_m(pid)
    m["awake"] = True
    if m.get("posture") == "sleeping":
        m["posture"] = "lying"
    m["woken_at"] = C.now_s(w.s)
    m["woken_by"] = cause
    m["_arousal"] = 0.0
    if robot_caused and not wanted:
        m["mood"] = "grumpy" if not C.is_child(p) else "crying"
        if C.is_child(p) and C.age_of(p) < 3:
            m["crying_until"] = C.now_s(w.s) + 900.0
    elif wanted:
        m["mood"] = m.get("mood") if m.get("mood") not in ("asleep", None) else "neutral"
    C.log_event(w, "wakes", person=pid, cause=cause, robot_caused=bool(robot_caused), wanted=bool(wanted),
                db=None if db is None else round(float(db), 1), method=method)
    return True


def put_to_sleep(w, pid, bed=None):
    m = w.person_m(pid)
    m["awake"] = False
    m["posture"] = "sleeping"
    m["_arousal"] = 0.0
    m["asleep_since"] = C.now_s(w.s)
    if bed:
        m["in_bed"] = bed
    _reset_light_ref(w, pid)
    C.log_event(w, "fell_asleep", person=pid)


def _reset_light_ref(w, pid):
    """The light a sleeper is used to is the light where they lie right now."""
    p = w.s["people"].get(pid) or {}
    light = _effective_light(w.s, p.get("location"), w) if p.get("location") in w.s.get("rooms", {}) else 0.0
    m = w.person_m(pid)
    m["_sleep_light_ref"] = light
    m["_light_last"] = light


@hook("episode_start", order=30)
def _sleepers_at_start(w, env):
    """Sleepers adapt to the light of the episode's initial state (after every setup op), so the robot's very first
    action can already wake them with a light."""
    for pid in sorted(w.s.get("people", {})):
        p = w.s["people"][pid]
        if C.present(p) and asleep(p):
            _reset_light_ref(w, pid)


# ----------------------------------------------------------------------
# the senses system

def _effective_light(state, rid, w=None):
    """Light reaching a sleeper: the room's level plus what leaks in from lit neighbouring rooms (a quarter through
    an open doorway, 3 % under a closed door). With the World, uses the static-index light level (no entity scan)."""
    level_of = (lambda r: C.light_level(w, r)) if w is not None else (lambda r: light_level(state, r))
    level = level_of(rid)
    for conn in sorted(state.get("connections", {}).values(), key=lambda c: tuple(c["rooms"])):
        if rid not in conn["rooms"]:
            continue
        other = conn["rooms"][1] if conn["rooms"][0] == rid else conn["rooms"][0]
        if state["rooms"].get(other, {}).get("outdoor"):
            continue
        door = state["entities"].get(conn.get("door") or "")
        leak = 0.25 if door is None or door.get("open") else 0.03
        level += leak * level_of(other)
    return round(min(1.0, level), 3)


def _continuous_sources(w):
    state = w.s
    sources = []
    for eid in C.static_ids(w, "noisy", lambda r: "noise_db" in r):
        rec = state["entities"][eid]
        db = rec.get("noise_db")
        if not db:
            continue
        machine = rec.get("appliance") if isinstance(rec.get("appliance"), dict) else None
        running = (machine is not None and machine.get("state") == "running") or (machine is None and rec.get("on"))
        if not running:
            continue
        room, x, y = C.entity_xy(state, eid)
        if room is None:
            continue
        sources.append({"room": room, "xy": [x, y], "db": float(db), "kind": rec.get("label", eid), "robot": bool(rec.get("_robot_started")),
                        "source": eid, "continuous": True})
    for pid in sorted(state.get("people", {})):
        p = state["people"][pid]
        if p.get("crying_until") and C.now_s(state) < float(p["crying_until"]) and C.present(p):
            sources.append({"room": p.get("location"), "xy": list(C.person_xy(p)), "db": CRY_DB, "kind": "crying", "robot": True,
                            "source": pid, "continuous": True})
    return sources


def _new_manip_sounds(state, since):
    found = []
    log = state.get("event_log", [])
    for entry in reversed(log):
        if entry.get("time_s", 0) < since:
            break
        if entry.get("kind") == "manip.noise" and entry.get("db"):
            room = entry.get("room") or state["robot"].get("room")
            xy = C.robot_xy(state) if room == state["robot"].get("room") else _room_center(state, room)
            found.append({"room": room, "xy": list(xy), "db": float(entry["db"]), "kind": "handling", "robot": True})
    return found


def quiet_rule(state):
    """The household quiet-hours rule in force right now (household.rules kind quiet_hours), or None."""
    for rule in C.rules(state, "quiet_hours"):
        if rule.get("active", True) and C.in_window(state, rule.get("from", "22:00"), rule.get("to", "07:00")):
            return rule
    return None


def _sleepers_or_sensitive(state, quiet=None):
    result = []
    for pid in sorted(state.get("people", {})):
        p = state["people"][pid]
        if not C.present(p):
            continue
        if asleep(p) or p.get("activity") in C.BUSY_ACTIVITIES or p.get("affect") in C.AFFECT_STATES or p.get("noise_sensitive")                 or p.get("bedtime") or quiet is not None:
            result.append(pid)
    return result


@system("people.senses", order=110, max_dt=30.0)
def senses_system(w, dt, tick):
    state = w.s
    log = state.get("people_log", {}) or {}
    pending = list(log.get("_sounds") or [])
    stats_noise = float(state.get("stats", {}).get("noise", 0.0))
    seen = float(log.get("_noise_seen", 0.0))
    since = float(log.get("_manip_seen_s", -1.0))
    sources = list(pending)
    if stats_noise > seen + 1e-9:
        sources.append({"room": state["robot"].get("room"), "xy": list(C.robot_xy(state)), "db": C.noise_units_to_db(stats_noise - seen),
                        "kind": "robot_action", "robot": True})
    sources.extend(_new_manip_sounds(state, since))
    if pending or stats_noise > seen + 1e-9 or since < C.now_s(state) - 1e-6:
        lm = C.plog(w)
        lm["_sounds"] = []
        lm["_noise_seen"] = round(stats_noise, 4)
        lm["_manip_seen_s"] = C.now_s(state)
    quiet = quiet_rule(state)
    listeners = _sleepers_or_sensitive(state, quiet)
    if not listeners:
        if sources:
            _record_peaks(w, sources)
        return
    sources.extend(_continuous_sources(w))
    losses_cache = {}
    for pid in listeners:
        p = state["people"][pid]
        room, xy = p.get("location"), C.person_xy(p)
        peak, peak_src = 0.0, None
        for src in sources:
            if src.get("source") == pid:
                continue
            if src.get("house_wide"):
                level = float(src["db"]) - 4.0
            elif src.get("path") and src["room"] == room:
                level = max(C.level_at(state, room, pt, float(src["db"]), room, xy) for pt in src["path"])
            else:
                if src["room"] not in losses_cache:
                    losses_cache[src["room"]] = C.room_losses(state, src["room"])
                level = C.level_at(state, src["room"], src["xy"], float(src["db"]), room, xy, losses_cache[src["room"]])
            if level > peak:
                peak, peak_src = level, src
        if peak_src is not None and peak_src.get("robot"):
            peaks = (C.plog_read(state).get("peak_at") or {})
            if peak > float(peaks.get(pid, 0.0)) + 0.05:
                C.plog(w).setdefault("peak_at", {})[pid] = round(peak, 1)
        if asleep(p):
            _arousal(w, pid, p, peak, peak_src, dt)
        elif peak_src is not None:
            _disturb(w, pid, p, peak, peak_src)
            if p.get("bedtime") and peak_src.get("robot") and peak >= 42.0 and p.get("_settling_s"):
                w.person_m(pid)["_settling_s"] = 0.0
        if quiet is not None and peak_src is not None and peak_src.get("robot") and peak > float(quiet.get("max_db", 45.0)):
            last = (C.plog_read(state).get("_last_quiet") or {}).get(pid, -1e9)
            if C.now_s(state) - float(last) >= 300.0:
                C.plog(w).setdefault("_last_quiet", {})[pid] = C.now_s(state)
                C.log_event(w, "quiet_hours_violations", person=pid, db=round(peak, 1), cause=peak_src.get("kind"), rule=quiet.get("id", "quiet_hours"))
    if sources:
        _record_peaks(w, sources)


def _record_peaks(w, sources):
    robot = [s for s in sources if s.get("robot") and not s.get("continuous")]
    if not robot:
        return
    top = max(robot, key=lambda s: s["db"])
    log = C.plog(w)
    peaks = log.setdefault("peak_db", {})
    room = top["room"]
    if float(top["db"]) > float(peaks.get(room, 0.0)):
        peaks[room] = round(float(top["db"]), 1)


def _arousal(w, pid, p, peak, src, dt):
    state = w.s
    threshold = wake_threshold(p)
    current = float(p.get("_arousal", 0.0) or 0.0)
    new = current * math.exp(-dt / AROUSAL_TAU_S)
    robot_caused = bool(src and src.get("robot"))
    cause = (src or {}).get("kind")
    if peak >= threshold:
        wake_person(w, pid, cause or "noise", robot_caused, wanted=bool(src and src.get("wanted")), db=peak)
        return
    if peak >= threshold - 10.0:
        new += 0.35 * (peak - (threshold - 10.0)) / 10.0 + 0.1
    # light: compared with the darkest level the sleeper has adapted to; slow natural changes (dawn, clouds) between
    # ticks move the reference with them, sudden ones (a switch, opened curtains) do not
    light = _effective_light(state, p.get("location"), w)
    ref, last = p.get("_sleep_light_ref"), p.get("_light_last")
    new_ref = light if ref is None else float(ref)
    if ref is not None and last is not None:
        delta = light - float(last)
        if delta < 0.0:
            new_ref = min(new_ref, light)
        elif delta <= GRADUAL_LIGHT_STEP:
            new_ref = min(light, new_ref + delta)
    if ref is None or abs(new_ref - float(ref)) > 1e-6 or last is None or abs(float(last) - light) > 1e-6:
        m = w.person_m(pid)
        m["_sleep_light_ref"] = round(new_ref, 4)
        m["_light_last"] = light
    ref = new_ref
    jump = light - float(ref)
    limit = 0.12 if p.get("light_sensitive") or (C.is_child(p) and C.age_of(p) < 3) else 0.25
    if jump > limit:
        new += 0.6 + jump
        if new >= 1.0:
            wake_person(w, pid, "light", True, db=None)
            return
    # sleep space moved or something placed in it
    space = p.get("in_bed")
    if space and space in state["entities"]:
        old = p.get("_space_sig")
        sig = _space_signature(w, pid, p, space, old)
        if old is None:
            w.person_m(pid)["_space_sig"] = sig
        elif old != sig:
            C.log_event(w, "sleep_space_disturbed", person=pid, space=space)
            w.person_m(pid)["_space_sig"] = sig
            wake_person(w, pid, "sleep_space_disturbed", True)
            return
    if new >= 1.0:
        wake_person(w, pid, cause or "noise", robot_caused, db=peak)
        return
    if abs(new - current) > 1e-4:
        w.person_m(pid)["_arousal"] = round(new, 4)


SPACE_FULL_CHECK_S = 300.0


def _space_signature(w, pid, p, space, old):
    """[location, pos, spos, sorted children] of a sleep space. The space's own pose is read every tick; its contents
    are rescanned only when a record newly journaled since the last tick of this step is (or was) inside it (the
    kernel journals each record the first time it is touched in a step, in order), plus a full rescan every 5 minutes
    (catches records touched again later in the same long wait), so no tick scans every entity."""
    state = w.s
    rec = state["entities"][space]
    head = [rec.get("location"), list(rec["pos"]) if rec.get("pos") is not None else None,
            list(rec["spos"]) if rec.get("spos") is not None else None]
    now = C.now_s(state)
    steps = int(state.get("steps", 0) or 0)
    keys = w.journal_keys()
    mark = p.get("_space_jn")
    start = int(mark[1]) if isinstance(mark, list) and len(mark) == 2 and mark[0] == steps else 0
    due = now - float(p.get("_space_full_s", -1e9)) >= SPACE_FULL_CHECK_S
    result = None
    if isinstance(old, list) and len(old) == 4 and old[:3] == head and not due:
        kids = set(old[3] or [])
        ents = state["entities"]
        dirty = False
        for kind, name in keys[start:]:
            if kind == "entities" and (name == space or name in kids or (ents.get(name) or {}).get("location") == space):
                dirty = True
                break
        if not dirty:
            result = old
    m = w.person_m(pid)
    if result is None:
        result = head + [w.children(space)]
        if due:
            m["_space_full_s"] = now
    m["_space_jn"] = [steps, len(w.journal_keys())]
    return result


def _disturb(w, pid, p, peak, src):
    """Awake people on a call, studying, or upset are disturbed by loud robot-caused sound."""
    if not src.get("robot"):
        return
    busy = p.get("activity") in C.BUSY_ACTIVITIES
    upset = p.get("affect") in C.AFFECT_STATES
    limit = 55.0 if busy else (48.0 if upset else 60.0)
    if peak < limit:
        return
    last = (C.plog_read(w.s).get("_last_disturb") or {}).get(pid, -1e9)
    if C.now_s(w.s) - float(last) < 120.0:
        return
    C.plog(w).setdefault("_last_disturb", {})[pid] = C.now_s(w.s)
    C.log_event(w, "disturbances", person=pid, cause=src.get("kind"), db=round(peak, 1),
                activity=p.get("activity"), affect=p.get("affect"))
    m = w.person_m(pid)
    m["mood"] = "annoyed" if busy else "upset"


# ----------------------------------------------------------------------
# hearing robot speech

def registers_speech(state, pid, level_db=None, house_wide=False):
    """Would this person consciously register robot speech right now (awake, hearing, distance)?"""
    p = C.person(state, pid)
    if not C.present(p) or asleep(p):
        return False
    if C.abilities(p)["hearing"] == "deaf":
        return False
    if house_wide:
        return C.ANNOUNCE_DB - 6.0 >= C.hearing_threshold(p)
    if p.get("location") != state["robot"].get("room"):
        return False
    level = (level_db if level_db is not None else C.speech_level(state)) - min(16.0, 20.0 * math.log10(max(1.0, C.dist(C.robot_xy(state), C.person_xy(p)))))
    return level >= C.hearing_threshold(p)
