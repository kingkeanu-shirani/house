"""Robot faults, falls, drops and water exposure through the engine (BODY-FAULTS, TIER1_INTEGRATION 2.4 B5;
TIER1_INTEGRATION 1.5; TIER1_EMBODIMENT 4.11-4.13 and 10 items 25-30).

BODY-MODEL tested kernel/body_faults.py as a library on fake worlds; this file drives it through HouseEnv with the
real physics layer and the real observation:

* legacy fault logs and setup faults normalize at reset, and realistic proprioception shows what the robot detected;
* scripted robot.fault / robot.clear_fault events start and end faults with their effects (hearing, network, lens);
* log_fault (logged, undetected, unfounded, notes) and diagnostics through actions, with the statistics surviving;
* gripper slips are the same in two runs and in a replay, drop through physics and are then known to the robot;
* a failing arm drops what it holds, or hands a two-handed load to the other arm when that arm can carry it;
* a fall counts once, lies the robot down, drops what it holds through physics (phys.fell, cause robot_fell),
  undocks it and damages it by the height, load and water rules;
* IP54 water exposure: rain and splashes are harmless, aimed spray up to 600 s in total, jets up to 3 s, immersion
  never; ingress fails the cameras and the actuators, and the observation follows;
* the random fault events the fuzzer and the perception audit use are valid, deterministic and replayable.

The houses are built by hand (no furnishers), so the file passes with HOUSE_SIM_DOMAINS=core and with every domain
loaded.
"""
import copy
import random
import tempfile
import unittest
from pathlib import Path

from house_sim.kernel import body, body_faults
from house_sim.kernel.actions import action
from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.plugins import ACTIONS, HOOKS
from house_sim.kernel.predicates import digest


def config(robot=None, entities=None, events=None):
    """A lit 6 x 4 m room ("lab") with a hallway; the robot at [2, 3]."""
    ents = {"light_1": {"type": "ceiling_light", "location": "lab", "pos": [11, 0], "on": True, "mounted": True,
                        "mount_height_m": 2.4},
            "light_2": {"type": "ceiling_light", "location": "hall", "pos": [1, 1], "on": True, "mounted": True,
                        "mount_height_m": 2.4}}
    ents.update(copy.deepcopy(entities or {}))
    rob = {"room": "lab", "pos": [2, 3]}
    rob.update(copy.deepcopy(robot or {}))
    return {"schema_version": 2, "start_room": "lab",
            "rooms": {"lab": {"size_m": [6.0, 4.0], "type": "living_room", "exits": ["hall"]},
                      "hall": {"size_m": [2.0, 3.0], "type": "hallway", "exits": ["lab"]}},
            "entities": ents, "people": {}, "pets": {}, "events": list(events or []), "robot": rob}


def held(eid, hand, mass_kg=1.0, size=(0.1, 0.1, 0.12), **props):
    """An entity the robot holds ("right", "left" or "both")."""
    out = {"type": "generic_item", "label": props.pop("label", eid), "location": "robot", "relation": "held",
           "held_hand": hand, "size_m": list(size), "mass_kg": mass_kg}
    out.update(props)
    return {eid: out}


def hands(left=None, right=None):
    return {"hands": {"left": left, "right": right}}


def make(robot=None, perception=None, **kw):
    return HouseEnv(config(robot, **kw), seed=0, perception=perception, camera="room")


def robot(env):
    return env.world.s["robot"]


def records(env):
    return robot(env)["fault_state"]


class FaultTestCase(unittest.TestCase):
    def register(self, name, fn, params=None, duration=1.0):
        """A test action calling body_faults functions inside a step (journaled like any verb); removed after the
        test."""
        ACTIONS.pop(name, None)
        action(name, params=params or {}, duration=duration, category="control")(fn)
        self.addCleanup(ACTIONS.pop, name, None)

    def capture(self, name):
        """Add a recorder to the hook list `name` for one test; returns the list of recorded calls. (The recorder is
        appended and removed again: replacing the HOOKS table for the test would also drop, at its restore, whatever a
        module imported lazily during the test registered.)"""
        calls = []
        entry = (100, "test." + name, lambda *args: calls.append(args))
        HOOKS[name].append(entry)
        self.addCleanup(HOOKS[name].remove, entry)
        return calls


