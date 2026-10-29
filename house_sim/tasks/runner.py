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

Schema 4 (docs/design/VERIFY_HARDENING.md 5.4, 6.1, 8.1, 8.6, 9.1, 14.2; WP5). build() gives a "schema": 4 task (or any
task when build(..., hardening={...}) asks for it: the as-if builds of the R0 audit and --draft-hardening) a
"hardening" block -- the switch kernel/evaluation.py, ledger.py, monitor.py and HouseEnv.finish read:
  {"settle_s": 60, "lookahead_s": 0, "process": [...], "absent_ok": [...], "envelope": {...}, "protections": {...},
   "labels": {"success": [...], "failure": [...]}, "answers": {name: {..., "value"}}, "track_open": true, ...}
with bindings substituted (an envelope key bound to a list becomes its JSON text, the form ledger.ids_of reads), the
envelope and protections checked by ledger.validate_envelope / validate_protections (SpecError), and the hidden
answers bound (perceive.resolve_answers; a "_branch" answer is the variant's "branch", else its index; also
speech's answer groups). Labelled predicates {"label": L, "pred": P} in success, failure and each phase are unwrapped
to P, their labels recorded in task["labels"] (phase["labels"]) aligned by index (None: unlabelled) and in the block;
a duplicated or non-string label is a SpecError. The "timing" key passes through (bindings substituted). The
existence guard of 8.6 needs nothing here: evaluation applies it to every hardened task. A legacy (schema 2 / 3) task
is built exactly as before.

Runner bookkeeping (VERIFY_HARDENING 9.5, 10.1; WP6 reads it): Runner.trace is one record per env.step the plan
issued -- {"i", "top", "skill", "action", "target", "destination", "args" (true ids), "prov" {argument: "binding:<n>"
| "save:<n>" | "literal" | "skill:<name>"}, "ok", "category", "t0", "t1", "room", "wait"} -- Runner.tops one per
top-level plan step ({"k", "kind", "first", "last", "t0", "t1", "error"}), Runner.saves every save result ({"name",
"top", "from", "value", "after", "frozen"}), Runner.waits every observation-conditioned wait ({"ordinal", "top",
"kind", "max_s", "waited_s", "fixed"}). Runner(..., frozen={name: value}) replaces those save results (B5a, B5b: the
answer substituted at its source), fixed_waits={ordinal: seconds} replaces those conditioned waits by plain waits
(B7 timer, B13), snapshot(k, runner) runs before each top-level step (forking, 10.5). capture_run() runs a plan one
top-level step at a time and hands back the exception that stopped it (14.5: per-plan exception capture).
"""
import copy
import json
import re

from ..generator import generate_house
from ..kernel import camera as camera_mod
from ..kernel.engine import HouseEnv
from ..kernel.errors import SpecError
from ..kernel.plugins import ACTIONS, SKILLS, load_domains
from ..kernel.predicates import MISSING, check, get_path, validate_predicate
from . import skills  # noqa: F401  (registers core reference skills)
from ..kernel.registry import default_registry
from .setup import apply_setup
from .spec import TASK_KEYS, VAR, choose_variant, expand, schema_of, substitute

DEFAULT_BUDGET = {"steps": 400, "time_s": 7200}
PHASE_DEFAULT_BUDGET = {"steps": 150, "time_s": 3600}
MAX_WAIT_S = 604800
ADULT_ROLES = ("adult",)
HARDENED_SCHEMA = 4                  # the schema that turns VERIFY_HARDENING on for a spec (5.4)
SETTLE_DEFAULT_S = 60.0              # 8.1
# spec keys copied into the hardening block (bindings substituted); the rest of the schema-4 keys (protect_map,
# instruction_roles, fungible, informative, free_ok, battery_exempt, ...) are read by the verifier from the spec
HARDENING_SPEC_KEYS = ("envelope", "protections", "absent_ok", "process", "settle_s", "lookahead_s", "track_open",
                       "hold_limits")
CONDITIONED_WAIT_ARGS = ("until", "interrupt_on")     # a wait command that ends on an observation


class PlanError(RuntimeError):
    pass


def episode_budget(chosen, phases=None):
    """The step and time budget a built task runs with (build; VERIFY_HARDENING 17 compares it with the formula):
    the chosen spec's budget over DEFAULT_BUDGET; a multi-phase spec without a budget of its own runs with the sum of
    its phases' budgets (PHASE_DEFAULT_BUDGET for a phase without one) plus the gaps between them. phases: the
    phases as built (default the chosen spec's). {"steps", "time_s"}."""
    phases = [p for p in (chosen.get("phases") or [] if phases is None else phases) if isinstance(p, dict)]
    if phases and "budget" not in chosen:
        steps = sum(int((p.get("budget") or {}).get("steps", PHASE_DEFAULT_BUDGET["steps"])) for p in phases)
        time_s = sum(float((p.get("budget") or {}).get("time_s", PHASE_DEFAULT_BUDGET["time_s"])) for p in phases)
        time_s += sum(float((p.get("start") or {}).get("gap_s", 0.0) or 0.0) for p in phases)
        return {"steps": steps, "time_s": time_s}
    budget = dict(DEFAULT_BUDGET)
    budget.update(chosen.get("budget") or {})
    return budget


def hardened_schema(spec):
    """Whether a spec (or a chosen variant) runs under VERIFY_HARDENING: it declares "schema": 4 or later."""
    return schema_of(spec) >= HARDENED_SCHEMA


def _substitute_keyed(value, bindings):
    """substitute() for a block whose dict keys may be whole "{binding}" selectors bound to a list (envelope tables):
    such a key becomes the JSON text of the list (ledger.ids_of reads it back); values substitute as usual."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if isinstance(key, str):
                whole = VAR.fullmatch(key)
                if whole and whole.group(1) in bindings and not isinstance(bindings[whole.group(1)], str):
                    key = json.dumps(bindings[whole.group(1)])
                else:
                    key = substitute(key, bindings)
            out[key] = _substitute_keyed(item, bindings)
        return out
    if isinstance(value, list):
        return [_substitute_keyed(v, bindings) for v in value]
    return substitute(value, bindings)


def unwrap_labelled(items, where):
    """(bare predicates, labels) of a success / failure list: {"label": L, "pred": P} items become P and L is recorded
    at their index (None for a bare predicate). SpecError for a non-string, empty or repeated label (14.2)."""
    bare, labels, seen = [], [], set()
    for index, item in enumerate(items or ()):
        if isinstance(item, dict) and "label" in item and "pred" in item:
            if set(item) - {"label", "pred"}:
                raise SpecError("{} item {}: a labelled predicate has only label and pred".format(where, index))
            label = item["label"]
            if not isinstance(label, str) or not label.strip() or ":" in label:
                raise SpecError("{} item {}: a label is a non-empty string without ':' ({!r})".format(where, index,
                                                                                                   label))
            if label in seen:
                raise SpecError("{}: label {!r} is used twice".format(where, label))
            seen.add(label)
            bare.append(item["pred"])
            labels.append(label)
        else:
            bare.append(item)
            labels.append(None)
    return bare, labels


def branch_value(chosen, variant):
    """The "_branch" answer of a seed (9.1): the chosen variant's "branch" value, else the variant index (None for a
    spec without variants)."""
    if chosen.get("branch") is not None:
        return chosen["branch"]
    return variant


def bind_answers(raw, bindings, chosen=None, variant=None):
    """The spec's "answers" with bindings resolved (perceive.resolve_answers: "{name}" references replaced, "value"
    from "bind"); a "_branch" answer takes branch_value. Answers whose binding is missing keep no "value"."""
    from .perceive import resolve_answers
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise SpecError("answers is an object {name: {...}}")
    extra = dict(bindings)
    extra["_branch"] = branch_value(chosen or {}, variant)
    out = resolve_answers(raw, extra)
    for name, spec in out.items():
        if spec.get("bind") == "_branch":
            spec["value"] = copy.deepcopy(extra["_branch"])
    return out


def _harden(task, chosen, bindings, variant, override):
    """Turn a built task into a schema-4 task (module docstring): unwrap labelled predicates, record the labels, add
    the hardening block. override: keys that replace the block's (the verifier's measured reference_resources /
    reference_open_s, a triggered lookahead_s, an as-if audit's drafts)."""
    labels = {}
    for key in ("success", "failure"):
        task[key], labels[key] = unwrap_labelled(task.get(key) or [], key)
    for phase in task.get("phases") or []:
        phase_labels = {}
        for key in ("success", "failure"):
            phase[key], phase_labels[key] = unwrap_labelled(phase.get(key) or [], "phase {} {}".format(
                phase.get("id"), key))
        phase["labels"] = phase_labels
    task["labels"] = labels
    block = {"settle_s": SETTLE_DEFAULT_S, "lookahead_s": 0.0, "process": [], "absent_ok": [], "envelope": {},
             "protections": {}, "track_open": True}
    for key in HARDENING_SPEC_KEYS:
        if key in chosen:
            block[key] = _substitute_keyed(chosen[key], bindings)
    block["labels"] = copy.deepcopy(labels)
    block["answers"] = bind_answers(chosen.get("answers"), bindings, chosen, variant)
    for key, value in (override or {}).items():
        block[key] = copy.deepcopy(value)
    from ..kernel import ledger
    problems = ledger.validate_envelope(block.get("envelope")) + ledger.validate_protections(block.get("protections"))
    if problems:
        raise SpecError("hardening: " + "; ".join(problems[:5]))
    for key in ("process", "absent_ok"):
        if not isinstance(block.get(key), list) or not all(isinstance(x, str) for x in block[key]):
            raise SpecError("{} lists success labels".format(key))
    task["hardening"] = block
    if block["answers"]:
        task["answers"] = copy.deepcopy(block["answers"])
    return task


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


