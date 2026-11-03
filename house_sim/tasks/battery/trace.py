"""The battery's view of a reference run (VERIFY_HARDENING 9.5, 10.1, 10.5; WP6).

    command_class(entry)          PERCEPTION | NAVIGATION | COMM | DEPOSIT | WAIT | EFFECT of a trace record
                                  (house_sim/data/verify/actions.json; open / close of a door or gate a route skill
                                  issued on the way is NAVIGATION)
    effectful(entry)              a succeeded EFFECT or DEPOSIT command
    replay_step(entry)            the record as a concrete "do" step (true ids; what the reference sent)
    Seed                          one verification seed: the build in the battery's mode, the idle counterfactual,
                                  the reference re-run with the state of every named thing before and after each
                                  command, fork snapshots, the look-alike groups and sibling sets at B
    build_seed(spec, seed, ref, mode, extras, snapshots=False, root=None) -> Seed
    entity_ids(value, state)      the entity ids a command argument names
    named_ids(entry, state)       the ids a trace record names (target, destination, arguments)
    goal_paths(task)              {label: predicate} of the success items; footprints per label
    deposits(seed)                the DEPOSIT commands of the trace with the object they move
    conditioned_waits(seed)       the observation-conditioned waits of the trace with their observed durations

The re-run is the verifier's own reference (verify.reference_run's plan, bindings and mode) run once more in the
battery's process: its trace must equal the trace the verifier recorded (a different one is a harness error: the
reference does not replay), and its idle rollout's digest must equal the verifier's.
"""
import copy
import json
import os
import shutil
import tempfile
import time

from ...kernel.errors import SpecError
from ...kernel.predicates import digest
from .. import verify

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "verify")
ACTIONS_PATH = os.path.join(DATA, "actions.json")
INVERSES_PATH = os.path.join(DATA, "inverses.json")
CLASSES = ("PERCEPTION", "NAVIGATION", "COMM", "DEPOSIT", "WAIT", "EFFECT")
DOOR_WORDS = ("door", "gate", "hatch", "doorway", "screen_door", "storm_door", "pet_door")
FORK_MIN_CPU_S = 0.6          # a fork pays when the reference prefix costs more than restoring a snapshot (22.1)
FORK_ENV = "HOUSE_SIM_FORK_CACHE"
# fields an inverse never restores by itself (where a thing is is the replace form's business; bookkeeping)
PLACEMENT_SKIP = ("location", "relation", "shelf", "spos", "pos", "cell", "held_hand", "facing", "facing_deg",
                  "orientation", "last_moved_s", "moved_by", "touched", "seen")
_TABLES = {}


def tables():
    """actions.json and inverses.json (read once)."""
    if "actions" not in _TABLES:
        with open(ACTIONS_PATH, encoding="utf-8") as fh:
            _TABLES["actions"] = json.load(fh)
        with open(INVERSES_PATH, encoding="utf-8") as fh:
            _TABLES["inverses"] = json.load(fh)
    return _TABLES


def action_class(action):
    """The class of an action name (actions.json; an unregistered name is EFFECT)."""
    return tables()["actions"]["classes"].get(str(action or ""), "EFFECT")


def content_deposit(action):
    return str(action) in tables()["actions"].get("content_deposits", ())


def _is_door(state, eid):
    record = ((state or {}).get("entities") or {}).get(eid) if isinstance(eid, str) else None
    if record is None:
        return False
    if record.get("connects"):
        return True
    words = [record.get("type"), record.get("category")] + list(record.get("is_a") or ())
    return any(isinstance(w, str) and any(w == d or w.endswith("_" + d) for d in DOOR_WORDS) for w in words)


def command_class(entry, state=None):
    """10.1's class of a trace record: actions.json, except that open / close of a door or gate issued by a route
    skill (approach, go_room, goto) on the way is NAVIGATION, and phase_done is WAIT."""
    action = str(entry.get("action") or "")
    if action == "phase_done":
        return "WAIT"
    cls = action_class(action)
    if action in ("open", "close", "unlock") and entry.get("skill") in tables()["actions"].get("route_skills", ()) \
            and (state is None or _is_door(state, entry.get("target"))):
        return "NAVIGATION"
    return cls


def effectful(entry, state=None):
    return bool(entry.get("ok")) and command_class(entry, state) in ("EFFECT", "DEPOSIT")


def conditioned(entry):
    return bool((entry.get("wait") or {}).get("conditioned"))


