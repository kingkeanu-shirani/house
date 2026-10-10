"""Agents evaluated by the harness (docs/EVAL_HARNESS.md, docs/EVAL_CONTRACTS.md section B).

An agent sees only what a deployed robot policy would see:

    reset(instruction, observation, action_catalog)    once per episode
    act(observation, last_result) -> command dict       once per step; {"action": "done"} ends the episode

`last_result` is None on the first step and afterwards contains ONLY {"ok", "message", "category"} of the previous
step. The harness never passes evaluation, reward, terminated/truncated, raw (untranslated) messages, the spec, the
seed or privileged state to an agent. The one exception is ScriptedOracle (privileged = True, labelled in every
result): the harness calls its prepare(spec, episode, perception) so it can expand the spec's reference plan.

Budgets: the harness hands every agent a Budget (bind_budget) with the per-episode token/cost caps and the run-wide
CostLedger; agents that spend tokens call budget.check(...) before a request and budget.record(...) after it.
"""
import copy
import inspect
import json
import random
import re
import threading

DEFAULT_ANTHROPIC_MODEL = "claude-opus-5-5"
DONE = "done"
# keys an evaluator or the privileged runner would produce; never part of what an agent receives
EVALUATOR_KEYS = frozenset({"evaluation", "reward", "terminated", "truncated", "raw_message", "agent_message"})
RESULT_KEYS = ("ok", "message", "category")


class BudgetExceeded(RuntimeError):
    """Raised (by an agent or the harness) when a cap would be or has been exceeded. reason is one of
    "token_cap", "cost_cap" (per episode) or "run_cost_cap" (whole run)."""

    def __init__(self, reason, detail=""):
        super().__init__("{}: {}".format(reason, detail) if detail else reason)
        self.reason = reason
        self.detail = detail


class CostLedger:
    """Run-wide cost accounting shared by parallel episodes (thread-safe). A request reserves its worst-case cost
    first, so concurrent workers cannot overshoot the cap together; the reservation is replaced by the actual
    cost afterwards."""

    def __init__(self, max_cost_usd=None):
        self.max_cost_usd = None if max_cost_usd is None else float(max_cost_usd)
        self.spent = 0.0
        self.reserved = 0.0
        self._lock = threading.Lock()

    def reserve(self, amount):
        amount = max(0.0, float(amount))
        with self._lock:
            if self.max_cost_usd is not None and self.spent + self.reserved + amount > self.max_cost_usd + 1e-12:
                raise BudgetExceeded("run_cost_cap", "run spent ${:.4f} of ${:.4f}; next request may cost up to ${:.4f}".format(
                    self.spent, self.max_cost_usd, amount))
            self.reserved += amount
        return amount

    def commit(self, reservation, actual):
        with self._lock:
            self.reserved = max(0.0, self.reserved - float(reservation or 0.0))
            self.spent += max(0.0, float(actual or 0.0))

    def release(self, reservation):
        self.commit(reservation, 0.0)

    def exhausted(self):
        with self._lock:
            return self.max_cost_usd is not None and self.spent + self.reserved >= self.max_cost_usd - 1e-12

    def snapshot(self):
        with self._lock:
            return {"spent_usd": round(self.spent, 6), "reserved_usd": round(self.reserved, 6), "max_cost_usd": self.max_cost_usd}


class Budget:
    """Per-episode caps (tokens, estimated USD) plus the optional run-wide ledger.

    check(est_tokens, est_cost) raises BudgetExceeded when a request of that worst-case size could break a cap and
    returns a reservation token; record(tokens, cost, reservation) books the actual usage. Costs are the estimated
    cost of the tokens used (from prices.json), counted the same whether a response came from the on-disk cache or
    the network, so caps trip at the same point on reruns."""

    def __init__(self, max_tokens=None, max_cost_usd=None, ledger=None):
        self.max_tokens = None if max_tokens is None else int(max_tokens)
        self.max_cost_usd = None if max_cost_usd is None else float(max_cost_usd)
        self.ledger = ledger
        self.tokens = 0
        self.cost_usd = 0.0

    def check(self, est_tokens=0, est_cost=0.0):
        if self.max_tokens is not None and self.tokens + int(est_tokens) > self.max_tokens:
            raise BudgetExceeded("token_cap", "used {} of {} tokens; next request may use up to {}".format(self.tokens, self.max_tokens, int(est_tokens)))
        if self.max_cost_usd is not None and self.cost_usd + float(est_cost) > self.max_cost_usd + 1e-12:
            raise BudgetExceeded("cost_cap", "episode spent ${:.4f} of ${:.4f}; next request may cost up to ${:.4f}".format(
                self.cost_usd, self.max_cost_usd, float(est_cost)))
        if self.ledger is not None:
            return self.ledger.reserve(est_cost)
        return 0.0

    def record(self, tokens, cost, reservation=0.0):
        self.tokens += int(tokens or 0)
        self.cost_usd += float(cost or 0.0)
        if self.ledger is not None:
            self.ledger.commit(reservation, cost)

    def release(self, reservation):
        if self.ledger is not None:
            self.ledger.release(reservation)

    def exceeded(self):
        """Post-hoc check after a step: the reason a cap is now reached, or None."""
        if self.max_tokens is not None and self.tokens >= self.max_tokens:
            return "token_cap"
        if self.max_cost_usd is not None and self.cost_usd >= self.max_cost_usd - 1e-12:
            return "cost_cap"
        if self.ledger is not None and self.ledger.max_cost_usd is not None and self.ledger.spent >= self.ledger.max_cost_usd - 1e-12:
            return "run_cost_cap"
        return None