# ----------------------------------------------------------------------
# fault records, events, the fault log (items 25, 26)

class FaultRecordTests(FaultTestCase):
    def test_legacy_fault_logs_and_setup_faults_normalize_at_reset(self):
        legacy = {"faults": ["water_ingress", {"kind": "gripper_cut", "time_s": 12.6}],
                  "fault_state": {"arm_weak_left": {"kind": "arm_weak", "part": "left", "latent": True, "severity": 0.4},
                                  "legs_weak": {"kind": "legs_weak"}}}
        for mode in ("oracle", "realistic"):
            env = make(legacy, perception=mode)
            r = robot(env)
            self.assertEqual(r["faults"][0], {"kind": "water_ingress", "time_s": None, "legacy": True})
            self.assertEqual(r["faults"][1], {"kind": "gripper_cut", "time_s": 12.6})
            self.assertEqual((r["arm_health"]["left"], r["arms_ok"]["left"], r["legs_health"]), (0.4, True, 0.5))
            self.assertTrue(records(env)["arm_weak_left"]["latent"])
            self.assertTrue(records(env)["legs_weak"]["detected"])                # known at once
            own = env.observe()["robot"]
            if mode == "realistic":
                self.assertEqual(own["arms"], {"left": "ok", "right": "ok"})       # latent: not known
                self.assertEqual(own["legs"], "weak")
                self.assertEqual([f["kind"] for f in own["faults"]], ["water_ingress", "gripper_cut", "legs_weak"])
                self.assertEqual(own["faults"][1]["time_s"], 13.0)                 # the clock in whole seconds
            else:
                self.assertTrue(own["fault_state"]["arm_weak_left"]["latent"])     # oracle: the debug view
        named = make({"fault_state": {"gripper_slip_right": {"latent": True}, "network_down": {}}})
        self.assertEqual({fid: (r["kind"], r["part"], r["latent"]) for fid, r in records(named).items()},
                         {"gripper_slip_right": ("gripper_slip", "right", True),       # a record named by its id
                          "network_down": ("network_down", "network", False)})
        self.assertFalse(robot(named)["network"])
        with self.assertRaises(ValueError):
            make({"fault_state": {"arm_weak": {"severity": 0.5}}})                     # an arm fault needs its side

    def test_a_cut_gripper_pad_is_a_kernel_fault_that_makes_the_hand_slip(self):
        """Adoption-round request (food 6): gripper_cut is a kernel fault kind (food and cleaning registered identical
        copies, whichever loaded first); its onset starts a gripper_slip on that hand, known at once."""
        self.assertIn("gripper_cut", body_faults.FAULT_KINDS)
        self.assertEqual(body_faults.FAULT_KINDS["gripper_cut"]["part"], "gripper")
        events = [{"at_s": 0.0, "effect": "robot.fault", "args": {"kind": "gripper_cut", "part": "left"}}]
        env = make(events=events)
        env.step("wait", seconds=30)
        self.assertEqual(sorted(records(env)), ["gripper_cut_left", "gripper_slip_left"])
        self.assertEqual(records(env)["gripper_slip_left"]["source"], "gripper_cut")
        self.assertTrue(records(env)["gripper_cut_left"]["detected"])

    def test_a_house_network_outage_is_no_robot_fault(self):
        """Adoption-round request (electrical 3): the household router going down takes the robot's network with it
        without logging a robot fault (body.set_network); the network comes back only when every house outage and the
        robot's own network_down fault are over, in either order."""
        env = make()
        w = env.world
        w.begin()
        self.assertFalse(body.set_network(w, False, "router"))
        self.assertEqual((robot(env)["network"], robot(env)["network_outages"]), (False, ["router"]))
        self.assertEqual(robot(env)["faults"], [])
        body_faults.add_fault(w, "network_down", source="event")
        self.assertFalse(body.set_network(w, True, "router"))          # the robot's own fault keeps it down
        self.assertNotIn("network_outages", robot(env))
        body_faults.clear_fault(w, "network_down", "restored")
        self.assertTrue(robot(env)["network"])
        body.set_network(w, False, "router")
        body_faults.add_fault(w, "network_down", source="event")
        body_faults.clear_fault(w, "network_down", "restored")
        self.assertFalse(robot(env)["network"])                           # the router is still down
        self.assertTrue(body.set_network(w, True, "router"))
        w.commit()
        self.assertIn("network_outages", body.ROBOT_FIELDS)
        self.assertEqual(body.ROBOT_FIELDS["network_outages"]["tier"], "hidden")

    def test_scripted_fault_events_start_and_clear_faults_with_their_effects(self):
        events = [{"at_s": 0.0, "effect": "robot.fault", "args": {"kind": "mic_failed"}},
                  {"at_s": 0.0, "effect": "robot.fault", "args": {"kind": "network_down", "until_s": 90.0}},
                  {"at_s": 0.0, "effect": "robot.fault", "args": {"kind": "camera_dirty", "severity": 0.5}},
                  {"at_s": 60.0, "effect": "robot.clear_fault", "args": {"kind": "mic_failed"}}]
        env = make(perception="realistic", events=events)
        env.step("wait", seconds=30)
        own = env.observe()["robot"]
        self.assertEqual((own["hearing"], own["network"], own["camera"]), ("off", "offline", "dirty"))
        self.assertEqual(sorted(f["kind"] for f in own["faults"]), ["camera_dirty", "mic_failed", "network_down"])
        env.step("wait", seconds=90)
        own = env.observe()["robot"]
        self.assertEqual((own["hearing"], own["network"]), ("full", "connected"))  # cleared; until_s restored it
        self.assertEqual([f["kind"] for f in own["faults"]], ["camera_dirty"])
        self.assertIsNotNone(records(env)["mic_failed"]["cleared_s"])
        kinds = [e["kind"] for e in env.world.s["event_log"]]
        for kind in ("fault_onset", "fault_detected", "fault_cleared"):
            self.assertIn(kind, kinds)

    def test_log_fault_and_diagnostics_through_actions(self):
        self.register("_t_log", lambda ctx: str(body_faults.log_fault(ctx.w, ctx.args["kind"], "test")["status"]),
                      params={"kind": "str"})
        self.register("_t_diagnose", lambda ctx: ",".join(body_faults.diagnose(ctx.w)["faults"]))
        latent = {"fault_state": {"arm_weak_left": {"kind": "arm_weak", "part": "left", "latent": True}}}
        env = make(latent, perception="realistic")
        self.assertEqual(env.step("_t_log", kind="arm_weak")["message"], "undetected")
        self.assertEqual(env.step("_t_log", kind="camera_failed")["message"], "unfounded")
        self.assertEqual(env.step("_t_log", kind="loose_handrail")["message"], "note")
        self.assertEqual(env.world.s["stats"]["unfounded_fault_logs"], 2)        # notes never count
        self.assertEqual(env.step("_t_diagnose")["message"], "arm_weak_left")
        self.assertTrue(records(env)["arm_weak_left"]["detected"])
        self.assertEqual(env.step("_t_log", kind="arm_weak")["message"], "logged")
        self.assertIsNotNone(records(env)["arm_weak_left"]["logged_s"])
        own = env.observe()["robot"]
        self.assertEqual(own["arms"]["left"], "weak")
        # the robot's own log entries (what it wrote, founded or not) and then the detected record, now logged
        self.assertEqual([(f["kind"], f["part"], f["logged"]) for f in own["faults"]],
                         [("arm_weak", None, True), ("camera_failed", None, True), ("loose_handrail", None, True),
                          ("arm_weak", "left", True)])


