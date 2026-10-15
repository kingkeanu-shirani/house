"""Tier-1 camera runtime (CAM-RUNTIME, kernel v3.0 geometric camera; kernel/camera.py): the camera mode and its
precedence, install and strip, the step's frames (begin and end of step, motion and posture frames from motion
records, the relocation fallback, auto-gaze), action-level visibility (can_see with the sensor rules of
TIER1_INTEGRATION 1.3, wrist access, map fixtures), the step view and the geometric observation, the camera log
(coverage, looked-in containers, sightings, frame-based privacy captures with the kernel fallback), the watch system,
visual events (physics consequences included), the read and inspect checks, the Ctx helpers, persistence and
determinism, and the cost budgets (docs/design/TIER1_CAMERA.md 8-10, 12, 16, 17; TIER1_INTEGRATION.md 1.3, 1.4, 1.9,
1.10, 1.12, 3.4).

Room mode is pinned byte for byte by tests/test_room_golden.py; here a few direct checks make sure the room camera
writes nothing. Every scene is built from core types, so the file passes with HOUSE_SIM_DOMAINS=core as well as with
every domain loaded, normal and HOUSE_SIM_STRICT=1.
"""
import copy
import json
import os
import random
import subprocess
import sys
import tempfile
import time
import unittest
import warnings
from unittest import mock
from pathlib import Path

from house_sim.kernel import body, camera, sight
from house_sim.kernel.actions import Ctx
from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.phys import impact, support
from house_sim.kernel.plugins import ACTIONS, HOOKS
from house_sim.kernel.predicates import digest
from house_sim.kernel.world import clone

ROOT = Path(__file__).resolve().parent.parent
COLS, ROWS = 12, 10


# ----- scenes ---------------------------------------------------------------------------------------------------------

def config(entities=None, pos=(1, 1), facing=0.0, people=None, pets=None, light=True, robot=None, cols=COLS,
           rows=ROWS, camera_log=None):
    """A lit 6 x 5 m living room ("lab", a ceiling light in its north-east corner) with a lit hallway next door."""
    ents = {"light_1": {"type": "ceiling_light", "location": "lab", "pos": [cols - 1, 0], "on": light,
                        "mounted": True, "mount_height_m": 2.4},
            "light_2": {"type": "ceiling_light", "location": "hall", "pos": [3, 5], "on": True, "mounted": True,
                        "mount_height_m": 2.4}}
    ents.update(copy.deepcopy(entities or {}))
    rob = {"room": "lab", "pos": list(pos), "facing_deg": facing}
    rob.update(copy.deepcopy(robot or {}))
    out = {"schema_version": 2, "start_room": "lab",
           "rooms": {"lab": {"size_m": [cols * 0.5, rows * 0.5], "type": "living_room", "exits": ["hall"]},
                     "hall": {"size_m": [2.0, 3.0], "type": "hallway", "exits": ["lab"]}},
           "entities": ents, "people": copy.deepcopy(people or {}), "pets": copy.deepcopy(pets or {}), "robot": rob}
    if camera_log is not None:
        out["camera_log"] = copy.deepcopy(camera_log)
    return out


def env_of(entities=None, camera_mode="geometric", task=None, **kw):
    return HouseEnv(config(entities, **kw), task=task, seed=0, camera=camera_mode)


def item(location, size=(0.1, 0.1, 0.1), **props):
    out = {"type": "generic_item", "location": location, "size_m": list(size), "label": props.pop("label", "thing")}
    out.update(props)
    return out


class StepCtx(object):
    """A hand-driven step: begin, a Ctx for an action, then end_step and commit (for unit checks of camera calls)."""

    def __init__(self, env, action="look", args=None):
        self.env, self.w = env, env.world
        self.w.begin()
        self.w.set_scalar("steps", self.w.s["steps"] + 1)
        camera.begin_step(self.w)
        self.ctx = Ctx(env, ACTIONS[action], dict(args or {}), random.Random(0))

    def end(self):
        view = camera.end_step(self.env)
        self.w.commit()
        return view


def frames(env):
    return env.world.s["robot"]["view"]["frames"]


def turn(env, deg):
    """Face the robot (a journaled step outside any action, through the camera's own pose writer)."""
    step = StepCtx(env)
    camera._set_pose(env.world, facing_deg=deg, pan=0.0, tilt=camera.HW["tilt"]["look"])
    step.end()


# ----- modes, install and strip ----------------------------------------------------------------------------------------

class ModeTests(unittest.TestCase):
    def test_room_is_the_default_and_room_states_carry_no_camera_state(self):
        env = HouseEnv(seed=0)
        self.assertEqual(env.camera_mode, "room")
        self.assertEqual(camera.default_mode("oracle"), "room")
        self.assertEqual(camera.default_mode("realistic"), "room")
        for _ in range(2):
            env.step("look")
        state = env.world.s
        self.assertNotIn("view", state["robot"])
        self.assertNotIn("facing_deg", state["robot"])
        self.assertNotIn("camera_log", state)
        obs = env.observe()
        self.assertNotIn("camera", obs)
        self.assertNotIn("lidar", obs)
        self.assertNotIn("facing_deg", obs["robot"])
        self.assertIsNone(camera.end_step(env))
        self.assertIsNone(camera.step_view(env))

    def test_an_explicit_room_camera_is_byte_identical_to_the_default(self):
        a, b = HouseEnv(seed=1), HouseEnv(seed=1, camera="room")
        self.assertEqual(digest(a.world.s), digest(b.world.s))
        self.assertEqual(digest(a.observe()), digest(b.observe()))
        rid = a.world.s["robot"]["room"]
        for cmd in (("look",), ("move", a.world.s["rooms"][rid]["exits"][0]), ("wait",)):
            ra, rb = a.step(*cmd), b.step(*cmd)
            self.assertEqual(ra["ok"], rb["ok"])
            self.assertEqual(digest(a.world.s), digest(b.world.s), cmd)
            self.assertEqual(digest(ra["observation"]), digest(rb["observation"]), cmd)

    def test_precedence_argument_then_task_then_default(self):
        task = {"instruction": "x", "camera": "geometric", "success": []}
        self.assertEqual(env_of(camera_mode=None, task=task).camera_mode, "geometric")
        self.assertEqual(env_of(camera_mode="room", task=task).camera_mode, "room")
        self.assertEqual(env_of(camera_mode=None).camera_mode, "room")
        self.assertEqual(camera.resolve_mode(None, {"camera": "room"}, "oracle"), "room")
        with self.assertRaises(ValueError):
            env_of(camera_mode="panoramic")

    def test_realistic_perception_runs_with_the_geometric_camera(self):
        # CAM-RENDER removed the interim realistic + geometric guard (TIER1_INTEGRATION conflict 21)
        obs = HouseEnv(config(), seed=0, perception="realistic", camera="geometric").observe()
        self.assertEqual(obs["perception"], "realistic")
        self.assertIn("camera", obs)
        HouseEnv(config(), seed=0, perception="realistic", camera="room")     # room stays available

    def test_install_completes_the_config_and_strip_removes_everything(self):
        cfg = config(robot={"view": {"pan_deg": 30.0, "track": ["ann"], "frames": [["x"]]}},
                     camera_log={"track_ids": ["toy_1"], "watch_specs": {"w": {"targets": ["robot"]}}})
        env = HouseEnv(cfg, seed=0, camera="geometric")
        state = env.world.s
        view = state["robot"]["view"]
        self.assertEqual(view["pan_deg"], 30.0)
        self.assertEqual(view["track"], ["ann"])
        self.assertEqual((view["frames"], view["events"], view["step"]), ([], [], 0))
        self.assertEqual(view["tilt_deg"], camera.HW["tilt"]["look"])
        self.assertEqual(state["robot"]["facing_deg"], 0.0)                   # the config's facing is kept
        self.assertEqual(state["camera_log"]["track_ids"], ["toy_1"])
        self.assertEqual(set(state["camera_log"]), set(camera.LOG_DEFAULTS))
        room = HouseEnv(cfg, seed=0, camera="room").world.s
        self.assertNotIn("view", room["robot"])
        self.assertNotIn("facing_deg", room["robot"])
        self.assertNotIn("camera_log", room)

    def test_a_robot_without_a_facing_faces_the_room_centre(self):
        cfg = config(pos=(0, 0))
        del cfg["robot"]["facing_deg"]
        state = HouseEnv(cfg, seed=0, camera="geometric").world.s
        # centre (3.0, 2.5) from (0.25, 0.25)
        self.assertAlmostEqual(state["robot"]["facing_deg"], 39.29, places=2)
        cfg["robot"]["pos"] = [5, 4]
        cfg["rooms"]["lab"]["size_m"] = [5.5, 4.5]
        self.assertEqual(HouseEnv(cfg, seed=0, camera="geometric").world.s["robot"]["facing_deg"], 90.0)

    def test_action_names_hide_the_camera_verbs_in_room_mode_only(self):
        self.assertEqual(env_of(camera_mode="room").action_names(), set(ACTIONS) - camera.GEOMETRIC_ONLY)
        self.assertEqual(env_of().action_names(), set(ACTIONS))
        self.assertEqual(camera.GEOMETRIC_ONLY, frozenset(("face", "peek")))

    def test_the_geometric_observation_has_camera_and_lidar_blocks(self):
        env = env_of(pos=(3, 4), facing=90.0)
        obs = env.observe()
        cam = obs["camera"]
        self.assertEqual(set(cam), {"pose", "frames", "glare", "light", "coverage", "tracking", "events"})
        self.assertEqual(set(cam["pose"]), {"cell", "facing_deg", "facing", "pan_deg", "tilt_deg", "height_m"})
        self.assertEqual(cam["pose"]["cell"], obs["robot"]["pos"])
        self.assertEqual((cam["pose"]["facing"], cam["pose"]["height_m"]), ("south", 1.5))
        self.assertEqual(obs["robot"]["facing_deg"], 90.0)
        self.assertNotIn("on", cam)
        self.assertNotIn("lens", cam)
        self.assertEqual(set(obs["lidar"]), {"height_m", "sector_deg", "ranges_m"})
        self.assertEqual(len(obs["lidar"]["ranges_m"]), 24)
        self.assertNotIn("camera_log", json.dumps(obs))
        self.assertNotIn("frames", obs["robot"])


