"""tools/robot_step.py (the robot's one command) and tools/audit_robot_transcript.py (the proof it used nothing else).

Offline, with a temporary home, normal and HOUSE_SIM_STRICT=1. The reference episode's commands come from the oracle's own log
under the same nonce, so these tests follow the spec. See docs/EVAL_HARNESS.md, "Claude as the driver".
"""
import copy
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from house_sim.eval import llm as L
from house_sim.eval import run as R
from house_sim.eval.agents import EVALUATOR_KEYS, make_agent as make_harness_agent
from house_sim.eval.common import load_authored
from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.replays import read as read_replay
from house_sim.tasks.runner import build

ROOT = Path(__file__).resolve().parent.parent
audit_limit = 30000                     # the Bash tool's output limit, which a page must stay below
BED = "T-ed45170be6256e-01"
NONCE = "robot-step-test-nonce"
FIND_SPEC = {
    "catalog_id": "X-ROBOT-STEP-TEST-01", "title": "A person and an event", "schema": 4, "perception": "realistic", "risk_level": 0,
    "fidelity": "S", "house": {"residents": "minimal", "pets": "none", "gen": 2, "require": ["kitchen", "living_room"]},
    "setup": [{"op": "room", "var": "liv", "type": "living_room", "pick": "first"},
              {"op": "spawn", "var": "mug", "type": "mug", "floor": "{liv}", "props": {"label": "blue mug", "color": "blue"}},
              {"op": "event", "at_s": 5, "effect": "people.request", "args": {"person": "resident_1", "text": "Also, please water the fern."}}],
    "instruction": "Take the blue mug to the kitchen.",
    "phrasings": ["Take the blue mug to the kitchen.", "The blue mug goes to the kitchen.", "Bring the blue mug to the kitchen."],
    "success": [{"label": "mug_lifted_secret_label", "pred": {"path": "entities.{mug}.location", "op": "eq", "value": "robot"}}],
    "budget": {"steps": 40, "time_s": 300},
    "reference": [{"skill": "fetch", "obj": "{mug}"}], "wrong": [],
}


def _load_tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


robot = _load_tool("robot_step")
audit = _load_tool("audit_robot_transcript")


def call(*argv):
    lines = []
    status = robot.main([str(a) for a in argv], emit=lines.append)
    return status, "\n".join(lines)


def step(folder, why, raw, goal="the goal", plan=None):
    """One robot call in the form the briefing gives."""
    argv = ["step", "--session", folder, "--why", why, "--goal", goal, "--json", raw]
    return call(*(argv + (["--plan", plan] if plan else [])))


def result_of(text):
    """The result in a sparse print."""
    return json.loads(text)["result"]


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="robot_step_"))
        cls.env = mock.patch.dict(os.environ, {"USERPROFILE": str(cls.tmp), "HOME": str(cls.tmp), "HOUSE_AI_FORBID_NETWORK": "1"})
        cls.env.start()
        cls.bed_spec = load_authored(ROOT)[0][BED]
        cls.spec_file = cls.tmp / "spec_file.json"
        cls.spec_file.write_text(json.dumps({"specs": [cls.bed_spec]}), encoding="utf-8")
        cls.setUpSessions()

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def setUpSessions(cls):
        pass

    @classmethod
    def oracle_log(cls):
        """The oracle's whole episode under NONCE, through the harness: what the reference commands and hash are."""
        out = cls.tmp / "oracle"
        episode = {"catalog_id": BED, "split": "dev", "seed": 0, "seed_tag": "dev:0", "phrasing": None, "perception": "realistic",
                   "episode_nonce": NONCE}
        record = R.run_episode(cls.bed_spec, episode, make_harness_agent("oracle"), out_dir=out, run_id="oracle")
        log = json.loads((out / record["log"]).read_text(encoding="utf-8"))
        return log, read_replay(out / log["replay"])["final_hash"]

    @classmethod
    def start(cls, name, spec_file=None, nonce=NONCE, seed=0):
        folder = cls.tmp / name
        status, text = call("start", "--session", folder, "--spec", spec_file or cls.spec_file, "--seed", seed, "--nonce", nonce)
        assert status == 0, text
        return folder, text

    @classmethod
    def run_reference(cls, name, commands):
        folder, first = cls.start(name)
        status, briefing = call("prompt", "--session", folder)
        assert status == 0
        outputs = []
        for index, command in enumerate(commands):
            raw = json.dumps({k: v for k, v in command.items() if not k.startswith("_")}, ensure_ascii=False)
            status, text = step(folder, "because of step {}".format(index + 1), raw)
            assert status == 0
            outputs.append(text)
        out = cls.tmp / (name + "_out")
        status, text = call("finalize", "--session", folder, "--out", out, "--agent-label", "test driver")
        assert status == 0, text
        return {"folder": folder, "first": first, "briefing": briefing, "outputs": outputs, "out": out, "report": json.loads(text)}


