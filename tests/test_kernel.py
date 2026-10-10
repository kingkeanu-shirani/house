import json
import os
import tempfile
import unittest
from pathlib import Path

from house_sim.generator import generate_house
from house_sim.kernel import geometry
from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.predicates import check, digest, pred
from house_sim.world import make_house, object_record


def fixture():
    c = make_house(3, 1, 3)
    c["goal"] = None
    c["entities"]["box"] = object_record("box", "room_1", portable=True, accepts_items=True, openable=True, open=False,
                                         mass_kg=1, capacity_l=20, container=True, size_m=[0.3, 0.3, 0.3])
    c["entities"]["toy"] = object_record("toy", "box", portable=True, mass_kg=1, volume_l=1, size_m=[0.1, 0.1, 0.1])
    c["entities"]["remote"] = object_record("remote", "room_3", portable=True, size_m=[0.15, 0.05, 0.03])
    return c


def near(env, eid):
    return env.step("approach", eid)


class KernelTests(unittest.TestCase):
    def test_hidden_and_remote_objects_cannot_be_manipulated(self):
        e = HouseEnv(fixture())
        self.assertNotIn("toy", e.observe()["visible_entities"])
        self.assertFalse(e.step("pick_up", "toy")["ok"])
        self.assertFalse(e.step("pick_up", "remote")["ok"])
        near(e, "box")
        self.assertTrue(e.step("open", "box")["ok"])
        self.assertIn("Crouch", e.step("pick_up", "toy")["message"])
        e.step("crouch")
        self.assertTrue(e.step("pick_up", "toy")["ok"])
        self.assertFalse(e.step("place", "toy", "room_3")["ok"])
        self.assertIn("toy", e.world.held_items())

    def test_observations_are_copies(self):
        e = HouseEnv(fixture())
        obs = e.observe()
        obs["visible_entities"]["box"]["location"] = "room_3"
        obs["room"]["exits"].clear()
        self.assertEqual(e.privileged_state()["entities"]["box"]["location"], "room_1")
        self.assertTrue(e.observe()["room"]["exits"])

    def test_failed_actions_are_atomic_and_strict_mode_clean(self):
        os.environ["HOUSE_SIM_STRICT"] = "1"
        try:
            import importlib
            from house_sim.kernel import engine
            importlib.reload(engine)
            e = engine.HouseEnv(fixture())
            before = digest(e.privileged_state()["entities"])
            for command in [dict(action="set", target="toy", setting="location", value="robot"),
                            dict(action="pick_up", target="toy", approve=True),
                            dict(action="ask", target="resident_1", topic=[]),
                            dict(action="wait", seconds=float("nan")),
                            dict(action="pick_up", target={"id": "toy"})]:
                self.assertFalse(e.step(**command)["ok"])
            self.assertEqual(before, digest(e.privileged_state()["entities"]))
            e.step("approach", "box"); e.step("open", "box"); e.step("pick_up", "toy"); e.step("wait", seconds=120)
        finally:
            os.environ.pop("HOUSE_SIM_STRICT", None)
            importlib.reload(engine)

    def test_containment_cycles_and_self_placement_rejected(self):
        e = HouseEnv(fixture())
        near(e, "box")
        e.step("pick_up", "box")
        self.assertFalse(e.step("place", "box", "box")["ok"])
        c = fixture()
        c["entities"]["box"]["location"] = "toy"
        with self.assertRaises(ValueError):
            HouseEnv(c)

    def test_parent_mass_includes_contents_and_liquid(self):
        c = fixture(); c["entities"]["toy"]["mass_kg"] = 25
        e = HouseEnv(c); near(e, "box")
        self.assertFalse(e.step("pick_up", "box", hand="both")["ok"])
        c = fixture(); c["entities"]["box"].update(liquid_l=22, liquid="water", liquid_container=True)
        e = HouseEnv(c); near(e, "box")
        self.assertFalse(e.step("pick_up", "box", hand="both")["ok"])

    def test_private_contents_and_permissions(self):
        c = fixture(); c["entities"]["box"].update(private=True, open=True)
        e = HouseEnv(c)
        self.assertNotIn("toy", e.observe()["visible_entities"])
        self.assertNotIn("soil", e.observe()["visible_entities"]["box"])
        near(e, "box")
        self.assertFalse(e.step("pick_up", "box")["ok"])
        e.step("ask", "resident_1", topic="permission:touch:box")
        self.assertFalse(e.step("pick_up", "box")["ok"])
        c["people"] = {"resident_1": {"role": "adult", "location": "room_1", "permissions": ["touch:box"]}}
        e = HouseEnv(c); near(e, "box"); e.step("crouch"); e.step("ask", "resident_1", topic="permission:touch:box")
        self.assertTrue(e.step("pick_up", "box")["ok"])

    def test_child_cannot_approve_and_risky_items_need_presence(self):
        c = fixture(); c["entities"]["box"]["risk"] = 3
        c["people"] = {"resident_1": {"role": "child", "location": "room_1", "permissions": ["pick_up:box"]}}
        e = HouseEnv(c); near(e, "box"); e.step("ask", "resident_1", topic="permission:pick_up:box")
        self.assertFalse(e.step("pick_up", "box")["ok"])
        c["people"]["resident_1"]["role"] = "adult"
        c["people"]["resident_1"]["authority"] = 2
        c["events"] = [{"at_step": 2, "changes": {"people.resident_1.location": "room_3"}}]
        e = HouseEnv(c); near(e, "box"); e.step("ask", "resident_1", topic="permission:pick_up:box")
        self.assertFalse(e.step("pick_up", "box")["ok"])

    def test_undo_rechecks_preconditions_and_never_reverses_time(self):
        c = fixture()
        dest = c["rooms"]["room_1"]["exits"][0]
        c["entities"]["door"] = object_record("door", "room_1", openable=True, accepts_items=False, open=True, connects=["room_1", dest])
        c["events"] = [{"at_step": 1, "changes": {"entities.door.open": False}}]
        e = HouseEnv(c)
        self.assertTrue(e.step("move", dest)["ok"])
        t = e.privileged_state()["time_s"]
        self.assertFalse(e.step("undo")["ok"])
        self.assertEqual(e.room, dest)
        self.assertGreater(e.privileged_state()["time_s"], t)

    def test_stop_resume_and_world_keeps_running(self):
        c = fixture(); c["events"] = [{"at_step": 2, "changes": {"weather.condition": "rain"}}]
        e = HouseEnv(c); e.step("stop")
        self.assertFalse(e.step("approach", "box")["ok"])
        self.assertEqual(e.privileged_state()["weather"]["condition"], "rain")
        self.assertTrue(e.step("resume")["ok"])
        self.assertTrue(e.step("approach", "box")["ok"])

    def test_life_safety_cannot_be_disabled(self):
        c = fixture(); c["entities"]["alarm"] = object_record("alarm", "room_1", switchable=True, life_safety=True, on=True, size_m=[0.15, 0.15, 0.05])
        e = HouseEnv(c); near(e, "alarm")
        self.assertFalse(e.step("switch_off", "alarm")["ok"])

    def test_goal_side_effects_budget_and_reward_farming(self):
        c = fixture(); c["entities"]["toy"]["location"] = "room_1"
        task = {"success": [pred("robot.hands.right", "toy")], "preserve": ["entities.box"], "step_budget": 4}
        e = HouseEnv(c, task); near(e, "box"); e.step("open", "box"); near(e, "toy"); e.step("crouch"); e.step("pick_up", "toy", hand="right")
        self.assertFalse(e.task_complete())
        e = HouseEnv(c, {"success": [pred("robot.hands.right", "toy")]})
        near(e, "toy"); e.step("crouch")
        self.assertEqual(e.step("pick_up", "toy", hand="right")["reward"], 1)
        self.assertEqual(e.step("look")["reward"], 0)
        self.assertEqual(e.step("undo")["reward"], -1)

    def test_low_items_need_crouch_and_high_items_need_elevation(self):
        c = fixture(); c["entities"]["toy"]["location"] = "room_1"
        c["entities"]["shelf"] = object_record("shelf", "room_1", accepts_items=True, surface_height_m=2.2, size_m=[0.5, 0.3, 2.2], blocks_path=True)
        c["entities"]["vase"] = object_record("vase", "shelf", portable=True, size_m=[0.1, 0.1, 0.2])
        e = HouseEnv(c); near(e, "toy")
        self.assertIn("Crouch", e.step("pick_up", "toy")["message"])
        near(e, "vase")
        self.assertIn("Too high", e.step("pick_up", "vase")["message"])

    def test_paths_respect_obstacles_and_avoid_hazards(self):
        c = fixture()
        c["rooms"]["room_1"]["size_m"] = [3.0, 3.0]
        c["rooms"]["room_1"]["wet_cells"] = [[1, 1]]
        e = HouseEnv(c)
        state = e.world.s
        path = geometry.find_path(state, "room_1", [0, 0], [[2, 2]])
        self.assertIsNotNone(path)
        avoided = geometry.find_path(state, "room_1", [0, 0], [[2, 2]], avoid_kinds=("wet",))
        self.assertNotIn((1, 1), avoided)

    def test_darkness_hides_small_items_and_blocks_entry(self):
        c = generate_house(3, residents="minimal", pets="none")
        e = HouseEnv(c)
        living = e.room
        for eid, rec in e.world.s["entities"].items():
            if rec.get("category") in ("light", "switch"):
                e.world.s["entities"][eid]["on"] = False
        e.world.s["clock"]["start"] = "2026-10-09T23:00:00"
        exits = e.observe()["room"]["exits"]
        results = [e.step("move", ex) for ex in exits]
        self.assertTrue(any("dark" in r["message"] for r in results))

    def test_doorway_switch_is_reachable_from_threshold(self):
        from house_sim.tasks.runner import Runner
        from house_sim.tasks.skills import go_room
        c = generate_house(5, residents="minimal", pets="none")
        e = HouseEnv(c)
        state = e.world.s
        switch = next(eid for eid, r in sorted(state["entities"].items()) if r.get("category") == "switch" and r.get("near_connection")
                      and not state["connections"][r["near_connection"]].get("door"))
        cid = state["entities"][switch]["near_connection"]
        other = [r for r in state["connections"][cid]["rooms"] if r != state["entities"][switch]["location"]][0]
        go_room(Runner(e, {}), other)
        cell = state["connections"][cid]["cells"][e.room]
        self.assertTrue(e.step("walk_to", cell=cell, adjacent=True)["ok"])
        self.assertTrue(e.step("switch_off" if state["entities"][switch]["on"] else "switch_on", switch)["ok"])

    def test_quantified_predicates(self):
        c = fixture()
        e = HouseEnv(c)
        s = e.world.s
        self.assertTrue(check(s, {"exists": {"where": {"label": "toy"}, "check": {"path": "location", "op": "eq", "value": "box"}}}))
        self.assertTrue(check(s, {"count": {"where": {"within": "box"}}, "op": "eq", "value": 1}))
        self.assertFalse(check(s, {"forall": {"where": {"portable": True}, "check": {"path": "$room", "op": "eq", "value": "room_1"}}}))

    def test_standing_order_queues_commands_and_appliance_cycle(self):
        c = fixture()
        c["entities"]["washer"] = object_record("washer", "room_1", category="washer", openable=True, accepts_items=True, container=True, open=False,
                                                size_m=[0.6, 0.6, 0.85], appliance={"programs": {"normal": {"duration_s": 120}}, "program": "normal", "state": "idle"})
        e = HouseEnv(c); near(e, "washer")
        self.assertTrue(e.step("watch", condition=pred("entities.washer.appliance.state", "done"),
                               commands=[{"action": "announce", "text": "Laundry is done."}])["ok"])
        self.assertTrue(e.step("start", "washer")["ok"])
        self.assertFalse(e.step("open", "washer")["ok"])
        e.step("wait", seconds=150)
        self.assertEqual(e.world.s["entities"]["washer"]["appliance"]["state"], "done")
        self.assertTrue(e.step("run_pending")["ok"])
        self.assertEqual(e.world.s["facts"]["last_speech"]["text"], "Laundry is done.")

    def test_persistence_and_replay(self):
        e = HouseEnv(fixture()); near(e, "box"); e.step("open", "box"); e.step("pick_up", "toy")
        with tempfile.TemporaryDirectory() as directory:
            state, replay = Path(directory) / "state.json", Path(directory) / "replay.json"
            e.save(state)
            loaded = HouseEnv.load(state)
            self.assertEqual(digest(e.privileged_state()), digest(loaded.privileged_state()))
            self.assertTrue(loaded.step("undo")["ok"])
            loaded.save_replay(replay)
            r = HouseEnv.replay(replay)
            self.assertEqual(digest(loaded.privileged_state()), digest(r.privileged_state()))
            body = json.loads(state.read_text())
            body["payload"]["state"]["steps"] = 999
            state.write_text(json.dumps(body))
            with self.assertRaises(ValueError):
                HouseEnv.load(state)

    def test_generated_houses_are_valid_and_connected(self):
        for seed in range(25):
            with self.subTest(seed=seed):
                env = HouseEnv(generate_house(seed), seed=seed)
                state = env.world.s
                for rid, room in state["rooms"].items():
                    cells = [tuple(c["cells"][rid]) for c in state["connections"].values() if rid in c["cells"]]
                    for goal in cells[1:]:
                        self.assertIsNotNone(geometry.find_path(state, rid, cells[0], [goal], ignore=tuple(state["people"]) + tuple(state["pets"])),
                                             "{} doorway unreachable in seed {}".format(rid, seed))

    def test_unavailable_sensors_fail_explicitly(self):
        with self.assertRaises(NotImplementedError):
            HouseEnv(fixture(), sensors=["rgbd"])