def replay_step(entry):
    """A trace record as a concrete plan step (wrong-plan format)."""
    if entry.get("action") == "phase_done":
        return {"phase_done": True}
    step = {"do": entry["action"]}
    if entry.get("target") not in ("", None):
        step["target"] = copy.deepcopy(entry["target"])
    if entry.get("destination") not in ("", None):
        step["destination"] = copy.deepcopy(entry["destination"])
    for key, value in sorted((entry.get("args") or {}).items()):
        step[key] = copy.deepcopy(value)
    return step


def entity_ids(value, state):
    """The entity ids (and people / pets / rooms) a command argument names."""
    found = []
    entities = (state or {}).get("entities") or {}
    people = (state or {}).get("people") or {}
    pets = (state or {}).get("pets") or {}
    rooms = (state or {}).get("rooms") or {}

    def walk(v):
        if isinstance(v, str):
            if v in entities or v in people or v in pets or v in rooms:
                found.append(v)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
    walk(value)
    return found


def named_ids(entry, state):
    out = []
    for value in [entry.get("target"), entry.get("destination")] + [v for _, v in sorted(
            (entry.get("args") or {}).items()) if _ != "avoid"]:
        for eid in entity_ids(value, state):
            if eid not in out:
                out.append(eid)
    return out


def _robot_view(state):
    robot = state.get("robot") or {}
    return {"room": robot.get("room"), "hands": copy.deepcopy(robot.get("hands") or {}),
            "posture": robot.get("posture"), "pos": copy.deepcopy(robot.get("pos"))}


def placement_of(record):
    """The placement fields a put needs to restore a thing (location, relation, shelf, spos, cell)."""
    out = {}
    for key in ("location", "relation", "shelf", "spos", "cell", "orientation"):
        if record.get(key) is not None:
            out[key] = copy.deepcopy(record[key])
    return out


class Seed(object):
    """One seed's material for generation and execution (module docstring)."""

    def __init__(self, seed, ref):
        self.seed = seed
        self.ref = ref or {}
        self.variant = self.ref.get("variant")
        self.branch = self.ref.get("branch")
        self.bindings = dict(self.ref.get("bindings") or {})
        self.answers = copy.deepcopy(self.ref.get("answers") or {})
        self.trace = list(self.ref.get("trace") or ())
        self.tops = list(self.ref.get("tops") or ())
        self.saves = list(self.ref.get("saves") or ())
        self.waits = list(self.ref.get("waits") or ())
        self.record = self.ref.get("reference") or {}
        self.config = self.task = self.oracle = None
        self.idle = None
        self.idle_rec = None
        self.baseline = None
        self.final_state = None         # state D of the re-run reference (before finish)
        self.pre, self.post, self.robot_pre = {}, {}, {}
        self.snapshots = {}             # top index -> path (before that top); "end" -> after the last top
        self.prefix_cpu = {}            # top index -> CPU-s of the reference before that top
        self.cpu_s = 0.0
        self.errors = []                # harness errors found while preparing (G7)
        self.fork_dir = None
        self.initial_unmet, self.initial_success = [], False
        self.events = []                # the setup's scheduled event times (at_s)
        self.timing = {}                # timing label -> {"t_trigger", "labels"} at the reference's D
        self.plan = {}                  # what accept.py reads: footprints, rules, said labels, state labels
        self.seen = None                # entities the robot perceived in the reference (None: oracle, all)
        self.carried = {}               # trace index of a DEPOSIT -> the mess kinds its target carried just before
        self.groups = {}                # eid -> sorted look-alike group members at B (itself included)
        self.siblings = {}              # eid -> sorted sibling set at B (itself included)

    # ----- convenience ------------------------------------------------------------------------------------
    @property
    def entities(self):
        return (self.baseline or {}).get("entities") or {}

    def record_at(self, i, eid, when="pre"):
        table = self.pre if when == "pre" else self.post
        return (table.get(i) or {}).get(eid)

    def effectful_indices(self):
        return [e["i"] for e in self.trace if effectful(e, self.baseline)]

    def time_budget(self):
        return float((self.task or {}).get("time_budget_s") or 0.0)

    def step_budget(self):
        return int((self.task or {}).get("step_budget") or 0)

    def cleanup(self):
        if self.fork_dir and os.path.isdir(self.fork_dir):
            shutil.rmtree(self.fork_dir, ignore_errors=True)
        self.fork_dir = None
        self.snapshots = {}


