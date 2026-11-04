"""tools/audit_robot_transcript.py on the headless form of Claude Code's transcript, record for record the shape a real `claude -p` child wrote
(version 2.1.306, the transcript in docs/EVAL_HARNESS.md "Claude as the driver"), with one planted fault each. The subagent form's own negative
tests are in tests/test_robot_step.py. Offline, with a temporary home, normal and HOUSE_SIM_STRICT=1.
"""
import copy
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from house_sim.eval.common import load_authored

ROOT = Path(__file__).resolve().parent.parent
BED = "T-ed45170be6256e-01"
NONCE = "robot-audit-test-nonce"


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


def stamp(seconds):
    return "2026-11-04T04:{:02d}:{:02d}.000Z".format(seconds // 60, seconds % 60)


class HeadlessBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="robot_audit_"))
        cls.env = mock.patch.dict(os.environ, {"USERPROFILE": str(cls.tmp), "HOME": str(cls.tmp), "HOUSE_AI_FORBID_NETWORK": "1"})
        cls.env.start()
        spec_file = cls.tmp / "spec_file.json"
        spec_file.write_text(json.dumps({"specs": [load_authored(ROOT)[0][BED]]}), encoding="utf-8")
        cls.folder = cls.tmp / "run"
        status, cls.first_print = call("start", "--session", cls.folder, "--spec", spec_file, "--seed", 0, "--nonce", NONCE, "--robot-script", "robot.py")
        assert status == 0
        cls.calls = [("I will look; it is safe (really) & cheap", "see the room", "look then stop", '{"action": "look"}'), ("nothing more to do", "see the room", None, '{"action": "done"}')]
        cls.outputs = []
        for why, goal, plan, raw in cls.calls:
            argv = ["step", "--session", cls.folder, "--why", why, "--goal", goal, "--json", raw] + (["--plan", plan] if plan else [])
            cls.outputs.append(call(*argv)[1])
        cls.session = json.loads((cls.folder / "session.json").read_text(encoding="utf-8"))
        cls.briefing = (cls.folder / "briefing.txt").read_text(encoding="utf-8").rstrip("\n")
        cls.message = (cls.folder / "first_message.txt").read_text(encoding="utf-8").rstrip("\n")
        cls.reprint = cls.tmp / "reprint.json"
        assert call("reprint", "--session", cls.folder, "--out", cls.reprint)[0] == 0
        cls.final = cls.tmp / "final"
        assert call("finalize", "--session", cls.folder, "--out", cls.final)[0] == 0

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def step_text(self, why, goal, plan, raw):
        plan_text = ' --plan "{}"'.format(plan) if plan else ""
        return 'python robot.py step --why "{}" --goal "{}"{} --json \'{}\''.format(why, goal, plan_text, raw)

    def attachment(self, kind, seconds, rendered=None, **fields):
        record = {"type": "attachment", "isSidechain": False, "timestamp": stamp(seconds), "attachment": dict({"type": kind}, **fields)}
        if rendered is not None:
            record.update(rendered=[{"content": rendered}], renderedRole="system")
        return record

    def assistant_call(self, call_id, tool, tool_input, seconds, model="claude-opus-5-5"):
        return {"type": "assistant", "isSidechain": False, "timestamp": stamp(seconds), "requestRef": "r" + call_id,
                "message": {"id": "msg_" + call_id, "model": model, "role": "assistant", "usage": {"input_tokens": 3, "output_tokens": 20}, "content": [{"type": "tool_use", "id": call_id, "name": tool, "input": tool_input}]}}

    def tool_result(self, call_id, output, seconds):
        return {"type": "user", "isSidechain": False, "timestamp": stamp(seconds), "message": {"role": "user", "content": [{"tool_use_id": call_id, "type": "tool_result", "content": output, "is_error": False}]}}

    def records(self):
        """What a clean headless child wrote, in order."""
        system = [{"type": "text", "text": "You are a Claude agent, built on Anthropic's Claude Agent SDK."}, {"type": "text", "text": self.briefing}]
        out = [{"type": "queue-operation", "operation": "enqueue", "timestamp": stamp(0), "content": self.message}, {"type": "queue-operation", "operation": "dequeue", "timestamp": stamp(1)},
               {"type": "user", "isSidechain": False, "timestamp": stamp(1), "message": {"role": "user", "content": self.message}},
               self.attachment("environment", 1, "<system-reminder># Environment\n - Primary working directory: D:\\robot_run\\sim_abc\n - Platform: win32</system-reminder>", snapshot={"platform": "win32"}),
               self.attachment("model", 1, "You are powered by the model named Opus 5.5."), self.attachment("total_tokens_reminder", 1, "<total_tokens>15000000 tokens left</total_tokens>"),
               self.attachment("session_context", 1, context={}), self.attachment("date", 1, "Today's date is 2026-11-03."), self.attachment("prompt_snapshot", 1, systemPrompt=[self.briefing]),
               {"type": "atis-latch", "atis": ""}, {"type": "last-prompt", "lastPrompt": "x"}, {"type": "api-request-shape", "timestamp": stamp(1), "shape": {"system": system}},
               {"type": "api-request-blob", "hash": "h", "message": {"role": "user", "content": [{"type": "text", "text": self.message}]}},
               {"type": "api-request-blob", "hash": "i", "message": {"role": "system", "content": [{"type": "text", "text": "# Environment\nYou have been invoked in the following environment"}]}}]
        clock = 2
        for index, (why, goal, plan, raw) in enumerate(self.calls):
            call_id = "toolu_{}".format(index)
            out.append({"type": "assistant", "isSidechain": False, "timestamp": stamp(clock), "message": {"id": "msg_" + call_id, "model": "claude-opus-5-5", "role": "assistant", "content": [{"type": "text", "text": "Next."}]}})
            out.append(self.assistant_call(call_id, "Bash", {"command": self.step_text(why, goal, plan, raw), "description": "Robot step"}, clock))
            out.append(self.tool_result(call_id, self.outputs[index], clock + 10))
            out.append(self.attachment("total_tokens_reminder", clock + 10, "<total_tokens>14999977 tokens left</total_tokens>"))
            clock += 12
        out.append({"type": "assistant", "isSidechain": False, "timestamp": stamp(clock), "message": {"id": "msg_end", "model": "claude-opus-5-5", "role": "assistant", "content": [{"type": "text", "text": "Done."}]}})
        out.append({"type": "cost-state", "totalCostUSD": 0})
        return out

    def verdict(self, records, session=None, **kwargs):
        path = self.tmp / "transcript.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
        return audit.audit(path, session or self.folder, **kwargs)

    def assertVoid(self, records, kind, **kwargs):
        result = self.verdict(records, **kwargs)
        self.assertEqual(result["verdict"], "FAIL", result["warnings"])
        self.assertIn(kind, [v["kind"] for v in result["violations"]], result["violations"])
        return result


