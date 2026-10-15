"""The robot's own sensors in what it observes and what its actions can target (BODY-FAULTS, TIER1_INTEGRATION 2.4
B5; TIER1_INTEGRATION 1.3 and 1.12; TIER1_EMBODIMENT 4.10, 4.14, 6 and 10 items 31-34).

What this file pins:

* the imaging gate: privacy mode, cameras switched off, a failed camera and a room privacy policy all give the same
  depth-only view; room mode applies the darkness rule to the whole room in both perception modes (things of 0.4 m
  and up as dark shapes, people and pets as shapes in realistic perception, no hazards, no weather through a window,
  obs.dark), map fixtures stay targetable without being listed, held things stay visible; the geometric camera's IR
  rule gives the same picture;
* the power gate: powered down, only what the robot holds is visible -- no people, pets, hazards, weather, observer
  channels, sounds or messages -- in both perception and camera modes, and everything comes back when it wakes;
* hearing: obs.messages follows body.can_hear (full, addressed only, none) and "none" drops every sounds channel;
* proprioception: oracle obs.robot is today's keys plus the registered visible robot fields, exactly; realistic
  obs.robot is the robot's own report (integer gauge equal to obs.battery, buckets and estimates, detected faults only,
  pos None while unlocalized, and obs.camera.pose.cell with it); without the proprioception sensor only pose and
  hands;
* realism reads the lens clarity from the body (scratches included);
* the perception audit's robot check accepts clean observations and catches every leak class it names;
* determinism (two runs and a replay give the same realistic observations) and the proprioception budget.

The houses are built by hand (no furnishers), so the file passes with HOUSE_SIM_DOMAINS=core and with every domain
loaded.
"""
import copy
import json
import tempfile
import time
import unittest
from pathlib import Path

from house_sim.kernel import body, perception as perception_mod, proprio
from house_sim.kernel.actions import IMAGING_OFF_TEXT, MIC_OFF_TEXT
from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.errors import ActionError
from house_sim.kernel.plugins import OBSERVERS
from house_sim.kernel.predicates import digest
from house_sim.kernel.realism import BLIND_WHO
from house_sim.kernel.registry import default_registry
from house_sim.tasks.setup import apply_setup
from tools import perception_audit as audit

COLS, ROWS = 12, 8
MODES = ("oracle", "realistic")
HIDDEN = {n for n, e in body.ROBOT_FIELDS.items() if e["tier"] == "hidden"}


def config(robot=None, entities=None, people=None, pets=None, light=True, events=None, facing=None):
    """A lit 6 x 4 m living room ("lab": ceiling light, window, light switch, a table with a cup on it, a 0.6 m crate,
    a wet patch on the floor, Ann and her dog Rex) and a hallway; the robot at [2, 3] holds a jar in its right hand."""
    ents = {"light_1": {"type": "ceiling_light", "location": "lab", "pos": [COLS - 1, 0], "on": light, "mounted": True,
                        "mount_height_m": 2.4},
            "light_2": {"type": "ceiling_light", "location": "hall", "pos": [1, 1], "on": True, "mounted": True,
                        "mount_height_m": 2.4},
            "window_1": {"type": "window", "location": "lab", "pos": [6, ROWS - 1]},
            "switch_1": {"type": "light_switch", "location": "lab", "pos": [0, 3]},
            "table_1": {"type": "table", "location": "lab", "pos": [3, 3], "accepts_items": True},
            "cup_1": {"type": "generic_item", "label": "cup", "location": "table_1", "relation": "on",
                      "size_m": [0.09, 0.09, 0.11], "mass_kg": 0.3, "color": "red"},
            "crate_1": {"type": "generic_item", "label": "crate", "location": "lab", "pos": [5, 1],
                        "size_m": [0.6, 0.5, 0.5], "mass_kg": 6.0},
            "jar_1": {"type": "generic_item", "label": "jar", "location": "robot", "relation": "held",
                      "held_hand": "right", "size_m": [0.1, 0.1, 0.12], "mass_kg": 0.5}}
    ents.update(copy.deepcopy(entities or {}))
    rob = {"room": "lab", "pos": [2, 3], "hands": {"left": None, "right": "jar_1"}}
    if facing is not None:
        rob["facing_deg"] = facing
    rob.update(copy.deepcopy(robot or {}))
    if people is None:
        people = {"ann": {"label": "Ann", "role": "adult", "location": "lab", "pos": [8, 3], "posture": "standing",
                          "awake": True, "mood": "happy"}}
    if pets is None:
        pets = {"rex": {"label": "Rex", "species": "dog", "location": "lab", "pos": [6, 5], "posture": "standing"}}
    return {"schema_version": 2, "start_room": "lab",
            "rooms": {"lab": {"size_m": [COLS * 0.5, ROWS * 0.5], "type": "living_room", "exits": ["hall"],
                              "wet_cells": [[9, 1]]},
                      "hall": {"size_m": [2.0, 3.0], "type": "hallway", "exits": ["lab"]}},
            "entities": ents, "people": copy.deepcopy(people), "pets": copy.deepcopy(pets),
            "events": list(events or []), "robot": rob}