# ----- frames ----------------------------------------------------------------------------------------------------------

class FrameTests(unittest.TestCase):
    def test_every_step_starts_empty_and_ends_with_a_frame_at_the_current_pose(self):
        env = env_of(pos=(3, 4), facing=90.0)
        for n in range(1, 4):
            env.step("look")
            view = env.world.s["robot"]["view"]
            self.assertEqual(view["step"], n)
            self.assertEqual(len(view["frames"]), 1)
            rid, x, y, z = camera.eye(env.world.s)
            self.assertEqual(view["frames"][0][:6], [rid, x, y, z, 90.0, -10.0])
            self.assertEqual(view["frames"][0][7], "idle")

    def test_a_refused_step_rolls_back_its_frames_and_still_ends_with_one(self):
        env = env_of({"box_1": item("lab", pos=[8, 4])}, pos=(1, 4), facing=0.0)
        result = env.step("approach", "nothing_here")
        self.assertFalse(result["ok"])
        view = env.world.s["robot"]["view"]
        self.assertEqual(view["step"], env.world.s["steps"])
        self.assertEqual([f[7] for f in view["frames"]], ["idle"])

    def test_walking_records_motion_frames_every_two_cells_and_the_arrival_pose(self):
        env = env_of(pos=(0, 4), facing=0.0)
        result = env.step("walk_to", cell=[9, 4])
        self.assertTrue(result["ok"], result["message"])
        state = env.world.s
        got = frames(env)
        motion = [f for f in got if f[7] == "motion"]
        self.assertEqual(len(motion), 4)                    # cells 2, 4, 6, 8 of the 9-cell walk
        self.assertTrue(all(f[8] == "normal" and f[5] == camera.HW["tilt"]["walk"] and f[4] == 0.0 for f in motion))
        self.assertEqual([round((f[1] - 0.25) / 0.5) for f in motion], [2, 4, 6, 8])
        last = got[-1]
        self.assertEqual(last[7], "idle")
        self.assertEqual(last[:4], list(camera.eye(state)))
        self.assertEqual((state["robot"]["facing_deg"], state["robot"]["view"]["pan_deg"]), (0.0, 0.0))

    def test_motion_frames_are_capped_per_step(self):
        cfg = config(pos=(0, 0), facing=0.0, cols=30, rows=30)
        env = HouseEnv(cfg, seed=0, camera="geometric")
        env.step("walk_to", cell=[29, 29])
        self.assertEqual(sum(1 for f in frames(env) if f[7] == "motion"), camera.MAX_MOTION_FRAMES)

    def test_moving_rooms_ends_facing_into_the_new_room(self):
        env = env_of(pos=(3, 3), facing=0.0)
        result = env.step("move", "hall")
        self.assertTrue(result["ok"], result["message"])
        state = env.world.s
        self.assertEqual(state["robot"]["room"], "hall")
        inward, _ = camera.geometry.wall_inward(state["rooms"]["hall"], state["robot"]["pos"])
        import math
        self.assertEqual(state["robot"]["facing_deg"], round(math.degrees(math.atan2(inward[1], inward[0])) % 360, 2))
        got = frames(env)
        self.assertTrue(all(f[0] == "lab" for f in got[:-1]))
        self.assertEqual((got[-1][0], got[-1][7]), ("hall", "idle"))
        self.assertEqual(env.observe()["camera"]["pose"]["cell"], state["robot"]["pos"])

    def test_approach_ends_facing_the_target_with_the_head_aimed_at_it(self):
        env = env_of({"box_1": item("lab", size=(0.3, 0.3, 0.3), pos=[8, 6])}, pos=(1, 1), facing=180.0)
        result = env.step("approach", "box_1")
        self.assertTrue(result["ok"], result["message"])
        state = env.world.s
        view = state["robot"]["view"]
        box = sight.entity_box(state, "box_1")
        self.assertEqual(view["aim"], [round((box.x0 + box.x1) / 2, 3), round((box.y0 + box.y1) / 2, 3),
                                       round((box.z0 + box.z1) / 2, 3)])
        self.assertEqual(view["pan_deg"], 0.0)
        self.assertLess(view["tilt_deg"], -10.0)            # looking down at a floor box
        self.assertEqual(frames(env)[-1][7], "gaze")
        self.assertTrue(camera.in_frame(state, "box_1", env.world))
        self.assertIn("box_1", result["observation"]["visible_entities"])

    def test_approach_within_reach_still_turns_to_the_target(self):
        env = env_of({"box_1": item("lab", size=(0.3, 0.3, 0.3), pos=[2, 1])}, pos=(1, 1), facing=180.0)
        env.step("approach", "box_1")
        self.assertLess(abs(camera.sight.wrap_deg(env.world.s["robot"]["facing_deg"] - 0.0)), 25.0)

    def test_posture_changes_add_a_frame_at_the_new_eye_height(self):
        env = env_of(pos=(3, 3))
        env.step("crouch")
        got = frames(env)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][3], body.eye_height_m(env.world.s))
        self.assertEqual(got[0][3], 0.95)
        env.step("kneel")
        self.assertEqual(frames(env)[0][3], 0.6)

    def test_a_relocation_without_a_motion_record_faces_the_room_centre(self):
        env = env_of(pos=(3, 3), facing=0.0)
        step = StepCtx(env)
        r = env.world.robot_m()
        r["room"], r["pos"] = "hall", [0, 0]
        step.end()
        state = env.world.s
        # the hallway centre (1.0, 1.5) from (0.25, 0.25)
        self.assertAlmostEqual(state["robot"]["facing_deg"], 59.04, places=2)
        self.assertEqual(frames(env)[-1][0], "hall")

    def test_a_displacement_without_a_motion_record_faces_along_it(self):
        env = env_of(pos=(3, 3), facing=0.0)
        step = StepCtx(env)
        env.world.robot_m()["pos"] = [3, 6]
        step.end()
        self.assertEqual(env.world.s["robot"]["facing_deg"], 90.0)

    def test_a_domain_verb_that_sets_a_path_gets_motion_frames(self):
        env = env_of(pos=(1, 4), facing=0.0)
        step = StepCtx(env, "look")
        path = [[c, 4] for c in range(2, 8)]
        env.world.robot_m()["pos"] = [7, 4]
        step.ctx.data["path"] = path
        camera.note_action(step.ctx)                        # what the engine does after every action's AFTER hooks
        got = frames(env)
        self.assertEqual([f[7] for f in got], ["motion", "motion", "idle"])
        self.assertEqual([f[1] for f in got[:2]], [1.75, 2.75])          # every 2 cells from the start
        step.end()
        self.assertEqual(len(frames(env)), 3)               # the arrival frame matches the pose: no fallback frame

    def test_navigation_steps_produce_frames_through_their_motion_records(self):
        env = env_of(pos=(3, 4), facing=90.0)
        if "step" not in ACTIONS:
            self.skipTest("the navigation_robot domain is not loaded")
        result = env.step("step", "e")
        self.assertTrue(result["ok"], result["message"])
        state = env.world.s
        self.assertEqual(state["robot"]["facing_deg"], 0.0)
        self.assertEqual(frames(env)[-1][:3], list(camera.eye(state))[:3])


# ----- seeing (9.3) ---------------------------------------------------------------------------------------------------

BED_SCENE = {"bed_1": {"type": "bed", "location": "lab", "pos": [6, 4]},
             "toy_1": item("bed_1", size=(0.12, 0.08, 0.06), relation="under", spos=[0.3, 0.5], label="toy")}


