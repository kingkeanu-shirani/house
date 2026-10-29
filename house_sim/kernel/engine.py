"""HouseEnv: reset/step/observe/evaluate, simulated time, events, undo, persistence, replay.

The agent interface is env.step(action, target="", destination="", **params). Every
action is a registered ActionSpec with validated parameters, guard hooks and effect
hooks. Failed actions are atomic (journal rollback) but still consume a step and one
simulated second, during which exogenous processes continue. Task goals are read
only by the evaluator; no action can mark a task complete or set arbitrary fields.

The robot body (kernel v3.0, kernel/body.py) pays for every leaf action once it succeeded
(body.settle_energy: activity power over the action's own seconds plus the walking work of
its motion record); idle time (wait, a refused step's second) is integrated by the body
system. While the robot is powered down every action except wait is refused (PowerOff). A
power-down partway through a step is a consequence, not a refusal: the step commits what
happened, ok=True, "Powered down partway: ..." (TIER1_INTEGRATION 1.9).

Time (kernel v3.1, TIME-EVAL; TIER1_SWEEP 6, 8, 9, 10.1; TIER1_INTEGRATION 1.8-1.9): every 30 s substep runs the
systems (skipping those whose idle probe says they would change nothing), events, the physics settle, the standing
orders, the kernel's notices, a monitor sample and the phase controller; a wait with until / interrupt_on ends at the
first boundary where its condition holds or a notice it listens for arrived (the step lasts the time waited); auto
standing orders run their commands at idle boundaries (a wait, a phase gap), each under a World savepoint and settled
as an action. The temporal monitor (kernel/monitor.py) samples after the action's settlement, after every substep,
after every auto command and at the end of each step (after the Mind's update), so milestones record their true time.
phase_done() ends the active phase of a multi-phase task, runs the gap and starts the next phase (kernel/phases.py).

Verification hardening (VERIFY_HARDENING 6.2, 8.1, 10.5; WP2):

    env.writes              the episode's last-writer log (world.WriterLog): writes[(rid, field)] -> (class, name, t)
                            with class robot (an action phase and its own settle, an auto standing-order command, a
                            substep physics consequence the kernel attributes to the robot), physical (every system
                            tagged physical, the substep settle pass, the kernel's own bookkeeping), volition (systems
                            tagged volition: people and pets; scheduled events; phase starts; reactions to the robot's
                            sounds) or setup (after the baseline: the first request; writes outside the engine's frames).
                            Outside world state: env.save stores it, a replay rebuilds it, no agent observes it
    env.robot_placement(e)  P_R(e): e's placement fields after the robot's last placement change, or None
    env.robot_attributable(rid, field, t, causes=None, unknown=None)
                            a physical change at t linked to the robot: True when the robot wrote rid (or a container
                            of it) by t or the cause chain (<= 5 physical links) reaches a standing robot write, False
                            when its recorded causes rule the robot out, `unknown` (None) when the write reports no
                            cause (6.2's conservative default: charged when it diverges from idle)
    env.finish(settle_s=None, lookahead_s=None, process=None) -> evaluation
                            how a verification run or harness episode ends (8.1): goal at the declared-done state D,
                            an idle settle window sampled every 30 s (settle:<label> on a robot-attributable
                            regression, "consumed" for the rest), the evaluation on the settled state F, and an
                            optional idle look-ahead whose new harms are later:<label> (6.10). Not a step; recorded
                            in the history, so a replay repeats it
    env.fast_forward(seconds, tick=300)
                            a plain wait on a coarse tick (10.5): substeps of `tick` seconds while nothing needs fine
                            time (no event, standing order or phase due, no person or pet changing room or active in
                            the robot's room, no physics consequence or harm flag in the last substep), fine 30 s
                            substeps otherwise. A step like the wait it replaces; recorded, so a replay repeats it
    env.idle                the idle counterfactual (counterfactual.Projections) the ledger of a hardened task compares
                            with (set by the verifier or the harness). env.save keeps its digest, not the projections;
                            HouseEnv.load(path, idle=...) attaches it after checking the digest, and a fork loaded
                            without it raises IdleMissing when evaluation reads it (10.5: a fork judges like the
                            uninterrupted run)
"""
import copy
import json
import os
import random
from collections.abc import MutableMapping
from pathlib import Path

from . import body, evaluation, physics, timeline  # noqa: F401  (physics registers shared systems; timeline the time verbs)
from . import camera as camera_mod      # the camera runtime: its system, after-hooks and hooks; begin/end of step
from . import mind as mind_mod
from . import monitor as monitor_mod
from . import phases as phases_mod
from . import plugins as plugins_mod
from . import replays as replays_mod
from .actions import NOVEL_OPERATE_MESSAGE, Ctx, motion_records
from .errors import ActionError
from .phys import settle
from .perception import Perception, current_request
from .plugins import (ACTIONS, AFTER, BEFORE, IDLE_PROBES, IDLE_TAKES_TICK, SYSTEM_IDLE, SYSTEM_PERIODS, SYSTEMS,
                      load_domains, run_hooks)
from .predicates import check, digest, validate_predicate
from .registry import default_registry
from .schema import normalize, validate
from .world import EXISTS, KEYED_KINDS, PLACEMENT_FIELDS, World, WriterLog, clone, is_bookkeeping

body.register_plugins()                 # the robot body's system, invariant and event effects (TIER1_INTEGRATION 1.16)

STRICT = os.environ.get("HOUSE_SIM_STRICT") == "1"
# HOUSE_SIM_SAFE=1 (for evaluating external agents): a defect inside a handler, hook or system becomes a refused
# step (category "internal", stat internal_errors, which fails the task) instead of an exception. Off by default so
# development, tests, the verifier and tools/fuzz.py see defects.
SAFE = os.environ.get("HOUSE_SIM_SAFE") == "1" and not STRICT
RUNTIME_DEFAULTS = {"steps": 0, "time_s": 0.0}
SENSORS = {"symbolic", "language", "audio", "olfactory", "thermal", "proprioception"}
SUBSTEP_S = 30.0
CLOCK_DIGITS = 3                    # time_s is kept to the millisecond (_substep rounds it)

# writers of the engine's own windows (WP2; the systems' come from plugins.SYSTEM_WRITERS, default physical)
W_SETUP = ("setup", "reset")
W_KERNEL = ("physical", "kernel")
W_SETTLE = ("physical", "physics.settle")
W_SETTLE_ROBOT = ("robot", "physics.settle")
W_PERCEPTION = ("physical", "kernel.perception")
W_PHASE = ("volition", "phase")
W_SOUND = ("volition", "robot_sound")
FINISH_TICK_BASE = 2000000          # substep index offset of finish's settle and look-ahead (their own RNG streams)
SETTLE_MAX_S = 600.0
LOOKAHEAD_MAX_S = 86400.0
CAUSE_LINKS = 5                     # 6.2: a cause chain is followed back through at most 5 physical writes
FF_QUIET_SUBSTEPS = 2               # fast_forward: fine substeps without activity before a coarse one
# sections whose robot writes are the step's own bookkeeping (a standing order's trail, counters, logs): never a
# robot-attributable regression in the settle window
BOOKKEEPING_SECTIONS = frozenset(("action_log", "event_log", "order_log", "stats", "logs", "_sched", "notices",
                                  "camera_log", "orders", "pending"))
_WRITERS = {}


def system_writer(name):
    """The (class, name) writer of a system's writes: plugins.SYSTEM_WRITERS (WP1's @system(..., writer=)), default
    physical. Interned per (class, name), so the World's segment check is an identity test."""
    table = getattr(plugins_mod, "SYSTEM_WRITERS", None)
    cls = table.get(name, "physical") if table else "physical"
    key = (cls, name)
    writer = _WRITERS.get(key)
    if writer is None:
        writer = _WRITERS[key] = key
    return writer


# the entity fields a selector key reads (predicates.select)
SELECTOR_FIELDS = {"type": ("type", "is_a"), "category": ("category",), "label": ("label",), "tag": ("tags",),
                   "tags_any": ("tags",), "within": ("location",), "room": ("location",)}


def predicate_fields(rule):
    """The entity fields a predicate reads: ({entity id: set of fields, or None for the whole record} of its direct
    paths, set of fields its quantifiers and aggregates read on the entities they select). HouseEnv.finish narrows a
    label's footprint (monitor.footprint: which records) to these fields (which writes)."""
    direct, quant = {}, set()

    def absolute(path):
        parts = path.split(".")
        if parts[0] != "entities" or len(parts) < 2:
            return
        if len(parts) == 2 or direct.get(parts[1], set()) is None:
            direct[parts[1]] = None
        else:
            direct.setdefault(parts[1], set()).add(parts[2])

    def relative(sub):
        if not isinstance(sub, dict):
            return
        for key in ("all", "any"):
            for child in sub.get(key) or []:
                relative(child)
        if "not" in sub:
            relative(sub["not"])
        path = sub.get("path")
        if isinstance(path, str):
            if path.startswith("$room"):
                quant.update(("location", "relation"))
            elif not path.startswith("$"):
                quant.add(path.split(".")[0])
        if isinstance(sub.get("ref"), str):
            absolute(sub["ref"])

    def selector(body):
        if body.get("in") not in (None, "entities"):
            return
        for key in body.get("where") or {}:
            if key not in ("ids", "not_ids"):
                quant.update(SELECTOR_FIELDS.get(key, (str(key).split(".")[0],)))
        relative(body.get("check"))
        if isinstance(body.get("field"), str):
            quant.add(body["field"].split(".")[0])

    def walk(sub):
        if not isinstance(sub, dict):
            return
        for key in ("all", "any"):
            for child in sub.get(key) or []:
                walk(child)
        if "not" in sub:
            walk(sub["not"])
        for key in ("forall", "exists", "count", "agg", "ref_agg"):
            if isinstance(sub.get(key), dict):
                selector(sub[key])
        for key in ("path", "ref"):
            if isinstance(sub.get(key), str):
                absolute(sub[key])

    walk(rule)
    return direct, quant


def goal_items(task, phase=None):
    """[(label, test(view) -> bool, footprint rule)] of a task's success conditions (or a phase's): each success
    predicate (a labelled {"label", "pred"} item keeps its label; an unwrapped one the label runner.build recorded
    for its index -- task["labels"]["success"], else hardening.labels.success, else task["success_labels"], as
    evaluation.recorded_labels reads them; else "success_<i>"), the rubric ("rubric") and each required milestone
    ("milestone:<name>"). HouseEnv.finish samples them during the settle window."""
    source = phase if phase is not None else (task or {})
    rules = list(source.get("success") or [])
    names = None
    for where in (source.get("labels"), (source.get("hardening") or {}).get("labels")):
        if isinstance(where, dict) and isinstance(where.get("success"), list):
            names = where["success"]
            break
    if names is None and isinstance(source.get("success_labels"), list):
        names = source["success_labels"]
    items = []
    for i, rule in enumerate(rules):
        label, pred = None, rule
        if isinstance(rule, dict) and "pred" in rule and "label" in rule and len(rule) == 2:
            label, pred = str(rule["label"]), rule["pred"]
        elif isinstance(names, list) and i < len(names) and isinstance(names[i], str):
            label = names[i]
        items.append((label or "success_{}".format(i), (lambda view, p=pred: check(view, p)), pred))
    rubric = source.get("rubric")
    if rubric and rubric.get("items"):
        def rubric_ok(view, rubric=rubric):
            total = sum(float(item.get("weight", 1)) for item in rubric["items"]) or 1.0
            got = sum(float(item.get("weight", 1)) for item in rubric["items"] if check(view, item["pred"]))
            return got / total >= float(rubric.get("threshold", 1.0)) - 1e-9
        items.append(("rubric", rubric_ok, {"all": [item["pred"] for item in rubric["items"]]}))
    if phase is None:
        for name in (task or {}).get("required_milestones") or []:
            items.append(("milestone:" + str(name),
                          (lambda view, n=name: n in ((view.get("$temporal") or {}).get("milestones") or {})),
                          {"path": "$temporal.milestones." + str(name), "op": "exists"}))
    return items