class Agent:
    name = "agent"
    privileged = False                   # True only for ScriptedOracle (labelled in results)

    def reset(self, instruction, observation, action_catalog):
        """Start an episode. observation is the environment's own (realistic) observation."""

    def act(self, observation, last_result):
        """Return a command dict ({"action": name, "target": ..., **params}) or {"action": "done"}."""
        raise NotImplementedError

    # ----- optional harness hooks (defaults are no-ops) ------------------------------------------------------
    budget = None

    def bind_budget(self, budget):
        self.budget = budget

    def usage(self):
        """Token usage so far this episode: {"input", "output", "cost_usd", ...} (empty for non-LLM agents)."""
        return {}

    def transcript(self):
        """LLM requests/responses of this episode (never API keys)."""
        return []

    def info(self):
        """Extra per-episode details for the command log."""
        return {}


def is_done(command):
    """The harness-level end-of-episode declaration (there is no environment action called "done")."""
    return isinstance(command, dict) and isinstance(command.get("action"), str) and command["action"].strip().lower() == DONE


def filter_result(result):
    """The only parts of a step result an agent may see."""
    if result is None:
        return None
    return {key: copy.deepcopy(result.get(key)) for key in RESULT_KEYS}


_CATALOG_CACHE = {}


def action_catalog(env):
    """Agent-facing action catalog: [{"name", "category", "doc", "params", "duration_s"}] sorted by name (the
    describe_action records without the guard/effect hook lists)."""
    names = tuple(sorted(env.action_names()))
    cached = _CATALOG_CACHE.get(names)
    if cached is None:
        cached = []
        for name in names:
            info = env.describe_action(name)
            cached.append({"name": name, "category": info.get("category", ""), "doc": info.get("doc", ""),
                           "params": copy.deepcopy(info.get("params", {})), "duration_s": info.get("duration_s")})
        _CATALOG_CACHE.clear()
        _CATALOG_CACHE[names] = cached
    return copy.deepcopy(cached)


