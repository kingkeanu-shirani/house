"""Time: interruptible waits, standing orders that act on their own, the notice bus and what the robot can perceive of
a condition (docs/design/TIER1_SWEEP.md 6 and 16.1; TIER1_INTEGRATION.md 1.3, 1.8 and 1.9; kernel v3.1).

Moved out of systems/core.py by the sweep's Phase 0 (A0, unchanged then); TIME-EVAL owns it from stage C. engine.py
imports this module (which registers the time verbs) and systems/core.py re-exports the public names of A0
(`core.OBSERVABLE_FIELDS` is the same set object, so a domain that extends it still extends the rule used here).

Actions (registration names as before; the new parameters are additive and default to today's behaviour):

    wait(seconds, until=?<predicate>, interrupt_on=?[kinds])
        today's fixed-length wait unless until / interrupt_on are given. `until` ends the wait at the first 30 s substep
        boundary (or at once) where it holds AND the robot observes it holding (holds_perceived: per referenced id,
        realism and the camera included, TIER1_INTEGRATION 1.3); a condition the robot cannot watch when the wait is
        issued is refused ("Wait only on what the robot can observe").
        `interrupt_on` lists notice kinds (NOTICE_KINDS; "urgent" is a group) that end the wait at the boundary after
        the robot perceived one. The step lasts the time actually waited; the message keeps its "Waited N s" prefix
        and says why it ended, with notes listing up to 10 notices perceived during the wait and the standing orders
        that ran.
    watch(condition, commands, persistent=false, mode=queue|auto, guard=?, max_runs=?, cooldown_s=?, expires_s=?)
        queue (default): today's standing order (its commands go to `pending`, run with run_pending). auto: while the
        robot is idle (a wait, a phase gap) the engine runs the commands itself at the substep where the order fires,
        each as a nested action under a World savepoint: a refused command is rolled back alone, logged in
        `order_log` (stats.order_refusals, never stats.invalid) and ends that firing; other orders go on. An auto
        order that fires during a busy action waits for the next idle moment; one due when a listening wait ends runs
        before the wait gives control back. Limits: 20 commands, max_runs (1; persistent 50), cooldown_s (persistent
        60), expires_s, 500 auto commands per step and simulated hour (a paused firing resumes where it stopped); wait,
        watch, cancel_watch, run_pending and undo cannot be auto commands. guard: a perceivable predicate checked
        before each command.
    cancel_watch(target), run_pending(): as before.

The notice bus (6.1-6.2): `World.notice(kind, ...)` posts something perceivable; robot_perceives() applies the P-approx
rules (sound: L = db - 6 per open hop - 20 per closed door along the quietest connection path, heard at 40 dB, speech
words at 55 dB to the robot or in its room; hearing from body.can_hear -- privacy mode drops only speech not addressed
to the robot --, the robot_hears hook may replace the propagation; visual: lit room and imaging, or the geometric
camera's own rule; remote: the robot's network). Perceived notices are stored in the list section "notices" (cap 200)
when the episode's state has that section (schema-3 tasks and configs that carry it: the bus is data-gated, so older
specs keep their exact states), and are always handed to a wait that listens for them. The kernel derives speech /
addressed (new heard messages), person_enters / person_leaves / pet_enters, alarm (an alarm that starts sounding: a
detector's alarm_state, its temporal pattern, beeping -- the bridge until the domains post their own), order (a
standing order fired), battery (the body's low / reserve warnings) and fault (a detected fault) itself. A notice names
its source only when the robot perceives it now (source_perceived: entity_seen / agent_seen per id).

What the robot observes of a condition (observed, perceivable, holds_perceived; stage-C fix of TIER1_INTEGRATION 1.3)
is judged per referenced id: a thing's glance field while the robot sees it (not a dark shape; geometric: in the head
camera's view at the level the field needs), a person's or pet's location / posture while it can tell who they are,
"X comes in / leaves" (a location compared with the robot's room) while it watches that room, an alarm by its sound,
the weather by the weather rule (never the process's bookkeeping).

Realistic perception judges what it observes on the rendered value, never the true one (review L1, K-LEAK): an entity
atom compares the field as the observation shows it now (realism.shown_entity: buckets and words such as "looks dry" or
"half", an unresolved look-alike's group values; a status app's or audible status's glance rendering), a person's or
pet's posture as shown (asleep and lying awake are both "lying"; a pet asleep, lying or resting is "resting"), an alarm
known by its sound as "alarm" while it sounds and "quiet" otherwise; and holds_perceived is that answer alone. So a
wait, a standing order or a guard tells the agent nothing its observation does not.
"""
import copy
import heapq
import weakref

from . import body
from .actions import action
from .errors import ActionError, require
from .plugins import ACTIONS, HOOKS
from .predicates import check, root_room, validate_predicate
from .world import EMIT_NOTICES, NOTICES_CAP

OBSERVABLE_FIELDS = {"open", "on", "locked", "wetness", "state", "level", "water_level", "alarm", "beeping",
                     "temperature_c", "cycle", "position", "occupied", "running", "lit", "condition", "alarm_state"}
# Domains may extend what standing orders can watch, e.g. core.OBSERVABLE_FIELDS.add("water_level")
# or core.AGENT_OBSERVABLE_FIELDS.add("thirst") (systems/core.py re-exports these very sets).
AGENT_OBSERVABLE_FIELDS = {"location", "awake", "posture", "activity", "mood"}
REALISTIC_WATCH_AGENT_FIELDS = {"location", "posture"}
NUMERIC_OPS = ("gt", "gte", "lt", "lte", "approx", "between")


def watch_condition_visible(w, rule, realism=None):
    """Standing orders may only watch what the robot can observe. In realistic perception that means glance-tier
    fields compared without exact numeric thresholds, people's/pets' location and posture, and no 'ref' operands
    into anything else. (The rule as it was before kernel v3.1; perceivable() is the current one.)"""
    if "all" in rule or "any" in rule:
        return all(watch_condition_visible(w, r, realism) for r in rule.get("all", rule.get("any", [])))
    if "not" in rule:
        return watch_condition_visible(w, rule["not"], realism)
    if any(k in rule for k in ("forall", "exists", "count")):
        return False
    if realism is not None:
        if "ref" in rule:
            return False
        path = rule["path"].split(".")
        if path[0] == "entities" and len(path) >= 3 and path[1] in w.s["entities"]:
            field = ".".join(path[2:])
            if realism.tier(w.s["entities"][path[1]], path[2]) != "glance" or rule.get("op") in NUMERIC_OPS:
                return False
        if path[0] in ("people", "pets") and (len(path) != 3 or path[2] not in REALISTIC_WATCH_AGENT_FIELDS):
            return False
    path = rule["path"].split(".")
    if path[0] in ("weather", "time_s", "clock"):
        return True
    if path[0] == "entities" and len(path) >= 3 and path[1] in w.s["entities"]:
        field = ".".join(path[2:])
        entity = w.s["entities"][path[1]]
        if field not in OBSERVABLE_FIELDS and field not in ("appliance.state", "appliance.remaining_s"):
            return False
        from .perception import Perception  # noqa
        visible = w.s["robot"]["room"] and _visible(w, path[1])
        remote = entity.get("remote_status") and w.robot.get("network", True)
        audible = entity.get("audible_status")
        return bool(visible or remote or audible)
    if path[0] in ("people", "pets") and len(path) == 3 and path[2] in AGENT_OBSERVABLE_FIELDS:
        group = w.s.get(path[0], {})
        return path[1] in group and group[path[1]].get("location") == w.robot["room"]
    return False


def _visible(w, eid):
    from .perception import Perception
    return Perception(None).visible(w.s, eid)


