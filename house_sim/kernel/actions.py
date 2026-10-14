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
        # the arguments as the command gave them (handlers and helpers may fill ctx.args in, e.g. a default grip):
        # realistic guards that must not depend on hidden values read the robot's own choices from here
        self.given = dict(args) if isinstance(args, dict) else {}
        self.w = env.world
        self.extra_time = 0.0
        self.notes = []
        self.data = {}
        self._violations = set()

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
        require_access(isinstance(eid, str) and eid in state["entities"] and self.visible(eid), "That is not visible here", "perception")
        record = state["entities"][eid]
        action = action or self.spec.name
        if touch or grasp:
            robot = self.robot
            require_access(robot.get("actuator_ok", True), "Actuator fault; request human assistance", "capability")
            require_access(robot.get("battery", 100) > 0, "Battery depleted; dock or request assistance", "capability")
            require_access(not robot.get("fallen"), "The robot has fallen; recover first", "capability")
            if reach:
                problem = geometry.reach_problem(state, eid, grasp=grasp)
                require_access(problem is None, problem or "", "capability" if problem and "reach" in problem.lower() else "precondition")
            self.check_permission(eid, record, action)
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
