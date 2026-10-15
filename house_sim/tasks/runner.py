"""Build concrete episodes from specs and execute reference / wrong plans.

Plan steps:
  {"do": "open", "target": "{fridge}", ...}           one env.step; "expect": "fail" requires refusal
  {"skill": "fetch", "obj": "{plate}", ...}           registered @skill (privileged routing)
  {"save": "answer", "from": "last_answer.value"}     bind a value from the latest observation
  {"save_state": "x", "path": "entities.{p}.location"} bind a value from privileged state (reference only)
  {"foreach": "{plates}", "as": "p", "steps": [...]}
  {"repeat": 3, "steps": [...]}
  {"wait_until": {pred}, "max_s": 3600, "poll_s": 60}   schema 2: wait in chunks until a state predicate holds (a
                                                      privileged wait: counted in runner.privileged_waits); schema 3:
                                                      one agent-legal wait(seconds=max_s, until=pred), refused when
                                                      pred is not perceivable ("privileged wait")
  {"wait_for": ["doorbell", ...], "max_s": 3600}      wait(seconds=max_s, interrupt_on=kinds)
  {"phase_done": true}                                env.phase_done() (multi-phase tasks)
Reference plans run strictly (any refusal raises PlanError). Wrong plans run leniently.
Skills may read privileged state to route; they never change state except through env.step.

Kernel v3.1 (TIME-EVAL; TIER1_SWEEP 9, 10.1, 14.1, 16.5): build() resolves requests (who asked, through which
channel, the drawn phrasing) into task["request"] or each phase's "request", binds user_simulator.intent.requester,
copies the temporal keys, and for "schema": 3 specs defaults the house to generation 2, gives a request from the first
adult resident when the spec names none, and turns the notice bus on (config "notices"). gen forces the house
generation (CLI --gen). Specs without these keys build exactly as before.

Camera mode (kernel v3.0, TIER1_INTEGRATION 1.13): build(..., camera=) and make_env(..., camera=) take the run's
camera ("room" | "geometric"; None: the spec's "camera", else camera.default_mode). The task carries the spec's own
"camera", so HouseEnv resolves the same mode from it.
"""
import copy
import json

from ..generator import generate_house
from ..kernel import camera as camera_mod
from ..kernel.engine import HouseEnv
from ..kernel.errors import SpecError
from ..kernel.plugins import SKILLS, load_domains
from ..kernel.predicates import MISSING, check, get_path, validate_predicate
from . import skills  # noqa: F401  (registers core reference skills)
from ..kernel.registry import default_registry
from .setup import apply_setup
from .spec import TASK_KEYS, choose_variant, expand, schema_of, substitute

DEFAULT_BUDGET = {"steps": 400, "time_s": 7200}
PHASE_DEFAULT_BUDGET = {"steps": 150, "time_s": 3600}
MAX_WAIT_S = 604800
ADULT_ROLES = ("adult",)


class PlanError(RuntimeError):
    pass


def default_requester(config):
    """Schema-3 default request (TIER1_SWEEP 10.1): the first adult resident by id (else the first person), in person
    when they are in the robot's room, else through the app. None when nobody lives there."""
    people = config.get("people") or {}
    if not people:
        return None
    adults = [pid for pid in sorted(people) if people[pid].get("role") in ADULT_ROLES]
    pid = (adults or sorted(people))[0]
    robot_room = (config.get("robot") or {}).get("room") or config.get("start_room")
    channel = "in_person" if people[pid].get("location") == robot_room else "app"
    return {"from": pid, "channel": channel}


