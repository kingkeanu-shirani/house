"""Agent harness (house_sim/eval/agents.py, run.py, llm.py): leakage, done protocol, invalid steps, caps, caching,
providers, built-in agents end to end, results layout. Fully offline: every network path is stubbed to fail. The module
runs with a temporary HOME/USERPROFILE and no HOUSE_AI_SPLIT_KEY (tests that need a key set a throwaway one), so the
user's real ~/.house_ai is never read or written."""
import copy
import hashlib
import io
import json
import os
import shutil
import socket
import tempfile
import unittest
import urllib.request
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest import mock

from house_sim.eval import agents as A
from house_sim.eval import llm as L
from house_sim.eval import run as R

WET = "T-089c40fb507289-01"        # "Avoid wet floors": one-step reference, success = robot in the goal room
GLASSES = "T-6af2ea41755bbe-01"    # "Retrieve an object on request": hand the reading glasses to the requester
FORBIDDEN = {"evaluation", "reward", "terminated", "truncated", "raw_message", "agent_message", "goal_fraction",
             "budget_exceeded", "milestones", "failures", "score", "bindings", "reference", "wrong", "oracle"}

_SAVED = {}


def _no_network(*args, **kwargs):
    raise AssertionError("network access attempted in an offline test")


def setUpModule():
    _SAVED["urlopen"] = urllib.request.urlopen
    _SAVED["create_connection"] = socket.create_connection
    _SAVED["connect"] = socket.socket.connect
    urllib.request.urlopen = _no_network
    socket.create_connection = _no_network

    def guarded_connect(self, address, *rest):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError("network access attempted in an offline test: {}".format(address))
        return _SAVED["connect"](self, address, *rest)
    socket.socket.connect = guarded_connect
    for key in _ENV_KEYS:
        _SAVED["env:" + key] = os.environ.pop(key, None)
    os.environ["HOUSE_AI_FORBID_NETWORK"] = "1"
    _SAVED["home"] = tempfile.mkdtemp(prefix="harness_home_")
    os.environ["HOME"] = os.environ["USERPROFILE"] = _SAVED["home"]   # never the user's real ~/.house_ai


def tearDownModule():
    urllib.request.urlopen = _SAVED["urlopen"]
    socket.create_connection = _SAVED["create_connection"]
    socket.socket.connect = _SAVED["connect"]
    for key in _ENV_KEYS:
        value = _SAVED.get("env:" + key)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    shutil.rmtree(_SAVED["home"], ignore_errors=True)


_ENV_KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "HOUSE_AI_LLM_BASE_URL", "HOUSE_AI_FORBID_NETWORK", "HOUSE_AI_SPLIT_KEY",
             "HOME", "USERPROFILE")
THROWAWAY_KEY = "5eed" * 16       # a throwaway 32-byte split key (hex) for tests that need one


class NetworkStandIn(L.UrllibTransport):
    """Classified exactly like the real network transport (an UrllibTransport) but answers locally through an inner
    transport: lets offline tests exercise what only real network runs do (default cache, network namespace)."""

    def __init__(self, inner):
        super().__init__(allow_network=True)
        self.inner = inner

    @property
    def calls(self):
        return self.inner.calls

    def post(self, url, headers, body, timeout):
        return self.inner.post(url, headers, body, timeout)


_RECORDS = {}


def record(cid):
    if not _RECORDS:
        for r in R.load_specs():
            _RECORDS[r["catalog_id"]] = r
    return _RECORDS[cid]


def episode(cid, seed=0, split="dev", tag=None):
    return {"catalog_id": cid, "split": split, "seed": seed, "seed_tag": tag or "{}:{}".format(split, seed), "phrasing": None,
            "perception": "realistic"}


def keys_anywhere(value, keys=FORBIDDEN):
    return A.scan_keys(value, keys)


class ScriptAgent(A.Agent):
    """Issues a fixed list of commands, then done."""
    name = "script"

    def __init__(self, commands):
        self.commands = list(commands)
        self.i = 0

    def reset(self, instruction, observation, action_catalog):
        self.i = 0

    def act(self, observation, last_result):
        if self.i >= len(self.commands):
            return {"action": "done"}
        self.i += 1
        return copy.deepcopy(self.commands[self.i - 1])


class SpyAgent(A.Agent):
    """Records everything the harness hands it."""
    name = "spy"

    def __init__(self, n=4):
        self.n, self.calls, self.reset_args, self.prepared = n, [], None, False

    def prepare(self, *args, **kwargs):            # must never be called: this agent is not privileged
        self.prepared = True

    def reset(self, instruction, observation, action_catalog):
        self.reset_args = (instruction, copy.deepcopy(observation), copy.deepcopy(action_catalog))

    def act(self, observation, last_result):
        self.calls.append((copy.deepcopy(observation), copy.deepcopy(last_result)))
        observation["visible_entities"] = {}       # mutating what we got must not affect the environment
        if len(self.calls) > self.n:
            return {"action": "done"}
        return [{"action": "look"}, {"action": "pick_up", "target": "obj_00000000"}, {"action": "wait", "seconds": 5},
                {"action": "inspect", "target": "nonexistent"}][len(self.calls) % 4]


class OracleThenUndo(A.ScriptedOracle):
    """Achieves the goal with the reference commands, then walks back to where it started, then declares done."""

    def reset(self, instruction, observation, action_catalog):
        super().reset(instruction, observation, action_catalog)
        self.start_room = observation["room_id"]
        self.undone = False

    def act(self, observation, last_result):
        if self.index < len(self.commands):
            return super().act(observation, last_result)
        if not self.undone:
            self.undone = True
            return {"action": "move", "target": self.start_room, "avoid": ["wet"]}
        return {"action": "done"}


class OracleNoDone(A.ScriptedOracle):
    """Achieves the goal, then waits forever (never declares done)."""

    def act(self, observation, last_result):
        command = super().act(observation, last_result)
        return {"action": "wait", "seconds": 1} if A.is_done(command) else command


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="harness_test_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


# ----------------------------------------------------------------------------------------------------------------
class TestLeakage(Temp):
    def test_spy_agent_receives_only_instruction_observation_and_filtered_results(self):
        spy = SpyAgent()
        rec = R.run_episode(record(GLASSES), episode(GLASSES), spy, out_dir=self.tmp)
        self.assertFalse(spy.prepared, "a non-privileged agent must not receive the spec/seed")
        instruction, observation, catalog = spy.reset_args
        self.assertIsInstance(instruction, str)
        self.assertTrue(instruction)
        self.assertEqual(observation.get("perception"), "realistic")
        self.assertEqual(keys_anywhere(observation), [])
        self.assertEqual(keys_anywhere(catalog, A.EVALUATOR_KEYS), [])   # params may legitimately be named e.g. "reference"
        for item in catalog:
            self.assertEqual(set(item), {"name", "category", "doc", "params", "duration_s"})
        self.assertIsNone(spy.calls[0][1], "no result before the first step")
        for obs, last in spy.calls:
            self.assertEqual(keys_anywhere(obs), [], "evaluator field in an observation")
            if last is not None:
                self.assertEqual(set(last), {"ok", "message", "category"})
                self.assertEqual(keys_anywhere(last), [])
            for handle in obs.get("visible_entities", {}):
                self.assertRegex(handle, r"^obj_[0-9a-f]{8,16}$", "realistic observations expose only handles")
        # the agent's mutation of its observation copy did not leak back into the environment
        self.assertTrue(spy.calls[1][0].get("visible_entities"))
        self.assertEqual(rec["steps"], spy.n)
        self.assertTrue(rec["done_declared"])

    def test_filter_result_strips_every_privileged_field(self):
        full = {"ok": True, "message": "m", "category": "", "observation": {}, "evaluation": {"success": True}, "reward": 1.0,
                "terminated": True, "truncated": False, "raw_message": "true id leak", "agent_message": "x"}
        self.assertEqual(A.filter_result(full), {"ok": True, "message": "m", "category": ""})
        self.assertIsNone(A.filter_result(None))


class TestEpisodeNonce(Temp):
    """Review H8, the harness half of K-LEAK's fix (phase 1, INT): every agent episode runs with a secret nonce
    (house_sim.kernel.realism.NONCE_KEY), so its handles and fixed draws cannot be recomputed from the public spec,
    seed and code, and differ between episodes; public logs keep it for replays, test-split logs never do."""

    def test_each_public_episode_has_its_own_nonce_and_replays_from_its_log(self):
        runs = []
        for out in ("a", "b"):
            spy = SpyAgent(n=3)
            rec = R.run_episode(record(WET), episode(WET), spy, out_dir=self.tmp / out)
            log = json.loads((self.tmp / out / rec["log"]).read_text(encoding="utf-8"))
            runs.append((rec, log, sorted(spy.reset_args[1]["visible_entities"])))
        (rec_a, log_a, handles_a), (rec_b, log_b, handles_b) = runs
        self.assertRegex(log_a["episode_nonce"], r"^[0-9a-f]{32}$")
        self.assertNotEqual(log_a["episode_nonce"], log_b["episode_nonce"])
        self.assertTrue(handles_a)
        self.assertNotEqual(handles_a, handles_b, "the same episode shows other handles under another nonce")
        for rec, log in ((rec_a, log_a), (rec_b, log_b)):
            again = R.replay_log(log, record(WET))
            self.assertEqual((again["success"], again["steps"]), (rec["success"], rec["steps"]))

    def test_the_privileged_oracle_shares_the_episodes_nonce(self):
        rec = R.run_episode(record(WET), episode(WET), A.make_agent("oracle"), out_dir=self.tmp)
        self.assertTrue(rec["success"], rec)
        log = json.loads((self.tmp / rec["log"]).read_text(encoding="utf-8"))
        self.assertTrue(R.replay_log(log, record(WET))["success"])

    def test_a_test_split_nonce_is_never_logged_and_the_key_holder_recomputes_it(self):
        key = bytes.fromhex(THROWAWAY_KEY)
        first = R.episode_nonce(True, WET, "test:0123456789ab", key)
        self.assertEqual(first, R.episode_nonce(True, WET, "test:0123456789ab", key))     # a rerun reproduces it
        self.assertNotEqual(first, R.episode_nonce(True, WET, "test:0123456789ac", key))
        self.assertNotEqual(first, R.episode_nonce(True, WET, "test:0123456789ab", bytes(32)))
        log = {"catalog_id": WET, "seed_tag": "test:0123456789ab", "episode_nonce_scheme": R.NONCE_SCHEME}
        self.assertEqual(R.log_nonce(log, key=key), first)
        self.assertIsNone(R.log_nonce(log))                  # no key here: only the key holder can rebuild it
        self.assertIsNone(R.log_nonce({"catalog_id": WET}))  # a log from before harness version 3

    def test_a_cache_backed_agent_sees_the_same_handles_on_a_rerun(self):
        cache = L.ResponseCache(self.tmp / "cache")
        key = cache.nonce_key("anthropic|network")
        self.assertEqual(key, L.ResponseCache(self.tmp / "cache").nonce_key("anthropic|network"))
        self.assertNotEqual(key, cache.nonce_key("mock|mock"))
        self.assertNotEqual(key, L.ResponseCache(self.tmp / "other").nonce_key("anthropic|network"))
        self.assertEqual(R.episode_nonce(False, WET, "dev:0", public_key=key),
                         R.episode_nonce(False, WET, "dev:0", public_key=key))
        self.assertNotEqual(R.episode_nonce(False, WET, "dev:0"), R.episode_nonce(False, WET, "dev:0"))


