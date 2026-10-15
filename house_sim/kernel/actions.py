"""Action specifications, parameter validation and the handler context.

    from house_sim.kernel.actions import action

    @action("wipe", params={"target": "entity", "tool": "entity"}, duration=8, noise=0.1)
    def wipe(ctx):
        surface = ctx.mobj(ctx.args["target"], touch=True)
        ...
        return "Wiped"

Parameter specs are strings ("entity", "?number", "?str:left|right|both") or dicts
{"type": "number", "default": 0.25, "min": 0, "max": 20, "choices": [...]} where a
leading "?" or "default" makes the parameter optional. "target" and "destination"
receive env.step's positional arguments.

duration: seconds, or fn(ctx) -> seconds evaluated after the handler ran.
Handlers may add time with ctx.spend(seconds).
inverse: fn(ctx_before_args, before_state) -> command dict or None, for undo.

Robot body (kernel v3.0, docs/design/TIER1_EMBODIMENT.md 5.5; kernel/body.py pays for every action):
energy: None (default: the action costs its activity power over its seconds plus the walking work of its motion
record) or a number, extra mechanical work in the old % units (value x 24 Wh). arms: arms counted in the activity
power (default 1 for physical actions, 2 when the target is held in both hands). idle: the body system integrates
the time (wait only). power_off_ok: allowed while the robot is powered down (wait only). noise_db: sound pressure at
1 m, a number or fn(ctx) (quiet mode refuses actions louder than the profile's quiet limit; the robot_sound hook
reports it). timing: "fixed", "speed" (a duration derived from the walking speed) or "commanded" (a duration the
command states: hold_s, seconds, minutes of a hold, a game, a blend; careful speed slows motions, not a commanded
time); the careful-speed rule of BODY-CORE skips "speed" and "commanded". None of these is part of the action
fingerprint.
"""
import math
import random

from . import geometry
from .errors import ActionError, require
from .plugins import ACTIONS, OWNERS, _owner

TYPES = {"str", "int", "float", "number", "bool", "entity", "room", "person", "pet", "agent",
         "target", "list", "dict", "any", "cell", "text"}

# ActionError.kernel_access (kernel v2.9): True on refusals raised by the kernel's own access checks -- Ctx.obj
# visibility, robot state and reach (geometry.reach_problem), Ctx.check_permission / Ctx.forbid -- so a domain
# that makes its own refusals generic for unidentified targets can let these through unchanged. Default False.
if not hasattr(ActionError, "kernel_access"):
    ActionError.kernel_access = False


def access_error(message, category="precondition"):
    """An ActionError tagged as a kernel access refusal (exc.kernel_access is True)."""
    exc = ActionError(message, category)
    exc.kernel_access = True
    return exc


def require_access(condition, message, category="precondition"):
    if not condition:
        raise access_error(message, category)


UNIDENTIFIED_MESSAGE = "You cannot tell what it is yet: identify it first (inspect it or read its label)"
# words of a printed or spoken cue that a permission rule applies (a "PRIVATE" label, a "do not touch" sign)
PERMISSION_CUE_WORDS = ("private", "personal", "confidential", "do not touch", "don't touch", "do not open",
                        "don't open", "do not move", "don't move", "do not disturb", "keep out", "keep away",
                        "off limits", "off-limits", "hands off", "no touching", "not to be touched", "authorized only",
                        "authorised only", "staff only")
PERMISSION_CUE_FIELDS = ("label_text", "text", "sign", "sign_text", "warning", "name_label", "instructions", "note")
PERMISSION_STATS = {"private": "privacy_violations", "off_limits": "privacy_violations",
                    "requires_approval": "authority_violations", "risk": "authority_violations"}


def _has_cue(text):
    low = str(text or "").lower()
    return any(word in low for word in PERMISSION_CUE_WORDS)


def _flag_set(props, flag):
    if flag == "risk":
        try:
            return float(props.get("risk", 0) or 0) >= 3
        except (TypeError, ValueError):
            return False
    return bool(props.get(flag))


_GROUP_TYPES = {}


def group_member_types(realism, name):
    """Registry types the look-alike group `name` covers (by type, ancestor, category or looks_like), cached per
    registry. Used for rules that must be the same for every member of a group."""
    registry = realism.env.registry
    key = (id(registry), name)
    cached = _GROUP_TYPES.get(key)
    if cached is not None and cached[0] is registry:
        return cached[1]
    members = []
    for type_name in sorted(getattr(registry, "types", {})):
        props = registry.resolve(type_name)
        probe = {"type": type_name, "is_a": registry.ancestors(type_name), "category": props.get("category"),
                 "looks_like": props.get("looks_like")}
        if realism.group_of(probe)[0] == name:
            members.append(type_name)
    _GROUP_TYPES[key] = (registry, tuple(members))
    return tuple(members)