def make(robot=None, perception=None, camera="room", sensors=None, **kw):
    if camera == "geometric":
        kw.setdefault("facing", 0.0)
    return HouseEnv(config(robot, **kw), seed=0, perception=perception, camera=camera, sensors=sensors)


def listed(env, obs):
    """True ids of obs.visible_entities -> records (realistic handles resolved)."""
    out = {}
    for key, rec in obs["visible_entities"].items():
        eid = env.realism.handles.resolve(key) if env.realism is not None else key
        out[eid] = rec
    return out


def messages(state, steps=0):
    return [{"step": steps, "time_s": 0.0, "speaker": "ann", "kind": "speech", "text": "to the robot", "to": "robot",
             "room": "lab", "heard_by_robot": True, "channel": "voice"},
            {"step": steps, "time_s": 0.0, "speaker": "ann", "kind": "speech", "text": "to Bob", "to": "bob",
             "room": "lab", "heard_by_robot": True, "channel": "voice"},
            {"step": steps, "time_s": 0.0, "speaker": "ann", "kind": "speech", "text": "on the phone", "to": "robot",
             "room": "hall", "heard_by_robot": True, "channel": "phone"},
            {"step": steps, "time_s": 0.0, "speaker": "ann", "kind": "speech", "text": "hey robot", "to": None,
             "room": "lab", "heard_by_robot": True, "channel": "voice", "addressed_robot": True},
            {"step": steps, "time_s": 0.0, "speaker": "ann", "kind": "speech", "text": "far away", "to": "robot",
             "room": "hall", "heard_by_robot": False, "channel": "voice"}]


IMAGING_OFF = ({"privacy_mode": True}, {"camera_on": False}, {"sensors": {"camera": False}},
               {"privacy_policy": {"camera_off": ["living_room"], "when": "always"}},
               {"privacy_policy": {"camera_off": ["living_room"]}})       # "occupied": Ann is in the room


class ObserverCase(unittest.TestCase):
    def probe(self):
        """A test observer that adds a channel and sounds in every place the hearing filter clears."""
        def fn(env, w, obs):
            obs["probe"] = {"seen": True, "sounds": [{"kind": "hum", "source_hint": "here", "loudness": 0.2}]}
            obs.setdefault("sounds", []).append({"kind": "beep", "source_hint": "here", "loudness": 0.3})
            obs["mechanism_sounds"] = [{"kind": "click"}]
        entry = (999, "test.probe", fn)
        OBSERVERS.append(entry)
        self.addCleanup(OBSERVERS.remove, entry)


# ----------------------------------------------------------------------
# the imaging gate (item 31)