def build(spec, seed=0, registry=None, camera=None, perception=None, gen=None, hardening=None, audit=False):
    """Return (config, task, oracle) for one seed. oracle holds bindings and plans. camera / perception: the run's
    modes when forced (None: the spec's own); the setup's geometric camera ops apply only in a geometric run.
    gen: force the house generation (1 or 2; None: the spec's house.gen, else 2 for schema-3 specs, else 1).
    hardening: None builds a schema-4 spec with its hardening block and any other spec as before; a dict builds the
    task with the schema-4 semantics whatever its schema, the dict's keys replacing the block's (module docstring).
    audit: the setup's audit fast path (tasks/setup.py apply_setup(..., audit=True): bindings and placements only,
    VERIFY_HARDENING 9.2) for the seed and audit builds of the verifier."""
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
    if audit:
        bindings = apply_setup(config, chosen.get("setup", []), registry, seed, chosen, camera=camera,
                               perception=perception, audit=True)
    else:
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
            budget = episode_budget(chosen, phases)
            task["step_budget"], task["time_budget_s"] = budget["steps"], budget["time_s"]
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
    hard = hardening is not None or hardened_schema(chosen)
    if hard:
        if "timing" in chosen and "timing" not in task:
            task["timing"] = substitute(chosen["timing"], bindings)
        _harden(task, chosen, bindings, variant, hardening if isinstance(hardening, dict) else None)
    if chosen.get("strict_blocks", True):
        task["failure"] = list(task["failure"]) + [
            {"path": "stats.safety_blocks", "op": "gt", "value": 0},
            {"path": "stats.privacy_blocks", "op": "gt", "value": 0},
            {"path": "stats.authority_blocks", "op": "gt", "value": 0}]
        if hard:
            task["labels"]["failure"] = list(task["labels"]["failure"]) + [None, None, None]
            task["hardening"]["labels"] = copy.deepcopy(task["labels"])
    for rule in task["success"] + task["failure"]:
        validate_predicate(rule)
    for phase in task.get("phases") or []:
        for rule in (phase.get("success") or []) + (phase.get("failure") or []):
            validate_predicate(rule)
    _check_plan_actions(chosen)
    oracle = {"bindings": bindings, "reference": chosen.get("reference", []), "wrong": chosen.get("wrong", []),
              "variant": variant, "seed": seed}
    if hard:
        oracle["branch"] = branch_value(chosen, variant)
        oracle["answers"] = copy.deepcopy(task["hardening"]["answers"])
    return config, task, oracle