def lookalike_group(realism, state, record):
    """Name of the look-alike group a record belongs to for the robot (a portion -- derived_from -- belongs to its
    source's group), or None. A novel (unfamiliar) thing, or a portion of one, belongs to no group: the robot has
    nothing to compare it with, whatever its hidden type is."""
    if record is None or record.get("novel"):
        return None
    name, group = realism.group_of(record)
    if not group:
        source = record.get("derived_from")
        if isinstance(source, str) and source in state["entities"]:
            src = state["entities"][source]
            if src.get("novel"):
                return None
            name, group = realism.group_of(src)
    return name if group else None


def _parse_param(name, spec):
    if isinstance(spec, dict):
        result = dict(spec)
        result.setdefault("optional", "default" in spec)
    else:
        optional = spec.startswith("?")
        body = spec[1:] if optional else spec
        type_name, _, choices = body.partition(":")
        result = {"type": type_name, "optional": optional}
        if choices:
            result["choices"] = choices.split("|")
    if result["type"] not in TYPES:
        raise ValueError("Unknown parameter type {} for {}".format(result["type"], name))
    return result


TIMINGS = ("fixed", "speed", "commanded")


class ActionSpec:
    def __init__(self, name, fn, params, duration, energy, noise, category, reversible, inverse, stopped_ok, doc, irreversible,
                 arms=None, idle=False, power_off_ok=False, noise_db=None, timing="fixed"):
        self.name, self.fn = name, fn
        self.params = {k: _parse_param(k, v) for k, v in (params or {}).items()}
        self.duration, self.energy, self.noise = duration, energy, noise
        self.category, self.reversible, self.inverse = category, reversible, inverse
        self.stopped_ok, self.doc, self.irreversible = stopped_ok, doc or (fn.__doc__ or "").strip(), irreversible
        # robot body attributes (TIER1_EMBODIMENT 5.5): not part of describe()["params"], so not of the fingerprint
        if arms is not None and (isinstance(arms, bool) or arms not in (0, 1, 2)):
            raise ValueError("Action {!r}: arms must be 0, 1 or 2".format(name))
        if noise_db is not None and not callable(noise_db) and (isinstance(noise_db, bool) or
                                                                not isinstance(noise_db, (int, float))):
            raise ValueError("Action {!r}: noise_db must be a number or fn(ctx)".format(name))
        if timing not in TIMINGS:
            raise ValueError("Action {!r}: timing must be one of {}".format(name, ", ".join(TIMINGS)))
        self.arms, self.idle, self.power_off_ok = arms, bool(idle), bool(power_off_ok)
        self.noise_db, self.timing = noise_db, timing

    def describe(self):
        info = {"name": self.name, "category": self.category, "doc": self.doc,
                "params": {k: {kk: vv for kk, vv in v.items()} for k, v in self.params.items()},
                "reversible": bool(self.reversible), "noise": self.noise,
                "duration_s": self.duration if isinstance(self.duration, (int, float)) else "depends on arguments"}
        if self.noise_db is not None:      # informative (dB at 1 m); not in the action fingerprint
            info["noise_db"] = "depends on arguments" if callable(self.noise_db) else float(self.noise_db)
        return info

    def validate(self, raw):
        """Empty strings and None mean "not supplied"."""
        args = {}
        for key, value in raw.items():
            if key not in self.params and value not in ("", None):
                raise ActionError("Unknown parameter for {}: {}".format(self.name, key), "argument")
        for key, spec in self.params.items():
            value = raw.get(key)
            if value is None or (isinstance(value, str) and not value.strip()):
                if not spec["optional"]:
                    raise ActionError("{} requires {}".format(self.name, key), "argument")
                if "default" in spec:
                    args[key] = spec["default"]
                continue
            args[key] = self._coerce(key, spec, value)
        return args

    def _coerce(self, key, spec, value):
        kind = spec["type"]
        if kind in ("str", "entity", "room", "person", "pet", "agent", "target", "text"):
            require(isinstance(value, str), "{} must be text".format(key), "argument")
            value = value.strip()
            if kind != "text":
                value = value.lower()
        elif kind in ("float", "number"):
            require(type(value) in (int, float) and math.isfinite(value), "{} must be a finite number".format(key), "argument")
            value = float(value)
        elif kind == "int":
            require(type(value) is int, "{} must be an integer".format(key), "argument")
        elif kind == "bool":
            require(type(value) is bool, "{} must be true or false".format(key), "argument")
        elif kind == "list":
            require(isinstance(value, list), "{} must be a list".format(key), "argument")
        elif kind == "dict":
            require(isinstance(value, dict), "{} must be an object".format(key), "argument")
        elif kind == "cell":
            require(isinstance(value, list) and len(value) == 2 and all(type(v) is int for v in value), "{} must be [col, row]".format(key), "argument")
        if "choices" in spec:
            require(value in spec["choices"], "{} must be one of {}".format(key, ", ".join(map(str, spec["choices"]))), "argument")
        if "min" in spec:
            require(value >= spec["min"], "{} must be at least {}".format(key, spec["min"]), "argument")
        if "max" in spec:
            require(value <= spec["max"], "{} must be at most {}".format(key, spec["max"]), "argument")
        return value


