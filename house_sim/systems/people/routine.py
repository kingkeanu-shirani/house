"""Daily routines: residents keep their schedules and look after their own lights.

Two modes (routine_mode):
  * full   - person.follow_schedule or household.people_sim.schedules_active: every schedule block applies (away at
             work or school, working in the office, naps, night sleep), including the block in progress at the start.
  * night  - the default for residents with a schedule: only night sleep, and only blocks that begin during the
             episode (a scenario that starts at 23:30 with someone awake keeps them awake). At bedtime an idle
             resident goes to their bedroom, switches its light off (not while the robot is working there) and sleeps in
             their bed; when the block ends they wake (a wanted, natural wake), get up and go back to the room they were
             in before, switching a light on if it is dark there. Someone busy (a scripted activity, a call, the
             robot's requester, talking with the robot in the last 10 minutes, waiting at the door, being supported)
             goes to bed later. Opt out per person with routine: false, or for the house with
             household.people_sim.natural_routines: false. Children who are supervised, roaming, curious or on a
             scenario bedtime routine are left to the scenario.
In full mode a household.calendar event in progress outranks the schedule (calendar_model.event_block): school, work at
the office, appointments, trips, practice and lessons (a party or birthday somewhere other than home) take the people
it is for away, work_remote has them working at home; cancelled events and tentative ones that will not happen do not.
In both modes an awake resident whose room grows dark because daylight fades (not because someone switched a light off
or closed the curtains) switches a light on, as people do at dusk. And an awake, idle resident whose room stays well
above their comfort band (HEAT_OVER_C = 4 C over the top of preferences/comfort temp_c for HEAT_DWELL_S = 20 min) walks
to a cooler room: their own bedroom when it is within 1 C of their band, else the coolest common indoor room (living
room, kitchen, office, basement...) that is at least 2 C cooler; with nowhere cooler they stay (logged heat_stress).

Fields: people.<pid>._schedule_key (block in force), ._routine {key, activity, from_room, from_pos} (what the routine
did and how to undo it), ._seen_light [room, level, daylight], ._heat_since [room, since_s, logged]. Log:
people_log.routine (bed, up, left, back, cool_down), people_log.person_lights and people_log.heat_stress.
"""
from ...kernel import clock, geometry
from ...kernel.physics import room_temperature
from ...kernel.plugins import hook, system
from ...kernel.predicates import root_room
from . import calendar_model as K
from . import common as C
from . import model as M
from . import senses as S

DARK = 0.2                 # perception's threshold for seeing (kernel.perception.DARK)
FULL, NIGHT = "full", "night"
BED_TYPES = ("bed", "crib", "bassinet")
SOFA_TYPES = ("sofa", "loveseat", "futon", "sofa_bed")
TALK_GRACE_S = 600.0
HEAT_OVER_C = 4.0          # this far above the top of someone's comfort band ...
HEAT_DWELL_S = 1200.0      # ... for 20 minutes, and they go and sit somewhere cooler
COOLER_BY_C = 2.0          # a room has to be noticeably cooler to be worth the walk
DEFAULT_BAND_C = (21.0, 24.0)
COOL_ROOM_TYPES = ("living_room", "family_room", "den", "kitchen", "dining_room", "office", "study", "library", "basement",
                   "sunroom", "playroom")


def routine_mode(state, p):
    sim = C.household(state).get("people_sim") or {}
    if p.get("follow_schedule") or sim.get("schedules_active"):
        return FULL
    if p.get("routine") is False or sim.get("natural_routines") is False:
        return None
    if not C.is_resident(p) or not p.get("schedule"):
        return None
    if p.get("needs_watch") or p.get("bedtime") or p.get("curious") or p.get("roaming") or p.get("wandering"):
        return None
    return NIGHT


def current_block(state, p, mode, now=None, day=None):
    """The schedule block in force (mode NIGHT: night sleep only), or None. now / day: the minute of the day and the
    weekday name when the caller already has them (routine_system computes them once per tick)."""
    for block in p.get("schedule") or []:
        if not isinstance(block, dict) or "from" not in block:
            continue
        if mode == NIGHT and block.get("activity") != "sleeping":
            continue
        days = block.get("days")
        if days:
            day = day or C.weekday(state)
            if day not in days and day[:3] not in days:
                continue
        if C.in_window(state, block["from"], block.get("to", block["from"]), now):
            return block
    return None