class ImagingGateTests(ObserverCase):
    def test_privacy_mode_shows_only_shapes_of_0_4_m_and_up_in_both_perception_modes(self):
        for mode in MODES:
            on, off = make(perception=mode), make({"privacy_mode": True}, perception=mode)
            seen, blind = on.observe(), off.observe()
            self.assertTrue({"cup_1", "crate_1", "jar_1", "table_1"} <= set(listed(on, seen)), mode)
            self.assertEqual(set(listed(off, blind)), {"crate_1", "jar_1", "table_1", "window_1"}, mode)
            self.assertEqual(listed(off, blind)["crate_1"]["label"], "dark shape")
            self.assertEqual(listed(off, blind)["jar_1"]["label"], "jar")         # held things stay visible
            self.assertFalse(seen["dark"])
            self.assertTrue(blind["dark"])
            self.assertTrue(seen["grid"]["hazards"])
            self.assertEqual(blind["grid"]["hazards"], [])
            self.assertIn("weather", seen)                                        # through the window
            self.assertNotIn("weather", blind)
            self.assertEqual(blind["grid"]["blocked"], seen["grid"]["blocked"])   # the map stays
        oracle = make({"privacy_mode": True}).observe()
        self.assertEqual(oracle["people"]["ann"]["label"], "Ann")                 # oracle: the darkness rule's listing
        self.assertEqual(listed(make({"privacy_mode": True}), oracle)["crate_1"]["type"], "unknown")

    def test_realistic_people_and_pets_are_shapes_and_cannot_be_named(self):
        env = make({"privacy_mode": True}, perception="realistic")
        obs = env.observe()
        self.assertNotIn("ann", obs["people"])
        (key, person), = obs["people"].items()
        self.assertTrue(key.startswith("obj_"))
        self.assertEqual(person["label"], "person")
        (_, pet), = obs["pets"].items()
        self.assertEqual(pet["label"], "animal")
        self.assertEqual(env.realism.inbound({"target": key}, entity_keys=("target",))["target"], "ann")
        with self.assertRaises(ActionError) as caught:
            env.realism.inbound({"target": "ann"}, entity_keys=("target",))
        self.assertEqual(str(caught.exception), BLIND_WHO)
        self.assertEqual((obs["robot"]["camera"], obs["robot"]["hearing"]), ("off", "addressed"))

    def test_every_imaging_switch_gives_the_same_view(self):
        reference = None
        for robot in IMAGING_OFF:
            env = make(robot)
            self.assertFalse(body.imaging(env.world.s), robot)
            obs = env.observe()
            view = (sorted(obs["visible_entities"]), obs["dark"], obs["grid"]["hazards"], "weather" in obs)
            reference = reference or view
            self.assertEqual(view, reference, robot)
        elsewhere = make({"privacy_policy": {"camera_off": ["bathroom"], "when": "always"}}).observe()
        self.assertIn("cup_1", elsewhere["visible_entities"])                     # the policy names another room
        nobody = make({"privacy_policy": {"camera_off": ["living_room"]}}, people={}).observe()
        self.assertIn("cup_1", nobody["visible_entities"])                        # "occupied": nobody is here

    def test_map_fixtures_stay_targetable_with_the_cameras_off(self):
        env = make({"privacy_mode": True})
        state, seen = env.world.s, env.perception
        self.assertEqual({e: seen.visible(state, e) for e in ("switch_1", "table_1", "crate_1", "jar_1", "cup_1")},
                         {"switch_1": True, "table_1": True, "crate_1": True, "jar_1": True, "cup_1": False})
        self.assertNotIn("switch_1", env.observe()["visible_entities"])          # targetable, not listed
        self.assertTrue(env.step("approach", "switch_1")["ok"])
        refused = env.step("approach", "cup_1")
        self.assertEqual((refused["ok"], refused["category"]), (False, "perception"), refused["message"])

    def test_a_scratched_lens_counts_like_a_dirty_one(self):
        key = {"key_1": {"type": "generic_item", "label": "key", "location": "lab", "pos": [2, 2],
                         "size_m": [0.05, 0.02, 0.01], "mass_kg": 0.02}}
        clear, scratched = make(entities=key), make({"camera_clean": 1.0, "lens_scratch": 0.65}, entities=key)
        self.assertAlmostEqual(body.clarity(scratched.world.s), 0.35)
        self.assertTrue(clear.perception.visible(clear.world.s, "key_1"))
        self.assertFalse(scratched.perception.visible(scratched.world.s, "key_1"))
        real = [make(robot, perception="realistic", entities=key) for robot in ({}, {"lens_scratch": 0.65})]
        m = [env.realism.room_context(env.world.s)["m"] for env in real]
        self.assertAlmostEqual(m[1] - m[0], 1.5 * (0.7 - 0.35) / 0.3)            # realism's lens term from the body
        self.assertEqual(real[1].observe()["robot"]["camera"], "obscured")

    def test_the_geometric_camera_applies_the_same_imaging_gate(self):
        for mode in MODES:
            env = make({"privacy_mode": True}, perception=mode, camera="geometric")
            obs = env.observe()
            shown = listed(env, obs)
            self.assertNotIn("cup_1", shown, mode)
            self.assertIn("jar_1", shown)
            self.assertTrue(obs["dark"])
            self.assertIn("lidar", obs)                                           # the depth camera stays on
            for rec in shown.values():
                self.assertIn(rec["view"]["via"], ("depth", "held"))
        env = make({"privacy_mode": True}, perception="realistic", camera="geometric")
        obs = env.observe()
        self.assertTrue(obs["people"])
        for key, person in obs["people"].items():
            self.assertTrue(key.startswith("obj_"))
            self.assertEqual(person["label"], "person")
        with self.assertRaises(ActionError) as caught:
            env.realism.inbound({"target": "ann"}, entity_keys=("target",))
        self.assertEqual(str(caught.exception), BLIND_WHO)