# ----------------------------------------------------------------------
# what the robot observes of a condition (6.5; TIER1_INTEGRATION 1.3: perceivable signals only, per referenced id)

ROOM_COMPARE_OPS = ("eq", "ne")         # "X comes in" / "X leaves": a location compared with the robot's own room
ALARM_STATES = ("alarm", "testing", "test")     # an alarm sounding (a smoke / CO detector's test sounds it too)
ALARM_PATTERNS = ("T3", "T4")           # the temporal-3 (smoke) and temporal-4 (CO) patterns
ALARM_DB = 95.0                         # a smoke / CO alarm at 1 m (UL 217: 85 dB at 3 m)
ALARM_CATEGORIES = ("smoke_detector", "smoke_alarm", "co_detector", "co_alarm", "smoke_co_detector", "combo_alarm",
                    "heat_detector", "alarm_panel", "security_alarm", "siren")
ALARM_FIELDS = ("alarm_state",)         # heard as well as seen: until / watch on an alarm sounding
WINDOW_WEATHER = ("condition",)         # what a window shows in realistic perception (no numbers)
SMALL_M = 0.15                          # realism.missed: only things smaller than this can be missed from afar


def _has_window(state, rid):
    return any(e.get("category") == "window" and e.get("location") == rid for e in state["entities"].values())


def _env_of(w, env=None):
    """The env a World belongs to (timeline.start keeps a weak reference), for the per-id rules that need its
    perception, camera view and realism; None for a bare World."""
    if env is not None:
        return env
    ref = getattr(w, "_env_ref", None)
    return ref() if ref is not None else None


def _realism_of(env):
    return getattr(env, "realism", None) if env is not None else None


def _room_dark(w, rid):
    """perception's seen_dark for room rid: True when the cameras give the robot no colour view of it (imaging off, or
    the room dark without a robot light). The light sum reads only the house's lights and windows (a static index):
    the until check runs every substep."""
    state = w.s
    count = getattr(w, "mutation_count", None)
    key = (count, rid, state.get("time_s"))
    memo = getattr(w, "_dark_memo", None)
    if count is not None and memo is not None and memo[0] == key:
        return memo[1]              # nothing was written since (lights, the robot's sensors and the clock included)
    if not body.imaging(state):
        dark = True
    else:
        from .perception import DARK, flashlight_on, light_level
        ids = w.static_index("timeline:lights", lambda r: r.get("category") in ("light", "window")) \
            if hasattr(w, "static_index") else None
        dark = not flashlight_on(state) and light_level(state, rid, ids) < DARK
    if count is not None:
        w._dark_memo = (key, dark)
    return dark


def entity_seen(w, eid, realism=None, env=None, field=None):
    """Whether the robot sees entity eid now well enough to read its glance fields (TIER1_INTEGRATION 1.3, per
    referenced id). Held things always. Room camera: perception's visibility, not a dark shape (the room lit and
    imaging on: a dark shape shows no field), realistic perception also not a small thing missed from this viewpoint
    (realism.missed). Geometric camera: the head camera at its current eye detects it above a shape (camera.acuity);
    realistic perception applies the detection band and the view level the field needs (realism.acuity_need), at the
    apparent size of an unresolved look-alike (realism.apparent_view)."""
    return _seen_view(w, eid, realism, env, field) is not None


def _seen_view(w, eid, realism=None, env=None, field=None):
    """entity_seen's answer with what it saw: None when the robot does not see eid well enough; else the realistic
    geometric camera's ViewInfo of it (apparent size applied), or True (room camera, held things, oracle)."""
    state = w.s
    record = state["entities"].get(eid)
    if record is None or not body.is_on(state):
        return None
    robot = state["robot"]
    if record.get("location") == "robot" or eid in [v for v in (robot.get("hands") or {}).values() if v]:
        return True
    env = _env_of(w, env)
    if isinstance(robot.get("view"), dict):
        from . import camera
        found = camera.entity_view_now(env, eid) if env is not None else camera.acuity(state, eid)
        if found is None:
            return None
        info = found["view"]
        if realism is None:
            return True if info.get("detected", True) is not False and info.get("level") != "shape" else None
        from .realism import VIEW_LEVELS
        # the full ViewInfo (light, glare, faces: what renders the field), at an unresolved look-alike's apparent size;
        # entity_view_now's cut view only says that the camera sees it at all
        full = camera.entity_view_full(env, eid) if env is not None else info
        info = realism.apparent_view(state, eid, record, full)
        if info is None:
            return None
        need = realism.acuity_need(record, field) if field else "recognize"
        need = "identify" if need == "inspect" else need
        if VIEW_LEVELS.get(info.get("level"), 0) >= VIEW_LEVELS.get(need, VIEW_LEVELS["identify"]) and \
                realism.geo_detected(state, eid, record, info):
            return info
        return None
    if env is not None and getattr(env, "perception", None) is not None:
        perception = env.perception
    else:
        from .perception import Perception
        perception = Perception(None)
    if not perception.visible(state, eid) or _room_dark(w, robot.get("room")):
        return None
    if realism is not None and realism.apparent_max(eid, record) < SMALL_M and \
            realism.missed(state, eid, record, realism.context(state, eid, record)):
        return None
    return True


def agent_seen(w, aid, realism=None, env=None, lit=False):
    """Whether the robot perceives person or pet aid now (TIER1_INTEGRATION 1.3): in its room and not hiding. Room
    camera: the oracle observation lists everyone in the room (also in the dark); realistic perception only in a lit
    room with imaging on (in the dark they are shapes, no one). Geometric camera: the head camera in its current pose
    sees them above a shape (realistic: well enough to identify them; camera.agent_level_now). lit: a lit room in
    oracle room mode too (a notice names its source only when the robot saw it)."""
    state = w.s
    if not body.is_on(state):
        return False
    agent = (state.get("people") or {}).get(aid)
    if agent is None:
        agent = (state.get("pets") or {}).get(aid)
    rid = state["robot"].get("room")
    if agent is None or agent.get("location") != rid or (agent.get("concealed") and not agent.get("revealed")):
        return False
    if isinstance(state["robot"].get("view"), dict):
        env = _env_of(w, env)
        if env is None:
            return False
        from . import camera
        level = camera.agent_level_now(env, aid)
        if level is None:
            return False
        return level == "identify" if realism is not None else level != "shape"
    if realism is None and not lit:
        return True
    return not _room_dark(w, rid)


def _room_atom(rule, rid):
    """A people / pets location compared (eq / ne) with the robot's own room: "X comes in" / "X leaves"."""
    parts = rule["path"].split(".")
    return len(parts) == 3 and parts[0] in ("people", "pets") and parts[2] == "location" and \
        rule.get("op") in ROOM_COMPARE_OPS and rule.get("value") == rid and rid is not None and "ref" not in rule


def _room_watchable(w, realism):
    """The robot can watch its own room for someone coming or going: room camera -- the oracle observation lists
    everyone in the room, realistic perception needs it lit with imaging on; geometric camera -- imaging on."""
    state = w.s
    if isinstance(state["robot"].get("view"), dict):
        return body.imaging(state)
    return realism is None or not _room_dark(w, state["robot"].get("room"))


def _absence_seen(w, aid, realism):
    """Whether the robot can tell that aid is not in its room now (aid not perceived there): room camera -- whenever
    it watches the room (_room_watchable); geometric camera -- only for someone its head tracks (view.track: the
    camera follows them, so it sees them go)."""
    state = w.s
    view = state["robot"].get("view")
    if isinstance(view, dict):
        return body.imaging(state) and aid in (view.get("track") or ())
    return _room_watchable(w, realism)