# ----------------------------------------------------------------------
# slips, failing arms, falls (items 27-29)

class SlipTests(FaultTestCase):
    WALKS = ([6, 3], [2, 3]) * 8

    def scene(self):
        robot = dict(hands(right="pot_1"), fault_state={
            "gripper_slip_right": {"kind": "gripper_slip", "part": "right", "latent": True}})
        return make(robot, perception="realistic", entities=held("pot_1", "right", mass_kg=2.0, size=(0.25, 0.25, 0.2)))

    def walk(self, env):
        trail = []
        for cell in self.WALKS:
            result = env.step("walk_to", cell=cell)
            trail.append((result["ok"], result["message"], digest(env.world.s)))
        return trail

    def test_gripper_slips_are_the_same_in_two_runs_and_a_replay(self):
        first, second = self.scene(), self.scene()
        trail = self.walk(first)
        self.assertEqual(trail, self.walk(second))
        r = robot(first)
        self.assertIsNone(r["hands"]["right"])                                    # it slipped out
        slipped = [i for i, (_, message, _) in enumerate(trail) if "slipped out of the right gripper" in message]
        self.assertEqual(len(slipped), 1, trail)
        self.assertEqual(r["_slips"], slipped[0] + 1)                             # one draw per loaded walk
        self.assertEqual(first.world.s["stats"]["robot_drops"], 1)
        self.assertEqual(first.world.s["entities"]["pot_1"]["location"], "lab")
        self.assertTrue(records(first)["gripper_slip_right"]["detected"])
        self.assertEqual(first.observe()["robot"]["grip"], {"left": "ok", "right": "slipping"})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "replay.json"
            first.save_replay(path)
            replayed = HouseEnv.replay(path)
        self.assertEqual(digest(replayed.world.s), digest(first.world.s))

    def test_the_slip_hazard_is_per_metre_not_per_walk(self):
        """Stage-B review (body, medium): the slip was drawn once per motion leaf, so the same 5 m carried as ten short
        walks dropped the item 95 % of the time against 23-30 % for one walk. It is a constant hazard per metre now:
        how an agent splits a route does not change the odds."""
        state = self.scene().world.s
        p_walk = body_faults.slip_probability(state, 5.0)
        p_step = body_faults.slip_probability(state, 0.5)
        self.assertAlmostEqual(1.0 - (1.0 - p_step) ** 10, p_walk, places=12)
        self.assertAlmostEqual(body_faults.slip_probability(state, 10.0), 0.3, places=12)    # the profile's anchor
        self.assertEqual(body_faults.slip_probability(state), 0.3)

        def drops(cells, seed):
            robot = dict(hands(right="pot_1"), pos=[0, 3], fault_state={
                "gripper_slip_right": {"kind": "gripper_slip", "part": "right", "latent": True}})
            env = HouseEnv(config(robot, entities=held("pot_1", "right", mass_kg=2.0, size=(0.25, 0.25, 0.2))),
                           seed=seed, camera="room")
            for cell in cells:
                env.step("walk_to", cell=cell)
            return robot_hand_empty(env)

        def robot_hand_empty(env):
            return robot(env)["hands"]["right"] is None
        one = sum(drops([[10, 3]], seed) for seed in range(40))
        ten = sum(drops([[c, 3] for c in range(1, 11)], seed) for seed in range(40))
        self.assertLessEqual(abs(one - ten), 8, (one, ten))
        self.assertLess(ten, 16, (one, ten))                       # it was 38 of 40

    def test_a_light_load_never_slips(self):
        cfg = dict(hands(right="card_1"), fault_state={
            "gripper_slip_right": {"kind": "gripper_slip", "part": "right", "latent": True}})
        env = make(cfg, entities=held("card_1", "right", mass_kg=0.2))
        self.walk(env)
        self.assertEqual((robot(env)["hands"]["right"], robot(env)["_slips"]), ("card_1", 0))


