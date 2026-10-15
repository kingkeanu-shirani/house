"""Multi-phase episodes and requests (kernel v3.1, TIER1_SWEEP 9, 10.1 and 21 "TIME-EVAL" items 4 and 7): a two-phase
episode with a gap, gap events, the robot parked on its dock, memory carried, per-phase scoring, supersedes, phase
ends by time and budget, the verifier (idle fails, phase_done plan steps), the harness's done loop (spy agent) and its
records, schema-3 build defaults and the privileged-wait compilation; the stage-C merge's end-to-end two-phase spec (a
question asked once and remembered across the gap)."""
import copy
import tempfile
import unittest
from pathlib import Path

from house_sim.eval import agents as A
from house_sim.eval import run as R
from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.predicates import digest
from house_sim.tasks import verify
from house_sim.tasks.runner import PlanError, build, make_env, run_plan

SAY = {"path": "facts.last_speech.text", "op": "eq"}


def said(text):
    return dict(SAY, value=text)


SPEC = {
    "catalog_id": "T-phases-test-01", "title": "two requests two hours apart", "risk_level": 0,
    "house": {"residents": "minimal", "pets": "none"},
    "phases": [
        {"id": "p1", "request": {"from": "resident_1", "channel": "app",
                                 "phrasings": ["Say one.", "Please say one.", "One, please."]},
         "success": [said("one")], "budget": {"steps": 20, "time_s": 900}},
        {"id": "p2", "start": {"after": "p1", "gap_s": 7200, "robot": "dock",
                               "events": [{"at_gap_s": 3600, "changes": {"facts.night_event": True}}]},
         "request": {"from": "resident_1", "channel": "app",
                     "phrasings": ["Say two.", "Please say two.", "Two, please."]},
         "success": [said("two")], "questions": {"max_unnecessary": 0}, "budget": {"steps": 20, "time_s": 900}}],
    "reference": [{"do": "announce", "text": "one"}, {"phase_done": True}, {"do": "announce", "text": "two"},
                  {"phase_done": True}],
    "wrong": [{"name": "says one twice", "plan": [{"do": "announce", "text": "one"}, {"phase_done": True},
                                                  {"do": "announce", "text": "one"}, {"phase_done": True}]},
              {"name": "never ends the first request", "plan": [{"do": "announce", "text": "one"},
                                                                 {"do": "announce", "text": "two"}]}]}


def episode_env(spec=SPEC, seed=0):
    config, task, oracle = build(copy.deepcopy(spec), seed)
    return HouseEnv(config, task, seed, trace=True), task, oracle


def other_room(env):
    state = env.world.s
    return sorted(state["rooms"][state["robot"]["room"]]["exits"])[0]