def _alarm_heard(w, eid, rule):
    """An alarm_state compared (eq / ne) with "alarm" is heard: the robot hears an alarm at that entity (ALARM_DB along
    the quietest path, any hearing but "none": alarms stay in privacy mode)."""
    if rule.get("op") not in ROOM_COMPARE_OPS or rule.get("value") != "alarm":
        return False
    state = w.s
    if body.can_hear(state) == "none":
        return False
    level = sound_level(state, root_room(state, eid), state["robot"].get("room"), ALARM_DB)
    return level is not None and level >= HEARD_DB - 1e-9


def _weather_observable(w, rule, realism):
    """weather.<field>: never the weather process's bookkeeping (perception.WEATHER_HIDDEN, "_" keys). With the
    robot's network (a weather app) every perceived field; else from an outdoor room or through a window of the
    robot's room with the cameras on -- realistic perception there only the condition word, without numbers."""
    from .perception import WEATHER_HIDDEN
    parts = rule["path"].split(".")
    if len(parts) < 2 or parts[1].startswith("_") or parts[1] in WEATHER_HIDDEN:
        return False
    state = w.s
    robot = state["robot"]
    if robot.get("network", True):
        return True
    rid = robot.get("room")
    room = state["rooms"].get(rid) or {}
    if not (room.get("outdoor") or (body.imaging(state) and _has_window(state, rid))):
        return False
    return realism is None or (len(parts) == 2 and parts[1] in WINDOW_WEATHER and rule.get("op") not in NUMERIC_OPS)


def _entity_observable(w, rule, parts, realism, env):
    """How the robot observes an entity atom now: "heard" (an alarm by its sound), "remote" (a status app over the
    network), "audible" (a device's audible status), the realistic geometric camera's ViewInfo or True when it sees the
    thing (_seen_view), or None when it cannot observe it."""
    state = w.s
    eid = parts[1]
    entity = state["entities"][eid]
    field = ".".join(parts[2:])
    if field not in OBSERVABLE_FIELDS and field not in ("appliance.state", "appliance.remaining_s"):
        return None
    if field in ALARM_FIELDS and _alarm_heard(w, eid, rule):
        return "heard"
    if realism is not None and (realism.tier(entity, parts[2]) != "glance" or rule.get("op") in NUMERIC_OPS):
        return None
    if entity.get("remote_status") and state["robot"].get("network", True):
        return "remote"
    if entity.get("audible_status") and body.can_hear(state) != "none":
        return "audible"
    if not state["robot"].get("room"):
        return None
    return _seen_view(w, eid, realism, env, parts[2])


def _shown_atom(w, rule, parts, realism, how):
    """Realistic perception (review L1): an entity atom judged on the value the robot perceives -- the field as the
    observation renders it (buckets, words, look-alike group values; realism.shown_entity) -- never on the true value,
    so an accepted condition is no exact-value oracle. None when that rendering shows no such field (the robot cannot
    tell). An alarm known only by its sound is "alarm" while it sounds (a test sounds the same) and "quiet" otherwise;
    a status app or an audible status reports the glance rendering."""
    state = w.s
    eid, top = parts[1], parts[2]
    if how == "heard":
        sounding = state["entities"][eid].get(top) in ALARM_STATES
        shown = {top: "alarm" if sounding else "quiet"}
    else:
        shown = realism.shown_entity(state, eid, info=how if isinstance(how, dict) else None,
                                     remote=how in ("remote", "audible"))
    from .predicates import MISSING
    if not isinstance(shown, dict) or shown.get(top, MISSING) is MISSING:
        return None
    return bool(check({"entities": {eid: {top: shown[top]}}}, rule))


def _observe_atom(w, rule, realism, env):
    """What the robot observes of one atomic condition now: True / False, or None when it cannot tell."""
    if not isinstance(rule, dict) or not isinstance(rule.get("path"), str):
        return None             # quantifiers, aggregates, reports, milestone counts: not observable as a condition
    state = w.s
    parts = rule["path"].split(".")
    head = parts[0]
    if head == "notices" or (realism is not None and "ref" in rule):
        return None             # notices: use interrupt_on; realistic: no ref operands into hidden values
    if head == "weather":
        return bool(check(state, rule)) if _weather_observable(w, rule, realism) else None
    if head in ("time_s", "clock"):
        return bool(check(state, rule))
    if head == "entities":
        if len(parts) < 3 or parts[1] not in state["entities"]:
            return None
        how = _entity_observable(w, rule, parts, realism, env)
        if how is None:
            return None
        if realism is None:
            return bool(check(state, rule))
        return _shown_atom(w, rule, parts, realism, how)
    if head in ("people", "pets") and len(parts) == 3:
        aid, field = parts[1], parts[2]
        fields = REALISTIC_WATCH_AGENT_FIELDS if realism is not None else AGENT_OBSERVABLE_FIELDS
        if aid not in (state.get(head) or {}) or field not in fields:
            return None
        if agent_seen(w, aid, realism, env):
            if realism is None:
                return bool(check(state, rule))
            # what the observation shows (review L1): asleep and lying awake are both "lying", a pet asleep "resting"
            shown = realism.agent_shown(head, field, state[head][aid].get(field))
            return bool(check({head: {aid: {field: shown}}}, rule))
        if _room_atom(rule, state["robot"].get("room")) and _absence_seen(w, aid, realism):
            return rule.get("op") == "ne"       # not in the room: "== my room" is false, "!= my room" true
    return None


def _observed(w, rule, realism, env):
    if not isinstance(rule, dict):
        return None
    if "all" in rule or "any" in rule:
        is_all = "all" in rule
        out = True if is_all else False
        for child in rule.get("all", rule.get("any", [])) or []:
            value = _observed(w, child, realism, env)
            if value is None:
                out = None
            elif value is not is_all:
                return value            # a false branch decides "all", a true one "any"
        return out
    if "not" in rule:
        value = _observed(w, rule["not"], realism, env)
        return None if value is None else not value
    return _observe_atom(w, rule, realism, env)


def observed(w, rule, realism=None, env=None):
    """What the robot observes of a condition now (TIER1_SWEEP 6.5, TIER1_INTEGRATION 1.3): True / False, or None when
    it cannot tell. Atoms are judged per referenced id: an entity field when the robot sees that thing well enough
    (entity_seen; remote_status devices over the network, audible_status ones by ear, an alarm_state compared with
    "alarm" by its sound), a person's or pet's location / posture when it perceives them (agent_seen), "X comes in" /
    "X leaves" (a location compared with the robot's room) also when it can tell X is not there, the weather by the
    weather rule, time_s / clock always. all / any / not combine three-valued: an "any" with one observed true branch
    holds, a "not" of what the robot cannot tell stays unknown. Nothing is observed while the robot is powered down."""
    if not body.is_on(w.s):
        return None
    env = _env_of(w, env)
    return _observed(w, rule, realism if realism is not None else _realism_of(env), env)


def holds_perceived(w, rule, realism=None, env=None):
    """True when the robot observes `rule` holding now (an until ends a wait, a standing order fires, a guard passes).
    Oracle perception: check(state, rule) and observed(...) is True. Realistic perception: observed(...) is True alone
    -- the atoms are judged on what the robot perceives (rendered values), and the true state must not gate the
    answer either, or every wait would still be a yes/no question about it (review L1)."""
    realism = realism if realism is not None else _realism_of(_env_of(w, env))
    if realism is not None:
        return observed(w, rule, realism, env) is True
    return bool(check(w.s, rule)) and observed(w, rule, realism, env) is True