# ----------------------------------------------------------------------
# the power gate (item 31)

class PowerGateTests(ObserverCase):
    def test_powered_down_only_held_things_are_seen_in_both_perception_and_camera_modes(self):
        self.probe()
        for camera_mode in ("room", "geometric"):
            for mode in MODES:
                env = make({"battery": 0.0}, perception=mode, camera=camera_mode)
                env.world.s["messages"] = messages(env.world.s)
                self.assertFalse(body.is_on(env.world.s))
                obs = env.observe()
                where = (camera_mode, mode)
                self.assertEqual(set(listed(env, obs)), {"jar_1"}, where)
                self.assertEqual((obs["people"], obs["pets"], obs["grid"]["hazards"]), ({}, {}, []), where)
                self.assertTrue(obs["dark"], where)
                for key in ("weather", "probe", "sounds", "mechanism_sounds", "lidar"):
                    self.assertNotIn(key, obs, where)
                self.assertEqual(obs["messages"], [], where)
                self.assertEqual(obs["robot"]["power_state"], "off", where)
                self.assertEqual(obs["room_id"], "lab")                           # it still knows where it is
                if mode == "realistic":
                    self.assertEqual((obs["robot"]["battery"], obs["battery"]), (0, 0))
                    self.assertEqual((obs["robot"]["camera"], obs["robot"]["hearing"]), ("off", "off"))
                self.assertFalse(env.perception.visible(env.world.s, "crate_1"), where)
                self.assertTrue(env.perception.visible(env.world.s, "jar_1"), where)

    def test_everything_comes_back_when_the_robot_wakes(self):
        self.probe()
        wake = {"at_s": 0.0, "effect": "robot.battery", "args": {"level": 50.0}}
        for mode in MODES:
            env = make({"battery": 0.0}, perception=mode, events=[wake])
            self.assertNotIn("probe", env.observe())
            self.assertTrue(env.step("wait", seconds=30)["ok"])                  # the only action while off
            self.assertTrue(body.is_on(env.world.s), mode)
            obs = env.observe()
            self.assertIn("crate_1", listed(env, obs))
            self.assertIn("probe", obs)
            self.assertTrue(obs["people"])

    def test_a_worn_thing_counts_as_held(self):
        gloves = {"gloves_1": {"type": "generic_item", "label": "gloves", "location": "robot", "relation": "worn",
                               "size_m": [0.25, 0.12, 0.03]}}
        env = make({"battery": 0.0, "wearing": ["gloves_1"]}, entities=gloves)
        self.assertTrue(env.perception.visible(env.world.s, "gloves_1"))
        self.assertIn("gloves_1", env.observe()["visible_entities"])


# ----------------------------------------------------------------------
# hearing (item 32)