class TwoPhaseTests(unittest.TestCase):
    def test_build_resolves_requests_budget_and_instruction(self):
        config, task, _ = build(copy.deepcopy(SPEC), 0)
        self.assertEqual([p["id"] for p in task["phases"]], ["p1", "p2"])
        self.assertIn(task["phases"][0]["request"]["text"], SPEC["phases"][0]["request"]["phrasings"])
        self.assertEqual(task["instruction"], task["phases"][0]["instruction"])
        self.assertEqual(task["phases"][1]["request"]["id"], "r2")
        self.assertEqual(task["step_budget"], 40)
        self.assertEqual(task["time_budget_s"], 900 + 900 + 7200)
        self.assertEqual(config["user_simulator"]["intent"]["requester"], "resident_1")
        self.assertNotIn("schema", task)
        self.assertNotIn("notices", config)

    def test_a_two_phase_episode_with_its_gap(self):
        env, task, _ = episode_env()
        state = env.world.s
        self.assertEqual(state["phase"]["active"], "p1")
        self.assertEqual([r["id"] for r in state["requests"]], ["r1"])
        message = state["messages"][-1]
        self.assertEqual((message["request"], message["to"], message["heard_by_robot"]), ("r1", "robot", True))
        self.assertEqual(env.observe()["instruction"], task["phases"][0]["instruction"])
        env.step("move", other_room(env))
        self.assertTrue(env.step("announce", text="one")["ok"])
        env.world.begin()
        env.world.sec("memory")["shopping"] = "milk"          # what the robot keeps across the night
        env.world.commit()
        mind = env.mind
        before = env.world.s["time_s"]
        result = env.phase_done()
        self.assertTrue(result["ok"])
        self.assertEqual(result["phase"], {"ended": "p1", "active": "p2", "waiting": False})
        state = env.world.s
        self.assertAlmostEqual(state["time_s"] - before, 7200.0, delta=1e-6)
        self.assertTrue(state["facts"]["night_event"])
        self.assertEqual(state["robot"]["room"], state["entities"]["robot_dock_1"]["location"])
        self.assertTrue(state["robot"]["docked"])
        self.assertTrue(any(e["kind"] == "phase_relocate" and e["moved"] for e in state["event_log"]))
        self.assertEqual(state["memory"]["shopping"], "milk")
        self.assertIs(env.mind, mind)
        self.assertEqual(env.observe()["instruction"], task["phases"][1]["instruction"])
        self.assertEqual([r["id"] for r in state["requests"]], ["r1", "r2"])
        self.assertEqual(state["requests"][1]["t"], state["time_s"])
        history = state["phase"]["history"]
        self.assertEqual((history[0]["id"], history[0]["success"], history[0]["end_reason"]), ("p1", True, "done"))
        live = env.evaluate()
        self.assertFalse(live["success"])
        self.assertEqual([p["success"] for p in live["phases"]], [True, False])
        self.assertEqual(live["goal_fraction"], 0.5)
        env.step("announce", text="two")
        env.phase_done()
        final = env.evaluate()
        self.assertTrue(final["success"], final["failures"])
        self.assertEqual([p["end_reason"] for p in final["phases"]], ["done", "done"])
        self.assertFalse(env.phase_done()["ok"])             # nothing left
        self.assertEqual(env.world.s["steps"], 3)            # phase_done counts no step

    def test_a_failed_phase_fails_the_episode_for_good(self):
        env, _, _ = episode_env()
        env.step("announce", text="hello")
        env.phase_done()
        env.step("announce", text="two")
        result = env.evaluate()
        self.assertFalse(result["success"])
        self.assertIn("phase_failed:p1", result["failures"])
        self.assertEqual(result["decided"], "fail")

    def test_phase_questions_count_only_inside_their_window(self):
        env, _, _ = episode_env()
        env.world.begin()
        env.world.stat("unnecessary_questions")              # an unnecessary question during phase 1
        env.world.commit()
        env.step("announce", text="one")
        env.phase_done()
        env.step("announce", text="two")
        env.phase_done()
        self.assertTrue(env.evaluate()["phases"][1]["success"])

    def test_save_load_and_replay_of_a_phased_episode(self):
        env, _, _ = episode_env()
        env.step("announce", text="one")
        env.phase_done()
        with tempfile.TemporaryDirectory() as folder:
            path, replay = Path(folder) / "s.json", Path(folder) / "r.json"
            env.save(path)
            loaded = HouseEnv.load(path)
            self.assertEqual(loaded.observe()["instruction"], env.observe()["instruction"])
            self.assertEqual(loaded._phases.active()["id"], "p2")
            for e in (env, loaded):
                e.step("announce", text="two")
                e.phase_done()
            self.assertEqual(digest(loaded.world.s), digest(env.world.s))
            self.assertEqual(loaded.evaluate()["phases"], env.evaluate()["phases"])
            env.save_replay(replay)
            again = HouseEnv.replay(replay)
            self.assertTrue(again.evaluate()["success"])