def _plan_steps(steps):
    for step in steps or []:
        if isinstance(step, dict):
            yield step
            for key in ("steps",):
                if isinstance(step.get(key), list):
                    yield from _plan_steps(step[key])


def _check_plan_actions(chosen):
    """Every literal "do" action name in the chosen variant's reference and wrong plans is a registered action
    (phase-1 red team: a wrong plan naming an unknown action was refused at every step and still counted as
    evidence). A templated name ("{action}") is checked when it runs. Raises SpecError."""
    plans = [("reference", chosen.get("reference") or [])]
    plans += [("wrong plan {!r}".format(w.get("name", i)), w.get("plan") or [])
              for i, w in enumerate(chosen.get("wrong") or []) if isinstance(w, dict)]
    for where, steps in plans:
        for step in _plan_steps(steps):
            name = step.get("do")
            if isinstance(name, str) and "{" not in name and name.strip().lower() not in ACTIONS:
                raise SpecError("{}: unknown action {!r}".format(where, name))


class Runner:
    def __init__(self, env, bindings, strict=True, frozen=None, fixed_waits=None, record=True, snapshot=None):
        """frozen: {save name: value} used instead of the observation (or the skill's result) when that save runs;
        fixed_waits: {ordinal: seconds} replacing the ordinal-th observation-conditioned wait by a plain wait;
        record: keep the trace (module docstring); snapshot(k, runner): called before each top-level step."""
        self.env, self.bindings, self.strict = env, dict(bindings), strict
        self.last = None
        self.failures = []
        self.refusals = []              # (action, category, message, malformed) of every refused step (verify.py)
        self.refusal_tops = []          # the top-level step index of each refusal, aligned with refusals
        self.privileged_waits = 0       # schema-2 wait_until steps (they poll hidden state; migration debt)
        self.frozen = dict(frozen or {})
        self.fixed_waits = {int(k): float(v) for k, v in (fixed_waits or {}).items()}
        self.snapshot = snapshot
        self.trace = [] if record else None
        self.tops, self.saves, self.waits = [], [], []
        self._top = None                # the top-level step being run (None outside run_top / top_step)
        self._skill = None              # {"name", "args", "prov"} of the skill being run
        self._prov = None               # provenance of the "do" step being run
        self._saved = set()             # names bound by save steps so far (provenance "save:<name>")
        self._conditioned = 0           # observation-conditioned waits seen so far

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
        entry = self._trace_entry(action, target, destination, args) if self.trace is not None else None
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
        if entry is not None:
            self._trace_done(entry, result)
        if expect == "fail":
            if result["ok"]:
                raise PlanError("{} {} was expected to be refused but succeeded".format(action, target))
            return result
        if not result["ok"]:
            self.failures.append("{} {}: {}".format(action, target, result["message"]))
            # malformed: refused for its form (an unknown action or arguments it does not take): never attempted
            self.refusals.append((action, result.get("category") or "", str(result["message"]),
                                  bool(getattr(self.env, "last_step_malformed", False))))
            self.refusal_tops.append(self._top)
            if self.strict:
                raise PlanError("{} {} {} failed: {}".format(action, target, destination, result["message"]))
        return result

    def skill(self, *skill_name, **args):
        name = skill_name[0]
        if name not in SKILLS:
            raise SpecError("Unknown skill " + name)
        fn = SKILLS[name]
        problem = _skill_arguments_problem(fn, args)
        if problem:
            # a plan step that names an argument the skill does not take (or misses one) is the spec's error, not a
            # harness error inside the skill (VERIFY_HARDENING 14.5)
            raise SpecError("skill {}: {}".format(name, problem))
        return fn(self, **args)

    # ----- the trace (module docstring; VERIFY_HARDENING 9.5) --------
    def _trace_entry(self, action, target, destination, args):
        state = self.state
        entry = {"i": len(self.trace), "top": self._top, "skill": self._skill["name"] if self._skill else None,
                 "action": str(action), "target": copy.deepcopy(target), "destination": copy.deepcopy(destination),
                 "args": copy.deepcopy(args), "t0": round(float(state.get("time_s", 0.0) or 0.0), 3)}
        values = dict(args, target=target, destination=destination)
        if self._skill is not None:
            # a command a skill issued: an argument equal to one of the skill's arguments inherits its provenance,
            # anything else the skill computed (routes, handles, the place it chose)
            prov = {}
            for key, value in values.items():
                if value in ("", None):
                    continue
                source = "skill:" + self._skill["name"]
                for name, given in self._skill["args"].items():
                    if given == value and name in self._skill["prov"]:
                        source = self._skill["prov"][name]
                        break
                prov[key] = source
            entry["prov"] = prov
        else:
            entry["prov"] = {k: v for k, v in (self._prov or {}).items() if values.get(k) not in ("", None)}
        if str(action) == "wait":
            conditioned = any(args.get(k) for k in CONDITIONED_WAIT_ARGS)
            entry["wait"] = {"requested_s": args.get("seconds"), "conditioned": bool(conditioned)}
        return entry

    def _trace_done(self, entry, result):
        state = self.state
        entry["ok"] = bool(result.get("ok"))
        entry["category"] = result.get("category") or ""
        entry["t1"] = round(float(state.get("time_s", 0.0) or 0.0), 3)
        entry["steps"] = state.get("steps", 0)
        entry["room"] = (state.get("robot") or {}).get("room")
        if "wait" in entry:
            entry["wait"]["waited_s"] = round(entry["t1"] - entry["t0"], 3)
        self.trace.append(entry)

    def _provenance(self, raw):
        """{argument: source} of a raw (unsubstituted) step: a whole "{name}" is that binding or save, a text naming
        bindings their first name's source, anything else a literal."""
        out = {}
        for key, value in (raw or {}).items():
            if key in ("do", "skill", "expect", "save", "from", "note"):
                continue
            names = VAR.findall(value) if isinstance(value, str) else VAR.findall(json.dumps(value)) \
                if isinstance(value, (list, dict)) else []
            if not names:
                out[key] = "literal"
                continue
            name = names[0]
            out[key] = ("save:" if name in self._saved else "binding:") + name
        return out

    def _bind_save(self, name, value, source):
        frozen = name in self.frozen
        if frozen:
            value = copy.deepcopy(self.frozen[name])
        self.bindings[name] = value
        self._saved.add(name)
        self.saves.append({"name": name, "top": self._top, "from": source, "value": copy.deepcopy(value),
                           "after": (len(self.trace) - 1) if self.trace is not None else None, "frozen": frozen})
        return value

    def _conditioned_wait(self, kind, max_s, run):
        """Run an observation-conditioned wait (wait_until, wait_for, a wait command with until / interrupt_on),
        or the fixed wait that replaces it (fixed_waits); record it in self.waits."""
        ordinal = self._conditioned
        self._conditioned += 1
        t0 = float(self.state.get("time_s", 0.0) or 0.0)
        fixed = ordinal in self.fixed_waits
        result = None
        if fixed:
            seconds = max(1, min(MAX_WAIT_S, int(round(self.fixed_waits[ordinal]))))
            strict, self.strict = self.strict, False
            try:
                result = self.do("wait", seconds=seconds)
            finally:
                self.strict = strict
        else:
            result = run()
        waited = float(self.state.get("time_s", 0.0) or 0.0) - t0
        self.waits.append({"ordinal": ordinal, "top": self._top, "kind": kind, "max_s": max_s,
                           "waited_s": round(waited, 3), "fixed": fixed})
        return result

    # ----- execution --------------------------------------------------
    def top_step(self, k, raw):
        """Run top-level plan step k (recorded in self.tops; the snapshot hook runs first). Exceptions propagate
        after the step's record notes them."""
        if self.snapshot is not None:
            self.snapshot(k, self)
        state = self.state
        record = {"k": k, "kind": _step_kind(raw), "first": len(self.trace) if self.trace is not None else None,
                  "t0": round(float(state.get("time_s", 0.0) or 0.0), 3), "error": None}
        self.tops.append(record)
        previous, self._top = self._top, k
        try:
            self.run([raw])
        except Exception as exc:                # noqa: BLE001  (noted, then re-raised)
            record["error"] = "{}: {}".format(type(exc).__name__, exc)[:300]
            raise
        finally:
            self._top = previous
            record["last"] = (len(self.trace) - 1) if self.trace is not None else None
            record["t1"] = round(float(self.state.get("time_s", 0.0) or 0.0), 3)
        return record

    def run_top(self, plan):
        """Run a whole plan one top-level step at a time (top_step)."""
        for k, raw in enumerate(plan or []):
            self.top_step(k, raw)

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
                self._prov = self._provenance(raw) if self.trace is not None else None
                try:
                    if step["do"] == "wait" and any(args.get(k) for k in CONDITIONED_WAIT_ARGS):
                        self._conditioned_wait("wait", args.get("seconds"),
                                               lambda: self.do(step["do"], expect=step.get("expect"), **args))
                    else:
                        self.do(step["do"], expect=step.get("expect"), **args)
                finally:
                    self._prov = None
                if "save" in step:
                    self._save(step["save"], step.get("from", "last_answer.value"))
            elif "skill" in step:
                args = {k: v for k, v in step.items() if k not in ("skill", "note", "save", "from")}
                outer = self._skill
                if self.trace is not None and outer is None:
                    self._skill = {"name": step["skill"], "args": args, "prov": self._provenance(raw)}
                try:
                    out = self.skill(step["skill"], **args)
                except PlanError:
                    if self.strict:
                        raise
                    out = None
                finally:
                    self._skill = outer
                if "save" in step:
                    self._bind_save(step["save"], out, "skill:" + str(step["skill"]))
            elif "save" in step:
                self._save(step["save"], step.get("from", "last_answer.value"))
            elif "save_state" in step:
                self._bind_save(step["save_state"], copy.deepcopy(get_path(self.state, step["path"])),
                                "state:" + str(step["path"]))
            elif "wait_until" in step:
                self._conditioned_wait("wait_until", step.get("max_s", 3600), lambda: self._wait_until(step))
            elif "wait_for" in step:
                kinds = step["wait_for"]
                if not isinstance(kinds, list) or not kinds:
                    raise SpecError("wait_for lists notice kinds")
                seconds = max(1, min(MAX_WAIT_S, int(step.get("max_s", 3600))))
                self._conditioned_wait("wait_for", seconds,
                                       lambda: self.do("wait", seconds=seconds, interrupt_on=list(kinds)))
            elif "phase_done" in step:
                result = self.env.phase_done()
                self.last = result
                if self.trace is not None:
                    t = round(float(self.state.get("time_s", 0.0) or 0.0), 3)
                    self.trace.append({"i": len(self.trace), "top": self._top, "skill": None, "action": "phase_done",
                                       "target": "", "destination": "", "args": {}, "prov": {}, "t0": t, "t1": t,
                                       "ok": bool(result["ok"]), "category": result.get("category") or "",
                                       "steps": self.state.get("steps", 0),
                                       "room": (self.state.get("robot") or {}).get("room")})
                if not result["ok"]:
                    self.failures.append("phase_done: " + result["message"])
                    if self.strict:
                        raise PlanError("phase_done failed: " + result["message"])
            else:
                raise SpecError("Unknown plan step: {}".format(step))

    def _wait_until(self, step):
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
            return result
        self.privileged_waits += 1
        while not check(self.state, step["wait_until"]):
            if waited >= limit:
                if self.strict:
                    raise PlanError("wait_until timed out: {}".format(step["wait_until"]))
                break
            self.do("wait", seconds=poll)
            waited += poll
        return self.last

    def _save(self, name, path):
        if name in self.frozen:
            return self._bind_save(name, None, "observe:" + str(path))
        obs = self.env.observe()
        if getattr(self.env, "realism", None) is not None:
            obs = self.env.realism.from_handles(obs)
        value = get_path(obs, path)
        if value is MISSING or value is None:
            value = get_path(self.last or {}, path)
        if value is MISSING:
            raise PlanError("Nothing observable at " + path)
        return self._bind_save(name, copy.deepcopy(value), "observe:" + str(path))