class ArmFailureTests(FaultTestCase):
    FAIL_LEFT = [{"at_s": 0.0, "effect": "robot.fault", "args": {"kind": "arm_failed", "part": "left"}}]

    def test_a_failing_arm_drops_what_it_holds(self):
        fell = self.capture("phys.fell")
        env = make(hands(left="mug_1"), events=self.FAIL_LEFT,
                   entities=held("mug_1", "left", mass_kg=0.4, material="ceramic"))
        env.step("wait", seconds=30)
        r = robot(env)
        self.assertEqual((r["arm_health"]["left"], r["arms_ok"]["left"], r["hands"]["left"]), (0.0, False, None))
        self.assertEqual(env.world.s["entities"]["mug_1"]["location"], "lab")
        self.assertEqual(env.world.s["stats"]["robot_drops"], 1)
        self.assertEqual([(call[1], call[2]["cause"]) for call in fell], [("mug_1", "arm_failed")])
        self.assertTrue(records(env)["arm_failed_left"]["detected"])

    def test_a_two_handed_load_goes_to_the_other_arm_when_it_can_carry_it(self):
        light = make(hands(left="box_1", right="box_1"), events=self.FAIL_LEFT,
                     entities=held("box_1", "both", mass_kg=6.0, size=(0.5, 0.4, 0.3)))
        light.step("wait", seconds=30)
        self.assertEqual(robot(light)["hands"], {"left": None, "right": "box_1"})
        self.assertEqual(light.world.s["entities"]["box_1"]["held_hand"], "right")
        self.assertEqual(light.world.s["stats"].get("robot_drops", 0), 0)
        heavy = make(hands(left="box_1", right="box_1"), events=self.FAIL_LEFT,
                     entities=held("box_1", "both", mass_kg=12.0, size=(0.5, 0.4, 0.3)))
        heavy.step("wait", seconds=30)
        self.assertEqual(robot(heavy)["hands"], {"left": None, "right": None})   # too heavy for one arm
        self.assertEqual(heavy.world.s["stats"]["robot_drops"], 1)


