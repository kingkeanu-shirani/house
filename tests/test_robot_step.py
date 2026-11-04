"""tools/robot_step.py (the robot's one command) and tools/audit_robot_transcript.py (the proof it used nothing else).

Offline, with a temporary home, normal and HOUSE_SIM_STRICT=1. The reference episode's commands come from the oracle's own log
under the same nonce, so these tests follow the spec. See docs/EVAL_HARNESS.md, "Claude as the driver".
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
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
            status, text = call("step", "--session", folder, "--why", "because of step {}".format(index + 1), "--json", raw)
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
        for index, step in enumerate(log["steps"]):
            self.assertEqual(step["command"]["_why"], "because of step {}".format(index + 1))
            if step.get("sent") is not None:
                self.assertFalse([k for k in step["sent"] if k.startswith("_")], step["sent"])
        replay = read_replay(self.a["out"] / log["replay"])
        self.assertNotIn("because of step", json.dumps(replay["steps"], default=str))

    def test_after_done_the_command_says_only_that_the_episode_is_over(self):
        self.assertEqual(self.a["outputs"][-1], robot.OVER_TEXT)
        count = len(robot.read_steps(self.a["folder"]))
        status, text = call("step", "--session", self.a["folder"], "--why", "one more", "--json", '{"action": "look"}')
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
        self.assertTrue(briefing.startswith(L.system_prompt(catalog, tool_mode=False)))
        form = json.loads((self.a["folder"] / "session.json").read_text(encoding="utf-8"))["step_form"]
        self.assertIn('{} "{}" step --session "{}"'.format(form["python"], form["script"], form["session"]), briefing)
        session = json.loads((self.a["folder"] / "session.json").read_text(encoding="utf-8"))
        for fact in (session["episode_nonce"], session["catalog_id"], session["spec_hash"], session["instruction"], "spec.json", "dev:0", "budget"):
            self.assertNotIn(fact, briefing)
        self.assertEqual(session["system_prompt_sha256"], robot.hashlib.sha256(L.system_prompt(catalog, tool_mode=False).encode("utf-8")).hexdigest())

    def _session_args(self):
        folder, session, spec = robot.load_session(self.a["folder"])
        return session, spec, [], []

    def test_start_prints_only_the_instruction_and_the_observation(self):
        lines = self.a["first"].splitlines()
        self.assertEqual(lines[0], "INSTRUCTION: " + self.log["instruction"])
        self.assertEqual(lines[1], "OBSERVATION (JSON):")
        self.assertEqual(len(lines), 3)
        self.assertIn("visible_entities", json.loads(lines[2]))


class TestCommands(Base):
    def test_malformed_commands_are_the_harness_refusals_and_counted_steps(self):
        folder, _ = self.start("bad")
        for raw, message in (("not json", "Unknown action: __unparseable__"), ("[1, 2]", "Unknown action: __unparseable__"), ('{"target": "x"}', "Unknown action: __unparseable__"),
                             ('{"a":' * 3000 + "1" + "}" * 3000, "Unknown action: __unparseable__"), ('{"action": "fly"}', "Unknown action: fly"),
                             ('{"action": "look", "bogus": 1}', "Unknown parameter for look: bogus"), ('{"action": "pick_up", "target": "door_1"}', "That is not visible here")):
            status, text = call("step", "--session", folder, "--why", "trying", "--json", raw)
            self.assertEqual(status, 0)
            first = json.loads(text.splitlines()[0].split("RESULT: ", 1)[1])
            self.assertEqual((first["ok"], first["message"]), (False, message))
        steps = robot.read_steps(folder)
        self.assertEqual(len(steps), 7)
        self.assertEqual(steps[0]["command"], {"action": None, "_invalid": "no_command", "_raw": "not json", "_why": "trying"})

    def test_the_robots_own_annotations_are_dropped_and_params_flatten_like_an_llm_reply(self):
        folder, _ = self.start("flat")
        call("step", "--session", folder, "--why", "the real reason", "--json", '{"action": "wait", "params": {"seconds": 1}, "note": "n", "_why": "fake", "_x": 1}')
        self.assertEqual(robot.read_steps(folder)[0]["command"], {"action": "wait", "seconds": 1, "_why": "the real reason"})

    def test_a_call_without_a_reason_is_refused_and_not_stored(self):
        folder, _ = self.start("nowhy")
        for argv in (["step", "--session", folder, "--json", '{"action": "look"}'], ["step", "--session", folder, "--why", "  ", "--json", '{"action": "look"}'],
                     ["step", "--session", folder, "--why", "x"], ["step"], ["step", "--bogus", "1"]):
            self.assertEqual(call(*argv), (0, robot.BAD_CALL_TEXT))
        self.assertEqual(robot.read_steps(folder), [])
        self.assertEqual(call("step", "--session", self.tmp / "absent", "--why", "x", "--json", "{}"), (0, robot.NO_SESSION_TEXT))

    def test_a_person_an_event_and_a_custom_spec(self):
        spec_file = self.tmp / "custom.json"
        spec_file.write_text(json.dumps(FIND_SPEC), encoding="utf-8")
        folder, first = self.start("custom", spec_file)
        self.assertIn('"people"', first)
        self.assertNotIn("water the fern", first)
        status, text = call("step", "--session", folder, "--why", "let time pass", "--json", '{"action": "wait", "seconds": 8, "interrupt_on": ["addressed"]}')
        self.assertIn("water the fern", text)
        self.assertNotIn("mug_lifted_secret_label", text)
        status, text = call("step", "--session", folder, "--why", "I give up", "--json", '{"action": "done"}')
        self.assertEqual(text, robot.OVER_TEXT)
        status, text = call("finalize", "--session", folder, "--out", self.tmp / "custom_out")
        report = json.loads(text)
        self.assertEqual((status, report["success"]), (0, False))
        self.assertEqual(report["evaluation"]["unmet"], ["mug_lifted_secret_label"])
        self.assertEqual((report["code_fingerprint_match"], report["harness_fingerprint_match"]), (True, True))

    def test_finalize_of_an_unfinished_run_records_that_the_robot_stopped(self):
        folder, _ = self.start("stopped")
        call("step", "--session", folder, "--why", "one look", "--json", '{"action": "look"}')
        status, text = call("finalize", "--session", folder, "--out", self.tmp / "stopped_out")
        report = json.loads(text)
        self.assertEqual((status, report["record"]["end_reason"], report["record"]["done_declared"], report["success"]), (0, "agent_error", False, False))

    def test_an_edited_session_halts_and_reports_instead_of_showing_a_wrong_world(self):
        folder, _ = self.start("tamper")
        call("step", "--session", folder, "--why", "one", "--json", '{"action": "look"}')
        path = folder / "steps.jsonl"
        entry = json.loads(path.read_text(encoding="utf-8"))
        entry["digest"] = "0" * 64
        path.write_text(json.dumps(entry) + "\n", encoding="utf-8")
        self.assertEqual(call("step", "--session", folder, "--why", "two", "--json", '{"action": "look"}'), (0, robot.OVER_TEXT))
        report = [json.loads(line) for line in (folder / "operator_report.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(report[-1]["kind"], "divergence")
        self.assertEqual(call("step", "--session", folder, "--why", "three", "--json", '{"action": "look"}'), (0, robot.OVER_TEXT))
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
            status, text = call("step", "--session", folder, "--why", "report", "--json", '{"action": "look"}')
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
            self.assertEqual(call("step", "--session", folder, "--why", "x", "--json", "{}"), (0, robot.TOOL_FAILURE_TEXT))
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
            done = subprocess.run([sys.executable, str(ROOT / "tools" / "robot_step.py"), "step", "--session", str(folder), "--why", "across processes", "--json", raw],
                                  capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(self.tmp))
            self.assertEqual((done.returncode, done.stderr), (0, ""))
            self.assertTrue(done.stdout.startswith("RESULT:"), done.stdout[:200])
            self.assertNotIn("Traceback", done.stdout)
        self.assertFalse((folder / "halted.json").exists())
        self.assertEqual(len(robot.read_steps(folder)), 2)
        done = subprocess.run([sys.executable, str(ROOT / "tools" / "robot_step.py"), "step", "--session", str(self.tmp / "absent"), "--why", "x", "--json", "{"],
                              capture_output=True, text=True, encoding="utf-8", cwd=str(self.tmp))
        self.assertEqual((done.returncode, done.stderr, done.stdout.strip()), (0, "", robot.NO_SESSION_TEXT))


def step_command(form, why, raw):
    return '{} "{}" step --session "{}" --why "{}" --json \'{}\''.format(form["python"], form["script"], form["session"], why, raw)


class TestAudit(Base):
    """On a session finished by real steps and a transcript in Claude Code's format, then with one planted violation each."""

    @classmethod
    def setUpSessions(cls):
        cls.folder, cls.first = cls.start("audited")
        status, cls.briefing = call("prompt", "--session", cls.folder)
        cls.calls = [("look first; it is safe (really) & cheap | fine", '{"action": "look"}'), ("I'm crouching", '{"action": "crouch"}'),
                     ("nothing more to do", '{"action": "done"}')]
        cls.outputs = []
        for why, raw in cls.calls:
            cls.outputs.append(call("step", "--session", cls.folder, "--why", why, "--json", raw)[1])
        cls.session = json.loads((cls.folder / "session.json").read_text(encoding="utf-8"))
        cls.form = cls.session["step_form"]

    def records(self):
        records = [{"type": "user", "message": {"role": "user", "content": self.briefing + "\n" + self.first}}]
        for index, (why, raw) in enumerate(self.calls):
            records += self.turn(index, "Bash", {"command": step_command(self.form, why, raw), "description": "Robot step"}, self.outputs[index])
        return records

    def turn(self, index, name, tool_input, output):
        call_id = "toolu_{}_{}".format(name, index)
        return [{"type": "assistant", "requestId": "req_{}_{}".format(name, index),
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