class TestDoneProtocol(Temp):
    def test_oracle_succeeds_and_achieve_then_undo_fails(self):
        good = A.make_agent("oracle")
        rec = R.run_episode(record(WET), episode(WET), good)
        self.assertTrue(rec["success"], rec)
        self.assertTrue(rec["privileged"])
        undo = OracleThenUndo()
        rec2 = R.run_episode(record(WET), episode(WET), undo, out_dir=self.tmp)
        self.assertTrue(rec2["done_declared"])
        self.assertFalse(rec2["success"], "goal achieved then undone must fail at the declared end")
        # the goal really was achieved before it was undone: replay the prefix in a fresh env
        log = json.loads((self.tmp / rec2["log"]).read_text(encoding="utf-8"))
        prefix = dict(log, steps=log["steps"][:len(undo.commands)], done_declared=True)
        self.assertTrue(R.replay_log(prefix, record(WET))["success"])
        self.assertFalse(R.replay_log(log, record(WET))["success"])

    def test_success_requires_a_declared_done(self):
        rec = R.run_episode(record(WET), episode(WET), OracleNoDone(), caps={"max_steps": 6})
        self.assertEqual(rec["end_reason"], "step_budget")
        self.assertFalse(rec["done_declared"])
        self.assertFalse(rec["success"])
        self.assertGreater(rec["goal_fraction"], 0.99, "the goal state was reached; only the declaration is missing")
        lenient = R.run_episode(record(WET), episode(WET), OracleNoDone(), caps={"max_steps": 6, "require_done": False})
        self.assertTrue(lenient["success"])

    def test_done_is_accepted_after_the_last_budgeted_step(self):
        rec = R.run_episode(record(WET), episode(WET), A.make_agent("oracle"), caps={"max_steps": 1})
        self.assertEqual(rec["steps"], 1)
        self.assertTrue(rec["done_declared"])
        self.assertTrue(rec["success"])
        script = ScriptAgent([{"action": "look"}, {"action": "look"}])
        rec2 = R.run_episode(record(WET), episode(WET), script, caps={"max_steps": 1})
        self.assertEqual((rec2["steps"], rec2["end_reason"]), (1, "step_budget"))


class TestInvalidSteps(Temp):
    def test_invalid_json_is_an_invalid_step(self):
        agent = L.MockLLMAgent(responses=["this is not json {", "still {not json", {"action": "done"}])
        rec = R.run_episode(record(WET), episode(WET), agent, out_dir=self.tmp)
        self.assertEqual(rec["invalid_steps"], 1)
        self.assertEqual(rec["steps"], 1)
        log = json.loads((self.tmp / rec["log"]).read_text(encoding="utf-8"))
        self.assertEqual(log["steps"][0]["parse"], "unparseable")
        self.assertEqual(log["steps"][0]["sent"], {"action": R.UNPARSEABLE})
        self.assertFalse(log["steps"][0]["result"]["ok"])
        self.assertEqual(agent.usage()["parse_retries"], 1, "one retry on a parse failure")
        replay = (self.tmp / log["replay"])
        from house_sim.kernel.engine import HouseEnv
        self.assertEqual(HouseEnv.replay(replay).evaluate()["metrics"]["invalid"], 1)

    def test_unknown_actions_and_malformed_commands_are_invalid_steps(self):
        script = ScriptAgent([{"action": "teleport_to_moon"}, "not a dict", {"target": "obj_1"}, {"action": "look", "self": 1},
                              {"action": "inspect", "target": ["a", "b"]}])
        rec = R.run_episode(record(WET), episode(WET), script, out_dir=self.tmp)
        self.assertEqual(rec["steps"], 5)
        self.assertEqual(rec["invalid_steps"], 5)
        log = json.loads((self.tmp / rec["log"]).read_text(encoding="utf-8"))
        self.assertEqual([s["parse"] for s in log["steps"][:5]], ["ok", "not_a_dict", "no_action", "reserved_key", "ok"])
        self.assertEqual(log["unknown_actions"], 1)
        self.assertEqual(log["steps"][4]["sent"]["target"], '["a", "b"]')


class TestCaps(Temp):
    BIG = {"input_tokens": 100000, "output_tokens": 1000, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}

    def looping_agent(self, **kw):
        return L.MockLLMAgent(responses=lambda body, i: {"action": "wait", "params": {"seconds": 1}}, usage=self.BIG,
                              max_output_tokens=1000, **kw)

    def test_episode_cost_cap(self):
        # mock prices: $1/M input, $5/M output -> $0.105 per call
        agent = self.looping_agent()
        rec = R.run_episode(record(WET), episode(WET), agent, caps={"max_cost_usd_episode": 0.25})
        self.assertEqual(rec["end_reason"], "cost_cap")
        self.assertFalse(rec["success"])
        self.assertLess(rec["steps"], 30)
        self.assertLessEqual(rec["cost_usd"], 0.25 + 0.105 + 1e-9)
        self.assertGreaterEqual(rec["cost_usd"], 0.2)

    def test_token_cap(self):
        rec = R.run_episode(record(WET), episode(WET), self.looping_agent(), caps={"max_tokens": 250000})
        self.assertEqual(rec["end_reason"], "token_cap")
        self.assertLessEqual(rec["tokens"]["input"] + rec["tokens"]["output"], 250000 + 101000)

    def test_worst_case_precheck_blocks_a_request_that_could_exceed_the_cap(self):
        rec = R.run_episode(record(WET), episode(WET), self.looping_agent(), caps={"max_cost_usd_episode": 0.0001})
        self.assertEqual((rec["end_reason"], rec["steps"], rec["cost_usd"]), ("cost_cap", 0, 0.0))

    def test_run_cost_cap_is_shared_across_episodes(self):
        specs = [record(WET), record(GLASSES)]
        summary = R.run("mock", "dev", specs, self.tmp / "run", caps={"max_cost_usd": 0.3}, verify="none", seeds=[0], emit=None,
                        agent_options={"responses": [{"action": "look"}] * 50, "usage": self.BIG, "max_output_tokens": 1000})
        records = [json.loads(l) for l in (self.tmp / "run" / "episodes.jsonl").read_text(encoding="utf-8").splitlines()]
        total = sum(r["cost_usd"] for r in records)
        self.assertLessEqual(total, 0.3 + 0.105 + 1e-9)
        self.assertIn(records[0]["end_reason"], ("run_cost_cap",))
        info = json.loads((self.tmp / "run" / "run.json").read_text(encoding="utf-8"))
        self.assertIn(info["status"], ("incomplete", "complete"))
        self.assertEqual(info["episodes_run"] + info["episodes_skipped"], 2)
        self.assertEqual(summary["episodes"], info["episodes_run"])

    def test_wall_clock_cap(self):
        rec = R.run_episode(record(WET), episode(WET), ScriptAgent([{"action": "look"}] * 10), caps={"max_wall_s": 1e-9})
        self.assertEqual(rec["end_reason"], "wall_clock")

    def test_budget_and_ledger_units(self):
        ledger = A.CostLedger(1.0)
        budget = A.Budget(max_tokens=100, max_cost_usd=0.5, ledger=ledger)
        r = budget.check(50, 0.4)
        budget.record(40, 0.3, r)
        self.assertAlmostEqual(ledger.spent, 0.3)
        self.assertAlmostEqual(ledger.reserved, 0.0)
        with self.assertRaises(A.BudgetExceeded) as ctx:
            budget.check(70, 0.0)
        self.assertEqual(ctx.exception.reason, "token_cap")
        with self.assertRaises(A.BudgetExceeded) as ctx:
            budget.check(0, 0.25)
        self.assertEqual(ctx.exception.reason, "cost_cap")
        other = A.Budget(max_cost_usd=10, ledger=ledger)
        with self.assertRaises(A.BudgetExceeded) as ctx:
            other.check(0, 0.8)
        self.assertEqual(ctx.exception.reason, "run_cost_cap")