def empty_stats():
    return {"invalid": 0, "safety_blocks": 0, "privacy_blocks": 0, "social_blocks": 0, "authority_blocks": 0,
            "unnecessary_questions": 0, "necessary_questions": 0, "noise": 0.0, "energy": 0.0,
            "damage": 0, "injury": 0, "fire": 0, "flood": 0, "gas_exposure": 0, "burns": 0,
            "privacy_violations": 0, "safety_violations": 0, "authority_violations": 0, "pet_escape": 0,
            "food_poisoning_risk": 0, "electrical_hazard": 0, "handoffs": 0, "undo_count": 0, "collisions": 0,
            "falls": 0, "spills": 0, "breakages": 0}


TIMESTAMP_KEYS = {"time_s", "t_s", "at_s", "since_s", "until_s", "started_s", "start_s", "end_s", "last_s", "heard_s", "seen_s"}
TIMESTAMP_SUFFIXES = ("_time_s", "_since_s", "_at_s", "_until_s", "_started_s")


def whole_seconds(value):
    """A clock reading as the agent sees it in realistic perception: whole seconds (half up), as a float."""
    return float(int(float(value) + 0.5)) if value >= 0 else -float(int(-float(value) + 0.5))


def _round_timestamps(value, depth):
    """Round clock readings (TIMESTAMP_KEYS / TIMESTAMP_SUFFIXES) inside observer channels to whole seconds."""
    if depth > 6:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, float) and isinstance(key, str) and (key in TIMESTAMP_KEYS or key.endswith(TIMESTAMP_SUFFIXES)):
                value[key] = whole_seconds(item)
            elif isinstance(item, (dict, list)):
                _round_timestamps(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                _round_timestamps(item, depth + 1)


def _with_notes(result, notes):
    """Attach handler notes without breaking JSON results."""
    if isinstance(result, dict):
        result = dict(result)
        if notes:
            result["notes"] = [str(n) for n in notes]
        return json.dumps(result, sort_keys=True)
    if not isinstance(result, str):
        result = json.dumps(result, sort_keys=True)
    if not notes:
        return result
    stripped = result.strip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
            data["notes"] = [str(n) for n in notes]
            return json.dumps(data, sort_keys=True)
        except ValueError:
            pass
    return result + " | " + " | ".join(str(n) for n in notes)


def _legacy_map_entry(entry):
    """A Mind map entry in the shape of the pre-v3.1 observation-time known map (exits, connections, label, type)."""
    entry = entry or {}
    return {"exits": list(entry.get("exits") or []), "connections": copy.deepcopy(entry.get("connections") or []),
            "label": entry.get("label"), "type": entry.get("type"), "seen_s": entry.get("seen_s")}


# what an action's effect on the world is (HouseEnv.track_effect): a change to a thing, a room, the robot's body, a
# person, a pet or a connection -- not to an underscore bookkeeping field, a domain's log section, the clock, the
# counters or the robot's energy, which change with any step whatever it did
EFFECT_SECTIONS = ("entities", "rooms", "robot", "people", "pets", "connections")
EFFECT_BOOKKEEPING = ("robot.battery", "robot.energy", "robot.soc", "robot.runtime", "robot.power", "robot.body_log")


def effect_path(path):
    """True when a changed world path is an effect of the action (EFFECT_SECTIONS minus bookkeeping)."""
    parts = path.split(".")
    return parts[0] in EFFECT_SECTIONS and not any(p.startswith("_") for p in parts) and         not path.startswith(EFFECT_BOOKKEEPING)


class KnownMapView(MutableMapping):
    """env._known_map: the compatibility alias of the robot's known map (TIER1_SWEEP 7.1). The map is env.mind.map;
    this read-only view shows it in the pre-v3.1 shape {rid: {"exits", "connections", "label", "type"}} for code that
    still reads env._known_map (navigation_robot's known_rooms until it reads env.mind.map alone, reports' known
    subjects). The robot's current room is always present (the observation-time table always held it). Writes and
    deletions are ignored: the Mind is updated by the engine from what the robot perceives, never by its readers
    (perception's and realism's observation-time writes of the old table are dropped here). Copies are plain dicts."""

    __slots__ = ("_env",)

    def __init__(self, env):
        self._env = env

    def _tables(self):
        mind = getattr(self._env, "mind", None)
        table = mind.map if mind is not None and isinstance(getattr(mind, "map", None), dict) else {}
        world = getattr(self._env, "world", None)
        state = world.s if world is not None else {}
        rid = (state.get("robot") or {}).get("room")
        current = rid if rid in (state.get("rooms") or {}) and rid not in table else None
        return table, state, current

    def __getitem__(self, rid):
        table, state, current = self._tables()
        if rid in table:
            return _legacy_map_entry(table[rid])
        if rid is not None and rid == current:
            return _legacy_map_entry(mind_mod.map_entry(state, rid, state.get("time_s")))
        raise KeyError(rid)

    def __setitem__(self, rid, value):
        pass

    def __delitem__(self, rid):
        pass

    # the mutating helpers of MutableMapping, made consistent with a read-only view (clear() would otherwise loop:
    # popitem deletes nothing here)
    def clear(self):
        pass

    def update(self, *args, **kwargs):
        pass

    def pop(self, rid, *default):
        return self[rid] if rid in self or not default else default[0]

    def popitem(self):
        raise KeyError("the known map is a read-only view of env.mind.map")

    def setdefault(self, rid, default=None):
        return self[rid] if rid in self else default

    def __iter__(self):
        table, _, current = self._tables()
        return iter(sorted(list(table) + ([current] if current is not None else [])))

    def __len__(self):
        table, _, current = self._tables()
        return len(table) + (1 if current is not None else 0)

    def __contains__(self, rid):
        table, _, current = self._tables()
        return rid in table or (rid is not None and rid == current)

    def snapshot(self):
        return {rid: self[rid] for rid in self}

    def __copy__(self):
        return self.snapshot()

    def __deepcopy__(self, memo):
        return self.snapshot()

    def __reduce__(self):
        return (dict, (self.snapshot(),))

    def __repr__(self):
        return "KnownMapView({!r})".format(sorted(self))


def _never(field):
    return False


class IdleMissing(RuntimeError):
    """env.idle read on an env loaded from a snapshot saved with an idle counterfactual that was not attached again
    (HouseEnv.load(path, idle=...)). Not an AttributeError, so getattr(env, "idle", None) does not hide it."""


def idle_record(idle):
    """What a snapshot keeps of an idle counterfactual: {"digest", "checkpoints"} (counterfactual.Projections.digest
    over the checkpoints; None for an object without one, which any attached counterfactual then matches)."""
    fn = getattr(idle, "digest", None)
    try:
        count = len(idle)
    except TypeError:
        count = None
    return {"digest": fn() if callable(fn) else None, "checkpoints": count}


def _lookahead_guards():
    """The ledger guards the look-ahead re-checks (6.10): WP1's ledger.LOOKAHEAD_GUARDS (landed: read directly, one
    list for the ledger, the lint and finish; P2-S2 merge gate)."""
    from . import ledger
    return tuple(ledger.LOOKAHEAD_GUARDS)


def _idle(probe, name, w, tick):
    """A system's idle probe: fn(w), or fn(w, tick) for a probe that registered with the tick (IDLE_TAKES_TICK)."""
    return probe(w, tick) if name in IDLE_TAKES_TICK else probe(w)


class Tick:
    """Per-substep context for systems: deterministic RNG and env access."""

    def __init__(self, env, index):
        self.env, self.index = env, index
        self.step = env.world.s.get("steps", 0)

    def rng(self, name):
        return random.Random("{}:{}:{}:{}".format(self.env.seed, self.step, self.index, name))


class HouseEnv:
    def __init__(self, config=None, task=None, seed=0, sensors=None, action_file=None, registry=None,
                 trace=True, observe_on_step=True, perception=None, camera=None):
        """perception: "oracle" (exact records of everything visible; the verification default) or "realistic"
        (opaque handles, field tiers, knowledge levels, noise and look-alikes; see kernel/realism.py). Defaults to
        the task's "perception" field, else "oracle".

        camera: "room" (room-wide visibility, the default) or "geometric" (the robot's posed cameras: line of sight,
        light and resolution; kernel/camera.py). Precedence (TIER1_INTEGRATION 1.13): this argument (the CLI passes
        --camera here), then the task's "camera", then camera.default_mode(perception)."""
        load_domains()
        self.registry = registry or default_registry()
        if config is None:
            from ..generator import generate_house
            config = generate_house(seed=seed)
        self._initial = copy.deepcopy(config)
        normalize(self._initial, self.registry)  # validate before accepting
        self._task = copy.deepcopy(task)
        if task:
            for key in ("success", "failure"):
                for rule in task.get(key, []):
                    validate_predicate(rule)
            for phase in task.get("phases") or []:
                for key in ("success", "failure"):
                    for rule in phase.get(key) or []:
                        validate_predicate(rule)
            # milestones (plain and extended), never / always / responses, deadlines (kernel/monitor.py)
            monitor_mod.validate_task(task, task.get("phases"))
            for item in task.get("rubric", {}).get("items", []):
                validate_predicate(item["pred"])
        self.seed = seed
        self.sensors = list(sensors or ["symbolic", "language", "audio", "olfactory", "thermal", "proprioception"])
        if set(self.sensors) - SENSORS:
            raise NotImplementedError("Only symbolic channels exist ({}); raw RGB-D/tactile/audio signals are not simulated".format(", ".join(sorted(SENSORS))))
        self.trace = trace
        self.observe_on_step = observe_on_step
        self.perception = Perception(self)
        mode = perception or (task or {}).get("perception") or "oracle"
        if mode not in ("oracle", "realistic"):
            raise ValueError("perception must be 'oracle' or 'realistic'")
        self.perception_mode = mode
        self.camera_mode = camera_mod.resolve_mode(camera, task, mode)
        self._camera_view = None
        # transient (never saved): "idle" while the body system integrates the robot's idle drain (a wait, a refused
        # step's second), "covered" while the time was already paid by the action's settlement (TIER1_INTEGRATION 1.9)
        self._advance_mode = "covered"
        self._tick_index = 0            # substep index within a step (Tick RNG streams); gaps use their own range
        self._timeline = None           # the notice cursors (timeline.derive_notices); rebuilt from the state
        self._uses_mind = '"mind.' in json.dumps(task or {})     # mind.* paths: evaluate with the Mind's view
        self.realism = None
        self.raw_results = False   # the task runner sets this to also receive untranslated messages
        # tools/fuzz.py sets track_effect: after each successful step, last_step_effect lists the world paths the action
        # and its consequences changed, bookkeeping excepted (EFFECT_BOOKKEEPING; phase-1 red team: actions that
        # "succeeded" without changing anything counted as driven to success). Off by default: it costs a diff
        self.track_effect = False
        self.last_step_effect = None
        # the idle counterfactual (counterfactual.Projections) a hardened task's ledger compares with (VERIFY_HARDENING
        # 6.2): set by the verifier or the harness (WP5 / WP10), read by evaluation through World._env_ref; kept by
        # reset. A snapshot records its digest, not the projections (env.save; the idle property)
        self._idle_pending = None
        self.idle = None
        self.action_file = action_file
        self.reset()

    # ------------------------------------------------------------------
    @property
    def room(self):
        return self.world.s["robot"]["room"]

    @property
    def holding(self):
        held = self.world.held_items()
        return held[0] if held else None

    @property
    def steps(self):
        return self.world.s["steps"]

    def reset(self, preserve_world=False):
        if preserve_world and hasattr(self, "world"):
            state = self.world.s
            state["steps"] = 0
            state["stats"] = empty_stats()
            state["reports"] = {}
            state["facts"] = {}
        else:
            state = normalize(self._initial, self.registry)
            state.update(steps=0, time_s=float(state.get("time_s", 0.0)), stats=empty_stats(), facts={}, reports={},
                         approvals=list(state.get("approvals", [])), memory=copy.deepcopy(state.get("memory", {})),
                         orders=[], pending=[], event_log=[], messages=[], logs={"moves": []})
        # the camera mode is the env's: install or strip the camera state before the episode starts (1.9)
        if self.camera_mode == "geometric":
            camera_mod.install(state)
        else:
            camera_mod.strip(state)
        self._camera_view = None
        self.world = World(state, self.registry)
        if self.perception_mode == "realistic":
            from .realism import Realism
            self.realism = Realism(self)
        if not preserve_world:
            self.world.begin()
            run_hooks("episode_start", self.world, self)
            self.world.commit()
        if self.camera_mode == "geometric":
            self.world.begin()
            camera_mod.note_start(self)         # the reset view's sightings (geometric camera only)
            self.world.commit()
        self._baseline = clone(state)
        # the last-writer log starts at the baseline (WP2): what happened before it is setup, never charged
        self.writes = WriterLog()
        self.world.track(self.writes)
        self._coarse = None             # fast_forward's coarse tick while it runs (else None)
        self._ff = None
        self.last_fast_forward = None
        self._known_map, self._history, self._undo, self._snapshots = {}, [], [], {}
        self._monitor = monitor_mod.Monitor(self._task)         # temporal evaluation (kernel/monitor.py)
        self._milestones = self._monitor.milestones
        self._phases = phases_mod.PhaseController(self._task).bind(self)
        self._tick_index = 0
        self.mind = None
        self._note_view()
        self._start_mind()
        self._post_requests()
        # the notice cursors start at the reset state, the request just posted included (the episode's first request
        # is obs.request, not a notice; a later phase's request is an "addressed" notice)
        timeline.start(self)
        self._replay_start = clone(self.world.s)
        self._last_score = self.evaluate()["score"]
        return self.observe()

    def _start_mind(self):
        """The robot's memory at reset (TIER1_SWEEP 7.1; kernel/mind.py): Mind.from_config (prior knowledge; with
        realistic perception it also issues the handles and grants the knowledge the robot brought into the episode, so
        it runs after the Realism exists), then the first update from what the robot perceives."""
        self.mind = mind_mod.Mind.from_config(self.world.s, self)
        self._update_mind()

    def _update_mind(self):
        """mind.update(env, perception.perceived_ids(env)) (TIER1_INTEGRATION 1.9 rule 5)."""
        if self.mind is not None:
            self.mind.update(self, self.perception.perceived_ids(self))

    def _post_requests(self):
        """Reset (TIER1_SWEEP 10.1): the first phase starts (its request posted), or a single request is posted. Only
        tasks that use phases or a request write anything (requests, phase, the request's speech)."""
        task = self._task or {}
        if not (self._phases.phases or task.get("request")):
            return
        self.world.begin()
        self.world._writer = W_SETUP
        if self._phases.phases:
            self._phases.start_first(self)
        else:
            phases_mod.post_request(self, task["request"], None)
        self.world.commit()

    def _note_view(self):
        """Realistic perception with the geometric camera: what the cameras see at the start of an episode counts like
        a step's view (tracked identity, unidentified people; realism.note_step). No-op otherwise."""
        if self.realism is not None and self.camera_mode == "geometric" and hasattr(self.realism, "note_step"):
            self.realism.note_step(camera_mod.step_view(self))

    def privileged_state(self):
        """Oracle/debug API. Never give this to an evaluated agent."""
        return clone(self.world.s)

    # ----- the idle counterfactual (VERIFY_HARDENING 6.2, 10.5; WP2) -----
    @property
    def idle(self):
        """The idle counterfactual (counterfactual.Projections) a hardened task's ledger compares with, or None.

        An env loaded from a snapshot that was saved with one (HouseEnv.load without idle=) has none until the caller
        attaches it again -- env.idle = projections, checked against the digest the snapshot recorded (a forked run
        must judge against the uninterrupted run's counterfactual, 10.5); reading it before that raises IdleMissing,
        so no evaluation of the fork judges against no counterfactual (counterfactual.diverges: every change)."""
        pending = self._idle_pending
        if pending is not None:
            raise IdleMissing("this env was loaded from a snapshot saved with an idle counterfactual (digest {}, {} "
                              "checkpoints): attach it (HouseEnv.load(path, idle=...) or env.idle = ...) before "
                              "evaluating".format(pending.get("digest"), pending.get("checkpoints")))
        return self._idle

    @idle.setter
    def idle(self, value):
        pending = self._idle_pending
        if pending is not None:
            if value is None:
                raise ValueError("this env's snapshot was saved with an idle counterfactual (digest {}): attach that "
                                 "one, not None".format(pending.get("digest")))
            want = pending.get("digest")
            got = idle_record(value)["digest"]
            if want is not None and got != want:
                raise ValueError("idle counterfactual digest {} differs from the snapshot's {}: a fork must judge "
                                 "against the uninterrupted run's counterfactual".format(got, want))
            self._idle_pending = None
        self._idle = value

    # ----- attribution (VERIFY_HARDENING 6.2; WP2) ---------------------
    def robot_placement(self, eid):
        """P_R(e): the placement fields of eid ({field: value}) after the robot's last placement change, or None
        (the robot never placed it). Placement guards read it when a person or pet moved e afterwards (6.2)."""
        return self.writes.robot_placement(eid)

    def robot_attributable(self, rid, field=None, t=None, causes=None, ignore=is_bookkeeping, unknown=None):
        """Whether a physical change of rid's field (None: of any of its fields; a placement field: of its placement)
        at time t (None: now) is linked to the robot (6.2's causal link, 8.1):

        True      the robot wrote rid itself by t (bookkeeping fields aside), or an entity of its location chain (now,
                  or in its final record for a destroyed one), or the change's cause chain -- `causes`, else the causes
                  logged with the physical write standing at t -- reaches a record one of whose fields the robot was
                  the last to write by then, followed back through at most CAUSE_LINKS physical writes;
        False     not linked: no physical write of the field stands at t, or every branch of its recorded cause chain
                  ends at a volition or setup write, at a record unchanged since the baseline, at a physical write
                  without causes or after CAUSE_LINKS links;
        unknown   (default None: "link unknown") the change is a physical write that reports no cause, so its link to
                  the robot cannot be ruled out. 6.2's conservative default charges it whenever it diverges from idle,
                  so a charging caller treats None as linked (the ledger: charged_field, charged_placement, _destroyed);
                  no domain reports causes yet, so today this is every physical change of a record the robot did not
                  write. finish's settle rule passes unknown=False (8.1: a regression is the robot's only on a record
                  the robot wrote, or through a recorded cause)."""
        log = self.writes
        state = self.world.s
        t = float(state.get("time_s", 0.0)) if t is None else float(t)
        if ignore is None:
            ignore = _never
        if log.robot_wrote(rid, before=t, ignore=ignore):
            return True
        entities = state["entities"]
        record = entities.get(rid)
        if record is None and ":" not in rid:
            record = self.world.gone_record(rid)
        seen = {rid}
        loc = (record or {}).get("location")
        while isinstance(loc, str) and loc not in seen and loc in entities:
            if log.robot_wrote(loc, before=t, ignore=ignore):
                return True
            seen.add(loc)
            loc = entities[loc].get("location")
        # the frontier: (cause id, t0, t1), the cause and the time window of the write that cites it
        blind = False                   # the change is a physical write without a recorded cause
        if causes:
            frontier = [(cause, t, t) for cause in causes]
        else:
            frontier = []
            if field is None:
                fields = [f for f in log.fields(rid) if not ignore(f)]
            elif field in PLACEMENT_FIELDS:
                written = set(log.fields(rid))
                fields = [f for f in PLACEMENT_FIELDS if f in written]
            else:
                fields = [field]
            for f in fields:
                entry = log.entry(rid, f, before=t)
                if entry is None or entry[0] != "physical":
                    continue
                if entry[4]:
                    frontier.extend((cause, entry[2], entry[3]) for cause in entry[4])
                else:
                    blind = True
        visited = {rid}
        for _ in range(CAUSE_LINKS):
            nxt = []
            for cause, t0, t1 in frontier:
                if cause in visited:
                    continue
                visited.add(cause)
                # the cause record reaches the robot when the robot's write of one of its fields stood while the citing
                # write was made (6.2: "whose last writer before the change was the robot") -- a burner the robot
                # switched on whose temperature a physical process keeps writing; its physical writes' own causes are
                # the next links (a volition or setup write, or one without causes, ends the branch)
                for f in log.fields(cause):
                    if ignore(f):
                        continue
                    for cls, _, e0, e1, ids in log.during(cause, f, t0, t1):
                        if cls == "robot":
                            return True
                        if cls == "physical" and ids:
                            nxt.extend((c, e0, min(e1, t1)) for c in ids)      # judged when it was made
            if not nxt:
                break
            frontier = nxt
        return unknown if blind else False

    def known_map(self):
        """The robot's known map (TIER1_SWEEP 7.1): a copy of env.mind.map, {rid: {"label", "type", "exits",
        "connections": [{"id", "to", "kind", "door"}], "seen_s"}} -- the rooms it has been in at the end of a step (or
        at reset) and the rooms prior knowledge gave it, whether or not the agent observed. A tool view (the CLI's map
        command, the viewer); no action or observation gives it to an agent (the robot's own way is recall("map")).
        Realistic perception: a door id shows as the handle the robot was issued for it, else "something"."""
        mind = self.mind
        table = copy.deepcopy(mind.map) if mind is not None and isinstance(getattr(mind, "map", None), dict) else {}
        if self.realism is not None:
            issued = self.realism.handles.by_id
            entities = self.world.s.get("entities", {})
            for entry in table.values():
                for conn in (entry or {}).get("connections") or []:
                    door = conn.get("door")
                    if isinstance(door, str) and door in entities:
                        conn["door"] = issued.get(door) or "something"
        return {rid: table[rid] for rid in sorted(table)}

    @property
    def _known_map(self):
        """Compatibility alias of the known map (a read-only view of env.mind.map in the old table's shape; see
        KnownMapView). Kept until no code reads env._known_map; assignments are ignored."""
        return KnownMapView(self)

    @_known_map.setter
    def _known_map(self, value):
        pass                # the Mind owns the map (reset and load assign the old table's slot: nothing to keep)

    def observe(self):
        if self.realism is not None:
            obs = self.realism.observe()
            self._agent_clock(obs)
            if self.realism.record_observations and self.realism.observed and \
                    isinstance(self.realism.observed[-1], dict) and "visible_entities" in self.realism.observed[-1]:
                self._agent_clock(self.realism.observed[-1])
            return obs
        return self.perception.observe(self)

    def _agent_clock(self, obs):
        """Realistic perception: the clock the agent sees reads whole seconds (time_s, the calendar view derived from
        it, and timestamps in observer channels and messages), so step durations computed from hidden quantities
        (liters, rates, identities) cannot be recovered from exact clock differences. The world keeps the exact
        clock; oracle observations are unchanged."""
        if not isinstance(obs, dict):
            return obs
        true_s = obs.get("time_s")
        if isinstance(true_s, (int, float)) and not isinstance(true_s, bool):
            shown = whole_seconds(true_s)
            obs["time_s"] = shown
            state = self.world.s
            if isinstance(obs.get("time"), dict):
                from . import clock
                obs["time"] = clock.describe({"clock": state.get("clock", {}), "time_s": shown, "weather": state.get("weather", {})})
        for key, value in obs.items():
            if key not in ("time_s", "time", "visible_entities", "grid", "connections", "room"):
                _round_timestamps(value, 0)
        return obs

    # ------------------------------------------------------------------
    def action_names(self):
        """The actions an agent is offered: every registered action, without the camera-control verbs in room mode
        (camera.GEOMETRIC_ONLY; actions_fingerprint() still covers them)."""
        if self.camera_mode == "geometric":
            return set(ACTIONS)
        return set(ACTIONS) - camera_mod.GEOMETRIC_ONLY

    def describe_action(self, name):
        spec = ACTIONS.get(name)
        if spec is None:
            raise KeyError(name)
        info = spec.describe()
        info["guards"] = [label for _, label, _ in BEFORE.get(name, [])]
        info["effects"] = [label for _, label, _ in AFTER.get(name, [])]
        return info

    def perform(self, command, parent_ctx=None):
        """Run one action inside the current step: a nested leaf (undo, scheduled commands, domain chains). The leaf
        takes its own careful-speed time (body.careful_seconds) and pays its own energy (body.settle_energy over its
        own seconds; refused while powered down); the parent gets its time, its nested_s (so the parent neither pays
        for nor slows down those seconds again) and its motion records. Physics settle and the camera's step are the
        enclosing step's (TIER1_INTEGRATION 1.9)."""
        return self._leaf(command, parent_ctx)[0]

    def _refuse_novel_operate(self, spec, args):
        """An unfamiliar thing is not operated (review I4): an action declared operates=True on a target the robot
        takes for an unfamiliar thing is refused with one message for every one (NOVEL_OPERATE_MESSAGE), before any
        domain hook -- in a step and in a nested leaf alike (phase-1 close-out, manipulation's request: perform's
        chains skipped the check, so a nested open answered "It does not open" for a novel knife and "Opened" for a
        novel kettle). Realistic perception only."""
        if self.realism is None or not spec.operates:
            return
        target_id = args.get("target")
        record = self.world.s["entities"].get(target_id) if isinstance(target_id, str) else None
        if record is not None and self.realism.novel_unidentified(target_id, record):
            raise ActionError(NOVEL_OPERATE_MESSAGE, "precondition")

    def _leaf(self, command, parent_ctx=None):
        """perform's body: returns (result, the leaf's Ctx, the seconds it took)."""
        command = dict(command)
        name = command.pop("action")
        spec = ACTIONS.get(name)
        if spec is None:
            raise ActionError("Unknown action: " + str(name), "argument")
        args = spec.validate(command)
        w = self.world
        body.power_guard(w.s, spec)
        body.quiet_guard(w.s, spec)
        ctx = Ctx(self, spec, args, random.Random("{}:{}:nested:{}".format(self.seed, w.s["steps"], name)))
        self._refuse_novel_operate(spec, args)      # as step() does: a domain chain operates no unfamiliar thing either
        for _, _, fn in BEFORE.get(name, []):
            fn(ctx)
        result = spec.fn(ctx)
        for _, _, fn in AFTER.get(name, []):
            fn(ctx, result)
        result = body.motion_result(ctx, result)    # a battery-cut message a domain's stop made false
        body.unrecorded_motion(ctx)     # a domain verb that moved the robot without ctx.motion still walked
        camera_mod.note_action(ctx)     # motion frames of a leaf that moved the robot (geometric mode)
        seconds = spec.duration(ctx) if callable(spec.duration) else float(spec.duration)
        body.quiet_guard(w.s, spec, ctx)
        # a nested leaf at careful speed takes longer for its own seconds too (body.careful_seconds); its parent adds
        # the result as nested time and never multiplies it again
        spent = body.careful_seconds(w.s, spec, seconds + ctx.extra_time, ctx.nested_s)
        body.settle_energy(w, ctx, max(0.0, spent - ctx.nested_s), hold_check=body.HOLD_SAFETY_NET)
        self._robot_sound(ctx)
        if parent_ctx is not None:
            parent_ctx.spend(spent)
            parent_ctx.nested_s += spent
            # no physics settle here: the enclosing step's settle covers nested leaves, and its carry law reads
            # their motion records
            parent_ctx.data.setdefault("motions", []).extend(motion_records(ctx))
        return result, ctx, spent

    def _note_facts(self, action, args, seconds):
        """facts.action_times (when the action on its target ends) and facts.last_action of a step that did something
        (ok, or committed partway)."""
        state = self.world.s
        facts = self.world.sec("facts")
        facts.setdefault("action_times", {}).setdefault(args.get("target") or "robot", {})[action] = state["time_s"] + seconds
        facts["last_action"] = {"action": action, "target": args.get("target", ""), "step": state["steps"]}

    def _robot_sound(self, ctx):
        """run_hooks("robot_sound", w, db, action, room, cell) for an action that declares noise_db (dB at 1 m; a
        callable is evaluated on the action's ctx, None for silence): people and pets hear it (domain follow-ups)."""
        level = ctx.spec.noise_db
        if callable(level):
            level = level(ctx)
        if level is None:
            return
        w = self.world
        robot = w.s["robot"]
        saved = (w._writer, w._wcause)
        w._writer, w._wcause = W_SOUND, None        # people's and pets' reactions are their own (WP2)
        try:
            run_hooks("robot_sound", w, float(level), ctx.spec.name, robot.get("room"), list(robot.get("pos") or []))
        finally:
            w._writer, w._wcause = saved

    def step(self, action, target="", destination="", **kwargs):
        if not all(isinstance(v, str) for v in (action, target, destination)):
            evaluation_now = self.evaluate()
            return {"ok": False, "message": "Action arguments must be strings", "category": "argument",
                    "observation": self.observe() if self.observe_on_step else None, "evaluation": evaluation_now,
                    "reward": 0.0, "terminated": evaluation_now["success"], "truncated": evaluation_now["budget_exceeded"]}
        w = self.world
        state = w.s
        strict_copy = clone(state) if STRICT else None
        if self.realism is not None:
            self.realism.begin_step()
        action = action.strip().lower()
        command = {"action": action, "target": target, "destination": destination}
        command.update(kwargs)
        w.begin()                       # also clears the touch log, the physics scratch and the power-down stamp
        settle.begin_action(w)          # consequences until the action-phase settle ends are the robot's (6.4)
        scheduled = action == "run_pending" and bool(state.get("pending"))
        if scheduled:
            queued = copy.deepcopy(state["pending"][0])
            action = queued.pop("action")
            target, destination = queued.pop("target", ""), queued.pop("destination", "")
            kwargs = queued
        # the robot window (WP2): the action, its hooks and its own settle; the step's bookkeeping stays the robot's
        w._writer, w._wcause = ("robot", action if isinstance(action, str) else "?"), None
        w.set_scalar("steps", state["steps"] + 1)
        camera_mod.begin_step(w)        # this step's frames and seen events start empty (no-op in room mode)
        self._advance_mode = "covered"  # the action pays for its own seconds at settlement
        self._tick_index = 0
        spec = ACTIONS.get(action)
        ok, category, message, seconds = False, "", "", 1.0
        rng = random.Random("{}:{}:{}".format(self.seed, state["steps"], action))
        undo_record = None
        ctx, popped = None, False
        logged_target = target          # the action trail's target: the true id once realistic handles are resolved
        # a command refused for its form -- an unknown action or arguments the action does not take (ActionSpec.
        # validate) -- never reached the action: the verifier does not count such a wrong-plan step as evidence
        # (tasks/verify.py; phase-1 red team). Not part of the step result (the agent reads the message)
        self.last_step_malformed = False
        self.last_step_effect = None
        malformed = True
        try:
            if spec is None:
                raise ActionError("Unknown action: " + action, "argument")
            malformed = False
            raw = {"target": target, "destination": destination}
            raw.update(kwargs)
            if self.realism is not None and not scheduled:
                raw = self.realism.inbound(raw, text_keys=tuple(k for k, v in spec.params.items() if v.get("type") == "text"),
                                           entity_keys=tuple(k for k, v in spec.params.items()
                                                             if v.get("type") in ("entity", "target", "agent", "person", "pet")))
                self.realism.involved = {v for v in raw.values() if isinstance(v, str) and v in state["entities"]}
                logged_target = raw.get("target", target)
            malformed = True
            args = spec.validate(raw)
            malformed = False
            robot = state["robot"]
            if robot.get("stopped") and not spec.stopped_ok:
                raise ActionError("Robot is stopped; resume first", "precondition")
            body.power_guard(state, spec)           # PowerOff unless the action may run powered down (wait)
            body.quiet_guard(state, spec)           # quiet mode: a declared noise_db over the quiet limit
            ctx = Ctx(self, spec, args, rng)
            self._refuse_novel_operate(spec, args)
            before_state = None
            if spec.reversible or spec.inverse:
                before_state = {"robot": clone(state["robot"])}
                target_id = args.get("target")
                if isinstance(target_id, str) and target_id in state["entities"]:
                    before_state["entity"] = clone(state["entities"][target_id])
            for _, _, fn in BEFORE.get(action, []):
                fn(ctx)
            result = spec.fn(ctx)
            for _, _, fn in AFTER.get(action, []):
                fn(ctx, result)
            # a walk the battery would have cut but a domain stopped first (a blocker, a fall): the robot stays on,
            # so the verb's "Battery depleted after X m" message becomes "Walked X m" (B4 merge)
            result = body.motion_result(ctx, result)
            body.unrecorded_motion(ctx)     # a domain verb that moved the robot without ctx.motion still walked
            camera_mod.note_action(ctx)     # motion frames of any action that moved the robot (geometric mode)
            if scheduled:
                w.sec("pending").pop(0)
                popped = True
            if self.realism is not None:
                # realistic perception: the computed parts of a step's duration (a duration function, time a handler
                # added) last whole seconds, so no hidden quantity behind them (mass, liters, identity) can be read off
                # the clock below one second -- not even by offsetting the clock with actions of known fractional
                # duration; declared constant durations stay as they are
                base = spec.duration(ctx) if callable(spec.duration) else float(spec.duration)
                if callable(spec.duration):
                    base = whole_seconds(max(0.0, float(base)))
                seconds = base + whole_seconds(max(0.0, float(ctx.extra_time)))
            else:
                seconds = (spec.duration(ctx) if callable(spec.duration) else float(spec.duration)) + ctx.extra_time
            seconds = max(0.0, seconds)
            # careful speed (TIER1_EMBODIMENT 4.9, M5): only the action's own seconds take longer (its nested leaves
            # took theirs), and not at all for a duration that comes from the walking speed (timing="speed"); in
            # realistic perception the nested part counts in whole seconds, as the step's computed time does
            nested = ctx.nested_s if self.realism is None else whole_seconds(max(0.0, float(ctx.nested_s)))
            seconds = body.careful_seconds(state, spec, seconds, nested)
            body.quiet_guard(state, spec, ctx)      # quiet mode: a noise_db computed from the action (fn(ctx))
            if spec.idle:
                self._advance_mode = "idle"         # wait: the body system integrates the idle drain
            # the robot pays for this leaf (TIER1_INTEGRATION 1.9 rule 1: before the physics settle): its own seconds,
            # the nested leaves having paid theirs; may raise the energy refusal (rolled back like any refusal)
            body.settle_energy(w, ctx, max(0.0, seconds - ctx.nested_s), hold_check=body.HOLD_SAFETY_NET)
            # physical consequences of the action (TIER1_INTEGRATION 1.9): never raises; adds id-only notes
            consequences = settle.settle(self, "action", ctx)
            if self.track_effect:
                self.last_step_effect = sorted({d["path"] for d in w.changes() if effect_path(d["path"])})
            # temporal predicates see the action and its consequences; what it reaches is stamped at its end (an
            # action completes when its time is spent; a wait's own sample is its start)
            self._monitor.sample(self, "action", at=None if spec.idle else state["time_s"] + seconds)
            message = _with_notes(result, ctx.notes)
            if spec.noise:
                w.stat("noise", float(spec.noise))
            self._note_facts(action, args, seconds)
            if spec.irreversible:
                self._undo = []
            elif consequences:
                pass                    # a broken glass cannot be undone: no undo record for this step
            elif w._power_down_step == state["steps"]:
                pass                    # nor a battery run flat: no undo record for a step that powered the robot down
            elif spec.inverse is not None or spec.reversible:
                inverse = spec.inverse(ctx, before_state) if spec.inverse else None
                if inverse:
                    changed = [d for d in w.changes() if d["path"].startswith(("entities.", "robot.room", "robot.hands", "robot.pos", "rooms."))]
                    if changed:
                        undo_record = {"inverse": inverse, "changes": changed}
            self._robot_sound(ctx)
            ok = True
        except ActionError as exc:
            if ctx is not None and w._power_down_step == state["steps"]:
                # the robot powered down partway (a nested leaf ran the pack flat, then the next one was refused):
                # a consequence, not a refusal -- what happened is committed (decision; TIER1_INTEGRATION 1.9)
                if scheduled and not popped and state.get("pending"):
                    w.sec("pending").pop(0)     # the queued command ran (partly): it is not run again
                settle.settle(self, "action", ctx)
                ok, category = True, ""
                # the nested leaves' time (realistic perception: whole seconds, as computed durations are)
                seconds = ctx.extra_time if self.realism is None else whole_seconds(max(0.0, float(ctx.extra_time)))
                self._monitor.sample(self, "action", at=state["time_s"] + seconds)
                message = _with_notes("Powered down partway: " + str(exc), ctx.notes)
                self._note_facts(action, ctx.args, seconds)
            else:
                w.rollback()
                w.begin()
                w.set_scalar("steps", state["steps"] + 1)
                camera_mod.begin_step(w)
                self._advance_mode = "idle"         # the refused step's second is idle time
                category, message, seconds = exc.category, str(exc), 1.0
                self.last_step_malformed = malformed
                w.stat("invalid")
                if category in ("safety", "privacy", "social", "authority"):
                    w.stat(category + "_blocks")
                if isinstance(exc, body.BodyRefusal):
                    exc.apply(self)         # its bookkeeping (an energy refusal, an overload, a felt weight) stays
        except Exception as exc:  # noqa: BLE001
            if not SAFE:
                raise
            w.rollback()
            w.begin()
            w.set_scalar("steps", state["steps"] + 1)
            camera_mod.begin_step(w)
            self._advance_mode = "idle"
            category, message, seconds = "internal", "Internal simulator error ({}); the action had no effect".format(type(exc).__name__), 1.0
            w.stat("internal_errors")
        if undo_record:
            self._undo.append(undo_record)
            del self._undo[:-50]
        # the trail (hidden state, never observed) names what the robot acted on by its true id in every perception
        # mode: in realistic episodes the agent's handle is resolved first (phase 1, climate's and tools_vehicles'
        # kernel request: domain attribution that reads the trail never matched a handle against its ids)
        trail = w.sec("action_log")
        trail.append({"step": state["steps"], "time_s": state["time_s"], "action": action,
                      "target": logged_target if isinstance(logged_target, str) else "", "ok": ok})
        if len(trail) > 300:
            del trail[:len(trail) - 300]
        # a wait that can end early (until / interrupt_on) or, in an episode with the notice bus, lists what it heard;
        # an idle step runs due auto standing orders (TIER1_SWEEP 6.3-6.4)
        is_wait = ok and spec is not None and spec.name == "wait" and ctx is not None
        plan = ctx.data.get("wait") if is_wait else None
        wait_run = timeline.WaitRun(self, plan or {"seconds": seconds}) \
            if is_wait and (plan is not None or timeline.bus_on(state)) else None
        idle_run = timeline.IdleRun(self) if ok and spec is not None and spec.idle and timeline.has_auto(state) else None
        waited = self._advance(seconds, wait=wait_run, idle=idle_run)
        if wait_run is not None or (idle_run is not None and (idle_run.ran or idle_run.stopped or idle_run.paused)):
            message = timeline.wait_message(waited, wait_run, idle_run)
            if abs(waited - seconds) > 1e-9:
                self._note_facts(action, ctx.args, 0.0)     # the wait ended now (time has advanced)
        if self.realism is not None:
            self.realism.commit_grants(ok)      # after the action's duration, so a reading matches what is shown next
        self._end_of_step()
        if self._phases.phases:
            w._writer, w._wcause = W_PHASE, None            # the task's own schedule (WP2)
            before = self._phases.active()
            self._phases.tick(self, end_of_step=True)       # a phase's at_s / when / budget end
            if self._phases.gap_left(self) is not None:
                camera_mod.begin_step(w)
                self._phases.start_next(self)               # its gap and the next phase before the agent acts again
                self._end_of_step()
            if self._phases.active() is not before:
                timeline.derive_notices(self)               # a new phase's request reaches the robot now
        outcome = self.evaluate()
        changes = w.changes() if self.trace else []
        if STRICT:
            self._strict_check(strict_copy)
        w.commit()
        record = {"command": copy.deepcopy(command), "ok": ok, "category": category, "message": message,
                  "time_s": state["time_s"]}
        if self.trace:
            record["state_hash"] = digest(state)
            record["changes"] = changes
        self._history.append(record)
        reward = round(outcome["score"] - self._last_score, 4)
        self._last_score = outcome["score"]
        raw_message = message
        if self.realism is not None:
            self.realism.invalidate()
            if not ok:
                message, category = self.realism.scrub_refusal(message, category)
            message = self.realism.outbound_message(message, involved=getattr(self.realism, "involved", ()))
            self.realism.involved = set()
            self.realism._agent_text = ()
            # an agent gets what a robot would: no evaluator verdict, reward or termination signal
            # (env.evaluate() remains the privileged scoring API for harnesses and verifiers)
            result = {"ok": ok, "message": message, "category": category,
                      "observation": self.observe() if self.observe_on_step else None}
            if self.realism.record_observations:
                self.realism.observed.append({"message": message})
            if self.raw_results:
                result["raw_message"] = raw_message
                result["evaluation"] = outcome
            return result
        result = {"ok": ok, "message": message, "category": category,
                  "observation": self.observe() if self.observe_on_step else None,
                  "evaluation": outcome, "reward": reward, "terminated": outcome["success"],
                  "truncated": outcome["budget_exceeded"]}
        return result

    def _end_of_step(self):
        """The end of a step after time advanced (TIER1_INTEGRATION 1.9 rule 5): the camera's step view and log, the
        realism note of the view, the Mind's update, the monitor's end sample. Its writes are perception bookkeeping
        (WP2: writer kernel.perception, class physical: never the robot's acts)."""
        w = self.world
        saved = (w._writer, w._wcause)
        w._writer, w._wcause = W_PERCEPTION, None
        try:
            view = camera_mod.end_step(self)
            if self.realism is not None and hasattr(self.realism, "note_step"):
                self.realism.note_step(view)        # camera tracked identity (CAM-RENDER)
            self._update_mind()
            self._monitor.sample(self, "end")
        finally:
            w._writer, w._wcause = saved

    # ------------------------------------------------------------------
    def phase_done(self):
        """The agent (or a plan's {"phase_done": true}) declares the active phase done (TIER1_SWEEP 9): the phase ends
        and is scored, its gap runs (an idle advance; the robot parked on its dock when the next phase asks for it)
        and the next phase starts with its request. Not an action: no step is counted. Returns {"ok", "message",
        "phase": {"ended", "active", "waiting"}, "observation"} (oracle also "evaluation"); waiting: no phase follows at
        once but one that starts on its own (at_s / when) is still to come."""
        w = self.world
        if not self._phases.phases or self._phases.active() is None:
            return {"ok": False, "message": "No active phase", "phase": {"ended": None, "active": None},
                    "observation": self.observe() if self.observe_on_step else None}
        state = w.s
        strict_copy = clone(state) if STRICT else None
        if self.realism is not None:
            self.realism.begin_step()
        w.begin()
        w._writer, w._wcause = ("robot", "phase_done"), None
        camera_mod.begin_step(w)
        if timeline.bus_on(state):
            # the agent already saw this step's notices: the observation phase_done returns lists only those perceived
            # from here on (perception.notices_since reads the mark; phase_done counts no step)
            steps, now = state.get("steps", 0), state["time_s"]
            seen = sum(1 for n in state.get("notices") or () if isinstance(n, dict) and n.get("step") == steps
                       and abs(float(n.get("t", 0.0)) - now) <= 1e-9)
            w.sec("phase")["notice_mark"] = {"step": steps, "t": now, "n": seen}
        ended = self._phases.end(self, "done")
        self._tick_index = phases_mod.GAP_TICK_BASE
        w._writer = W_PHASE
        started = self._phases.start_next(self)
        timeline.derive_notices(self)               # the new phase's request reaches the robot now (once)
        if self.realism is not None:
            self.realism.commit_grants(True)
        self._end_of_step()
        outcome = self.evaluate()
        changes = w.changes() if self.trace else []
        if STRICT:
            self._strict_check(strict_copy)
        w.commit()
        waiting = started is None and self._phases.waiting(self)
        message = "Phase {} done{}".format(ended["id"], "; phase {} begins".format(started["id"]) if started else
                                           "; the next request comes later" if waiting else "; no phase left")
        record = {"command": {"action": "phase_done"}, "phase_done": True, "phase": ended["id"],
                  "next_phase": started["id"] if started else None, "waiting": bool(waiting), "ok": True,
                  "category": "", "message": message, "time_s": state["time_s"]}
        if self.trace:
            record["state_hash"] = digest(state)
            record["changes"] = changes
        self._history.append(record)
        self._last_score = outcome["score"]
        result = {"ok": True, "message": message, "category": "",
                  "phase": {"ended": ended["id"], "active": started["id"] if started else None, "waiting": waiting},
                  "observation": self.observe() if self.observe_on_step else None}
        if self.realism is not None:
            self.realism.invalidate()
            if self.raw_results:
                result["evaluation"] = outcome
            return result
        result["evaluation"] = outcome
        return result

    def request(self):
        """The active request (TIER1_SWEEP 10.1): the last state.requests record of the active phase (or of the
        episode); a task without requests has today's plain instruction, {"id": "r0", "from": None, "channel":
        "unspecified", ...}, as has a phase that came without a request. None without a task. The record is the one
        obs.request shows (perception.current_request)."""
        record = current_request(self.world.s)
        if record is not None:
            return copy.deepcopy(record)
        if not self._task:
            return None
        active = self._phases.active() if self._phases.phases else None
        return {"id": "r0", "phase": active["id"] if active else None, "from": None, "channel": "unspecified",
                "room": None, "t": 0.0, "text": self._task.get("instruction", ""), "subject": None, "gesture": None}

    def _strict_check(self, before):
        w = self.world
        journaled = set()
        for kind, name in w.journal_keys():
            journaled.add(name if kind in ("section", "scalar") else kind + "." + name)
        def walk(a, b, prefix):
            if isinstance(a, dict) and isinstance(b, dict):
                for key in set(a) | set(b):
                    path = prefix + "." + key if prefix else key
                    if path in journaled:
                        continue
                    if key not in a or key not in b:
                        raise RuntimeError("Untracked mutation at " + path)
                    walk(a[key], b[key], path)
            elif a != b:
                raise RuntimeError("Untracked mutation at " + prefix)
        for top in set(before) | set(w.s):
            if top in journaled:
                continue
            if top in ("entities", "rooms", "people", "pets", "connections"):
                walk(before.get(top, {}), w.s.get(top, {}), top)
            elif before.get(top) != w.s.get(top):
                raise RuntimeError("Untracked mutation at " + top)

    # ------------------------------------------------------------------
    def _advance(self, seconds, wait=None, idle=None):
        """Advance simulated time in substeps of at most 30 s (TIER1_INTEGRATION 1.9). wait: a timeline.WaitRun that
        may end the advance at a substep boundary (until / interrupt_on); idle: a timeline.IdleRun when the robot is
        idle (a wait, a phase gap), so due auto standing orders run at the boundaries (each command's time is spent in
        the world and counts against the advance). Returns the seconds actually advanced."""
        w = self.world
        remaining, waited = float(seconds), 0.0
        if wait is not None:
            w._listening, w._heard = True, []
        try:
            if wait is not None and wait.reason is not None:
                return 0.0                      # until held when the wait began
            if idle is not None and timeline.has_due(w.s):
                spent = self._run_orders(idle)  # orders held during a busy step run at this idle moment
                remaining -= spent
                waited += spent
            while remaining > 1e-9:
                dt = min(remaining, SUBSTEP_S) if self._coarse is None else self._coarse_dt(remaining)
                self._substep(dt)
                remaining -= dt
                waited += dt
                if wait is not None and wait.check():
                    if idle is not None and timeline.has_due(w.s):
                        # an order that fired at this boundary (its "order" notice may be what ended the wait) runs
                        # before the agent gets control back
                        waited += self._run_orders(idle)
                    w.trim_touched()
                    break
                if idle is not None and timeline.has_due(w.s):
                    spent = self._run_orders(idle)
                    remaining -= spent
                    waited += spent
                w.trim_touched()                # every consumer of the touch log has read this substep's touches
        finally:
            if wait is not None:
                w._listening = False
        return round(waited, 3)

    def _substep(self, dt):
        """One substep: systems in order (idle probes skip provably idle ones; a periodic system still keeps its
        schedule), events, physics settle, standing orders, notices, the monitor, phase boundaries."""
        w = self.world
        w.set_scalar("time_s", round(w.s["time_s"] + dt, CLOCK_DIGITS))
        tick = Tick(self, self._tick_index)
        self._tick_index += 1
        probes = IDLE_PROBES[0]
        track = w._wtrack
        tags = (getattr(plugins_mod, "SYSTEM_WRITERS", None) or {}) if track else None
        saved = (w._writer, w._wcause)
        ff = self._ff if self._coarse is not None else None
        flags = self._flag_counts() if ff is not None else None
        try:
            for _, name, max_dt, fn in SYSTEMS:
                probe = SYSTEM_IDLE.get(name) if probes else None
                period = SYSTEM_PERIODS.get(name)
                if period:
                    acc = float(w.s.get("_sched", {}).get(name, 0.0)) + dt
                    if acc + 1e-9 >= period:
                        if probe is None or not _idle(probe, name, w, tick):
                            if track:
                                w._writer = _WRITERS.get((tags.get(name, "physical"), name)) or system_writer(name)
                                w._wcause = None
                            fn(w, acc, tick)
                        acc = 0.0
                    if track:
                        w._writer, w._wcause = W_KERNEL, None
                    w.sec("_sched")[name] = round(acc, 6)
                    continue
                if probe is not None and _idle(probe, name, w, tick):
                    continue
                if track:
                    w._writer = _WRITERS.get((tags.get(name, "physical"), name)) or system_writer(name)
                    w._wcause = None
                sub = dt
                while sub > 1e-9:
                    part = min(sub, max_dt)
                    fn(w, part, tick)
                    sub -= part
            if track:
                w._writer, w._wcause = W_KERNEL, None
            self._fire_events()
            if track:
                w._writer = W_SETTLE
            # consequences of what systems and events changed in this substep
            records = settle.settle(self, "substep")
            if track and records:
                self._robot_consequences(records)
            if track:
                w._writer = W_KERNEL
            self._fire_orders()
            timeline.derive_notices(self)           # speech, people coming and going (the kernel's own notices)
            self._monitor.sample(self, "substep")
            if self._phases.phases:
                if track:
                    w._writer = W_PHASE
                before = self._phases.active()
                self._phases.tick(self)
                if self._phases.active() is not before:
                    timeline.derive_notices(self)   # a new phase's request reaches a listening wait at this boundary
            if ff is not None:
                ff["busy"] = bool(records) or self._flag_counts() != flags
                ff["substeps"].append(dt)
        finally:
            w._writer, w._wcause = saved

    def _robot_consequences(self, records):
        """A substep settle's consequences the kernel physics attributes to the robot (by "robot": what the robot
        arranged, TIER1_PHYSICS 6.4) are robot writes (6.2), the rest stay physical."""
        ids = set()
        for record in records:
            if isinstance(record, dict) and record.get("by") == "robot":
                if isinstance(record.get("target"), str):
                    ids.add(record["target"])
                ids.update(i for i in record.get("ids") or () if isinstance(i, str))
        if ids:
            self.world.reclassify(sorted(ids), W_SETTLE, W_SETTLE_ROBOT)

    def _flag_counts(self):
        stats = self.world.s.get("stats") or {}
        return tuple(stats.get(key, 0) for key in evaluation.GLOBAL_FLAGS)

    def _run_orders(self, idle):
        """Run the due auto standing orders; returns the seconds their commands took."""
        before = self.world.s["time_s"]
        timeline.run_auto_orders(self, idle)
        return round(self.world.s["time_s"] - before, 3)

    def _auto_command(self, command, idle):
        """One auto standing-order command (TIER1_SWEEP 6.4, TIER1_INTEGRATION 1.9 rule 3 and conflict 43): a nested
        leaf under a savepoint, its energy settled under "covered"; refused -> rolled back to the savepoint (its body
        bookkeeping kept); done -> settled as an action, sampled by the monitor, then its time spent in the world.
        Returns (ok, message, category)."""
        w = self.world
        mode = self._advance_mode
        saved = (w._writer, w._wcause)
        sp = w.savepoint()
        self._advance_mode = "covered"
        settle.begin_action(w)
        # an auto standing-order command the robot issued is the robot's (WP2)
        w._writer, w._wcause = ("robot", "order:" + str(command.get("action"))), None
        try:
            try:
                result, ctx, spent = self._leaf(dict(command))
            except ActionError as exc:
                w.rollback_to(sp)
                self._advance_mode = mode
                if isinstance(exc, body.BodyRefusal):
                    exc.apply(self)
                return False, str(exc), exc.category
            except Exception as exc:  # noqa: BLE001
                if not SAFE:
                    raise
                w.rollback_to(sp)
                self._advance_mode = mode
                w.stat("internal_errors")
                return False, "Internal simulator error ({})".format(type(exc).__name__), "internal"
            settle.settle(self, "action", ctx)
            w.release(sp)
            self._monitor.sample(self, "order", at=w.s["time_s"] + spent)  # what it reached: at the command's end
            message = _with_notes(result, ctx.notes)
            if spent > 1e-9:
                self._advance(spent)                # the command's time: the world keeps running (covered energy)
            self._advance_mode = mode
            return True, message, ""
        finally:
            w._writer, w._wcause = saved

    def _run_gap(self, seconds):
        """A phase gap: an idle advance with every system and auto standing orders (TIER1_SWEEP 9)."""
        mode = self._advance_mode
        self._advance_mode = "idle"
        self._tick_index = max(self._tick_index, phases_mod.GAP_TICK_BASE)
        try:
            return self._advance(seconds, idle=timeline.IdleRun(self, "gap"))
        finally:
            self._advance_mode = mode

    def _fire_events(self):
        w = self.world
        state = w.s
        for i, event in enumerate(state.get("events", [])):
            if event.get("fired") and not event.get("repeat_s"):
                continue
            due = False
            if "at_step" in event and state["steps"] >= event["at_step"]:
                due = True
            if "at_s" in event and state["time_s"] >= event["at_s"]:
                due = True
            if "when" in event and check(state, event["when"]):
                due = True
            if not due or (event.get("fired") and event.get("repeat_s") and state["time_s"] < event.get("next_s", 0)):
                continue
            if w._wtrack:
                # a scheduled event of the task is volition (6.2: never charged to the robot)
                w._writer, w._wcause = ("volition", "event:" + str(event.get("kind", event.get("effect", "event")))), None
            events = w.sec("events")
            events[i]["fired"] = True
            if event.get("repeat_s"):
                events[i]["next_s"] = state["time_s"] + event["repeat_s"]
            for path, value in event.get("changes", {}).items():
                w.set_path(path, value)
            if event.get("effect"):
                results = run_hooks("event:" + event["effect"], w, event.get("args", {}), self)
                if not results:
                    raise RuntimeError("No handler for event effect " + event["effect"])
            w.emit(event.get("kind", event.get("effect", "event")), **{k: v for k, v in event.get("log", {}).items()})

    def _fire_orders(self):
        """Standing orders (timeline.fire_orders): queue orders fill `pending`, auto orders become due; the condition
        must hold and be perceivable now, realism included (TIER1_SWEEP 6.5)."""
        timeline.fire_orders(self)

    def _update_milestones(self):
        """The monitor's end-of-step sample (milestones are sampled at every sample point since kernel v3.1)."""
        self._monitor.sample(self, "end")

    # ----- the end of an episode: declared done, settle, look-ahead (VERIFY_HARDENING 8.1, 6.10; WP2) ----------
    def finish(self, settle_s=None, lookahead_s=None, process=None):
        """End the episode the way every verification run and harness episode ends (8.1). Not an action and no step:
        the robot has declared done (state D) and does nothing from here on.

        1. At D: the step count and time (for the budget), the evaluation, and each success label's value
           (goal_items: labelled predicates, the rubric, required milestones).
        2. settle_s seconds (None: the task's hardening block or "settle_s", default 60; 0..600) advance idle in 30 s
           frames: systems, people, pets and auto standing orders run. After each, every label that held at D and no
           longer holds is a regression: robot-attributable when a write since the previous sample that meets the
           label's footprint (monitor.footprint) is the robot's (a standing order) or a physical write linked to the
           robot (robot_attributable: a glass the robot left at the edge falls) -- unless the label is in `process`
           (None: the hardening block's or the task's "process") -> failure "settle:<label>"; otherwise it is
           consumed (a resident, a pet or a process changed it; listed in "consumed" with the writers).
        3. The evaluation at the settled state F: success = every label held at D, no settle failure and no failure
           at D or at F (the clock's own: "budget", missed deadlines, are judged at D).
        4. lookahead_s seconds (None: the hardening block's or "lookahead_s", default 0; 0..86400) more of idle time
           with fine ticks (6.10); a global flag, an invariant or a house.* guard first failing at the end is
           "later:<label>" and fails the run. Success is not re-checked.

        Returns the evaluation dict of F with success, failures, goal_fraction, budget_exceeded, steps and time_s as
        decided above, plus "declared" {"steps", "time_s"}, "end_time_s", "consumed" [{"label", "t", "writers"}],
        "settle" {"s", "samples", "regressions", "consumed"} and "lookahead" {"s", "labels"}. Recorded in the
        history (a replay repeats it with the same arguments)."""
        task = self._task or {}
        hardening = task.get("hardening") if isinstance(task.get("hardening"), dict) else {}
        if settle_s is None:
            settle_s = hardening.get("settle_s", task.get("settle_s", 60.0))
        if lookahead_s is None:
            lookahead_s = hardening.get("lookahead_s", task.get("lookahead_s", 0.0))
        if process is None:
            process = hardening.get("process", task.get("process")) or ()
        for label, value, top in (("settle_s", settle_s, SETTLE_MAX_S), ("lookahead_s", lookahead_s, LOOKAHEAD_MAX_S)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= top:
                raise ValueError("finish: {} must be a number of seconds in 0..{:g} (got {!r})".format(label, top, value))
        if isinstance(process, str) or not all(isinstance(p, str) for p in process):
            raise ValueError("finish: process is a list of success labels")
        # on the clock's grid (_substep keeps time_s to the millisecond): a settle off the grid (30.0004 s) would leave
        # a remainder no frame can advance, and the settle loop would never end
        settle_s, lookahead_s = round(float(settle_s), CLOCK_DIGITS), round(float(lookahead_s), CLOCK_DIGITS)
        process = sorted(set(process))
        w = self.world
        state = w.s
        at_d = self.evaluate()
        declared = {"steps": state.get("steps", 0), "time_s": state.get("time_s", 0.0)}
        items = self._finish_items()
        held = self._held(items, self._view())
        watch = [(label, test, (monitor_mod.footprint(rule), predicate_fields(rule))) for label, test, rule in items
                 if held[label]]
        regressions, consumed, samples = [], [], 0
        self._tick_index = max(self._tick_index, FINISH_TICK_BASE)
        end = round(float(state.get("time_s", 0.0)) + settle_s, CLOCK_DIGITS)
        log = self.writes
        log.recent = []
        try:
            while float(state.get("time_s", 0.0)) < end - 1e-6:
                before = float(state.get("time_s", 0.0))
                self._idle_frame(min(SUBSTEP_S, end - before))
                samples += 1
                if float(state.get("time_s", 0.0)) <= before:
                    # the clock did not move (a remainder below its resolution): the window is over -- every run has
                    # a cap (P7)
                    end = before
                recent, log.recent = log.recent, []
                if not watch:
                    continue
                now_held = self._held(items, self._view())
                keep = []
                for label, test, fp in watch:
                    if now_held.get(label):
                        keep.append((label, test, fp))
                        continue
                    robot, writers = self._regression(fp, recent)
                    entry = {"label": label, "t": round(float(state.get("time_s", 0.0)), 3), "writers": writers}
                    if robot and label not in process:
                        regressions.append(entry)
                    else:
                        consumed.append(entry)
                watch = keep
        finally:
            log.recent = None
        at_f = self.evaluate()
        failures = self._finish_failures(at_d, at_f)
        failures.extend("settle:" + r["label"] for r in regressions)
        at_f_held = self._held(items, self._view())
        listed = {c["label"] for c in consumed} | {r["label"] for r in regressions}
        for label, _, _ in items:
            if held[label] and label not in listed and not at_f_held[label]:
                consumed.append({"label": label, "t": round(float(state.get("time_s", 0.0)), 3), "writers": []})
        later = []
        if lookahead_s > 0:
            flags_f = self._harm_labels(at_f)
            self._idle_frame(lookahead_s)
            at_l = self.evaluate()
            later = ["later:" + label for label in sorted(self._harm_labels(at_l) - flags_f)]
            failures.extend(later)
        goal_d = at_d.get("goal_fraction", 0.0) >= 1.0 - 1e-9 and all(held.values())
        success = goal_d and not failures
        result = dict(at_f)
        penalty = (1.0 if at_f.get("success") else 0.0) - float(at_f.get("score", 0.0))
        result.update({"success": success, "failures": failures, "goal_fraction": at_d.get("goal_fraction", 0.0),
                       "budget_exceeded": at_d.get("budget_exceeded", False), "steps": declared["steps"],
                       "time_s": declared["time_s"], "end_time_s": state.get("time_s", 0.0),
                       "score": round((1.0 if success else 0.0) - penalty, 4), "declared": declared,
                       "consumed": consumed,
                       "settle": {"s": settle_s, "samples": samples, "regressions": [r["label"] for r in regressions],
                                  "consumed": [c["label"] for c in consumed]},
                       "lookahead": {"s": lookahead_s, "labels": later}})
        if "unmet" in at_d:
            result["unmet"] = at_d["unmet"]
        record = {"command": {"action": "finish"}, "finish": {"settle_s": settle_s, "lookahead_s": lookahead_s,
                                                              "process": process},
                  "ok": True, "category": "", "message": "finished", "time_s": state.get("time_s", 0.0)}
        if self.trace:
            record["state_hash"] = digest(state)
        self._history.append(record)
        self._last_score = result["score"]
        return result

    def _view(self):
        """The evaluation view finish samples labels on: with the derived-field overlay of a hardened task (WP1:
        evaluation_view(..., task=))."""
        mind = self.mind if self._uses_mind else None
        return evaluation.evaluation_view(self.world.s, self._monitor, mind, task=self._task)

    def _held(self, items, view):
        """{label: value} of finish's labels on a view; a hardened task's protective label whose subject is gone is
        not held (WP1's existence guard: evaluation.goal_status marks it "<label>:absent")."""
        held = {label: bool(test(view)) for label, test, _ in items}
        if evaluation.hardened(self._task) and not self._phases.phases:
            mind = self.mind if self._uses_mind else None
            for label, value in evaluation.goal_status(self._task, self.world.s, self._baseline, self._monitor, mind,
                                                       view=view):
                if label.endswith(":absent") and label[:-7] in held:
                    held[label[:-7]] = False
        return held

    def _finish_items(self):
        """The success labels finish watches: the task's (goal_items), or the active phase's in a multi-phase task
        (labelled "<phase id>:<label>")."""
        if self._phases.phases:
            phase = self._phases.active()
            if phase is None:
                return []
            prefix = str(phase.get("id", "phase")) + ":"
            return [(prefix + label, test, rule) for label, test, rule in goal_items(self._task, phase)]
        return goal_items(self._task)

    def _finish_failures(self, at_d, at_f):
        """The run's failures: those at D, then those new at F, except the clock's own (the budget and missed
        deadlines are judged at D: the settle window is not the robot's time)."""
        out = list(at_d.get("failures") or [])
        seen = set(out)
        for label in at_f.get("failures") or []:
            if label in seen or label == "budget" or str(label).startswith("deadline:"):
                continue
            seen.add(label)
            out.append(label)
        return out

    def _harm_labels(self, result):
        """The look-ahead's harms in an evaluation (6.10, 6.4): global flags, invariant labels and the ledger guards
        the look-ahead re-checks (house.damaged, the mess guards, house.waste, house.held_open: ledger.LOOKAHEAD_GUARDS
        when the ledger is present)."""
        flags = set(evaluation.GLOBAL_FLAGS)
        inv = set()
        state, baseline = self.world.s, self._baseline
        allow = set((self._task or {}).get("allow", []))
        for name, fn in sorted(plugins_mod.INVARIANTS.items()):
            if name in allow:
                continue
            arity = evaluation._ARITY.get(fn)
            if arity is None:
                arity = evaluation._ARITY[fn] = evaluation._arity(fn)
            label = fn(state, baseline, self.world) if arity >= 3 else fn(state, baseline)
            if label:
                inv.add(label)
        guards = _lookahead_guards()
        return {label for label in result.get("failures") or []
                if label in flags or label in inv or str(label).split(":", 1)[0] in guards}

    def _regression(self, fp, recent):
        """(robot-attributable, writers) of a label's regression: the writes since the previous sample that meet its
        footprint (the records it reads, narrowed to the fields it reads); robot-attributable when one is the
        robot's, or physical and linked to the robot."""
        robot, writers = False, set()
        for rid, field, cls, name, t, causes in recent:
            if not self._meets(fp, rid, field):
                continue
            writers.add((cls, name))
            if cls == "robot" and (not is_bookkeeping(field) if field is not None else
                                   rid[8:] not in BOOKKEEPING_SECTIONS):
                robot = True
            elif cls == "physical" and not robot and \
                    self.robot_attributable(rid, field, t, list(causes) if causes else None, unknown=False):
                robot = True
        return robot, [list(wr) for wr in sorted(writers)]

    def _meets(self, footprint, rid, field):
        """Whether a write of rid's field can change a predicate with this footprint: (monitor.Footprint, the
        predicate_fields of the predicate). A creation or destruction ("$exists") meets every record in scope."""
        fp, (direct, quant) = footprint
        if fp.always:
            return True
        if rid.startswith("section:"):
            return rid[8:] in fp.sections
        head, sep, rest = rid.partition(":")
        if sep and head in KEYED_KINDS:
            return head in fp.sections or (head == "rooms" and fp.any_entity)
        if hasattr(fp, "meets"):
            # WP3's field-level footprint (monitor.Footprint.meets: conservative, None = a creation or destruction)
            record = self.world.s["entities"].get(rid) or self.world.gone_record(rid)
            return bool(fp.meets(rid, None if field == EXISTS else field, record))
        if rid in direct:
            fields = direct[rid]
            if fields is None or field in fields or field == EXISTS:
                return True
        if field != EXISTS and field not in quant:
            return False
        if fp.any_entity:
            return True
        if fp.types:
            record = self.world.s["entities"].get(rid) or self.world.gone_record(rid) or {}
            kinds = {("type", record.get("type")), ("category", record.get("category"))}
            kinds.update(("type", t) for t in record.get("is_a") or ())
            return bool(kinds & fp.types)
        return rid in fp.ids and rid not in direct

    def _idle_frame(self, seconds):
        """Advance `seconds` of idle time as one frame that is not a step (finish's settle samples and look-ahead): the
        robot does nothing; systems, people, pets, events and auto standing orders run, and the robot keeps
        perceiving (the end-of-step updates). Returns the seconds advanced."""
        w = self.world
        state = w.s
        strict_copy = clone(state) if STRICT else None
        if self.realism is not None:
            self.realism.begin_step()
        w.begin()
        w._writer, w._wcause = W_KERNEL, None
        camera_mod.begin_step(w)
        mode = self._advance_mode
        self._advance_mode = "idle"
        try:
            idle = timeline.IdleRun(self, "finish") if timeline.has_auto(state) else None
            waited = self._advance(seconds, idle=idle)
        finally:
            self._advance_mode = mode
        if self.realism is not None:
            self.realism.commit_grants(True)
        self._end_of_step()
        if STRICT:
            self._strict_check(strict_copy)
        w.commit()
        if self.realism is not None:
            self.realism.invalidate()
        return waited

    # ----- fast-forwarded waits (VERIFY_HARDENING 10.5; WP2) -----------------------------------------------------
    def fast_forward(self, seconds, tick=300):
        """A plain wait of `seconds` (1..604800) run on a coarse tick (10.5): substeps of `tick` seconds (a multiple of
        30, 30..3600) whenever nothing needs fine time, fine 30 s substeps around anything that does -- an event, a
        standing order or a phase that could fire before the next coarse boundary, a person or pet that changed room
        or is active in the robot's room (moved, changed posture, activity or wakefulness: a resident asleep there is
        no activity), a physics consequence or a raised harm flag in the last substep (and two quiet fine substeps
        after any of them). Otherwise exactly env.step("wait", seconds=...): a step, recorded in the history (a
        replay repeats it with the same tick). For battery and must-pass runs after the per-spec equivalence check;
        core trials, the look-ahead and the agent harness use fine ticks. last_fast_forward reports the substeps run
        ({"coarse", "fine", "seconds"})."""
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 1 <= seconds <= 604800:
            raise ValueError("fast_forward: seconds must be in 1..604800 (got {!r})".format(seconds))
        if isinstance(tick, bool) or not isinstance(tick, (int, float)) or not SUBSTEP_S <= tick <= 3600 or \
                abs(tick / SUBSTEP_S - round(tick / SUBSTEP_S)) > 1e-9:
            raise ValueError("fast_forward: tick must be a multiple of 30 s in 30..3600 (got {!r})".format(tick))
        self._coarse = float(tick)
        self._ff = {"rooms": None, "quiet": 0, "busy": False, "substeps": []}
        try:
            result = self.step("wait", seconds=int(seconds))
        finally:
            ff, self._coarse, self._ff = self._ff, None, None
        coarse = sum(1 for dt in ff["substeps"] if dt > SUBSTEP_S + 1e-9)
        self.last_fast_forward = {"coarse": coarse, "fine": len(ff["substeps"]) - coarse,
                                  "seconds": round(sum(ff["substeps"]), 3)}
        if self._history:
            self._history[-1]["fast_forward"] = float(tick)
        return result

    def _coarse_dt(self, remaining):
        """The next substep's length while fast-forwarding: the coarse tick when nothing needs fine time, else 30 s."""
        fine = min(remaining, SUBSTEP_S)
        ff = self._ff
        state = self.world.s
        agents = self._agent_signatures(state)
        before = ff["rooms"] or {}
        robot_room = (state.get("robot") or {}).get("room")
        # activity: someone changed room anywhere, or someone in the robot's room moved, got up, lay down, started or
        # stopped doing something (a resident asleep in the room is no activity)
        active = ff["rooms"] is not None and any(
            sig[0] != (before.get(key) or (None,))[0] or (sig[0] == robot_room and sig != before.get(key))
            for key, sig in agents.items()) or set(agents) != set(before) and ff["rooms"] is not None
        ff["rooms"] = agents
        if active or ff["busy"] or self._phases.phases:
            ff["quiet"] = 0
            return fine
        dt = min(remaining, self._coarse)
        if not self._quiet_until(state, float(state.get("time_s", 0.0)) + dt):
            ff["quiet"] = 0
            return fine
        if ff["quiet"] < FF_QUIET_SUBSTEPS:
            ff["quiet"] += 1
            return fine
        return dt

    @staticmethod
    def _agent_signatures(state):
        """{"people:<id>" / "pets:<id>": (room, position, posture, activity, awake, state)} for fast_forward."""
        out = {}
        for kind in ("people", "pets"):
            for pid, record in (state.get(kind) or {}).items():
                if isinstance(record, dict):
                    pos = record.get("pos")
                    out[kind + ":" + pid] = (record.get("room", record.get("location")),
                                             tuple(pos) if isinstance(pos, list) else pos, record.get("posture"),
                                             record.get("activity"), record.get("awake"), record.get("state"))
        return out

    @staticmethod
    def _quiet_until(state, horizon):
        """True when no event and no standing order can fire before `horizon` (fast_forward)."""
        steps = state.get("steps", 0)
        for event in state.get("events") or ():
            if not isinstance(event, dict) or (event.get("fired") and not event.get("repeat_s")):
                continue
            if "when" in event:
                return False
            if "at_step" in event and steps >= event["at_step"]:
                return False
            at = event.get("next_s", 0.0) if event.get("fired") else event.get("at_s")
            if at is not None and float(at) <= horizon + 1e-9:
                return False
        for order in state.get("orders") or ():
            if isinstance(order, dict) and (order.get("due") or not order.get("fired") or order.get("persistent")):
                return False
        return True

    # ------------------------------------------------------------------
    def evaluate(self):
        phases = self._phases if self._phases.phases else None
        return evaluation.evaluate(self._task, self.world.s, self._baseline, self._milestones, self.world.s["steps"],
                                   world=self.world, temporal=self._monitor, phases=phases,
                                   mind=self.mind if self._uses_mind else None)

    def task_complete(self):
        return self.evaluate()["success"]

    def affordances(self, target):
        """Side-effect-free availability probe for single-target actions on an entity."""
        state = self.world.s
        if self.realism is not None:
            # no free probing in realistic mode: which actions take an object, not whether they would succeed
            self.realism.inbound({"target": target}, entity_keys=("target",))
            return [{"action": name, "params": sorted(ACTIONS[name].params)} for name in sorted(self.action_names())
                    if "target" in ACTIONS[name].params and ACTIONS[name].params["target"]["type"] in ("entity", "target")]
        if not (target in state["entities"] and self.perception.visible(state, target)):
            raise ActionError("That is not visible here", "perception")
        return self._affordances(target)

    def _affordances(self, target):
        state = self.world.s
        if not (target in state["entities"] and self.perception.visible(state, target)):
            raise ActionError("That is not visible here", "perception")
        held = self.world.held_items()
        result = []
        for name in sorted(self.action_names()):        # what the agent is offered (no camera verbs in room mode)
            spec = ACTIONS[name]
            if "target" not in spec.params or spec.params["target"]["type"] not in ("entity", "target"):
                continue
            raw = {"target": target}
            if "tool" in spec.params and held:
                raw["tool"] = held[0]
            saved = clone(state)
            self.world.begin()
            try:
                args = spec.validate(raw)
                body.power_guard(state, spec)           # as step: nothing but wait while powered down
                body.quiet_guard(state, spec)
                ctx = Ctx(self, spec, args, random.Random(0))
                for _, _, fn in BEFORE.get(name, []):
                    fn(ctx)
                spec.fn(ctx)
                result.append({"action": name, "available": True, "reason": ""})
            except ActionError as exc:
                result.append({"action": name, "available": False, "reason": str(exc)})
            except Exception as exc:  # a probe must never crash the caller
                result.append({"action": name, "available": False, "reason": type(exc).__name__})
            finally:
                self.world.rollback()
                self.world.s.clear()
                self.world.s.update(saved)
        return result

    # ------------------------------------------------------------------
    def save(self, path):
        payload = {"format": "house-state-v2", "initial": self._initial, "task": self._task, "seed": self.seed,
                   "state": self.world.s, "baseline": self._baseline, "history": self._history,
                   "known_map": self._known_map.snapshot(), "undo": self._undo, "snapshots": self._snapshots,
                   "replay_start": self._replay_start, "sensors": self.sensors, "milestones": self._milestones,
                   "actions_hash": actions_fingerprint(), "perception": self.perception_mode,
                   "camera": self.camera_mode,
                   "realism": self.realism.dump() if self.realism is not None else None,
                   "monitor": self._monitor.record(), "mind": self._mind_dump(),
                   "writes": self.writes.dump(), "tick_index": self._tick_index, "last_score": self._last_score,
                   # the idle counterfactual by digest (the projections can be ~20 MB for a 4 h budget, against 10.5's
                   # 0.5-1 MB per snapshot): HouseEnv.load attaches the caller's after checking it
                   "idle": self._idle_pending if self._idle_pending is not None else
                   (idle_record(self._idle) if self._idle is not None else None)}
        envelope = {"payload": payload, "sha256": digest(payload)}
        Path(path).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path, idle=None):
        """The env saved at path (env.save), for continuing or forking it (10.5: a forked run computes what the
        uninterrupted one does). idle: the idle counterfactual the saved env had (a snapshot records only its digest,
        which must match); without it an env saved with one raises IdleMissing on evaluation until env.idle is set.
        Passing one for a snapshot saved without one is a ValueError."""
        envelope = json.loads(Path(path).read_text(encoding="utf-8"))
        payload = envelope["payload"]
        if envelope.get("sha256") != digest(payload) or payload.get("format") != "house-state-v2":
            raise ValueError("Corrupt or unsupported state file")
        env = cls(payload["initial"], payload["task"], payload["seed"], payload["sensors"], perception=payload.get("perception"),
                  camera=payload.get("camera") or "room")
        if payload["actions_hash"] != actions_fingerprint():
            raise ValueError("Action definitions changed since this state was saved")
        state = payload["state"]
        check_config = copy.deepcopy(state)
        for eid in [e for e, rec in check_config["entities"].items() if rec.get("location") == "robot" and rec.get("relation") != "worn"]:
            if eid not in check_config["robot"].get("hands", {}).values():
                raise ValueError("Inconsistent held object")
        validate(check_config)
        env.world = World(state, env.registry)
        env.writes = WriterLog.load(payload.get("writes"))     # a state saved before WP2: an empty log
        env.world.track(env.writes)
        env._tick_index = int(payload.get("tick_index", 0) or 0)
        env._camera_view = None
        env._baseline, env._history = payload["baseline"], payload["history"]
        env._known_map, env._undo, env._snapshots = payload["known_map"], payload["undo"], payload["snapshots"]
        env._replay_start = payload["replay_start"]
        if payload.get("monitor") is not None:
            env._monitor.load(payload["monitor"])
        else:
            env._monitor.load({"milestones": payload.get("milestones", {})})
        timeline.start(env)
        env._sync_instruction()
        if env.realism is not None:
            env.realism.load(payload.get("realism"))
        if env.mind is not None and payload.get("mind") is not None:
            env.mind.load(payload["mind"])
        elif env.mind is not None and isinstance(payload.get("known_map"), dict):
            # a state saved before the Mind existed: its observation-time known map seeds the Mind's map
            for rid, entry in sorted(payload["known_map"].items()):
                if isinstance(entry, dict):
                    env.mind.map[rid] = {"label": entry.get("label"), "type": entry.get("type"),
                                         "exits": sorted(entry.get("exits") or []),
                                         "connections": [{k: c.get(k) for k in ("id", "to", "kind", "door")}
                                                         for c in entry.get("connections") or [] if isinstance(c, dict)],
                                         "seen_s": entry.get("seen_s")}
        recorded = payload.get("idle")
        if isinstance(recorded, dict):
            env._idle_pending = dict(recorded)
            if idle is not None:
                env.idle = idle                 # checked against the recorded digest
        elif idle is not None:
            raise ValueError("this snapshot was saved without an idle counterfactual: a fork judged against one would "
                             "not compute what the uninterrupted run does")
        # the score the next step's reward starts from: the saved env's (a state saved before it was recorded:
        # evaluated here)
        env._last_score = payload["last_score"] if "last_score" in payload else env.evaluate()["score"]
        return env

    def _mind_dump(self):
        return self.mind.dump() if self.mind is not None else None

    def _sync_instruction(self):
        """The instruction is the active phase's request text (a loaded or replayed multi-phase episode)."""
        if not self._phases.phases or self._task is None:
            return
        phase = self._phases.active()
        if phase is not None:
            text = (phase.get("request") or {}).get("text", phase.get("instruction"))
            if text is not None:
                self._task["instruction"] = text

    def save_replay(self, path, compact=True, keep_changes=False):
        """Write the episode's replay (kernel/replays.py): the compact house-replay-v3 by default (the states and the
        command log exactly, with every recorded step digest, compressed together: about 60-95 KB instead of about
        3 MB; traced steps' change lists only with keep_changes=True, since a traced re-run computes them again),
        the plain house-replay-v2 with compact=False. HouseEnv.replay and the viewer read both."""
        data = {"format": replays_mod.FORMAT_V2, "initial": self._initial, "start_state": self._replay_start,
                "baseline": self._baseline, "task": self._task, "seed": self.seed, "perception": self.perception_mode,
                "camera": self.camera_mode,
                "actions_hash": actions_fingerprint(), "steps": self._history, "final_hash": digest(self.world.s),
                "evaluation": self.evaluate(), "metadata": {"physics_version": physics.physics_version()}}
        replays_mod.write(path, data, compact=compact, keep_changes=keep_changes)

    @classmethod
    def replay(cls, path):
        """Re-run a replay file (house-replay-v2 or v3) and check every recorded step digest and the final one."""
        data = replays_mod.read(path)
        env = cls(data["initial"], data["task"], data["seed"], perception=data.get("perception"),
                  camera=data.get("camera") or "room")
        if actions_fingerprint() != data["actions_hash"]:
            raise ValueError("Replay action definitions differ")
        recorded = (data.get("metadata") or {}).get("physics_version")
        if recorded != physics.physics_version():
            import warnings
            warnings.warn("Replay recorded with physics data {} (now {}): outcomes may differ".format(
                recorded or "from before kernel v3.0", physics.physics_version()), RuntimeWarning, stacklevel=2)
        env.world = World(clone(data["start_state"]), env.registry)
        env.writes = WriterLog()            # rebuilt by the re-run (WP2)
        env.world.track(env.writes)
        env._camera_view = None
        timeline.start(env)
        env._sync_instruction()
        if env.realism is not None:
            from .realism import Realism
            env.realism = Realism(env)
            # as at the recorded episode's reset: the view noted, the Mind started against this Realism (prior knowledge
            # issues its handles and grants its knowledge there, kernel/mind.py from_config), and the observation
            # reset() returned (it issued the handles of what was seen then and started a dark spell; with the
            # geometric camera those things may never be in view again)
            env._note_view()
            env._start_mind()
            env.observe()
        env._baseline = data["baseline"]
        env._replay_start = clone(env.world.s)
        env._last_score = env.evaluate()["score"]
        for number, record in enumerate(data["steps"], 1):
            command = dict(record["command"])
            if record.get("phase_done"):
                result = env.phase_done()
            elif record.get("finish"):
                env.finish(**record["finish"])
                result = {"ok": True}
            elif record.get("fast_forward"):
                result = env.fast_forward(command.get("seconds", 1), tick=record["fast_forward"])
            else:
                result = env.step(**command)
            if result["ok"] != record["ok"]:
                raise ValueError("Replay diverged at step {} (ok {} vs {})".format(number, result["ok"], record["ok"]))
            if record.get("state_hash") and digest(env.world.s) != record["state_hash"]:
                raise ValueError("Replay diverged at step " + str(number))
        if digest(env.world.s) != data["final_hash"]:
            raise ValueError("Replay final state differs")
        return env


def actions_fingerprint():
    load_domains()
    return digest(sorted((name, spec.describe()["params"], str(spec.duration) if not callable(spec.duration) else spec.fn.__module__)
                         for name, spec in ACTIONS.items()))