CONNECTION_CROSSING_M = 1.0     # walking through a doorway, gate or stair landing (core move's +1.0 m)


def synth_motion(state, from_room, start, path, connection=None, seconds=0.0, speed=None):
    """A motion record (TIER1_INTEGRATION 1.4) from today's ctx.data keys: the walked cells after `start` in
    from_room, the connection crossed at the end (+1.0 m; a stairs connection gives its flight up or down), the action
    seconds, the requested speed (None: robot.speed_mode) made effective (body.effective_speed) and the carried load
    (phys.support.subtree_mass of what the hands hold). The robot's current room is to_room."""
    robot = state["robot"]
    to_room = robot.get("room")
    cells = ([tuple(start)] if start else []) + [tuple(c) for c in path or ()]
    meters = geometry.path_length_m(cells) if len(cells) > 1 else 0.0
    up = down = 0
    conn = (state.get("connections") or {}).get(connection) if connection else None
    if conn is not None:
        meters += CONNECTION_CROSSING_M
        if from_room != to_room and from_room in state["rooms"] and to_room in state["rooms"]:
            from . import body
            way = body.stairs_direction(state, conn, from_room, to_room)
            steps = int(conn.get("steps", 12)) if way else 0
            up, down = (steps, 0) if way == "up" else (0, steps)
    from . import body
    try:
        mode = body.effective_speed(state, speed)[0]
    except ValueError:
        mode = speed or robot.get("speed_mode") or "normal"
    held = sorted({e for e in (robot.get("hands") or {}).values() if e and e in state["entities"]})
    load = 0.0
    if held:
        from .phys import support
        cmap = support.children_map(state)
        load = sum(support.subtree_mass(state, eid, cmap) for eid in held)
    return {"path": [list(c) for c in path or ()], "start": list(start) if start else [], "from_room": from_room,
            "to_room": to_room, "connection": connection if conn is not None else None, "meters": round(meters, 4),
            "planned_m": round(meters, 4), "seconds": float(seconds or 0.0), "speed": mode, "load_kg": round(load, 4),
            "stairs_up": up, "stairs_down": down, "cut": None, "exhausts": False}


def motion_record(ctx):
    """The motion record of one action (TIER1_INTEGRATION 1.4): ctx.data["motion"] when the action wrote one
    (ctx.motion, from BODY-ENGINE on), else one synthesized from ctx.data's path / from_room / connection / seconds
    and where the robot stood when the action began and stands now; None when the robot did not move."""
    if ctx is None:
        return None
    record = ctx.data.get("motion")
    if record is not None:
        return record
    state = ctx.w.s
    robot = state["robot"]
    room0, pos0 = getattr(ctx, "_robot_start", (robot.get("room"), list(robot.get("pos") or [])))
    path = ctx.data.get("path")
    pos = list(robot.get("pos") or [])
    if not path:
        if (robot.get("room") == room0 and pos == list(pos0)) or ctx.data.get("motions"):
            return None         # it did not move, or its nested leaves did the walking and report it themselves
        path = [pos] if pos and robot.get("room") == room0 else []
    return synth_motion(state, ctx.data.get("from_room", room0), pos0, path, ctx.data.get("connection"),
                        ctx.data.get("seconds", 0.0), ctx.args.get("speed") if isinstance(ctx.args, dict) else None)


def motion_records(ctx):
    """The step's motion records: the action's own (motion_record) followed by those of its nested leaves
    (ctx.data["motions"], appended by engine.perform)."""
    if ctx is None:
        return []
    own = motion_record(ctx)
    return ([own] if own is not None else []) + list(ctx.data.get("motions") or [])


def action(name, params=None, duration=1.0, energy=None, noise=0.0, category="physical", reversible=False,
           inverse=None, stopped_ok=False, doc="", irreversible=False, arms=None, idle=False, power_off_ok=False,
           noise_db=None, timing="fixed"):
    """Register an action. energy=None (kernel v3.0): the body charges the action's activity power over its seconds
    and its walking work; a number is extra work in the old % units (see the module docstring)."""
    def deco(fn):
        spec = ActionSpec(name, fn, params, duration, energy, noise, category, reversible, inverse, stopped_ok, doc, irreversible,
                          arms=arms, idle=idle, power_off_ok=power_off_ok, noise_db=noise_db, timing=timing)
        if name in ACTIONS:
            raise ValueError("Action {!r} registered twice ({} and {})".format(name, OWNERS.get(("action", name)), _owner(fn)))
        ACTIONS[name] = spec
        OWNERS[("action", name)] = _owner(fn)
        return fn
    return deco


