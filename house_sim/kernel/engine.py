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
"""
import copy
import json
import os
import random
from pathlib import Path

from . import body, evaluation, physics, timeline  # noqa: F401  (physics registers shared systems; timeline the time verbs)
from . import camera as camera_mod      # the camera runtime: its system, after-hooks and hooks; begin/end of step
from . import mind as mind_mod
from . import monitor as monitor_mod
from . import phases as phases_mod
from .actions import Ctx, motion_records
from .errors import ActionError
from .phys import settle
from .perception import Perception, current_request
from .plugins import (ACTIONS, AFTER, BEFORE, IDLE_PROBES, SYSTEM_IDLE, SYSTEM_PERIODS, SYSTEMS, load_domains,
                      run_hooks)
from .predicates import check, digest, validate_predicate
from .registry import default_registry
from .schema import normalize, validate
from .world import World, clone

body.register_plugins()                 # the robot body's system, invariant and event effects (TIER1_INTEGRATION 1.16)

STRICT = os.environ.get("HOUSE_SIM_STRICT") == "1"
# HOUSE_SIM_SAFE=1 (for evaluating external agents): a defect inside a handler, hook or system becomes a refused
# step (category "internal", stat internal_errors, which fails the task) instead of an exception. Off by default so
# development, tests, the verifier and tools/fuzz.py see defects.
SAFE = os.environ.get("HOUSE_SIM_SAFE") == "1" and not STRICT
RUNTIME_DEFAULTS = {"steps": 0, "time_s": 0.0}
SENSORS = {"symbolic", "language", "audio", "olfactory", "thermal", "proprioception"}
SUBSTEP_S = 30.0


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

    def known_map(self):
        return copy.deepcopy(self._known_map)

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
        robot = self.world.s["robot"]
        run_hooks("robot_sound", self.world, float(level), ctx.spec.name, robot.get("room"),
                  list(robot.get("pos") or []))

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
        w.set_scalar("steps", state["steps"] + 1)
        camera_mod.begin_step(w)        # this step's frames and seen events start empty (no-op in room mode)
        self._advance_mode = "covered"  # the action pays for its own seconds at settlement
        self._tick_index = 0
        spec = ACTIONS.get(action)
        ok, category, message, seconds = False, "", "", 1.0
        rng = random.Random("{}:{}:{}".format(self.seed, state["steps"], action))
        undo_record = None
        ctx, popped = None, False
        try:
            if spec is None:
                raise ActionError("Unknown action: " + action, "argument")
            raw = {"target": target, "destination": destination}
            raw.update(kwargs)
            if self.realism is not None and not scheduled:
                raw = self.realism.inbound(raw, text_keys=tuple(k for k, v in spec.params.items() if v.get("type") == "text"),
                                           entity_keys=tuple(k for k, v in spec.params.items()
                                                             if v.get("type") in ("entity", "target", "agent", "person", "pet")))
                self.realism.involved = {v for v in raw.values() if isinstance(v, str) and v in state["entities"]}
            args = spec.validate(raw)
            robot = state["robot"]
            if robot.get("stopped") and not spec.stopped_ok:
                raise ActionError("Robot is stopped; resume first", "precondition")
            body.power_guard(state, spec)           # PowerOff unless the action may run powered down (wait)
            body.quiet_guard(state, spec)           # quiet mode: a declared noise_db over the quiet limit
            ctx = Ctx(self, spec, args, rng)
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
        trail = w.sec("action_log")
        trail.append({"step": state["steps"], "time_s": state["time_s"], "action": action,
                      "target": target if isinstance(target, str) else "", "ok": ok})
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
        realism note of the view, the Mind's update, the monitor's end sample."""
        view = camera_mod.end_step(self)
        if self.realism is not None and hasattr(self.realism, "note_step"):
            self.realism.note_step(view)        # camera tracked identity (CAM-RENDER)
        self._update_mind()
        self._monitor.sample(self, "end")

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
        record = {"command": {"action": "phase_done"}, "phase_done": True, "ok": True, "category": "",
                  "message": message, "time_s": state["time_s"]}
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
                dt = min(remaining, SUBSTEP_S)
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
        w.set_scalar("time_s", round(w.s["time_s"] + dt, 3))
        tick = Tick(self, self._tick_index)
        self._tick_index += 1
        probes = IDLE_PROBES[0]
        for _, name, max_dt, fn in SYSTEMS:
            probe = SYSTEM_IDLE.get(name) if probes else None
            period = SYSTEM_PERIODS.get(name)
            if period:
                acc = float(w.s.get("_sched", {}).get(name, 0.0)) + dt
                if acc + 1e-9 >= period:
                    if probe is None or not probe(w):
                        fn(w, acc, tick)
                    acc = 0.0
                w.sec("_sched")[name] = round(acc, 6)
                continue
            if probe is not None and probe(w):
                continue
            sub = dt
            while sub > 1e-9:
                part = min(sub, max_dt)
                fn(w, part, tick)
                sub -= part
        self._fire_events()
        settle.settle(self, "substep")          # consequences of what systems and events changed in this substep
        self._fire_orders()
        timeline.derive_notices(self)           # speech, people coming and going (the kernel's own notices)
        self._monitor.sample(self, "substep")
        if self._phases.phases:
            before = self._phases.active()
            self._phases.tick(self)
            if self._phases.active() is not before:
                timeline.derive_notices(self)   # a new phase's request reaches a listening wait at this boundary

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
        sp = w.savepoint()
        self._advance_mode = "covered"
        settle.begin_action(w)
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
                   "known_map": self._known_map, "undo": self._undo, "snapshots": self._snapshots,
                   "replay_start": self._replay_start, "sensors": self.sensors, "milestones": self._milestones,
                   "actions_hash": actions_fingerprint(), "perception": self.perception_mode,
                   "camera": self.camera_mode,
                   "realism": self.realism.dump() if self.realism is not None else None,
                   "monitor": self._monitor.record(), "mind": self._mind_dump()}
        envelope = {"payload": payload, "sha256": digest(payload)}
        Path(path).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path):
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
        env._last_score = env.evaluate()["score"]
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

    def save_replay(self, path):
        data = {"format": "house-replay-v2", "initial": self._initial, "start_state": self._replay_start,
                "baseline": self._baseline, "task": self._task, "seed": self.seed, "perception": self.perception_mode,
                "camera": self.camera_mode,
                "actions_hash": actions_fingerprint(), "steps": self._history, "final_hash": digest(self.world.s),
                "evaluation": self.evaluate(), "metadata": {"physics_version": physics.physics_version()}}
        Path(path).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def replay(cls, path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("format") != "house-replay-v2":
            raise ValueError("Unsupported replay")
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