def perceivable(w, rule, realism=None, env=None):
    """Whether the robot can watch `rule` now (wait until / watch / guard acceptance; TIER1_SWEEP 6.5): every atomic
    condition in it is one the robot observes now (observed() is not None for it) or a location comparison with the
    robot's own room while it watches that room ("X comes in" is accepted while X is elsewhere). The three fixes of
    6.5 hold: realism applies at fire time too (holds_perceived), weather.* follows the weather rule (never its hidden
    bookkeeping), notices.* paths are refused (use interrupt_on). Nothing is perceivable while the robot is powered
    down. realism: pass the env's (None: oracle perception); env: the env (default: the World's own)."""
    if not body.is_on(w.s):
        return False
    return _watchable(w, rule, realism, _env_of(w, env))


def _watchable(w, rule, realism, env):
    if not isinstance(rule, dict):
        return False
    if "all" in rule or "any" in rule:
        return all(_watchable(w, r, realism, env) for r in rule.get("all", rule.get("any", [])))
    if "not" in rule:
        return _watchable(w, rule["not"], realism, env)
    if _observe_atom(w, rule, realism, env) is not None:
        return True
    if not isinstance(rule.get("path"), str):
        return False
    parts = rule["path"].split(".")
    rid = w.s["robot"].get("room")
    return _room_atom(rule, rid) and parts[1] in (w.s.get(parts[0]) or {}) and _room_watchable(w, realism)


# ----------------------------------------------------------------------
# notice kinds (6.3)

NOTICE_KINDS = {}       # kind -> {"doc", "implies", "db", "visual"}; register_notice_kind adds domain kinds
GROUPS = {"urgent": ("alarm", "call_for_help", "addressed", "person_down", "smoke")}
DEFAULT_DB = 60.0       # a sound notice without a level and without a kind default
HEARD_DB = 40.0         # heard at all (6.2)
WORDS_DB = 55.0         # speech words carry to the robot (addressed speech) from another room
OPEN_HOP_DB = 6.0       # one open connection (a doorway, an open door, stairs) between rooms
CLOSED_DOOR_DB = 20.0   # one closed door
SPEECH_DB, LOUD_SPEECH_DB = 60.0, 75.0      # conversation and a raised voice at 1 m
NOTE_LIMIT = 10         # notices listed in a wait's message


def register_notice_kind(kind, doc, implies=(), db=None, visual=False):
    """Declare a notice kind that w.notice posts and wait(interrupt_on=...) accepts (sweep 6.3). implies: broader
    kinds a listener for them also hears (a doorbell implies "any_sound"); db: the default sound level at 1 m; visual:
    seen rather than heard by default. Registering the same kind again with the same values is a no-op."""
    if not isinstance(kind, str) or not kind or kind != kind.strip().lower() or " " in kind:
        raise ValueError("Notice kinds are lower-case words: {!r}".format(kind))
    if kind in GROUPS:
        raise ValueError("{!r} is a notice group, not a kind".format(kind))
    record = {"doc": str(doc), "implies": tuple(implies), "db": None if db is None else float(db),
              "visual": bool(visual)}
    old = NOTICE_KINDS.get(kind)
    if old is not None and old != record:
        raise ValueError("Notice kind {!r} registered twice with different definitions".format(kind))
    NOTICE_KINDS[kind] = record
    return kind


_SOUND = ("any_sound",)
for _kind, _doc, _implies, _db, _visual in (
        ("any_sound", "any sound the robot hears", (), None, False),
        ("speech", "someone speaks within earshot (text when the words carry)", _SOUND, SPEECH_DB, False),
        ("addressed", "someone speaks to the robot", ("speech", "any_sound"), SPEECH_DB, False),
        ("doorbell", "the doorbell rings", _SOUND, 75.0, False),
        ("knock", "someone knocks", _SOUND, 60.0, False),
        ("alarm", "a smoke, CO, security or appliance alarm sounds", _SOUND, 85.0, False),
        ("phone", "a phone rings", _SOUND, 65.0, False),
        ("crying", "a baby or child cries", _SOUND, 80.0, False),
        ("call_for_help", "someone calls for help", _SOUND, 80.0, False),
        ("person_down", "a person falls or collapses", _SOUND, 65.0, False),
        ("smoke", "smoke is seen", (), None, True),
        ("appliance_done", "an appliance chimes that its cycle is done", _SOUND, 65.0, False),
        ("device", "a connected device sends a notification", (), None, False),
        ("person_enters", "a person comes into the robot's room", (), None, False),
        ("person_leaves", "a person leaves the robot's room", (), None, False),
        ("pet_enters", "a pet comes into the robot's room", (), None, False),
        ("order", "one of the robot's standing orders fired", (), None, False),
        ("battery", "the robot's battery reaches its low or reserve level", (), None, False),
        ("fault", "the robot detects a fault of its own", (), None, False),
        ("visual", "the robot's camera sees something happen", (), None, True),
        # physics consequences (kernel/phys/observe.py: the sounds of falls, breaks and spills)
        ("crash", "something crashes", _SOUND, 60.0, False),
        ("glass_breaking", "glass or ceramic breaks", _SOUND, 60.0, False),
        ("splash", "liquid splashes or spills", _SOUND, 45.0, False),
        ("thud", "something heavy falls", _SOUND, 60.0, False),
        ("clatter", "something small falls or rattles", _SOUND, 45.0, False),
        ("scrape", "something scrapes or slides", _SOUND, 45.0, False),
        ("creak", "something creaks or gives", _SOUND, 45.0, False)):
    register_notice_kind(_kind, _doc, _implies, _db, _visual)


def expand_kinds(kinds):
    """The notice kinds a wait listens for: each kind as given, groups expanded. Raises ValueError for an unknown
    kind (the wait refuses it)."""
    out = set()
    for kind in kinds:
        if not isinstance(kind, str):
            raise ValueError("interrupt_on lists notice kinds (words)")
        word = kind.strip().lower()
        if word in GROUPS:
            out.update(GROUPS[word])
        elif word in NOTICE_KINDS:
            out.add(word)
        else:
            raise ValueError("Unknown notice kind: {} (known: {})".format(
                word, ", ".join(sorted(set(NOTICE_KINDS) | set(GROUPS)))))
    return out


def kinds_of(kind):
    """The kind and every kind it implies (transitively)."""
    seen, stack = set(), [kind]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend((NOTICE_KINDS.get(current) or {}).get("implies", ()))
    return seen


def matches(notice, wanted):
    """True when a stored notice is one of the kinds a wait listens for (its own kind or one it implies)."""
    return bool(kinds_of(notice.get("kind")) & set(wanted))


# ----------------------------------------------------------------------
# perceivability of a notice (6.2)

def _room_of(state, source):
    if not isinstance(source, str):
        return None
    if source in state.get("rooms", {}):
        return source
    return root_room(state, source)


def _door_closed(state, conn):
    door = conn.get("door")
    if not door:
        return False
    rec = state["entities"].get(door)
    return rec is not None and not rec.get("open", False)


def sound_level(state, src_room, dst_room, db):
    """The level (dB) of a sound of `db` at 1 m in src_room as heard in dst_room: db minus 6 dB per open hop and 20 dB
    per closed door along the connection path that loses least (6.2). None when no path connects the rooms."""
    if src_room == dst_room:
        return float(db)
    rooms = state.get("rooms", {})
    if src_room not in rooms or dst_room not in rooms:
        return None
    graph = {}
    for cid in sorted(state.get("connections", {})):
        conn = state["connections"][cid]
        pair = conn.get("rooms") or ()
        if len(pair) != 2:
            continue
        loss = CLOSED_DOOR_DB if _door_closed(state, conn) else OPEN_HOP_DB
        a, b = pair
        graph.setdefault(a, []).append((loss, b))
        graph.setdefault(b, []).append((loss, a))
    best = {src_room: 0.0}
    heap = [(0.0, src_room)]
    while heap:
        loss, rid = heapq.heappop(heap)
        if rid == dst_room:
            return float(db) - loss
        if loss > best.get(rid, 1e18):
            continue
        for step, other in graph.get(rid, ()):
            total = loss + step
            if total < best.get(other, 1e18) - 1e-9:
                best[other] = total
                heapq.heappush(heap, (total, other))
    return None


