"""HouseEnv: reset/step/observe/evaluate, simulated time, events, undo, persistence, replay.

The agent interface is env.step(action, target="", destination="", **params). Every
action is a registered ActionSpec with validated parameters, guard hooks and effect
hooks. Failed actions are atomic (journal rollback) but still consume a step and one
simulated second, during which exogenous processes continue. Task goals are read
only by the evaluator; no action can mark a task complete or set arbitrary fields.
"""
import copy
import json
import os
import random
from pathlib import Path

from . import evaluation, physics  # noqa: F401  (physics registers shared systems)
from .actions import Ctx
from .errors import ActionError
from .perception import Perception
from .plugins import ACTIONS, AFTER, BEFORE, SYSTEM_PERIODS, SYSTEMS, load_domains, run_hooks
from .predicates import check, digest, validate_predicate
from .registry import default_registry
from .schema import normalize, validate
from .world import World, clone

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
                 trace=True, observe_on_step=True, perception=None):
        """perception: "oracle" (exact records of everything visible; the verification default) or "realistic"
        (opaque handles, field tiers, knowledge levels, noise and look-alikes; see kernel/realism.py). Defaults to
        the task's "perception" field, else "oracle"."""
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
            for rule in task.get("milestones", {}).values():
                validate_predicate(rule)
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
        self.world = World(state, self.registry)
        if self.perception_mode == "realistic":
            from .realism import Realism
            self.realism = Realism(self)
        if not preserve_world:
            self.world.begin()
            run_hooks("episode_start", self.world, self)
            self.world.commit()
        self._baseline = clone(state)
        self._known_map, self._history, self._undo, self._snapshots = {}, [], [], {}
        self._milestones = {}
        self._replay_start = clone(state)
        self._last_score = self.evaluate()["score"]
        return self.observe()

    def privileged_state(self):
        """Oracle/debug API. Never give this to an evaluated agent."""
        return clone(self.world.s)

    def known_map(self):
        return copy.deepcopy(self._known_map)

    def observe(self):
        if self.realism is not None:
            return self.realism.observe()
        return self.perception.observe(self)

    # ------------------------------------------------------------------
    def action_names(self):
        return set(ACTIONS)

    def describe_action(self, name):
        spec = ACTIONS.get(name)
        if spec is None:
            raise KeyError(name)
        info = spec.describe()
        info["guards"] = [label for _, label, _ in BEFORE.get(name, [])]
        info["effects"] = [label for _, label, _ in AFTER.get(name, [])]
        return info

    def perform(self, command, parent_ctx=None):
        """Run one action inside the current step (used by undo and scheduled commands)."""
        command = dict(command)
        name = command.pop("action")
        spec = ACTIONS.get(name)
        if spec is None:
            raise ActionError("Unknown action: " + str(name), "argument")
        args = spec.validate(command)
        ctx = Ctx(self, spec, args, random.Random("{}:{}:nested:{}".format(self.seed, self.world.s["steps"], name)))
        for _, _, fn in BEFORE.get(name, []):
            fn(ctx)
        result = spec.fn(ctx)
        for _, _, fn in AFTER.get(name, []):
            fn(ctx, result)
        seconds = spec.duration(ctx) if callable(spec.duration) else float(spec.duration)
        if parent_ctx is not None:
            parent_ctx.spend(seconds + ctx.extra_time)
        return result

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
        w.begin()
        scheduled = action == "run_pending" and bool(state.get("pending"))
        if scheduled:
            queued = copy.deepcopy(state["pending"][0])
            action = queued.pop("action")
            target, destination = queued.pop("target", ""), queued.pop("destination", "")
            kwargs = queued
        w.set_scalar("steps", state["steps"] + 1)
        spec = ACTIONS.get(action)
        ok, category, message, seconds = False, "", "", 1.0
        rng = random.Random("{}:{}:{}".format(self.seed, state["steps"], action))
        undo_record = None
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
            if scheduled:
                w.sec("pending").pop(0)
            seconds = (spec.duration(ctx) if callable(spec.duration) else float(spec.duration)) + ctx.extra_time
            seconds = max(0.0, seconds)
            if robot.get("speed_mode") == "careful" and spec.category == "physical":
                seconds *= 1.5
            message = _with_notes(result, ctx.notes)
            cost = float(spec.energy)
            if cost:
                r = w.robot_m()
                r["battery"] = round(max(0.0, r["battery"] - cost), 4)
                w.stat("energy", cost)
            if spec.noise:
                w.stat("noise", float(spec.noise))
            facts = w.sec("facts")
            facts.setdefault("action_times", {}).setdefault(args.get("target") or "robot", {})[action] = state["time_s"] + seconds
            facts["last_action"] = {"action": action, "target": args.get("target", ""), "step": state["steps"]}
            if spec.irreversible:
                self._undo = []
            elif spec.inverse is not None or spec.reversible:
                inverse = spec.inverse(ctx, before_state) if spec.inverse else None
                if inverse:
                    changed = [d for d in w.changes() if d["path"].startswith(("entities.", "robot.room", "robot.hands", "robot.pos", "rooms."))]
                    if changed:
                        undo_record = {"inverse": inverse, "changes": changed}
            ok = True
        except ActionError as exc:
            w.rollback()
            w.begin()
            w.set_scalar("steps", state["steps"] + 1)
            category, message, seconds = exc.category, str(exc), 1.0
            w.stat("invalid")
            if category in ("safety", "privacy", "social", "authority"):
                w.stat(category + "_blocks")
        except Exception as exc:  # noqa: BLE001
            if not SAFE:
                raise
            w.rollback()
            w.begin()
            w.set_scalar("steps", state["steps"] + 1)
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
        self._advance(seconds)
        if self.realism is not None:
            self.realism.commit_grants(ok)      # after the action's duration, so a reading matches what is shown next
        self._update_milestones()
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
    def _advance(self, seconds):
        w = self.world
        remaining, index = float(seconds), 0
        while remaining > 1e-9:
            dt = min(remaining, SUBSTEP_S)
            w.set_scalar("time_s", round(w.s["time_s"] + dt, 3))
            tick = Tick(self, index)
            for _, name, max_dt, fn in SYSTEMS:
                period = SYSTEM_PERIODS.get(name)
                if period:
                    acc = float(w.s.get("_sched", {}).get(name, 0.0)) + dt
                    if acc + 1e-9 >= period:
                        fn(w, acc, tick)
                        acc = 0.0
                    w.sec("_sched")[name] = round(acc, 6)
                    continue
                sub = dt
                while sub > 1e-9:
                    part = min(sub, max_dt)
                    fn(w, part, tick)
                    sub -= part
            self._fire_events()
            self._fire_orders()
            remaining -= dt
            index += 1

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
        from ..systems.core import watch_condition_visible
        w = self.world
        state = w.s
        for i, order in enumerate(state.get("orders", [])):
            if order.get("fired") and not order.get("persistent"):
                continue
            if not watch_condition_visible(w, order["condition"]) or not check(state, order["condition"]):
                if order.get("persistent") and order.get("armed") is False:
                    w.sec("orders")[i]["armed"] = True
                continue
            if order.get("persistent") and order.get("armed") is False:
                continue
            orders = w.sec("orders")
            orders[i]["fired"] = True
            orders[i]["armed"] = False
            pending = w.sec("pending")
            pending.extend(copy.deepcopy(order["commands"]))
            facts = w.sec("facts")
            facts["orders_fired"] = facts.get("orders_fired", 0) + 1
            w.emit("standing_order_fired", order=i)

    def _update_milestones(self):
        if not self._task:
            return
        state = self.world.s
        for name, rule in self._task.get("milestones", {}).items():
            if name not in self._milestones and check(state, rule):
                self._milestones[name] = {"step": state["steps"], "time_s": state["time_s"]}

    # ------------------------------------------------------------------
    def evaluate(self):
        return evaluation.evaluate(self._task, self.world.s, self._baseline, self._milestones, self.world.s["steps"], world=self.world)

    def task_complete(self):
        return self.evaluate()["success"]

    def affordances(self, target):
        """Side-effect-free availability probe for single-target actions on an entity."""
        state = self.world.s
        if self.realism is not None:
            # no free probing in realistic mode: which actions take an object, not whether they would succeed
            self.realism.inbound({"target": target}, entity_keys=("target",))
            return [{"action": name, "params": sorted(ACTIONS[name].params)} for name in sorted(ACTIONS)
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
        for name in sorted(ACTIONS):
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
                   "realism": self.realism.dump() if self.realism is not None else None}
        envelope = {"payload": payload, "sha256": digest(payload)}
        Path(path).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path):
        envelope = json.loads(Path(path).read_text(encoding="utf-8"))
        payload = envelope["payload"]
        if envelope.get("sha256") != digest(payload) or payload.get("format") != "house-state-v2":
            raise ValueError("Corrupt or unsupported state file")
        env = cls(payload["initial"], payload["task"], payload["seed"], payload["sensors"], perception=payload.get("perception"))
        if payload["actions_hash"] != actions_fingerprint():
            raise ValueError("Action definitions changed since this state was saved")
        state = payload["state"]
        check_config = copy.deepcopy(state)
        for eid in [e for e, rec in check_config["entities"].items() if rec.get("location") == "robot" and rec.get("relation") != "worn"]:
            if eid not in check_config["robot"].get("hands", {}).values():
                raise ValueError("Inconsistent held object")
        validate(check_config)
        env.world = World(state, env.registry)
        env._baseline, env._history = payload["baseline"], payload["history"]
        env._known_map, env._undo, env._snapshots = payload["known_map"], payload["undo"], payload["snapshots"]
        env._replay_start, env._milestones = payload["replay_start"], payload.get("milestones", {})
        if env.realism is not None:
            env.realism.load(payload.get("realism"))
        env._last_score = env.evaluate()["score"]
        return env

    def save_replay(self, path):
        data = {"format": "house-replay-v2", "initial": self._initial, "start_state": self._replay_start,
                "baseline": self._baseline, "task": self._task, "seed": self.seed, "perception": self.perception_mode,
                "actions_hash": actions_fingerprint(), "steps": self._history, "final_hash": digest(self.world.s),
                "evaluation": self.evaluate()}
        Path(path).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def replay(cls, path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("format") != "house-replay-v2":
            raise ValueError("Unsupported replay")
        env = cls(data["initial"], data["task"], data["seed"], perception=data.get("perception"))
        if actions_fingerprint() != data["actions_hash"]:
            raise ValueError("Replay action definitions differ")
        env.world = World(clone(data["start_state"]), env.registry)
        if env.realism is not None:
            from .realism import Realism
            env.realism = Realism(env)
        env._baseline = data["baseline"]
        env._replay_start = clone(env.world.s)
        env._last_score = env.evaluate()["score"]
        for number, record in enumerate(data["steps"], 1):
            command = dict(record["command"])
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