class HearingTests(ObserverCase):
    def texts(self, env):
        env.world.s["messages"] = messages(env.world.s)
        return sorted(m["text"] for m in env.observe()["messages"])

    def test_messages_follow_what_the_microphones_let_through(self):
        everything = ["hey robot", "on the phone", "to Bob", "to the robot"]
        addressed = ["hey robot", "on the phone", "to the robot"]
        for mode in MODES:
            cases = (({}, everything), ({"privacy_mode": True}, addressed),
                     ({"privacy_policy": {"mic_off": ["living_room"], "when": "always"}}, addressed),
                     ({"mic_on": False}, []), ({"sensors": {"mics": False}}, []), ({"battery": 0.0}, []))
            for robot, expected in cases:
                self.assertEqual(self.texts(make(robot, perception=mode)), expected, (mode, robot))

    def test_a_deaf_robot_hears_no_sounds_and_privacy_keeps_the_non_speech_ones(self):
        self.probe()
        for mode in MODES:
            full, addressed, deaf = (make(robot, perception=mode).observe()
                                     for robot in ({}, {"privacy_mode": True}, {"mic_on": False}))
            for obs in (full, addressed):
                self.assertTrue(obs["sounds"] and obs["mechanism_sounds"] and obs["probe"]["sounds"], mode)
            self.assertNotIn("sounds", deaf)
            self.assertNotIn("mechanism_sounds", deaf)
            self.assertEqual(deaf["probe"], {"seen": True})                       # the channel itself stays
            if mode == "realistic":
                self.assertEqual([o["robot"]["hearing"] for o in (full, addressed, deaf)], ["full", "addressed", "off"])

    def test_the_heard_messages_helper(self):
        state = {"robot": {"power_state": "on"}, "messages": messages({})}
        self.assertEqual(len(perception_mod.heard_messages(state)), 5)
        self.assertEqual([m["text"] for m in perception_mod.heard_messages(state, "addressed")],
                         ["to the robot", "on the phone", "hey robot", "far away"])
        self.assertEqual(perception_mod.heard_messages(state, "none"), [])
        obs = {"sounds": [1], "mechanism_sounds": [2], "climate": {"sounds": [3], "room": {}}, "time_s": 0}
        perception_mod.drop_sounds(obs)
        self.assertEqual(obs, {"climate": {"room": {}}, "time_s": 0})


# ----------------------------------------------------------------------
# proprioception (item 33)

