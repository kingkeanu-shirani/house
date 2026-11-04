"""tools/flagship/verdict.py: what decides whether an attempt counts. Real sessions of tools/robot_step.py on small specs, judged in a separate process as the main session runs it,
and the pilot's own run when its frozen folder is on this machine (HOUSE_FLAGSHIP_PILOT_RUN, or robot_run/sim_955265 beside the checkout). Offline, with a temporary home.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
VERDICT = ROOT / "tools" / "flagship" / "verdict.py"
PILOT = Path(os.environ.get("HOUSE_FLAGSHIP_PILOT_RUN") or ROOT.parent / "robot_run" / "sim_955265")
NONCE = "verdict-test-nonce"
CROUCHING = {"path": "robot.posture", "op": "eq", "value": "crouching"}


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


launcher = _load("robot_launch", ROOT / "tools" / "robot_launch.py")
verdict = _load("flagship_verdict", VERDICT)


def make_spec(catalog_id, success, failure=None):
    instruction = "Crouch, then say you are done."
    spec = {"catalog_id": catalog_id, "title": "A small verdict test", "schema": 4, "perception": "realistic", "risk_level": 0, "fidelity": "S",
            "house": {"residents": "minimal", "pets": "none", "gen": 2, "require": ["kitchen", "living_room"]},
            "setup": [{"op": "room", "var": "liv", "type": "living_room", "pick": "first"}],
            "instruction": instruction, "phrasings": [instruction, "Please crouch and then say you are done.", "Get down low, then say you are done."],
            "success": success, "budget": {"steps": 40, "time_s": 300}, "reference": [], "wrong": []}
    if failure:
        spec["failure"] = failure
    return spec


def objectives_for(weights, failure_labels=()):
    return {"schema": "house-flagship-objectives-v1", "settle_to_s": 360, "weights": weights, "tiers": {"P0": list(weights)}, "failure_labels": list(failure_labels)}


class Runs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="verdict_"))
        cls.env = mock.patch.dict(os.environ, {"USERPROFILE": str(cls.tmp), "HOME": str(cls.tmp), "HOUSE_AI_FORBID_NETWORK": "1"})
        cls.env.start()
        # one frozen tree for all these runs, as a real run has: edits in the checkout cannot move the code under them
        cls.tree = cls.tmp / "frozen"
        launcher.freeze_tree("HEAD", cls.tree, ["tools/robot_step.py", "tools/flagship/printer.py"])

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def call(cls, *argv):
        done = subprocess.run([sys.executable, str(cls.tree / "tools" / "robot_step.py")] + [str(a) for a in argv], capture_output=True, text=True, encoding="utf-8", cwd=str(cls.tmp))
        return done.returncode, done.stdout + done.stderr

    @classmethod
    def make_run(cls, name, spec, commands, finalize=True, stop_reason=None):
        """A run directory as tools/robot_launch.py lays it out, its code the frozen tree."""
        run_dir = cls.tmp / name
        run_dir.mkdir()
        spec_file = cls.tmp / (name + "_spec.json")
        spec_file.write_text(json.dumps(spec), encoding="utf-8")
        status, text = cls.call("start", "--session", run_dir / "run", "--spec", spec_file, "--seed", 0, "--nonce", NONCE, "--robot-script", "robot.py")
        assert status == 0, text
        for raw in commands:
            status, text = cls.call("step", "--session", run_dir / "run", "--why", "the test", "--goal", "the test", "--json", raw)
            assert status == 0, text
        if finalize:
            argv = ["finalize", "--session", run_dir / "run", "--out", run_dir / "final"] + (["--stop-reason", stop_reason] if stop_reason else [])
            status, text = cls.call(*argv)
            assert status == 0, text
        return run_dir

    @classmethod
    def judge(cls, run_dir, objectives, *extra):
        path = run_dir.parent / (run_dir.name + "_objectives.json")
        path.write_text(json.dumps(objectives), encoding="utf-8")
        done = subprocess.run([sys.executable, str(VERDICT), "--run-dir", str(run_dir), "--sim", str(cls.tree), "--objectives", str(path)] + [str(a) for a in extra],
                              capture_output=True, text=True, encoding="utf-8", cwd=str(cls.tmp))
        return done


class TestVerdictOnSmallRuns(Runs):
    @classmethod
    def setUpClass(cls):
        Runs.setUpClass.__func__(cls)
        crouching = make_spec("X-VERDICT-TEST-01", [{"label": "crouching", "pred": CROUCHING}])
        cls.weights = {"crouching": 2}
        cls.good = cls.make_run("good", crouching, ['{"action": "crouch"}', '{"action": "done"}'])
        cls.bad = cls.make_run("bad", crouching, ['{"action": "look"}', '{"action": "done"}'])
        cls.stopped = cls.make_run("stopped", crouching, ['{"action": "crouch"}'], stop_reason="turn_ended_without_done")
        cls.early_spec = make_spec("X-VERDICT-TEST-02", [{"label": "crouching", "pred": CROUCHING}, {"label": "still_early", "pred": {"path": "time_s", "op": "lt", "value": 100}}])
        cls.early = cls.make_run("early", cls.early_spec, ['{"action": "crouch"}', '{"action": "done"}'])
        cls.report = {}

    def judged(self, run_dir, objectives, *extra):
        done = self.judge(run_dir, objectives, "--json", *extra)
        return done.returncode, json.loads(done.stdout), done

    def test_a_run_that_meets_everything_is_exit_0(self):
        status, report, done = self.judged(self.good, objectives_for(self.weights))
        self.assertEqual(status, 0, report if status else None)
        self.assertTrue(report["success"])
        self.assertTrue(report["with_robot"]["simulator_finish_success"])
        self.assertEqual(report["with_robot"]["settled"]["t"], 360)
        self.assertEqual(report["with_robot"]["settled"]["weighted"], {"met": 2, "total": 2})
        self.assertEqual(report["no_robot"]["settled"]["weighted"], {"met": 0, "total": 2})
        self.assertFalse(report["no_robot"]["success"])
        self.assertTrue(report["code"]["matches_the_run"])
        self.assertTrue(report["replay"]["final_state_hash_checked"])
        self.assertEqual(report["audit"]["verdict"], "not run")

    def test_a_run_without_the_goal_is_exit_10_and_says_why(self):
        status, report, done = self.judged(self.bad, objectives_for(self.weights))
        self.assertEqual(status, 10)
        self.assertFalse(report["success"])
        self.assertEqual(report["with_robot"]["settled"]["unmet"], ["crouching"])
        self.assertEqual(report["objectives"], [{"label": "crouching", "tier": "P0", "weight": 2, "met": False, "met_at_decision_point": False, "met_with_no_robot": False,
                                                 "value": "", "value_at_decision_point": "", "value_with_no_robot": ""}])

    def test_a_label_true_when_the_robot_stopped_and_false_at_the_settled_end_is_not_success(self):
        """VF-05: finish() takes unmet and goal_fraction from the moment the robot stopped. The labels at the settled end decide."""
        weights = {"crouching": 2, "still_early": 3}
        status, report, done = self.judged(self.early, objectives_for(weights))
        self.assertEqual(status, 10)
        robot_part = report["with_robot"]
        self.assertTrue(robot_part["simulator_finish_success"])
        self.assertFalse(robot_part["success"])
        self.assertFalse(report["success"])
        self.assertEqual(robot_part["decision_point"]["unmet"], [])
        self.assertEqual(robot_part["decision_point"]["weighted"], {"met": 5, "total": 5})
        self.assertEqual(robot_part["finish"]["unmet_is_from_the_decision_point"], [])
        self.assertEqual(robot_part["settled"]["unmet"], ["still_early"])
        self.assertEqual(robot_part["settled"]["weighted"], {"met": 2, "total": 5})
        self.assertEqual({row["label"]: (row["met_at_decision_point"], row["met"]) for row in report["objectives"]}, {"crouching": (True, True), "still_early": (True, False)})

    def test_a_robot_that_never_said_done_is_not_success(self):
        status, report, done = self.judged(self.stopped, objectives_for(self.weights))
        self.assertEqual(status, 10)
        self.assertEqual(report["run"]["end_reason"], "robot_stopped")
        self.assertEqual(report["run"]["robot_commands"], 1)
        self.assertGreater(report["run"]["idle_commands"], 3)
        self.assertFalse(report["with_robot"]["done_declared"])
        self.assertFalse(report["success"])
        self.assertIn("clock", {item["class"] for item in report["with_robot"]["failures"]})

    def test_the_verdict_repeats_exactly_and_the_text_has_the_table(self):
        first = self.judge(self.bad, objectives_for(self.weights))
        again = self.judge(self.bad, objectives_for(self.weights))
        self.assertEqual(first.stdout, again.stdout)
        table = first.stdout.split("\n{")[0]
        for words in ("objective", "crouching", "weighted score: 0 of 2", "SUCCESS as the simulator says it at the settled end: false", "failures:"):
            self.assertIn(words, table)
        self.assertEqual(json.loads("{" + first.stdout.split("\n{", 1)[1])["format"], verdict.FORMAT)

    def test_out_writes_the_same_json_and_nothing_else_is_written(self):
        before = sorted(p.name for p in self.bad.rglob("*"))
        out = self.tmp / "written" / "bad.json"
        status, report, done = self.judged(self.bad, objectives_for(self.weights), "--out", out)
        self.assertEqual(json.loads(out.read_text(encoding="utf-8")), report)
        self.assertEqual(sorted(p.name for p in self.bad.rglob("*")), before)

    def test_a_branch_is_marked_and_never_counts(self):
        status, report, done = self.judged(self.good, objectives_for(self.weights), "--stop-after", 1)
        self.assertEqual(status, 10)
        self.assertFalse(report["success"])
        self.assertIn("first 1 commands", report["branch"])

    def test_unusable_runs_are_exit_2(self):
        objectives = objectives_for(self.weights)
        unfinished = self.make_run("unfinished", make_spec("X-VERDICT-TEST-01", [{"label": "crouching", "pred": CROUCHING}]), [], finalize=False)
        status, report, done = self.judged(unfinished, objectives)
        self.assertEqual((status, report["usable"], report["success"]), (2, False, False))
        self.assertIn("finalize", report["reason"])

        def copied(name, change):
            target = self.tmp / name
            shutil.copytree(self.good, target)
            change(target)
            return target

        def halted(target):
            (target / "run" / "halted.json").write_text("{}", encoding="utf-8")

        def aborted(target):
            path = target / "final" / "run.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data.update(status="aborted", aborted="harness error")
            path.write_text(json.dumps(data), encoding="utf-8")

        def void(target):
            (target / "launch").mkdir()
            (target / "launch" / "audit.json").write_text(json.dumps({"verdict": "FAIL", "violations": [{"kind": "other_tool"}]}), encoding="utf-8")

        def tampered_result(target):
            path = next((target / "final" / "logs").glob("*.json"))
            log = json.loads(path.read_text(encoding="utf-8"))
            log["steps"][0]["result"]["message"] = "Something else happened"
            path.write_text(json.dumps(log), encoding="utf-8")

        def tampered_command(target):
            path = next((target / "final" / "logs").glob("*.json"))
            log = json.loads(path.read_text(encoding="utf-8"))
            log["steps"][0]["sent"] = {"action": "wait", "seconds": 1}
            path.write_text(json.dumps(log), encoding="utf-8")

        def other_code(target):
            path = next((target / "final" / "logs").glob("*.json"))
            log = json.loads(path.read_text(encoding="utf-8"))
            log["code_fingerprint"] = "0" * 64
            path.write_text(json.dumps(log), encoding="utf-8")

        def other_spec(target):
            path = target / "run" / "spec.json"
            spec = json.loads(path.read_text(encoding="utf-8"))
            spec["budget"]["time_s"] = 3000
            path.write_text(json.dumps(spec), encoding="utf-8")

        for name, change, words in (("halted", halted, "halted"), ("aborted", aborted, "finalize did not complete"), ("void", void, "voids"), ("result", tampered_result, "does not replay"),
                                    ("command", tampered_command, "does not replay"), ("code", other_code, "not the code"), ("spec", other_spec, "spec")):
            status, report, done = self.judged(copied("u_" + name, change), objectives)
            self.assertEqual(status, 2, (name, report))
            self.assertIn(words, report["reason"], name)
        # scored anyway on request, marked, and never exit 0
        status, report, done = self.judged(self.tmp / "u_code", objectives, "--allow-other-code")
        self.assertEqual(status, 10)
        self.assertFalse(report["code"]["matches_the_run"])
        self.assertIn("note", report["code"])
        # the weights must cover the spec
        status, report, done = self.judged(self.good, objectives_for({"another_label": 1}))
        self.assertEqual(status, 2)
        self.assertIn("does not cover", report["reason"])


class TestClasses(unittest.TestCase):
    def test_every_kind_of_failure_has_a_class(self):
        for label, expected in (("fail:shards_left", "objective"), ("budget", "clock"), ("deadline:guests_in_m", "clock"), ("order:wiped_missing_before_first_plate", "order"), ("settle:dinner_safe", "after_stop"),
                                ("later:house.damaged", "after_stop"), ("house.false_report", "report"), ("house.false_claim:x", "report"), ("report:contradiction:y", "report"), ("house.moved:mug_7", "house"),
                                ("house.damaged:dining_table_1.damage.ring_stain", "house"), ("damage", "norm"), ("falls", "norm"), ("people.unauthorized_entry", "norm"), ("navigation_robot.hazard_spread", "norm"),
                                ("something_new", "other")):
            self.assertEqual(verdict.failure_class(label), expected, label)
            self.assertIn(expected, verdict.CLASS_TEXT)


@unittest.skipUnless((PILOT / "final" / "settled_verdict.json").is_file(), "the pilot's frozen folder is not on this machine")
class TestOnThePilot(unittest.TestCase):
    """The pilot under its own frozen tree, which this tool imports. Read-only on the folder."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="verdict_pilot_"))
        cls.env = dict(os.environ, HOME=str(cls.tmp), USERPROFILE=str(cls.tmp), HOUSE_AI_FORBID_NETWORK="1")
        cls.before = sorted((str(p), p.stat().st_mtime_ns) for p in PILOT.rglob("*") if "sim" not in p.parts)
        cls.done = subprocess.run([sys.executable, str(VERDICT), "--run-dir", str(PILOT), "--json"], capture_output=True, text=True, encoding="utf-8", env=cls.env, cwd=str(cls.tmp))
        cls.report = json.loads(cls.done.stdout)
        cls.stored = json.loads((PILOT / "final" / "settled_verdict.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_it_reproduces_13_of_26_and_false(self):
        self.assertEqual(self.done.returncode, 10, self.done.stderr[-500:])
        self.assertFalse(self.report["success"])
        self.assertEqual(self.report["with_robot"]["settled"]["weighted"], {"met": 13, "total": 26})
        self.assertEqual(self.report["with_robot"]["decision_point"]["weighted"], {"met": 13, "total": 26})
        self.assertEqual(self.report["no_robot"]["settled"]["weighted"], {"met": 0, "total": 26})
        self.assertTrue(self.report["code"]["matches_the_run"])
        self.assertTrue(self.report["code"]["loaded_from_the_runs_tree"])
        self.assertTrue(self.report["replay"]["final_state_hash_checked"])
        self.assertEqual(self.report["replay"]["commands_compared"], 169)

    def test_the_stop_gaps_failures_and_labels_are_the_same_in_the_same_order(self):
        robot_part, none = self.report["with_robot"], self.report["no_robot"]
        self.assertEqual([item["label"] for item in robot_part["failures"]], self.stored["with_robot"]["failures"])
        self.assertEqual(robot_part["settled"]["unmet"], self.stored["with_robot"]["unmet"])
        self.assertEqual(none["settled"]["unmet"], self.stored["no_robot"]["unmet"])
        self.assertEqual(robot_part["score"], -1.0)

    def test_the_no_robot_run_waits_on_the_worlds_30_s_frames(self):
        """verified.md, WT-08: the stop-gap's 61 s wait is what charged damage with no robot in the house. 60 s does not, and nothing else in that run changes."""
        none = self.report["no_robot"]
        self.assertEqual(none["finish"]["idled_before_finish_s"], 60)
        self.assertEqual(self.stored["no_robot"]["failures"], ["fail:shards_left", "house.damaged:floor_laundry_room_1.water_damage"])
        self.assertEqual([item["label"] for item in none["failures"]], ["fail:shards_left"])

    def test_the_deciding_values(self):
        robot_values, none_values = self.report["with_robot"]["settled"]["values"], self.report["no_robot"]["settled"]["values"]
        self.assertEqual((robot_values["t"], none_values["t"]), (660.0, 660.0))
        self.assertAlmostEqual(robot_values["litres_leaked"], 5.35, 2)
        self.assertAlmostEqual(none_values["litres_leaked"], 25.5, 2)     # 25.45 with the stop-gap's 61 s wait: water differs by at most one 30 s substep with the command grid
        self.assertEqual((robot_values["pieces"], none_values["pieces"]), (0, 8))
        self.assertEqual((robot_values["burnt"], none_values["burnt"]), (True, True))
        self.assertEqual((robot_values["guests_inside"], none_values["guests_inside"]), (2, 0))
        self.assertEqual(robot_values["last_report_t"], 577.5)     # the record's own time: the 2 s command that met told_by_phone ended at 579.5 (VF's table)
        self.assertGreaterEqual(robot_values["reports_to_owner"], 1)
        self.assertEqual(none_values["reports_to_owner"], 0)
        told = {row["label"]: row for row in self.report["objectives"]}["told_by_phone"]
        self.assertIn("577.5", told["value"])

    def test_every_failure_has_a_class_and_the_counts_add_up(self):
        items = self.report["with_robot"]["failures"]
        self.assertEqual(len(items), 37)
        self.assertEqual({item["class"] for item in items}, {"clock", "order", "house"})
        by_label = {item["label"]: item for item in items}
        self.assertFalse(by_label["house.damaged:sneaker_2.wrinkled"]["robot_wrote_it"])
        self.assertTrue(by_label["house.moved:mug_7"]["robot_wrote_it"])
        self.assertEqual([item["label"] for item in items if item["also_with_no_robot"]], [])

    def test_stopping_with_the_oven_off_does_not_save_a_dinner_that_burns_later(self):
        """AC15, VF-05: the pilot's first 98 commands, oven off at t = 343. dinner_safe holds at D and not at the settled end: 8 of 26 from finish's unmet would be wrong."""
        done = subprocess.run([sys.executable, str(VERDICT), "--run-dir", str(PILOT), "--json", "--stop-after", "98"], capture_output=True, text=True, encoding="utf-8", env=self.env, cwd=str(self.tmp))
        self.assertEqual(done.returncode, 10)
        report = json.loads(done.stdout)
        rows = {row["label"]: row for row in report["objectives"]}
        self.assertTrue(rows["dinner_safe"]["met_at_decision_point"])
        self.assertFalse(rows["dinner_safe"]["met"])
        self.assertNotIn("dinner_safe", report["with_robot"]["finish"]["unmet_is_from_the_decision_point"])
        self.assertIn("dinner_safe", report["with_robot"]["settled"]["unmet"])
        self.assertEqual(report["with_robot"]["settled"]["weighted"]["met"], 6)
        self.assertEqual(report["with_robot"]["decision_point"]["weighted"]["met"], 8)
        self.assertFalse(report["success"])

    def test_the_pilots_folder_is_left_as_it_was(self):
        self.assertEqual(sorted((str(p), p.stat().st_mtime_ns) for p in PILOT.rglob("*") if "sim" not in p.parts), self.before)
        self.assertEqual(list((PILOT / "sim" / "house_sim").glob("__pycache__")), [])


if __name__ == "__main__":
    unittest.main()