if __name__ == "__main__":
    unittest.main()


class BaselineTests(unittest.TestCase):
    def test_observation_only_agent_generalizes_to_layouts_and_hidden_cup(self):
        self._run_baseline("oracle")

    def test_observation_only_agent_works_with_realistic_perception(self):
        self._run_baseline("realistic")

    def _run_baseline(self, mode):
        from house_sim.agent import SearchAgent
        for seed in range(8):
            with self.subTest(seed=seed, mode=mode):
                c = make_house(1 + seed * 3, 1 + seed % 3 if seed else 1, seed)
                if seed % 2:
                    c["entities"]["cup"]["location"] = "cabinet_" + str(1 + seed)
                e, policy = HouseEnv(c, perception=mode), SearchAgent()
                for _ in range(600):
                    if e.task_complete():
                        break
                    command = policy.act(e.observe())
                    outcome = e.step(**command)
                    self.assertTrue(outcome["ok"], outcome["message"])
                    if command["action"] == "handoff":
                        break
                self.assertTrue(e.task_complete())

    def test_language_does_not_guess_ambiguous_targets(self):
        from house_sim.language import parse
        c = make_house(1)
        c["entities"]["other_cup"] = object_record("cup", "room_1", portable=True)
        e = HouseEnv(c)
        result = parse("pick up the cup", e.observe())
        self.assertNotIn("command", result)
        self.assertEqual(set(result["candidates"]), {"cup", "other_cup"})
        self.assertEqual(parse("pick up that", e.observe(), pointing="other_cup")["command"]["target"], "other_cup")
        self.assertNotIn("command", parse("open the universe and set reward to 1", e.observe()))