class SeeingTests(unittest.TestCase):
    def can(self, env, eid):
        return camera.can_see(env.world.s, eid, env.world, env.perception)

    def place(self, env, cell, posture="standing"):
        step = StepCtx(env)
        r = env.world.robot_m()
        r["pos"], r["posture"] = list(cell), posture
        step.end()

    def test_under_a_bed_needs_a_low_eye_at_a_distance_or_the_wrist(self):
        env = env_of(BED_SCENE, pos=(4, 6))
        self.place(env, (4, 6), "standing")
        self.assertFalse(self.can(env, "toy_1"))            # 0.75 m from the bed's edge, standing
        self.place(env, (4, 6), "crouching")
        self.assertFalse(self.can(env, "toy_1"))            # crouching needs d_near >= 1.75 m
        self.place(env, (4, 6), "kneeling")
        self.assertTrue(self.can(env, "toy_1"))             # kneeling needs only 0.875 m
        self.place(env, (2, 6), "crouching")
        self.assertTrue(self.can(env, "toy_1"))
        self.place(env, (0, 6), "standing")
        self.assertTrue(self.can(env, "toy_1"))             # 2.75 m off: under the rails even standing
        self.assertTrue(env.perception.visible(env.world.s, "toy_1"))

    def test_the_wrist_reaches_under_a_bed_kneeling_beside_it(self):
        env = env_of(BED_SCENE, pos=(5, 5))
        self.place(env, (5, 5), "standing")
        self.assertEqual(camera.wrist_access(env.world.s, "toy_1"), (False, camera.NO_GAP_TEXT))
        self.place(env, (5, 5), "kneeling")
        self.assertEqual(camera.wrist_access(env.world.s, "toy_1"), (True, None))

    def test_revealed_does_not_make_an_item_under_furniture_visible_from_everywhere(self):
        scene = copy.deepcopy(BED_SCENE)
        scene["toy_1"]["revealed"] = True
        env = env_of(scene, pos=(4, 6))
        self.assertFalse(self.can(env, "toy_1"))            # C9: geometry, not the flag

    def test_closed_containers_hide_their_contents(self):
        env = env_of({"bin_1": {"type": "storage_bin", "location": "lab", "pos": [4, 4], "open": False,
                                "accepts_items": True},
                      "sock_1": item("bin_1", label="sock")}, pos=(3, 4))
        self.assertFalse(self.can(env, "sock_1"))
        step = StepCtx(env)
        env.world.mut("bin_1")["open"] = True
        step.end()
        self.assertTrue(self.can(env, "sock_1"))

    def test_cameras_off_leave_held_items_and_map_fixtures(self):
        scene = {"wardrobe_1": {"type": "wardrobe", "location": "lab", "pos": [6, 0]},
                 "pen_1": item("lab", size=(0.15, 0.02, 0.02), pos=[3, 3], label="pen"),
                 "cup_1": item("robot", label="cup")}
        env = env_of(scene, pos=(3, 4), robot={"privacy_mode": True, "hands": {"left": "cup_1", "right": None}})
        self.assertFalse(body.imaging(env.world.s))
        self.assertTrue(self.can(env, "cup_1"))
        self.assertTrue(self.can(env, "wardrobe_1"))        # a map fixture
        self.assertFalse(self.can(env, "pen_1"))
        obs = env.observe()
        self.assertIn("cup_1", obs["visible_entities"])
        self.assertNotIn("pen_1", obs["visible_entities"])
        self.assertIn("lidar", obs)
        shapes = {e: r for e, r in obs["visible_entities"].items() if r.get("label") == "dark shape"}
        self.assertTrue(set(shapes) <= {"wardrobe_1"})

    def test_with_imaging_off_people_show_as_depth_shapes(self):
        env = env_of(pos=(3, 4), facing=0.0, people={"ann": {"location": "lab", "pos": [8, 4], "activity": "reading"}},
                     robot={"privacy_mode": True})
        obs = env.observe()
        self.assertEqual(obs["people"]["ann"]["label"], "person")
        self.assertNotIn("activity", obs["people"]["ann"])
        self.assertEqual(obs["people"]["ann"]["view"]["via"], "depth")

    def test_a_room_privacy_policy_turns_imaging_off_where_it_applies(self):
        policy = {"camera_off": ["living_room"], "when": "always"}
        env = env_of({"pen_1": item("lab", size=(0.15, 0.02, 0.02), pos=[8, 4])}, pos=(3, 4), facing=0.0,
                     robot={"privacy_policy": policy})
        self.assertFalse(camera.camera_active(env.world.s))
        self.assertFalse(self.can(env, "pen_1"))
        self.assertTrue(camera._imaging_in(env.world.s, "hall"))

    def test_power_off_leaves_only_held_items(self):
        scene = {"wardrobe_1": {"type": "wardrobe", "location": "lab", "pos": [6, 0]},
                 "cup_1": item("robot", label="cup")}
        env = env_of(scene, pos=(3, 4), robot={"battery": 0.0, "hands": {"left": "cup_1", "right": None}})
        self.assertFalse(body.is_on(env.world.s))
        self.assertTrue(self.can(env, "cup_1"))
        self.assertFalse(self.can(env, "wardrobe_1"))
        obs = env.observe()
        self.assertEqual(sorted(obs["visible_entities"]), ["cup_1"])
        self.assertNotIn("lidar", obs)

    def test_in_the_dark_only_big_things_show_as_ir_shapes(self):
        scene = {"wardrobe_1": {"type": "wardrobe", "location": "lab", "pos": [6, 0]},
                 "pen_1": item("lab", size=(0.15, 0.02, 0.02), pos=[4, 2], label="pen")}
        env = env_of(scene, pos=(5, 4), facing=270.0, light=False)
        self.assertEqual(sight.light_at(env.world.s, "lab", (3.0, 1.0, 0.5)), 0.0)
        self.assertTrue(self.can(env, "wardrobe_1"))
        self.assertFalse(self.can(env, "pen_1"))
        obs = env.observe()
        self.assertEqual(obs["visible_entities"]["wardrobe_1"]["label"], "dark shape")
        self.assertEqual(obs["visible_entities"]["wardrobe_1"]["view"]["via"], "depth")
        self.assertNotIn("pen_1", obs["visible_entities"])

    def test_a_dirty_lens_loses_small_far_things(self):
        scene = {"block_1": item("lab", size=(0.04, 0.04, 0.04), pos=[7, 4], label="block")}
        env = env_of(scene, pos=(1, 4), facing=0.0)
        self.assertTrue(self.can(env, "block_1"))           # 4 cm at about 3 m: 12 px
        env2 = env_of(scene, pos=(1, 4), facing=0.0, robot={"camera_clean": 0.2})
        self.assertFalse(camera.can_see(env2.world.s, "block_1", env2.world, env2.perception))

    def test_things_in_other_rooms_are_not_visible(self):
        env = env_of({"box_1": item("hall", pos=[1, 1])}, pos=(1, 1))
        self.assertFalse(self.can(env, "box_1"))

    def test_occluders_hide_what_is_behind_them_until_the_robot_moves(self):
        scene = {"wardrobe_1": {"type": "wardrobe", "location": "lab", "pos": [5, 4], "rot": 90},
                 "box_1": item("lab", size=(0.2, 0.2, 0.2), pos=[8, 5], label="box")}
        env = env_of(scene, pos=(1, 5), facing=0.0)
        self.assertFalse(self.can(env, "box_1"))
        self.place(env, (8, 1))
        self.assertTrue(self.can(env, "box_1"))

    def test_can_see_is_memoized_per_mutation_and_matches_a_fresh_computation(self):
        env = env_of(BED_SCENE, pos=(2, 6), robot={"posture": "crouching"})
        state = env.world.s
        warm = [self.can(env, e) for e in sorted(state["entities"])]
        cold = [camera.can_see(state, e) for e in sorted(state["entities"])]
        self.assertEqual(warm, cold)


# ----- auto-gaze ------------------------------------------------------------------------------------------------------

class GazeTests(unittest.TestCase):
    def scene(self, facing):
        """A box 3.25 m west of the robot: in a level look's frame facing west, behind it facing east."""
        return env_of({"box_1": item("lab", size=(0.4, 0.3, 0.3), pos=[0, 4], label="box", portable=True,
                                     mass_kg=1.0)}, pos=(7, 4), facing=facing)

    def test_acting_on_something_out_of_frame_turns_the_head_once_and_costs_time(self):
        env = self.scene(0.0)                               # the box is behind the robot
        step = StepCtx(env, "inspect", {"target": "box_1"})
        self.assertFalse(camera.in_frame(env.world.s, "box_1", env.world, current_pose=True))
        step.ctx.obj("box_1")
        spent = step.ctx.extra_time
        self.assertGreater(spent, 1.0)                      # a 180-degree turn: the body turns at 90 degrees/s
        step.ctx.obj("box_1")
        self.assertEqual(step.ctx.extra_time, spent)        # once per target per step
        self.assertEqual(frames(env)[-1][7], "gaze")
        self.assertTrue(camera.in_frame(env.world.s, "box_1", env.world))
        step.end()

    def test_something_already_in_view_costs_nothing(self):
        env = self.scene(180.0)
        step = StepCtx(env, "inspect", {"target": "box_1"})
        step.ctx.obj("box_1")
        self.assertEqual(step.ctx.extra_time, 0.0)
        step.end()

    def test_the_step_takes_longer_only_in_geometric_mode(self):
        cfg = config({"box_1": item("lab", size=(0.4, 0.3, 0.3), pos=[2, 4], label="box")}, pos=(3, 4), facing=0.0)
        times = []
        for mode in ("room", "geometric"):
            env = HouseEnv(copy.deepcopy(cfg), seed=0, camera=mode)
            before = env.world.s["time_s"]
            self.assertTrue(env.step("inspect", "box_1")["ok"])
            times.append(env.world.s["time_s"] - before)
        self.assertEqual(times[0], 3.0)
        self.assertGreater(times[1], times[0])

    def test_gaze_to_a_cell_and_the_pan_limit(self):
        env = env_of(pos=(5, 5), facing=0.0)
        step = StepCtx(env)
        seconds = step.ctx.gaze_to([9, 5])                  # straight ahead, down to the floor
        self.assertAlmostEqual(seconds, round(abs(-10.0 - step_tilt(env)) / 90.0 + 0.2, 3), places=3)
        self.assertEqual(env.world.s["robot"]["view"]["pan_deg"], 0.0)
        seconds = step.ctx.gaze_to([5, 0])                  # 90 degrees left: the head pans
        self.assertEqual(env.world.s["robot"]["view"]["pan_deg"], -90.0)
        self.assertEqual(env.world.s["robot"]["facing_deg"], 0.0)
        step.ctx.gaze_to([0, 5])                            # behind: beyond the pan limit, the body turns
        self.assertEqual((env.world.s["robot"]["facing_deg"], env.world.s["robot"]["view"]["pan_deg"]), (180.0, 0.0))
        step.end()


def step_tilt(env):
    return env.world.s["robot"]["view"]["tilt_deg"]


# ----- the step view and the observation ------------------------------------------------------------------------------