class FallTests(FaultTestCase):
    def setUp(self):
        self.register("_t_fall", lambda ctx: str(sorted(body_faults.fall(
            ctx.w, "test", height_m=ctx.args.get("height_m", 0.0), water=ctx.args.get("water", False)))),
            params={"height_m": "?float", "water": "?bool"})

    def test_a_fall_counts_once_lies_down_and_drops_through_physics(self):
        fell, robot_fell = self.capture("phys.fell"), self.capture("robot_fell")
        env = make(dict(hands(right="jar_1"), docked=True), perception="realistic",
                   entities=held("jar_1", "right", mass_kg=1.0))
        self.assertTrue(env.step("_t_fall")["ok"])
        r = robot(env)
        self.assertEqual((r["fallen"], r["posture"], r["docked"], r["hands"]["right"]), (True, "lying", False, None))
        self.assertEqual(env.world.s["stats"]["falls"], 1)
        self.assertEqual(env.world.s["stats"]["robot_drops"], 1)
        self.assertEqual([(call[1], call[2]["cause"], call[2]["height_m"]) for call in fell], [("jar_1", "robot_fell", 1.0)])
        self.assertEqual(robot_fell[0][1]["cause"], "test")
        self.assertIsNone(body.fault(env.world.s, "actuator_failed"))           # a fall on the flat is survivable
        self.assertEqual(env.step("_t_fall")["message"], "[]")                   # already down: nothing more
        self.assertEqual(env.world.s["stats"]["falls"], 1)
        own = env.observe()["robot"]
        self.assertEqual((own["fallen"], own["posture"]), (True, "lying"))
        self.assertIn("fall", [f["kind"] for f in own["faults"]])               # it felt the fall

    def test_a_power_down_on_a_climbing_aid_frees_it_and_lands_beside_it(self):
        """Stage-B review (body, low): a power-down on a stool left the stool occupied_by the robot and the robot
        lying in the stool's own cell."""
        stool = {"stool_1": {"type": "generic_item", "label": "step stool", "location": "lab", "pos": [4, 3],
                             "size_m": [0.4, 0.3, 0.23], "standing_height_m": 0.23, "occupied_by": "robot",
                             "occupied_step": 1}}
        env = make({"battery": 0.01, "pos": [4, 3], "standing_on": "stool_1", "nav_climbed_from": [3, 3]},
                   entities=stool)
        self.assertTrue(env.step("wait", seconds=600)["ok"])
        r = robot(env)
        self.assertEqual((r["power_state"], r["fallen"], r["standing_on"], r["pos"]), ("off", True, None, [3, 3]))
        self.assertNotIn("occupied_by", env.world.s["entities"]["stool_1"])
        self.assertNotIn("occupied_step", env.world.s["entities"]["stool_1"])

    def test_a_power_down_lowers_the_posture_only_from_standing(self):
        """Stage-B review (body, low): power_down wrote "crouching" whatever the posture, so a fallen robot read
        lying-and-crouching and a sitting one rose to a crouch while its motors lost power."""
        for props, posture in (({"fallen": True, "posture": "lying"}, "lying"), ({"posture": "sitting"}, "sitting"),
                               ({"posture": "kneeling"}, "kneeling"), ({}, "crouching")):
            env = make(dict(props, battery=0.01))
            self.assertTrue(env.step("wait", seconds=600)["ok"])
            self.assertEqual((robot(env)["power_state"], robot(env)["posture"]), ("off", posture), props)

    def test_falls_damage_by_height_load_and_water(self):
        for robot_cfg, args, faults in (
                ({}, {"height_m": 0.5}, {"actuator_failed"}),
                (dict(hands(right="bag_1")), {}, {"actuator_failed"}),            # onto a 5 kg load
                ({}, {"water": True}, {"water_ingress", "camera_failed", "actuator_failed"})):
            entities = held("bag_1", "right", mass_kg=5.0) if robot_cfg else None
            env = make(robot_cfg, perception="realistic", entities=entities)
            env.step("_t_fall", **args)
            active = {r["id"] for r in body_faults.active_faults(env.world.s)}
            self.assertEqual(active, faults, args)
            own = env.observe()["robot"]
            if "camera_failed" in faults:
                self.assertEqual(own["camera"], "off")
            self.assertFalse(env.step("walk_to", cell=[4, 3])["ok"])             # damaged: it cannot walk