class RecordingRunner(verify.Runner):
    """The reference runner of the battery's re-run: the records of every thing a command names (and the robot's
    hands) before and after each command."""

    def __init__(self, env, bindings, seed_ctx, **kwargs):
        verify.Runner.__init__(self, env, bindings, **kwargs)
        self._ctx = seed_ctx

    def do(self, action, expect=None, **args):
        i = len(self.trace) if self.trace is not None else None
        probe = {"action": action, "target": args.get("target", ""), "destination": args.get("destination", ""),
                 "args": {k: v for k, v in args.items() if k not in ("target", "destination")}}
        state = self.state
        ids = named_ids(probe, state)
        before = {eid: copy.deepcopy((state.get("entities") or {}).get(eid)) for eid in ids
                  if eid in (state.get("entities") or {})}
        robot = _robot_view(state)
        carried = carried_kinds(state, probe["target"]) if action_class(action) == "DEPOSIT" else None
        try:
            return verify.Runner.do(self, action, expect=expect, **args)
        finally:
            if i is not None and self.trace is not None and len(self.trace) == i + 1:
                after_state = self.state
                self._ctx.pre[i] = before
                self._ctx.robot_pre[i] = robot
                self._ctx.post[i] = {eid: copy.deepcopy((after_state.get("entities") or {}).get(eid))
                                     for eid in ids}
                if carried:
                    self._ctx.carried[i] = carried


def trace_rows(trace):
    """The comparable part of a trace (what was sent and what happened): the re-run must reproduce it."""
    return [[e.get("action"), e.get("target"), e.get("destination"), e.get("args"), bool(e.get("ok")),
             e.get("top")] for e in trace or ()]


def fork_root(root=None):
    """Where fork snapshots live while a check runs (10.5): root, else HOUSE_SIM_FORK_CACHE, else the system's
    temporary folder (never the repository; deleted after the check)."""
    base = root or os.environ.get(FORK_ENV) or None
    if base:
        os.makedirs(base, exist_ok=True)
    return tempfile.mkdtemp(prefix="house_fork_", dir=base)