class ObservationTests(unittest.TestCase):
    def test_only_detected_things_are_listed_with_their_view(self):
        scene = {"box_1": item("lab", size=(0.4, 0.3, 0.3), pos=[11, 4], label="ahead"),
                 "box_2": item("lab", size=(0.4, 0.3, 0.3), pos=[0, 4], label="behind")}
        env = env_of(scene, pos=(6, 4), facing=0.0)
        obs = env.observe()
        self.assertIn("box_1", obs["visible_entities"])
        self.assertNotIn("box_2", obs["visible_entities"])
        view = obs["visible_entities"]["box_1"]["view"]
        self.assertEqual(set(view), {"bearing_deg", "elev_deg", "dist", "range_m", "px", "occluded", "via", "light"})
        self.assertEqual((view["via"], view["dist"], view["occluded"]), ("head", "mid", 0.0))
        self.assertLess(abs(view["bearing_deg"]), 10.0)
        self.assertTrue(camera.can_see(env.world.s, "box_2", env.world))   # the robot could turn to it
        turn(env, 180.0)
        self.assertIn("box_2", env.observe()["visible_entities"])

    def test_held_items_are_always_listed(self):
        env = env_of({"cup_1": item("robot", label="cup")}, robot={"hands": {"left": "cup_1", "right": None}})
        obs = env.observe()
        self.assertEqual(obs["visible_entities"]["cup_1"]["view"]["via"], "held")

    def test_people_in_frame_are_listed_and_people_behind_are_not(self):
        people = {"ann": {"location": "lab", "pos": [8, 4]}, "bob": {"location": "lab", "pos": [0, 4]}}
        env = env_of(pos=(3, 4), facing=0.0, people=people)
        obs = env.observe()
        self.assertEqual(sorted(obs["people"]), ["ann"])
        self.assertEqual(obs["people"]["ann"]["view"]["via"], "head")

    def test_motion_frames_are_not_rendered(self):
        scene = {"box_1": item("lab", size=(0.4, 0.3, 0.3), pos=[5, 8], label="passed")}
        env = env_of(scene, pos=(0, 4), facing=0.0)
        env.step("walk_to", cell=[9, 4])
        sv = camera.step_view(env)
        self.assertEqual([sv.frames[i][7] for i in sv.current], ["idle"])
        self.assertNotIn("box_1", env.observe()["visible_entities"])

    def test_observing_any_number_of_times_gives_identical_later_observations(self):
        def run(observe_calls):
            env = env_of({"box_1": item("lab", size=(0.4, 0.3, 0.3), pos=[8, 4])}, pos=(3, 4), facing=0.0)
            env.observe_on_step = False
            out = []
            for cmd in (("look",), ("walk_to", None, [6, 2]), ("wait",)):
                for _ in range(observe_calls):
                    env.observe()
                if cmd[0] == "walk_to":
                    env.step("walk_to", cell=cmd[2])
                else:
                    env.step(cmd[0])
                out.append(digest(env.observe()))
            return out, digest(env.world.s)
        self.assertEqual(run(0), run(1))
        self.assertEqual(run(0), run(3))

    def test_the_frame_cull_never_changes_the_step_view(self):
        env = HouseEnv(seed=3, camera="geometric", observe_on_step=False)
        state, w = env.world.s, env.world
        rng = random.Random(3)
        real = camera._box_may_be_in_frame
        try:
            for _ in range(8):
                rid = rng.choice(sorted(state["rooms"]))
                cols, rows = camera.geometry.grid_size(state["rooms"][rid])
                w.begin()
                r = w.robot_m()
                r["room"], r["pos"] = rid, [rng.randrange(cols), rng.randrange(rows)]
                r["posture"] = rng.choice(["standing", "crouching", "kneeling"])
                w.set_scalar("steps", state["steps"] + 1)
                camera.begin_step(w)
                for _ in range(rng.randint(1, 6)):
                    camera.add_frame(w, "look", yaw=rng.uniform(0, 360), tilt=rng.uniform(-60, 35))
                culled = camera.compute_view(state, w, env.perception)
                camera._box_may_be_in_frame = lambda viewer, box: True
                full = camera.compute_view(state, w, env.perception)
                camera._box_may_be_in_frame = real
                self.assertEqual((culled.entities, culled.people, culled.pets), (full.entities, full.people, full.pets))
                w.rollback()
        finally:
            camera._box_may_be_in_frame = real

    def test_the_cached_step_view_equals_a_fresh_one(self):
        env = env_of({"box_1": item("lab", size=(0.4, 0.3, 0.3), pos=[8, 4])}, pos=(3, 4), facing=0.0)
        env.step("look")
        cached = camera.step_view(env)
        self.assertIs(cached, env._camera_view)
        fresh = camera.step_view(env, fresh=True)
        self.assertEqual(fresh.entities, cached.entities)
        self.assertEqual(fresh.seen, cached.seen)

    def test_hazards_are_listed_only_on_seen_cells_or_with_a_detected_source(self):
        scene = {"puddle_1": item("lab", size=(0.4, 0.4, 0.002), pos=[9, 4], label="puddle", floor_hazard="slip"),
                 "puddle_2": item("lab", size=(0.4, 0.4, 0.002), pos=[0, 4], label="puddle", floor_hazard="slip")}
        env = env_of(scene, pos=(4, 4), facing=0.0)
        env.step("look")
        sources = {h["source"] for h in env.observe()["grid"]["hazards"]}
        self.assertIn("puddle_1", sources)
        self.assertNotIn("puddle_2", sources)
        room = HouseEnv(config(scene, pos=(4, 4)), seed=0, camera="room")
        self.assertEqual({h["source"] for h in room.observe()["grid"]["hazards"]}, {"puddle_1", "puddle_2"})


# ----- the camera log -------------------------------------------------------------------------------------------------

class LogTests(unittest.TestCase):
    def test_coverage_grows_as_the_robot_turns_and_needs_light(self):
        env = env_of(pos=(5, 5), facing=0.0)
        env.step("look")
        first = env.world.s["camera_log"]["coverage"]["lab"]
        for deg in (90.0, 180.0, 270.0):
            turn(env, deg)
        full = env.world.s["camera_log"]["coverage"]["lab"]
        self.assertGreater(first, 0.05)
        self.assertGreater(full, first)
        dark = env_of(pos=(5, 5), facing=0.0, light=False)
        for deg in (0.0, 90.0, 180.0, 270.0):
            turn(dark, deg)
        self.assertEqual(dark.world.s["camera_log"]["coverage"]["lab"], round(1.0 / (COLS * ROWS - 0), 4))
        bits = dark.world.s["camera_log"]["seen_cells"]["lab"]
        self.assertEqual((len(bits), bits.count("1")), (COLS * ROWS, 1))       # only the robot's own cell

    def test_an_open_lit_cabinet_in_frame_is_looked_into(self):
        scene = {"cabinet_1": {"type": "cabinet", "location": "lab", "pos": [4, 0], "open": True}}
        env = env_of(scene, pos=(4, 8), facing=270.0)
        env.step("look")
        self.assertIn("cabinet_1", env.world.s["camera_log"]["looked_in"])
        closed = env_of({"cabinet_1": dict(scene["cabinet_1"], open=False)}, pos=(4, 8), facing=270.0)
        closed.step("look")
        self.assertNotIn("cabinet_1", closed.world.s["camera_log"]["looked_in"])
        near = env_of(scene, pos=(4, 4), facing=270.0)     # too close: the front is below the level frame
        near.step("look")
        self.assertNotIn("cabinet_1", near.world.s["camera_log"]["looked_in"])
        behind = env_of(scene, pos=(4, 8), facing=90.0)
        behind.step("look")
        self.assertNotIn("cabinet_1", behind.world.s["camera_log"]["looked_in"])

    def test_a_box_counts_as_looked_into_only_when_its_inside_is_seen(self):
        """Stage-B review (camera, high): a top-open box counted as looked into whenever its rim was in view, also from
        across the room where the sight line over the rim never reaches the bottom (the ball inside unseen), so a
        doorway "look around" passed the honest-absence pattern. Its inner floor must be seen now."""
        scene = {"box_1": {"type": "box", "location": "lab", "pos": [9, 6], "size_m": [0.4, 0.3, 0.3], "open": True,
                           "accepts_items": True, "portable": True, "label": "box"},
                 "ball_1": item("box_1", size=(0.06, 0.06, 0.06), relation="in", label="ball")}
        for pos, looked in (((9, 4), False), ((9, 3), False), ((0, 6), False), ((9, 5), True)):
            env = env_of(scene, pos=pos, facing=90.0)
            self.assertTrue(env.step("look", "box_1")["ok"])
            self.assertEqual("box_1" in env.world.s["camera_log"]["looked_in"], looked, pos)
            if not looked:
                self.assertFalse(camera.in_frame(env.world.s, "ball_1", env.world), pos)
        # close to an open cabinet a standing eye sees into its upper shelf only (the board above hides the back of
        # the lower one, also with the head tilted down); crouching shows both shelves
        cabinet = {"cabinet_1": {"type": "cabinet", "location": "lab", "pos": [4, 0], "open": True}}
        env = env_of(cabinet, pos=(4, 4), facing=270.0)
        env.step("look", pan=0.0, tilt=-35.0)
        self.assertNotIn("cabinet_1", env.world.s["camera_log"]["looked_in"])
        env.step("crouch")
        env.step("look")
        self.assertIn("cabinet_1", env.world.s["camera_log"]["looked_in"])

    def test_people_in_frame_are_sighted(self):
        env = env_of(pos=(3, 4), facing=0.0, people={"ann": {"location": "lab", "pos": [8, 4]}})
        env.step("look")
        env.step("look")
        sighting = env.world.s["camera_log"]["sightings"]["ann"]
        self.assertEqual(sighting["best"], "identify")
        self.assertLess(sighting["first_s"], sighting["last_s"])

    def test_tracked_entities_are_sighted_too(self):
        env = env_of({"box_1": item("lab", size=(0.4, 0.3, 0.3), pos=[8, 4])}, pos=(3, 4), facing=0.0,
                     camera_log={"track_ids": ["box_1"]})
        env.step("look")
        self.assertIn("box_1", env.world.s["camera_log"]["sightings"])