def anthropic_handler(script):
    """Fake Anthropic API: answers from a list of commands in order (then done), with realistic usage fields."""
    state = {"i": 0}

    def handler(url, headers, body):
        i = state["i"]
        state["i"] += 1
        item = script[i] if i < len(script) else {"action": "done"}
        return L.anthropic_response(item, {"input_tokens": 3000, "output_tokens": 120, "cache_creation_input_tokens": 20000 if i == 0 else 0,
                                           "cache_read_input_tokens": 0 if i == 0 else 20000}, body["model"])
    return handler


class TestCache(Temp):
    def test_second_run_makes_zero_transport_calls(self):
        script = [{"action": "look"}, {"action": "wait", "params": {"seconds": 2}, "note": "waited"}, {"action": "look"}]
        first = L.FakeTransport(anthropic_handler(script))
        agent = L.LLMAgent(provider="anthropic", model="claude-opus-5-5", transport=first, cache_dir=self.tmp / "cache")
        rec1 = R.run_episode(record(WET), episode(WET), agent, out_dir=self.tmp / "a")
        self.assertEqual(len(first.calls), 4)
        self.assertEqual(agent.usage()["cache_hits"], 0)
        second = L.FakeTransport(_no_network)
        again = L.LLMAgent(provider="anthropic", model="claude-opus-5-5", transport=second, cache_dir=self.tmp / "cache")
        rec2 = R.run_episode(record(WET), episode(WET), again, out_dir=self.tmp / "b")
        self.assertEqual(len(second.calls), 0, "a rerun must be served entirely from the cache")
        self.assertEqual(again.usage()["cache_hits"], 4)
        self.assertEqual(again.usage()["spent_usd"], 0.0)
        for key in ("success", "steps", "end_reason", "tokens", "cost_usd", "invalid_steps"):
            self.assertEqual(rec1[key], rec2[key], key)
        log1 = json.loads((self.tmp / "a" / rec1["log"]).read_text(encoding="utf-8"))
        log2 = json.loads((self.tmp / "b" / rec2["log"]).read_text(encoding="utf-8"))
        self.assertEqual([s.get("sent") for s in log1["steps"]], [s.get("sent") for s in log2["steps"]])

    def test_cache_key_ignores_headers_and_credentials_in_urls(self):
        body = {"model": "m", "messages": []}
        self.assertEqual(L.ResponseCache.key("p", "https://user:pw@host/v1", body), L.ResponseCache.key("p", "https://host/v1", body))
        self.assertNotEqual(L.ResponseCache.key("p", "https://host/v1", body), L.ResponseCache.key("p", "https://host/v1", {"model": "n"}))


class TestNetworkSafety(Temp):
    def test_default_transport_refuses(self):
        agent = L.LLMAgent(provider="anthropic", model="claude-opus-5-5")
        self.assertIsInstance(agent.transport, L.DisabledTransport)
        rec = R.run_episode(record(WET), episode(WET), agent)
        self.assertEqual(rec["end_reason"], "agent_error")
        self.assertEqual(rec["steps"], 0)

    def test_real_transport_needs_explicit_permission_and_a_key(self):
        self.assertIsInstance(L.make_transport("anthropic", allow_network=False), L.DisabledTransport)
        self.assertIsInstance(L.make_transport("anthropic", allow_network="yes"), L.DisabledTransport)
        with self.assertRaises(L.LLMConfigError):
            L.make_transport("anthropic", allow_network=True)            # no ANTHROPIC_API_KEY in the test environment
        with self.assertRaises(L.NetworkDisabled):
            L.UrllibTransport()
        with self.assertRaises(L.NetworkDisabled):                     # HOUSE_AI_FORBID_NETWORK=1 during tests
            L.UrllibTransport(allow_network=True).post("https://api.anthropic.com/v1/messages", {}, {}, 1)

    def test_run_refuses_real_llm_without_allow_network(self):
        with self.assertRaises(L.NetworkDisabled):
            R.run("llm:anthropic:claude-opus-5-5", "dev", [record(WET)], self.tmp / "r", verify="none", seeds=[0], emit=None)
        self.assertFalse((self.tmp / "r").exists())