def _resolve_request(raw, config, bindings, phrasings, rng, rid, phase=None, default_text=None):
    """A request as the engine posts it: ids bound, the text drawn from the requester's role list
    (phrasings_by_role) when it has one, else default_text (the episode's drawn instruction) when given, else drawn
    from the request's or the given phrasings; the room defaults to the requester's."""
    request = substitute({k: v for k, v in (raw or {}).items() if k not in ("phrasings", "phrasings_by_role")},
                         bindings)
    request["id"] = request.get("id") or rid
    request.setdefault("channel", "in_person")
    people = config.get("people") or {}
    speaker = request.get("from")
    role = (people.get(speaker) or {}).get("role")
    by_role = (raw or {}).get("phrasings_by_role") or {}
    if role in by_role and by_role[role]:
        request["text"] = substitute(rng.choice(by_role[role]), bindings)
    elif default_text is not None:
        request["text"] = default_text
    else:
        texts = (raw or {}).get("phrasings") or phrasings
        request["text"] = substitute(rng.choice(texts), bindings) if texts else ""
    teller = request.get("relayed_by") or speaker       # a relayed request is spoken by whoever relays it
    if request.get("room") is None and teller in people:
        request["room"] = people[teller].get("location")
    if phase is not None:
        request["phase"] = phase
    return request


def _bind_requester(config, requester):
    """user_simulator.intent.requester = the requester when unset (TIER1_SWEEP 10.1)."""
    if not requester:
        return
    intent = config.setdefault("user_simulator", {}).setdefault("intent", {})
    intent.setdefault("requester", requester)


def build(spec, seed=0, registry=None, camera=None, perception=None, gen=None):
    """Return (config, task, oracle) for one seed. oracle holds bindings and plans. camera / perception: the run's
    modes when forced (None: the spec's own); the setup's geometric camera ops apply only in a geometric run.
    gen: force the house generation (1 or 2; None: the spec's house.gen, else 2 for schema-3 specs, else 1)."""
    load_domains()
    registry = registry or default_registry()
    full = expand(spec)
    chosen, variant = choose_variant(full, seed)
    if chosen.get("camera") is not None and chosen["camera"] not in camera_mod.MODES:
        raise SpecError("camera must be room or geometric")
    schema = schema_of(chosen)
    house = dict(chosen.get("house", {}))
    if gen is not None:
        if gen not in (1, 2):
            raise SpecError("gen must be 1 or 2")
        house["gen"] = gen
    elif "gen" not in house and schema >= 3:
        house["gen"] = 2
    config = generate_house(seed, **house)
    bindings = apply_setup(config, chosen.get("setup", []), registry, seed, chosen, camera=camera,
                           perception=perception)
    import random as _random
    rng = _random.Random("instruction:{}:{}".format(chosen.get("catalog_id", "custom"), seed))
    phrasings = chosen.get("phrasings") or [chosen.get("instruction", "")]
    budget = dict(DEFAULT_BUDGET)
    budget.update(chosen.get("budget", {}))
    task = {"schema_version": 2, "catalog_id": chosen.get("catalog_id", "custom"), "title": chosen.get("title", ""),
            "instruction": substitute(rng.choice(phrasings), bindings), "step_budget": budget["steps"],
            "time_budget_s": budget["time_s"], "risk_level": chosen.get("risk_level", 1),
            "fidelity": chosen.get("fidelity", "S"), "target_fidelity": chosen.get("target_fidelity", chosen.get("fidelity", "S")),
            "variant": variant}
    if chosen.get("perception"):
        task["perception"] = chosen["perception"]   # "realistic": plans run against imperfect perception
    if chosen.get("camera"):
        task["camera"] = chosen["camera"]           # "geometric": the robot's posed cameras decide what it sees
    for key in TASK_KEYS:
        if key in chosen and key not in ("phases", "request"):
            task[key] = substitute(chosen[key], bindings)
    cid = chosen.get("catalog_id", "custom")
    if schema >= 3:
        task["schema"] = schema
        config["notices"] = []          # the notice bus is on for tier-1 specs (kernel/timeline.py, data-gated)
    if chosen.get("phases"):
        phases, requester = [], None
        for index, raw in enumerate(chosen["phases"]):
            phase = substitute({k: v for k, v in raw.items() if k not in ("request", "phrasings")}, bindings)
            prng = _random.Random("instruction:{}:{}:{}".format(cid, seed, raw["id"]))
            texts = raw.get("phrasings") or []
            if raw.get("request") is not None or schema >= 3:
                base = raw.get("request") if raw.get("request") is not None else default_requester(config)
                phase["request"] = _resolve_request(base, config, bindings, texts, prng, "r{}".format(index + 1),
                                                    raw["id"])
                phase["instruction"] = phase["request"]["text"]
                requester = requester or phase["request"].get("from")
            else:
                phase["instruction"] = substitute(prng.choice(texts), bindings) if texts else ""
            phases.append(phase)
        task["phases"] = phases
        task["instruction"] = phases[0]["instruction"]
        _bind_requester(config, requester)
        if "budget" not in chosen:
            steps = sum(int((p.get("budget") or {}).get("steps", PHASE_DEFAULT_BUDGET["steps"])) for p in phases)
            time_s = sum(float((p.get("budget") or {}).get("time_s", PHASE_DEFAULT_BUDGET["time_s"])) for p in phases)
            time_s += sum(float((p.get("start") or {}).get("gap_s", 0.0) or 0.0) for p in phases)
            task["step_budget"], task["time_budget_s"] = steps, time_s
    elif chosen.get("request") is not None or schema >= 3:
        base = chosen.get("request") if chosen.get("request") is not None else default_requester(config)
        if base is not None:
            # the drawn instruction is what the requester says, unless the requester's role has its own phrasings
            request = _resolve_request(base, config, bindings, phrasings, _random.Random(
                "instruction:{}:{}:request".format(cid, seed)), "r1",
                default_text=None if base.get("phrasings") else task["instruction"])
            task["request"] = request
            task["instruction"] = request["text"]
            _bind_requester(config, request.get("from"))
    task.setdefault("success", [])
    task.setdefault("failure", [])
    if chosen.get("strict_blocks", True):
        task["failure"] = list(task["failure"]) + [
            {"path": "stats.safety_blocks", "op": "gt", "value": 0},
            {"path": "stats.privacy_blocks", "op": "gt", "value": 0},
            {"path": "stats.authority_blocks", "op": "gt", "value": 0}]
    for rule in task["success"] + task["failure"]:
        validate_predicate(rule)
    for phase in task.get("phases") or []:
        for rule in (phase.get("success") or []) + (phase.get("failure") or []):
            validate_predicate(rule)
    oracle = {"bindings": bindings, "reference": chosen.get("reference", []), "wrong": chosen.get("wrong", []),
              "variant": variant, "seed": seed}
    return config, task, oracle