class TestReferenceEpisode(Base):
    @classmethod
    def setUpSessions(cls):
        cls.log, cls.final_hash = cls.oracle_log()
        cls.commands = [dict(s["command"]) for s in cls.log["steps"]]
        cls.a = cls.run_reference("a", cls.commands)
        cls.b = cls.run_reference("b", cls.commands)

    def test_the_evaluation_and_final_state_match_the_oracles_episode(self):
        report = self.a["report"]
        self.assertTrue(report["success"])
        for key in ("success", "goal_fraction", "failures", "score", "steps", "time_s", "budget_exceeded"):
            self.assertEqual(report["evaluation"].get(key), self.log["evaluation"].get(key), key)
        self.assertEqual(report["final_state_hash"], self.final_hash)
        self.assertFalse(report["record"]["privileged"])
        self.assertEqual(report["record"]["agent"], "robot_step")

    def test_two_whole_runs_are_identical(self):
        self.assertEqual(self.a["first"], self.b["first"])
        self.assertEqual(self.a["outputs"], self.b["outputs"])
        self.assertEqual(self.a["report"]["final_state_hash"], self.b["report"]["final_state_hash"])
        self.assertEqual(json.dumps(self.a["report"]["evaluation"], sort_keys=True), json.dumps(self.b["report"]["evaluation"], sort_keys=True))

    def test_the_standard_files_exist_and_the_log_rescores_with_both_fingerprints(self):
        out = self.a["out"]
        name = "{}__dev-0".format(BED)
        for path in ("run.json", "episodes.jsonl", "logs/{}.json".format(name), "replays/{}.replay.json".format(name)):
            self.assertTrue((out / path).is_file(), path)
        run_json = json.loads((out / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(run_json["format"], "house-harness-run-v1")
        self.assertEqual(run_json["status"], "complete")
        self.assertEqual(run_json["agent_label"], "test driver")
        record = json.loads((out / "episodes.jsonl").read_text(encoding="utf-8").strip())
        self.assertEqual(record["log"], "logs/{}.json".format(name))
        log = json.loads((out / record["log"]).read_text(encoding="utf-8"))
        scored = R.replay_log(log, self.bed_spec)
        self.assertTrue(scored["success"])
        self.assertTrue(scored["code_fingerprint_match"])
        self.assertTrue(scored["harness_fingerprint_match"])

    def test_the_stated_reason_reaches_the_log_and_never_the_simulator(self):
        log = json.loads((self.a["out"] / "logs" / "{}__dev-0.json".format(BED)).read_text(encoding="utf-8"))
        for index, entry in enumerate(log["steps"]):
            self.assertEqual(entry["command"]["_why"], "because of step {}".format(index + 1))
            self.assertEqual(entry["command"]["_goal"], "the goal")
            if entry.get("sent") is not None:
                self.assertFalse([k for k in entry["sent"] if k.startswith("_")], entry["sent"])
        replay = read_replay(self.a["out"] / log["replay"])
        self.assertNotIn("because of step", json.dumps(replay["steps"], default=str))
        self.assertNotIn("the goal", json.dumps(replay["steps"], default=str))

    def test_after_done_the_command_says_only_that_the_episode_is_over(self):
        self.assertEqual(self.a["outputs"][-1], robot.OVER_TEXT)
        count = len(robot.read_steps(self.a["folder"]))
        status, text = step(self.a["folder"], "one more", '{"action": "look"}')
        self.assertEqual((status, text), (0, robot.OVER_TEXT))
        self.assertEqual(len(robot.read_steps(self.a["folder"])), count)

    def test_nothing_the_robot_reads_carries_a_secret(self):
        config, task, _plans = build(self.bed_spec, 0, perception="realistic")
        env = HouseEnv(config, task, 0, perception="realistic", trace=False, observe_on_step=True)
        hidden = set(env.world.s["entities"]) - set(env.world.s["rooms"]) - set(env.world.s.get("people", {}))
        session = json.loads((self.a["folder"] / "session.json").read_text(encoding="utf-8"))
        texts = [self.a["first"]] + self.a["outputs"]
        forbidden_keys = set(EVALUATOR_KEYS) | {"score", "goal_fraction", "failures", "unmet", "milestones", "budget", "budget_exceeded",
                                                "seed", "episode_nonce", "nonce", "step_budget", "time_budget_s"}
        for text in texts:
            self.assertFalse(set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text)) & hidden)
            for secret in (session["episode_nonce"], session["catalog_id"], session["spec_hash"], str(self.spec_file), "spec.json"):
                self.assertNotIn(secret, text)
            for line in text.splitlines():
                if line.startswith("{"):
                    stack = [json.loads(line)]
                    while stack:
                        value = stack.pop()
                        if isinstance(value, dict):
                            self.assertFalse(set(value) & forbidden_keys, sorted(set(value) & forbidden_keys))
                            stack.extend(value.values())
                        elif isinstance(value, list):
                            stack.extend(value)

    def test_the_briefing_is_the_real_prompt_plus_the_step_form_and_no_scenario_fact(self):
        briefing = self.a["briefing"]
        catalog = robot.rebuild(*self._session_args()).agent.catalog
        base = L.system_prompt(catalog, tool_mode=False)
        form = json.loads((self.a["folder"] / "session.json").read_text(encoding="utf-8"))["step_form"]
        long_form = '{} "{}" step --session "{}"'.format(form["python"], form["script"], form["session"])
        more_form = long_form.replace(" step --session", " more --session")
        # byte for byte, with only the format paragraph replaced
        self.assertEqual(briefing, base.replace(L.JSON_FORMAT, robot.SPARSE_FORMAT.format(step=long_form, more=more_form)))
        self.assertNotIn(L.JSON_FORMAT, briefing)
        self.assertIn(long_form, briefing)
        for wording in ("What is listed depends on where you stand.", "`out_of_view` means not listed from here, not that it moved.",
                        "The world may have changed since you were last shown something.",
                        "An entity field left out is false, null or empty, or looks dry, room temperature, clean."):
            self.assertIn(wording, briefing)
        self.assertNotIn("gone", robot.SPARSE_FORMAT)
        session = json.loads((self.a["folder"] / "session.json").read_text(encoding="utf-8"))
        for fact in (session["episode_nonce"], session["catalog_id"], session["spec_hash"], session["instruction"], "spec.json", "dev:0", "budget"):
            self.assertNotIn(fact, briefing)
        self.assertEqual(session["system_prompt_sha256"], robot.hashlib.sha256(L.system_prompt(catalog, tool_mode=False).encode("utf-8")).hexdigest())

    def _session_args(self):
        folder, session, spec = robot.load_session(self.a["folder"])
        return session, spec, [], []

    def test_start_prints_only_the_instruction_and_the_observation(self):
        first = json.loads(self.a["first"])
        self.assertEqual(sorted(first), ["room_first_seen", "set"])
        self.assertEqual(first["set"]["instruction"], self.log["instruction"])
        message = (self.a["folder"] / "first_message.txt").read_text(encoding="utf-8").splitlines()
        self.assertEqual(message, ["INSTRUCTION: " + self.log["instruction"], "OBSERVATION:", self.a["first"]])