# ----------------------------------------------------------------------
# water (item 30)

class WaterTests(FaultTestCase):
    def setUp(self):
        self.register("_t_water", lambda ctx: ",".join(body_faults.expose_water(ctx.w, ctx.args["kind"],
                                                                                ctx.args["seconds"])),
                      params={"kind": "str", "seconds": "float"})

    def test_ip54_rain_spray_jet_and_immersion(self):
        env = make()
        self.assertEqual(env.step("_t_water", kind="rain", seconds=36000.0)["message"], "")
        self.assertEqual(env.step("_t_water", kind="splash", seconds=600.0)["message"], "")
        self.assertEqual(env.step("_t_water", kind="spray", seconds=400.0)["message"], "")
        self.assertEqual(robot(env)["water_exposure_s"], {"spray": 400.0})
        self.assertEqual(env.step("_t_water", kind="spray", seconds=300.0)["message"],
                         "water_ingress,camera_failed,actuator_failed")          # 700 s > the 600 s IPX4 limit
        jets = make()
        self.assertEqual(jets.step("_t_water", kind="jet", seconds=3.0)["message"], "")
        self.assertEqual(jets.step("_t_water", kind="jet", seconds=1.0)["message"],
                         "water_ingress,camera_failed,actuator_failed")
        dunked = make(perception="realistic")
        self.assertTrue(dunked.step("_t_water", kind="immersion", seconds=1.0)["message"])
        own = dunked.observe()["robot"]
        self.assertEqual(own["camera"], "off")
        self.assertEqual(sorted(f["kind"] for f in own["faults"]), ["actuator_failed", "camera_failed", "water_ingress"])
        self.assertNotIn("water_exposure_s", own)                                 # hidden tier
        with self.assertRaises(ValueError):
            body_faults.expose_water(make().world, "hail", 1.0)


# ----------------------------------------------------------------------
# the random fault events of the fuzzer and the perception audit

class RandomFaultTests(FaultTestCase):
    def test_sampled_events_are_valid_deterministic_and_replay(self):
        first = body_faults.sample_fault_events(random.Random("x"), count=6, horizon_s=600.0)
        self.assertEqual(first, body_faults.sample_fault_events(random.Random("x"), count=6, horizon_s=600.0))
        faults = [e for e in first if e["effect"] == "robot.fault"]
        self.assertEqual(len(faults), 6)
        self.assertEqual(faults[0]["at_s"], 0.0)
        for event in first:
            args = event["args"]
            self.assertIn(args["kind"], body_faults.FAULT_KINDS)
            body_faults.fault_id(args["kind"], args.get("part"))                  # a valid id (sides given)
            lo, hi = body_faults.SAMPLE_SEVERITY.get(args["kind"], (0.0, 1.0))
            self.assertTrue(lo <= args.get("severity", lo) <= hi)
        for seed in range(6):
            events = body_faults.sample_fault_events(random.Random("fuzz-faults:{}".format(seed)), horizon_s=300.0)
            env = make(perception="realistic", events=events)
            for _ in range(12):
                env.step("wait", seconds=30)
            env.observe()
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "replay.json"
                env.save_replay(path)
                replayed = HouseEnv.replay(path)
            self.assertEqual(digest(replayed.world.s), digest(env.world.s), seed)
            self.assertEqual(digest(replayed.observe()), digest(env.observe()), seed)


if __name__ == "__main__":
    unittest.main()