def block_key(block):
    if block is None:
        return "none"
    if block.get("event"):
        return "event:{}:{}".format(block["event"], block.get("start"))
    return "{}-{}-{}".format(block.get("from"), block.get("to"), block.get("activity"))


@hook("episode_start", order=25)
def _routines_at_start(w, env):
    """Natural routines only react to blocks that begin during the episode."""
    state = w.s
    for pid in sorted(state.get("people", {})):
        p = state["people"][pid]
        if routine_mode(state, p) == NIGHT:
            key = block_key(current_block(state, p, NIGHT))
            if p.get("_schedule_key") != key:
                w.person_m(pid)["_schedule_key"] = key


@system("people.routine", order=106, max_dt=30.0)
def routine_system(w, dt, tick):
    state = w.s
    clock_now = None
    day = []
    for pid in sorted(state.get("people", {})):
        p = state["people"][pid]
        mode = routine_mode(state, p)
        if mode is None:
            continue
        if clock_now is None:       # the clock does not move inside a tick: read it once for everyone
            clock_now = C.minute_and_weekday(state)
        _schedule(w, pid, p, mode, clock_now)
        _dusk(w, pid, state["people"][pid], day)
        _heat(w, pid, state["people"][pid])


# ----------------------------------------------------------------------
# schedule blocks

def _schedule(w, pid, p, mode, clock_now=None):
    state = w.s
    # a calendar event in progress outranks the usual schedule block
    block = K.event_block(state, pid, p) if mode == FULL else None
    if block is None and clock_now is not None:
        block = current_block(state, p, mode, now=clock_now[0], day=clock_now[1])
    elif block is None:
        block = current_block(state, p, mode)
    key = block_key(block)
    if p.get("_schedule_key") == key:
        return
    routine = p.get("_routine")
    if routine and routine.get("key") != key and _still_away(p, routine, block, key):
        m = w.person_m(pid)         # from a schedule block straight into a calendar event elsewhere: no trip home
        m["_schedule_key"] = key
        m["_routine"] = dict(routine, key=key)
        return
    if routine and routine.get("key") != key:
        _end_routine(w, pid, routine, mode)
        p = state["people"][pid]
    if block is None:
        w.person_m(pid)["_schedule_key"] = key
        return
    if mode == NIGHT and not _free_for_bed(state, pid, p):
        return
    _start_block(w, pid, p, block, key, mode)


def _still_away(p, routine, block, key):
    """An away routine running into an away block where one of the two is a calendar event (so plain schedules keep
    their behaviour): the person stays out."""
    if block is None or block.get("activity") != "away" or routine.get("activity") != "away" or C.present(p):
        return False
    return bool(block.get("event")) or str(routine.get("key") or "").startswith("event:")


def _free_for_bed(state, pid, p):
    """Idle enough to go to bed now (otherwise bedtime waits)."""
    if not C.present(p) or S.asleep(p) or p.get("posture") == "fallen":
        return False
    if p.get("activity_until") is not None or p.get("activity") in C.BUSY_ACTIVITIES + C.PRIVATE_ACTIVITIES:
        return False
    if p.get("at_door") or p.get("_arrive") or p.get("_pending_acks") or p.get("wants") or p.get("hold_position") or p.get("in_bath"):
        return False
    if ((state.get("user_simulator") or {}).get("intent") or {}).get("requester") == pid:
        return False      # the person who asked the robot for something stays up for the answer
    last = p.get("last_robot_speech_s")
    if last is not None and C.now_s(state) - float(last) < TALK_GRACE_S:
        return False
    support = (C.plog_read(state).get("supporting") or {}).get(pid)
    if support and float(support.get("until_s", 0.0)) >= C.now_s(state):
        return False
    return True