def _adjacent(state, a, b):
    return any(set(conn.get("rooms") or ()) == {a, b} for conn in state.get("connections", {}).values())


def source_perceived(w, source, env=None):
    """A notice names its source only when the robot perceives the source now (TIER1_INTEGRATION 1.3, per id): a thing
    it sees (entity_seen: not a dark shape; geometric: in the head camera's view), a person or pet it can tell who it
    is (agent_seen with a lit room in room mode; geometric: identified in the view in realistic perception)."""
    if not isinstance(source, str):
        return False
    state = w.s
    env = _env_of(w, env)
    realism = _realism_of(env)
    if source in state["entities"]:
        return entity_seen(w, source, realism, env)
    if source in (state.get("people") or {}) or source in (state.get("pets") or {}):
        return agent_seen(w, source, realism, env, lit=True)
    return False


def robot_perceives(w, notice):
    """The notice as the robot perceives it, or None (TIER1_SWEEP 6.2, TIER1_INTEGRATION 1.3). notice: the dict
    w.notice posts ({"kind", "room", "db", "source", "to_robot", "remote", "visual", "text", "data"}). The record is
    {"kind", "t", "step", "room" | "near" | "far" | "remote", "source" (only when perceived now), "text" (heard words),
    "data"}. Powered down: nothing. remote: needs the robot's network. visual: in the robot's room, lit, imaging on
    (room camera), or the geometric camera's event rule. Sound: body.can_hear ("none" hears nothing; "addressed",
    privacy mode or a microphone policy, drops speech that is not addressed to the robot and keeps every non-speech
    sound and alarm, TIER1_INTEGRATION 1.3), then the propagation (or a robot_hears hook's verdict)."""
    state = w.s
    if not body.is_on(state):
        return None
    kind = notice.get("kind")
    spec = NOTICE_KINDS.get(kind) or {}
    robot = state["robot"]
    rid = robot.get("room")
    source = notice.get("source")
    src_room = notice.get("room") if notice.get("room") in state.get("rooms", {}) else _room_of(state, source)
    out = {"t": state.get("time_s", 0.0), "step": state.get("steps", 0), "kind": kind}
    text = notice.get("text")
    if notice.get("remote"):
        if not body.get(state, "network"):
            return None
        out["remote"] = True
        if text is not None:
            out["text"] = str(text)
    elif notice.get("visual") or (spec.get("visual") and notice.get("db") is None):
        if "view" in robot:         # geometric camera: the camera's own event rule
            from . import camera
            seen = camera.sees_event(w, source=source, room=src_room)
            if not seen:
                return None
        else:
            from .perception import Perception
            if (src_room or rid) != rid or not body.imaging(state) or not Perception(None).lit(state, rid):
                return None
        out["room"] = rid
    else:
        hearing = body.can_hear(state)
        if hearing == "none":
            return None
        to_robot = bool(notice.get("to_robot"))
        if hearing == "addressed" and "speech" in kinds_of(kind) and not to_robot:
            return None             # privacy mode: speech to others is not listened to; other sounds stay
        db = notice.get("db")
        db = float(db) if isinstance(db, (int, float)) and not isinstance(db, bool) else \
            (spec.get("db") if spec.get("db") is not None else DEFAULT_DB)
        where = src_room or rid
        level = sound_level(state, where, rid, db)
        heard = notice.get("_heard")
        if heard is None:
            for _, _, fn in HOOKS.get("robot_hears", []):
                verdict = fn(state, dict(notice))
                if verdict is not None:
                    heard = bool(verdict)
                    break
        if heard is None:
            heard = level is not None and level >= HEARD_DB - 1e-9
        if not heard:
            return None
        if where == rid:
            out["room"] = rid
        elif _adjacent(state, where, rid):
            out["near"] = where
        else:
            out["far"] = True
        if text is not None and (where == rid or notice.get("_remote_words") or
                                 (to_robot and level is not None and level >= WORDS_DB - 1e-9)):
            out["text"] = str(text)
    if source_perceived(w, source):
        out["source"] = source
    data = notice.get("data")
    if data is not None:
        out["data"] = copy.deepcopy(data)
    return out


# ----------------------------------------------------------------------
# posting and storing notices (6.1)

def bus_on(state):
    """The notice bus stores notices in this episode: its state has the "notices" section (schema-3 tasks; a config
    that carries the section)."""
    return isinstance(state.get("notices"), list)


def active(w):
    """Someone listens: the bus stores notices or a wait listens for them."""
    return w._listening or bus_on(w.s)


def post(w, notice, perceived=None):
    """World.notice's implementation: check the kind, decide perceivability (robot_perceives) unless `perceived` is a
    record already decided (the geometric camera's seen events), then store it."""
    kind = notice.get("kind")
    if kind not in NOTICE_KINDS:
        raise ValueError("Unknown notice kind {!r}: register it with timeline.register_notice_kind".format(kind))
    if not active(w):
        return None
    record = perceived if perceived is not None else robot_perceives(w, notice)
    if record is None:
        return None
    return store(w, record)


def store(w, record):
    """Keep a perceived notice: in the "notices" section (cap 200) when the bus is on, and for a listening wait."""
    if bus_on(w.s):
        notices = w.sec("notices")
        notices.append(record)
        if len(notices) > NOTICES_CAP:
            del notices[:len(notices) - NOTICES_CAP]
    if w._listening:
        w._heard.append(record)
    return record


def _from_emit(kind, warning=None):
    def derive(w, entry):
        if not active(w):
            return
        data = {"warning": warning} if warning else {"fault": entry.get("fault")}
        if body.is_on(w.s):
            store(w, {"t": w.s.get("time_s", 0.0), "step": w.s.get("steps", 0), "kind": kind,
                      "room": w.s["robot"].get("room"), "data": data})
    return derive


# the body's own warnings (the BMS and the self-check report to the robot itself: no propagation, needs power)
EMIT_NOTICES["battery_low"] = _from_emit("battery", "low")
EMIT_NOTICES["battery_reserve"] = _from_emit("battery", "reserve")
EMIT_NOTICES["fault_detected"] = _from_emit("fault")


def start(env):
    """The engine's transient notice bookkeeping from the current state (reset, load, replay): the message cursor,
    the snapshot of who is where, the alarms sounding now, and the World's weak reference to its env (the per-id
    perceivability rules read the env's perception, camera and realism). Never saved: it is a function of the
    state."""
    w = env.world
    state = w.s
    w._env_ref = weakref.ref(env)
    key, seen = _msg_cursor(state)
    env._timeline = {"msg_key": key, "msg_seen": seen, "agents": _agents(state),
                     "robot_room": state["robot"].get("room"), "alarms": _sounding(w)}
    return env._timeline


def _msg_key(message):
    return (message.get("step", -1), message.get("time_s", -1.0))


def _msg_cursor(state):
    """The message cursor now: the boundary key (steps, time_s) and how many messages already carry exactly that key
    (they are not fresh at the next derivation, while messages posted later at the same boundary -- a phase's
    request posted after the boundary's first derivation -- are)."""
    key = (state.get("steps", 0), state.get("time_s", 0.0))
    seen = 0
    for message in reversed(state.get("messages") or []):
        if not isinstance(message, dict):
            continue
        mkey = _msg_key(message)
        if mkey == key:
            seen += 1
        elif mkey < key:
            break
    return key, seen