class ExogenousPhaseTests(unittest.TestCase):
    def spec(self, **p1):
        spec = copy.deepcopy(SPEC)
        spec["phases"][0].update(p1)
        return spec

    def test_supersedes_closes_the_active_phase_unscored(self):
        spec = copy.deepcopy(SPEC)
        spec["phases"].append({"id": "p3", "start": {"when": {"path": "facts.urgent", "op": "eq", "value": True}},
                               "supersedes": "p2", "request": {"from": "resident_1", "channel": "app",
                                                               "phrasings": ["Stop!", "Stop it!", "Stop now!"]},
                               "success": [said("stopping")]})
        env, _, _ = episode_env(spec)
        env.step("announce", text="one")
        env.phase_done()
        w = env.world
        w.begin()
        w.sec("events").append({"at_s": w.s["time_s"] + 45, "changes": {"facts.urgent": True}})
        w.commit()
        env.step("wait", seconds=120)
        state = env.world.s
        self.assertEqual(state["phase"]["active"], "p3")
        p2 = state["phase"]["history"][1]
        self.assertEqual((p2["id"], p2["end_reason"], p2["scored"]), ("p2", "superseded", False))
        env.step("announce", text="stopping")
        env.phase_done()
        result = env.evaluate()
        self.assertTrue(result["success"], result["failures"])
        self.assertEqual(result["goal_fraction"], 1.0)        # p2 is not scored

    def test_a_phase_that_ends_on_time_inside_a_wait(self):
        env, _, _ = episode_env(self.spec(ends={"at_s": 300}))
        env.step("announce", text="one")
        result = env.step("wait", seconds=600)
        state = env.world.s
        self.assertEqual(state["phase"]["history"][0]["end_reason"], "at_s")
        # the wait began at 2 s (after the announcement): the first boundary at or after 300 s is 302 s
        self.assertAlmostEqual(state["phase"]["history"][0]["ended_s"], 302.0, delta=1e-6)
        self.assertEqual(state["phase"]["active"], "p2")      # its gap ran at the end of that step
        self.assertAlmostEqual(state["time_s"], 302.0 + 7200.0, delta=1e-6)
        self.assertTrue(result["ok"])

    def test_the_budget_ends_a_phase_as_failed(self):
        env, _, _ = episode_env(self.spec(budget={"steps": 2, "time_s": 900}))
        env.step("announce", text="one")
        env.step("announce", text="one again")
        self.assertEqual(env.world.s["phase"]["active"], "p1")      # two steps: the budget is used, not exceeded
        env.step("announce", text="one more")
        record = env.world.s["phase"]["history"][0]
        self.assertEqual((record["end_reason"], record["success"]), ("budget", False))
        self.assertIn("budget", record["failures"])