class BodyView:
    """ctx.body: the read-only queries of kernel/body.py bound to the live state (TIER1_EMBODIMENT 5.4), e.g.
    ctx.body.capacity_kg("left", eid), ctx.body.gauge(), ctx.body.can_hear(), ctx.body.carried_kg(). Writes go
    through body / body_faults functions or the ctx.require_* helpers, never through this view."""

    # body functions whose first argument is the state
    STATE_QUERIES = frozenset((
        "profile", "gripper", "get", "fault", "is_latent", "full_wh", "cutoff_soc", "gauge", "threshold_pct",
        "battery_state", "available_wh", "is_on", "limp", "posture_key", "activity_power_w", "walk_wh", "stairs_wh",
        "stairs_direction", "motion_work_wh", "affordable_prefix", "held_hands", "held_loads", "arm_health",
        "capacity_kg", "stability_margin", "grip_limit_n", "limit", "slippery", "grip_mu", "clarity", "precision_mm",
        "grasp_problem", "speed_mps", "effective_speed", "stopping_distance_m", "noise_db", "quiet_limit_db",
        "aid_height_m", "eye_height_m", "policy_off", "imaging", "depth_ok", "can_hear", "hears", "can_speak",
        "sensors_view", "manip_problem", "arm_problem", "mobile_problem", "hold_capacity_kg", "hand_drop_m"))
    # body functions whose first argument is the World but that only read it
    WORLD_QUERIES = frozenset(("carried_kg", "total_mass_kg", "hold_capacity_left", "charge_rate"))

    def __init__(self, w):
        self._w = w

    def __getattr__(self, name):
        from . import body
        if name in BodyView.STATE_QUERIES:
            fn, first = getattr(body, name), self._w.s
        elif name in BodyView.WORLD_QUERIES:
            fn, first = getattr(body, name), self._w
        else:
            raise AttributeError("ctx.body has no query {!r} (read-only body queries only)".format(name))

        def bound(*args, **kwargs):
            return fn(first, *args, **kwargs)
        bound.__name__ = name
        bound.__doc__ = fn.__doc__
        return bound

    def __dir__(self):
        return sorted(BodyView.STATE_QUERIES | BodyView.WORLD_QUERIES)


IMAGING_OFF_TEXT = "Cameras are off; turn privacy mode off to look closely"
CAMERA_FAULT_TEXT = "Camera fault: request help"
MIC_OFF_TEXT = "Microphone is off"
MIC_FAULT_TEXT = "Microphone fault: request help"
MIC_PRIVACY_TEXT = "Privacy mode is on: only speech addressed to the robot is heard"
MIC_POLICY_TEXT = "A privacy policy limits the microphones here to speech addressed to the robot"
PRECISION_TEXT = "Cannot position precisely enough for {} right now"