def _scratch(env):
    data = getattr(env, "_timeline", None)
    return data if data is not None else start(env)


def _agents(state):
    return {(group, aid): agent.get("location") for group in ("people", "pets")
            for aid, agent in sorted((state.get(group) or {}).items())}


def _alarm_candidate(record):
    return any(k in record for k in ("alarm_state", "alarm_pattern", "detector_kind", "siren_db")) or \
        record.get("category") in ALARM_CATEGORIES


def _alarm_sounding(record):
    state = record.get("alarm_state")
    return (isinstance(state, str) and state in ALARM_STATES) or record.get("alarm_pattern") in ALARM_PATTERNS or \
        record.get("beeping") is True


def _sounding(w):
    """{entity id: its level at 1 m} of the alarms sounding now: smoke / CO detectors and sirens (an alarm_state of
    alarm or a test, the temporal-3 / temporal-4 pattern, beeping), over a static index of the entities that can sound
    one (O(detectors) per substep)."""
    if not hasattr(w, "static_index"):
        return {}
    entities = w.s["entities"]
    out = {}
    for eid in w.static_index("timeline:alarms", _alarm_candidate):
        record = entities.get(eid)
        if record is not None and _alarm_sounding(record):
            db = record.get("alarm_db")
            out[eid] = float(db) if isinstance(db, (int, float)) and not isinstance(db, bool) else ALARM_DB
    return out


SIGHTING_S = 30.0          # a substep: a person the head camera saw at the last boundary was seen going


def _seen_leaving(w, aid, realism, rid):
    """Whether the robot saw who left its room (from the state alone, so a saved episode goes on exactly as a continuous
    one): room camera -- its room is lit with imaging on; geometric camera -- the camera's watch had them in view at the
    previous boundary (camera_log.sightings last_s; realistic perception: at the level that identifies them)."""
    state = w.s
    if not isinstance(state["robot"].get("view"), dict):
        return not _room_dark(w, rid)
    sighting = ((state.get("camera_log") or {}).get("sightings") or {}).get(aid) or {}
    last = sighting.get("last_s")
    if not isinstance(last, (int, float)) or float(last) < float(state.get("time_s", 0.0)) - SIGHTING_S - 1e-6:
        return False
    return realism is None or sighting.get("best") == "identify"


def derive_notices(env):
    """The kernel's own notices of one substep boundary (6.1): speech / addressed from messages heard since the last
    boundary, person_enters / person_leaves / pet_enters from location changes into or out of the robot's room, and
    alarm from an alarm that started sounding (a detector's alarm_state, its temporal pattern or beeping: the
    kernel's bridge until the domains post their own; the robot hears it by the 6.2 propagation, also in privacy
    mode). The cursors advance even when nobody listens, so a wait that starts listening hears only what happens
    after it."""
    w = env.world
    state = w.s
    data = _scratch(env)
    key, seen_now = _msg_cursor(state)
    last, seen = data["msg_key"], data.get("msg_seen", 0)
    agents = _agents(state)
    rid = state["robot"].get("room")
    before, room_before = data["agents"], data["robot_room"]
    alarms_before = data.get("alarms") or {}
    alarms = _sounding(w)
    data["msg_key"], data["msg_seen"], data["agents"], data["robot_room"] = key, seen_now, agents, rid
    data["alarms"] = alarms
    if last is None or not active(w):
        return
    for eid in sorted(set(alarms) - set(alarms_before)):
        record = robot_perceives(w, {"kind": "alarm", "room": root_room(state, eid), "db": alarms[eid],
                                     "source": eid, "to_robot": False, "remote": False, "visual": False,
                                     "text": None, "data": None})
        if record is not None:
            store(w, record)
    messages = state.get("messages") or []
    tail = []
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        if _msg_key(message) < last:
            break
        tail.append(message)
    tail.reverse()
    # messages at the cursor's own key beyond the ones counted then (posted later at that boundary), then newer ones
    fresh = [m for m in tail if _msg_key(m) == last][seen:] + [m for m in tail if _msg_key(m) > last]
    for message in fresh:
        if not message.get("heard_by_robot") or message.get("speaker") in ("robot", None):
            continue
        to_robot = message.get("to") == "robot" or bool(message.get("addressed_robot"))
        remote = message.get("channel") in ("phone", "intercom", "app", "text")
        notice = {"kind": "addressed" if to_robot else "speech", "room": message.get("room"),
                  "db": LOUD_SPEECH_DB if message.get("loud") else SPEECH_DB, "source": message.get("speaker"),
                  "to_robot": to_robot, "remote": False, "visual": False, "text": message.get("text"),
                  "data": None, "_heard": True, "_remote_words": remote}    # the speaker: "source", when perceived
        record = robot_perceives(w, notice)
        if record is not None:
            store(w, record)
    if before is None or room_before != rid:
        return                  # the robot itself changed rooms: nobody "entered"
    realism = _realism_of(env)
    for (group, aid), location in agents.items():
        was = before.get((group, aid))
        if was == location:
            continue
        if location == rid:
            kind = "person_enters" if group == "people" else "pet_enters"
            seen = agent_seen(w, aid, realism, env, lit=True)
        elif was == rid and group == "people":
            kind = "person_leaves"
            seen = _seen_leaving(w, aid, realism, rid)
        else:
            continue
        record = _agent_notice(w, kind, aid, seen)
        if record is not None:
            store(w, record)


def _agent_notice(w, kind, aid, seen):
    """Someone came in or left: seen (the robot perceives who it is: agent_seen per id, so a person behind the
    geometric camera or a shape in the dark is no one) or heard (footsteps, a door: full hearing, no source)."""
    state = w.s
    if not body.is_on(state):
        return None
    if not seen and body.can_hear(state) != "full":
        return None
    record = {"t": state.get("time_s", 0.0), "step": state.get("steps", 0), "kind": kind,
              "room": state["robot"].get("room")}
    if seen:
        record["source"] = aid
    return record


def note_order_fired(w, index, mode):
    """The robot's own software saw a standing order fire (an "order" notice; needs power)."""
    if active(w) and body.is_on(w.s):
        store(w, {"t": w.s.get("time_s", 0.0), "step": w.s.get("steps", 0), "kind": "order",
                  "room": w.s["robot"].get("room"), "data": {"order": index, "mode": mode}})


def describe_notice(record):
    """Words for a perceived notice in a wait's message: kind, where, and the words when heard."""
    kind = record.get("kind", "?")
    if record.get("remote"):
        where = "(remote)"
    elif "room" in record:
        where = "in " + str(record["room"])
    elif "near" in record:
        where = "near " + str(record["near"])
    else:
        where = "far away"
    out = "{} {}".format(kind, where)
    if record.get("source"):
        out += " from " + str(record["source"])
    if record.get("text"):
        out += ': "{}"'.format(str(record["text"])[:80])
    return out


# ----------------------------------------------------------------------
# time verbs and standing orders

# idle / power_off_ok (BODY-ENGINE, TIER1_EMBODIMENT 5.5): the body system integrates a wait's idle drain (awake for
# the profile's auto_standby_s, then parked standby), and wait is the one action a powered-down robot can take
@action("wait", params={"seconds": {"type": "int", "default": 1, "min": 1, "max": 604800},
                        "until": "?dict", "interrupt_on": "?list"},
        energy=0.0, duration=lambda ctx: 0.0 if (ctx.data.get("wait") or {}).get("met") else float(ctx.args["seconds"]),
        category="control", stopped_ok=True, idle=True, power_off_ok=True,
        doc="Let time pass; until=observable condition, interrupt_on=[urgent, doorbell, addressed, ...] ends it early. "
            "Appliances, people, pets and weather keep running; pass interrupt_on=[\"urgent\"] for unattended waits.")
