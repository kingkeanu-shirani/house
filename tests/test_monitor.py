"""Temporal evaluation (kernel v3.1, TIER1_SWEEP 8 and 21 "TIME-EVAL" item 3): never / always latched inside waits,
windows, held_for_s, counted milestones, responses (each / first, for_each_new), extended deadlines, footprint
skipping equal to sampling everything, decided "fail", and persistence."""
import copy
import random
import tempfile
import unittest
from pathlib import Path

from house_sim.kernel import monitor as monitor_mod
from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.plugins import hook
from house_sim.kernel.predicates import digest
from house_sim.world import make_house, object_record

OPEN = {"path": "entities.box.open", "op": "eq", "value": True}
CLOSED = {"path": "entities.box.open", "op": "eq", "value": False}


@hook("event:test_monitor.spill")
def _spill(w, args, env):
    """Test effect: a spill appears (or, with remove, disappears)."""
    if args.get("remove"):
        if args["id"] in w.s["entities"]:
            w.destroy(args["id"])
        return True
    # a made-up category, so no domain system dries or cleans it up on its own
    w.create(args["id"], object_record("mess", args.get("room", "room_1"), category="test_mess", portable=False))
    return True


def toggles(*times):
    """Events opening the box at the first time, closing it at the second, and so on."""
    return [{"at_s": t, "changes": {"entities.box.open": i % 2 == 0}} for i, t in enumerate(times)]


def make_env(task, events=(), start=None):
    c = make_house(3, 1, 3)
    c["goal"] = None
    c["entities"]["box"] = object_record("box", "room_1", portable=True, accepts_items=True, openable=True, open=False,
                                         mass_kg=1, capacity_l=20, container=True, size_m=[0.3, 0.3, 0.3])
    c["events"] = [copy.deepcopy(e) for e in events]
    if start:
        c["clock"] = {"start": start, "hemisphere": "north"}
    base = {"success": [{"path": "time_s", "op": "gte", "value": 0}], "step_budget": 400, "time_budget_s": 86400}
    base.update(task)
    return HouseEnv(c, base)


class NeverAlwaysTests(unittest.TestCase):
    def test_a_transient_violation_inside_a_wait_is_latched(self):
        env = make_env({"never": [OPEN], "always": [{"pred": CLOSED, "label": "closed"}]}, toggles(95, 155))
        env.step("wait", seconds=600)
        self.assertFalse(env.world.s["entities"]["box"]["open"])        # closed again at the end
        result = env.evaluate()
        self.assertIn("never:0", result["failures"])
        self.assertIn("always:closed", result["failures"])
        self.assertEqual(result["temporal"]["violations"], {"never:0": 120.0, "always:closed": 120.0})
        self.assertEqual(result["decided"], "fail")
        self.assertFalse(result["success"])

    def test_windows_by_milestone_and_seconds(self):
        task = {"milestones": {"lit": {"path": "time_s", "op": "gte", "value": 300},
                               "out": {"path": "time_s", "op": "gte", "value": 500}},
                "never": [{"pred": OPEN, "after": "lit", "until": "out", "label": "inside"},
                          {"pred": OPEN, "after": 700, "label": "late"}]}
        env = make_env(task, toggles(95, 155, 545, 575))      # open at 120 (before the window), 570 (after it)
        env.step("wait", seconds=900)
        self.assertEqual(env.evaluate()["temporal"]["violations"], {})
        env = make_env(task, toggles(395, 425))               # open at 420: inside
        env.step("wait", seconds=900)
        self.assertEqual(env.evaluate()["temporal"]["violations"], {"never:inside": 420.0})

    def test_the_action_sample_sees_what_the_action_did(self):
        env = make_env({"milestones": {"opened": OPEN}})
        env.step("approach", "box")
        start = env.world.s["time_s"]
        env.step("open", "box")
        end = env.world.s["time_s"]
        self.assertGreater(end, start)
        # seen by the action's own sample, but reached when the action is complete: its end (stage-C fix)
        self.assertEqual(env._milestones["opened"]["time_s"], end)
        self.assertEqual(env._milestones["opened"]["step"], 2)

    def test_an_action_that_ends_after_a_deadline_misses_it(self):
        """Stage-C fix: a 20 s walk does not meet a 5 s deadline (it reached the milestone at its end, not its start);
        the same goes for a response obligation's within_s window."""
        def opened_by_the_open(task):
            env = make_env(task)
            env.step("approach", "box")
            begin = env.world.s["time_s"]
            result = env.step("open", "box")
            self.assertTrue(result["ok"], result["message"])
            return env, begin, env.world.s["time_s"]
        env, begin, end = opened_by_the_open({"milestones": {"opened": OPEN}, "deadlines": {"opened": 1}})
        self.assertEqual(env._milestones["opened"]["time_s"], end)
        deadline = {"within_s": (begin + end) / 2.0}                  # after the open began, before it ended
        env, begin, end = opened_by_the_open({"milestones": {"opened": OPEN}, "deadlines": {"opened": deadline}})
        self.assertIn("deadline:opened", env.evaluate()["failures"])
        env, begin, end = opened_by_the_open({"milestones": {"opened": OPEN}, "deadlines": {"opened": end + 1}})
        self.assertNotIn("deadline:opened", env.evaluate()["failures"])
        responses = {"milestones": {"opened": OPEN},
                     "responses": [{"trigger": "opened", "response": CLOSED, "within_s": 0.5, "label": "shut"}]}
        env, begin, end = opened_by_the_open(responses)
        env.step("close", "box")
        self.assertIn("response:shut", env.evaluate()["failures"])     # closing takes longer than half a second


