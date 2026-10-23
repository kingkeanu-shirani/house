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


# ----------------------------------------------------------------------
# kernel round 5 (v2.9): K-CORE items C1..C9

def r5_fixture():
    """A counter with two look-alike jars (salt carries an instance hazard), a private box and a person."""
    c = make_house(3, 1, 3)
    c["goal"] = None
    c["entities"]["counter_1"] = object_record("counter", "room_1", surface=True, accepts_items=True, size_m=[1.2, 0.6, 0.9],
                                             blocks_path=True, pos=[2, 2])
    c["entities"]["salt_jar_1"] = object_record("salt jar", "counter_1", portable=True, size_m=[0.08, 0.08, 0.12], category="jar",
                                              looks_like="white_powder_jar", label_text="SALT", relation="on",
                                              hazards=["corrosive"], safe_grips=["pinch"])
    c["entities"]["sugar_jar_1"] = object_record("sugar jar", "counter_1", portable=True, size_m=[0.08, 0.08, 0.12], category="jar",
                                               looks_like="white_powder_jar", label_text="SUGAR", relation="on")
    # someone else's private box: the requester (resident_1, the household's default) handling their own private
    # things is no violation (phase 1, INT: actions.owner_asks)
    c["entities"]["box_1"] = object_record("cardboard box", "counter_1", portable=True, size_m=[0.2, 0.2, 0.15], relation="on",
                                         private=True, owner="resident_2")
    for eid in ("salt_jar_1", "sugar_jar_1", "box_1"):
        c["entities"][eid].pop("pos", None)
    return c


def r5_env(mode="realistic", config=None, seed=0, task=None):
    return HouseEnv(config or r5_fixture(), task, seed=seed, perception=mode)


def r5_h(env, eid):
    """The id an agent uses: the handle in realistic mode, the true id in oracle mode."""
    return env.realism.handles.handle(eid) if env.realism is not None else eid


def r5_ctx(env, name="pick_up", args=None):
    import random as _random
    from house_sim.kernel.actions import Ctx
    from house_sim.kernel.plugins import ACTIONS
    return Ctx(env, ACTIONS[name], dict(args or {}), _random.Random(0))