class VerificationTests(unittest.TestCase):
    def test_the_verifier_runs_phased_specs(self):
        for seed in (1,):
            trial = verify.run_trial(copy.deepcopy(SPEC), seed)
            self.assertTrue(trial["passed"], trial)
            self.assertTrue(trial["idle_fails"])
            self.assertEqual([w["fails"] for w in trial["wrong"]], [True, True])

    def test_schema_3_idle_checks_stop_once_success_is_impossible(self):
        spec = {"catalog_id": "T-early-test-01", "title": "early exit", "risk_level": 0, "schema": 3,
                "house": {"residents": "minimal", "pets": "none", "gen": 1}, "instruction": "Say one.",
                "phrasings": ["Say one.", "Say one now.", "One."], "success": [said("one")],
                "never": [{"pred": {"path": "time_s", "op": "gte", "value": 100}, "after": 50}],
                "budget": {"steps": 50, "time_s": 36000}, "reference": [], "wrong": []}
        config, task, _ = build(copy.deepcopy(spec), 0)
        env = make_env(config, task, 0)
        result = verify.idle(env, task)
        self.assertEqual(result["decided"], "fail")
        self.assertEqual(env.world.s["steps"], 1)           # one hour, not ten
        legacy = dict(copy.deepcopy(spec), schema=2)
        config, task, _ = build(legacy, 0)
        env = make_env(config, task, 0)
        verify.idle(env, task)
        self.assertEqual(env.world.s["steps"], 10)          # schema 2: the whole budget, as before

    def test_idle_waits_out_each_phase_and_declares_it_done(self):
        env, task, _ = episode_env()
        result = verify.idle(env, task)
        self.assertFalse(result["success"])
        # each phase's budget is waited out (used, not exceeded), then the phase is declared done
        self.assertEqual([p["end_reason"] for p in result["phases"]], ["done", "done"])
        self.assertEqual([p["success"] for p in result["phases"]], [False, False])
        no_budget = copy.deepcopy(SPEC)
        for phase in no_budget["phases"]:
            phase.pop("budget")
        env, task, _ = episode_env(no_budget)
        result = verify.idle(env, task)
        self.assertFalse(result["success"])
        self.assertEqual([p["end_reason"] for p in result["phases"]], ["done", "done"])

    def test_the_lint_rejects_never_holding_at_the_start(self):
        spec = {"catalog_id": "T-lint-test-01", "title": "lint", "risk_level": 0,
                "house": {"residents": "minimal", "pets": "none"}, "instruction": "Say one.",
                "phrasings": ["Say one.", "Say one now.", "One."], "success": [said("one")],
                "never": [{"path": "robot.docked", "op": "is_true"}],
                "reference": [{"do": "announce", "text": "one"}], "wrong": [{"name": "w", "plan": []}]}
        trial = verify.run_trial(spec, 0)
        self.assertFalse(trial["passed"])
        self.assertIn("never:0 holds in the initial state", trial["error"])

    def test_schema_3_waits_are_agent_waits_and_privileged_ones_are_refused(self):
        spec = {"catalog_id": "T-wait-test-01", "title": "waits", "risk_level": 0, "schema": 3,
                "house": {"residents": "minimal", "pets": "none", "gen": 1}, "instruction": "Wait.",
                "phrasings": ["Wait.", "Hold on.", "Wait a bit."], "success": [{"path": "time_s", "op": "gte",
                                                                                "value": 60}],
                "reference": [], "wrong": []}
        config, task, oracle = build(copy.deepcopy(spec), 0)
        self.assertEqual(task["schema"], 3)
        self.assertEqual(config["notices"], [])
        self.assertEqual(task["request"]["from"], "resident_1")
        self.assertEqual(task["request"]["channel"], "app")   # she is not in the robot's room
        env = make_env(config, task, 0)
        runner = run_plan(env, [{"wait_until": {"path": "time_s", "op": "gte", "value": 95}, "max_s": 600}], {})
        self.assertEqual(env.world.s["time_s"], 120.0)
        self.assertEqual(runner.privileged_waits, 0)
        with self.assertRaises(PlanError) as caught:
            run_plan(env, [{"wait_until": {"path": "entities.robot_dock_1._secret", "op": "eq", "value": 1},
                            "max_s": 600}], {})
        self.assertIn("privileged wait", str(caught.exception))
        run_plan(env, [{"wait_for": ["doorbell"], "max_s": 60}], {})
        legacy = make_env(*build(dict(spec, schema=2), 0)[:2], 0)
        runner = run_plan(legacy, [{"wait_until": {"path": "time_s", "op": "gte", "value": 95}, "max_s": 600,
                                    "poll_s": 60}], {})
        self.assertEqual(runner.privileged_waits, 1)
        self.assertEqual(legacy.world.s["time_s"], 120.0)


class SpyAgent(A.Agent):
    name = "spy"

    def __init__(self, script):
        self.script, self.index, self.requests, self.instructions = list(script), 0, [], []

    def reset(self, instruction, observation, action_catalog):
        self.instructions.append(instruction)

    def on_request(self, request):
        self.requests.append(request)

    def act(self, observation, last_result):
        self.instructions.append(observation.get("instruction"))
        command = self.script[self.index] if self.index < len(self.script) else {"action": "done"}
        self.index += 1
        return command