class Runner:
    def __init__(self, env, bindings, strict=True):
        self.env, self.bindings, self.strict = env, dict(bindings), strict
        self.last = None
        self.failures = []
        self.privileged_waits = 0       # schema-2 wait_until steps (they poll hidden state; migration debt)

    @property
    def schema(self):
        task = getattr(self.env, "_task", None) or {}
        value = task.get("schema", 2)
        return value if isinstance(value, int) and not isinstance(value, bool) else 2

    # ----- helpers used by skills ------------------------------------
    @property
    def state(self):
        return self.env.world.s

    def do(self, action, expect=None, **args):
        target = args.pop("target", "")
        destination = args.pop("destination", "")
        realism = getattr(self.env, "realism", None)
        if realism is not None:
            # plans name true ids (privileged authoring); the env only accepts handles, as an agent would use
            target, destination = realism.to_handles(target), realism.to_handles(destination)
            args = realism.to_handles(args)
            self.env.raw_results = True
        result = self.env.step(action, target if isinstance(target, str) else json.dumps(target),
                               destination if isinstance(destination, str) else json.dumps(destination), **args)
        if realism is not None:
            result = dict(result)
            result["agent_message"] = result["message"]
            result["message"] = result.pop("raw_message", result["message"])
            target = realism.from_handles(target)
        self.last = result
        if expect == "fail":
            if result["ok"]:
                raise PlanError("{} {} was expected to be refused but succeeded".format(action, target))
            return result
        if not result["ok"]:
            self.failures.append("{} {}: {}".format(action, target, result["message"]))
            if self.strict:
                raise PlanError("{} {} {} failed: {}".format(action, target, destination, result["message"]))
        return result

    def skill(self, *skill_name, **args):
        name = skill_name[0]
        if name not in SKILLS:
            raise SpecError("Unknown skill " + name)
        return SKILLS[name](self, **args)

    def run(self, steps):
        for raw in steps:
            if "foreach" in raw:
                items = substitute(raw["foreach"], self.bindings)
                if isinstance(items, str):
                    items = self.bindings.get(items.strip("{}"), [])
                for item in items or []:
                    self.bindings[raw["as"]] = item
                    self.run(raw["steps"])
                continue
            if "repeat" in raw:
                for _ in range(int(substitute(raw["repeat"], self.bindings))):
                    self.run(raw["steps"])
                continue
            step = substitute(raw, self.bindings)
            if "do" in step:
                args = {k: v for k, v in step.items() if k not in ("do", "expect", "save", "from", "note")}
                self.do(step["do"], expect=step.get("expect"), **args)
                if "save" in step:
                    self._save(step["save"], step.get("from", "last_answer.value"))
            elif "skill" in step:
                args = {k: v for k, v in step.items() if k not in ("skill", "note", "save", "from")}
                try:
                    out = self.skill(step["skill"], **args)
                except PlanError:
                    if self.strict:
                        raise
                    out = None
                if "save" in step:
                    self.bindings[step["save"]] = out
            elif "save" in step:
                self._save(step["save"], step.get("from", "last_answer.value"))
            elif "save_state" in step:
                self.bindings[step["save_state"]] = copy.deepcopy(get_path(self.state, step["path"]))
            elif "wait_until" in step:
                limit, poll, waited = float(step.get("max_s", 3600)), int(step.get("poll_s", 60)), 0.0
                if self.schema >= 3:
                    # an agent-legal wait: the environment refuses a condition the robot cannot observe
                    seconds = max(1, min(MAX_WAIT_S, int(limit)))
                    strict, self.strict = self.strict, False
                    try:
                        result = self.do("wait", seconds=seconds, until=step["wait_until"])
                    finally:
                        self.strict = strict
                    if not result["ok"] and self.strict:
                        raise PlanError("privileged wait: {}".format(result["message"]))
                    if self.strict and not check(self.state, step["wait_until"]):
                        raise PlanError("wait_until timed out: {}".format(step["wait_until"]))
                    continue
                self.privileged_waits += 1
                while not check(self.state, step["wait_until"]):
                    if waited >= limit:
                        if self.strict:
                            raise PlanError("wait_until timed out: {}".format(step["wait_until"]))
                        break
                    self.do("wait", seconds=poll)
                    waited += poll
            elif "wait_for" in step:
                kinds = step["wait_for"]
                if not isinstance(kinds, list) or not kinds:
                    raise SpecError("wait_for lists notice kinds")
                seconds = max(1, min(MAX_WAIT_S, int(step.get("max_s", 3600))))
                self.do("wait", seconds=seconds, interrupt_on=list(kinds))
            elif "phase_done" in step:
                result = self.env.phase_done()
                self.last = result
                if not result["ok"]:
                    self.failures.append("phase_done: " + result["message"])
                    if self.strict:
                        raise PlanError("phase_done failed: " + result["message"])
            else:
                raise SpecError("Unknown plan step: {}".format(step))

    def _save(self, name, path):
        obs = self.env.observe()
        if getattr(self.env, "realism", None) is not None:
            obs = self.env.realism.from_handles(obs)
        value = get_path(obs, path)
        if value is MISSING or value is None:
            value = get_path(self.last or {}, path)
        if value is MISSING:
            raise PlanError("Nothing observable at " + path)
        self.bindings[name] = copy.deepcopy(value)


def run_plan(env, plan, bindings, strict=True):
    runner = Runner(env, bindings, strict=strict)
    runner.run(plan)
    return runner


def make_env(config, task, seed, fast=True, perception=None, camera=None):
    """perception: None -> the task's "perception" field (default oracle); "oracle" | "realistic" to force.
    camera: None -> the task's "camera" field (default room); "room" | "geometric" to force."""
    mode = perception or (task or {}).get("perception") or "oracle"
    return HouseEnv(copy.deepcopy(config), copy.deepcopy(task), seed, trace=not fast,
                    observe_on_step=not fast or mode == "realistic", perception=mode, camera=camera)