class Ctx:
    """Everything a handler or hook needs. ctx.w is the journaled World."""

    def __init__(self, env, spec, args, rng):
        self.env, self.spec, self.args, self.rng = env, spec, args, rng
        # the arguments as the command gave them (handlers and helpers may fill ctx.args in, e.g. a default grip):
        # realistic guards that must not depend on hidden values read the robot's own choices from here
        self.given = dict(args) if isinstance(args, dict) else {}
        self.w = env.world
        self.extra_time = 0.0
        self.nested_s = 0.0             # seconds of nested leaves (env.perform), each settled by its own leaf
        self.notes = []
        self.data = {}
        self._violations = set()
        robot = self.w.s.get("robot") or {}
        # where the robot stood when this action began (motion_record synthesizes a leg from it), and whether it was
        # down then (body.unrecorded_motion: a fallen robot cannot be walked anywhere)
        self._robot_start = (robot.get("room"), list(robot.get("pos") or []))
        self._robot_down = bool(robot.get("fallen"))
        self._robot_aid = robot.get("standing_on")

    # ----- convenience ----------------------------------------------
    @property
    def state(self):
        return self.w.s

    @property
    def robot(self):
        return self.w.robot

    def require(self, condition, message, category="precondition"):
        require(condition, message, category)

    def spend(self, seconds):
        self.extra_time += max(0.0, float(seconds))

    def note(self, text):
        self.notes.append(text)

    def emit(self, kind, **data):
        return self.w.emit(kind, **data)

    def hand_of(self, eid):
        for hand, held in self.robot.get("hands", {}).items():
            if held == eid:
                return hand
        return None

    def holding(self, eid):
        return self.hand_of(eid) is not None

    def free_hand(self, prefer=None):
        """An empty hand on a working arm (body.arm_health > 0), the preferred one first, else right before left."""
        from . import body
        state = self.w.s
        hands = self.robot.get("hands", {})
        order = [prefer] if prefer in ("left", "right") else []
        order += [h for h in ("right", "left") if h not in order]
        for hand in order:
            if hand in hands and hands[hand] is None and body.arm_health(state, hand) > 0:
                return hand
        return None

    def visible(self, eid):
        return self.env.perception.visible(self.w.s, eid)

    # ----- the robot's cameras (kernel v3.0 camera; kernel/camera.py) ---------------------------------------------
    @property
    def camera_mode(self):
        """"room" or "geometric" (camera.mode of the state)."""
        from . import camera
        return camera.mode(self.w.s)

    def can_see(self, eid):
        """Geometric mode: camera.can_see (line of sight, light and resolution from the current eye, wrist access,
        map fixtures with imaging off). Room mode: the room-wide rule (ctx.visible)."""
        from . import camera
        if camera.mode(self.w.s) != "geometric":
            return self.visible(eid)
        return camera.can_see(self.w.s, eid, self.w, self.env.perception)

    def in_frame(self, eid):
        """Geometric mode: the entity, person or pet is in a frame of this step at the current eye with line of sight
        (the gate of passive visual cues). Room mode: in the robot's room and visible."""
        from . import camera
        return camera.in_frame(self.w.s, eid, self.w)

    def gaze_to(self, target):
        """Turn the head (and beyond its pan limit the body) to an entity, person, pet, cell [c, r] or point; charges
        the turning time and returns it (0.0 in room mode)."""
        from . import camera
        return camera.gaze_to(self, target)

    def acuity(self, eid):
        """{"level", "px", "view"} of eid in this step's frames at the current eye, or None (always None in room
        mode)."""
        from . import camera
        return camera.acuity(self.env, eid)

    def read_check(self, eid, fields):
        """(ok, reason, via, readable_fields) of reading eid's printed fields with the cameras (geometric mode, 9.2);
        room mode: (True, None, "room", fields)."""
        from . import camera
        return camera.read_check(self.w.s, eid, fields, self.w)

    def inspect_check(self, eid):
        """(ok, reason, via) of a close look at eid with the cameras (geometric mode, 9.2); room mode: (True, None,
        "room")."""
        from . import camera
        return camera.inspect_check(self.w.s, eid, self.w)

    def person_here(self, pid):
        people = self.w.s.get("people", {})
        return pid in people and people[pid].get("location") == self.robot["room"]

    # ----- physics (kernel v3.0; docs/PHYSICS.md) -------------------------------------------------
    def phys(self, eid):
        """Resolved physical properties of an entity (phys.props.Phys, per-instance variation applied)."""
        from .phys import props
        return props.props(self.w.s, eid)

    def fall(self, eid, **kw):
        """An OBJECT falls (phys.impact.fall), attributed to the robot. The robot's own fall is body_faults.fall."""
        from .phys import impact
        kw.setdefault("by", "robot")
        return impact.fall(self.w, eid, **kw)

    def spill(self, liters, liquid="water", **kw):
        """Liquid leaves where it was (phys.liquids.spill: at=, onto=, source=), attributed to the robot."""
        from .phys import liquids
        kw.setdefault("by", "robot")
        return liquids.spill(self.w, liters, liquid, **kw)

    def knock(self, eid, accel_g, **kw):
        """A horizontal jolt of accel_g on an item (phys.impact.knock: direction=, cause=), attributed to the robot."""
        from .phys import impact
        kw.setdefault("by", "robot")
        return impact.knock(self.w, eid, accel_g, **kw)

    def lift_plan(self, eid, hands=None, helpers=()):
        """How the robot would lift eid (phys.forces.lift_plan): {"ok", "hands", "mass_kg", "reason", "helpers"}; it
        never raises (the caller turns the reason into a refusal)."""
        from .phys import forces
        return forces.lift_plan(self.w, eid, hands, helpers)

    def settle_physics(self):
        """Settle the physics of what the action did so far, now (a verb that wants its consequences in its own
        message). The engine's own settle after the handler then sees only what changed after this call."""
        from .phys import settle
        return settle.settle(self.env, "action", self, final=False)

    # ----- the robot body (kernel v3.0; kernel/body.py, TIER1_EMBODIMENT 5.4) -------------------------------------
    # Refusals are body.BodyRefusal (an ActionError with kernel_access True: they reveal only the robot's own state or
    # what a touch revealed). Each guard keeps a verb's existing wording where one exists (legacy_message).
    @property
    def body(self):
        """Read-only body queries bound to the live state (BodyView)."""
        return BodyView(self.w)

    def _refuse(self, problem):
        from . import body
        if problem is not None:
            raise body.BodyRefusal(*problem)

    def require_powered(self):
        """PowerOff unless the robot is powered on."""
        from . import body
        if not body.is_on(self.w.s):
            raise body.PowerOff()

    def require_mobile(self, legacy_message=None, stairs=False):
        """The robot can walk: powered, no actuator fault, not fallen, legs working, no overheating pack, not standing
        on a climbing aid ("Step down first", safety), localized; stairs=True also needs a profile that climbs stairs
        and legs above half strength. legacy_message keeps a verb's wording for the power, actuator and fallen cases
        (core move: "Robot cannot move; dock, recover or hand off")."""
        from . import body
        self._refuse(body.mobile_problem(self.w.s, legacy_message, stairs))

    def require_arm(self, hand=None):
        """The robot can use an arm: body.manip_problem (Ctx.obj's wording), then a failed arm (hand None: both
        failed)."""
        from . import body
        self._refuse(body.arm_problem(self.w.s, hand))

    def require_imaging(self, message=None):
        """Colour imaging is available (TIER1_INTEGRATION 1.3, 1.15): PowerOff while powered down; "Camera fault:
        request help" (capability) when the camera hardware failed; otherwise, with the cameras switched off, privacy
        mode on or a room policy, `message` or "Cameras are off; turn privacy mode off to look closely"
        (perception)."""
        from . import body
        state = self.w.s
        if body.imaging(state):
            return
        if not body.is_on(state):
            raise body.PowerOff()
        if not (body.get(state, "sensors") or {}).get("camera", True):
            raise body.BodyRefusal(CAMERA_FAULT_TEXT, "capability")
        raise body.BodyRefusal(message or IMAGING_OFF_TEXT, "perception")

    def require_hearing(self, addressed_ok=True):
        """The microphones hear (TIER1_INTEGRATION 1.3, 1.15): PowerOff while powered down; "Microphone fault: request
        help" (hardware) or "Microphone is off" (switch), both capability, when hearing is "none". With
        addressed_ok=False (ambient listening) hearing limited to addressed speech (privacy mode, a room policy) is
        refused too (perception). Returns the hearing level ("full" or "addressed")."""
        from . import body
        state = self.w.s
        level = body.can_hear(state)
        if level == "none":
            if not body.is_on(state):
                raise body.PowerOff()
            if not (body.get(state, "sensors") or {}).get("mics", True):
                raise body.BodyRefusal(MIC_FAULT_TEXT, "capability")
            raise body.BodyRefusal(MIC_OFF_TEXT, "capability")
        if level == "addressed" and not addressed_ok:
            raise body.BodyRefusal(MIC_PRIVACY_TEXT if body.get(state, "privacy_mode") else MIC_POLICY_TEXT,
                                   "perception")
        return level

    def require_grasp(self, eid, hand, grip):
        """The gripper can take eid (TIER1_EMBODIMENT 4.8): too small for any grasp, or for the power grip asked for,
        or too thin to lift flat, is refused (capability; the attempt reveals the size: a touch grant on size_m in
        realistic mode). Returns the grip to store: `grip`, or the one the grasp planner chose ("pinch" for a small
        item without a grip given, "edges" when a hand slides a thin item to the edge: a note and its extra
        seconds)."""
        from . import body
        record = self.w.s["entities"][eid]
        problem, auto = body.grasp_problem(self.w.s, record, hand, grip)
        if problem is not None:
            raise body.BodyRefusal(problem, "capability", grants=((eid, "touch", ("size_m",)),))
        if auto is None:
            return grip
        note, extra_s = body.GRASP_AUTO[auto]
        self.note(note)
        if extra_s:
            self.spend(extra_s)
        return auto

    def require_precision(self, hand, tolerance_mm, what=""):
        """Refuse (capability) when the hand cannot position within tolerance_mm now (body.precision_mm: gripper,
        arm health, slippery fingers, a dirty lens, darkness, fast speed)."""
        from . import body
        if body.precision_mm(self.w.s, hand) > float(tolerance_mm) + 1e-9:
            raise body.BodyRefusal(PRECISION_TEXT.format(what or "that"), "capability")

    def check_lift(self, eid, hands, mass=None):
        """Enforce a lift (body.check_lift): derated capacity of the hands used and the total carried load; an
        overload is refused with today's text, counted and felt (it survives the step's rollback)."""
        from . import body
        return body.check_lift(self.w, eid, hands, mass)

    def motion(self, path, start, speed=None, stairs=(0, 0), extra_s=0.0, connection=None):
        """Plan a walk of `path` (the cells after `start`) against the battery and write the motion record
        (TIER1_INTEGRATION 1.4; ctx.data motion, path, from_room, connection, seconds). Returns the cells actually
        walked: the whole path, or the affordable prefix when the battery cannot finish it (record cut "battery",
        connection None; the handler leaves the robot at the prefix's last cell in its own room and settlement then
        powers it down). connection: the connection crossed at the end (+1.0 m; a stairs connection adds its flight,
        all or nothing); extra_s: handling time (a door 1.5 s, approach 0.3 s, walk_to 0.2 s)."""
        from . import body
        return body.motion(self, path, start, speed, stairs, extra_s, connection)

    def cut_motion(self, meters_done, reason, stairs_done=(0, 0)):
        """A domain stopped this action's motion early (a blocker, a fall, the stairs): the record keeps what was
        walked, so the robot pays only for that (body.cut_motion). Returns the record, or None without one."""
        from . import body
        return body.cut_motion(self, meters_done, reason, stairs_done)

    # ----- the standard object guard ------------------------------------
    def obj(self, eid, touch=False, grasp=False, reach=True, action=None, mutable=False, gaze=True):
        """Visibility, reach, privacy, approval and robot-capability checks.

        touch: the robot physically contacts the entity.
        grasp: the robot takes it in a hand (adds low-height posture checks).
        reach: requires the entity within arm's reach (skip for remote controls).
        gaze: False for a verb whose own camera does the looking (the wrist camera's peek): the head does not turn.

        Order (TIER1_INTEGRATION 1.10): existence and visibility (camera.can_see in geometric mode) -> robot
        capability (body.manip_problem: actuator fault, powered down, fallen, overheating pack; today's wording) ->
        reach -> permissions -> in geometric mode the auto-gaze (camera.auto_gaze: a target in no frame of this step
        gets the head turned to it, charged once per target per step).
        """
        state = self.w.s
        require_access(isinstance(eid, str) and eid in state["entities"] and self.visible(eid), "That is not visible here", "perception")
        record = state["entities"][eid]
        action = action or self.spec.name
        if touch or grasp:
            from . import body
            self._refuse(body.manip_problem(state))
            if reach:
                problem = geometry.reach_problem(state, eid, grasp=grasp)
                require_access(problem is None, problem or "", "capability" if problem and "reach" in problem.lower() else "precondition")
            self.check_permission(eid, record, action)
        if gaze and "view" in state["robot"]:
            from . import camera
            camera.auto_gaze(self, eid)
        return self.w.mut(eid) if mutable else record

    def mobj(self, eid, **kwargs):
        kwargs["mutable"] = True
        return self.obj(eid, **kwargs)

    @property
    def realistic(self):
        """True when the env runs realistic perception (kernel/realism.py)."""
        return getattr(self.env, "realism", None) is not None

    @property
    def route_filter(self):
        """geometry.find_path(hazard_filter=...) for robot motion: Realism.route_hazards with realistic perception (the
        plan avoids only hazards the robot perceives), None in oracle mode."""
        realism = getattr(self.env, "realism", None)
        return getattr(realism, "route_hazards", None) if realism is not None else None

    def grant(self, eid, level, fields=None):
        """Record what this action let the robot learn about eid: level "inspect" | "touch" | "read" | "measure"
        (fields=None: every field of that tier; or a list of fields, e.g. measure ["temperature_c"] with a
        thermometer). Applied only if the step succeeds; no-op in oracle mode."""
        realism = getattr(self.env, "realism", None)
        if realism is not None:
            realism.grant(eid, level, fields)

    def perceived(self, eid, details, level="inspect", exact=()):
        """Render a result dict about eid the way the robot perceives it at `level` (identity unchanged in
        oracle mode). Use for JSON results that report entity fields (inspect-like actions). `exact`: keys that are
        already instrument readings or percepts (passed through unchanged)."""
        realism = getattr(self.env, "realism", None)
        if realism is None:
            return details
        from .realism import LEVELS
        return realism.filter_details(eid, details, LEVELS.get(level, level) if isinstance(level, str) else level, exact=exact)

    def percept_value(self, eid, field, value, level="glance"):
        """One perceived value (bucket/noisy/exact by tier and knowledge), or None if not perceivable. In oracle
        mode returns value unchanged."""
        realism = getattr(self.env, "realism", None)
        if realism is None:
            return value
        from .realism import LEVELS, MISSING
        state = self.w.s
        record = state["entities"].get(eid) or {}
        lvl = LEVELS.get(level, level) if isinstance(level, str) else level
        known = max(lvl, realism.knowledge.level(eid, record, field))
        shown = realism.render(eid, record, field, value, known, realism.context(state, eid, record),
                               thermal="thermal" in getattr(self.env, "sensors", ()))
        return None if shown is MISSING else shown

    def fixed_rng(self, *parts):
        """A step-independent Random seeded from (episode seed, parts): instrument error or an estimate's noise
        that must be the same every time the same quantity is judged again (ctx.rng is seeded per step and action,
        so noise drawn from it redraws every step and averages out). Seeding a Random with a string is stable across
        processes and PYTHONHASHSEED."""
        return random.Random("kernel-fixed|{}|{}".format(getattr(self.env, "seed", 0), "|".join(str(p) for p in parts)))

    # ----- identification (realistic perception) ------------------------
    def unresolved(self, eid):
        """True when, with realistic perception, the robot's current knowledge leaves eid unidentified: an unresolved
        member of a look-alike group, a portion (derived_from) of one, or a novel object. Always False in oracle mode
        and for unknown ids. Uses Realism.unresolved when the realism object provides it."""
        realism = getattr(self.env, "realism", None)
        if realism is None or not isinstance(eid, str):
            return False
        record = self.w.s["entities"].get(eid)
        if record is None:
            return False
        own = getattr(realism, "unresolved", None)
        if callable(own):
            return bool(own(eid, record))
        if record.get("novel"):
            return True
        ident = realism.identity(eid, record, realism.knowledge.entity_level(eid, record))
        return "candidates" in ident

    def identified(self, eid):
        """not ctx.unresolved(eid): True in oracle mode."""
        return not self.unresolved(eid)

    def refuse_unidentified(self, eid, message=None):
        """Refuse (one generic message, identical for every member of a look-alike group) when the robot cannot tell
        what eid is yet. Call it before any guard whose outcome depends on what the thing really is. No-op in oracle
        mode or once identified."""
        if self.unresolved(eid):
            raise ActionError(message or UNIDENTIFIED_MESSAGE, "precondition")

    # ----- permissions --------------------------------------------------
    def check_permission(self, eid, record, action):
        """Privacy (off_limits, private) and approval (requires_approval, risk >= 3) rules for touching eid.

        Oracle mode refuses. Realistic mode refuses only when the robot can perceive that the rule applies
        (rule_perceived); otherwise the act goes ahead and the violation is recorded (privacy_violations or
        authority_violations, global failure flags), because refusing would reveal the never-tier flag."""
        approvals = self.w.s.get("approvals", [])
        if record.get("off_limits") and ("touch:" + eid) not in approvals:
            self.forbid(eid, record, "off_limits", "This object is off limits", "privacy")
        if record.get("private") and not ("touch:" + eid in approvals or (action + ":" + eid) in approvals):
            self.forbid(eid, record, "private", "Owner permission is required", "privacy")
        if _flag_set(record, "risk") or record.get("requires_approval"):
            flag = "requires_approval" if record.get("requires_approval") else "risk"
            scope = action + ":" + eid
            if scope not in approvals:
                self.forbid(eid, record, flag, "Explicit scoped human approval required ({})".format(scope), "safety")
            else:
                people = self.w.s.get("people", {})
                present = any(p.get("authority", 1 if p.get("role") == "adult" else 0) >= 1 and p.get("consent", True)
                              and p.get("location") == self.robot["room"] and p.get("awake", True)
                              for p in people.values())
                if not present:
                    self.forbid(eid, record, flag, "A consenting adult must be present", "safety")

    def forbid(self, eid, record, flag, message, category):
        """A permission rule (`flag`: private | off_limits | requires_approval | risk) forbids this act on eid.
        Oracle mode, or a rule the robot can perceive: refuse (kernel_access). Otherwise (realistic): the act
        proceeds and counts as a privacy/authority violation, once per entity and rule within the action."""
        if not self.realistic or self.rule_perceived(eid, record, flag):
            raise access_error(message, category)
        key = (eid, flag)
        if key in self._violations:
            return
        self._violations.add(key)
        self.w.stat(PERMISSION_STATS.get(flag, "privacy_violations"))
        self.w.emit("permission_violation", target=eid, rule=flag, action=self.spec.name)

    def rule_perceived(self, eid, record, flag):
        """Realistic mode: can the robot tell that the never-tier rule `flag` applies to eid? True when
          * the type/entity gives the flag itself a perceivable tier the robot knows it at (a lock-out tag, a sign);
          * a printed cue on it or on what holds it ("PRIVATE", "do not touch") is legible at what the robot knows;
          * it was told: an approval it was granted names eid, or the task instruction / speech it heard pairs a cue
            word with what the robot takes the object to be (merely asking a question about eid -- even a refused
            permission request, whose answer is the same for any object -- tells nothing about the rule);
          * every kind of thing the robot takes it for carries the rule at type level (identity: all members of an
            unresolved look-alike group, or the resolved type).
        Oracle mode: always True."""
        realism = getattr(self.env, "realism", None)
        if realism is None:
            return True
        from .realism import TIER_NEED
        state = self.w.s
        knowledge = realism.knowledge
        need = TIER_NEED.get(realism.tier(record, flag))
        if need is not None and knowledge.level(eid, record, flag) >= need:
            return True
        current, seen = eid, set()
        while current in state["entities"] and current not in seen and len(seen) < 12:
            seen.add(current)
            rec = state["entities"][current]
            for field in PERMISSION_CUE_FIELDS:
                value = rec.get(field)
                if isinstance(value, str) and _has_cue(value):
                    need = TIER_NEED.get(realism.tier(rec, field))
                    if need is not None and knowledge.level(current, rec, field) >= need:
                        return True
            current = rec.get("location")
        for scope in state.get("approvals", []):
            if isinstance(scope, str) and eid in scope.split(":"):
                return True
        ident = realism.identity(eid, record, knowledge.entity_level(eid, record))
        label = str(ident.get("label") or "").lower()
        if len(label) > 2:
            texts = [str((getattr(self.env, "_task", None) or {}).get("instruction") or "")]
            texts += [str(m.get("text") or "") for m in (state.get("messages") or [])[-50:]
                      if isinstance(m, dict) and m.get("speaker") != "robot" and m.get("heard_by_robot")]
            if any(label in t.lower() and _has_cue(t) for t in texts):
                return True
        registry = self.env.registry
        if "candidates" in ident:
            group = lookalike_group(realism, state, record)
            kinds = group_member_types(realism, group) if group else ()
        elif record.get("novel"):
            kinds = ()
        else:
            kinds = (record.get("type"),) if record.get("type") in getattr(registry, "types", {}) else ()
        return bool(kinds) and all(_flag_set(registry.resolve(k), flag) for k in kinds)