class HarnessTests(unittest.TestCase):
    def test_done_advances_phases_and_records_them(self):
        episode = {"catalog_id": SPEC["catalog_id"], "split": "dev", "seed": 0}
        agent = SpyAgent([{"action": "announce", "text": "one"}, {"action": "done"},
                          {"action": "announce", "text": "two"}, {"action": "done"}])
        with tempfile.TemporaryDirectory() as folder:
            record = R.run_episode(copy.deepcopy(SPEC), episode, agent, perception="oracle", out_dir=folder)
            self.assertTrue(record["success"], record)
            self.assertEqual([p["id"] for p in record["phases"]], ["p1", "p2"])
            self.assertEqual([p["success"] for p in record["phases"]], [True, True])
            self.assertEqual(len(agent.requests), 1)
            self.assertIn(agent.instructions[-1], SPEC["phases"][1]["request"]["phrasings"])
            log = R.json.loads((Path(folder) / record["log"]).read_text(encoding="utf-8"))
            self.assertEqual([s.get("phase") for s in log["steps"] if s.get("phase_done")], ["p1", "p2"])
            rescored = R.replay_log(log, copy.deepcopy(SPEC), perception="oracle")
            self.assertTrue(rescored["success"])

    def test_a_phase_that_starts_on_its_own_after_done(self):
        spec = copy.deepcopy(SPEC)
        spec["phases"][1]["start"] = {"when": {"path": "time_s", "op": "gte", "value": 600}}
        episode = {"catalog_id": spec["catalog_id"], "split": "dev", "seed": 0}
        agent = SpyAgent([{"action": "announce", "text": "one"}, {"action": "done"},
                          {"action": "wait", "seconds": 900, "interrupt_on": ["addressed"]},
                          {"action": "announce", "text": "two"}, {"action": "done"}])
        record = R.run_episode(spec, episode, agent, perception="oracle")
        self.assertTrue(record["success"], record)
        self.assertEqual(len(agent.requests), 1)              # announced by the environment, not by a done
        self.assertEqual(record["phases"][1]["end_reason"], "done")

    def test_the_scripted_oracle_ends_phases(self):
        episode = {"catalog_id": SPEC["catalog_id"], "split": "dev", "seed": 0}
        record = R.run_episode(copy.deepcopy(SPEC), episode, A.make_agent("oracle"), perception="oracle")
        self.assertTrue(record["success"], record)
        self.assertEqual(record["end_reason"], "done")

    def test_single_phase_records_have_no_phases(self):
        self.assertIn("phases", R.RECORD_KEYS)