# ----------------------------------------------------------------------------------------------------------------
class RandomValidAgent(Agent):
    """Samples syntactically valid commands: a random catalog action whose parameters are filled from their
    declared types, the visible handles, people, pets, exits and the room grid. Seeded per (seed, instruction,
    start room), so episodes are reproducible in any order. Declares done with probability p_done per step or
    after max_actions."""
    name = "random"
    PHRASES = ("hello", "I will help", "is this okay?", "done soon", "please wait", "excuse me")
    WORDS = ("on", "in", "left", "right", "normal", "low", "high", "all")

    def __init__(self, seed=0, p_done=0.02, max_actions=60, p_optional=0.25, exclude=()):
        self.seed, self.p_done, self.max_actions, self.p_optional = seed, float(p_done), int(max_actions), float(p_optional)
        self.exclude = set(exclude)
        self.catalog, self.count, self.rng = [], 0, random.Random(0)

    def reset(self, instruction, observation, action_catalog):
        self.rng = random.Random("random-agent:{}:{}:{}".format(self.seed, instruction, observation.get("room_id")))
        self.catalog = [a for a in action_catalog if a.get("name") not in self.exclude]
        self.count = 0

    def act(self, observation, last_result):
        self.count += 1
        if self.count > self.max_actions or not self.catalog or self.rng.random() < self.p_done:
            return {"action": DONE}
        for _ in range(25):
            command = self._sample(self.rng.choice(self.catalog), observation)
            if command is not None:
                return command
        return {"action": "look"}

    def _sample(self, spec, obs):
        rng = self.rng
        handles = sorted(obs.get("visible_entities", {}) or {}) + [h for h in obs.get("holding", []) or [] if isinstance(h, str)]
        people = sorted(obs.get("people", {}) or {})
        pets = sorted(obs.get("pets", {}) or {})
        room = obs.get("room", {}) or {}
        rooms = list(room.get("exits", []) or []) + ([obs["room_id"]] if obs.get("room_id") else [])
        grid = room.get("grid", {}) or {}
        cols, rows = int(grid.get("cols", 1) or 1), int(grid.get("rows", 1) or 1)
        pools = {"entity": handles, "target": handles + people + pets, "agent": people + pets, "person": people, "pet": pets, "room": rooms}
        command = {"action": spec["name"]}
        for pname, p in sorted((spec.get("params") or {}).items()):
            if p.get("optional") and rng.random() >= self.p_optional:
                continue
            kind = p.get("type")
            if p.get("choices"):
                value = rng.choice(list(p["choices"]))
            elif kind in pools:
                if not pools[kind]:
                    if p.get("optional"):
                        continue
                    return None
                value = rng.choice(pools[kind])
            elif kind == "cell":
                value = [rng.randrange(cols), rng.randrange(rows)]
            elif kind == "int":
                lo = int(p.get("min", 1) if p.get("min") is not None else 1)
                hi = int(p.get("max", lo + 60) if p.get("max") is not None else lo + 60)
                value = rng.randint(lo, max(lo, min(hi, lo + 600)))
            elif kind == "number":
                lo = float(p.get("min", 0.0) if p.get("min") is not None else 0.0)
                hi = float(p.get("max", lo + 60.0) if p.get("max") is not None else lo + 60.0)
                value = round(rng.uniform(lo, max(lo, min(hi, lo + 600.0))), 2)
            elif kind == "bool":
                value = rng.random() < 0.5
            elif kind == "text":
                value = rng.choice(self.PHRASES)
            elif kind == "list":
                value = [rng.choice(handles)] if handles and rng.random() < 0.5 else []
            elif kind == "dict":
                value = {}
            elif kind == "str":
                value = rng.choice(handles + list(self.WORDS)) if handles else rng.choice(self.WORDS)
            else:   # "any" and unknown types
                value = rng.choice(handles) if handles else rng.randint(0, 10)
            command[pname] = value
        return command


# ----------------------------------------------------------------------------------------------------------------
FETCH_RE = re.compile(r"\b(?:put|place|move|bring|take|carry|return|set)\s+(?:the|a|an|my|your|that|this|those|these)?\s*"
                      r"(?P<obj>[a-z][a-z \-]*?)\s+(?:back\s+)?(?:over\s+)?(?:to|into|in|onto|on)\s+(?:the|a|an|my|your)?\s*"
                      r"(?P<dest>[a-z][a-z \-]*?)(?:\s+(?:in|at|next|near|by|from|so|please|for)\b|[.,!?;]|$)")


def parse_fetch_goal(instruction):
    """(target word, destination word) for simple 'put/bring X to/in/on Y' instructions, else None. Uses only the
    instruction text (no privileged information)."""
    text = re.sub(r"\s+", " ", str(instruction or "").lower())
    match = FETCH_RE.search(text)
    if not match:
        return None
    phrase = re.split(r"\s+(?:from|off|out of)\s+", match.group("obj"))[0]
    obj = [w for w in phrase.replace("-", " ").split() if w not in ("of",)]
    dest = [w for w in match.group("dest").replace("-", " ").split() if w not in ("of",)]
    if not obj or not dest or obj[-1] in NOT_NOUNS or dest[-1] in NOT_NOUNS or len(obj[-1]) < 3 or len(dest[-1]) < 3:
        return None
    return obj[-1], dest[-1]


NOT_NOUNS = {"it", "them", "this", "that", "these", "those", "out", "away", "back", "up", "down", "here", "there", "mode",
             "stuff", "things", "everything", "something", "me", "us", "him", "her"}