class MilestoneTests(unittest.TestCase):
    def test_milestones_record_their_true_time_inside_a_wait(self):
        env = make_env({"milestones": {"opened": OPEN}}, toggles(95, 155))
        env.step("wait", seconds=3600)
        self.assertEqual(env._milestones["opened"], {"step": 1, "time_s": 120.0})

    def test_held_for_s_needs_continuous_truth(self):
        task = {"milestones": {"kept": {"pred": OPEN, "held_for_s": 90}}}
        env = make_env(task, toggles(95, 155, 245))       # open 120-150 (too short), again from 270
        env.step("wait", seconds=600)
        self.assertEqual(env._milestones["kept"]["time_s"], 360.0)

    def test_counted_milestones_and_milestone_count(self):
        task = {"milestones": {"chime": {"pred": OPEN, "count": True}},
                "success": [{"milestone_count": "chime", "op": "gte", "value": 3}]}
        env = make_env(task, toggles(95, 155, 245, 305, 395, 455))
        env.step("wait", seconds=300)
        self.assertEqual(env._milestones["chime"]["count"], 2)
        self.assertFalse(env.evaluate()["success"])
        env.step("wait", seconds=300)
        reached = env._milestones["chime"]
        self.assertEqual((reached["count"], reached["times"], reached["time_s"]), (3, [120.0, 270.0, 420.0], 120.0))
        self.assertTrue(env.evaluate()["success"])