class TestCommands(Base):
    def test_malformed_commands_are_the_harness_refusals_and_counted_steps(self):
        folder, _ = self.start("bad")
        for raw, message in (("not json", "Unknown action: __unparseable__"), ("[1, 2]", "Unknown action: __unparseable__"), ('{"target": "x"}', "Unknown action: __unparseable__"),
                             ('{"a":' * 3000 + "1" + "}" * 3000, "Unknown action: __unparseable__"), ('{"action": "fly"}', "Unknown action: fly"),
                             ('{"action": "look", "bogus": 1}', "Unknown parameter for look: bogus"), ('{"action": "pick_up", "target": "door_1"}', "That is not visible here")):
            status, text = step(folder, "trying", raw)
            self.assertEqual(status, 0)
            first = result_of(text)
            self.assertEqual((first["ok"], first["message"]), (False, message))
        steps = robot.read_steps(folder)
        self.assertEqual(len(steps), 7)
        self.assertEqual(steps[0]["command"], {"action": None, "_invalid": "no_command", "_raw": "not json", "_why": "trying", "_goal": "the goal"})

    def test_the_robots_own_annotations_are_dropped_and_params_flatten_like_an_llm_reply(self):
        folder, _ = self.start("flat")
        step(folder, "the real reason", '{"action": "wait", "params": {"seconds": 1}, "note": "n", "_why": "fake", "_goal": "fake", "_plan": "fake", "_x": 1}', goal="the real goal", plan="the real plan")
        self.assertEqual(robot.read_steps(folder)[0]["command"], {"action": "wait", "seconds": 1, "_why": "the real reason", "_goal": "the real goal", "_plan": "the real plan"})

    def test_a_call_without_a_reason_is_refused_and_not_stored(self):
        folder, _ = self.start("nowhy")
        for argv in (["step", "--session", folder, "--goal", "g", "--json", '{"action": "look"}'], ["step", "--session", folder, "--why", "  ", "--goal", "g", "--json", '{"action": "look"}'],
                     ["step", "--session", folder, "--why", "x", "--json", '{"action": "look"}'], ["step", "--session", folder, "--why", "x", "--goal", "  ", "--json", '{"action": "look"}'],
                     ["step", "--session", folder, "--why", "x", "--goal", "g"], ["step", "--session", folder, "--w", "x", "--goal", "g", "--json", '{"action": "look"}'], ["step"], ["step", "--bogus", "1"]):
            self.assertEqual(call(*argv), (0, robot.BAD_CALL_TEXT), argv)
        self.assertEqual(robot.read_steps(folder), [])
        self.assertFalse((folder / "chain.jsonl").exists())
        self.assertEqual(step(self.tmp / "absent", "x", "{}"), (0, robot.NO_SESSION_TEXT))

    def test_a_person_an_event_and_a_custom_spec(self):
        spec_file = self.tmp / "custom.json"
        spec_file.write_text(json.dumps(FIND_SPEC), encoding="utf-8")
        folder, first = self.start("custom", spec_file)
        self.assertIn('"people"', first)
        self.assertNotIn("water the fern", first)
        status, text = step(folder, "let time pass", '{"action": "wait", "seconds": 8, "interrupt_on": ["addressed"]}')
        self.assertIn("water the fern", text)
        self.assertNotIn("mug_lifted_secret_label", text)
        status, text = step(folder, "I give up", '{"action": "done"}')
        self.assertEqual(text, robot.OVER_TEXT)
        status, text = call("finalize", "--session", folder, "--out", self.tmp / "custom_out")
        report = json.loads(text)
        self.assertEqual((status, report["success"]), (0, False))
        self.assertEqual(report["evaluation"]["unmet"], ["mug_lifted_secret_label"])
        self.assertEqual((report["code_fingerprint_match"], report["harness_fingerprint_match"]), (True, True))

    def test_finalize_of_an_unfinished_run_records_that_the_robot_stopped(self):
        spec_file = self.tmp / "custom_stop.json"
        spec_file.write_text(json.dumps(FIND_SPEC), encoding="utf-8")
        folder, _ = self.start("stopped", spec_file)
        step(folder, "one look", '{"action": "look"}')
        status, text = call("finalize", "--session", folder, "--out", self.tmp / "stopped_out", "--stop-reason", "turn_ended_without_done")
        report = json.loads(text)
        record = report["record"]
        self.assertEqual((status, record["end_reason"], record["stop_reason"], record["done_declared"], report["success"]), (0, "robot_stopped", "turn_ended_without_done", False, False))
        self.assertEqual(record["harness_end_reason"], "time_budget")
        self.assertEqual(record["robot_commands"], 1)
        self.assertGreater(record["idle_commands"], 10)
        self.assertGreater(record["sim_time_s"], 300)
        self.assertEqual(report["stop"]["stop_reason"], "turn_ended_without_done")
        log = json.loads((self.tmp / "stopped_out" / record["log"]).read_text(encoding="utf-8"))
        self.assertEqual((log["end_reason"], log["stop_reason"]), ("robot_stopped", "turn_ended_without_done"))
        self.assertFalse(log["steps"][0]["command"].get("_idle"))
        for entry in log["steps"][1:]:
            if entry.get("sent") is not None:
                self.assertTrue(entry["command"]["_idle"])
                self.assertEqual(entry["sent"]["action"], "wait")
                self.assertNotIn("_idle", entry["sent"])
                self.assertIn("Not Claude", entry["command"]["_why"])
        run_json = json.loads((self.tmp / "stopped_out" / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(run_json["totals"]["end_reasons"], {"robot_stopped": 1})
        # the event that came after the robot stopped still happened, and the session is closed
        self.assertEqual(step(folder, "too late", '{"action": "look"}'), (0, robot.OVER_TEXT))
        # finalizing again gives the same world
        again = json.loads(call("finalize", "--session", folder, "--out", self.tmp / "stopped_out2", "--stop-reason", "turn_ended_without_done")[1])
        self.assertEqual(again["final_state_hash"], report["final_state_hash"])

    def test_an_edited_session_halts_and_reports_instead_of_showing_a_wrong_world(self):
        folder, _ = self.start("tamper")
        step(folder, "one", '{"action": "look"}')
        path = folder / "steps.jsonl"
        entry = json.loads(path.read_text(encoding="utf-8"))
        entry["digest"] = "0" * 64
        path.write_text(json.dumps(entry) + "\n", encoding="utf-8")
        self.assertEqual(step(folder, "two", '{"action": "look"}'), (0, robot.OVER_TEXT))
        report = [json.loads(line) for line in (folder / "operator_report.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(report[-1]["kind"], "divergence")
        self.assertEqual(step(folder, "three", '{"action": "look"}'), (0, robot.OVER_TEXT))
        status, text = call("finalize", "--session", folder, "--out", self.tmp / "tamper_out")
        self.assertEqual((status, json.loads(text)["status"]), (3, "aborted"))

    def test_a_harness_crash_reaches_the_operator_and_never_the_robot(self):
        # the shape of map_agent item 6: env.evaluate raises and the exception escapes run_episode (ledger.py:2102 on two
        # contradicting typed reports); a mock keeps this test once that defect is fixed
        folder, _ = self.start("crash")
        real = HouseEnv.evaluate

        def failing(env, *args, **kwargs):
            if env.world.s.get("steps", 0) >= 1:
                raise IndexError("list index out of range")
            return real(env, *args, **kwargs)

        with mock.patch.object(HouseEnv, "evaluate", failing):
            status, text = step(folder, "report", '{"action": "look"}')
            self.assertEqual((status, text), (0, robot.OVER_TEXT))
            status, text = call("finalize", "--session", folder, "--out", self.tmp / "crash_out")
        entry = robot.read_steps(folder)[-1]
        self.assertEqual((entry["over"], entry["end_reason"]), (True, "harness_crash"))
        report = [json.loads(line) for line in (folder / "operator_report.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(report[-1]["kind"], "harness_crash")
        self.assertIn("IndexError", report[-1]["error"])
        self.assertIn("Traceback", report[-1]["traceback"])
        self.assertEqual(status, 3)
        self.assertEqual(json.loads(text)["status"], "aborted")
        self.assertEqual(json.loads((self.tmp / "crash_out" / "run.json").read_text(encoding="utf-8"))["status"], "aborted")

    def test_any_failure_of_the_tool_is_one_plain_sentence(self):
        folder, _ = self.start("fail")
        with mock.patch.object(robot, "cmd_step", side_effect=KeyError("secret_true_id")):
            self.assertEqual(step(folder, "x", "{}"), (0, robot.TOOL_FAILURE_TEXT))
        report = (folder / "operator_report.jsonl").read_text(encoding="utf-8")
        self.assertIn("secret_true_id", report)

    def test_a_session_is_never_restarted_in_place_and_private_seeds_are_refused(self):
        folder, _ = self.start("once")
        with mock.patch("sys.stderr"):
            self.assertEqual(call("start", "--session", folder, "--spec", self.spec_file, "--seed", 0)[0], 2)
            self.assertEqual(call("start", "--session", self.tmp / "private", "--spec", self.spec_file, "--seed", 900000001)[0], 2)
        self.assertFalse((self.tmp / "private" / "session.json").exists())

    def test_a_separate_process_with_another_hash_seed_rebuilds_the_same_decisions(self):
        folder, _ = self.start("processes")
        for hash_seed, raw in (("1", '{"action": "look"}'), ("2", '{"action": "crouch"}')):
            env = dict(os.environ, PYTHONHASHSEED=hash_seed)
            done = subprocess.run([sys.executable, str(ROOT / "tools" / "robot_step.py"), "step", "--session", str(folder), "--why", "across processes", "--goal", "g", "--json", raw],
                                  capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(self.tmp))
            self.assertEqual((done.returncode, done.stderr), (0, ""))
            self.assertTrue(done.stdout.startswith('{"result":'), done.stdout[:200])
            self.assertNotIn("Traceback", done.stdout)
        self.assertFalse((folder / "halted.json").exists())
        self.assertEqual(len(robot.read_steps(folder)), 2)
        done = subprocess.run([sys.executable, str(ROOT / "tools" / "robot_step.py"), "step", "--session", str(self.tmp / "absent"), "--why", "x", "--goal", "g", "--json", "{"],
                              capture_output=True, text=True, encoding="utf-8", cwd=str(self.tmp))
        self.assertEqual((done.returncode, done.stderr, done.stdout.strip()), (0, "", robot.NO_SESSION_TEXT))


class TestPrinter(unittest.TestCase):
    OBS = {"room_id": "kitchen_1", "time_s": 0, "flag": False, "steps": 0, "instruction": "x",
           "visible_entities": {"a": {"label": "lamp", "on": False, "open": False, "color": None, "soil": {"overall": "clean"}, "wetness": "looks dry", "temperature_c": "room temperature",
                                      "openable": False, "portable": False, "accepts_items": False, "rot": 0, "covered": 0.0},
                                "b": {"label": "cup", "open": True, "color": "blue", "soil": {"overall": "dirty"}, "wetness": "damp", "temperature_c": "hot", "openable": True, "portable": True, "accepts_items": True}}}

    def setUp(self):
        self.printer = robot._printer()

    def round_trip(self, observations):
        """Renders each with results and restores it from the text alone; both states stay plain JSON."""
        printed, state, back = [], self.printer.new_state(), self.printer.new_state()
        for index, observation in enumerate(observations):
            text, state = self.printer.render(state, observation, None if index == 0 else {"ok": True, "message": str(index)})
            printed.append(text)
            again, back = self.printer.restore(back, text)
            self.assertEqual(self.printer.canonical(again), self.printer.canonical(observation), index)
            json.dumps(state)
        return printed

    def test_it_imports_only_json(self):
        import ast
        source = (ROOT / "tools" / "flagship" / "printer.py").read_text(encoding="utf-8")
        names = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                names |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                names.add(node.module)
        self.assertEqual(names, {"json"})

    def test_only_unremarkable_values_of_the_named_fields_are_left_out(self):
        first = json.loads(self.round_trip([self.OBS])[0])["room_first_seen"]
        self.assertEqual(first["a"], {"label": "lamp", "on": False, "rot": 0, "covered": 0.0})     # false and 0 are printed, not dropped
        self.assertEqual(first["b"], self.OBS["visible_entities"]["b"])

    def test_a_named_field_the_record_does_not_have_is_said_to_be_absent(self):
        observation = copy.deepcopy(self.OBS)
        del observation["visible_entities"]["a"]["wetness"]
        first = json.loads(self.round_trip([observation])[0])["room_first_seen"]["a"]
        self.assertEqual(first["_absent"], ["wetness"])

    def test_walking_away_and_coming_back_prints_out_of_view_and_never_gone(self):
        kitchen = self.OBS
        elsewhere = {"room_id": "hall_1", "time_s": 5, "flag": False, "steps": 1, "instruction": "x", "visible_entities": {"c": {"label": "coat"}}}
        moved = copy.deepcopy(kitchen)
        moved.update(time_s=9, steps=2)
        del moved["visible_entities"]["b"]
        moved["visible_entities"]["a"]["on"] = True
        moved["visible_entities"]["a"]["open"] = True
        moved["visible_entities"]["d"] = {"label": "new thing"}
        back = copy.deepcopy(moved)
        back.update(time_s=10, steps=3)
        back["visible_entities"]["a"]["open"] = False
        printed = self.round_trip([kitchen, elsewhere, moved, back])
        self.assertEqual(sorted(json.loads(printed[1])), ["result", "room_first_seen", "set"])
        third = json.loads(printed[2])
        self.assertEqual(third["out_of_view"], ["b"])
        self.assertEqual(sorted(third["new"]), ["d"])
        self.assertEqual(third["new"]["d"]["label"], "new thing")
        self.assertEqual(third["changed"], {"a": {"set": {"on": True, "open": True}, "unset": []}})
        self.assertEqual(json.loads(printed[3])["changed"], {"a": {"set": {}, "unset": ["open"]}})
        for text in printed:
            self.assertNotIn("gone", json.loads(text))

    def test_zero_false_and_null_are_different_values(self):
        one = dict(self.OBS, flag=False)
        two = dict(self.OBS, flag=0)
        three = dict(self.OBS, flag=None)
        printed = self.round_trip([one, two, three])
        self.assertEqual(json.loads(printed[1])["set"], {"flag": 0})
        self.assertIn('"flag":null', printed[2])
        self.assertNotIn("visible_entities", printed[1])

    def test_a_top_level_field_that_goes_away_is_unset_and_a_missing_entity_list_survives(self):
        no_list = {"room_id": "kitchen_1", "time_s": 1}
        printed = self.round_trip([dict(self.OBS, extra=1), self.OBS, no_list, self.OBS])
        self.assertEqual(json.loads(printed[1])["unset"], ["extra"])

    def test_the_same_observation_prints_the_same_bytes_in_any_key_order(self):
        shuffled = {key: self.OBS[key] for key in reversed(list(self.OBS))}
        self.assertEqual(self.printer.render(self.printer.new_state(), self.OBS)[0], self.printer.render(self.printer.new_state(), shuffled)[0])

    def test_pages_join_back_and_each_fits(self):
        text = "x" * 70000
        pages = self.printer.pages(text, "more: go on")
        self.assertGreater(len(pages), 2)
        self.assertLessEqual(max(len(p) for p in pages), self.printer.PRINT_LIMIT)
        self.assertLess(self.printer.PRINT_LIMIT, robot.BASH_OUTPUT_LIMIT)
        self.assertEqual("".join(p[:-len("\nmore: go on")] for p in pages[:-1]) + pages[-1], text)
        self.assertEqual(self.printer.pages("short", "more: go on"), ["short"])


class TestSparseOnTheHarness(Base):
    """The whole path on real observations: a walk through the house, restored from the prints."""

    def test_a_walk_reconstructs_exactly_and_the_tool_prints_the_same(self):
        printer = robot._printer()
        seen = []

        class Walker(robot._agent_base()):
            name = "walker"
            privileged = False

            def reset(self, instruction, observation, action_catalog):
                self.count = 0

            def act(self, observation, last_result):
                seen.append((observation, last_result))
                self.count += 1
                if self.count > 14:
                    return {"action": "done"}
                ways = [c.get("to") for c in observation.get("connections") or []]
                things = sorted(observation.get("visible_entities") or {})
                if self.count % 5 == 0 and ways:
                    return {"action": "move", "target": ways[self.count % len(ways)]}
                if self.count % 5 == 2 and things:
                    return {"action": "approach", "target": things[self.count % len(things)]}
                return {"action": "look"} if self.count % 2 else {"action": "wait", "seconds": 3}

        episode = {"catalog_id": BED, "split": "dev", "seed": 0, "seed_tag": "dev:0", "phrasing": None, "perception": "realistic", "episode_nonce": NONCE}
        R.run_episode(self.bed_spec, episode, Walker(), out_dir=self.tmp / "walk", run_id="walk")
        self.assertGreater(len({o.get("room_id") for o, _r in seen}), 1)
        state, back, texts = printer.new_state(), printer.new_state(), []
        for observation, last in seen:
            text, state = printer.render(state, observation, last)
            again, back = printer.restore(back, text)
            self.assertEqual(printer.canonical(again), printer.canonical(observation))
            self.assertLess(len(text), robot.BASH_OUTPUT_LIMIT)
            texts.append(text)

    def test_two_hash_seeds_print_the_same_bytes(self):
        printed = []
        for hash_seed in ("11", "4242"):
            folder = self.tmp / ("bytes" + hash_seed)
            env = dict(os.environ, PYTHONHASHSEED=hash_seed)
            base = [sys.executable, str(ROOT / "tools" / "robot_step.py")]
            done = subprocess.run(base + ["start", "--session", str(folder), "--spec", str(self.spec_file), "--seed", "0", "--nonce", NONCE], capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(self.tmp))
            self.assertEqual(done.returncode, 0, done.stderr)
            walk = subprocess.run(base + ["step", "--session", str(folder), "--why", "w", "--goal", "g", "--json", '{"action": "look"}'], capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(self.tmp))
            printed.append(done.stdout + walk.stdout)
        self.assertEqual(printed[0], printed[1])

    def test_a_long_print_comes_in_pages_and_more_costs_no_sim_time(self):
        printer = robot._printer()
        folder, _ = self.start("paged")
        original = printer.pages
        with mock.patch.object(printer, "pages", lambda text, line: original(text, line, 300)):
            status, first = step(folder, "look around", '{"action": "look"}')
        entry = robot.read_steps(folder)[0]
        self.assertEqual(first, entry["pages"][0])
        self.assertTrue(first.endswith(robot._more_line(robot.load_session(folder)[1])))
        joined = [first]
        for _ in range(len(entry["pages"]) - 1):
            status, text = call("more", "--session", folder)
            joined.append(text)
        self.assertEqual(joined, entry["pages"])
        self.assertEqual(call("more", "--session", folder), (0, robot.NOTHING_MORE_TEXT))
        self.assertEqual(len(robot.read_steps(folder)), 1)
        self.assertEqual("".join(p.rsplit("\n", 1)[0] for p in joined[:-1]) + joined[-1], entry["output"])
        status, second = step(folder, "and again", '{"action": "look"}')
        self.assertEqual(json.loads(robot.read_steps(folder)[1]["output"])["result"]["ok"], True)


class TestChainAndReprint(Base):
    def test_the_chain_links_prints_and_reasons_and_a_broken_one_refuses_before_simulating(self):
        folder, first = self.start("chain")
        step(folder, "a", '{"action": "look"}', plan="look then crouch")
        step(folder, "b", '{"action": "crouch"}')
        session = robot.load_session(folder)[1]
        steps = robot.read_steps(folder)
        self.assertEqual(robot.chain_problem(session, steps, first)[0], None)
        self.assertEqual(steps[0]["h"], robot.chain_hash(session["chain_genesis"], first, '{"action": "look"}', "a", "the goal", "look then crouch"))
        self.assertEqual(steps[1]["h"], robot.chain_hash(steps[0]["h"], steps[0]["output"], '{"action": "crouch"}', "b", "the goal", None))
        self.assertEqual(steps[1]["h"], audit.chain_hash(steps[0]["h"], steps[0]["output"], '{"action": "crouch"}', "b", "the goal", None))
        self.assertEqual(session["chain_genesis"], audit.chain_genesis(session))
        lines = [json.loads(line) for line in (folder / "chain.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([line["h"] for line in lines], [s["h"] for s in steps])
        path = folder / "steps.jsonl"
        path.write_text(path.read_text(encoding="utf-8").replace('"why": "b"', '"why": "a better reason"'), encoding="utf-8")
        with mock.patch.object(robot, "rebuild", side_effect=AssertionError("simulated")):
            self.assertEqual(step(folder, "c", '{"action": "look"}'), (0, robot.OVER_TEXT))
        self.assertIn("hash chain", json.loads((folder / "halted.json").read_text(encoding="utf-8"))["error"])

    def test_the_reason_is_committed_before_the_simulator_runs(self):
        folder, _ = self.start("intent")
        with mock.patch.object(robot, "rebuild", side_effect=RuntimeError("killed mid-step")):
            self.assertEqual(step(folder, "lost", '{"action": "look"}'), (0, robot.TOOL_FAILURE_TEXT))
        self.assertEqual(robot.read_steps(folder), [])
        self.assertEqual(json.loads((folder / "chain.jsonl").read_text(encoding="utf-8"))["why"], "lost")
        status, text = step(folder, "kept", '{"action": "look"}')
        self.assertTrue(result_of(text)["ok"])
        self.assertEqual(robot.read_steps(folder)[0]["why"], "kept")
        report = json.loads(call("finalize", "--session", folder, "--out", self.tmp / "intent_out", "--stop-reason", "child_stalled")[1])
        self.assertEqual(report["commands_that_never_returned"], [1])
        self.assertTrue(report["chain_verifies"])

    def test_reprint_gives_the_stored_prints_from_a_fresh_simulation(self):
        folder, first = self.start("again")
        outputs = [step(folder, "a", '{"action": "look"}')[1], step(folder, "b", '{"action": "crouch"}')[1]]
        out = self.tmp / "reprint.json"
        status, text = call("reprint", "--session", folder, "--out", out)
        self.assertEqual(status, 0)
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["prints"], [first] + outputs)
        self.assertEqual(data["prompt_sha256"], robot.load_session(folder)[1]["prompt_sha256"])


class TestOtherForms(Base):
    def test_compact_stays_available_with_the_old_print(self):
        folder = self.tmp / "compact"
        status, first = call("start", "--session", folder, "--spec", self.spec_file, "--seed", 0, "--nonce", NONCE, "--observation", "compact")
        self.assertEqual(status, 0)
        lines = first.splitlines()
        self.assertTrue(lines[0].startswith("INSTRUCTION: "))
        self.assertEqual(lines[1], "OBSERVATION (JSON):")
        self.assertIn("visible_entities", json.loads(lines[2]))
        status, text = step(folder, "look", '{"action": "look"}')
        self.assertTrue(text.startswith("RESULT: "))
        briefing = call("prompt", "--session", folder)[1]
        loaded = robot.load_session(folder)
        catalog = robot.rebuild(loaded[1], loaded[2], [], []).agent.catalog
        self.assertTrue(briefing.startswith(L.system_prompt(catalog, tool_mode=False)))
        self.assertIn("--goal", briefing)


class TestHome(Base):
    def test_a_step_opens_nothing_under_the_private_home(self):
        folder, _ = self.start("homecheck")
        probe = self.tmp / "home_probe.py"
        probe.write_text("""import json, os, sys
seen = []
def hook(event, args):
    if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
        path = os.fsdecode(args[0]).lower().replace("/", "\\\\")
        if ".house_ai" in path:
            seen.append(path)
sys.addaudithook(hook)
sys.path.insert(0, sys.argv[1])
import robot_step
sys.path.insert(0, str(robot_step.ROOT))
robot_step.prepare_process(sys.argv[2:])
lines = []
status = robot_step.main(sys.argv[2:], emit=lines.append)
print(json.dumps({"status": status, "seen": seen, "output": lines[0][:30]}))
""", encoding="utf-8")
        done = subprocess.run([sys.executable, str(probe), str(ROOT / "tools"), "step", "--session", str(folder), "--why", "w", "--goal", "g", "--json", '{"action": "look"}'],
                              capture_output=True, text=True, encoding="utf-8", cwd=str(self.tmp))
        report = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertEqual((report["status"], report["seen"]), (0, []))
        self.assertTrue(report["output"].startswith('{"result"'), report)


class TestLauncher(Base):
    @classmethod
    def setUpSessions(cls):
        cls.launcher = _load_tool("robot_launch")

    def test_the_childs_environment_is_built_from_a_list_and_names_no_key(self):
        source = {"PATH": "p", "USERPROFILE": "u", "ANTHROPIC_API_KEY": "secret-1", "ANTHROPIC_AUTH_TOKEN": "secret-2", "CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_USE_VERTEX": "1", "CLAUDE_CODE_USE_FOUNDRY": "1",
                  "ANTHROPIC_BASE_URL": "http://elsewhere", "CLAUDE_CODE_USER_EMAIL": "x", "CLAUDE_CODE_MESSAGING_TOKEN": "y", "ALPACA_API_KEY": "z", "CLAUDECODE": "1", "CLAUDE_CODE_SIMPLE": "1"}
        env = self.launcher.child_env(source)
        for name in source:
            if name not in ("PATH", "USERPROFILE"):
                self.assertNotIn(name, env)
        self.assertEqual(env["USERPROFILE"], "u")
        self.assertEqual(env["MSYS_NO_PATHCONV"], "1")
        self.assertEqual(env["HOUSE_AI_FORBID_NETWORK"], "1")
        self.assertNotIn("secret", json.dumps(env))
        for name in env:
            self.assertFalse(name.startswith(("ANTHROPIC_", "CLAUDE_CODE_USE_")), name)
        for name in self.launcher.CHILD_ENV:
            self.assertFalse(name in self.launcher.FORBIDDEN_NAMES or name.startswith(self.launcher.FORBIDDEN_PREFIXES), name)

    def test_the_command_has_the_flags_and_never_bare(self):
        command = self.launcher.child_command("D:/run", {"model": self.launcher.MODEL, "session_id": "u"})
        self.assertNotIn("--bare", command)
        for flag in ("-p", "--safe-mode", "--strict-mcp-config", "--tools", "--allowedTools", "--system-prompt-file", "--permission-prompts", "--session-id"):
            self.assertIn(flag, command)
        self.assertEqual(command[command.index("--model") + 1], "claude-opus-5-5")
        self.assertEqual(command[command.index("--tools") + 1], "Bash")
        self.assertEqual(command[command.index("--allowedTools") + 1], "Bash(python robot.py *)")
        self.assertEqual(command[command.index("--permission-prompts") + 1], "none")
        self.assertNotIn("--effort", command)

    def status(self, **fields):
        return subprocess.CompletedProcess([], 0, stdout=json.dumps(fields), stderr="")

    def test_the_auth_gate_lets_only_a_login_through(self):
        for fields, allowed in (({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty", "subscriptionType": "max"}, True),
                                ({"loggedIn": True, "authMethod": "oauth_token", "apiProvider": "firstParty"}, True),
                                ({"loggedIn": False, "authMethod": "none", "apiProvider": "firstParty"}, False),
                                ({"loggedIn": True, "authMethod": "api_key", "apiProvider": "firstParty", "apiKeySource": "ANTHROPIC_API_KEY"}, False),
                                ({"loggedIn": True, "authMethod": "third_party", "apiProvider": "bedrock"}, False)):
            with mock.patch.object(self.launcher.subprocess, "run", return_value=self.status(**fields)):
                if allowed:
                    self.assertTrue(self.launcher.auth_gate("claude", {})["loggedIn"])
                else:
                    with self.assertRaises(self.launcher.LaunchError):
                        self.launcher.auth_gate("claude", {})

    def child(self, code):
        return subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, encoding="utf-8")

    def test_a_child_that_would_use_a_key_is_killed_at_its_first_record(self):
        code = "import json,time;print(json.dumps({'type':'system','subtype':'init','apiKeySource':'ANTHROPIC_API_KEY'}),flush=True);time.sleep(120);print(json.dumps({'type':'assistant'}))"
        started = time.monotonic()
        records, killed = self.launcher.watch_child(self.child(code), self.tmp / "s1.jsonl", 300, 300, gate=self.launcher.init_gate)
        self.assertEqual(killed, "would_use_an_api_key")
        self.assertEqual([r["type"] for r in records], ["system"])
        self.assertLess(time.monotonic() - started, 100)

    def test_a_stalled_child_is_killed_and_says_so(self):
        records, killed = self.launcher.watch_child(self.child("import time;print('{}',flush=True);time.sleep(120)"), self.tmp / "s2.jsonl", 2, 300)
        self.assertEqual(killed, "child_stalled")

    def test_why_the_robot_stopped(self):
        reason = self.launcher.stop_reason_of
        self.assertIsNone(reason({"result": {"is_error": False}}, True))
        self.assertEqual(reason({"result": {"is_error": False, "subtype": "success"}}, False), "turn_ended_without_done")
        self.assertEqual(reason({"killed": "child_stalled"}, False), "child_stalled")
        self.assertEqual(reason({}, False), "child_ended_without_a_result")
        self.assertEqual(reason({"result": {"is_error": True, "result": "Claude usage limit reached"}}, False), "usage_limit")
        self.assertEqual(reason({"result": {"is_error": True, "result": "Not logged in"}}, False), "child_error")

    def test_prepare_freezes_a_neutral_folder_and_dry_prints_the_command(self):
        prepared = []
        rel = ["tools/robot_step.py", "tools/robot_launch.py", "tools/audit_robot_transcript.py", "tools/flagship/printer.py"]
        status = self.launcher.main(["prepare", "--commit", "HEAD", "--spec", str(self.spec_file), "--seed", "0", "--nonce", NONCE, "--root", str(self.tmp / "runs"), "--name", "sim_test", "--overlay"] + rel, emit=prepared.append)
        self.assertEqual(status, 0)
        run_dir = self.tmp / "runs" / "sim_test"
        self.assertEqual(os.listdir(run_dir / "work"), ["robot.py"])       # the child's working directory holds nothing else
        robot_py = (run_dir / "work" / "robot.py").read_text(encoding="utf-8")
        self.assertLess(len(robot_py.splitlines()), 8)
        briefing = (run_dir / "run" / "briefing.txt").read_text(encoding="utf-8")
        session = json.loads((run_dir / "run" / "session.json").read_text(encoding="utf-8"))
        self.assertIn("`python robot.py step --why", briefing)
        self.assertIn("`python robot.py more`", briefing)
        message = (run_dir / "launch" / "first_message.txt").read_text(encoding="utf-8")
        for text in (briefing, message, robot_py):
            for word in ("flagship", "home reset", str(run_dir), str(self.spec_file), session["episode_nonce"], session["catalog_id"]):
                self.assertNotIn(word.lower(), text.lower())
        frozen = json.loads((run_dir / "launch" / "run_meta.json").read_text(encoding="utf-8"))
        self.assertFalse(frozen["from_commit_alone"])
        dry = []
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "secret-1", "ANTHROPIC_AUTH_TOKEN": "secret-2", "CLAUDE_CODE_USE_BEDROCK": "1"}):
            self.assertEqual(self.launcher.main(["launch", "--run-dir", str(run_dir), "--dry"], emit=dry.append), 0)
        printed = json.loads(dry[0])
        self.assertTrue(printed["dry"])
        self.assertIn("--safe-mode", printed["argv"])
        self.assertNotIn("secret", dry[0])
        self.assertNotIn("ANTHROPIC", json.dumps(printed["env_names"]))
        self.assertFalse((run_dir / "launch" / "child_exit.json").exists())
        # the robot's own command form, in a real process with the real few-line script
        done = subprocess.run([sys.executable, "robot.py", "step", "--why", "first look", "--goal", "check", "--json", '{"action": "look"}'], capture_output=True, text=True, encoding="utf-8", cwd=str(run_dir / "work"))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(done.stdout.startswith('{"result":'), done.stdout[:100])
        self.assertEqual(json.loads((run_dir / "run" / "steps.jsonl").read_text(encoding="utf-8"))["goal"], "check")
        # robot.py passes on only the robot's verbs, and its session wins over one given on the line
        for argv in (["finalize", "--out", str(self.tmp / "sneak")], ["reprint", "--out", str(self.tmp / "sneak.json")], ["prompt"], ["start", "--spec", "x", "--seed", "0"], []):
            done = subprocess.run([sys.executable, "robot.py"] + argv, capture_output=True, text=True, encoding="utf-8", cwd=str(run_dir / "work"))
            self.assertEqual((done.returncode, done.stdout.strip()), (0, robot.BAD_CALL_TEXT), argv)
        self.assertFalse((self.tmp / "sneak").exists())
        self.assertFalse((self.tmp / "sneak.json").exists())
        done = subprocess.run([sys.executable, "robot.py", "more", "--session", str(self.tmp / "absent")], capture_output=True, text=True, encoding="utf-8", cwd=str(run_dir / "work"))
        self.assertEqual(done.stdout.strip(), robot.NOTHING_MORE_TEXT)


def step_command(form, why, raw, goal="the goal"):
    return '{} "{}" step --session "{}" --why "{}" --goal "{}" --json \'{}\''.format(form["python"], form["script"], form["session"], why, goal, raw)


class TestAudit(Base):
    """On a session finished by real steps and a transcript in Claude Code's format, then with one planted violation each."""

    @classmethod
    def setUpSessions(cls):
        robot._printer()
        # the checkout's own path holds a project word, which A5 rightly voids: a session this test can pass has a neutral script path
        with mock.patch.object(robot, "__file__", str(cls.tmp / "neutral" / "robot_step.py")):
            cls.folder, cls.first = cls.start("audited")
        status, cls.briefing = call("prompt", "--session", cls.folder)
        cls.calls = [("look first; it is safe (really) & cheap | fine", '{"action": "look"}'), ("I'm crouching", '{"action": "crouch"}'),
                     ("nothing more to do", '{"action": "done"}')]
        cls.outputs = []
        for why, raw in cls.calls:
            cls.outputs.append(step(cls.folder, why, raw)[1])
        cls.session = json.loads((cls.folder / "session.json").read_text(encoding="utf-8"))
        cls.form = cls.session["step_form"]

    def records(self):
        records = [{"type": "user", "isSidechain": True, "message": {"role": "user", "content": self.briefing + "\n" + self.first}}]
        for index, (why, raw) in enumerate(self.calls):
            records += self.turn(index, "Bash", {"command": step_command(self.form, why, raw), "description": "Robot step"}, self.outputs[index])
        return records

    def turn(self, index, name, tool_input, output):
        call_id = "toolu_{}_{}".format(name, index)
        return [{"type": "assistant", "isSidechain": True, "requestId": "req_{}_{}".format(name, index),
                 "message": {"role": "assistant", "content": [{"type": "tool_use", "id": call_id, "name": name, "input": tool_input}]}},
                {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call_id, "content": output}]}}]

    def verdict(self, records):
        path = self.tmp / "transcript.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
        return audit.audit(path, self.folder)

    def assertViolation(self, records, kind):
        result = self.verdict(records)
        self.assertEqual(result["verdict"], "FAIL", result)
        self.assertIn(kind, [v["kind"] for v in result["violations"]])
        return result

    def test_a_clean_run_passes_with_its_counts(self):
        result = self.verdict(self.records())
        self.assertEqual((result["verdict"], result["violations"], result["warnings"]), ("PASS", [], []))
        self.assertEqual(result["counts"]["step_calls"], 3)
        self.assertEqual(result["counts"]["stored_commands"], 3)
        self.assertEqual(result["counts"]["outputs_matching_the_session"], 3)
        self.assertEqual(result["first_message"]["verbatim"], {"briefing.txt": True, "start.txt": True})
        self.assertEqual(result["first_message"]["briefing_delivery"], "first_message")
        reminder = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": audit.HANDBACK_REMINDER + " when done."}]}}
        self.assertEqual(self.verdict(self.records() + [reminder])["warnings"], [])

    def test_a_final_hand_back_is_allowed_once_and_only_at_the_end(self):
        back = self.turn(9, audit.HANDBACK_TOOL, {"message": "Finished."}, "ok")
        self.assertEqual(self.verdict(self.records() + back)["verdict"], "PASS")
        self.assertViolation(back + self.records(), "handback_not_last")
        self.assertViolation(self.records() + back + self.turn(10, audit.HANDBACK_TOOL, {"message": "again"}, "ok"), "handback_not_last")

    def test_planted_other_tools_fail(self):
        for name, tool_input in (("Read", {"file_path": "spec.json"}), ("Grep", {"pattern": "seed"}), ("Glob", {"pattern": "*"}), ("Write", {"file_path": "x", "content": ""}),
                                 ("Edit", {"file_path": "x"}), ("Agent", {"prompt": "x"}), ("ToolSearch", {"query": "x"}), ("WebFetch", {"url": "x"}), ("PowerShell", {"command": "dir"}),
                                 ("mcp__x__y", {})):
            records = self.records()
            records[1:1] = self.turn(0, name, tool_input, "something")
            self.assertViolation(records, "other_tool")

    def test_planted_shell_tricks_fail(self):
        good = step_command(self.form, self.calls[0][0], self.calls[0][1])
        for command in (good + " ; cat spec.json", good + " && cat spec.json", good + " || cat spec.json", good + " | tee copy.txt", good + " > copy.txt", good + " 2>&1", good + " &",
                        "cat spec.json", "cat spec.json && " + good, "cd x; " + good, "FOO=1 " + good, good + "\ncat spec.json", good + " # note", good.replace("step --session", "step --session --session"),
                        step_command(self.form, "$(cat spec.json)", self.calls[0][1]), step_command(self.form, "`cat spec.json`", self.calls[0][1]), step_command(self.form, "$HOME", self.calls[0][1]),
                        good.replace(self.form["session"], self.form["session"] + "_other"), good.replace(self.form["script"], "other.py"), good.replace("python", "python3", 1)):
            records = self.records()
            records[1]["message"]["content"][0]["input"]["command"] = command
            self.assertViolation(records, "not_step_form")

    def test_bash_options_outside_the_form_fail(self):
        records = self.records()
        records[1]["message"]["content"][0]["input"]["run_in_background"] = True
        self.assertViolation(records, "bash_options")

    def test_a_planted_extra_step_fails(self):
        result = self.assertViolation(self.records() + self.turn(5, "Bash", {"command": step_command(self.form, "one more", '{"action": "look"}')}, "x"), "extra_step")
        self.assertIn("count_mismatch", [v["kind"] for v in result["violations"]])

    def test_a_planted_dropped_step_fails(self):
        records = self.records()
        del records[3:5]
        self.assertViolation(records, "dropped_step")

    def test_a_planted_edited_reason_or_command_fails(self):
        records = self.records()
        records[3]["message"]["content"][0]["input"]["command"] = step_command(self.form, "a better reason written later", self.calls[1][1])
        self.assertViolation(records, "reason_mismatch")
        records = self.records()
        records[3]["message"]["content"][0]["input"]["command"] = step_command(self.form, self.calls[1][0], '{"action": "kneel"}')
        self.assertViolation(records, "command_mismatch")

    def test_a_reordered_session_fails(self):
        records = self.records()
        records[1:3], records[3:5] = records[3:5], records[1:3]
        self.assertViolation(records, "reason_mismatch")

    def test_an_edited_session_fails(self):
        copy_dir = self.tmp / "edited"
        shutil.copytree(self.folder, copy_dir)
        steps = (copy_dir / "steps.jsonl").read_text(encoding="utf-8").replace("I'm crouching", "I am kneeling")
        (copy_dir / "steps.jsonl").write_text(steps, encoding="utf-8")
        path = self.tmp / "t.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in self.records()) + "\n", encoding="utf-8")
        self.assertIn("reason_mismatch", [v["kind"] for v in audit.audit(path, copy_dir)["violations"]])

    def test_a_secret_in_the_first_message_or_a_tool_output_fails(self):
        records = self.records()
        records[0]["message"]["content"] += "\nThe nonce is " + self.session["episode_nonce"]
        self.assertViolation(records, "leak")
        records = self.records()
        records[2]["message"]["content"][0]["content"] = self.session["catalog_id"]
        self.assertViolation(records, "leak")

    def test_an_unreadable_or_empty_transcript_fails(self):
        path = self.tmp / "broken.jsonl"
        path.write_text("{not json\n", encoding="utf-8")
        self.assertIn("unreadable_line", [v["kind"] for v in audit.audit(path, self.folder)["violations"]])
        path.write_text("", encoding="utf-8")
        self.assertEqual(audit.audit(path, self.folder)["verdict"], "FAIL")

    def test_a_briefing_read_in_pages_passes_and_a_late_page_fails(self):
        pages = json.loads((self.folder / "briefing_pages.json").read_text(encoding="utf-8"))
        self.assertGreater(len(pages), 1)
        self.assertTrue(all(len(p) < audit_limit for p in pages))
        form = '{} "{}" brief --session "{}" --part {{}}'.format(self.form["python"], self.form["script"], self.form["session"])
        reading = []
        for index, page in enumerate(pages, 1):
            status, text = call("brief", "--session", self.folder, "--part", index)
            self.assertEqual((status, text), (0, page))
            reading += self.turn(100 + index, "Bash", {"command": form.format(index)}, page)
        short = [{"type": "user", "message": {"role": "user", "content": "Read your instructions in pages."}}]
        result = self.verdict(short + reading + self.records()[1:])
        self.assertEqual((result["verdict"], result["warnings"]), ("PASS", []))
        self.assertEqual((result["counts"]["brief_calls"], result["counts"]["brief_pages_read"]), (len(pages), len(pages)))
        self.assertEqual(result["first_message"]["briefing_delivery"], "pages")
        self.assertViolation(short + self.records()[1:] + reading, "brief_after_step")
        self.assertEqual(call("brief", "--session", self.folder, "--part", 99), (0, "There are {} pages.".format(len(pages))))
        joined = "\n".join(pages)
        self.assertIn(self.first, joined)
        self.assertNotIn(self.session["episode_nonce"], joined)

    def test_the_launch_text_carries_no_scenario_fact(self):
        status, text = call("launch", "--session", self.folder)
        self.assertEqual(status, 0)
        self.assertLess(len(text), 900)
        self.assertIn("brief --session", text)
        for fact in (self.session["instruction"], self.session["catalog_id"], self.session["episode_nonce"]):
            self.assertNotIn(fact, text)

    def test_warnings_do_not_change_the_verdict(self):
        records = self.records()
        records[2]["message"]["content"][0]["content"] = "cut short"
        records.append({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "try the kitchen"}]}})
        records[3]["requestId"] = records[1]["requestId"]
        records[0]["message"]["content"] = "Do your best."
        result = self.verdict(records)
        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(sorted({w["kind"] for w in result["warnings"]}), ["first_message", "injected_message", "output_differs", "parallel_calls"])

    def test_the_command_line_writes_the_verdict_and_sets_the_status(self):
        path, out = self.tmp / "cli.jsonl", self.tmp / "verdict.json"
        path.write_text("\n".join(json.dumps(r) for r in self.records()) + "\n", encoding="utf-8")
        lines = []
        self.assertEqual(audit.main([str(path), "--session", str(self.folder), "--out", str(out)], emit=lines.append), 0)
        self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["verdict"], "PASS")
        path.write_text("", encoding="utf-8")
        self.assertEqual(audit.main([str(path), "--session", str(self.folder)], emit=lines.append), 1)
        with mock.patch("sys.stderr"):
            self.assertEqual(audit.main([str(path), "--session", str(self.tmp / "absent")], emit=lines.append), 2)


class TestShellWords(unittest.TestCase):
    def test_quoted_text_is_inert_and_active_forms_are_problems(self):
        words, problems = audit.shell_words("""a "b; && | > (c) it's" 'd $(e) `f` "g"' h\\ i""")
        self.assertEqual(problems, [])
        self.assertEqual([w[0] for w in words], ["a", "b; && | > (c) it's", 'd $(e) `f` "g"', "h i"])
        for command, expected in (("a; b", ";"), ("a && b", "&"), ("a | b", "|"), ("a > b", ">"), ("a < b", "<"), ("a $(b)", "$"), ("a `b`", "`"), ('"a $(b)"', "$ expansion"), ('"a `b`"', "backtick"),
                                  ('"$a"', "$ expansion"), ("a\nb", "line break"), ("'a", "unterminated"), ('"a', "unterminated"), ("a # b", "#")):
            self.assertTrue(any(expected in p for p in audit.shell_words(command)[1]), command)
        self.assertEqual(audit.shell_words('"a \\$b \\" \\\\ c"'), ([('a $b " \\ c', True)], []))
        self.assertEqual(audit.shell_words("'it'\\''s'"), ([("it's", True)], []))


if __name__ == "__main__":
    unittest.main()