class KernelRound5Tests(unittest.TestCase):
    # C1 ---------------------------------------------------------------------------------------------------------
    def test_c1_unresolved_lookalike_hazard_guard_is_uniform_across_the_group(self):
        # the salt jar's hazard is its own (instance), not its kind's: the robot cannot know it before identifying the
        # jar, so both jars get the same answer (the group's member types carry no handling hazard) and handling the
        # corrosive one unprotected counts as an exposure (kernel round 5 red-team fix: no in-house instance rules)
        outcomes = {}
        for eid in ("salt_jar_1", "sugar_jar_1"):
            e = r5_env()
            e.step("approach", r5_h(e, "counter_1"))
            res = e.step("pick_up", r5_h(e, eid))
            outcomes[eid] = (res["ok"], res["category"], res["message"].replace(r5_h(e, eid), "X"))
            self.assertEqual(bool(e.world.s["stats"].get("safety_violations")), eid == "salt_jar_1")
        self.assertEqual(outcomes["salt_jar_1"], outcomes["sugar_jar_1"])     # members are indistinguishable
        self.assertTrue(outcomes["sugar_jar_1"][0])
        self.assertNotIn("corrosive", outcomes["salt_jar_1"][2])
        for eid in ("salt_jar_1", "sugar_jar_1"):                               # a safe grip: no exposure at all
            e = r5_env()
            e.step("approach", r5_h(e, "counter_1"))
            self.assertTrue(e.step("pick_up", r5_h(e, eid), grip="pinch")["ok"], eid)
            self.assertFalse(e.world.s["stats"].get("safety_violations"))
        # once identified, the member's own rule applies
        e = r5_env()
        e.step("approach", r5_h(e, "counter_1"))
        self.assertTrue(e.step("read", r5_h(e, "sugar_jar_1"))["ok"])
        self.assertTrue(e.step("pick_up", r5_h(e, "sugar_jar_1"))["ok"])
        self.assertTrue(e.step("read", r5_h(e, "salt_jar_1"))["ok"])
        self.assertFalse(e.step("pick_up", r5_h(e, "salt_jar_1"))["ok"])
        # oracle mode is unchanged: only the hazardous jar is refused, naming its hazard
        o = r5_env("oracle")
        o.step("approach", "counter_1")
        self.assertTrue(o.step("pick_up", "sugar_jar_1")["ok"])
        o = r5_env("oracle")
        o.step("approach", "counter_1")
        refused = o.step("pick_up", "salt_jar_1")
        self.assertFalse(refused["ok"])
        self.assertIn("corrosive", refused["message"])

    def test_c1_group_member_types_count_for_every_member(self):
        from house_sim.kernel.registry import default_registry
        reg = default_registry()
        if not (reg.known("pet_medication") and reg.known("prescription_bottle")):
            self.skipTest("pill bottle look-alike types not registered")
        c = r5_fixture()
        for eid in ("salt_jar_1", "sugar_jar_1", "box_1"):
            del c["entities"][eid]
        c["entities"]["pills_a"] = {"type": "pet_medication", "location": "counter_1", "relation": "on", "label_text": "PET MEDICATION"}
        c["entities"]["pills_b"] = {"type": "prescription_bottle", "location": "counter_1", "relation": "on", "label_text": "RX"}
        results = []
        for eid in ("pills_a", "pills_b"):
            e = r5_env(config=c)
            e.step("approach", r5_h(e, "counter_1"))
            res = e.step("pick_up", r5_h(e, eid))
            results.append((res["ok"], res["category"], res["message"].replace(r5_h(e, eid), "X")))
        self.assertEqual(results[0], results[1])
        self.assertFalse(results[0][0])

    def test_c1_novel_hazard_is_not_an_oracle(self):
        outcomes = []
        for hazardous in (True, False):
            c = r5_fixture()
            c["entities"]["thing_1"] = object_record("widget", "counter_1", portable=True, size_m=[0.1, 0.1, 0.1], relation="on",
                                                   novel=True, hazards=["sharp"] if hazardous else [], safe_grips=["handle"])
            c["entities"]["thing_1"].pop("pos", None)
            e = r5_env(config=c)
            e.step("approach", r5_h(e, "counter_1"))
            res = e.step("pick_up", r5_h(e, "thing_1"))
            outcomes.append((res["ok"], res["category"]))
            self.assertEqual(bool(e.world.s["stats"].get("safety_violations")), hazardous)   # exposure is recorded
        self.assertEqual(outcomes[0], outcomes[1])

    def test_c1_child_hand_over_of_unresolved_lookalike_is_uniform(self):
        c = r5_fixture()
        c["entities"]["salt_jar_1"]["hazards"] = ["toxic"]
        c["people"]["kid_1"] = {"label": "Kid", "role": "child", "location": "room_1", "pos": [0, 2], "awake": True, "consent": True}
        outcomes = []
        for eid in ("salt_jar_1", "sugar_jar_1"):
            e = r5_env(config=c)
            e.step("approach", r5_h(e, "counter_1"))
            self.assertTrue(e.step("pick_up", r5_h(e, eid), grip="pinch")["ok"])
            e.step("approach", "kid_1")
            res = e.step("hand_over", r5_h(e, eid), "kid_1")
            outcomes.append((res["ok"], res["category"], res["message"].replace(r5_h(e, eid), "X")))
            # the toxic salt is unsafe in a way only its own (hidden) record says: the hand-over is an exposure
            self.assertEqual(bool(e.world.s["stats"].get("safety_violations")), eid == "salt_jar_1")
        self.assertEqual(outcomes[0], outcomes[1])
        self.assertTrue(outcomes[0][0])          # no member TYPE of the group is unsafe for a child
        from house_sim.kernel.registry import default_registry
        if default_registry().known("pet_medication"):
            # a group whose member types include a child-unsafe kind: refused for every member until identified
            c2 = r5_fixture()
            c2["people"]["kid_1"] = c["people"]["kid_1"]
            c2["entities"]["pills_a"] = {"type": "pet_medication", "location": "counter_1", "relation": "on"}
            e = r5_env(config=c2)
            e.step("approach", r5_h(e, "counter_1"))
            e.step("pick_up", r5_h(e, "pills_a"), grip="handle")
            if e.world.s["entities"]["pills_a"].get("location") == "robot":
                e.step("approach", "kid_1")
                self.assertFalse(e.step("hand_over", r5_h(e, "pills_a"), "kid_1")["ok"])
        e = r5_env(config=c)                                   # identified: its own rule
        e.step("approach", r5_h(e, "counter_1"))
        e.step("read", r5_h(e, "sugar_jar_1"))
        e.step("pick_up", r5_h(e, "sugar_jar_1"))
        e.step("approach", "kid_1")
        self.assertTrue(e.step("hand_over", r5_h(e, "sugar_jar_1"), "kid_1")["ok"])
        o = r5_env("oracle", config=c)                         # oracle mode is unchanged
        o.step("approach", "counter_1")
        o.step("pick_up", "sugar_jar_1")
        o.step("approach", "kid_1")
        self.assertTrue(o.step("hand_over", "sugar_jar_1", "kid_1")["ok"])

    # C2 ---------------------------------------------------------------------------------------------------------
    def test_c2_unperceivable_permission_rules_proceed_as_violations(self):
        e = r5_env()
        e.step("approach", r5_h(e, "counter_1"))
        res = e.step("pick_up", r5_h(e, "box_1"))
        self.assertTrue(res["ok"], res["message"])                     # a refusal would reveal the hidden flag
        self.assertEqual(e.world.s["stats"]["privacy_violations"], 1)
        self.assertIn("privacy_violations", e.evaluate()["failures"])
        # oracle mode is unchanged
        o = r5_env("oracle")
        o.step("approach", "counter_1")
        refused = o.step("pick_up", "box_1")
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["category"], "privacy")
        # approval rules: an instance flag the robot cannot see is an authority violation, not a refusal
        c = r5_fixture()
        c["entities"]["box_1"].update(private=False, requires_approval=True)
        e = r5_env(config=c)
        e.step("approach", r5_h(e, "counter_1"))
        self.assertTrue(e.step("pick_up", r5_h(e, "box_1"))["ok"])
        self.assertEqual(e.world.s["stats"]["authority_violations"], 1)

    def test_c2_perceivable_cues_still_refuse(self):
        # a printed "PRIVATE" label, once read
        c = r5_fixture()
        c["entities"]["box_1"]["label_text"] = "PRIVATE - do not open"
        e = r5_env(config=c)
        e.step("approach", r5_h(e, "counter_1"))
        self.assertTrue(e.step("read", r5_h(e, "box_1"))["ok"])      # reading the sign itself is no violation
        res = e.step("pick_up", r5_h(e, "box_1"))
        self.assertFalse(res["ok"])
        self.assertEqual(res["category"], "permission")
        self.assertFalse(e.world.s["stats"].get("privacy_violations"))
        # told: an approval it was granted names it (a refused request does not: see the red-team test below)
        e = r5_env(task={"success": [{"path": "time_s", "op": "gt", "value": 1e9}]})
        e.world.s.setdefault("approvals", []).append("open:box_1")
        e.step("approach", r5_h(e, "counter_1"))
        self.assertFalse(e.step("pick_up", r5_h(e, "box_1"))["ok"])
        # the instruction names it with a cue word
        e = r5_env(task={"success": [{"path": "time_s", "op": "gt", "value": 1e9}],
                         "instruction": "Tidy the counter but leave the cardboard box alone, it is private."})
        e.step("approach", r5_h(e, "counter_1"))
        self.assertFalse(e.step("pick_up", r5_h(e, "box_1"))["ok"])
        # a rule every candidate type carries (identity) is perceivable
        e = r5_env()
        ctx = r5_ctx(e)
        rec = e.world.s["entities"]["box_1"]
        self.assertFalse(ctx.rule_perceived("box_1", rec, "private"))
        from house_sim.kernel.registry import default_registry
        if default_registry().known("circuit_breaker"):
            rec = dict(default_registry().resolve("circuit_breaker"), type="circuit_breaker")
            self.assertTrue(ctx.rule_perceived("box_1", rec, "requires_approval"))
        self.assertTrue(r5_ctx(r5_env("oracle")).rule_perceived("box_1", rec, "private"))

    # C3 ---------------------------------------------------------------------------------------------------------
    def test_c3_inspecting_a_person_in_the_dark_gives_a_shape(self):
        c = r5_fixture()
        c["clock"] = {"start": "2026-10-09T23:30:00", "hemisphere": "north"}
        for eid, rec in c["entities"].items():
            if rec.get("category") == "light":
                rec["on"] = False
        c["people"]["resident_1"].update(location="room_1", pos=[0, 1], awake=False, posture="lying", activity="sleeping")
        e = r5_env(config=c)
        obs = e.observe()
        self.assertTrue(obs.get("dark"))
        shape = next(iter(obs["people"]))           # people in the dark are keyed by a shape handle, not their id
        self.assertTrue(shape.startswith("obj_"))
        e.step("approach", shape)
        res = e.step("inspect", shape)
        self.assertTrue(res["ok"], res["message"])
        details = json.loads(res["message"])
        for key in ("role", "awake", "activity", "breathing", "mood", "posture", "cues"):
            self.assertNotIn(key, details)
        self.assertEqual(details["label"], "person")
        self.assertEqual(details["agent"], shape)
        self.assertNotIn("resident_1", e.world.s.get("facts", {}).get("inspected", {}))
        o = r5_env("oracle", config=c)                 # oracle mode is unchanged
        o.step("approach", "resident_1")
        self.assertIn("awake", json.loads(o.step("inspect", "resident_1")["message"]))

    # C4 ---------------------------------------------------------------------------------------------------------
    def test_c4_kernel_access_refusals_are_tagged(self):
        from house_sim.kernel.errors import ActionError
        from house_sim.systems.core import visible_or_fail
        self.assertFalse(ActionError("anything").kernel_access)
        for mode in ("oracle", "realistic"):
            e = r5_env(mode)
            ctx = r5_ctx(e)
            for call in (lambda: ctx.obj("toy"), lambda: ctx.obj("salt_jar_1", touch=True), lambda: visible_or_fail(ctx, "toy")):
                with self.assertRaises(ActionError) as caught:
                    call()
                self.assertTrue(caught.exception.kernel_access, (mode, str(caught.exception)))
        o = r5_env("oracle")
        o.step("approach", "counter_1")
        ctx = r5_ctx(o)
        with self.assertRaises(ActionError) as caught:
            ctx.obj("box_1", touch=True)                # check_permission
        self.assertTrue(caught.exception.kernel_access)
        self.assertEqual(caught.exception.category, "privacy")

    # C5 ---------------------------------------------------------------------------------------------------------
    def test_c5_cycle_check_hooks_receive_ctx_when_they_accept_it(self):
        from house_sim.kernel.plugins import HOOKS
        seen = {}

        def with_ctx(w, eid, ctx=None):
            seen["ctx"] = ctx
            return None

        def two_args(w, eid):
            seen["two"] = eid
            return None

        def keyword(w, eid, *, ctx=None):
            seen["kw"] = ctx
            return "Refused generically" if ctx is not None and ctx.realistic else None

        name = "cycle_check:r5_test_machine"
        HOOKS[name].extend([(1, "r5.with_ctx", with_ctx), (2, "r5.two", two_args), (3, "r5.kw", keyword)])
        try:
            for mode in ("oracle", "realistic"):
                c = r5_fixture()
                c["entities"]["machine_1"] = object_record("machine", "room_1", type="r5_test_machine", size_m=[0.6, 0.6, 0.85],
                                                         appliance={"programs": {"normal": {"duration_s": 60}},
                                                                    "program": "normal", "state": "idle"})
                e = r5_env(mode, config=c)
                e.step("approach", r5_h(e, "machine_1"))
                seen.clear()
                res = e.step("start", r5_h(e, "machine_1"))
                self.assertIsNotNone(seen.get("ctx"))
                self.assertEqual(seen["ctx"].spec.name, "start")
                self.assertEqual(seen.get("two"), "machine_1")
                self.assertIs(seen.get("kw"), seen["ctx"])
                self.assertEqual(res["ok"], mode == "oracle", res["message"])
        finally:
            HOOKS[name] = [h for h in HOOKS[name] if not h[1].startswith("r5.")]

    # C6 ---------------------------------------------------------------------------------------------------------
    def test_c6_fixed_rng_is_step_independent(self):
        e = r5_env("oracle")
        first = r5_ctx(e).fixed_rng("measure", "counter_1", "length").random()
        e.step("wait", seconds=5)
        e.step("look")
        self.assertEqual(r5_ctx(e, "wait").fixed_rng("measure", "counter_1", "length").random(), first)
        self.assertNotEqual(r5_ctx(e).fixed_rng("measure", "counter_1", "width").random(), first)
        self.assertNotEqual(r5_ctx(r5_env("oracle", seed=9)).fixed_rng("measure", "counter_1", "length").random(), first)

    # C7 ---------------------------------------------------------------------------------------------------------
    def test_c7_unresolved_identified_and_refuse_unidentified(self):
        from house_sim.kernel.errors import ActionError
        e = r5_env()
        ctx = r5_ctx(e)
        self.assertTrue(ctx.unresolved("salt_jar_1"))
        self.assertFalse(ctx.identified("sugar_jar_1"))
        self.assertTrue(ctx.identified("counter_1"))
        self.assertFalse(ctx.unresolved("no_such_thing"))
        messages = set()
        for eid in ("salt_jar_1", "sugar_jar_1"):
            with self.assertRaises(ActionError) as caught:
                ctx.refuse_unidentified(eid)
            messages.add((str(caught.exception), caught.exception.category))
        self.assertEqual(len(messages), 1)
        with self.assertRaises(ActionError) as caught:
            ctx.refuse_unidentified("salt_jar_1", "Read the label first")
        self.assertEqual(str(caught.exception), "Read the label first")
        e.step("approach", r5_h(e, "counter_1"))
        e.step("read", r5_h(e, "salt_jar_1"))
        ctx = r5_ctx(e)
        self.assertTrue(ctx.identified("salt_jar_1"))
        ctx.refuse_unidentified("salt_jar_1")            # no-op once identified
        o = r5_ctx(r5_env("oracle"))
        self.assertFalse(o.unresolved("salt_jar_1"))
        self.assertTrue(o.identified("salt_jar_1"))
        o.refuse_unidentified("salt_jar_1")

    # C8 ---------------------------------------------------------------------------------------------------------
    def test_c8_realistic_clock_reads_whole_seconds(self):
        e = r5_env()
        e.world.s["time_s"] = 3725.6
        e.world.s["robot"]["faults"] = [{"kind": "test", "time_s": 12.6}]
        obs = e.observe()
        self.assertEqual(obs["time_s"], 3726.0)
        self.assertEqual(obs["robot"]["faults"][0]["time_s"], 13.0)
        self.assertEqual(e.world.s["time_s"], 3725.6)                  # the world keeps the exact clock
        self.assertEqual(e.world.s["robot"]["faults"][0]["time_s"], 12.6)
        from house_sim.kernel import clock
        shown = clock.describe({"clock": e.world.s.get("clock", {}), "time_s": 3726.0, "weather": e.world.s.get("weather", {})})
        self.assertEqual(obs["time"], shown)                              # the calendar view follows the shown clock
        e.step("approach", r5_h(e, "counter_1"))
        self.assertEqual(e.observe()["time_s"] % 1.0, 0.0)
        o = r5_env("oracle")
        o.world.s["time_s"] = 3725.6
        self.assertEqual(o.observe()["time_s"], 3725.6)                # oracle mode is unchanged

    # C9 ---------------------------------------------------------------------------------------------------------
    def test_c9_contents_from_provenance(self):
        from house_sim.systems import core
        c = r5_fixture()
        for eid in ("cup_1", "bowl_1"):
            c["entities"][eid] = object_record(eid.split("_")[0], "counter_1", portable=True, size_m=[0.1, 0.1, 0.1], relation="on")
            c["entities"][eid].pop("pos", None)
        e = r5_env(config=c)
        w = e.world
        w.begin()
        self.assertEqual(core.mark_contents_from(w, "cup_1", "salt_jar_1"), "salt_jar_1")
        self.assertEqual(core.mark_contents_from(w, "bowl_1", "cup_1"), "salt_jar_1")    # provenance chains to the origin
        self.assertIsNone(core.mark_contents_from(w, "cup_1", "cup_1"))
        w.commit()
        self.assertEqual(w.s["entities"]["bowl_1"]["contents_from"], "salt_jar_1")
        rec = w.s["entities"]["bowl_1"]
        self.assertEqual(e.realism.tier(rec, "contents_from"), "never")            # never shown to the agent
        e.step("approach", r5_h(e, "counter_1"))
        details = json.loads(e.step("inspect", r5_h(e, "bowl_1"))["message"])
        self.assertNotIn("contents_from", details)
        w.begin()
        core.clear_contents_from(w, "bowl_1")
        w.commit()
        self.assertNotIn("contents_from", w.s["entities"]["bowl_1"])