def wait(ctx):
    seconds = ctx.args["seconds"]
    until, kinds = ctx.args.get("until"), ctx.args.get("interrupt_on")
    if until is None and kinds is None:
        return "Waited {} s".format(seconds)
    plan = {"seconds": seconds}
    if until is not None:
        try:
            validate_predicate(until)
        except ValueError as exc:
            raise ActionError(str(exc), "argument")
        require(perceivable(ctx.w, until, getattr(ctx.env, "realism", None), ctx.env),
                "Wait only on what the robot can observe")
        plan["until"] = copy.deepcopy(until)
    if kinds is not None:
        require(0 < len(kinds) <= 20, "interrupt_on lists 1..20 notice kinds", "argument")
        try:
            plan["kinds"] = sorted(expand_kinds(kinds))
        except ValueError as exc:
            raise ActionError(str(exc), "argument")
    if until is not None and holds_perceived(ctx.w, until, getattr(ctx.env, "realism", None), ctx.env):
        plan["met"] = True          # it already holds, and the robot sees it hold: the wait ends at once
    ctx.data["wait"] = plan
    return "Waited {} s".format(seconds)


AUTO_FORBIDDEN = ("wait", "watch", "cancel_watch", "run_pending", "undo")
MAX_COMMANDS = 20
AUTO_BUDGET = 500           # auto commands per step
ORDER_LOG_CAP = 300
DEFAULT_MAX_RUNS, DEFAULT_PERSISTENT_RUNS = 1, 50
DEFAULT_COOLDOWN_S = 60.0


@action("watch", params={"condition": "dict", "commands": "list", "persistent": {"type": "bool", "default": False},
                         "mode": {"type": "str", "default": "queue", "choices": ["queue", "auto"]},
                         "guard": "?dict", "max_runs": {"type": "int", "optional": True, "min": 1, "max": 1000},
                         "cooldown_s": {"type": "number", "optional": True, "min": 0, "max": 604800},
                         "expires_s": {"type": "number", "optional": True, "min": 1, "max": 604800}},
        duration=1.0, energy=0.0, category="control",
        doc="Standing order on an observable condition; mode auto runs the commands itself while idle, queue needs "
            "run_pending. persistent=true re-arms after the condition clears; guard, max_runs, cooldown_s, expires_s.")
def watch(ctx):
    condition, commands = ctx.args["condition"], ctx.args["commands"]
    mode = ctx.args.get("mode", "queue")
    try:
        validate_predicate(condition)
    except ValueError as exc:
        raise ActionError(str(exc), "argument")
    require(0 < len(commands) <= MAX_COMMANDS, "Supply 1..20 commands", "argument")
    realism = getattr(ctx.env, "realism", None)
    require(perceivable(ctx.w, condition, realism, ctx.env),
            "Watch only conditions the robot can observe (visible, audible or reported by a connected device)")
    for command in commands:
        require(isinstance(command, dict) and command.get("action") in ACTIONS and command["action"] not in ("watch", "run_pending"),
                "Unsupported scheduled command", "argument")
        if mode == "auto":
            require(command["action"] not in AUTO_FORBIDDEN, "An auto standing order cannot {} ({} are not "
                    "allowed)".format(command["action"], ", ".join(AUTO_FORBIDDEN)), "argument")
    guard = ctx.args.get("guard")
    if guard is not None:
        try:
            validate_predicate(guard)
        except ValueError as exc:
            raise ActionError(str(exc), "argument")
        require(perceivable(ctx.w, guard, realism, ctx.env), "Guard only on what the robot can observe")
    orders = ctx.w.sec("orders")
    order = {"condition": copy.deepcopy(condition), "commands": copy.deepcopy(commands), "fired": False,
             "persistent": ctx.args["persistent"], "armed": True, "created_s": ctx.w.s["time_s"]}
    # the new keys only when used, so a queue-mode order keeps today's record (and state) exactly
    if mode == "auto":
        order["mode"] = "auto"
        default_runs = DEFAULT_PERSISTENT_RUNS if order["persistent"] else DEFAULT_MAX_RUNS
        order["max_runs"] = ctx.args.get("max_runs", default_runs)
        if order["persistent"]:
            order["cooldown_s"] = float(ctx.args.get("cooldown_s", DEFAULT_COOLDOWN_S))
        elif "cooldown_s" in ctx.args:
            order["cooldown_s"] = float(ctx.args["cooldown_s"])
    else:
        for key in ("max_runs", "cooldown_s"):
            if key in ctx.args:
                order[key] = ctx.args[key] if key == "max_runs" else float(ctx.args[key])
    if "expires_s" in ctx.args:
        order["expires_s"] = float(ctx.args["expires_s"])
    if guard is not None:
        order["guard"] = copy.deepcopy(guard)
    if any(k in order for k in ("mode", "max_runs", "cooldown_s", "expires_s", "guard")):
        order["runs"] = 0
    orders.append(order)
    return "Watching (order {}{})".format(len(orders) - 1, ", auto" if mode == "auto" else "")


@action("cancel_watch", params={"target": "str"}, duration=0.5, energy=0.0, category="control", stopped_ok=True,
        doc="Cancel a standing order by its index.")
def cancel_watch(ctx):
    orders = ctx.w.sec("orders")
    require(ctx.args["target"].isdigit() and int(ctx.args["target"]) < len(orders), "No such order", "argument")
    orders[int(ctx.args["target"])]["fired"] = True
    orders[int(ctx.args["target"])]["persistent"] = False
    if orders[int(ctx.args["target"])].get("due"):
        orders[int(ctx.args["target"])]["due"] = False
    return "Cancelled order " + ctx.args["target"]


@action("run_pending", duration=0.5, energy=0.0, category="control",
        doc="Execute the next queued standing-order command (it takes its own time and guards).")
def run_pending(ctx):
    raise ActionError("No pending command")


# ----------------------------------------------------------------------
# firing standing orders (engine, every substep) and running auto orders (6.4)

def fire_orders(env):
    """Fire the standing orders whose condition holds and the robot observes holding now (holds_perceived: realism
    applied at fire time, per referenced id, 6.5): queue orders put their commands into `pending`; auto orders become
    due (run by run_auto_orders at the next idle boundary). persistent orders re-arm once their condition clears;
    max_runs, cooldown_s and expires_s apply to the orders that declare them."""
    w = env.world
    state = w.s
    realism = getattr(env, "realism", None)
    now = state["time_s"]
    for i, order in enumerate(state.get("orders", [])):
        if order.get("fired") and not order.get("persistent"):
            continue
        expires = order.get("expires_s")
        if expires is not None and now >= float(order.get("created_s", 0.0)) + float(expires) - 1e-9:
            o = w.sec("orders")[i]
            o["fired"], o["persistent"], o["expired"] = True, False, True
            o.pop("due", None)
            continue
        if not holds_perceived(w, order["condition"], realism, env):
            if order.get("persistent") and order.get("armed") is False:
                w.sec("orders")[i]["armed"] = True
            continue
        if order.get("persistent") and order.get("armed") is False:
            continue
        if order.get("due"):
            continue            # fired, waiting for an idle moment
        last, cooldown = order.get("last_s"), order.get("cooldown_s")
        if last is not None and cooldown is not None and now - float(last) < float(cooldown) - 1e-9:
            continue            # stays armed: it fires once the cooldown is over
        orders = w.sec("orders")
        o = orders[i]
        o["fired"] = True
        o["armed"] = False
        auto = order.get("mode") == "auto"
        if "runs" in o:
            o["runs"] = int(o.get("runs", 0)) + 1
            o["last_s"] = now
            if o.get("max_runs") is not None and o["runs"] >= int(o["max_runs"]):
                o["persistent"] = False
        if auto:
            o["due"] = True
            o["due_s"] = now
        else:
            pending = w.sec("pending")
            pending.extend(copy.deepcopy(order["commands"]))
        facts = w.sec("facts")
        facts["orders_fired"] = facts.get("orders_fired", 0) + 1
        w.emit("standing_order_fired", order=i)
        note_order_fired(w, i, "auto" if auto else "queue")