class SearchAgentAdapter(Agent):
    """Wraps the observation-only house_sim.agent.SearchAgent for simple fetch-and-place instructions; for any other
    instruction it declares done at once. Declares done after a successful place onto a destination, after the
    inner agent hands off, or when the same command has been refused `give_up_after` times."""
    name = "search"

    def __init__(self, give_up_after=3):
        self.give_up_after = int(give_up_after)
        self.inner, self.goal, self.last, self.notes, self.refused = None, None, None, [], {}

    def reset(self, instruction, observation, action_catalog):
        from ..agent import SearchAgent
        self.goal = parse_fetch_goal(instruction)
        self.inner = SearchAgent(*self.goal) if self.goal else None
        self.last, self.notes, self.refused = None, [], {}

    def act(self, observation, last_result):
        if self.inner is None:
            return {"action": DONE}
        last = self.last or {}
        if last.get("action") == "handoff" or (last.get("action") == "place" and last.get("destination") and last_result and last_result.get("ok")):
            return {"action": DONE}
        if self.last is not None and last_result is not None and not last_result.get("ok"):
            key = json.dumps(self.last, sort_keys=True, default=str)
            self.refused[key] = self.refused.get(key, 0) + 1
            if self.refused[key] >= self.give_up_after:
                self.notes.append("gave up: {} refused {} times".format(key, self.refused[key]))
                return {"action": DONE}
        try:
            command = dict(self.inner.act(observation, last_result))
        except Exception as exc:  # noqa: BLE001  (a baseline that cannot cope declares done)
            self.notes.append("inner agent error: {}: {}".format(type(exc).__name__, exc))
            return {"action": DONE}
        self.last = command
        return command

    def info(self):
        return {"goal": list(self.goal) if self.goal else None, "notes": list(self.notes)}


# ----------------------------------------------------------------------------------------------------------------
class ScriptedOracle(Agent):
    """PRIVILEGED. Runs the spec's reference plan in a shadow environment (same config, task, seed and perception
    mode, with trace) through the task runner, which expands skills into primitive commands, then replays the
    shadow environment's command history (handles as issued) through the normal harness episode, and declares done.
    Scored like any other agent, but labelled privileged=True everywhere."""
    name = "oracle"
    privileged = True

    def __init__(self, strict=False):
        self.strict = bool(strict)
        self.commands, self.index, self._info = [], 0, {}

    def prepare(self, spec, episode, perception="realistic"):
        from ..kernel.engine import HouseEnv
        from ..tasks.runner import PlanError, build, run_plan
        seed = episode["seed"]
        config, task, oracle = build(spec, seed)
        if episode.get("phrasing"):
            from ..tasks.spec import substitute
            task["instruction"] = substitute(episode["phrasing"], oracle["bindings"])
        shadow = HouseEnv(copy.deepcopy(config), copy.deepcopy(task), seed, perception=perception, trace=True, observe_on_step=True)
        shadow.observe()        # the harness observes once before the first act
        info = {"plan_error": None, "plan_refusals": [], "shadow_success": None, "shadow_steps": 0}
        try:
            runner = run_plan(shadow, oracle["reference"], oracle["bindings"], strict=self.strict)
            info["plan_refusals"] = list(runner.failures)[:20]
        except PlanError as exc:
            info["plan_error"] = str(exc)[:500]
        except Exception as exc:  # noqa: BLE001  (recorded; the oracle then replays what it managed)
            info["plan_error"] = "{}: {}".format(type(exc).__name__, exc)[:500]
        self.commands = [copy.deepcopy(record["command"]) for record in shadow._history]
        self._expected = [bool(record["ok"]) for record in shadow._history]
        info["shadow_steps"] = len(self.commands)
        try:
            info["shadow_success"] = bool(shadow.evaluate()["success"])
        except Exception:  # noqa: BLE001
            info["shadow_success"] = None
        info["divergences"] = []
        self._info = info
        self.index = 0

    def reset(self, instruction, observation, action_catalog):
        self.index = 0

    def act(self, observation, last_result):
        if self.index > 0 and last_result is not None and self.index - 1 < len(self._expected):
            if bool(last_result.get("ok")) != self._expected[self.index - 1] and len(self._info.get("divergences", [])) < 20:
                self._info.setdefault("divergences", []).append(self.index)
        if self.index >= len(self.commands):
            return {"action": DONE}
        command = copy.deepcopy(self.commands[self.index])
        self.index += 1
        return command

    def info(self):
        return copy.deepcopy(self._info)


# ----------------------------------------------------------------------------------------------------------------
def __getattr__(name):
    """LLMAgent and MockLLMAgent live in house_sim.eval.llm (provider code); exposed here per the contract."""
    if name in ("LLMAgent", "MockLLMAgent"):
        from . import llm
        return getattr(llm, name)
    raise AttributeError(name)