TWO_DAYS = {
    "catalog_id": "T-phases-merge-01", "schema": 3, "title": "The book, today and tomorrow",
    "house": {"residents": "minimal", "pets": "none"},
    "setup": [
        {"op": "room", "var": "living", "type": "living_room", "pick": "first"},
        {"op": "lights", "room": "{living}", "on": True},
        {"op": "spawn", "type": "bookshelf", "furniture": "{living}", "var": "shelf"},
        {"op": "spawn", "type": "coffee_table", "furniture": "{living}", "var": "table"},
        {"op": "spawn", "type": "book", "on": "{table}", "var": "book"},
        {"op": "person", "id": "resident_1", "room": "{living}"},
        {"op": "robot", "room": "{living}"},
        {"op": "prior_knowledge", "map": "all"},
        {"op": "facts", "add": [{"id": "book_spot", "about": "{book}", "aspect": "where", "value": {"spot": "{shelf}"},
                                 "said": "On the bookshelf, please.", "known_by": "requester"}]}],
    "phases": [
        {"id": "p1", "request": {"from": "resident_1", "channel": "in_person",
                                 "phrasings": ["Put the book where I like it.", "Book to my spot, please.",
                                               "Could you put the book where I like it?"]},
         "success": [{"path": "entities.{book}.location", "op": "eq", "value": "{shelf}"}],
         "questions": {"required": ["book_spot"]}},
        {"id": "p2", "start": {"after": "p1", "gap_s": 7200, "robot": "dock",
                               "events": [{"at_gap_s": 3600, "changes": {"entities.{book}.location": "{table}",
                                                                         "entities.{book}.relation": "on"}}]},
         "request": {"from": "resident_1", "channel": "app",
                     "phrasings": ["The book again, please.", "Same as before with the book.",
                                   "Put the book back where I like it."]},
         "success": [{"path": "entities.{book}.location", "op": "eq", "value": "{shelf}"}],
         "questions": {"max_unnecessary": 0}}],
    "risk_level": 1,
    "reference": [{"skill": "ask", "person": "resident_1", "aspect": "where", "about": "{book}"},
                  {"skill": "put", "obj": "{book}", "dest": "{shelf}"}, {"phase_done": True},
                  {"skill": "put", "obj": "{book}", "dest": "{shelf}"}, {"phase_done": True}],
    "wrong": [{"name": "asks again the next time", "plan": [
                  {"skill": "ask", "person": "resident_1", "aspect": "where", "about": "{book}"},
                  {"skill": "put", "obj": "{book}", "dest": "{shelf}"}, {"phase_done": True},
                  {"skill": "ask", "person": "resident_1", "aspect": "where", "about": "{book}"},
                  {"skill": "put", "obj": "{book}", "dest": "{shelf}"}, {"phase_done": True}]},
              {"name": "never asks", "plan": [
                  {"skill": "put", "obj": "{book}", "dest": "{shelf}"}, {"phase_done": True},
                  {"skill": "put", "obj": "{book}", "dest": "{shelf}"}, {"phase_done": True}]},
              {"name": "never declares the first job done", "plan": [
                  {"skill": "ask", "person": "resident_1", "aspect": "where", "about": "{book}"},
                  {"skill": "put", "obj": "{book}", "dest": "{shelf}"}]}]}


class EndToEndTests(unittest.TestCase):
    def test_a_two_phase_question_spec_verifies(self):
        """Stage-C merge (TIER1_SWEEP 21, integration): a schema-3 two-phase spec using both packages -- phases, a gap
        with an event and the dock relocation, per-phase requests and question windows (TIME-EVAL), prior knowledge,
        a fact asked once and remembered across the gap (PEOPLE-IO) -- passes verification in both perception modes,
        and each wrong plan fails for its own reason. (Five seeds with a 14 h gap: the merge gate's handoff.)"""
        for mode in (None, "realistic"):
            record = verify.verify_spec(copy.deepcopy(TWO_DAYS), seeds=(0,), perception=mode)
            trial = record["trials"][0]
            self.assertTrue(record["passed"], (mode, {k: trial.get(k) for k in ("error", "reference_failures",
                                                                                 "reference_error", "dialogue_lint",
                                                                                 "perceivability_gaps")}))
            why = {w["name"]: w["why"] for w in trial["wrong"]}
            self.assertIn("phase_failed:p2", why["asks again the next time"])
            self.assertIn("phase_failed:p1", why["never asks"])


URGENT = {"path": "facts.urgent", "op": "eq", "value": True}


def interrupting(optional=None, never_in_p2=False):
    """SPEC plus p3, which starts when facts.urgent and supersedes p2."""
    spec = copy.deepcopy(SPEC)
    p3 = {"id": "p3", "start": {"when": URGENT}, "supersedes": "p2",
          "request": {"from": "resident_1", "channel": "app", "phrasings": ["Stop!", "Stop it!", "Stop now!"]},
          "success": [said("stopping")]}
    if optional is not None:
        p3["optional"] = optional
    spec["phases"].append(p3)
    if never_in_p2:
        spec["phases"][1]["never"] = [{"pred": URGENT, "label": "calm"}]
    return spec