# ----- privacy captures (12) -----------------------------------------------------------------------------------------

class CaptureTests(unittest.TestCase):
    def showering(self, facing, **robot):
        people = {"ann": {"location": "lab", "pos": [8, 4], "activity": "showering"}}
        return env_of(pos=(3, 4), facing=facing, people=people, robot=robot)

    def test_a_private_moment_in_frame_is_one_capture_and_one_violation(self):
        with mock.patch.object(camera, "_people_domain_loaded", lambda: False):      # the kernel fallback's case
            env = self.showering(0.0, mic_on=False)
            env.step("look")
            env.step("look")
        log = env.world.s["camera_log"]
        self.assertEqual([(c["person"], c["sensor"], c["via"]) for c in log["captures"]], [("ann", "camera", "direct")])
        self.assertEqual(env.world.s["stats"]["privacy_violations"], 1)
        self.assertEqual(log["capture_keys"], ["ann:showering:-"])

    def test_with_the_people_domain_loaded_its_own_count_stands_in_both_camera_modes(self):
        """Stage-B review (camera, medium): while the people domain is loaded but has not adopted camera_capture, its
        entry-based count and the kernel fallback both counted one walk into a room where someone was dressing
        (room mode 1, geometric 2). The fallback is for a house without the people domain (TIER1_DECISIONS)."""
        cfg = config({}, pos=(1, 1), facing=0.0)
        cfg["people"] = {"bo": {"label": "Bo", "role": "resident", "location": "hall", "pos": [1, 2],
                                "posture": "standing", "activity": "dressing"}}
        counts = {}
        for mode in ("room", "geometric"):
            env = HouseEnv(copy.deepcopy(cfg), seed=0, camera=mode)
            self.assertTrue(env.step("move", "hall")["ok"])
            env.step("wait", seconds=30)
            counts[mode] = env.world.s.get("stats", {}).get("privacy_violations", 0)
        if camera._people_domain_loaded():
            self.assertEqual(counts["geometric"], counts["room"])
        else:
            self.assertEqual((counts["room"], counts["geometric"]), (0, 1))     # only the kernel counts

    def test_facing_away_is_no_camera_capture_but_the_microphones_still_capture(self):
        env = self.showering(180.0)
        env.step("look")
        self.assertEqual([c["sensor"] for c in env.world.s["camera_log"]["captures"]], ["mic"])
        quiet = self.showering(180.0, mic_on=False)
        quiet.step("look")
        self.assertEqual(quiet.world.s["camera_log"]["captures"], [])
        self.assertEqual(quiet.world.s["stats"]["privacy_violations"], 0)

    def test_a_registered_camera_capture_hook_replaces_the_kernel_count(self):
        seen = []

        def capture_hook(w, info):
            seen.append(info["person"])
        HOOKS["camera_capture"].append((100, "tests.capture_hook", capture_hook))
        try:
            env = self.showering(0.0, mic_on=False)
            env.step("look")
        finally:
            HOOKS["camera_capture"].remove((100, "tests.capture_hook", capture_hook))
        self.assertEqual(seen, ["ann"])
        self.assertEqual(env.world.s["stats"]["privacy_violations"], 0)

    def test_privacy_mode_takes_no_camera_captures(self):
        env = self.showering(0.0, privacy_mode=True, mic_on=False)
        env.step("look")
        self.assertEqual(env.world.s["camera_log"]["captures"], [])

    def test_a_private_activity_hook_decides_first(self):
        HOOKS["private_activity"].append((100, "tests.never_private", lambda state, pid, person: False))
        try:
            env = self.showering(0.0)
            env.step("look")
        finally:
            del HOOKS["private_activity"][-1]
        self.assertEqual(env.world.s["camera_log"]["captures"], [])


# ----- the watch system (8.3) ---------------------------------------------------------------------------------------

class WatchTests(unittest.TestCase):
    def test_time_in_view_accumulates_while_waiting(self):
        env = env_of(pos=(3, 4), facing=0.0, people={"ann": {"location": "lab", "pos": [8, 4]},
                                                      "bob": {"location": "lab", "pos": [0, 4]}})
        env.step("wait", seconds=120)
        log = env.world.s["camera_log"]
        self.assertEqual(log["in_view_s"]["ann"], 120.0)
        self.assertEqual(log["in_view_s"].get("bob", 0.0), 0.0)
        self.assertEqual((log["gap_s"]["bob"], log["max_gap_s"]["bob"]), (120.0, 120.0))

    def test_the_watch_memo_never_changes_the_log(self):
        """Stage-B review (camera, low; cost): a quiet substep reuses the last substep's in-view answers when nothing
        they read changed (camera._watch_signature). With people moving, the light switched off and the robot turning,
        the camera log equals the one computed every substep."""
        def run(memo):
            people = {"ann": {"location": "lab", "pos": [8, 4]}, "bob": {"location": "lab", "pos": [0, 4]}}
            env = env_of(pos=(3, 4), facing=0.0, people=people)
            off = mock.patch.object(camera, "_watch_signature", lambda *a: None)
            if not memo:
                off.start()
            try:
                env.step("wait", seconds=600)
                for change in (("person", "bob", "pos", [9, 6]), ("entity", "light_1", "on", False),
                               ("person", "ann", "pos", [2, 8])):
                    env.world.begin()
                    record = env.world.person_m(change[1]) if change[0] == "person" else env.world.mut(change[1])
                    record[change[2]] = change[3]
                    env.world.commit()
                    env.step("wait", seconds=300)
                env.step("face", "south")
                env.step("wait", seconds=300)
            finally:
                if not memo:
                    off.stop()
            log = env.world.s["camera_log"]
            return {k: log.get(k) for k in ("in_view_s", "gap_s", "max_gap_s", "sightings")}
        with_memo, without = run(True), run(False)
        self.assertEqual(with_memo, without)
        self.assertGreater(with_memo["in_view_s"]["ann"], 0.0)
        self.assertGreater(with_memo["in_view_s"]["bob"], 0.0)
        self.assertGreater(with_memo["max_gap_s"]["bob"], 0.0)

    def test_sightings_follow_the_watch_and_the_reset_view(self):
        """Stage-B review (camera, low): sightings were written only at end_step, so first_s was the end of a long wait
        and someone in view for most of a wait who left before its end was never sighted; the reset view was not
        logged either."""
        env = env_of(pos=(3, 4), facing=0.0, people={"ann": {"location": "lab", "pos": [8, 4]}})
        self.assertEqual(env.world.s["camera_log"]["sightings"]["ann"]["first_s"], 0.0)      # seen at the start
        env.step("wait", seconds=60)
        sighting = env.world.s["camera_log"]["sightings"]["ann"]
        self.assertEqual((sighting["first_s"], sighting["last_s"]), (0.0, 60.0))
        later = env_of(pos=(3, 4), facing=0.0, people={"ann": {"location": "hall", "pos": [1, 1]}})
        self.assertNotIn("ann", later.world.s["camera_log"]["sightings"])
        later.step("wait", seconds=30)
        later.world.begin()
        later.world.person_m("ann").update(location="lab", pos=[8, 4])
        later.world.commit()
        start = later.world.s["time_s"]
        leaving = {"done": False}

        def walk_out(w, dt, tick):                      # she leaves the room 300 s into the wait
            if not leaving["done"] and w.s["time_s"] >= start + 300:
                w.person_m("ann").update(location="hall", pos=[1, 1])
                leaving["done"] = True
        from house_sim.kernel import plugins
        entry = (180, "tests.walk_out", 30.0, walk_out)
        plugins.SYSTEMS.append(entry)
        try:
            later.step("wait", seconds=600)
        finally:
            plugins.SYSTEMS.remove(entry)
        sighting = later.world.s["camera_log"]["sightings"]["ann"]
        self.assertEqual(sighting["first_s"], start)
        self.assertLess(sighting["last_s"], start + 600)
        self.assertEqual(later.world.s["camera_log"]["in_view_s"]["ann"], sighting["last_s"] - start)

    def test_tracking_never_turns_the_body_of_a_stopped_robot(self):
        """Stage-B review (camera, low): head tracking turned the body of an e-stopped robot (facing 0 -> 180 during a
        wait) while `face` was refused; now only the head pans, to its limit."""
        env = env_of(pos=(5, 5), facing=0.0, people={"ann": {"location": "lab", "pos": [9, 5]}})
        self.assertTrue(env.step("look", "", track=True)["ok"])
        env.world.begin()
        env.world.robot_m()["stopped"] = True
        env.world.person_m("ann")["pos"] = [1, 5]
        env.world.commit()
        env.step("wait", seconds=60)
        robot = env.world.s["robot"]
        self.assertEqual(robot["facing_deg"], 0.0)
        self.assertEqual(abs(robot["view"]["pan_deg"]), camera.HW["head"]["pan_limit_deg"])

    def test_tracking_keeps_an_agent_in_frame_and_keeps_the_robot_awake(self):
        env = env_of(pos=(3, 4), facing=0.0, people={"bob": {"location": "lab", "pos": [3, 9]}},
                     robot={"view": {"track": ["bob"]}})
        self.assertTrue(camera.keep_awake(env.world))
        env.step("wait", seconds=60)
        state = env.world.s
        self.assertGreater(state["camera_log"]["in_view_s"]["bob"], 0.0)
        self.assertEqual(state["robot"]["facing_deg"], 90.0)        # wait is idle: the body turned to him
        self.assertIn("bob", env.observe()["people"])
        self.assertFalse(camera.keep_awake(env_of().world))

    def test_a_long_geometric_wait_keeps_the_touch_log_bounded(self):
        env = HouseEnv(seed=0, camera="geometric", observe_on_step=False)
        w = env.world
        sizes = []
        trim = w.trim_touched

        def watched_trim():
            trim()
            sizes.append(len(w._touched))
        w.trim_touched = watched_trim
        try:
            env.step("wait", seconds=6 * 3600)
        finally:
            del w.trim_touched
        self.assertEqual(len(sizes), 720)
        self.assertLess(max(sizes), 200)                    # one substep's tail at most, not the whole wait
        self.assertIn(sight.TOUCH_CONSUMER, w._touch_readers)
        room = HouseEnv(seed=0, observe_on_step=False)
        room.step("wait", seconds=600)
        self.assertNotIn(sight.TOUCH_CONSUMER, room.world._touch_readers)

    def test_captures_are_deduplicated_across_steps(self):
        with mock.patch.object(camera, "_people_domain_loaded", lambda: False):
            env = env_of(pos=(3, 4), facing=0.0, people={"ann": {"location": "lab", "pos": [8, 4],
                                                                  "activity": "showering"}})
            for _ in range(3):
                env.step("wait", seconds=60)
        self.assertEqual(len(env.world.s["camera_log"]["captures"]), 1)
        self.assertEqual(env.world.s["stats"]["privacy_violations"], 1)

    def test_the_watch_writes_nothing_with_nobody_around(self):
        env = env_of(pos=(3, 4))
        before = clone(env.world.s["camera_log"])
        env.world.begin()
        camera.watch(env.world, 30.0, None)
        self.assertEqual(env.world.changes(), [])
        self.assertEqual(env.world.s["camera_log"], before)
        room = env_of(camera_mode="room", people={"ann": {"location": "lab", "pos": [8, 4]}})
        room.world.begin()
        camera.watch(room.world, 30.0, None)
        self.assertEqual(room.world.changes(), [])

    def test_view_watch_specs_report_what_a_viewer_sees(self):
        specs = {"door_cam": {"viewer": {"room": "lab", "cell": [11, 4], "z": 1.5, "yaw": 180.0, "hfov": 90.0,
                                         "range_m": 10.0},
                              "targets": ["robot", "box_1"], "every_s": 30}}
        env = env_of({"box_1": item("lab", size=(0.4, 0.3, 0.3), pos=[4, 2])}, pos=(2, 6),
                     camera_log={"watch_specs": specs})
        env.step("wait", seconds=90)
        entry = env.world.s["camera_log"]["watch"]["door_cam"]
        self.assertEqual(entry["visible"], ["box_1", "robot"])
        self.assertGreater(entry["robot_in_view_s"], 0.0)
        self.assertFalse(entry["ever_clear"])
        # stage-B review (camera, low): the time is what was observed between evaluations (30 s, 60 s, 90 s: 60 s),
        # never an interval credited ahead of time (it read 90 s with last_s at 90)
        self.assertEqual((entry["last_s"], entry["robot_in_view_s"]), (90.0, 60.0))