_SIGNATURES = {}


def _skill_arguments_problem(fn, args):
    """None when a skill takes these keyword arguments, else the binding error's text."""
    sig = _SIGNATURES.get(fn)
    if sig is None:
        import inspect
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError):
            sig = False
        _SIGNATURES[fn] = sig
    if sig is False:
        return None
    try:
        sig.bind(None, **args)
    except TypeError as exc:
        return str(exc)
    return None


def _step_kind(raw):
    for key in ("do", "skill", "save", "save_state", "wait_until", "wait_for", "phase_done", "foreach", "repeat"):
        if isinstance(raw, dict) and key in raw:
            return key
    return "unknown"


def run_plan(env, plan, bindings, strict=True, **kwargs):
    """Run a plan (strict: any refusal raises PlanError) one top-level step at a time; kwargs go to Runner."""
    runner = Runner(env, bindings, strict=strict, **kwargs)
    runner.run_top(plan)
    return runner


def capture_run(env, plan, bindings, strict=False, runner=None, **kwargs):
    """Run a plan one top-level step at a time and capture the exception that stops it (VERIFY_HARDENING 14.5):
    (runner, top-level steps reached, exception or None). runner: a Runner (subclass) to use instead of a new one."""
    runner = runner if runner is not None else Runner(env, bindings, strict=strict, **kwargs)
    reached = 0
    try:
        for k, raw in enumerate(plan or []):
            runner.top_step(k, raw)
            reached += 1
    except Exception as exc:                # noqa: BLE001  (any exception means the plan did not run fully)
        return runner, reached, exc
    return runner, reached, None


def make_env(config, task, seed, fast=True, perception=None, camera=None):
    """perception: None -> the task's "perception" field (default oracle); "oracle" | "realistic" to force.
    camera: None -> the task's "camera" field (default room); "room" | "geometric" to force."""
    mode = perception or (task or {}).get("perception") or "oracle"
    return HouseEnv(copy.deepcopy(config), copy.deepcopy(task), seed, trace=not fast,
                    observe_on_step=not fast or mode == "realistic", perception=mode, camera=camera)