def build_seed(spec, seed, ref, mode, extras=None, snapshots=False, root=None):
    """Prepare one seed (module docstring). snapshots: save fork snapshots before the top-level steps whose prefix
    costs more than FORK_MIN_CPU_S and after the last one."""
    from ..runner import build
    ctx = Seed(seed, ref)
    t0 = time.process_time()
    perception, camera = verify.mode_settings(mode)
    look = float(ctx.record.get("lookahead_s") or 0.0)
    try:
        config, task, oracle = build(spec, seed, camera=camera, perception=perception,
                                     hardening={"lookahead_s": look})
    except SpecError as exc:
        ctx.errors.append("seed {}: the build failed ({})".format(seed, exc))
        return ctx
    task["hardening"].update(copy.deepcopy(extras or {}))
    ctx.config, ctx.task, ctx.oracle = config, task, oracle
    if not ctx.bindings:
        ctx.bindings = dict(oracle["bindings"])
    if not ctx.answers:
        ctx.answers = copy.deepcopy(oracle.get("answers") or {})
    ctx.variant = oracle.get("variant") if ctx.variant is None else ctx.variant
    ctx.branch = oracle.get("branch") if ctx.branch is None else ctx.branch
    want = ctx.ref.get("idle_digest")
    if isinstance(ctx.ref.get("idle"), dict):
        # the verifier's own idle counterfactual (mode_trials with capture): no second rollout
        from ...kernel import counterfactual
        ctx.idle = counterfactual.Projections.from_dict(ctx.ref["idle"])
        ctx.idle_rec = {"digest": ctx.idle.digest(), "reused": True}
    else:
        idle_env = verify._hard_env(config, task, seed, mode, record=False)
        ctx.idle, ctx.idle_rec = verify.idle_rollout(idle_env, task)
    if want and ctx.idle_rec.get("digest") != want:
        ctx.errors.append("seed {}: the idle rollout does not reproduce the verifier's (digest {} != {})".format(
            seed, str(ctx.idle_rec.get("digest"))[:12], str(want)[:12]))
    env = verify._hard_env(config, task, seed, mode, idle=ctx.idle)
    ctx.baseline = copy.deepcopy(env._baseline)
    try:
        initial = env.evaluate()
        ctx.initial_unmet = list(initial.get("unmet") or ())
        ctx.initial_success = bool(initial.get("success"))
    except Exception as exc:                # noqa: BLE001  (a task that cannot be evaluated at B: G7)
        ctx.errors.append("seed {}: evaluate at B raised {}: {}".format(seed, type(exc).__name__, str(exc)[:120]))
    ctx.events = sorted(float(e["at_s"]) for e in config.get("events") or () if isinstance(e, dict) and
                        isinstance(e.get("at_s"), (int, float)) and not isinstance(e.get("at_s"), bool))
    if snapshots:
        ctx.fork_dir = fork_root(root)
    started = time.process_time()

    def snap(k, runner):
        spent = time.process_time() - started
        ctx.prefix_cpu[k] = round(spent, 4)
        if ctx.fork_dir and spent >= FORK_MIN_CPU_S:
            path = os.path.join(ctx.fork_dir, "top{}.json".format(k))
            runner.env.save(path)
            ctx.snapshots[k] = path
    runner = RecordingRunner(env, oracle["bindings"], ctx, strict=True, snapshot=snap)
    try:
        runner.run_top(oracle["reference"])
    except Exception as exc:                # noqa: BLE001  (the verifier's reference passed: a harness error)
        ctx.errors.append("seed {}: the reference re-run raised {}: {}".format(seed, type(exc).__name__,
                                                                              str(exc)[:160]))
    ctx.prefix_cpu["end"] = round(time.process_time() - started, 4)
    if ctx.fork_dir and ctx.prefix_cpu["end"] >= FORK_MIN_CPU_S:
        path = os.path.join(ctx.fork_dir, "end.json")
        env.save(path)
        ctx.snapshots["end"] = path
    if ctx.trace and trace_rows(runner.trace) != trace_rows(ctx.trace):
        ctx.errors.append("seed {}: the reference re-run's trace differs from the verifier's (the reference does "
                          "not replay deterministically; digest {} != {})".format(
                              seed, digest(trace_rows(runner.trace))[:12], digest(trace_rows(ctx.trace))[:12]))
    if not ctx.trace:
        ctx.trace, ctx.tops, ctx.saves, ctx.waits = runner.trace, runner.tops, runner.saves, runner.waits
    ctx.final_state = copy.deepcopy(env.world.s)
    monitor = getattr(env, "_monitor", None)
    if monitor is not None and task.get("timing"):
        try:
            ctx.timing = {label: {"t_trigger": v.get("t_trigger"), "labels": list(v.get("labels") or ())}
                          for label, v in (monitor.timing_verdicts(env.world.s) or {}).items() if isinstance(v, dict)}
        except Exception as exc:            # noqa: BLE001
            ctx.errors.append("seed {}: timing verdicts raised {}: {}".format(seed, type(exc).__name__,
                                                                            str(exc)[:120]))
    ctx.groups, ctx.siblings = lookalikes(env, ctx.baseline)
    ctx.seen = seen_ids(env)
    ctx.cpu_s = round(time.process_time() - t0, 4)
    return ctx


def seen_ids(env):
    """The entities the robot perceived during the reference (realistic runs: the evidence index's views and
    fields), None in oracle mode (everything is known)."""
    if getattr(env, "realism", None) is None:
        return None
    try:
        from .. import perceive
        idx = perceive.index(env)
    except Exception:                       # noqa: BLE001  (no index: nothing is known to be seen)
        return set()
    out = set(getattr(idx, "views", {}) or {})
    out |= {eid for (eid, _field) in (getattr(idx, "fields", {}) or {})}
    return out


def lookalikes(env, baseline):
    """({eid: look-alike group members}, {eid: sibling set}) at B: the realism group of every portable thing (its
    looks_like prop, else the registry's groups; realistic runs only, else explicit looks_like props), and the other
    entities of the same type under the same parent (else in the same root room)."""
    from ...kernel.predicates import root_room
    entities = baseline.get("entities") or {}
    realism = getattr(env, "realism", None)
    by_group, by_parent, by_room = {}, {}, {}
    for eid in sorted(entities):
        record = entities[eid]
        if not isinstance(record, dict) or record.get("connects"):
            continue
        name = None
        if realism is not None:
            try:
                name, _ = realism.group_of(record)
            except Exception:               # noqa: BLE001  (a record realism cannot group has no group)
                name = None
        elif isinstance(record.get("looks_like"), str):
            name = record["looks_like"]
        if name:
            by_group.setdefault(name, []).append(eid)
        kind = record.get("type")
        parent = record.get("location")
        by_parent.setdefault((kind, parent), []).append(eid)
        try:
            room = root_room(baseline, eid)
        except Exception:                   # noqa: BLE001
            room = None
        by_room.setdefault((kind, room), []).append(eid)
    groups = {}
    for members in by_group.values():
        if len(members) >= 2:
            for eid in members:
                groups[eid] = list(members)
    siblings = {}
    for (kind, parent), members in by_parent.items():
        for eid in members:
            siblings[eid] = list(members)
    for (kind, room), members in by_room.items():
        for eid in members:
            if len(siblings.get(eid) or ()) < 2:
                siblings[eid] = list(members)
    return groups, siblings