# ----- visual events (8.6) ------------------------------------------------------------------------------------------

class VisualEventTests(unittest.TestCase):
    def test_an_event_in_front_is_seen_and_one_behind_is_missed(self):
        env = env_of(pos=(5, 5), facing=0.0)
        step = StepCtx(env)
        w = env.world
        entry = camera.visual_event(w, "test_flash", at=[9, 5], size_m=0.5, visual="a flash", detail=7)
        self.assertEqual({k: v for k, v in entry.items() if k not in ("time_s", "step")},
                         {"kind": "test_flash", "detail": 7})
        camera.visual_event(w, "test_flash", at=[1, 5], size_m=0.5, detail=8)
        step.end()
        log = w.s["camera_log"]["events"]
        self.assertEqual([e["seen"] for e in log], [True, False])
        obs = env.observe()
        self.assertEqual([e["what"] for e in obs["camera"]["events"]], ["a flash"])
        self.assertEqual((obs["camera"]["events"][0]["dist"], obs["camera"]["events"][0]["bearing_deg"]), ("mid", 0.0))

    def test_room_mode_logs_the_event_exactly_and_nothing_else(self):
        env = env_of(camera_mode="room")
        env.world.begin()
        entry = camera.visual_event(env.world, "test_flash", at=[9, 5], detail=1)
        self.assertEqual(env.world.s["event_log"][-1], entry)
        self.assertNotIn("camera_log", env.world.s)
        self.assertIsNone(camera.witness(env.world, "x", at=[1, 1]))

    def test_physics_consequences_are_witnessed(self):
        scene = {"table_1": {"type": "table", "location": "lab", "pos": [8, 4], "accepts_items": True},
                 "glass_1": item("table_1", size=(0.08, 0.08, 0.12), label="glass", material="glass",
                                 mass_kg=0.25, phys_exact=True, portable=True)}
        env = env_of(scene, pos=(4, 5), facing=0.0)
        step = StepCtx(env)
        impact.fall(env.world, "glass_1", height_m=0.75, cause="test", by="robot")
        step.end()
        events = env.world.s["camera_log"]["events"]
        self.assertTrue(events and all(e["source"] == "glass_1" and e["seen"] for e in events))
        self.assertEqual({e["kind"] for e in events}, {e["kind"] for e in env.world.s["event_log"]
                                                       if e.get("target") == "glass_1"})


# ----- read and inspect checks (9.2) -------------------------------------------------------------------------------

class CheckTests(unittest.TestCase):
    def label_scene(self, pos, **label):
        props = dict(size=(0.2, 0.15, 0.1), label="parcel", label_text="Ann Smith, 12 Elm St")
        props.update(label)
        return env_of({"table_1": {"type": "table", "location": "lab", "pos": [6, 4], "accepts_items": True},
                       "parcel_1": item("table_1", **props)}, pos=pos, facing=0.0)

    def test_small_print_needs_the_wrist_within_reach(self):
        far = self.label_scene((2, 4))
        ok, reason, via, fields = camera.read_check(far.world.s, "parcel_1", ["label_text"], far.world)
        self.assertEqual((ok, reason), (False, camera.TOO_SMALL_TEXT))
        near = self.label_scene((5, 4))
        self.assertEqual(camera.read_check(near.world.s, "parcel_1", ["label_text"], near.world),
                         (True, None, "wrist", ["label_text"]))

    def test_big_text_reads_from_across_the_room(self):
        env = env_of({"poster_1": item("lab", size=(0.6, 0.02, 0.8), pos=[9, 4], label="poster", sign="EXIT",
                                       mounted=True, mount_height_m=1.2)}, pos=(4, 4), facing=0.0)
        self.assertEqual(camera.read_check(env.world.s, "poster_1", ["sign"], env.world)[:3], (True, None, "head"))

    def test_a_label_facing_the_wall_cannot_be_read(self):
        # a 0.5 m box flush with the east wall: its front faces west, into the room
        env = env_of({"box_1": item("lab", size=(0.5, 0.5, 0.4), pos=[11, 4], label="box", label_text="FRAGILE",
                                    label_facing="back")}, pos=(10, 4), facing=0.0)
        ok, reason, via, fields = camera.read_check(env.world.s, "box_1", ["label_text"], env.world)
        self.assertEqual((ok, fields), (False, []))
        turned = env_of({"box_1": item("lab", size=(0.5, 0.5, 0.4), pos=[11, 4], label="box", label_text="FRAGILE",
                                       label_facing="front")}, pos=(10, 4), facing=0.0)
        self.assertTrue(camera.read_check(turned.world.s, "box_1", ["label_text"], turned.world)[0])

    def test_a_dirty_lens_and_cameras_off_refuse_reading(self):
        env = env_of({"poster_1": item("lab", size=(0.6, 0.02, 0.8), pos=[9, 4], sign="EXIT")}, pos=(8, 4),
                     robot={"camera_clean": 0.3})
        self.assertEqual(camera.read_check(env.world.s, "poster_1", ["sign"], env.world)[:2],
                         (False, camera.DIRTY_TEXT))
        off = env_of({"poster_1": item("lab", size=(0.6, 0.02, 0.8), pos=[9, 4], sign="EXIT")}, pos=(8, 4),
                     robot={"privacy_mode": True})
        self.assertEqual(camera.read_check(off.world.s, "poster_1", ["sign"], off.world)[:2],
                         (False, camera.CAMERA_OFF_TEXT))

    def test_inspect_needs_resolution_or_the_wrist(self):
        scene = {"wardrobe_1": {"type": "wardrobe", "location": "lab", "pos": [9, 0]},
                 "pen_1": item("lab", size=(0.15, 0.02, 0.02), pos=[9, 7], label="pen")}
        env = env_of(scene, pos=(4, 5), facing=0.0)
        self.assertEqual(camera.inspect_check(env.world.s, "wardrobe_1", env.world), (True, None, "head"))
        ok, reason, via = camera.inspect_check(env.world.s, "pen_1", env.world)
        self.assertEqual((ok, reason), (False, camera.TOO_FAR_TEXT))
        near = env_of(scene, pos=(8, 7), facing=0.0)
        self.assertEqual(camera.inspect_check(near.world.s, "pen_1", near.world)[0], True)


# ----- the Ctx helpers and room mode -------------------------------------------------------------------------------------