class ProprioceptionTests(unittest.TestCase):
    def test_oracle_obs_robot_is_todays_keys_plus_the_registered_visible_fields_exactly(self):
        for camera_mode in ("room", "geometric"):
            env = make(camera=camera_mode)
            env.step("wait", seconds=30)
            robot = env.world.s["robot"]
            obs = env.observe()
            expected = {k for k in proprio.LEGACY_KEYS if k in robot} | {
                n for n, e in body.ROBOT_FIELDS.items() if n in robot and e["tier"] != "hidden" and not n.startswith("_")}
            self.assertEqual(set(obs["robot"]), expected, camera_mode)
            for key, value in obs["robot"].items():
                self.assertEqual(value, robot[key], key)
            self.assertEqual(obs["battery"], round(robot["battery"], 3))
            self.assertFalse(set(obs["robot"]) & HIDDEN)
            self.assertEqual("facing_deg" in obs["robot"], camera_mode == "geometric")
            for key in ("battery", "camera_clean", "faults", "fault_state", "sensors", "arm_health", "power_state"):
                self.assertIn(key, obs["robot"])

    def test_the_battery_estimates_do_not_weigh_what_the_robot_holds(self):
        """Stage-B review (body, high): runtime_min_est and range_m_est were computed at the held load's power, so
        comparing them before and after a pick weighed the item to about 0.2 kg (1.0 kg -> 1220 min, 2.2 kg -> 1180)
        while its realistic mass_kg was the touch bucket "medium" for both."""
        from tests import test_body_core as core_tests
        seen = {}
        for mass in (0.0, 1.0, 2.2, 5.5):
            entities = {"bag": core_tests.thing("bag", mass or 1.0, [0.3, 0.2, 0.3])}
            env = core_tests.make(entities=entities, perception="realistic")
            if mass:
                bag = [h for h, r in env.observe()["visible_entities"].items() if r.get("label") == "bag"][0]
                self.assertTrue(env.step("pick_up", bag)["ok"])
            else:
                env.step("wait", seconds=1)
            own = env.observe()["robot"]
            seen[mass] = (own["battery"], own["runtime_min_est"], own["range_m_est"])
        self.assertEqual(len({v for v in seen.values()}), 1, seen)

    def test_realistic_obs_robot_is_the_robots_own_report(self):
        faults = {"arm_weak_left": {"kind": "arm_weak", "part": "left", "latent": True, "severity": 0.4},
                  "network_down": {"kind": "network_down"}}
        robot = {"battery": 57.6, "camera_clean": 0.7, "fault_state": faults, "localized": False}
        for camera_mode in ("room", "geometric"):
            env = make(robot, perception="realistic", camera=camera_mode)
            obs = env.observe()
            own = obs["robot"]
            self.assertEqual((own["battery"], obs["battery"]), (58, 58))
            self.assertIsInstance(own["battery"], int)
            self.assertEqual((own["battery_state"], own["camera"], own["network"], own["battery_temp"]),
                             ("ok", "smudged", "offline", "normal"))
            self.assertEqual(own["runtime_min_est"] % 5, 0)
            self.assertEqual(own["range_m_est"] % 50, 0)
            self.assertEqual(own["arms"], {"left": "ok", "right": "ok"})          # the weak arm is still latent
            self.assertEqual([f["kind"] for f in own["faults"]], ["network_down"])
            self.assertIsNone(own["pos"])                                         # unlocalized
            self.assertTrue(own["hands"]["right"].startswith("obj_"))
            self.assertFalse(set(own) & (HIDDEN | {"camera_clean", "arm_health", "legs_health", "sensors",
                                                   "fault_state"}))
            self.assertFalse([k for k in own if k.startswith("_")])
            leaks = []
            audit.check_robot_obs(env, obs, leaks, "t")
            self.assertEqual(leaks, [], camera_mode)
            if camera_mode == "geometric":
                self.assertIsNone(obs["camera"]["pose"]["cell"])                  # mirrors obs.robot.pos
                self.assertIn("facing_deg", own)

    def test_without_the_proprioception_sensor_only_pose_and_hands_remain(self):
        for mode in MODES:
            env = make(perception=mode, sensors=["symbolic", "language", "audio"])
            obs = env.observe()
            self.assertEqual(set(obs["robot"]), {"pos", "posture", "hands"}, mode)
            self.assertIn("battery", obs)

    @unittest.skipUnless(hasattr(body, "relocalize"), "look relocalizes from BODY-CORE on")
    def test_a_look_in_a_lit_room_gives_the_position_back(self):
        lost = {"fault_state": {"localization_lost": {"kind": "localization_lost"}}}
        for camera_mode in ("room", "geometric"):
            env = make(lost, perception="realistic", camera=camera_mode)
            self.assertIsNone(env.observe()["robot"]["pos"])
            self.assertTrue(env.step("look")["ok"])
            obs = env.observe()
            self.assertEqual((obs["robot"]["pos"], obs["robot"]["localized"]), ([2, 3], True), camera_mode)
            if camera_mode == "geometric":
                self.assertEqual(obs["camera"]["pose"]["cell"], [2, 3])
        blinds = {"window_1": {"type": "window", "location": "lab", "pos": [6, ROWS - 1], "covered": 1.0}}
        dark = make(lost, perception="realistic", light=False, entities=blinds)
        self.assertTrue(dark.observe()["dark"])
        dark.step("look")
        self.assertIsNone(dark.observe()["robot"]["pos"])                         # not in the dark

    def test_the_camera_pose_cell_mirrors_the_position_when_localized(self):
        for mode in MODES:
            obs = make(perception=mode, camera="geometric").observe()
            self.assertEqual(obs["camera"]["pose"]["cell"], obs["robot"]["pos"])
            self.assertEqual(obs["robot"]["pos"], [2, 3])


# ----------------------------------------------------------------------
# the perception audit's robot check (item 34)