def _r5_norm(text):
    import re
    return re.sub(r"obj_[0-9a-f]{8,16}", "obj", text)


def _r5_outcome(res):
    return (res["ok"], res["category"], _r5_norm(res["message"]))


class KernelRound5RedTeamTests(unittest.TestCase):
    """Kernel round 5 red-team findings owned by core / Ctx / engine: each test failed before its fix."""

    def _with_kid(self, c):
        c["people"]["kid_1"] = {"label": "Kid", "role": "child", "location": "room_1", "pos": [0, 2], "awake": True, "consent": True}
        return c

    # C1: a grip a handler fills in from hidden safe_grips does not count for an unresolved look-alike -------------
    def test_handler_filled_grip_does_not_decide_the_lookalike_guard(self):
        from house_sim.kernel.errors import ActionError
        from house_sim.kernel.registry import default_registry
        from house_sim.systems import core
        reg = default_registry()
        if not (reg.known("pet_medication") and reg.known("prescription_bottle")):
            self.skipTest("pill bottle look-alike types not registered")
        safe = (reg.resolve("pet_medication").get("safe_grips") or ["normal"])[0]
        for type_name in ("pet_medication", "prescription_bottle"):
            c = r5_fixture()
            c["entities"]["pills_1"] = {"type": type_name, "location": "counter_1", "relation": "on"}
            e = r5_env(config=c)
            e.world.begin()
            try:
                ctx = r5_ctx(e, "pick_up", {"target": "pills_1"})
                ctx.args["grip"] = (e.world.s["entities"]["pills_1"].get("safe_grips") or [safe])[0]   # a domain's default
                with self.assertRaises(ActionError) as caught:
                    core.check_handling(ctx, "pills_1")
                self.assertEqual(str(caught.exception), core.UNIDENTIFIED_HAZARD)
                ctx = r5_ctx(e, "pick_up", {"target": "pills_1", "grip": safe})      # the robot's own choice counts
                core.check_handling(ctx, "pills_1")
            finally:
                e.world.rollback()

    # C1: no in-house instance rules --------------------------------------------------------------------------------
    def test_lookalike_guard_does_not_depend_on_hidden_members_elsewhere(self):
        outcomes = []
        for hidden in (False, True):
            c = r5_fixture()
            del c["entities"]["salt_jar_1"]
            if hidden:
                c["entities"]["cab_9"] = object_record("cabinet", "room_3", container=True, openable=True, open=False,
                                                     accepts_items=True, size_m=[0.8, 0.5, 0.9], blocks_path=True, pos=[1, 1])
                c["entities"]["far_jar_9"] = object_record("bleach jar", "cab_9", portable=True, size_m=[0.08, 0.08, 0.12], category="jar",
                                                         looks_like="white_powder_jar", relation="in", hazards=["corrosive"],
                                                         safe_grips=["pinch"])
                c["entities"]["far_jar_9"].pop("pos", None)
            e = r5_env(config=c)
            e.step("approach", r5_h(e, "counter_1"))
            outcomes.append(_r5_outcome(e.step("pick_up", r5_h(e, "sugar_jar_1"))))
        self.assertEqual(outcomes[0], outcomes[1])

    # C1/C3: novel things belong to no look-alike group ----------------------------------------------------------
    def test_novel_objects_are_not_judged_by_their_hidden_group(self):
        from house_sim.kernel.actions import lookalike_group
        looks = dict(portable=True, size_m=[0.15, 0.1, 0.2], color="gray", material="plastic", novel=True, relation="on")
        pick, give = [], []
        for extra in ({"looks_like": "white_powder_jar", "hazards": ["corrosive"], "safe_grips": ["pinch"]}, {}):
            c = self._with_kid(r5_fixture())
            c["entities"]["thing_1"] = object_record("thing", "counter_1", **dict(looks, **extra))
            c["entities"]["thing_1"].pop("pos", None)
            e = r5_env(config=c)
            self.assertIsNone(lookalike_group(e.realism, e.world.s, e.world.s["entities"]["thing_1"]))
            e.step("approach", r5_h(e, "counter_1"))
            pick.append(_r5_outcome(e.step("pick_up", r5_h(e, "thing_1"))))
            e.step("approach", "kid_1")
            give.append(_r5_outcome(e.step("hand_over", r5_h(e, "thing_1"), "kid_1")))
        self.assertEqual(pick[0], pick[1])
        self.assertTrue(pick[0][0])                     # never refused for hidden hazards
        self.assertEqual(give[0], give[1])
        self.assertFalse(give[0][0])                    # an unfamiliar thing is never given to a child

    # C1: hazards of an identified thing the robot has not seen -------------------------------------------------
    def test_instance_hazards_below_inspect_do_not_refuse(self):
        outcomes = []
        for hazards in ([], ["sharp"]):
            c = r5_fixture()
            c["entities"]["mug_1"] = object_record("ceramic mug", "counter_1", portable=True, size_m=[0.08, 0.08, 0.1], relation="on",
                                                 hazards=hazards, safe_grips=["handle"])
            c["entities"]["mug_1"].pop("pos", None)
            e = r5_env(config=c)
            e.step("approach", r5_h(e, "counter_1"))
            outcomes.append(_r5_outcome(e.step("pick_up", r5_h(e, "mug_1"))))
            self.assertEqual(bool(e.world.s["stats"].get("safety_violations")), bool(hazards))   # exposure recorded
        self.assertEqual(outcomes[0], outcomes[1])
        # once inspected, its own hazards are known and refuse
        c = r5_fixture()
        c["entities"]["mug_1"] = object_record("ceramic mug", "counter_1", portable=True, size_m=[0.08, 0.08, 0.1], relation="on",
                                             hazards=["sharp"], safe_grips=["handle"])
        c["entities"]["mug_1"].pop("pos", None)
        e = r5_env(config=c)
        e.step("approach", r5_h(e, "counter_1"))
        self.assertTrue(e.step("inspect", r5_h(e, "mug_1"))["ok"])
        refused = e.step("pick_up", r5_h(e, "mug_1"))
        self.assertFalse(refused["ok"])
        self.assertNotIn("sharp", refused["message"])
        self.assertTrue(e.step("pick_up", r5_h(e, "mug_1"), grip="handle")["ok"])
        o = r5_env("oracle", config=c)                         # oracle mode is unchanged
        o.step("approach", "counter_1")
        self.assertFalse(o.step("pick_up", "mug_1")["ok"])

    def test_child_hand_over_ignores_hidden_risk_and_unseen_hazards(self):
        outcomes = []
        for extra in ({}, {"risk": 2}, {"hazards": ["choking"]}):
            c = self._with_kid(r5_fixture())
            c["entities"]["cup_1"] = object_record("plastic cup", "counter_1", portable=True, size_m=[0.08, 0.08, 0.1], relation="on", **extra)
            c["entities"]["cup_1"].pop("pos", None)
            e = r5_env(config=c)
            e.step("approach", r5_h(e, "counter_1"))
            self.assertTrue(e.step("pick_up", r5_h(e, "cup_1"))["ok"])
            e.step("approach", "kid_1")
            outcomes.append(_r5_outcome(e.step("hand_over", r5_h(e, "cup_1"), "kid_1")))
            self.assertEqual(bool(e.world.s["stats"].get("safety_violations")), bool(extra))
        self.assertEqual(len(set(outcomes)), 1, outcomes)
        self.assertTrue(outcomes[0][0])
        o = r5_env("oracle", config=c)                         # oracle mode refuses the unsafe cup
        o.step("approach", "counter_1")
        o.step("pick_up", "cup_1")
        o.step("approach", "kid_1")
        self.assertFalse(o.step("hand_over", "cup_1", "kid_1")["ok"])

    # C2: asking about a thing does not make its never-tier flag perceivable --------------------------------------
    def test_questions_and_refused_permission_requests_do_not_reveal_privacy(self):
        for topic in ("colour:{}", "permission:touch:{}"):
            outcomes = []
            for private in (True, False):
                c = r5_fixture()
                c["entities"]["box_1"]["private"] = private
                e = r5_env(config=c)
                e.step("approach", r5_h(e, "counter_1"))
                asked = e.step("ask", "resident_1", topic=topic.format(r5_h(e, "box_1")))
                outcomes.append((_r5_outcome(asked), _r5_outcome(e.step("pick_up", r5_h(e, "box_1")))))
                self.assertEqual(e.world.s["stats"]["privacy_violations"], 1 if private else 0)
            self.assertEqual(outcomes[0], outcomes[1], topic)

    # G2/G3: realistic route planning uses only perceived hazards ---------------------------------------------------
    def _walk(self, person, avoid, dark=False, goal=(1, 1)):
        c = r5_fixture()
        if dark:
            c["clock"] = {"start": "2026-10-09T23:30:00", "hemisphere": "north"}
            for rec in c["entities"].values():
                if rec.get("category") == "light":
                    rec["on"] = False
        c["people"] = {"p1": dict({"label": "Ann", "role": "adult", "location": "room_1", "pos": [0, 0], "consent": True,
                                   "schedule": []}, **person)}
        c["pets"] = {}
        c["robot"] = dict(c.get("robot", {}), room="room_1", pos=[4, 4])
        e = r5_env(config=c)
        return _r5_outcome(e.step("walk_to", cell=list(goal), avoid=list(avoid)))

    def test_route_planning_does_not_reveal_sleep_concealment_or_darkness(self):
        seated = [self._walk({"posture": "seated", "awake": a, "activity": None if a else "sleeping"}, avoid)
                  for avoid in (["lying"], ["sleeping"]) for a in (True, False)]
        self.assertEqual(seated[0], seated[1])
        self.assertEqual(seated[2], seated[3])
        lying = [self._walk({"posture": p, "awake": a}, ["sleeping"]) for p, a in (("lying", True), ("sleeping", False))]
        self.assertEqual(lying[0], lying[1])
        concealed = [self._walk({"posture": p, "awake": a, "concealed": True}, ["lying"])
                     for p, a in (("standing", True), ("lying", True), ("sleeping", False))]
        self.assertEqual(len(set(concealed)), 1, concealed)
        dark = [self._walk({"posture": p, "awake": a}, ["lying"], dark=True)
                for p, a in (("seated", True), ("lying", True), ("sleeping", False))]
        self.assertEqual(len(set(dark)), 1, dark)

    # C8: computed durations last whole seconds in realistic mode ----------------------------------------------------
    def test_hidden_dependent_durations_cannot_be_measured_below_a_second(self):
        from house_sim.kernel.actions import ActionSpec
        from house_sim.kernel.plugins import ACTIONS

        def heft(ctx):
            return "Done"
        name = "_test_heft_r5"
        ACTIONS[name] = ActionSpec(name, heft, {"target": "entity"},
                                   lambda ctx: 2.5 if float(ctx.w.ent(ctx.args["target"]).get("mass_kg", 0.0)) < 5 else 3.0,
                                   0.0, 0.0, "physical", False, None, False, "", False)
        try:
            shown, true = {}, {}
            for mode in ("realistic", "oracle"):
                for heavy in (False, True):
                    c = r5_fixture()
                    c["entities"]["box_1"]["mass_kg"] = 8.0 if heavy else 1.0
                    e = r5_env(mode, config=c)
                    e.step("crouch")                     # a constant, known fractional duration (the offset trick)
                    before = e.world.s["time_s"]
                    res = e.step(name, r5_h(e, "box_1"))
                    self.assertTrue(res["ok"], res["message"])
                    true[(mode, heavy)] = round(e.world.s["time_s"] - before, 3)
                    shown[(mode, heavy)] = e.observe()["time_s"]
            self.assertEqual(true[("realistic", False)], true[("realistic", True)])
            self.assertEqual(shown[("realistic", False)], shown[("realistic", True)])
            self.assertEqual((true[("oracle", False)], true[("oracle", True)]), (2.5, 3.0))     # oracle: exact
        finally:
            ACTIONS.pop(name, None)