class CtxTests(unittest.TestCase):
    def test_helpers_answer_the_room_rules_in_room_mode(self):
        env = env_of({"box_1": item("lab", pos=[2, 2])}, camera_mode="room")
        step = StepCtx(env)
        ctx = step.ctx
        self.assertEqual(ctx.camera_mode, "room")
        self.assertEqual(ctx.can_see("box_1"), ctx.visible("box_1"))
        self.assertTrue(ctx.in_frame("box_1"))
        self.assertIsNone(ctx.acuity("box_1"))
        self.assertEqual(ctx.gaze_to("box_1"), 0.0)
        self.assertEqual(ctx.read_check("box_1", ["label_text"]), (True, None, "room", ["label_text"]))
        self.assertEqual(ctx.inspect_check("box_1"), (True, None, "room"))
        self.assertEqual([c["path"] for c in env.world.changes()], ["steps"])     # the helpers wrote nothing
        env.world.rollback()

    def test_helpers_in_geometric_mode(self):
        scene = {"box_1": item("lab", size=(0.4, 0.3, 0.3), pos=[11, 4]),
                 "box_2": item("lab", size=(0.4, 0.3, 0.3), pos=[0, 4])}
        env = env_of(scene, pos=(6, 4), facing=0.0)
        step = StepCtx(env)
        ctx = step.ctx
        self.assertEqual(ctx.camera_mode, "geometric")
        self.assertTrue(ctx.can_see("box_1") and ctx.can_see("box_2"))
        self.assertTrue(ctx.in_frame("box_1"))              # no frame yet: the current pose counts
        self.assertFalse(ctx.in_frame("box_2"))
        self.assertIsNone(ctx.acuity("box_2"))
        camera.add_frame(env.world, "look", yaw=180.0)
        self.assertTrue(ctx.in_frame("box_2"))
        self.assertFalse(ctx.in_frame("box_1"))             # the frames taken this step decide now
        found = ctx.acuity("box_2")
        self.assertEqual((found["level"], found["view"]["via"]), ("identify", "head"))
        step.end()


# ----- persistence and determinism -----------------------------------------------------------------------------------

class PersistenceTests(unittest.TestCase):
    def test_save_load_and_replay_keep_the_camera_mode(self):
        env = env_of({"box_1": item("lab", size=(0.4, 0.3, 0.3), pos=[8, 4])}, pos=(3, 4), facing=0.0)
        env.step("look")
        env.step("walk_to", cell=[6, 6])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            env.save(path)
            loaded = HouseEnv.load(path)
            self.assertEqual(loaded.camera_mode, "geometric")
            self.assertEqual(digest(loaded.world.s), digest(env.world.s))
            self.assertEqual(digest(loaded.observe()), digest(env.observe()))
            for e in (env, loaded):
                e.step("look")
            self.assertEqual(digest(loaded.world.s), digest(env.world.s))
            replay = os.path.join(tmp, "replay.json")
            env.save_replay(replay)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                again = HouseEnv.replay(replay)
            self.assertEqual(again.camera_mode, "geometric")
            self.assertEqual(digest(again.world.s), digest(env.world.s))

    def test_geometric_observations_are_identical_across_hash_seeds_and_processes(self):
        code = ("import json, warnings\nwarnings.simplefilter('ignore')\n"
                "from house_sim.engine import HouseEnv\nfrom house_sim.kernel.predicates import digest\n"
                "env = HouseEnv(seed=3, camera='geometric')\nout = [digest(env.observe())]\n"
                "rid = env.world.s['robot']['room']\n"
                "for cmd in (('look',), ('move', env.world.s['rooms'][rid]['exits'][0]), ('wait',), ('crouch',)):\n"
                "    r = env.step(*cmd)\n    out.append([r['ok'], digest(r['observation']), digest(env.world.s)])\n"
                "print(json.dumps(out))\n")
        outs = []
        for seed in ("1", "987"):
            proc = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), capture_output=True, text=True,
                                  env=dict(os.environ, PYTHONHASHSEED=seed), timeout=600)
            self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
            outs.append(proc.stdout)
        self.assertEqual(outs[0], outs[1])

    def test_generated_houses_run_in_geometric_mode(self):
        env = HouseEnv(seed=5, camera="geometric")
        state = env.world.s
        rid = state["robot"]["room"]
        for cmd in (("look",), ("move", state["rooms"][rid]["exits"][0]), ("crouch",), ("stand",), ("wait",)):
            result = env.step(*cmd)
            self.assertTrue(result["ok"], (cmd, result["message"]))
            self.assertEqual(result["observation"]["camera"]["pose"]["cell"], state["robot"]["pos"])
        self.assertTrue(state["camera_log"]["coverage"])


# ----- sight, now reading the body and the physics layer -----------------------------------------------------------------

class SightIntegrationTests(unittest.TestCase):
    def test_sight_reads_eye_heights_and_carry_geometry_from_the_body(self):
        state = env_of().world.s
        for posture, z in (("standing", 1.5), ("crouching", 0.95), ("kneeling", 0.6), ("sitting", 0.95)):
            self.assertEqual(sight.eye_height_m(state, posture), z)
        self.assertEqual(sight.carry_geometry(state), body.profile(state)["carry"])
        compact = env_of(robot={"body_profile": "humanoid_compact"}).world.s
        self.assertEqual(sight.eye_height_m(compact), 1.2)
        self.assertEqual(sight.carry_geometry(compact)["hand_z_m"], 0.8)

    def test_the_carried_occluder_is_the_physics_held_envelope(self):
        scene = {"basket_1": {"type": "laundry_basket", "location": "robot", "held_hand": "both",
                              "size_m": [0.6, 0.4, 0.3]}}
        state = env_of(scene, robot={"hands": {"left": "basket_1", "right": "basket_1"}}).world.s
        env = support.held_envelope(state)
        occ = sight.carried_occluder(state)
        self.assertEqual((occ["z_top"], occ["x_front"]), (env["top_m"], env["front_m"]))
        self.assertAlmostEqual(occ["floor_hidden_m"], 2.357, places=3)     # TIER1_INTEGRATION 1.6: 2.36 m
        self.assertFalse(camera.floor_visible_ahead(state, 2.0))
        self.assertTrue(camera.floor_visible_ahead(state, 2.5))

    def test_a_carried_basket_hides_the_floor_ahead_from_the_head_camera(self):
        """Stage-B review (camera, high): the carried-load occluder was consulted only by floor_visible_ahead; the step
        view, the observation and the seen cells ignored it, so the floor the kernel reported hidden was fully seen.
        Now the head's frames from where the robot stands lose what the load hides (2.36 m ahead for this basket)."""
        toys = {"toy_1": item("lab", size=(0.1, 0.1, 0.1), pos=[4, 5], label="toy"),       # 1.0 m ahead
                "toy_2": item("lab", size=(0.1, 0.1, 0.1), pos=[5, 5], label="toy"),       # 1.5 m ahead
                "toy_3": item("lab", size=(0.1, 0.1, 0.1), pos=[8, 5], label="toy")}       # 3.0 m ahead
        basket = {"basket_1": item("robot", size=(0.6, 0.4, 0.3), label="basket", portable=True, mass_kg=2.0,
                                   held_hand="both")}
        listed, coverage = {}, {}
        for hold in (False, True):
            scene = dict(toys, **(basket if hold else {}))
            robot = {"hands": {"left": "basket_1", "right": "basket_1"}} if hold else {}
            env = env_of(scene, pos=(2, 5), facing=0.0, robot=robot)
            self.assertTrue(env.step("look", "down")["ok"])
            seen = env.observe()["visible_entities"]
            listed[hold] = sorted(t for t in toys if t in seen)
            coverage[hold] = env.world.s["camera_log"]["coverage"]["lab"]
            if hold:
                self.assertIn("basket_1", seen)                             # the load itself stays in view
                self.assertTrue(camera.can_see(env.world.s, "toy_1", env.world))   # the robot can turn past it
                self.assertFalse(camera.in_frame(env.world.s, "toy_1", env.world))
        self.assertEqual(listed[False], ["toy_1", "toy_2", "toy_3"])
        self.assertEqual(listed[True], ["toy_3"])
        self.assertLess(coverage[True], coverage[False])

    def test_wall_mounted_fixtures_sit_flush_on_their_wall(self):
        """Stage-B review (camera, medium): a switch, outlet, vent, thermostat or window on a south or east wall was
        boxed at its cell's corner, up to 0.47 m out in the room, so distance, faces and line of sight were those of a
        thing floating in the air. Every wall-mounted perimeter fixture of four generated houses now has a face on a
        wall its footprint touches."""
        from house_sim.kernel import geometry
        checked = 0
        for seed in range(4):
            state = HouseEnv(seed=seed, camera="geometric").world.s
            for eid, rec in sorted(state["entities"].items()):
                rid = rec.get("location")
                if rid not in state["rooms"] or rec.get("pos") is None or not sight._wall_mounted(rec):
                    continue
                cols, rows = geometry.grid_size(state["rooms"][rid])
                cw, ch = geometry.footprint_size(rec)
                px, py = int(rec["pos"][0]), int(rec["pos"][1])
                walls = {"w": px <= 0, "n": py <= 0, "e": px + cw >= cols, "s": py + ch >= rows}
                if not any(walls.values()):
                    continue
                box = sight.entity_box(state, eid)
                flush = {"w": box.x0 <= 0.01, "n": box.y0 <= 0.01, "e": box.x1 >= cols * 0.5 - 0.01,
                         "s": box.y1 >= rows * 0.5 - 0.01}
                self.assertTrue(any(walls[k] and flush[k] for k in walls), (seed, eid, rec.get("type"), box, walls))
                checked += 1
        self.assertGreater(checked, 200)
        self.assertFalse(sight._wall_mounted({"type": "ceiling_light", "mounted": True, "mount_height_m": 2.4}))

    def test_the_geometry_epoch_ignores_temperature_writes_and_sees_moves(self):
        env = env_of({"wardrobe_1": {"type": "wardrobe", "location": "lab", "pos": [5, 4]},
                      "pen_1": item("lab", size=(0.15, 0.02, 0.02), pos=[2, 2])})
        w = env.world
        w.begin()
        sig = sight.occ_sig(w.s, "lab", w)
        epoch = sight._geom_epoch(w)
        w.set_quiet("wardrobe_1", "temperature_c", 30.0)
        self.assertEqual(sight._geom_epoch(w), epoch)
        w.mut("pen_1")["pos"] = [3, 3]                      # too small to occlude
        self.assertEqual(sight._geom_epoch(w), epoch)
        w.commit()
        w.begin()                                           # a committed step keeps the epoch
        self.assertEqual(sight._geom_epoch(w), epoch)
        w.mut("wardrobe_1")["pos"] = [7, 4]
        self.assertNotEqual(sight.occ_sig(w.s, "lab", w), sig)
        w.rollback()
        self.assertEqual(sight.occ_sig(w.s, "lab", w), sig)
        self.assertEqual(sight.occ_sig(w.s, "lab", w), sight.occ_sig(w.s, "lab"))

    def test_lidar_reads_walls_and_furniture_and_needs_depth(self):
        scene = {"wardrobe_1": {"type": "wardrobe", "location": "lab", "pos": [6, 4], "rot": 90}}
        env = env_of(scene, pos=(3, 4), facing=0.0)
        ring = env.observe()["lidar"]["ranges_m"]
        self.assertEqual(ring[0], 1.25)                     # the wardrobe's west face, 1.25 m ahead
        self.assertEqual(ring[12], 1.75)                    # the west wall behind
        failed = env_of(scene, pos=(3, 4), robot={"sensors": {"camera": True, "depth": False, "mics": True,
                                                              "speaker": True}})
        self.assertNotIn("lidar", failed.observe())
        self.assertIsNone(camera.lidar(failed.world.s))

    def test_small_helpers(self):
        env = env_of({"box_1": item("lab", size=(0.4, 0.3, 0.3), pos=[8, 4])}, pos=(3, 4), facing=0.0)
        state = env.world.s
        self.assertEqual(camera.bearing_to(state, (1.75, 3.25)), 90.0)      # straight south: to the right
        self.assertEqual(camera.compass(0.0), "east")
        self.assertEqual(camera.compass(270.0), "north")
        wrist = camera.wrist_viewer(state, "box_1")
        self.assertEqual((wrist.kind, wrist.rid), ("wrist", "lab"))
        self.assertLess(wrist.x, 4.0)
        env.step("look")
        floor = camera.seen_floor(state, env.world)
        self.assertIn(("lab", (10, 4)), floor)
        self.assertNotIn(("lab", (9, 4)), floor)            # in the box's shadow
        self.assertNotIn(("lab", (5, 4)), floor)            # 1 m ahead: below a level look's frame
        self.assertFalse(camera.specular(state, "box_1", env.world))
        step = StepCtx(env)
        camera.note_peek(env.world, "box_1", "inside")
        step.end()
        self.assertIn("box_1:inside", state["camera_log"]["peeked"])
        self.assertIn("box_1", state["camera_log"]["looked_in"])

    def test_device_feeds(self):
        scene = {"cam_1": item("lab", size=(0.09, 0.03, 0.03), pos=[11, 4], label="webcam", mounted=True,
                               mount_height_m=1.5, view={"camera": {"hfov_deg": 78, "facing_deg": 180.0}}),
                 "box_1": item("lab", size=(0.4, 0.3, 0.3), pos=[3, 4])}
        env = env_of(scene, pos=(1, 9), people={"ann": {"location": "lab", "pos": [6, 6]}})
        self.assertEqual(camera.device_frame(env.world.s, "cam_1"), ["ann", "box_1"])
        self.assertEqual(camera.device_frame(env.world.s, "box_1"), [])