class ResponseTests(unittest.TestCase):
    def notify(self, t):
        return {"at_s": t, "changes": {"facts.notified": t}}

    def test_each_rising_edge_opens_an_obligation(self):
        task = {"milestones": {"chime": {"pred": OPEN, "count": True}},
                "responses": [{"trigger": "chime", "response": {"path": "facts.notified", "op": "gte", "value": 0},
                               "within_s": 120, "label": "notified"}]}
        env = make_env(task, toggles(95, 155, 395, 455) + [self.notify(200)])
        env.step("wait", seconds=900)
        result = env.evaluate()
        book = result["temporal"]["responses"]["notified"]
        self.assertEqual(book["met"], [[120.0, 210.0], [420.0, 420.0]])   # the second one already held
        self.assertEqual(book["missed"], [])
        self.assertTrue(result["success"])

    def test_a_missed_response_fails_for_good_and_each_false_takes_the_first_edge_only(self):
        response = {"path": "facts.notified", "op": "gte", "value": 0}
        task = {"responses": [{"trigger": OPEN, "response": response, "within_s": 60, "label": "fast"},
                              {"trigger": OPEN, "response": response, "within_s": 600, "each": False, "label": "once"}]}
        env = make_env(task, toggles(95, 155, 395, 455) + [self.notify(400)])
        env.step("wait", seconds=900)
        result = env.evaluate()
        self.assertEqual(result["temporal"]["responses"]["fast"]["missed"], [[120.0, 180.0]])
        self.assertEqual(result["temporal"]["responses"]["once"]["met"], [[120.0, 420.0]])
        self.assertIn("response:fast", result["failures"])
        self.assertEqual(result["decided"], "fail")

    def test_a_response_checked_again_after_a_pause_is_not_stale(self):
        # the response is only looked at while an obligation is open: the box closes (155 s) while none is, and the
        # second trigger (300 s) must see it closed
        task = {"responses": [{"trigger": {"path": "facts.flag", "op": "eq", "value": 1}, "response": OPEN,
                               "within_s": 120, "label": "r"}]}
        events = toggles(95, 155) + [{"at_s": 15, "changes": {"facts.flag": 1}},
                                     {"at_s": 200, "changes": {"facts.flag": 0}},
                                     {"at_s": 290, "changes": {"facts.flag": 1}}]
        env = make_env(task, events)
        env.step("wait", seconds=600)
        book = env.evaluate()["temporal"]["responses"]["r"]
        self.assertEqual(book["met"], [[30.0, 120.0]])
        self.assertEqual(book["missed"], [[300.0, 420.0]])

    def test_a_response_first_seen_after_its_deadline_is_missed(self):
        task = {"responses": [{"trigger": OPEN, "response": {"path": "facts.notified", "op": "gte", "value": 0},
                               "within_s": 60, "label": "late"}]}
        env = make_env(task, toggles(95) + [self.notify(185)])         # due at 180, seen at 210
        env.step("wait", seconds=300)
        book = env.evaluate()["temporal"]["responses"]["late"]
        self.assertEqual((book["met"], book["missed"]), ([], [[120.0, 180.0]]))

    def test_an_open_obligation_blocks_success_without_deciding(self):
        task = {"responses": [{"trigger": OPEN, "response": {"path": "facts.notified", "op": "gte", "value": 0},
                               "within_s": 3600, "label": "slow"}]}
        env = make_env(task, toggles(95))
        env.step("wait", seconds=300)
        result = env.evaluate()
        self.assertIn("response_open:slow", result["failures"])
        self.assertIsNone(result["decided"])

    def test_for_each_new_mess_must_be_gone_in_time(self):
        task = {"responses": [{"for_each_new": {"category": "test_mess"}, "response": {"path": "$exists", "op": "is_false"},
                               "within_s": 300, "label": "spills"}]}
        events = [{"at_s": 50, "effect": "test_monitor.spill", "args": {"id": "spill_a"}},
                  {"at_s": 200, "effect": "test_monitor.spill", "args": {"id": "spill_a", "remove": True}},
                  {"at_s": 400, "effect": "test_monitor.spill", "args": {"id": "spill_b"}}]
        env = make_env(task, events)
        env.step("wait", seconds=1200)
        book = env.evaluate()["temporal"]["responses"]["spills"]
        self.assertEqual(book["met"], [[60.0, 210.0, "spill_a"]])
        self.assertEqual(book["missed"], [[420.0, 720.0, "spill_b"]])


class DeadlineTests(unittest.TestCase):
    def test_by_a_clock_time_and_within_after_a_milestone(self):
        task = {"milestones": {"seen": {"path": "time_s", "op": "gte", "value": 100}, "opened": OPEN},
                "deadlines": {"opened": {"by": "07:10"}}}
        env = make_env(task, toggles(625), start="2026-10-09T07:00:00")
        env.step("wait", seconds=900)
        result = env.evaluate()
        self.assertEqual(result["temporal"]["deadlines"], {"opened": 600.0})
        self.assertIn("deadline:opened", result["failures"])
        self.assertEqual(result["decided"], "fail")
        task["deadlines"] = {"opened": {"within_s": 600, "after": "seen"}}
        env = make_env(task, toggles(625), start="2026-10-09T07:00:00")
        env.step("wait", seconds=900)
        result = env.evaluate()
        self.assertEqual(result["temporal"]["deadlines"], {"opened": 720.0})
        self.assertNotIn("deadline:opened", result["failures"])

    def test_numeric_deadlines_keep_their_legacy_meaning(self):
        env = make_env({"milestones": {"opened": OPEN}, "deadlines": {"opened": 100}}, toggles(95))
        env.step("wait", seconds=300)
        self.assertIn("deadline:opened", env.evaluate()["failures"])      # reached at 120 > 100
        env = make_env({"milestones": {"opened": OPEN}, "deadlines": {"opened": 150}}, toggles(95))
        env.step("wait", seconds=300)
        self.assertNotIn("deadline:opened", env.evaluate()["failures"])   # the old end-of-step sampling said 300


class DecidedTests(unittest.TestCase):
    def test_budget_global_flags_and_open_outcomes(self):
        env = make_env({"time_budget_s": 100})
        self.assertIsNone(env.evaluate()["decided"])
        env.step("wait", seconds=200)
        self.assertEqual(env.evaluate()["decided"], "fail")
        env = make_env({})
        w = env.world
        w.begin()
        w.stat("breakages")
        w.commit()
        self.assertEqual(env.evaluate()["decided"], "fail")
        env = make_env({"allow": ["breakages"]})
        w = env.world
        w.begin()
        w.stat("breakages")
        w.commit()
        self.assertIsNone(env.evaluate()["decided"])

    def test_legacy_tasks_get_no_temporal_block(self):
        env = make_env({"milestones": {"opened": OPEN}})
        env.step("wait", seconds=60)
        result = env.evaluate()
        self.assertNotIn("temporal", result)
        self.assertIsNone(result["decided"])