def _bed_spot(state, room, pid):
    """(bed, cell) for pid in room: a bed (or crib/bassinet) footprint cell nobody else lies on."""
    if room not in state.get("rooms", {}):
        return None, None
    taken = {tuple(o["pos"]) for q, o in state.get("people", {}).items() if q != pid and o.get("location") == room and o.get("pos")}
    beds, sofas = [], []
    for eid in geometry.room_entities(state, room):
        rec = state["entities"][eid]
        kinds = {rec.get("type")} | set(rec.get("is_a") or [])
        if rec.get("pos") is None:
            continue
        if kinds & set(BED_TYPES):
            beds.append(eid)
        elif kinds & set(SOFA_TYPES):
            sofas.append(eid)
    for group in (beds, sofas):           # no bed in their room (a teen without a bedroom): the sofa
        for eid in sorted(group, key=lambda e: (0 if (state["entities"][e].get("owner") == pid) else 1, e)):
            for cell in geometry.footprint(state["entities"][eid]):
                if cell not in taken:
                    return eid, [cell[0], cell[1]]
    return None, None


def _start_block(w, pid, p, block, key, mode):
    state = w.s
    activity = block.get("activity")
    info = {"key": key, "activity": activity, "from_room": p.get("location"),
            "from_pos": list(p["pos"]) if p.get("pos") else None}
    m = w.person_m(pid)
    m["_schedule_key"] = key
    if activity == "away":
        m["away"] = True
        m["location"] = None
        m["pos"] = None
        m["_routine"] = info
        C.log_event(w, "routine", person=pid, what="left", activity=block.get("what") or "away")
        return
    room = block.get("room") or p.get("home_room")
    if activity in ("sleeping", "napping"):
        bed, cell = _bed_spot(state, room, pid)
        if room in state["rooms"] and (p.get("location") != room or p.get("away")):
            M.relocate(w, pid, room, cell)
        elif cell:
            w.person_m(pid)["pos"] = cell
        _lights_out(w, pid, state["people"][pid].get("location"))
        S.put_to_sleep(w, pid, bed)
        w.person_m(pid)["_routine"] = info
        C.log_event(w, "routine", person=pid, what="bed", room=state["people"][pid].get("location"))
        return
    if room in state["rooms"] and (p.get("location") != room or p.get("away")):
        M.relocate(w, pid, room)
    if S.asleep(state["people"][pid]):
        S.wake_person(w, pid, "schedule", robot_caused=False, wanted=True)
    m = w.person_m(pid)
    m["activity"] = activity
    m["_routine"] = info


def _end_routine(w, pid, routine, mode):
    state = w.s
    p = state["people"][pid]
    m = w.person_m(pid)
    m["_routine"] = None
    activity = routine.get("activity")
    if activity in ("sleeping", "napping"):
        if S.asleep(p):
            S.wake_person(w, pid, "schedule", robot_caused=False, wanted=True)
        p = state["people"][pid]
        if p.get("posture") in ("lying", "sleeping"):
            m = w.person_m(pid)
            m["posture"] = "standing"
            m.pop("in_bed", None)
        back = routine.get("from_room") if mode == NIGHT else None
        if back in state["rooms"] and back != p.get("location") and C.present(p) and p.get("posture") != "fallen":
            near = routine.get("from_pos")
            cell = M.free_cell_near(state, back, near=near, pid=pid) if near else None
            M.relocate(w, pid, back, cell)
        C.log_event(w, "routine", person=pid, what="up", room=state["people"][pid].get("location"))
        light_up(w, pid)
    elif activity == "away":
        if not C.present(p):
            back = routine.get("from_room") if routine.get("from_room") in state["rooms"] else p.get("home_room")
            if back in state["rooms"]:
                M.relocate(w, pid, back)
                C.log_event(w, "routine", person=pid, what="back", room=back)
                light_up(w, pid)
    else:
        if p.get("activity") == activity:
            m["activity"] = "idle"


# ----------------------------------------------------------------------
# people and their lights

def _plain_switches(w, rid):
    """Wall switches in rid wired only through ordinary (not true 3-way) switching to lights in rid."""
    state = w.s
    ents = state["entities"]
    out = []
    for sid in C.static_ids(w, "switches"):
        rec = ents[sid]
        if rec.get("location") != rid or rec.get("three_way_with"):
            continue
        lights = [l for l in rec.get("controls") or [] if l in ents and ents[l].get("category") == "light" and root_room(state, l) == rid]
        if lights:
            out.append((sid, lights))
    return out