def has_due(state):
    return any(o.get("due") for o in state.get("orders", ()) if isinstance(o, dict))


def has_auto(state):
    """True while an auto standing order can still fire or is due (the engine then runs idle boundaries for them)."""
    return any(isinstance(o, dict) and o.get("mode") == "auto" and (o.get("due") or not o.get("fired") or
                                                                    o.get("persistent"))
               for o in state.get("orders", ()))


class IdleRun(object):
    """What run_auto_orders needs from the idle advance it runs in: the env, and what it ran (for the wait's notes)."""

    def __init__(self, env, label="wait"):
        self.env, self.label = env, label
        self.ran = []           # (order index, action, ok)
        self.stopped = None     # "power_off" once auto execution stops for this advance
        self.paused = False     # the command budget paused auto execution for a while (an hour of this step)


def _log(w, entry):
    log = w.sec("order_log")
    log.append(entry)
    if len(log) > ORDER_LOG_CAP:
        del log[:len(log) - ORDER_LOG_CAP]


def _budget(env):
    """[key, commands run, budget logged] of the current window: one step, and within an idle advance one simulated
    hour of it (a multi-day wait runs at most 500 auto commands an hour, not 500 in all)."""
    state = env.world.s
    key = (state.get("steps", 0), int(float(state.get("time_s", 0.0)) // 3600.0))
    used = getattr(env, "_auto_budget", None)
    if not used or used[0] != key:
        used = env._auto_budget = [key, 0, False]
    return used


def run_auto_orders(env, idle_ctx):
    """Run the commands of every due auto order, in order index order (6.4). Each command is a nested leaf under a
    savepoint (engine._auto_command): refused -> rolled back alone, logged, the rest of that firing skipped; done ->
    settled as an action, its time spent (the world keeps running, counted against the wait) and sampled by the
    monitor. guard false skips the firing (logged guard_false); a power-down stops auto execution; at most 500 auto
    commands per step and simulated hour (then order_budget is logged once and the orders still due wait for the next
    hour of the advance, or the next step). Returns the order-log records written."""
    w = env.world
    written = []
    if idle_ctx.stopped:
        return written
    realism = getattr(env, "realism", None)
    for i in range(len(w.s.get("orders", []))):
        order = w.s["orders"][i]
        if not order.get("due"):
            continue
        commands = order.get("commands") or []
        resume = int(order.get("resume", 0) or 0)       # a firing the budget paused goes on where it stopped
        for n, command in enumerate(commands):
            if n < resume:
                continue
            state = w.s
            now = state["time_s"]
            if not body.is_on(state):
                idle_ctx.stopped = "power_off"
                return written
            used = _budget(env)
            if used[1] >= AUTO_BUDGET:
                if not used[2]:
                    used[2] = True
                    entry = {"t": now, "order": i, "cmd": n, "action": command.get("action"), "ok": False,
                             "message": "order_budget", "category": "budget"}
                    _log(w, entry)
                    written.append(entry)
                idle_ctx.paused = True
                w.sec("orders")[i]["resume"] = n
                return written          # this order stays due: it goes on once the budget window moves on
            guard = order.get("guard")
            if guard is not None and not holds_perceived(w, guard, realism, env):
                entry = {"t": now, "order": i, "cmd": n, "action": command.get("action"), "ok": False,
                         "message": "guard_false", "category": "guard"}
                _log(w, entry)
                written.append(entry)
                break
            used[1] += 1
            ok, message, category = env._auto_command(command, idle_ctx)
            entry = {"t": now, "order": i, "cmd": n, "action": command.get("action"), "ok": ok,
                     "message": str(message)[:300], "category": category}
            _log(w, entry)
            written.append(entry)
            idle_ctx.ran.append((i, command.get("action"), ok))
            if not ok:
                w.stat("order_refusals")
                break
        o = w.sec("orders")[i]
        o["due"] = False
        o.pop("due_s", None)
        o.pop("resume", None)
    return written


# ----------------------------------------------------------------------
# a wait in progress (engine-internal)

class WaitRun(object):
    """The until / interrupt_on plan of one wait step while the engine advances it."""

    def __init__(self, env, plan):
        self.env = env
        self.until = plan.get("until")
        self.kinds = set(plan.get("kinds") or ())
        self.seconds = plan.get("seconds")
        self.reason = "until" if plan.get("met") else None
        self.cause = None
        self.heard = []

    def listening(self):
        return bool(self.kinds)

    def check(self):
        """At a substep boundary: True when the wait ends here (until holds and is perceivable, or a notice it listens
        for was perceived)."""
        w = self.env.world
        fresh, w._heard = list(w._heard), []
        self.heard.extend(fresh)
        if self.kinds:
            for record in fresh:
                if matches(record, self.kinds):
                    self.reason, self.cause = "interrupted", record
                    return True
        if self.until is not None and holds_perceived(w, self.until, getattr(self.env, "realism", None), self.env):
            self.reason = "until"
            return True
        return False


def _seconds_text(value):
    value = round(float(value), 3)
    return str(int(value)) if abs(value - round(value)) < 1e-9 else "{:g}".format(value)


def wait_message(waited, run=None, idle_ctx=None, heard=None):
    """The wait's message: "Waited N s", why it ended ("(until condition met)", "(interrupted: doorbell near
    entry_1)"), then notes: up to 10 notices perceived during the wait and the standing orders that ran."""
    text = "Waited {} s".format(_seconds_text(waited))
    if run is not None and run.reason == "until":
        text += " (until condition met)"
    elif run is not None and run.reason == "interrupted":
        text += " (interrupted: {})".format(describe_notice(run.cause))
    notes = []
    records = list(heard if heard is not None else (run.heard if run is not None else ()))
    if run is not None and run.cause is not None:
        records = [r for r in records if r is not run.cause]     # the cause is already in the message
    if records:
        shown = [describe_notice(r) for r in records[:NOTE_LIMIT]]
        more = len(records) - len(shown)
        notes.append("noticed: " + "; ".join(shown) + (" (+{} more)".format(more) if more > 0 else ""))
    if idle_ctx is not None and idle_ctx.ran:
        runs = {}
        for index, name, ok in idle_ctx.ran[:NOTE_LIMIT]:
            runs.setdefault(index, []).append("{} {}".format(name, "ok" if ok else "refused"))
        more = len(idle_ctx.ran) - NOTE_LIMIT
        notes.append("standing orders ran: " + "; ".join("order {}: {}".format(i, ", ".join(v))
                                                        for i, v in sorted(runs.items())) +
                     (" (+{} more)".format(more) if more > 0 else ""))
    if idle_ctx is not None and idle_ctx.paused:
        notes.append("auto orders paused (order budget)")
    if idle_ctx is not None and idle_ctx.stopped:
        notes.append("auto orders paused ({})".format(idle_ctx.stopped.replace("_", " ")))
    return text + "".join(" | " + note for note in notes)