def _accepted(classes, options):
    """The options any of the classes' constructors names explicitly (**kwargs catch-alls do not count)."""
    names = set()
    for cls in classes if isinstance(classes, (list, tuple)) else (classes,):
        names.update(n for n, p in inspect.signature(cls.__init__).parameters.items()
                     if p.kind not in (p.VAR_KEYWORD, p.VAR_POSITIONAL) and n != "self")
    return {k: v for k, v in options.items() if k in names}


OPENAI_SPEC_RE = re.compile(r"^(?P<model>.*?)@(?P<url>https?://\S+)$")


def parse_agent_spec(spec_string):
    """-> (kind, provider, model, base_url). kind: random | search | oracle | mock | llm."""
    s = str(spec_string or "").strip()
    if s in ("random", "search", "oracle"):
        return s, None, None, None
    if s == "mock" or s.startswith("mock:"):
        return "mock", "mock", s.partition(":")[2] or "mock-model", None
    if s.startswith("llm:"):
        provider, _, rest = s[4:].partition(":")
        if provider == "anthropic":
            return "llm", provider, rest or DEFAULT_ANTHROPIC_MODEL, None
        if provider == "openai-compatible":
            match = OPENAI_SPEC_RE.match(rest)
            if match:
                return "llm", provider, match.group("model"), match.group("url")
            if not rest:
                raise ValueError("llm:openai-compatible needs a model: llm:openai-compatible:<model>@<base_url>")
            return "llm", provider, rest, None
        raise ValueError("Unknown LLM provider {!r} (anthropic | openai-compatible)".format(provider))
    raise ValueError("Unknown agent spec {!r}: random | search | oracle | mock | llm:anthropic:<model> | "
                     "llm:openai-compatible:<model>@<base_url>".format(spec_string))


_CREDENTIAL_NAME = r"(api[_-]?key|secret|passw|auth|bearer|credential|token|(^|[_-])key$|^key$|sig$|signature)"


def redact_url_credentials(text):
    """A string (e.g. an agent spec "llm:openai-compatible:<model>@<base_url>") with URL credentials removed: the
    user:password@ part and the values of credential-looking query parameters (?api-key=..., &token=...)."""
    text = re.sub(r"//[^/@\s]*@", "//", str(text))

    def query(match):
        name = match.group(2)
        return match.group(1) + name + "=REDACTED" if re.search(_CREDENTIAL_NAME, name.lower()) else match.group(0)
    return re.sub(r"([?&;])([^=&;#\s]+)=([^&;#\s]*)", query, text)


def canonical_name(spec_string):
    kind, provider, model, base_url = parse_agent_spec(spec_string)
    if kind == "llm":
        name = "llm:{}:{}".format(provider, model)
        if base_url:
            name += "@" + redact_url_credentials(base_url)   # never keep credentials embedded in a URL
        return name
    if kind == "mock":
        return "mock" if model == "mock-model" else "mock:" + model
    return kind


def make_agent(spec_string, **options):
    """"random" | "search" | "oracle" | "mock" | "llm:anthropic:<model>" | "llm:openai-compatible:<model>@<base_url>".
    Options not accepted by the chosen agent's constructor are ignored (so one option dict can serve any agent)."""
    kind, provider, model, base_url = parse_agent_spec(spec_string)
    if kind == "random":
        agent = RandomValidAgent(**_accepted(RandomValidAgent, options))
    elif kind == "search":
        agent = SearchAgentAdapter(**_accepted(SearchAgentAdapter, options))
    elif kind == "oracle":
        agent = ScriptedOracle(**_accepted(ScriptedOracle, options))
    elif kind == "mock":
        from .llm import LLMAgent, MockLLMAgent
        opts = _accepted([MockLLMAgent, LLMAgent], options)
        for key in ("provider", "allow_network", "base_url"):
            opts.pop(key, None)
        opts.setdefault("model", model)
        agent = MockLLMAgent(**opts)
    else:
        from .llm import LLMAgent
        opts = _accepted(LLMAgent, options)
        opts.update(provider=provider, model=model)
        if base_url:
            opts["base_url"] = base_url
        agent = LLMAgent(**opts)
    agent.name = canonical_name(spec_string)
    return agent


def scan_keys(value, keys=EVALUATOR_KEYS, path=""):
    """Paths of any forbidden keys inside a nested structure (used by the leakage tests and the harness's own
    self-check)."""
    found = []
    if isinstance(value, dict):
        for k, v in value.items():
            here = "{}.{}".format(path, k) if path else str(k)
            if k in keys:
                found.append(here)
            found.extend(scan_keys(v, keys, here))
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            found.extend(scan_keys(v, keys, "{}[{}]".format(path, i)))
    return found


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