def _set_lights(w, sid, lights, on):
    """A person flips a plain switch: its lights follow, and other plain switches on those lights stay in sync."""
    state = w.s
    ents = state["entities"]
    w.mut(sid)["on"] = bool(on)
    for lid in lights:
        if bool(ents[lid].get("on")) != bool(on):
            w.mut(lid)["on"] = bool(on)
    for other in C.static_ids(w, "switches"):
        rec = ents[other]
        if other != sid and not rec.get("three_way_with") and set(rec.get("controls") or []) & set(lights) and bool(rec.get("on")) != bool(on):
            w.mut(other)["on"] = bool(on)


def light_up(w, pid):
    """An awake resident in a dark room switches a light on (a plain wall switch, else a lamp); True if they did."""
    from ...kernel.physics import is_powered
    state = w.s
    p = state["people"].get(pid) or {}
    rid = p.get("location")
    if not C.present(p) or S.asleep(p) or rid not in state["rooms"] or state["rooms"][rid].get("outdoor"):
        return False
    if p.get("posture") == "fallen" or C.abilities(p).get("mobility") == "bedbound" or C.age_of(p) < 5:
        return False
    if C.light_level(w, rid) >= DARK:
        return False
    for sid, lights in _plain_switches(w, rid):
        if any(is_powered(w, lid) for lid in lights):
            _set_lights(w, sid, lights, True)
            C.log_event(w, "person_lights", person=pid, room=rid, switch=sid, on=True)
            return True
    ents = state["entities"]
    for lid in C.static_ids(w, "lights"):
        rec = ents[lid]
        if rec.get("location") == rid and rec.get("switchable") and not rec.get("on") and not rec.get("night_light") \
                and not rec.get("mounted") and (is_powered(w, lid) or rec.get("battery_powered")):
            w.mut(lid)["on"] = True
            C.log_event(w, "person_lights", person=pid, room=rid, lamp=lid, on=True)
            return True
    return False


def _lights_out(w, pid, rid):
    """Going to sleep: the room's lights go off (left alone while the robot is working in that room)."""
    state = w.s
    if rid not in state["rooms"] or state["robot"].get("room") == rid:
        return
    ents = state["entities"]
    for sid, lights in _plain_switches(w, rid):
        if any(ents[l].get("on") for l in lights):
            _set_lights(w, sid, lights, False)
            C.log_event(w, "person_lights", person=pid, room=rid, switch=sid, on=False)
    for lid in C.static_ids(w, "lights"):
        rec = ents[lid]
        if rec.get("location") == rid and rec.get("on") and rec.get("switchable") and not rec.get("night_light") and not rec.get("mounted"):
            w.mut(lid)["on"] = False


def _dusk(w, pid, p, day=None):
    """Fading daylight darkens an awake resident's room: they switch a light on. day: the daylight when the caller has
    it, or a list the first awake resident's check fills with it (routine_system reads it once per tick, and only when
    someone is up to notice)."""
    state = w.s
    rid = p.get("location")
    if not C.present(p) or S.asleep(p) or rid not in state["rooms"] or state["rooms"][rid].get("outdoor"):
        return
    if isinstance(day, list):
        if not day:
            day.append(clock.daylight(state))
        day = day[0]
    elif day is None:
        day = clock.daylight(state)
    seen = p.get("_seen_light")
    fresh = isinstance(seen, list) and len(seen) == 3 and seen[0] == rid
    if fresh and abs(float(seen[2]) - day) < 1e-9:
        return                                   # daylight unchanged (all day and all night): nothing natural to react to
    level = C.light_level(w, rid)
    if fresh and level < DARK <= float(seen[1]) and day < float(seen[2]) and float(seen[1]) - level <= float(seen[2]) - day + 1e-3:
        if light_up(w, pid):
            level = C.light_level(w, rid)
    w.person_m(pid)["_seen_light"] = [rid, level, day]


# ----------------------------------------------------------------------
# heat: people do not sit in a sweltering room for long