class AuditRobotCheckTests(unittest.TestCase):
    def leaks(self, env, obs):
        found = []
        audit.check_robot_obs(env, obs, found, "t")
        return found

    def test_clean_realistic_observations_pass_in_every_body_state(self):
        latent = {"gripper_slip_left": {"kind": "gripper_slip", "part": "left", "latent": True},
                  "battery_degraded": {"kind": "battery_degraded", "latent": True}}
        for robot in ({}, {"privacy_mode": True}, {"battery": 0.0}, {"mic_on": False}, {"localized": False},
                      {"fault_state": latent}, {"sensors": {"camera": False, "mics": False}}):
            for camera_mode in ("room", "geometric"):
                env = make(robot, perception="realistic", camera=camera_mode)
                env.world.s["messages"] = messages(env.world.s)
                self.assertEqual(self.leaks(env, env.observe()), [], (robot, camera_mode))

    def test_the_check_finds_each_leak_class(self):
        env = make({"fault_state": {"arm_weak_left": {"kind": "arm_weak", "part": "left", "latent": True}},
                    "localized": False}, perception="realistic")
        clean = env.observe()
        cases = {
            "hidden field": lambda o: o["robot"].update(battery_health=1.0),
            "unregistered": lambda o: o["robot"].update(secret_mood="calm"),
            "float battery": lambda o: o["robot"].update(battery=57.6),
            "battery differs": lambda o: o.update(battery=12),
            "position": lambda o: o["robot"].update(pos=[2, 3]),
            "latent fault listed": lambda o: o["robot"]["faults"].append(
                {"kind": "arm_weak", "part": "left", "time_s": 0.0, "logged": False}),
            "latent fault named": lambda o: o.update(nav={"note": "arm_weak_left"}),
            "observer field": lambda o: o.update(nav={"energy_wh_used": 3.0}),
        }
        for name, change in cases.items():
            obs = copy.deepcopy(clean)
            change(obs)
            found = self.leaks(env, obs)
            self.assertTrue(found and all(kind == "robot" for kind, _, _ in found), name)
        deaf = make({"mic_on": False}, perception="realistic")
        obs = deaf.observe()
        obs["messages"] = [{"text": "hello", "to": "robot"}]
        obs["sounds"] = [{"kind": "beep"}]
        self.assertEqual(len(self.leaks(deaf, obs)), 2)
        private = make({"privacy_mode": True}, perception="realistic")
        obs = private.observe()
        obs["messages"] = [{"text": "to Bob", "to": "bob"}]
        obs["visible_entities"][private.realism.handles.handle("cup_1")] = {"label": "cup"}
        obs["grid"]["hazards"] = [{"cell": [9, 1], "kind": "wet", "source": "lab"}]
        self.assertEqual(len(self.leaks(private, obs)), 3)
        off = make({"battery": 0.0}, perception="realistic")
        obs = off.observe()
        obs["visible_entities"][off.realism.handles.handle("crate_1")] = {"label": "dark shape"}
        obs["people"] = {"ann": {"label": "Ann"}}
        self.assertEqual(len(self.leaks(off, obs)), 2)


# ----------------------------------------------------------------------
# determinism and budget

