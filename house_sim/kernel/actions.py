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
"""
import math

from . import geometry
from .errors import ActionError, require
from .plugins import ACTIONS, OWNERS, _owner

TYPES = {"str", "int", "float", "number", "bool", "entity", "room", "person", "pet", "agent",
         "target", "list", "dict", "any", "cell", "text"}


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


class ActionSpec:
    def __init__(self, name, fn, params, duration, energy, noise, category, reversible, inverse, stopped_ok, doc, irreversible):
        self.name, self.fn = name, fn
        self.params = {k: _parse_param(k, v) for k, v in (params or {}).items()}
        self.duration, self.energy, self.noise = duration, energy, noise
        self.category, self.reversible, self.inverse = category, reversible, inverse
        self.stopped_ok, self.doc, self.irreversible = stopped_ok, doc or (fn.__doc__ or "").strip(), irreversible

    def describe(self):
        return {"name": self.name, "category": self.category, "doc": self.doc,
                "params": {k: {kk: vv for kk, vv in v.items()} for k, v in self.params.items()},
                "reversible": bool(self.reversible), "noise": self.noise,
                "duration_s": self.duration if isinstance(self.duration, (int, float)) else "depends on arguments"}

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


def action(name, params=None, duration=1.0, energy=0.01, noise=0.0, category="physical", reversible=False,
           inverse=None, stopped_ok=False, doc="", irreversible=False):
    def deco(fn):
        spec = ActionSpec(name, fn, params, duration, energy, noise, category, reversible, inverse, stopped_ok, doc, irreversible)
        if name in ACTIONS:
            raise ValueError("Action {!r} registered twice ({} and {})".format(name, OWNERS.get(("action", name)), _owner(fn)))
        ACTIONS[name] = spec
        OWNERS[("action", name)] = _owner(fn)
        return fn
    return deco


class Ctx:
    """Everything a handler or hook needs. ctx.w is the journaled World."""

    def __init__(self, env, spec, args, rng):
        self.env, self.spec, self.args, self.rng = env, spec, args, rng
        self.w = env.world
        self.extra_time = 0.0
        self.notes = []
        self.data = {}

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
        hands = self.robot.get("hands", {})
        arms = self.robot.get("arms_ok", {"left": True, "right": True})
        order = [prefer] if prefer in ("left", "right") else []
        order += [h for h in ("right", "left") if h not in order]
        for hand in order:
            if hand in hands and hands[hand] is None and arms.get(hand, True):
                return hand
        return None

    def visible(self, eid):
        return self.env.perception.visible(self.w.s, eid)

    def person_here(self, pid):
        people = self.w.s.get("people", {})
        return pid in people and people[pid].get("location") == self.robot["room"]

    # ----- the standard object guard ------------------------------------
    def obj(self, eid, touch=False, grasp=False, reach=True, action=None, mutable=False):
        """Visibility, reach, privacy, approval and robot-capability checks.

        touch: the robot physically contacts the entity.
        grasp: the robot takes it in a hand (adds low-height posture checks).
        reach: requires the entity within arm's reach (skip for remote controls).
        """
        state = self.w.s
        require(isinstance(eid, str) and eid in state["entities"] and self.visible(eid), "That is not visible here", "perception")
        record = state["entities"][eid]
        action = action or self.spec.name
        if touch or grasp:
            robot = self.robot
            require(robot.get("actuator_ok", True), "Actuator fault; request human assistance", "capability")
            require(robot.get("battery", 100) > 0, "Battery depleted; dock or request assistance", "capability")
            require(not robot.get("fallen"), "The robot has fallen; recover first", "capability")
            if reach:
                problem = geometry.reach_problem(state, eid, grasp=grasp)
                require(problem is None, problem or "", "capability" if problem and "reach" in problem.lower() else "precondition")
            self.check_permission(eid, record, action)
        return self.w.mut(eid) if mutable else record

    def mobj(self, eid, **kwargs):
        kwargs["mutable"] = True
        return self.obj(eid, **kwargs)

    @property
    def realistic(self):
        """True when the env runs realistic perception (kernel/realism.py)."""
        return getattr(self.env, "realism", None) is not None

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

    def check_permission(self, eid, record, action):
        approvals = self.w.s.get("approvals", [])
        require(not record.get("off_limits") or ("touch:" + eid) in approvals, "This object is off limits", "privacy")
        if record.get("private"):
            require("touch:" + eid in approvals or (action + ":" + eid) in approvals, "Owner permission is required", "privacy")
        if record.get("risk", 0) >= 3 or record.get("requires_approval"):
            scope = action + ":" + eid
            require(scope in approvals, "Explicit scoped human approval required ({})".format(scope), "safety")
            people = self.w.s.get("people", {})
            present = any(p.get("authority", 1 if p.get("role") == "adult" else 0) >= 1 and p.get("consent", True)
                          and p.get("location") == self.robot["room"] and p.get("awake", True)
                          for p in people.values())
            require(present, "A consenting adult must be present", "safety")