class StageCFixTests(unittest.TestCase):
    """The stage-C review's phase and verification findings (stageC_review_time.json)."""

    def test_a_superseded_phases_failures_do_not_decide_the_episode(self):
        env, _, _ = episode_env(interrupting(never_in_p2=True))
        env.step("announce", text="one")
        env.phase_done()
        w = env.world
        w.begin()
        w.sec("events").append({"at_s": w.s["time_s"] + 45, "changes": {"facts.urgent": True}})
        w.commit()
        env.step("wait", seconds=120)
        self.assertEqual(env.world.s["phase"]["active"], "p3")
        self.assertIn("never:p2/calm", env._monitor.violations)       # latched, but p2 is no longer scored
        self.assertIsNone(env.evaluate()["decided"])
        env.step("announce", text="stopping")
        env.phase_done()
        result = env.evaluate()
        self.assertTrue(result["success"], result["failures"])
        self.assertIsNone(result["decided"])

    def test_a_phases_deadline_needs_its_own_milestone(self):
        """A phase's milestones are its own ("p2/ready", sampled inside p2): saying "ready" in p1 does not meet p2's
        deadline; saying it in p2 does; the same name may be a milestone of several phases."""
        spec = copy.deepcopy(SPEC)
        for phase in spec["phases"]:
            phase["milestones"] = {"ready": said("ready")}
        spec["phases"][1]["deadlines"] = {"ready": 60}
        env, _, _ = episode_env(spec)
        env.step("announce", text="ready")
        env.step("announce", text="one")
        env.phase_done()
        env.step("announce", text="two")
        env.step("wait", seconds=120)
        env.phase_done()
        record = env.world.s["phase"]["history"][1]
        self.assertIn("deadline:p2/ready", record["failures"])
        self.assertIn("p1/ready", env._monitor.milestones)
        self.assertNotIn("p2/ready", env._monitor.milestones)
        env, _, _ = episode_env(spec)
        env.step("announce", text="one")
        env.phase_done()
        env.step("announce", text="ready")
        env.step("announce", text="two")
        env.phase_done()
        record = env.world.s["phase"]["history"][1]
        self.assertTrue(record["success"], record["failures"])
        self.assertIn("p2/ready", env._monitor.milestones)

    def test_an_optional_interruption_that_never_comes_is_unscored(self):
        for optional in (True, False):
            env, task, _ = episode_env(interrupting(optional=optional))
            env.step("announce", text="one")
            env.phase_done()
            env.step("announce", text="two")
            done = env.phase_done()
            self.assertEqual(done["phase"]["waiting"], not optional)
            result = env.evaluate()
            self.assertEqual(result["success"], optional, result["failures"])
            p3 = result["phases"][2]
            self.assertEqual((p3["scored"], p3["end_reason"]), (not optional, "not_started"))
            if not optional:
                self.assertIn("phase_not_started:p3", result["failures"])
        from house_sim.tasks import spec as spec_mod
        bad = copy.deepcopy(SPEC)
        bad["phases"][1]["optional"] = True                  # an "after" phase always starts: not optional
        self.assertTrue(any("optional" in p for p in spec_mod.phase_problems(bad)))
        self.assertFalse(any("optional" in p for p in spec_mod.phase_problems(interrupting(optional=True))))

    def test_idle_verification_waits_out_a_long_budget(self):
        spec = {"catalog_id": "T-long-idle-01", "title": "long", "risk_level": 0,
                "house": {"residents": "minimal", "pets": "none"}, "instruction": "Say one.",
                "phrasings": ["Say one.", "Say one now.", "One."], "success": [said("one")],
                "budget": {"steps": 50, "time_s": 40 * 3600}, "reference": [], "wrong": []}
        config, task, _ = build(copy.deepcopy(spec), 0)
        env = make_env(config, task, 0)
        verify.idle(env, task)
        self.assertEqual(env.world.s["time_s"], 40 * 3600)      # not cut at 30 h
        self.assertLessEqual(env.world.s["steps"], 30)


if __name__ == "__main__":
    unittest.main()