class DeterminismAndBudgetTests(unittest.TestCase):
    EVENTS = [{"at_s": 30.0, "changes": {"robot.privacy_mode": True}},
              {"at_s": 60.0, "effect": "robot.fault", "args": {"kind": "mic_failed"}},
              {"at_s": 90.0, "changes": {"robot.privacy_mode": False}},
              {"at_s": 120.0, "effect": "robot.fault", "args": {"kind": "camera_dirty", "severity": 0.5}}]

    def episode(self):
        env = make(perception="realistic", events=self.EVENTS)
        seen = [digest(env.observe())]
        for _ in range(5):
            env.step("wait", seconds=30)
            seen.append(digest(env.observe()))
        return env, seen

    def test_two_runs_and_a_replay_give_the_same_observations(self):
        env, first = self.episode()
        _, second = self.episode()
        self.assertEqual(first, second)
        self.assertEqual(len(set(first)), len(first))                             # every change was observed
        own = env.observe()["robot"]
        self.assertEqual((own["camera"], own["hearing"]), ("dirty", "off"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "replay.json"
            env.save_replay(path)
            replayed = HouseEnv.replay(path)
        self.assertEqual(digest(replayed.observe()), digest(env.observe()))

    def test_proprioception_stays_within_three_times_its_budget(self):
        faults = {"network_down": {"kind": "network_down"},
                  "arm_weak_left": {"kind": "arm_weak", "part": "left", "latent": True}}
        env = make({"fault_state": faults})
        state = env.world.s
        for realistic in (False, True):
            best = float("inf")
            for _ in range(5):
                start = time.perf_counter()
                for _ in range(200):
                    proprio.render(env, state, realistic)
                best = min(best, (time.perf_counter() - start) / 200)
            self.assertLess(best * 1000.0, 3 * 0.05, "realistic" if realistic else "oracle")


# ----------------------------------------------------------------------
# B5 merge gate: BODY-CORE's verbs and setup ops and this package's observation give one picture of the sensors

class MergeSeamTests(unittest.TestCase):
    """The seams between BODY-CORE (core look / inspect / listen / ask / speak, the setup ops robot and robot_fault)
    and BODY-FAULTS (the observation's sensor gates, proprioception, realism's shapes), realistic perception, both
    camera modes."""

    BLINDS = {"window_1": {"type": "window", "location": "lab", "pos": [6, ROWS - 1], "covered": 1.0}}

    def built(self, ops, camera="room", **kw):
        if camera == "geometric":
            kw.setdefault("facing", 0.0)
        cfg = config(**kw)
        apply_setup(cfg, ops, default_registry(), 0, {"catalog_id": "T-b5-merge"})
        return HouseEnv(cfg, seed=0, perception="realistic", camera=camera)

    def test_a_shape_is_inspected_in_the_dark_but_not_with_the_cameras_off(self):
        # docs/PERCEPTION.md: inspecting a person's shape gives only its label in an unlit room; with the cameras off
        # core inspect refuses first (TIER1_INTEGRATION 1.15), whatever the shape's handle
        for camera_mode in ("room", "geometric"):
            dark = make(perception="realistic", camera=camera_mode, light=False, entities=self.BLINDS)
            shape, = dark.observe()["people"]
            result = dark.step("inspect", shape)
            self.assertTrue(result["ok"], result["message"])
            self.assertEqual(json.loads(result["message"])["label"], "person")
            off = self.built([{"op": "robot", "sensors": {"camera_on": False}}], camera=camera_mode)
            shape, = off.observe()["people"]
            result = off.step("inspect", shape)
            self.assertEqual((result["ok"], result["message"], result["category"]),
                             (False, IMAGING_OFF_TEXT, "perception"), camera_mode)

    def test_setup_sensors_and_faults_reach_the_verbs_and_the_observation_alike(self):
        for camera_mode in ("room", "geometric"):
            env = self.built([{"op": "robot", "sensors": {"mic_on": False}},
                              {"op": "robot_fault", "kind": "speaker_failed", "latent": True},
                              {"op": "robot_fault", "kind": "localization_lost"}], camera=camera_mode)
            state = env.world.s
            state["messages"].extend(messages(state))
            obs = env.observe()
            self.assertEqual((obs["robot"]["hearing"], obs["robot"]["pos"], obs.get("messages")), ("off", None, []))
            result = env.step("listen")
            self.assertEqual((result["ok"], result["message"]), (False, MIC_OFF_TEXT))
            self.assertTrue(env.step("speak", text="Hello Ann")["ok"])
            # deaf, the robot hears no echo: the inaudible speech leaves the latent speaker fault unknown to it
            self.assertFalse(state["robot"]["fault_state"]["speaker_failed"]["detected"])
            self.assertNotIn("speaker_failed", [f["kind"] for f in env.observe()["robot"]["faults"]])
            self.assertIn("Relocalized", env.step("look")["message"])
            obs = env.observe()
            self.assertEqual(obs["robot"]["pos"], [2, 3], camera_mode)
            if camera_mode == "geometric":
                self.assertEqual(obs["camera"]["pose"]["cell"], [2, 3])
            private = self.built([{"op": "robot", "props": {"privacy_mode": True}}], camera=camera_mode)
            state = private.world.s
            state["messages"].extend(messages(state))
            obs = private.observe()
            self.assertEqual((obs["robot"]["camera"], obs["robot"]["hearing"]), ("off", "addressed"))
            self.assertEqual(sorted(m["text"] for m in obs["messages"]), ["hey robot", "on the phone", "to the robot"])
            heard = json.loads(private.step("listen")["message"])
            self.assertEqual(sorted(m["text"] for m in heard), ["hey robot", "to the robot"])
            shape, = obs["people"]
            self.assertEqual(private.step("ask", "ann", topic="anything")["message"], BLIND_WHO)
            self.assertTrue(private.step("ask", shape, topic="anything")["ok"])       # an answer is addressed speech


if __name__ == "__main__":
    unittest.main()