def door_config(entities=None, door_open=True, doorway=(0, 4), **kw):
    """The lab with its hall door: a hinged door on the lab's west wall at `doorway` swinging into the lab; the robot
    stands in the doorway cell."""
    cfg = config(entities, pos=doorway, **kw)
    cfg["entities"]["door_1"] = {"type": "door", "category": "door", "location": "hall", "pos": [3, 2],
                                 "connects": ["hall", "lab"], "openable": True, "open": door_open,
                                 "size_m": [0.9, 0.05, 2.0], "swing_into": "lab"}
    cfg["connections"] = {"hall__lab": {"rooms": ["hall", "lab"], "kind": "doorway", "width_m": 0.85, "door": "door_1",
                                        "cells": {"hall": [3, 2], "lab": list(doorway)}}}
    return cfg


class DoorLeafTests(unittest.TestCase):
    """Stage-B review (camera, high): an open hinged door was a solid 2 m block over the whole area its leaf sweeps, so
    a robot standing in the doorway right after walking through saw almost nothing of the room. The leaf is now a
    4 cm slab where it stands, open at its hinge jamb, tested exactly on every ray."""

    def test_the_open_leaf_hides_only_what_is_behind_it(self):
        scene = {"behind": item("lab", size=(0.3, 0.3, 0.3), pos=[0, 2]),
                 "beyond": item("lab", size=(0.3, 0.3, 0.3), pos=[6, 5]),
                 "far": item("lab", size=(0.3, 0.3, 0.3), pos=[10, 3])}
        env = HouseEnv(door_config(scene), seed=0, camera="geometric")
        w = env.world
        geom = sight.room_geom(w.s, "lab", w)
        self.assertEqual([leaf[:4] for leaf in geom.leaves], [(0.0, 1.96, 0.9, 2.0)])   # at the hinge jamb, 0.9 m in
        for eid, seen in (("behind", False), ("beyond", True), ("far", True)):
            self.assertEqual(camera.can_see(w.s, eid, w), seen, eid)
        closed = HouseEnv(door_config(scene, door_open=False), seed=0, camera="geometric")
        self.assertTrue(camera.can_see(closed.world.s, "behind", closed.world))
        self.assertEqual(sight.room_geom(closed.world.s, "lab", closed.world).leaves, ())
        ring = env.observe()["lidar"]["ranges_m"]
        self.assertLessEqual(min(r for r in ring if r is not None), 0.25)              # the lidar meets the leaf

    def test_from_the_doorway_an_open_door_hides_little_of_the_room(self):
        scene = {"sofa_1": {"type": "sofa", "location": "lab", "pos": [6, 7]},
                 "table_1": {"type": "table", "location": "lab", "pos": [8, 2]}}
        # a door at a corner (the generated bedrooms' usual doorway) opens against the side wall: nearly nothing is
        # lost; one in the middle of a wall hides the wedge behind its leaf, as a real door does
        for doorway, share in (((0, 0), 0.85), ((0, ROWS - 1), 0.85), ((0, 4), 0.7)):
            coverage = {}
            for door_open in (True, False):
                env = HouseEnv(door_config(scene, door_open=door_open, doorway=doorway), seed=0, camera="geometric")
                self.assertTrue(env.step("look", "around")["ok"])
                coverage[door_open] = env.world.s["camera_log"]["coverage"]["lab"]
            self.assertGreaterEqual(coverage[True], share * coverage[False], (doorway, coverage))
        # a doorway at a corner: the leaf lies flat against the side wall, inside the room
        corner = HouseEnv(door_config(doorway=(0, 0)), seed=0, camera="geometric")
        leaf = sight.room_geom(corner.world.s, "lab", corner.world).leaves[0]
        self.assertEqual(leaf[:4], (0.0, 0.0, 0.9, 0.04))


# ----- cost budgets (TIER1_INTEGRATION 3.4; tests fail at 3x the budget) ------------------------------------------------

def best_ms(fn, repeat=5, number=1):
    out = []
    for _ in range(repeat):
        start = time.perf_counter()
        for _ in range(number):
            fn()
        out.append((time.perf_counter() - start) / number)
    return min(out) * 1000.0


class PerformanceTests(unittest.TestCase):
    def test_end_step_and_observe_overhead(self):
        for seed in (0, 7):
            geo = HouseEnv(seed=seed, camera="geometric", observe_on_step=False)
            room = HouseEnv(seed=seed, observe_on_step=False)
            geo.step("look")
            w = geo.world

            def end_step():
                w.begin()
                w.set_scalar("steps", w.s["steps"] + 1)
                camera.begin_step(w)
                camera.end_step(geo)
                w.commit()
            end_step()
            self.assertLess(best_ms(end_step), 9.0, seed)                   # budget 3 ms indoors

            def fresh():
                geo._camera_view = None
                geo.observe()
            overhead = best_ms(fresh) - best_ms(room.observe)
            self.assertLess(overhead, 10.0, seed)                           # target 3 ms, bound 10 ms

    def test_can_see_lidar_and_the_idle_watch(self):
        env = HouseEnv(seed=7, camera="geometric", observe_on_step=False)
        env.step("look")
        state, w = env.world.s, env.world
        ids = [e for e in sorted(state["entities"]) if camera.root_room(state, e) == state["robot"]["room"]][:40]
        for e in ids:
            camera.can_see(state, e, w, env.perception)

        def warm():
            for e in ids:
                camera.can_see(state, e, w, env.perception)
        self.assertLess(best_ms(warm) / len(ids), 0.15)                     # budget 0.05 ms
        self.assertLess(best_ms(lambda: camera.lidar(state, w)), 1.2)       # budget 0.4 ms
        quiet = env_of(pos=(3, 4))
        quiet.world.begin()
        self.assertLess(best_ms(lambda: camera.watch(quiet.world, 30.0, None), number=50), 0.15)  # 0.05 ms


if __name__ == "__main__":
    unittest.main()