def goal_items(task):
    """[(label, predicate)] of the task's success items (labels as finish names them)."""
    from ...kernel.evaluation import labelled, recorded_labels
    return labelled(task.get("success"), recorded_labels(task, "success"))


def failure_items(task):
    from ...kernel.evaluation import labelled, recorded_labels
    return labelled(task.get("failure"), recorded_labels(task, "failure"), prefix="failure_")


def footprints(task):
    """{"goal:<l>": Footprint, "fail:<l>": Footprint} of the task's labelled predicates (monitor.footprint)."""
    from ...kernel import monitor
    out = {}
    for label, rule in goal_items(task):
        out["goal:" + label] = monitor.footprint(rule)
        out["settle:" + label] = out["goal:" + label]
    for label, rule in failure_items(task):
        out["fail:" + label] = monitor.footprint(rule)
    return out


def success_ids(task):
    """Entity ids a success or failure predicate names directly (footprint ids and field ids)."""
    out = set()
    for fp in footprints(task).values():
        out |= set(fp.ids)
        out |= {f[0] for f in fp.fields if f[0]}
    return out


def carried_kinds(state, eid):
    """The registered mess kinds (plugins.MESS_KINDS, the domains' measures, 6.5) with a parcel carried by entity eid
    in this state: what a dustpan, a vacuum bin, a mop bucket or a refuse item holds."""
    from ...kernel.plugins import MESS_KINDS
    if not isinstance(eid, str) or not eid:
        return []
    out = []
    for name, kind in sorted(MESS_KINDS.items()):
        try:
            parcels = kind.measure(state) or ()
        except Exception:                   # noqa: BLE001  (a measure that fails here names no carrier)
            continue
        for key, _origin, amount in parcels:
            if tuple(key or ()) == ("entity", eid) and float(amount or 0.0) > 0.0:
                out.append(name)
                break
    return out


def mess_carrier(record):
    """Whether a thing carries mess (a dustpan's load, a mop bucket's dirty water, a waste item)."""
    if not isinstance(record, dict):
        return False
    if record.get("mess_kind") or record.get("waste"):
        return True
    for key in ("contents", "load", "debris", "dirt"):
        value = record.get(key)
        if value not in (None, "", [], {}, 0, 0.0, False):
            return True
    return bool(record.get("dirty")) and bool(record.get("liquid_l"))


def deposits(ctx):
    """[(trace index, object id, destination)] of the trace's succeeded DEPOSIT commands, in order."""
    out = []
    for entry in ctx.trace:
        if not entry.get("ok") or command_class(entry, ctx.baseline) != "DEPOSIT":
            continue
        target = entry.get("target")
        if not isinstance(target, str) or not target:
            continue
        out.append((entry["i"], target, entry.get("destination")))
    return out


def pre_pickup(ctx, i, obj):
    """The placement obj had before the robot last picked it up ahead of trace index i (else just before i)."""
    for entry in reversed(ctx.trace[:i]):
        if entry.get("ok") and entry.get("action") in ("pick_up", "receive", "unload", "extract", "uninstall",
                                                       "take_off_hanger", "unstack", "unpack") and \
                entry.get("target") == obj:
            rec = ctx.record_at(entry["i"], obj, "pre")
            if rec:
                return placement_of(rec)
        if entry.get("ok") and entry.get("action") in ("place", "insert", "load", "pack", "stack", "throw",
                                                       "hang_dry", "hang_on_hanger", "hand_over") and \
                entry.get("target") == obj:
            break
    rec = ctx.record_at(i, obj, "pre") or (ctx.entities.get(obj) or {})
    return placement_of(rec)


def conditioned_waits(ctx):
    """[{"ordinal", "top", "kind", "waited_s"}] of the reference's observation-conditioned waits (Runner.waits)."""
    return [dict(w) for w in ctx.waits or ()]


def top_of(ctx, i):
    return ctx.trace[i].get("top") if 0 <= i < len(ctx.trace) else None


def work_room(ctx):
    """The robot's room at the first effectful command (else the start room)."""
    for entry in ctx.trace:
        if effectful(entry, ctx.baseline) and entry.get("room"):
            return entry["room"]
    return ((ctx.baseline or {}).get("robot") or {}).get("room")