class TestHeadlessForm(HeadlessBase):
    def full(self, records, **kwargs):
        first = self.tmp / "first.txt"
        first.write_text(self.message + "\n", encoding="utf-8")
        return self.verdict(records, reprint=self.reprint, finalized=self.final, first_message=first, expect_model="claude-opus-5-5", **kwargs)

    def test_a_clean_headless_run_passes_every_check(self):
        result = self.full(self.records())
        self.assertEqual((result["verdict"], result["violations"], result["warnings"]), ("PASS", [], []))
        self.assertEqual(result["form"], "headless")
        self.assertEqual(result["kind"], "consistency audit")
        self.assertEqual(result["counts"]["step_calls"], 2)
        self.assertEqual(result["counts"]["outputs_matching_the_session"], 2)
        self.assertEqual(result["first_message"]["briefing_delivery"], "system_prompt")
        self.assertTrue(result["first_message"]["equals_the_launch_file"])
        self.assertTrue(result["priming"]["system_prompt_is_the_briefing"])
        self.assertEqual(result["models"], ["claude-opus-5-5"])
        self.assertEqual(result["compactions"], 0)
        self.assertTrue(result["chain"]["verifies"])
        self.assertEqual(result["chain"]["head"], robot.read_steps(self.folder)[-1]["h"])
        self.assertEqual(result["reprint"]["compared"], 1)
        self.assertTrue(result["finalized"]["replay_checked"])
        self.assertEqual(result["timing"]["tool"]["n"], 2)
        self.assertEqual(result["timing"]["tool"]["mean_s"], 10)
        self.assertEqual(result["timing"]["model"]["mean_s"], 2)

    def planted_first_call(self, records, command):
        for record in records:
            if record["type"] == "assistant" and record["message"]["content"][0]["type"] == "tool_use":
                record["message"]["content"][0]["input"]["command"] = command
                return records
        raise AssertionError

    # the five that DESIGN 3.7 names, in this form
    def test_cat_of_the_spec_voids(self):
        self.assertVoid(self.planted_first_call(self.records(), "cat run/spec.json"), "not_step_form")

    def test_a_semicolon_chain_voids(self):
        records = self.records()
        self.assertVoid(self.planted_first_call(records, self.step_text(*self.calls[0]) + " ; cat run/spec.json"), "not_step_form")

    def test_a_read_voids(self):
        records = self.records()
        at = next(i for i, r in enumerate(records) if r["type"] == "assistant")
        records[at:at] = [self.assistant_call("toolu_read", "Read", {"file_path": "run/spec.json"}, 2), self.tool_result("toolu_read", "something", 2)]
        self.assertVoid(records, "other_tool")

    def test_a_changed_reason_voids(self):
        why, goal, plan, raw = self.calls[0]
        self.assertVoid(self.planted_first_call(self.records(), self.step_text("a better reason written later", goal, plan, raw)), "reason_mismatch")
        self.assertVoid(self.planted_first_call(self.records(), self.step_text(why, "another goal", plan, raw)), "reason_mismatch")
        self.assertVoid(self.planted_first_call(self.records(), self.step_text(why, goal, None, raw)), "reason_mismatch")

    def test_a_changed_observation_voids_against_the_reprint(self):
        records = self.records()
        for record in records:
            content = (record.get("message") or {}).get("content")
            if isinstance(content, list) and content[0].get("type") == "tool_result":
                content[0]["content"] = content[0]["content"].replace('"ok":true', '"ok":false')
                break
        self.assertVoid(records, "reprint_mismatch", reprint=self.reprint)
        self.assertEqual(self.verdict(records)["verdict"], "PASS")      # with no reprint it can only be a warning
        self.assertIn("output_differs", [w["kind"] for w in self.verdict(records)["warnings"]])

    # A2
    def test_an_unknown_block_voids(self):
        records = self.records()
        records.insert(4, {"type": "user", "message": {"role": "user", "content": [{"type": "image", "source": {}}]}})
        self.assertVoid(records, "unknown_block")

    def test_a_hook_that_reaches_the_model_voids_and_a_silent_one_is_a_warning(self):
        self.assertVoid(self.records() + [self.attachment("hook_additional_context", 90, "extra words")], "hook_reached_model")
        result = self.verdict(self.records() + [self.attachment("hook_non_blocking_error", 90)])
        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(result["priming"]["hook_records"], {"rendered": 0, "silent": 1})
        self.assertIn("hook_records", [w["kind"] for w in result["warnings"]])

    def test_an_unknown_attachment_that_reaches_the_model_voids(self):
        self.assertVoid(self.records() + [self.attachment("something_new", 90, "words")], "unknown_attachment")
        self.assertEqual(self.verdict(self.records() + [self.attachment("something_new", 90)])["verdict"], "PASS")

    # A3
    def test_a_planted_extra_or_dropped_step_voids(self):
        extra = self.step_text("one more", "g", None, '{"action": "look"}')
        self.assertVoid(self.records() + [self.assistant_call("toolu_x", "Bash", {"command": extra}, 90), self.tool_result("toolu_x", "x", 91)], "extra_step")
        records = [r for r in self.records() if json.dumps(r).find("toolu_1") < 0]
        self.assertVoid(records, "dropped_step")

    def test_a_log_that_differs_voids(self):
        changed = self.tmp / "final_changed"
        if changed.exists():
            shutil.rmtree(changed)
        shutil.copytree(self.final, changed)
        path = next((changed / "logs").glob("*.json"))
        log = json.loads(path.read_text(encoding="utf-8"))
        log["steps"][0]["command"]["_why"] = "rewritten afterwards"
        path.write_text(json.dumps(log), encoding="utf-8")
        self.assertVoid(self.records(), "log_mismatch", finalized=changed)
        log = json.loads((next((self.final / "logs").glob("*.json"))).read_text(encoding="utf-8"))
        log["steps"].insert(1, {"i": 2, "command": {"action": "wait", "seconds": 1}, "sent": {"action": "wait", "seconds": 1}, "result": {}})
        path.write_text(json.dumps(log), encoding="utf-8")
        self.assertVoid(self.records(), "log_mismatch", finalized=changed)

    # A5
    def test_a_project_word_or_a_label_in_the_first_message_voids(self):
        for word in ("the flagship scenario", "The Home Reset", self.session["catalog_id"]):
            records = self.records()
            records[2]["message"]["content"] = self.message + "\n" + word
            self.assertIn(self.verdict(records)["verdict"], ("FAIL",), word)
        target = self.tmp / "labelled"
        if not target.exists():
            shutil.copytree(self.folder, target)
            spec = json.loads((target / "spec.json").read_text(encoding="utf-8"))
            spec["success"] = [{"label": "secret_label", "pred": {}}]
            spec["setup"] = [{"op": "event", "at_s": 5, "effect": "people.request"}, {"op": "helper", "helper": "mechanisms.scene_bind"}]
            (target / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
        self.assertEqual(audit.spec_tokens(target), ["mechanisms.scene_bind", "people.request", "secret_label"])
        for word in audit.spec_tokens(target):
            records = self.records()
            records[2]["message"]["content"] = self.message + "\n" + word
            self.assertVoid(records, "priming_leak", session=target)

    def test_a_nonce_or_seed_tag_in_a_tool_output_voids(self):
        for value in (self.session["episode_nonce"], self.session["seed_tag"]):
            records = self.records()
            records.append(self.tool_result("toolu_0", value, 40))
            self.assertVoid(records, "leak")

    def test_personal_priming_voids_in_this_form(self):
        for kind, rendered, fields in (("environment", "<system-reminder>The user's email address is someone@example.com</system-reminder>", {}),
                                       ("date", "gitStatus: Current branch: main", {}), ("date", "Contents of MEMORY.md", {}),
                                       ("session_context", None, {"context": {"userEmail": "someone@example.com"}})):
            records = self.records()
            records.insert(3, self.attachment(kind, 1, rendered, **fields))
            self.assertVoid(records, "priming_personal")

    def test_a_subagent_priming_attachment_voids_in_this_form(self):
        records = self.records()
        records.insert(3, self.attachment("instructions", 1, "Contents of a project file"))
        self.assertVoid(records, "priming_attachment")

    def test_a_system_prompt_that_is_not_the_briefing_voids(self):
        records = self.records()
        for record in records:
            if record.get("attachment", {}).get("type") == "prompt_snapshot":
                record["attachment"]["systemPrompt"] = ["You are Claude Code, Anthropic's official CLI."]
        self.assertVoid(records, "system_prompt")
        records = self.records()
        for record in records:
            if record["type"] == "api-request-shape":
                record["shape"]["system"].append({"type": "text", "text": "x" * 2000})
        self.assertVoid(records, "system_prompt")

    def test_a_first_message_that_is_not_the_launch_file_voids(self):
        records = self.records()
        records[2]["message"]["content"] = self.message + "\nAlso, the valve is behind the door."
        first = self.tmp / "first.txt"
        first.write_text(self.message, encoding="utf-8")
        self.assertVoid(records, "first_message", first_message=first)

    # A6, A7
    def test_two_models_void_and_compaction_is_counted_not_void(self):
        records = self.records()
        at = next(r for r in records if r["type"] == "assistant" and r["message"]["content"][0]["type"] == "tool_use")
        at["message"]["model"] = "claude-other"
        self.assertVoid(records, "model_changed")
        self.assertVoid(self.records(), "model_changed", expect_model="claude-other")
        records = self.records() + [{"type": "system", "subtype": "compact_boundary", "timestamp": stamp(70)}, {"type": "user", "isCompactSummary": True, "message": {"role": "user", "content": []}}]
        result = self.verdict(records)
        self.assertEqual((result["verdict"], result["compactions"]), ("PASS", 2))

    def test_a_synthetic_message_is_a_warning(self):
        records = self.records() + [{"type": "assistant", "timestamp": stamp(70), "message": {"model": "<synthetic>", "role": "assistant", "content": [{"type": "text", "text": "Not logged in"}]}}]
        result = self.verdict(records)
        self.assertEqual((result["verdict"], result["models"]), ("PASS", ["claude-opus-5-5"]))
        self.assertIn("synthetic_message", [w["kind"] for w in result["warnings"]])

    # A9
    def edited_copy(self, name, change):
        target = self.tmp / name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(self.folder, target)
        path = target / "steps.jsonl"
        lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        change(lines, target)
        path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
        return target

    def test_a_broken_chain_voids(self):
        def wrong_head(lines, _target):
            lines[0]["h"] = "0" * 64

        def no_intent(_lines, target):
            (target / "chain.jsonl").write_text("", encoding="utf-8")

        def wrong_print(lines, _target):
            lines[0]["output"] = lines[0]["output"] + " "

        def wrong_genesis(_lines, target):
            session = json.loads((target / "session.json").read_text(encoding="utf-8"))
            session["seed_tag"] = "dev:99"
            (target / "session.json").write_text(json.dumps(session), encoding="utf-8")

        for name, change in (("c1", wrong_head), ("c2", no_intent), ("c3", wrong_print), ("c4", wrong_genesis)):
            self.assertVoid(self.records(), "chain", session=self.edited_copy(name, change))

    def test_a_command_that_never_returned_is_reported(self):
        def orphan(_lines, target):
            with open(target / "chain.jsonl", "a", encoding="utf-8") as handle:
                handle.write(json.dumps({"n": 3, "h": "f" * 64, "why": "lost"}) + "\n")

        result = self.verdict(self.records(), session=self.edited_copy("c5", orphan))
        self.assertEqual(result["verdict"], "PASS")
        self.assertIn("commands_that_never_returned", [w["kind"] for w in result["warnings"]])

    # A10
    def test_an_output_timed_before_its_call_voids(self):
        records = self.records()
        at = next(r for r in records if r["type"] == "user" and isinstance(r["message"]["content"], list))
        at["timestamp"] = stamp(0)
        self.assertVoid(records, "timing")

    # the calls that change nothing
    def test_a_refused_call_or_one_after_the_end_is_counted_not_void(self):
        missing_goal = 'python robot.py step --why "forgot" --json \'{"action": "look"}\''
        records = self.records()
        at = next(i for i, r in enumerate(records) if r["type"] == "assistant")
        records[at:at] = [self.assistant_call("toolu_bad", "Bash", {"command": missing_goal}, 2), self.tool_result("toolu_bad", robot.BAD_CALL_TEXT, 2)]
        records += [self.assistant_call("toolu_late", "Bash", {"command": self.step_text("again", "g", None, '{"action": "look"}')}, 90), self.tool_result("toolu_late", robot.OVER_TEXT, 91)]
        result = self.verdict(records)
        self.assertEqual((result["verdict"], result["counts"]["refused_calls"]), ("PASS", 2))
        self.assertIn("refused_calls", [w["kind"] for w in result["warnings"]])
        # the same text from a call that is not the form is still void
        records = self.records()
        records[at:at] = [self.assistant_call("toolu_bad", "Bash", {"command": "cat run/spec.json"}, 2), self.tool_result("toolu_bad", robot.BAD_CALL_TEXT, 2)]
        self.assertVoid(records, "not_step_form")

    def test_the_long_form_with_more_pages(self):
        folder = self.tmp / "paged"
        spec_file = self.tmp / "spec_file.json"
        status, first = call("start", "--session", folder, "--spec", spec_file, "--seed", 0, "--nonce", NONCE, "--robot-script", "robot.py")
        self.assertEqual(status, 0)
        printer = robot._printer()
        original = printer.pages
        with mock.patch.object(printer, "pages", lambda text, line: original(text, line, 300)):
            shown = call("step", "--session", folder, "--why", "w", "--goal", "g", "--json", '{"action": "look"}')[1]
        entry = robot.read_steps(folder)[0]
        message = (folder / "first_message.txt").read_text(encoding="utf-8").rstrip("\n")
        briefing = (folder / "briefing.txt").read_text(encoding="utf-8").rstrip("\n")
        records = [{"type": "user", "timestamp": stamp(1), "message": {"role": "user", "content": message}}, self.attachment("prompt_snapshot", 1, systemPrompt=[briefing]),
                   self.assistant_call("t0", "Bash", {"command": self.step_text("w", "g", None, '{"action": "look"}')}, 2), self.tool_result("t0", shown, 3)]
        for index, page in enumerate(entry["pages"][1:]):
            call_id = "m{}".format(index)
            records += [self.assistant_call(call_id, "Bash", {"command": "python robot.py more"}, 4 + index), self.tool_result(call_id, page, 5 + index)]
        result = self.verdict(records, session=folder)
        self.assertEqual((result["verdict"], result["warnings"]), ("PASS", []))
        self.assertEqual(result["counts"]["more_calls"], len(entry["pages"]) - 1)
        records[-1]["message"]["content"][0]["content"] = "something else"
        self.assertIn("output_differs", [w["kind"] for w in self.verdict(records, session=folder)["warnings"]])
        self.assertViolationFree(records, folder)

    def assertViolationFree(self, records, folder):
        self.assertEqual(self.verdict(records, session=folder)["violations"], [])


class TestStopped(HeadlessBase):
    def test_a_run_the_robot_stopped_passes_with_its_idle_commands_marked(self):
        folder = self.tmp / "stopped"
        spec_file = self.tmp / "spec_file.json"
        status, _first = call("start", "--session", folder, "--spec", spec_file, "--seed", 0, "--nonce", NONCE, "--robot-script", "robot.py")
        self.assertEqual(status, 0)
        shown = call("step", "--session", folder, "--why", "w", "--goal", "g", "--json", '{"action": "look"}')[1]
        out = self.tmp / "stopped_out"
        self.assertEqual(call("finalize", "--session", folder, "--out", out, "--stop-reason", "turn_ended_without_done")[0], 0)
        message = (folder / "first_message.txt").read_text(encoding="utf-8").rstrip("\n")
        briefing = (folder / "briefing.txt").read_text(encoding="utf-8").rstrip("\n")
        records = [{"type": "user", "timestamp": stamp(1), "message": {"role": "user", "content": message}}, self.attachment("prompt_snapshot", 1, systemPrompt=[briefing]),
                   self.assistant_call("t0", "Bash", {"command": self.step_text("w", "g", None, '{"action": "look"}')}, 2), self.tool_result("t0", shown, 3)]
        result = self.verdict(records, session=folder, finalized=out)
        self.assertEqual(result["verdict"], "PASS", result["violations"])
        self.assertEqual(result["finalized"]["end_reason"], "robot_stopped")
        self.assertEqual(result["finalized"]["stop_reason"], "turn_ended_without_done")
        self.assertGreater(result["finalized"]["idle_commands"], 0)
        # an idle command that is not marked is someone else's
        path = next((out / "logs").glob("*.json"))
        log = json.loads(path.read_text(encoding="utf-8"))
        changed = copy.deepcopy(log)
        for entry in changed["steps"][1:]:
            (entry.get("command") or {}).pop("_idle", None)
        path.write_text(json.dumps(changed), encoding="utf-8")
        self.assertVoid(records, "log_mismatch", session=folder, finalized=out)


if __name__ == "__main__":
    unittest.main()