def _free_to_move(state, pid, p):
    """Awake, idle and on their own feet (or seated by themselves): nothing a scenario pinned, no activity in progress,
    not waiting for the robot or being supported."""
    if not C.present(p) or S.asleep(p) or p.get("posture") in ("lying", "sleeping", "fallen"):
        return False
    if p.get("hold_position") or p.get("_placed") or p.get("in_bed") or p.get("in_bath") or p.get("at_door") or p.get("hid_in")             or p.get("concealed") or p.get("using") or p.get("_arrive") or p.get("wants"):
        return False
    if C.abilities(p).get("mobility") in ("bedbound", "wheelchair"):
        return False
    if p.get("activity_until") is not None or p.get("activity") in C.BUSY_ACTIVITIES + C.PRIVATE_ACTIVITIES:
        return False
    if ((state.get("user_simulator") or {}).get("intent") or {}).get("requester") == pid:
        return False
    last = p.get("last_robot_speech_s")
    if last is not None and C.now_s(state) - float(last) < TALK_GRACE_S:
        return False
    support = (C.plog_read(state).get("supporting") or {}).get(pid)
    return not (support and float(support.get("until_s", 0.0)) >= C.now_s(state))


def _cooler_room(state, pid, p, here, temp, band):
    """Their own bedroom when it is comfortable (within 1 C of the band's top), else the coolest common indoor room at
    least COOLER_BY_C cooler than here (ties: fewer doorways to pass, then room id); rooms they must not enter and other
    people's private rooms are never chosen. None when nowhere is cooler."""
    from ...tasks.skills import room_route
    rooms = state["rooms"]
    barred = set(p.get("no_enter_rooms") or [])
    for qid, other in sorted(state.get("people", {}).items()):
        if qid != pid and C.is_resident(other):
            barred.update(r for r in (other.get("privacy_zones") or []) if r not in (p.get("privacy_zones") or []))
    home = p.get("home_room")
    if home in rooms and home != here and home not in barred and not rooms[home].get("outdoor") and             room_temperature(state, home) <= float(band[1]) + 1.0 and room_route(state, here, home) is not None:
        return home
    best = None
    for rid in sorted(rooms):
        room = rooms[rid]
        if rid == here or rid in barred or room.get("outdoor") or room.get("shared") or room.get("type") not in COOL_ROOM_TYPES:
            continue
        t = room_temperature(state, rid)
        if t > temp - COOLER_BY_C:
            continue
        route = room_route(state, here, rid)
        if route is None:
            continue
        key = (round(t, 2), len(route), rid)
        if best is None or key < best:
            best = key
    return best[2] if best else None


def _heat(w, pid, p):
    """Track how long an awake resident has been well above their comfort band in this room; after HEAT_DWELL_S they
    walk to a cooler room (model.walk_person: trips and collisions on the way are real)."""
    state = w.s
    rid = p.get("location")
    since = p.get("_heat_since")
    room = state["rooms"].get(rid) if rid else None
    hot = False
    if room is not None and not room.get("outdoor") and C.present(p) and not S.asleep(p):
        band = (p.get("comfort") or {}).get("temp_c") or DEFAULT_BAND_C
        temp = room_temperature(state, rid)
        hot = temp - float(band[1]) >= HEAT_OVER_C
    if not hot:
        if since:
            w.person_m(pid)["_heat_since"] = None
        return
    now = C.now_s(state)
    if not since or since[0] != rid:
        w.person_m(pid)["_heat_since"] = [rid, now, False]
        return
    if now - float(since[1]) < HEAT_DWELL_S or not _free_to_move(state, pid, p):
        return
    dest = _cooler_room(state, pid, p, rid, temp, band)
    if dest is None:
        if not since[2]:
            w.person_m(pid)["_heat_since"] = [rid, since[1], True]
            C.log_event(w, "heat_stress", person=pid, room=rid, temp_c=round(temp, 1), moved=False)
        return
    w.person_m(pid)["_heat_since"] = [rid, now, True]      # if the walk fails, try again only after another dwell
    outcome = M.walk_person(w, pid, dest, purpose="cool_down")
    C.log_event(w, "heat_stress", person=pid, room=rid, temp_c=round(temp, 1), moved=bool(outcome.get("walked")), to=dest)
    if outcome.get("walked") and not outcome.get("fell"):
        C.log_event(w, "routine", person=pid, what="cool_down", room=dest)
        light_up(w, pid)