class TestAnthropicProvider(Temp):
    def test_request_shape_parsing_usage_and_cost(self):
        fake = L.FakeTransport(anthropic_handler([{"action": "move", "target": "kitchen_1", "params": {"speed": "careful"},
                                                   "note": "heading out"}]))
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-NOTREAL-9f8e7d"
        try:
            agent = L.LLMAgent(provider="anthropic", model="claude-opus-5-5", transport=fake, effort="low", temperature=0.3,
                               cache_dir=self.tmp / "cache")
            rec = R.run_episode(record(WET), episode(WET), agent, out_dir=self.tmp / "out")
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        call = fake.calls[0]
        self.assertEqual(call["url"], "https://api.anthropic.com/v1/messages")
        self.assertEqual(call["headers"]["anthropic-version"], "2023-06-01")
        self.assertEqual(call["headers"]["content-type"], "application/json")
        self.assertTrue(call["has_key"])
        body = call["body"]
        self.assertEqual(body["model"], "claude-opus-5-5")
        self.assertEqual(body["max_tokens"], 8000)
        self.assertEqual(body["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertIn("done", body["system"][0]["text"])
        self.assertIn("pick_up(", body["system"][0]["text"])
        self.assertEqual(body["tools"][0]["name"], "act")
        self.assertIn("input_schema", body["tools"][0])
        self.assertEqual(body["tool_choice"]["type"], "auto", "claude-opus-5-5 rejects forced tool_choice")
        self.assertNotIn("temperature", body, "current Claude models reject non-default sampling parameters")
        self.assertEqual(body["output_config"], {"effort": "low"})
        self.assertEqual(body["messages"][0]["role"], "user")
        self.assertIn("INSTRUCTION:", body["messages"][0]["content"])
        for call_ in fake.calls:                       # nothing an evaluator produces reaches the model either
            content = call_["body"]["messages"][0]["content"]
            for key in ("evaluation", "reward", "terminated", "truncated", "raw_message", "goal_fraction"):
                self.assertNotIn('"{}"'.format(key), content)
        # parsed command (params flattened, note kept as memory)
        log = json.loads((self.tmp / "out" / rec["log"]).read_text(encoding="utf-8"))
        self.assertEqual(log["steps"][0]["sent"], {"action": "move", "target": "kitchen_1", "speed": "careful"})
        self.assertIn("MEMORY NOTE: heading out", fake.calls[1]["body"]["messages"][0]["content"])
        # cost from prices.json: first call writes the cache, later calls read it
        price = L.price_for("anthropic", "claude-opus-5-5")
        first = (3000 * price["input"] + 20000 * price["cache_write"] + 120 * price["output"]) / 1e6
        later = (3000 * price["input"] + 20000 * price["cache_read"] + 120 * price["output"]) / 1e6
        self.assertAlmostEqual(rec["cost_usd"], round(first + later, 6), places=6)
        self.assertEqual(rec["tokens"], {"input": 2 * 23000, "output": 240})
        # the API key never reaches any file
        for path in (self.tmp).rglob("*"):
            if path.is_file():
                self.assertNotIn("NOTREAL", path.read_text(encoding="utf-8", errors="replace"), str(path))
        transcripts = list((self.tmp / "out" / "transcripts").glob("*.jsonl"))
        self.assertEqual(len(transcripts), 1)
        self.assertTrue(list((self.tmp / "out" / "transcripts").glob("system_*.txt")))

    def test_forced_tool_choice_for_models_that_accept_it(self):
        provider = L.AnthropicProvider("claude-haiku-5-5")
        self.assertEqual(provider.build("s", "t")["tool_choice"], {"type": "tool", "name": "act", "disable_parallel_tool_use": True})
        for model in ("claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1"):
            self.assertEqual(L.AnthropicProvider(model).build("s", "t")["tool_choice"]["type"], "auto", model)

    def test_response_parsing(self):
        p = L.AnthropicProvider("claude-opus-5-5")
        cmd, note, _, problem = p.parse(L.anthropic_response("Sure. ```json\n{\"action\": \"open\", \"target\": \"obj_1\"}\n```"))
        self.assertEqual((cmd, problem), ({"action": "open", "target": "obj_1"}, None))
        cmd, _, _, problem = p.parse({"content": [{"type": "text", "text": "I can't help with that."}], "stop_reason": "refusal"})
        self.assertEqual((cmd, problem), (None, "refusal"))
        cmd, _, _, problem = p.parse({"content": [{"type": "thinking", "thinking": ""}], "stop_reason": "max_tokens"})
        self.assertEqual((cmd, problem), (None, "max_tokens"))
        cmd, _, _, _ = p.parse(L.anthropic_response({"action": "DONE"}))
        self.assertEqual(cmd, {"action": "done"})
        self.assertEqual(L.to_command({"action": "set", "target": "obj_1", "params": "{\"value\": 3}"})[0],
                         {"action": "set", "target": "obj_1", "value": 3})

    def test_refusal_is_not_retried_and_counts_as_invalid(self):
        mock = L.MockTransport([{"content": [{"type": "text", "text": "no"}], "role": "assistant", "stop_reason": "refusal",
                                 "usage": {"input_tokens": 10, "output_tokens": 1}}, {"action": "done"}])
        agent = L.MockLLMAgent(transport=mock)
        rec = R.run_episode(record(WET), episode(WET), agent)
        self.assertEqual(len(mock.calls), 2)
        self.assertEqual(rec["invalid_steps"], 1)


class TestOpenAICompatible(Temp):
    def test_request_shape_and_one_retry_on_parse_failure(self):
        replies = ["Let me think about it...", json.dumps({"action": "look"}), json.dumps({"action": "done"})]

        def handler(url, headers, body):
            text = replies.pop(0)
            return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 900, "completion_tokens": 30, "prompt_tokens_details": {"cached_tokens": 600}}}
        fake = L.FakeTransport(handler)
        os.environ["OPENAI_API_KEY"] = "sk-local-NOTREAL"
        try:
            agent = A.make_agent("llm:openai-compatible:llama3.1:8b@http://localhost:11434/v1", transport=fake, temperature=0.2, seed=7)
            rec = R.run_episode(record(WET), episode(WET), agent)
        finally:
            os.environ.pop("OPENAI_API_KEY", None)
        self.assertEqual(agent.name, "llm:openai-compatible:llama3.1:8b@http://localhost:11434/v1")
        self.assertEqual(len(fake.calls), 3)
        call = fake.calls[0]
        self.assertEqual(call["url"], "http://localhost:11434/v1/chat/completions")
        self.assertTrue(call["has_key"])
        body = call["body"]
        self.assertEqual((body["model"], body["temperature"], body["seed"]), ("llama3.1:8b", 0.2, 7))
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual([m["role"] for m in body["messages"]], ["system", "user"])
        self.assertIn("could not be used", fake.calls[1]["body"]["messages"][-1]["content"])
        self.assertEqual((rec["steps"], rec["invalid_steps"], rec["done_declared"]), (1, 0, True))
        self.assertEqual(rec["cost_usd"], 0.0)          # local model: zero price in prices.json
        self.assertEqual(rec["tokens"], {"input": 2700, "output": 90})

    def test_needs_a_base_url(self):
        with self.assertRaises(L.LLMConfigError):
            A.make_agent("llm:openai-compatible:some-model", transport=L.FakeTransport(_no_network))

    def test_results_never_record_credentials_embedded_in_the_base_url(self):
        # regression: run.json "agent_spec" kept the raw spec (user:password@ in the base URL), and the agent name kept
        # credential query parameters such as ?api-key=
        spec = "llm:openai-compatible:m1@https://alice:UNITPW123@gateway.invalid/v1?api-key=UNITQK456&region=eu"

        def handler(url, headers, body):
            return {"choices": [{"message": {"role": "assistant", "content": json.dumps({"action": "done"})}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2}}
        fake = L.FakeTransport(handler)
        summary = R.run(spec, "dev", [record(WET)], self.tmp / "res", verify="none", seeds=[0], emit=None, transport=fake,
                        use_cache=False, agent_options={"price_free": True})   # an unlisted hosted model must be declared
        self.assertEqual(summary["status"], "complete")
        self.assertEqual(fake.calls[0]["url"], "https://alice:UNITPW123@gateway.invalid/v1?api-key=UNITQK456&region=eu/chat/completions")
        text = tree_text(self.tmp / "res") + json.dumps(summary)
        for secret in ("UNITPW123", "UNITQK456", "alice"):
            self.assertNotIn(secret, text)
        info = json.loads((self.tmp / "res" / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(info["agent_spec"], "llm:openai-compatible:m1@https://gateway.invalid/v1?api-key=REDACTED&region=eu")
        self.assertEqual(info["agent"], info["agent_spec"])
        self.assertEqual(A.canonical_name("llm:openai-compatible:llama3.1:8b@http://localhost:11434/v1?x=1"),
                         "llm:openai-compatible:llama3.1:8b@http://localhost:11434/v1?x=1")
        failing = L.FakeTransport(lambda url, headers, body: (_ for _ in ()).throw(L.HTTPStatusError(401, "unauthorized")))
        agent = A.make_agent(spec, transport=failing, use_cache=False)
        rec = R.run_episode(record(WET), episode(WET), agent, out_dir=self.tmp / "err")
        self.assertEqual(rec["end_reason"], "agent_error")
        self.assertNotIn("UNITQK456", tree_text(self.tmp / "err"))
        self.assertNotIn("UNITPW123", tree_text(self.tmp / "err"))


class TestRetriesAndRateLimit(unittest.TestCase):
    def agent(self, transport, sleeps):
        return L.LLMAgent(provider="anthropic", model="claude-opus-5-5", transport=transport, use_cache=False, sleep=sleeps.append,
                          max_retries=3, backoff_base=1.0)

    def test_transient_errors_are_retried_with_exponential_backoff(self):
        errors = [L.HTTPStatusError(529, "overloaded"), L.HTTPStatusError(429, "slow down"), L.TransportError("reset")]

        def handler(url, headers, body):
            if errors:
                raise errors.pop(0)
            return L.anthropic_response({"action": "look"})
        sleeps = []
        agent = self.agent(L.FakeTransport(handler), sleeps)
        agent.reset("do it", {}, [])
        self.assertEqual(agent.act({}, None), {"action": "look"})
        self.assertEqual(len(sleeps), 3)
        self.assertTrue(0.5 <= sleeps[0] <= 1.0 and 1.0 <= sleeps[1] <= 2.0 and 2.0 <= sleeps[2] <= 4.0, sleeps)
        self.assertEqual(agent.usage()["retries"], 3)

    def test_retry_after_is_honoured_and_retries_are_bounded(self):
        sleeps = []
        agent = self.agent(L.FakeTransport(lambda u, h, b: (_ for _ in ()).throw(L.HTTPStatusError(503, "x", "7"))), sleeps)
        agent.reset("do it", {}, [])
        with self.assertRaises(L.LLMError):
            agent.act({}, None)
        self.assertEqual(sleeps, [7.0, 7.0, 7.0])

    def test_client_errors_are_fatal_without_retry(self):
        fake = L.FakeTransport(lambda u, h, b: (_ for _ in ()).throw(L.HTTPStatusError(400, "bad request")))
        sleeps = []
        agent = self.agent(fake, sleeps)
        agent.reset("do it", {}, [])
        with self.assertRaises(L.LLMFatalError):
            agent.act({}, None)
        self.assertEqual((len(fake.calls), sleeps), (1, []))

    def test_rate_limiter_spaces_requests(self):
        clock = {"t": 100.0}
        slept = []

        def sleep(s):
            slept.append(round(s, 6))
            clock["t"] += s
        limiter = L.RateLimiter(rpm=60, clock=lambda: clock["t"], sleep=sleep)
        for _ in range(3):
            limiter.wait()
        self.assertEqual(slept, [1.0, 1.0])
        self.assertEqual(L.RateLimiter(None).wait(), 0.0)


class TestPromptingAndPrices(unittest.TestCase):
    def test_compact_observation(self):
        obs = {"instruction": "x", "perception": "realistic", "robot": {"pos": [0, 0]}, "pending": [], "last_answer": None,
               "grid": {"blocked": [[i, i] for i in range(40)]},
               "visible_entities": {"obj_{:08x}".format(i): {"label": "thing", "pos": [i, 0], "soil": {"overall": "clean"},
                                                             "wetness": "looks dry", "color": None, "open": False} for i in range(30)}}
        text = L.compact_observation(obs, max_list=10, max_entities=12)
        data = json.loads(text)
        self.assertNotIn("instruction", data)
        self.assertNotIn("pending", data)
        self.assertNotIn("last_answer", data)
        self.assertEqual(len(data["grid"]["blocked"]), 11)
        self.assertEqual(len(data["visible_entities"]), 12)
        self.assertIn("obj_00000000", data["visible_entities"], "nearest entities are kept")
        self.assertEqual(data["visible_entities"]["obj_00000000"], {"label": "thing", "pos": [0, 0], "open": False})
        self.assertIn("visible_entities_omitted", data)

    def test_prices_file_is_marked_and_complete(self):
        doc = L.load_prices()
        self.assertIn("VERIFY CURRENT PRICES BEFORE USE", doc["_WARNING"])
        for model in ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"):
            p = L.price_for("anthropic", model)
            self.assertNotIn("unknown", p)
            self.assertGreater(p["output"], p["input"])
        self.assertTrue(L.price_for("anthropic", "claude-unknown-9")["unknown"])
        haiku = L.price_for("anthropic", "claude-haiku-5-5")
        self.assertGreater(L.cost_of({"input_tokens": 200000}, haiku), 200000 * haiku["input"] / 1e6, "long-context tier applies")


class TestAgentsUnit(unittest.TestCase):
    def test_parse_agent_specs(self):
        self.assertEqual(A.parse_agent_spec("llm:anthropic"), ("llm", "anthropic", "claude-opus-5-5", None))
        self.assertEqual(A.parse_agent_spec("llm:anthropic:claude-haiku-5-5")[2], "claude-haiku-5-5")
        self.assertEqual(A.parse_agent_spec("llm:openai-compatible:qwen2.5@http://127.0.0.1:8000/v1"),
                         ("llm", "openai-compatible", "qwen2.5", "http://127.0.0.1:8000/v1"))
        self.assertEqual(A.canonical_name("llm:openai-compatible:m@https://u:p@host/v1"), "llm:openai-compatible:m@https://host/v1")
        for bad in ("gpt", "llm:other:x"):
            with self.assertRaises(ValueError):
                A.parse_agent_spec(bad)
        self.assertIsInstance(A.make_agent("random", seed=3, unrelated_option=1), A.RandomValidAgent)
        self.assertTrue(A.make_agent("oracle").privileged)
        self.assertFalse(A.make_agent("search").privileged)
        self.assertIs(A.MockLLMAgent, L.MockLLMAgent)

    def test_fetch_goal_parser(self):
        self.assertEqual(A.parse_fetch_goal("Take the stemmed glass off the kitchen side table and set it on the marble table next door."),
                         ("glass", "table"))
        self.assertEqual(A.parse_fetch_goal("Put the cup in the sink."), ("cup", "sink"))
        self.assertIsNone(A.parse_fetch_goal("Turn on the lights."))
        self.assertIsNone(A.parse_fetch_goal("Could you carry the tray with the tea into the living room without spilling it?"))

    def test_random_agent_is_seeded_and_type_valid(self):
        catalog = [{"name": "pick_up", "params": {"target": {"type": "entity", "optional": False},
                                                  "hand": {"type": "str", "optional": True, "choices": ["left", "right"]}}},
                   {"name": "walk_to", "params": {"cell": {"type": "cell", "optional": False}}},
                   {"name": "wait", "params": {"seconds": {"type": "int", "optional": True, "min": 1, "max": 604800}}},
                   {"name": "move", "params": {"target": {"type": "room", "optional": False}}}]
        obs = {"room_id": "kitchen_1", "room": {"exits": ["hall_1"], "grid": {"cols": 4, "rows": 5}},
               "visible_entities": {"obj_0000aaaa": {}, "obj_0000bbbb": {}}, "holding": []}

        def trace(seed):
            agent = A.RandomValidAgent(seed=seed, p_done=0.0, max_actions=30)
            agent.reset("tidy up", obs, catalog)
            return [agent.act(obs, None) for _ in range(31)]
        a, b = trace(5), trace(5)
        self.assertEqual(a, b)
        self.assertNotEqual(a, trace(6))
        self.assertEqual(a[-1], {"action": "done"})
        for cmd in a[:-1]:
            spec = {c["name"]: c for c in catalog}[cmd["action"]]
            self.assertTrue(set(cmd) - {"action"} <= set(spec["params"]))
            if cmd["action"] == "pick_up":
                self.assertIn(cmd["target"], obs["visible_entities"])
            if cmd["action"] == "walk_to":
                self.assertTrue(0 <= cmd["cell"][0] < 4 and 0 <= cmd["cell"][1] < 5)
            if cmd["action"] == "move":
                self.assertIn(cmd["target"], ("hall_1", "kitchen_1"))
            if cmd["action"] == "wait" and "seconds" in cmd:
                self.assertTrue(1 <= cmd["seconds"] <= 601)


class TestTestSplitPrivacy(Temp):
    def test_test_episodes_never_record_raw_seeds_or_phrasings(self):
        ep = episode(WET, seed=0, split="test", tag="test:0123456789ab")
        ep["phrasing"] = "Private paraphrase: go next door avoiding the wet patch."
        rec = R.run_episode(record(WET), ep, A.make_agent("oracle"), out_dir=self.tmp / "res", perception="oracle",
                            private_dir=self.tmp / "private")
        self.assertNotIn("seed", rec)
        self.assertEqual(rec["perception"], "realistic", "the test split always runs realistic perception")
        self.assertEqual(rec["log"], "logs/T-089c40fb507289-01__test-0123456789ab.json")
        log = json.loads((self.tmp / "res" / rec["log"]).read_text(encoding="utf-8"))
        self.assertNotIn("seed", log)
        self.assertNotIn("instruction", log)
        self.assertNotIn("instruction_sha256", log, "an unkeyed digest of the held-out instruction is an offline oracle")
        self.assertIsNone(log["instruction_hmac"], "no split key in this environment: no digest at all")
        self.assertIsNone(log["replay"])
        texts = "".join(p.read_text(encoding="utf-8") for p in (self.tmp / "res").rglob("*") if p.is_file())
        self.assertNotIn("Private paraphrase", texts)
        self.assertFalse((self.tmp / "res" / "replays").exists())
        self.assertTrue(list((self.tmp / "private" / "replays").glob("*.replay.json")))
        self.assertTrue(rec["success"])
        with self.assertRaises(ValueError):
            R.make_episodes("test", [record(WET)], seeds=[5])

    def test_hidden_seeds_are_refused_for_public_splits(self):
        with self.assertRaises(ValueError):
            R.run_episode(record(WET), episode(WET, seed=900_000_123), A.make_agent("random"), out_dir=self.tmp / "res")
        self.assertFalse((self.tmp / "res").exists(), "nothing may be written for a refused hidden seed")

    def test_llm_text_restating_the_private_phrasing_never_reaches_results(self):
        # regression: an unusable LLM reply that repeats its instruction (as real models do) was logged verbatim in
        # the step's command["_raw"] under results/<run>/logs, even for test episodes
        ep = episode(WET, seed=0, split="test", tag="test:0123456789ab")
        ep["phrasing"] = PRIVATE_MARKER + " beta: please relocate next door without crossing the wet spot."
        agent = L.MockLLMAgent(responses=echoing_model())
        rec = R.run_episode(record(WET), ep, agent, out_dir=self.tmp / "res", private_dir=self.tmp / "private")
        self.assertEqual(rec["invalid_steps"], 2)
        self.assertTrue(rec["done_declared"])
        log = json.loads((self.tmp / "res" / rec["log"]).read_text(encoding="utf-8"))
        self.assertEqual(log["steps"][0]["command"], {"action": None, "_redacted": True, "_invalid": "no_command"})
        self.assertEqual(log["steps"][1]["command"], {"action": None, "_redacted": True, "_invalid": "invalid_tool_input"})
        self.assertEqual([s.get("sent") for s in log["steps"][:2]], [{"action": R.UNPARSEABLE}] * 2)
        self.assertEqual(log["steps"][2]["command"], {"action": "look"}, "a usable command is logged as sent")
        self.assertEqual(log["steps"][-1]["command"], {"action": "done"})
        self.assertNotIn(PRIVATE_MARKER, json.dumps(log))
        self.assertNotIn(PRIVATE_MARKER, tree_text(self.tmp / "res"))
        self.assertIn(PRIVATE_MARKER, tree_text(self.tmp / "private"), "the private transcript keeps the model's text")
        # re-scoring replays "sent" only, so the sanitized command changes nothing
        rescored = R.replay_log(log, record(WET), seed=0, phrasing=ep["phrasing"])
        self.assertEqual((rescored["success"], rescored["steps"]), (rec["success"], rec["steps"]))
        # public episodes keep the annotation for debugging
        dev = R.run_episode(record(WET), episode(WET), L.MockLLMAgent(responses=echoing_model()), out_dir=self.tmp / "dev")
        dev_log = json.loads((self.tmp / "dev" / dev["log"]).read_text(encoding="utf-8"))
        self.assertIn("Understood - the resident asked:", dev_log["steps"][0]["command"]["_raw"])
        self.assertEqual(dev_log["steps"][0]["command"]["_invalid"], "no_command")

    def test_loggable_command_keeps_only_what_reaches_the_simulator_for_private_episodes(self):
        secret = PRIVATE_MARKER + " restated"
        cases = [({"action": "look", "_raw": secret, "_why": secret}, {"action": "look"}),
                 ({"action": "", "explanation": secret, "_invalid": "no_command"}, {"action": None, "_redacted": True, "_invalid": "no_command"}),
                 ({"action": None, "_invalid": secret, "_raw": secret}, {"action": None, "_redacted": True}),
                 (secret, {"action": None, "_redacted": True}),
                 ({"action": "look", "self": secret}, {"action": None, "_redacted": True}),
                 ({"action": "Done", "reason": secret}, {"action": "done"}),
                 ({"action": "inspect", "target": ["a", "b"]}, {"action": "inspect", "target": '["a", "b"]'})]
        for command, expected in cases:
            self.assertEqual(R.loggable_command(command, private=True), expected, command)
            self.assertEqual(R.loggable_command(command, private=False), json.loads(json.dumps(command)))


PRIVATE_MARKER = "ZQXPRIV-UNITTEST"


def echoing_model():
    """Mock model that restates its INSTRUCTION line: text-only twice (the reply and its parse retry), then a malformed
    tool call quoting it twice, then a usable command that keeps the goal in its memory note, then done."""
    def respond(body, index):
        instruction = body["messages"][-1]["content"].splitlines()[0][len("INSTRUCTION: "):]
        if index < 2:
            return "Understood - the resident asked: {} Let me first check my surroundings.".format(instruction)
        if index < 4:
            return {"action": "", "explanation": "Task is: " + instruction}
        if index == 4:
            return {"action": "look", "note": "Goal: " + instruction}
        return {"action": "done"}
    return respond


def tree_text(path):
    path = Path(path)
    return "".join(p.read_text(encoding="utf-8", errors="replace") for p in path.rglob("*") if p.is_file()) if path.exists() else ""


class TestTestSplitCache(Temp):
    """Regression: for llm agents the response cache was always created under <repo>/results/cache, so every test-split
    response (model text, tool input and memory note) was written in clear text where results are published."""
    PHRASING = PRIVATE_MARKER + " alpha: go to the next room and keep clear of the puddle."

    def _test_run(self, transport, out="res", **filters):
        ep = episode(WET, seed=0, split="test", tag="test:0123456789ab")
        ep["phrasing"] = self.PHRASING
        filters.setdefault("private_dir", self.tmp / "private")
        with mock.patch.object(R, "make_episodes", lambda *a, **k: ([dict(ep)], "splits")), \
                mock.patch.object(R, "_results_root", lambda: self.tmp / "repo_results"):
            return R.run("llm:anthropic:claude-opus-5-5", "test", [record(WET)], self.tmp / out, verify="none", emit=None,
                         run_id="t-cache", transport=transport, **filters)

    def test_test_split_cache_is_private_and_run_json_records_only_the_flag(self):
        transport = NetworkStandIn(L.MockTransport(echoing_model()))   # only network answers use the default cache
        summary = self._test_run(transport)
        self.assertEqual(summary["status"], "complete")
        self.assertGreater(len(transport.calls), 0)
        cache_files = list((self.tmp / "private" / "cache").rglob("*.json"))
        self.assertTrue(cache_files, "test-split responses are cached in the private directory")
        self.assertIn(PRIVATE_MARKER, tree_text(self.tmp / "private" / "cache"))
        self.assertFalse((self.tmp / "repo_results").exists(), "nothing may be cached under the results root")
        self.assertNotIn(PRIVATE_MARKER, tree_text(self.tmp / "res"))
        info = json.loads((self.tmp / "res" / "run.json").read_text(encoding="utf-8"))
        self.assertIsNone(info["llm_cache_dir"])
        self.assertTrue(info["llm_cache_private"])
        self.assertNotIn(str(self.tmp / "private"), json.dumps(info))

    def test_test_split_rerun_with_a_private_cache_dir_makes_no_calls(self):
        shared = self.tmp / "private_cache"
        first = NetworkStandIn(L.MockTransport(echoing_model()))
        self._test_run(first, out="a", cache_dir=shared)
        self.assertGreater(len(first.calls), 0)
        second = NetworkStandIn(L.FakeTransport(_no_network))
        summary = self._test_run(second, out="b", cache_dir=shared)
        self.assertEqual(summary["status"], "complete")
        self.assertEqual(len(second.calls), 0)
        self.assertFalse((self.tmp / "private" / "cache").exists())
        self.assertNotIn(PRIVATE_MARKER, tree_text(self.tmp / "a") + tree_text(self.tmp / "b"))

    def test_test_split_refuses_public_cache_and_private_dirs_before_writing(self):
        from house_sim.catalog import ROOT
        in_repo = ROOT / "results" / "unit_test_cache_should_not_exist"
        attempts = [{"cache_dir": in_repo}, {"cache_dir": self.tmp / "res" / "cache"},
                    {"cache_dir": self.tmp / "repo_results" / "cache"}, {"agent_options": {"cache_dir": str(in_repo)}},
                    {"private_dir": ROOT / "results" / "unit_test_private_should_not_exist"},
                    {"private_dir": self.tmp / "res" / "private"}]
        for filters in attempts:
            transport = L.FakeTransport(_no_network)
            with self.assertRaises(ValueError, msg=str(filters)):
                self._test_run(transport, **filters)
            self.assertEqual(transport.calls, [])
            self.assertFalse(in_repo.exists())
            self.assertFalse((ROOT / "results" / "unit_test_private_should_not_exist").exists())
            self.assertFalse((self.tmp / "res").exists(), "refused before the results directory is created")
            self.assertFalse((self.tmp / "repo_results").exists())

    def test_no_cache_option_disables_the_test_split_cache(self):
        transport = L.MockTransport(echoing_model())
        self._test_run(transport, use_cache=False, agent_options={"cache_dir": str(self.tmp / "opt_cache")})
        self.assertFalse((self.tmp / "private" / "cache").exists())
        self.assertFalse((self.tmp / "opt_cache").exists())
        info = json.loads((self.tmp / "res" / "run.json").read_text(encoding="utf-8"))
        self.assertFalse(info["llm_cache_private"])
        self.assertNotIn("cache_dir", info["agent_options"])


class TestEndToEnd(Temp):
    @classmethod
    def setUpClass(cls):
        cls.specs = [record(WET), record(GLASSES)]
        cls.verified = R.verify_on_the_fly(cls.specs, seeds=(0, 1), perception="realistic", workers=1)

    def test_oracle_succeeds_where_the_spec_verifies(self):
        checked = 0
        for spec in self.specs:
            if not self.verified[spec["catalog_id"]]["verified"]:
                continue
            for seed in (0, 1):
                rec = R.run_episode(spec, episode(spec["catalog_id"], seed), A.make_agent("oracle"))
                self.assertTrue(rec["success"], (spec["catalog_id"], seed, rec["failures"], rec["end_reason"]))
                self.assertTrue(rec["privileged"])
                checked += 1
        self.assertGreater(checked, 0, "expected at least one example spec to verify in realistic mode")

    def test_builtin_agents_end_to_end_with_results_layout(self):
        from house_sim.kernel.engine import HouseEnv
        for spec_string in ("random", "search", "oracle", "mock"):
            out = self.tmp / spec_string
            summary = R.run(spec_string, "dev", self.specs, out, verify="fly", verify_seeds=[0], seeds=[0], emit=None,
                            run_id="t-" + spec_string, agent_options={"seed": 1})
            self.assertEqual(summary["status"], "complete", spec_string)
            info = json.loads((out / "run.json").read_text(encoding="utf-8"))
            for key in ("agent", "split", "code_fingerprint", "spec_hashes", "commitment_root", "started", "ended", "caps", "verification"):
                self.assertIn(key, info)
            self.assertEqual(info["verification"]["mode"], "on_the_fly")
            self.assertEqual(set(info["spec_hashes"]), {s["catalog_id"] for s in self.specs if info["verification"]["per_spec"][s["catalog_id"]]["verified"]})
            records = [json.loads(l) for l in (out / "episodes.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(records), summary["episodes"])
            self.assertGreater(len(records), 0)
            for rec in records:
                self.assertEqual(set(rec), set(R.RECORD_KEYS) | {"seed"}, spec_string)
                self.assertEqual(rec["split"], "dev")
                self.assertEqual(rec["seed"], 0)
                self.assertEqual(rec["privileged"], spec_string == "oracle")
                self.assertEqual(rec["agent"], spec_string)
                self.assertEqual(rec["perception"], "realistic")
                log = json.loads((out / rec["log"]).read_text(encoding="utf-8"))
                self.assertEqual(log["seed"], 0)
                rescored = R.replay_log(log, {s["catalog_id"]: s for s in self.specs}[rec["catalog_id"]])
                self.assertEqual(rescored["success"], rec["success"], (spec_string, rec["catalog_id"]))
                self.assertEqual(rescored["steps"], rec["steps"])
                env = HouseEnv.replay(out / log["replay"])
                self.assertEqual(env.evaluate()["success"] and rec["done_declared"], rec["success"])
                if spec_string == "oracle":
                    self.assertTrue(rec["success"], rec)
            if spec_string == "mock":
                self.assertGreater(summary["tokens"]["input"], 0)

    def test_parallel_workers_give_the_same_records(self):
        one = R.run("oracle", "dev", self.specs, self.tmp / "w1", verify="none", seeds=[0], emit=None, workers=1)
        two = R.run("oracle", "dev", self.specs, self.tmp / "w2", verify="none", seeds=[0], emit=None, workers=2)
        strip = lambda path: [{k: v for k, v in json.loads(l).items() if k != "wall_s"}
                              for l in (path / "episodes.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(strip(self.tmp / "w1"), strip(self.tmp / "w2"))
        self.assertEqual(one["successes"], two["successes"])

    def test_dry_run_estimates_without_calls_or_files(self):
        out = io.StringIO()
        with redirect_stdout(out):
            report = R.run("llm:anthropic:claude-opus-5-5", "dev", self.specs, self.tmp / "dry", dry_run=True, verify="none",
                           seeds=[0, 1], caps={"max_steps": 20})
        self.assertTrue(report["dry_run"])
        self.assertEqual(len(report["episodes"]), 4)
        self.assertTrue(all(row["steps"] <= 20 for row in report["episodes"]))
        self.assertGreater(report["totals"]["input"], 0)
        self.assertGreater(report["totals"]["expected_usd"], 0)
        self.assertGreaterEqual(report["totals"]["worst_usd"], report["totals"]["expected_usd"])
        self.assertIn("DRY RUN", out.getvalue())
        self.assertIn("VERIFY CURRENT PRICES", out.getvalue())
        self.assertFalse((self.tmp / "dry").exists())

    def test_cli(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = R.main(["--run-agent", "random", "--split", "dev", "--ids", WET, "--seeds", "0", "--out", str(self.tmp / "cli"),
                           "--verify-on-the-fly", "--verify-seeds", "0", "--agent-opt", "seed=4", "--quiet"])
        self.assertEqual(code, 0, err.getvalue())
        self.assertTrue((self.tmp / "cli" / "episodes.jsonl").is_file())
        info = json.loads((self.tmp / "cli" / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(info["agent_options"], {"seed": 4})
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            code = R.main(["--run-agent", "llm:anthropic:claude-opus-5-5", "--ids", WET, "--seeds", "0", "--allow-unverified",
                           "--out", str(self.tmp / "nonet")])
        self.assertEqual(code, 2)
        self.assertIn("--allow-network", err.getvalue())
        self.assertFalse((self.tmp / "nonet").exists())
        dry = io.StringIO()
        with redirect_stdout(dry):
            code = R.main(["--run-agent", "llm:anthropic:claude-haiku-5-5", "--ids", WET, "--seeds", "0", "--allow-unverified",
                           "--dry-run", "--max-cost-usd", "5"])
        self.assertEqual(code, 0)
        self.assertIn("TOTAL", dry.getvalue())
        # the house_sim CLI hands --run-agent to this module
        from house_sim.eval import cli as eval_cli
        self.assertTrue(eval_cli.wants(["--run-agent", "random"]))


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def instruction_for(cid, seed, phrasing):
    from house_sim.tasks.runner import build
    config, task, plans = build(record(cid)["spec"], seed)
    return R.apply_phrasing(task, phrasing, plans["bindings"])["instruction"]


class TestKeyedPrivateDigests(Temp):
    """Regression (red team, harness #1): test logs carried instruction_sha256 = plain SHA-256 of the held-out
    instruction (and agent_info.memory_note_sha256), so anyone could recover or confirm the phrasing offline by
    enumerating the public phrasings x room ids. Both digests are now HMAC-SHA256 under a split-key subkey."""
    TAG = "test:0123456789ab"

    def _run(self, key=THROWAWAY_KEY):
        ep = episode(WET, seed=0, split="test", tag=self.TAG)
        ep["phrasing"] = PRIVATE_MARKER + " gamma: go to the next room and keep off the wet patch."
        env = {"HOUSE_AI_SPLIT_KEY": key} if key else {}
        with mock.patch.dict(os.environ, env):
            if not key:
                os.environ.pop("HOUSE_AI_SPLIT_KEY", None)
            rec = R.run_episode(record(WET), ep, L.MockLLMAgent(responses=echoing_model()), out_dir=self.tmp / "res",
                                private_dir=self.tmp / "private")
        log = json.loads((self.tmp / "res" / rec["log"]).read_text(encoding="utf-8"))
        return ep, rec, log

    def test_instruction_and_memory_note_digests_are_keyed(self):
        ep, rec, log = self._run()
        instruction = instruction_for(WET, 0, ep["phrasing"])
        note = "Goal: " + instruction                    # the echoing model's memory note
        key = bytes.fromhex(THROWAWAY_KEY)
        self.assertNotIn("instruction_sha256", log)
        self.assertNotIn("memory_note_sha256", log["agent_info"])
        self.assertEqual(log["private_digest_scheme"], R.PRIVATE_DIGEST_SCHEME)
        self.assertEqual(log["instruction_hmac"], R.private_text_digest("instruction", WET, self.TAG, instruction, key=key))
        self.assertEqual(log["agent_info"]["memory_note_hmac"], R.private_text_digest("memory_note", WET, self.TAG, note, key=key))
        # the offline attack: an unkeyed hash of the right guess appears nowhere in results/
        results = tree_text(self.tmp / "res")
        for guess in (instruction, note, ep["phrasing"]):
            self.assertNotIn(sha256(guess), results)
        self.assertNotIn(PRIVATE_MARKER, results)
        # the digest needs the key: another key or another episode gives another value
        self.assertNotEqual(log["instruction_hmac"], R.private_text_digest("instruction", WET, self.TAG, instruction, key=b"\x01" * 32))
        self.assertNotEqual(log["instruction_hmac"], R.private_text_digest("instruction", WET, "test:ffffffffffff", instruction, key=key))
        self.assertNotEqual(log["instruction_hmac"], R.private_text_digest("memory_note", WET, self.TAG, instruction, key=key))
        with mock.patch.dict(os.environ, {"HOUSE_AI_SPLIT_KEY": THROWAWAY_KEY}):
            self.assertEqual(R.private_text_digest("instruction", WET, self.TAG, instruction), log["instruction_hmac"])

    def test_without_a_key_no_digest_is_recorded(self):
        ep, rec, log = self._run(key=None)
        self.assertIsNone(log["instruction_hmac"])
        self.assertIsNone(log["agent_info"]["memory_note_hmac"])
        self.assertNotIn(sha256(instruction_for(WET, 0, ep["phrasing"])), tree_text(self.tmp / "res"))
        self.assertIsNone(R.private_text_digest("instruction", WET, self.TAG, "anything"))


HOSTED = "llm:openai-compatible:gpt-5@https://api.openai.example/v1"


def openai_handler(command=None, prompt_tokens=200000, completion_tokens=4000):
    def handler(url, headers, body):
        text = json.dumps(command or {"action": "wait", "seconds": 1})
        return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}}
    return handler


class TestUnpricedModels(Temp):
    """Regression (red team, harness #2): an openai-compatible model missing from prices.json priced at $0 on any host,
    so --max-cost-usd / --max-episode-cost-usd never tripped against a paid endpoint (11 calls, 2.2M input tokens,
    cost_usd 0.0) and the dry run reported $0.00 without a warning."""

    def test_price_resolution(self):
        hosted = L.price_for("openai-compatible", "gpt-5", base_url="https://api.openai.example/v1")
        self.assertTrue(hosted["unpriced"] and hosted["unknown"])
        self.assertEqual(hosted["source"], "none")
        for url in ("http://localhost:11434/v1", "http://127.0.0.1:8000/v1", "http://[::1]:8000/v1", "http://127.1.2.3/v1"):
            local = L.price_for("openai-compatible", "llama3.1:8b", base_url=url)
            self.assertEqual((local["source"], local["input"], local["output"]), ("loopback", 0.0, 0.0), url)
            self.assertNotIn("unpriced", local)
        for url in ("http://localhost.evil.example/v1", "http://localhost@evil.example/v1", "http://127.0.0.1.nip.io/v1",
                    "https://gateway.invalid/v1", None, "not a url"):
            self.assertTrue(L.price_for("openai-compatible", "m", base_url=url).get("unpriced"), url)
        free = L.price_for("openai-compatible", "gpt-5", base_url="https://api.openai.example/v1", price_free=True)
        self.assertEqual((free["source"], free.get("declared_free"), free.get("unpriced")), ("declared_free", True, None))
        listed = L.price_for("anthropic", "claude-opus-5-5", price_free=True)
        self.assertEqual((listed["source"], listed["input"]), ("model", 4.0), "price_free never zeroes a listed price")
        conservative = L.price_for("anthropic", "claude-unknown-9")
        self.assertEqual(conservative["source"], "default")
        self.assertGreater(conservative["input"], 0)
        self.assertNotIn("unpriced", conservative)
        self.assertIsNone(L.price_problem(hosted, "openai-compatible", "gpt-5", "http://localhost:1/v1", cost_capped=False))
        self.assertIn("cost cap", L.price_problem(hosted, "openai-compatible", "gpt-5", "http://localhost:1/v1", cost_capped=True))
        self.assertIn("not a loopback host", L.price_problem(hosted, "openai-compatible", "gpt-5", "https://api.openai.example/v1"))
        self.assertIsNone(L.price_problem(conservative, "anthropic", "claude-unknown-9", None, cost_capped=True))

    def test_run_refuses_an_unpriced_hosted_model_before_any_call_or_file(self):
        for caps in ({"max_cost_usd": 0.01, "max_cost_usd_episode": 0.01}, {"max_cost_usd": 0.01}, None):
            fake = L.FakeTransport(openai_handler())
            with self.assertRaises(ValueError) as ctx:
                R.run(HOSTED, "dev", [record(WET)], self.tmp / "r", caps=caps, verify="none", seeds=[0], emit=None, transport=fake)
            self.assertIn("no price for model 'gpt-5'", str(ctx.exception))
            self.assertIn("price_free=true", str(ctx.exception))
            self.assertEqual(fake.calls, [])
            self.assertFalse((self.tmp / "r").exists())
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            code = R.main(["--run-agent", HOSTED, "--ids", WET, "--seeds", "0", "--allow-unverified", "--allow-network",
                           "--max-cost-usd", "0.01", "--out", str(self.tmp / "cli")])
        self.assertEqual(code, 2)
        self.assertIn("no price for model", err.getvalue())
        self.assertFalse((self.tmp / "cli").exists())

    def test_agent_refuses_unpriced_requests_when_a_cap_or_the_network_is_involved(self):
        fake = L.FakeTransport(openai_handler())
        rec = R.run_episode(record(WET), episode(WET), A.make_agent(HOSTED, transport=fake, use_cache=False),
                            caps={"max_cost_usd_episode": 0.01})
        self.assertEqual((rec["end_reason"], rec["steps"], fake.calls), ("agent_error", 0, []))
        ledger = A.CostLedger(0.01)
        rec = R.run_episode(record(WET), episode(WET), A.make_agent(HOSTED, transport=fake, use_cache=False), ledger=ledger)
        self.assertEqual((rec["end_reason"], fake.calls), ("agent_error", []))
        net = NetworkStandIn(L.FakeTransport(openai_handler()))
        rec = R.run_episode(record(WET), episode(WET), A.make_agent(HOSTED, transport=net, use_cache=False))
        self.assertEqual((rec["end_reason"], net.calls), ("agent_error", []), "a real non-loopback endpoint is never priced at $0")

    def test_declared_free_and_loopback_models_run_and_are_recorded(self):
        done = openai_handler({"action": "done"}, 10, 2)
        summary = R.run(HOSTED, "dev", [record(WET)], self.tmp / "free", caps={"max_cost_usd": 0.01}, verify="none", seeds=[0],
                        emit=None, transport=L.FakeTransport(done), use_cache=False, agent_options={"price_free": True})
        self.assertEqual(summary["status"], "complete")
        info = json.loads((self.tmp / "free" / "run.json").read_text(encoding="utf-8"))
        self.assertEqual((info["prices"]["source"], info["prices"]["declared_free"], info["prices"]["unpriced"]), ("declared_free", True, False))
        self.assertTrue(info["agent_options"]["price_free"])
        local = "llm:openai-compatible:llama3.1:8b@http://127.0.0.1:11434/v1"
        summary = R.run(local, "dev", [record(WET)], self.tmp / "local", caps={"max_cost_usd": 1.0}, verify="none", seeds=[0],
                        emit=None, transport=L.FakeTransport(done), use_cache=False)
        self.assertEqual(summary["status"], "complete")
        info = json.loads((self.tmp / "local" / "run.json").read_text(encoding="utf-8"))
        self.assertEqual((info["prices"]["source"], info["prices"]["unknown"]), ("loopback", False))
        info = json.loads(next((self.tmp / "local" / "logs").glob("*.json")).read_text(encoding="utf-8"))
        self.assertEqual(info["agent_info"]["price"]["source"], "loopback")

    def test_dry_run_flags_an_unpriced_model(self):
        out = io.StringIO()
        with redirect_stdout(out):
            report = R.run(HOSTED, "dev", [record(WET)], self.tmp / "dry", dry_run=True, verify="none", seeds=[0],
                           caps={"max_cost_usd": 0.01, "max_steps": 5})
        self.assertTrue(report["price"]["unpriced"])
        self.assertEqual(len(report["warnings"]), 2)
        self.assertIn("NO PRICE", out.getvalue())
        self.assertIn("THE REAL RUN WOULD BE REFUSED", out.getvalue())
        self.assertFalse((self.tmp / "dry").exists())


class TestCacheProvenance(Temp):
    """Regression (red team, harness #3): the response cache was keyed only by (provider, URL, body), so a mock run
    (mock:claude-opus-5-5 with tool_choice=auto builds byte-identical Anthropic bodies) or an injected FakeTransport
    run poisoned the cache, and a later real llm:anthropic run replayed the canned answers with zero network calls."""
    MOVE = {"action": "move", "target": "living_room_1", "avoid": ["wet"]}

    def test_mock_answers_never_serve_a_real_run(self):
        shared = self.tmp / "cache"
        poisoned = R.run("mock:claude-opus-5-5", "dev", [record(WET)], self.tmp / "mock", verify="none", seeds=[0], emit=None,
                         cache_dir=shared, agent_options={"tool_choice": "auto", "responses": [self.MOVE, {"action": "done"}]})
        self.assertEqual(poisoned["successes"], 1)
        self.assertTrue(list(shared.rglob("*.json")), "the mock run wrote cache entries")
        real = NetworkStandIn(L.FakeTransport(anthropic_handler([{"action": "look"}])))
        summary = R.run("llm:anthropic:claude-opus-5-5", "dev", [record(WET)], self.tmp / "real", verify="none", seeds=[0],
                        emit=None, cache_dir=shared, transport=real)
        self.assertEqual(len(real.calls), 2, "a real run is answered by the network, never by cached mock answers")
        self.assertEqual(summary["successes"], 0)
        self.assertEqual(summary["llm_cache_hits"], 0)
        info = json.loads((self.tmp / "real" / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(info["llm_transport"], "network")
        self.assertEqual(info["llm_cache"]["source"], "network")
        mock_info = json.loads((self.tmp / "mock" / "run.json").read_text(encoding="utf-8"))
        self.assertEqual((mock_info["llm_transport"], mock_info["llm_cache"]["namespace"]), ("mock", "mock"))

    def test_identical_bodies_get_different_cache_keys(self):
        catalog = [{"name": "look", "category": "", "doc": "Look around.", "params": {}, "duration_s": 1}]
        mocked = L.MockLLMAgent(model="claude-opus-5-5", tool_choice="auto")
        real = L.LLMAgent(provider="anthropic", model="claude-opus-5-5", transport=NetworkStandIn(L.FakeTransport(_no_network)))
        faked = L.LLMAgent(provider="anthropic", model="claude-opus-5-5", transport=L.FakeTransport(_no_network))
        bodies, keys = [], []
        for agent in (mocked, real, faked):
            agent.reset("go", {}, catalog)
            body = agent.provider.build(agent.system_text, "turn")
            bodies.append(body)
            namespace, source = agent._cache_ids()
            keys.append(L.ResponseCache.key(namespace, agent.provider.url(), body, source))
        self.assertEqual(bodies[0], bodies[1])
        self.assertEqual(bodies[1], bodies[2])
        self.assertEqual(len(set(keys)), 3)
        self.assertEqual([a._cache_ids() for a in (mocked, real, faked)], [("mock", "mock"), ("anthropic", "network"), ("anthropic", "fake")])
        # an entry whose stored meta claims another source is a miss even at the right path
        cache = L.ResponseCache(self.tmp / "c")
        cache.put(keys[1], {"forged": True}, {"provider": "anthropic", "source": "fake"})
        self.assertIsNone(cache.get(keys[1], source="network", provider="anthropic"))
        cache.put(keys[1], {"real": True}, {"provider": "anthropic", "source": "network"})
        self.assertEqual(cache.get(keys[1], source="network", provider="anthropic"), {"real": True})

    def test_injected_transports_never_write_the_default_cache(self):
        root = self.tmp / "repo_results"
        with mock.patch.object(R, "_results_root", lambda: root):
            fake = L.FakeTransport(anthropic_handler([]))           # answers done at once
            R.run("llm:anthropic:claude-opus-5-5", "dev", [record(WET)], self.tmp / "fake", verify="none", seeds=[0], emit=None,
                  transport=fake)
            self.assertEqual(len(fake.calls), 1)
            self.assertFalse((root / "cache").exists(), "a fake transport must not write the default real-run cache")
            info = json.loads((self.tmp / "fake" / "run.json").read_text(encoding="utf-8"))
            self.assertEqual((info["llm_transport"], info["llm_cache"]["enabled"], info["llm_cache_dir"]), ("fake", False, None))
            first = NetworkStandIn(L.FakeTransport(anthropic_handler([{"action": "look"}])))
            one = R.run("llm:anthropic:claude-opus-5-5", "dev", [record(WET)], self.tmp / "net1", verify="none", seeds=[0],
                        emit=None, transport=first)
            self.assertEqual(len(first.calls), 2, "not served the fake run's 'done'")
            self.assertTrue((root / "cache").is_dir(), "network answers use the default cache")
            self.assertEqual((one["llm_responses"], one["llm_cache_hits"]), (2, 0))
            second = NetworkStandIn(L.FakeTransport(_no_network))
            two = R.run("llm:anthropic:claude-opus-5-5", "dev", [record(WET)], self.tmp / "net2", verify="none", seeds=[0],
                        emit=None, transport=second)
            self.assertEqual(len(second.calls), 0, "a real rerun is still served from genuine network entries")
            self.assertEqual((two["llm_responses"], two["llm_cache_hits"], two["llm_cache_hit_rate"]), (2, 2, 1.0))
            log = json.loads(next((self.tmp / "net2" / "logs").glob("*.json")).read_text(encoding="utf-8"))
            self.assertEqual((log["llm_cache"]["hits"], log["llm_cache"]["responses"], log["llm_cache"]["source"]), (2, 2, "network"))
            # an explicit cache_dir shared by a fake run and a real run: still separate namespaces
            explicit = self.tmp / "explicit"
            R.run("llm:anthropic:claude-opus-5-5", "dev", [record(WET)], self.tmp / "fake2", verify="none", seeds=[0], emit=None,
                  transport=L.FakeTransport(anthropic_handler([])), cache_dir=explicit)
            self.assertTrue(list(explicit.rglob("*.json")))
            third = NetworkStandIn(L.FakeTransport(anthropic_handler([{"action": "look"}])))
            R.run("llm:anthropic:claude-opus-5-5", "dev", [record(WET)], self.tmp / "net3", verify="none", seeds=[0], emit=None,
                  transport=third, cache_dir=explicit)
            self.assertEqual(len(third.calls), 2)


class TestVersionFingerprints(Temp):
    """Regression (red team, harness #4): audit.fingerprint() deliberately excludes house_sim/eval, and run.json /
    logs recorded nothing else, so runs scored by different harness code (done protocol, normalize_command, prompts,
    prices) merged silently. run.json and every log now record both fingerprints, and replay_log reports mismatches."""

    def test_run_json_and_every_log_record_both_fingerprints(self):
        from house_sim import audit
        before = (audit.fingerprint(), audit.harness_fingerprint())
        R.run("oracle", "dev", [record(WET), record(GLASSES)], self.tmp / "run", verify="none", seeds=[0], emit=None)
        after = (audit.fingerprint(), audit.harness_fingerprint())
        info = json.loads((self.tmp / "run" / "run.json").read_text(encoding="utf-8"))
        # the code at run time (before/after only differ if someone edits the package while this test runs)
        self.assertIn((info["code_fingerprint"], info["harness_fingerprint"]), (before, after))
        self.assertNotEqual(info["harness_fingerprint"], info["code_fingerprint"])
        self.assertEqual(info["harness_version"], R.HARNESS_VERSION)
        self.assertEqual((info["perception_modes"], info["privileged_perception"]), (["realistic"], False))
        self.assertEqual(info["planned_episodes"], [[WET, "dev:0"], [GLASSES, "dev:0"]])
        self.assertEqual(info["planned_episodes_sha256"], sha256(json.dumps(info["planned_episodes"], separators=(",", ":"))))
        R.run("random", "dev", [record(WET)], self.tmp / "oracle_perception", verify="none", seeds=[0], emit=None, perception="oracle")
        debug = json.loads((self.tmp / "oracle_perception" / "run.json").read_text(encoding="utf-8"))
        self.assertEqual((debug["perception_modes"], debug["privileged_perception"]), (["oracle"], True))
        logs = list((self.tmp / "run" / "logs").glob("*.json"))
        self.assertEqual(len(logs), 2)
        same = {"code_fingerprint": info["code_fingerprint"], "harness_fingerprint": info["harness_fingerprint"]}
        for path in logs:
            log = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual((log["code_fingerprint"], log["harness_fingerprint"]), (info["code_fingerprint"], info["harness_fingerprint"]))
            with mock.patch.object(R, "versions", lambda: dict(same)):
                rescored = R.replay_log(log, record(log["catalog_id"]))
                self.assertTrue(rescored["code_fingerprint_match"] and rescored["harness_fingerprint_match"])
                stale = dict(log, harness_fingerprint="0" * 64)
                self.assertFalse(R.replay_log(stale, record(log["catalog_id"]))["harness_fingerprint_match"])
                self.assertTrue(R.replay_log(stale, record(log["catalog_id"]))["code_fingerprint_match"])
                self.assertIsNone(R.replay_log({k: v for k, v in log.items() if k != "harness_fingerprint"},
                                               record(log["catalog_id"]))["harness_fingerprint_match"])

    def test_harness_fingerprint_changes_with_harness_code(self):
        from house_sim import audit
        package = self.tmp / "house_sim"
        shutil.copytree(Path(audit.__file__).resolve().parent / "eval", package / "eval",
                        ignore=shutil.ignore_patterns("__pycache__"))
        with mock.patch.object(audit, "__file__", str(package / "audit.py")):
            before = audit.harness_fingerprint()
            self.assertEqual(before, audit.harness_fingerprint(), "same harness code, same fingerprint")
            run_py = package / "eval" / "run.py"
            text = run_py.read_text(encoding="utf-8")
            self.assertIn('"require_done": True', text)
            run_py.write_text(text.replace('"require_done": True', '"require_done": False', 1), encoding="utf-8")
            after = audit.harness_fingerprint()
            prices = package / "eval" / "prices.json"
            prices.write_text(prices.read_text(encoding="utf-8").replace('"as_of"', '"as_of_edited"', 1), encoding="utf-8")
            edited_prices = audit.harness_fingerprint()
        self.assertNotEqual(before, after, "a harness edit (done protocol default) must change the harness fingerprint")
        self.assertNotEqual(after, edited_prices, "prices.json is part of the harness fingerprint")


if __name__ == "__main__":
    unittest.main()
