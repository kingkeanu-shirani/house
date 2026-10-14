"""Time verbs and standing orders: wait, watch, cancel_watch, run_pending, and the rule for what a standing order may
watch (watch_condition_visible).

Moved out of systems/core.py unchanged by the sweep's Phase 0 (docs/design/TIER1_SWEEP.md 22.1, TIER1_INTEGRATION.md
2.3 "A0"): the code, messages, parameters and registration names are exactly the former core ones, so behaviour,
state and observation digests are unchanged. Only the action fingerprint changes, because `wait`'s callable duration
is now defined in this module. engine.py imports this module (which registers the four actions), and systems/core.py
re-exports every public name for domains and tests (`core.OBSERVABLE_FIELDS` is the same set object, so a domain that
extends it still extends the rule used here).

Ownership: BODY-ENGINE gives `wait` its body attributes in B4; TIME-EVAL owns the module in stage C (interruptible
waits, auto standing orders, notice kinds and `perceivable`, sweep 6.3-6.5). The stage C names of TIER1_SWEEP 16.1
are defined below as stubs marked TIER1-STUB(TIME-EVAL); nothing calls them yet.
"""
import copy

from .actions import action
from .errors import ActionError, require
from .plugins import ACTIONS
from .predicates import validate_predicate

OBSERVABLE_FIELDS = {"open", "on", "locked", "wetness", "state", "level", "water_level", "alarm", "beeping",
                     "temperature_c", "cycle", "position", "occupied", "running", "lit", "condition"}
# Domains may extend what standing orders can watch, e.g. core.OBSERVABLE_FIELDS.add("water_level")
# or core.AGENT_OBSERVABLE_FIELDS.add("thirst") (systems/core.py re-exports these very sets).
AGENT_OBSERVABLE_FIELDS = {"location", "awake", "posture", "activity", "mood"}
REALISTIC_WATCH_AGENT_FIELDS = {"location", "posture"}
NUMERIC_OPS = ("gt", "gte", "lt", "lte", "approx", "between")


def watch_condition_visible(w, rule, realism=None):
    """Standing orders may only watch what the robot can observe. In realistic perception that means glance-tier
    fields compared without exact numeric thresholds, people's/pets' location and posture, and no 'ref' operands
    into anything else."""
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
# time verbs and standing orders (registration names, parameters and texts as they were in systems/core.py)

@action("wait", params={"seconds": {"type": "int", "default": 1, "min": 1, "max": 604800}}, energy=0.0,
        duration=lambda ctx: float(ctx.args["seconds"]), category="control", stopped_ok=True,
        doc="Let simulated time pass (no real sleeping). Appliances, people, pets and weather keep running.")
def wait(ctx):
    return "Waited {} s".format(ctx.args["seconds"])


@action("watch", params={"condition": "dict", "commands": "list", "persistent": {"type": "bool", "default": False}},
        duration=1.0, energy=0.0, category="control",
        doc="Standing order: when an observable condition becomes true, queue commands (run them with run_pending). persistent=true re-arms after the condition clears.")
def watch(ctx):
    condition, commands = ctx.args["condition"], ctx.args["commands"]
    try:
        validate_predicate(condition)
    except ValueError as exc:
        raise ActionError(str(exc), "argument")
    require(0 < len(commands) <= 20, "Supply 1..20 commands", "argument")
    require(watch_condition_visible(ctx.w, condition, getattr(ctx.env, "realism", None)),
            "Watch only conditions the robot can observe (visible, audible or reported by a connected device)")
    for command in commands:
        require(isinstance(command, dict) and command.get("action") in ACTIONS and command["action"] not in ("watch", "run_pending"),
                "Unsupported scheduled command", "argument")
    orders = ctx.w.sec("orders")
    orders.append({"condition": copy.deepcopy(condition), "commands": copy.deepcopy(commands), "fired": False,
                   "persistent": ctx.args["persistent"], "armed": True, "created_s": ctx.w.s["time_s"]})
    return "Watching (order {})".format(len(orders) - 1)


@action("cancel_watch", params={"target": "str"}, duration=0.5, energy=0.0, category="control", stopped_ok=True,
        doc="Cancel a standing order by its index.")
def cancel_watch(ctx):
    orders = ctx.w.sec("orders")
    require(ctx.args["target"].isdigit() and int(ctx.args["target"]) < len(orders), "No such order", "argument")
    orders[int(ctx.args["target"])]["fired"] = True
    orders[int(ctx.args["target"])]["persistent"] = False
    return "Cancelled order " + ctx.args["target"]


@action("run_pending", duration=0.5, energy=0.0, category="control",
        doc="Execute the next queued standing-order command (it takes its own time and guards).")
def run_pending(ctx):
    raise ActionError("No pending command")


# ----------------------------------------------------------------------
# stage C contract (TIER1_SWEEP 16.1, frozen signatures). TIME-EVAL implements these and removes the markers.

NOTICE_KINDS = {}    # kind -> {"doc", "implies"}; filled by register_notice_kind (sweep 6.3). TIER1-STUB(TIME-EVAL)


def perceivable(w, rule, realism=None):
    """Whether the robot could observe `rule` holding (sweep 6.5). Until TIME-EVAL lands its three fixes (realism at
    fire time, the weather rule, refusing notices.* paths) this is exactly today's rule, watch_condition_visible.
    TIER1-STUB(TIME-EVAL)"""
    return watch_condition_visible(w, rule, realism)


def register_notice_kind(kind, doc, implies=()):
    """Declare a notice kind that `wait(interrupt_on=...)` accepts (sweep 6.3). TIER1-STUB(TIME-EVAL)"""
    raise NotImplementedError("timeline.register_notice_kind lands with TIME-EVAL (TIER1_SWEEP 6.3)")


def robot_perceives(w, notice):
    """The notice as the robot perceives it (a dict) or None (sweep 6.2; applies the robot_hears hook).
    TIER1-STUB(TIME-EVAL)"""
    raise NotImplementedError("timeline.robot_perceives lands with TIME-EVAL (TIER1_SWEEP 6.2)")


def run_auto_orders(env, idle_ctx):
    """Engine-internal: run the due commands of auto standing orders during an idle advance; returns the order-log
    records (sweep 6.4). TIER1-STUB(TIME-EVAL)"""
    raise NotImplementedError("timeline.run_auto_orders lands with TIME-EVAL (TIER1_SWEEP 6.4)")