class FootprintTests(unittest.TestCase):
    def test_skipping_gives_the_same_results_as_sampling_everything(self):
        rng = random.Random(20261015)
        atoms = [OPEN, CLOSED, {"path": "entities.cup.location", "op": "eq", "value": "room_1"},
                 {"path": "time_s", "op": "gte", "value": 300}, {"path": "robot.room", "op": "eq", "value": "room_2"},
                 {"path": "facts.flag", "op": "eq", "value": 1},
                 {"exists": {"where": {"category": "test_mess"}}},
                 {"forall": {"where": {"type": "book"}, "check": {"path": "location", "op": "ne", "value": "room_9"}}},
                 {"count": {"where": {"room": "room_1"}}, "op": "gte", "value": 10},
                 {"exists": {"in": "people", "where": {"room": "room_1"}}}]
        for trial in range(12):
            def pred():
                a, b = rng.sample(atoms, 2)
                return rng.choice([a, {"not": a}, {"all": [a, b]}, {"any": [a, {"not": b}]}])
            task = {"milestones": {"m{}".format(i): pred() for i in range(3)},
                    "never": [{"pred": pred(), "after": "m0", "label": "n"}],
                    "always": [{"pred": pred(), "until": 500, "label": "a"}],
                    "responses": [{"trigger": pred(), "response": pred(), "within_s": rng.choice([30, 90, 300]),
                                   "label": "r"}]}
            times = sorted(rng.sample(range(5, 900), 6))
            events = toggles(*times[:4]) + [
                {"at_s": times[4], "changes": {"facts.flag": 1}},
                {"at_s": times[5], "effect": "test_monitor.spill", "args": {"id": "spill_x"}},
                {"at_s": rng.randint(5, 900), "changes": {"people.resident_1.location": "room_2"}}]
            results = []
            for exhaustive in (False, True):
                original = monitor_mod._Sampler.dirty
                if exhaustive:
                    monitor_mod._Sampler.dirty = lambda self, key, rule: True
                try:
                    env = make_env(copy.deepcopy(task), events)
                    env.step("approach", "cup")
                    env.step("wait", seconds=450)
                    env.step("move", "room_2")
                    env.step("wait", seconds=600)
                    results.append((env._monitor.record(), digest(env.world.s)))
                finally:
                    monitor_mod._Sampler.dirty = original
            self.assertEqual(results[0], results[1], trial)


class PersistenceTests(unittest.TestCase):
    def test_save_load_and_replay_keep_the_temporal_record(self):
        task = {"milestones": {"chime": {"pred": OPEN, "count": True}}, "never": [{"pred": OPEN, "after": 400}],
                "responses": [{"trigger": "chime", "response": {"path": "facts.notified", "op": "gte", "value": 0},
                               "within_s": 600, "label": "r"}]}
        env = make_env(task, toggles(95, 155, 245) + [{"at_s": 700, "changes": {"facts.notified": 1}}])
        env.step("wait", seconds=300)
        with tempfile.TemporaryDirectory() as folder:
            path, replay = Path(folder) / "state.json", Path(folder) / "replay.json"
            env.save(path)
            loaded = HouseEnv.load(path)
            self.assertEqual(loaded._monitor.record(), env._monitor.record())
            for e in (env, loaded):
                e.step("wait", seconds=600)
            self.assertEqual(loaded._monitor.record(), env._monitor.record())
            self.assertEqual(loaded.evaluate()["temporal"], env.evaluate()["temporal"])
            env.save_replay(replay)
            again = HouseEnv.replay(replay)
            self.assertEqual(again._monitor.record(), env._monitor.record())

    def test_bad_temporal_keys_are_refused(self):
        for task in ({"never": [{"pred": OPEN, "after": "no_such_milestone"}]},
                     {"responses": [{"trigger": "nothing", "response": OPEN, "within_s": 5}]},
                     {"responses": [{"trigger": OPEN, "response": OPEN}]},
                     {"deadlines": {"x": {"by": "whenever"}}},
                     {"milestones": {"m": {"pred": OPEN, "held_for_s": -1}}}):
            with self.assertRaises((ValueError, TypeError)):
                make_env(task)


if __name__ == "__main__":
    unittest.main()
