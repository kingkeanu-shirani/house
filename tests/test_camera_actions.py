"""Tier-1 camera actions (CAM-ACTIONS, kernel v3.0 geometric camera; docs/design/TIER1_CAMERA.md 9.1, 9.2, 10.5, 14,
15 and TIER1_INTEGRATION.md 1.13, 1.15, 2.4 B3): the camera verbs look (forward, around and its low row, up / down /
left / right / behind, at a thing, person, pet or cell, head pan / tilt, tracking), face and peek (the wrist camera
under / behind / inside / on top of / closely at a thing, lifting light supports, revealing within reach depth); the
geometric rules of inspect and read (acuity, the wrist, faces, readable fields only, grants); the setup ops camera,
view, track_view, view_watch and spawn rows; the spec key "camera" and its lint; the camera-mode plumbing of build,
make_env, the verifier and the CLI; the reference skills' geometric branches; determinism, legacy frames, both
perception modes and the cost of the new frames.

Room mode is pinned byte for byte by tests/test_room_golden.py; here direct checks make sure the new parameters and
verbs leave room mode untouched. Scenes are built from core types, so the file passes with HOUSE_SIM_DOMAINS=core as
well as with every domain loaded, normal and HOUSE_SIM_STRICT=1.
"""
import copy
import io
import json
import math
import os
import random
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock
from pathlib import Path

from house_sim import cli
from house_sim.kernel import camera, sight
from house_sim.kernel.actions import Ctx
from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.errors import SpecError
from house_sim.kernel.plugins import ACTIONS
from house_sim.kernel.predicates import digest
from house_sim.kernel.registry import default_registry
from house_sim.systems import core
from house_sim.tasks import setup as setup_mod
from house_sim.tasks import spec as spec_mod
from house_sim.tasks.runner import build, make_env, run_plan
from house_sim.tasks.verify import run_trial, verify_many

ROOT = Path(__file__).resolve().parent.parent
COLS, ROWS = 12, 10


# ----- scenes ---------------------------------------------------------------------------------------------------------

def config(entities=None, pos=(1, 1), facing=0.0, people=None, pets=None, light=True, robot=None):
    """A lit 6 x 5 m living room ("lab", a ceiling light in its north-east corner) with a lit hallway next door."""
    ents = {"light_1": {"type": "ceiling_light", "location": "lab", "pos": [COLS - 1, 0], "on": light,
                        "mounted": True, "mount_height_m": 2.4},
            "light_2": {"type": "ceiling_light", "location": "hall", "pos": [3, 5], "on": True, "mounted": True,
                        "mount_height_m": 2.4}}
    ents.update(copy.deepcopy(entities or {}))
    rob = {"room": "lab", "pos": list(pos), "facing_deg": facing}
    rob.update(copy.deepcopy(robot or {}))
    return {"schema_version": 2, "start_room": "lab",
            "rooms": {"lab": {"size_m": [COLS * 0.5, ROWS * 0.5], "type": "living_room", "exits": ["hall"]},
                      "hall": {"size_m": [2.0, 3.0], "type": "hallway", "exits": ["lab"]}},
            "entities": ents, "people": copy.deepcopy(people or {}), "pets": copy.deepcopy(pets or {}), "robot": rob}


def env_of(entities=None, camera_mode="geometric", perception=None, **kw):
    return HouseEnv(config(entities, **kw), seed=0, camera=camera_mode, perception=perception)


def item(location, size=(0.1, 0.1, 0.1), **props):
    out = {"type": "generic_item", "location": location, "size_m": list(size), "label": props.pop("label", "thing")}
    out.update(props)
    return out


def frames(env):
    return env.world.s["robot"]["view"]["frames"]


def view(env):
    return env.world.s["robot"]["view"]


def _wrist_frames(env):
    return [f for f in frames(env) if f[6] == "wrist"]


def seen(env):
    return env.observe()["visible_entities"]


def step(env, *args, **kw):
    """One step; returns (result, seconds it took)."""
    before = env.world.s["time_s"]
    result = env.step(*args, **kw)
    return result, round(env.world.s["time_s"] - before, 3)


def realistic_geometric_available():
    try:
        HouseEnv(config(), seed=0, perception="realistic", camera="geometric")
    except NotImplementedError:
        return False
    return True


# a 1.6 x 2.0 m bed (x 3.0-4.6, y 2.0-4.0, 25 cm clearance) with one toy near its west edge and one deep under it
BED_SCENE = {"bed_1": {"type": "bed", "location": "lab", "pos": [6, 4], "accepts_items": True},
             "toy_1": item("bed_1", size=(0.12, 0.08, 0.06), relation="under", spos=[0.3, 0.5], label="toy"),
             "toy_2": item("bed_1", size=(0.12, 0.08, 0.06), relation="under", spos=[1.35, 1.8], label="deep toy")}
# a sofa against the north wall (x 2.0-4.0, y 0-0.9, seat 0.45 m) with a light cushion and a coin under it
SOFA_SCENE = {"sofa_1": {"type": "sofa", "location": "lab", "pos": [4, 0], "accepts_items": True},
              "cushion_1": item("sofa_1", size=(0.5, 0.5, 0.12), label="cushion", mass_kg=0.6, spos=[0.2, 0.2],
                                accepts_items=True),
              "coin_1": item("cushion_1", size=(0.03, 0.03, 0.005), label="coin", relation="under")}


# ----- look (9.1) -----------------------------------------------------------------------------------------------------

class LookTests(unittest.TestCase):
    def test_room_mode_look_ignores_the_new_parameters_and_lasts_one_second(self):
        for perception in ("oracle", "realistic"):
            plain = env_of(BED_SCENE, camera_mode="room", perception=perception, pos=(2, 6))
            fancy = env_of(BED_SCENE, camera_mode="room", perception=perception, pos=(2, 6))
            a, sa = step(plain, "look", "around")
            b, sb = step(fancy, "look", "around", rows=2, track=True)
            c, sc = step(fancy, "look", "", pan=40.0, tilt=-20.0)
            self.assertEqual((sa, sb, sc), (1.0, 1.0, 1.0), perception)
            self.assertEqual((a["message"], b["message"], c["message"]), ("You look around",) * 3)
            step(plain, "look")
            self.assertEqual(digest(plain.world.s), digest(fancy.world.s), perception)
            self.assertEqual(digest(plain.observe()), digest(fancy.observe()), perception)
            self.assertNotIn("view", fancy.world.s["robot"])
            self.assertNotIn("camera_log", fancy.world.s)
        self.assertEqual(ACTIONS["look"].params["rows"]["default"], 1)
        bad = env_of(camera_mode="room").step("look", "around", rows=3)
        self.assertEqual((bad["ok"], bad["category"]), (False, "argument"))

    def test_look_forward_takes_one_resting_frame_straight_ahead(self):
        env = env_of(pos=(5, 5), facing=90.0)
        step(env, "look", "left")
        result, seconds = step(env, "look")
        self.assertEqual((result["message"], seconds), ("You look ahead", 1.0))
        self.assertEqual((view(env)["pan_deg"], view(env)["tilt_deg"], view(env)["aim"]), (0.0, -10.0, None))
        self.assertEqual([(f[4], f[5], f[7]) for f in frames(env)], [(90.0, -10.0, "idle")])

    def test_look_around_frames_every_direction_and_returns_ahead(self):
        scene = {"box_1": item("lab", size=(0.4, 0.3, 0.3), pos=[1, 5], label="behind"),
                 "box_2": item("lab", size=(0.4, 0.3, 0.3), pos=[11, 5], label="ahead")}
        env = env_of(scene, pos=(6, 5), facing=0.0)
        self.assertNotIn("box_1", seen(env))
        result, seconds = step(env, "look", "around")
        self.assertEqual((result["message"], seconds), ("You look around", 5.4))
        looks = [f for f in frames(env) if f[7] == "look"]
        self.assertEqual([f[4] for f in looks], [210.0, 270.0, 330.0, 30.0, 90.0, 150.0])
        self.assertTrue(all(f[5] == -10.0 and f[6] == "head" for f in looks))
        self.assertEqual((view(env)["pan_deg"], view(env)["tilt_deg"]), (0.0, -10.0))
        obs = seen(env)
        self.assertIn("box_1", obs)
        self.assertIn("box_2", obs)
        self.assertEqual(env.observe()["camera"]["frames"], len(frames(env)))
        # every bearing lies inside some frame: 6 frames x 69 degrees, 60 apart
        yaws = [f[4] for f in looks]
        for bearing in range(0, 360, 5):
            self.assertTrue(any(abs(sight.wrap_deg(bearing - y)) <= 34.5 for y in yaws), bearing)

    def test_rows_two_adds_a_low_row_that_sees_the_near_floor(self):
        scene = {"pen_1": item("lab", size=(0.15, 0.04, 0.03), pos=[7, 5], label="pen")}
        env = env_of(scene, pos=(4, 5), facing=0.0)
        step(env, "look", "around")
        self.assertNotIn("pen_1", seen(env))                 # 1.5 m ahead on the floor: under the level frames
        coverage = env.world.s["camera_log"]["coverage"]["lab"]
        result, seconds = step(env, "look", "around", rows=2)
        self.assertEqual(seconds, 8.3)
        lows = [f for f in frames(env) if f[7] == "look" and f[5] == -45.0]
        self.assertEqual(len(lows), 6)
        self.assertEqual(seen(env)["pen_1"]["view"]["via"], "head")
        self.assertGreaterEqual(env.world.s["camera_log"]["coverage"]["lab"], coverage)
        refused = env.step("look", "up", rows=2)
        self.assertEqual((refused["ok"], refused["category"]), (False, "argument"))

    def test_look_up_frames_a_high_shelf_and_down_the_floor(self):
        scene = {"shelf_1": item("lab", size=(0.6, 0.3, 0.05), pos=[8, 5], mounted=True, mount_height_m=2.1,
                                 label="shelf")}
        env = env_of(scene, pos=(5, 5), facing=0.0)
        self.assertNotIn("shelf_1", seen(env))
        result, seconds = step(env, "look", "up")
        self.assertEqual((result["message"], view(env)["tilt_deg"]), ("You look up", 20.0))
        self.assertAlmostEqual(seconds, round(30.0 / 90.0 + 0.4, 3))
        self.assertIn("shelf_1", seen(env))
        result, seconds = step(env, "look", "down")
        self.assertEqual(view(env)["tilt_deg"], -45.0)
        self.assertAlmostEqual(seconds, round(65.0 / 90.0 + 0.4, 3))

    def test_left_right_and_behind(self):
        env = env_of(pos=(5, 5), facing=0.0)
        _, seconds = step(env, "look", "left")
        self.assertEqual((view(env)["pan_deg"], env.world.s["robot"]["facing_deg"]), (-90.0, 0.0))
        self.assertAlmostEqual(seconds, 90.0 / 120.0 + 0.4)
        _, seconds = step(env, "look", "right")
        self.assertEqual(view(env)["pan_deg"], 90.0)
        self.assertAlmostEqual(seconds, 180.0 / 120.0 + 0.4)
        result, seconds = step(env, "look", "behind")
        self.assertEqual((result["message"], env.world.s["robot"]["facing_deg"], view(env)["pan_deg"]),
                         ("You look behind you", 180.0, 0.0))
        self.assertEqual(seconds, 2.4)                                  # the body turns 180 degrees at 90 / s
        perched = env_of(pos=(5, 5), facing=0.0, robot={"standing_on": "stool_1"},
                         entities={"stool_1": item("lab", size=(0.4, 0.4, 0.4), pos=[5, 5], standing_height_m=0.4)})
        step(perched, "look", "behind")                                 # cannot turn: the head pans to its limit
        self.assertEqual((perched.world.s["robot"]["facing_deg"], view(perched)["pan_deg"]), (0.0, 150.0))

    def test_look_at_a_thing_person_or_cell_aims_the_head(self):
        scene = {"box_1": item("lab", size=(0.4, 0.3, 0.3), pos=[9, 5], label="box"),
                 "bin_1": {"type": "storage_bin", "location": "lab", "pos": [2, 2], "open": False,
                           "accepts_items": True},
                 "sock_1": item("bin_1", label="sock"), "far_1": item("hall", pos=[1, 1], label="far")}
        env = env_of(scene, pos=(5, 5), facing=180.0, people={"ann": {"location": "lab", "pos": [5, 9]}})
        result, seconds = step(env, "look", "box_1")
        box = sight.entity_box(env.world.s, "box_1")
        self.assertEqual(result["message"], "You look at it")
        self.assertEqual(view(env)["aim"], [round((box.x0 + box.x1) / 2, 3), round((box.y0 + box.y1) / 2, 3),
                                            round((box.z0 + box.z1) / 2, 3)])
        yaw = math.degrees(math.atan2((box.y0 + box.y1) / 2 - 2.75, (box.x0 + box.x1) / 2 - 2.75)) % 360.0
        self.assertAlmostEqual(env.world.s["robot"]["facing_deg"], yaw, places=1)   # beyond the pan limit: turned
        self.assertEqual(view(env)["pan_deg"], 0.0)
        self.assertGreater(seconds, 2.0)
        result, _ = step(env, "look", "ann")
        self.assertEqual(result["message"], "You look at ann")
        self.assertEqual((env.world.s["robot"]["facing_deg"], view(env)["pan_deg"]),
                         (round(yaw, 2), round(sight.wrap_deg(90.0 - round(yaw, 2)), 2)))   # a head pan, no turn
        result, _ = step(env, "look", "[5, 0]")
        self.assertEqual(result["message"], "You look at cell [5, 0]")
        self.assertEqual(view(env)["aim"], [2.75, 0.25, 0.0])
        for target in ("sock_1", "far_1", "nobody", "[40, 2]"):         # closed in, another room, unknown, off the grid
            result = env.step("look", target)
            self.assertEqual((result["ok"], result["message"], result["category"]),
                             (False, "That is not visible here", "perception"), target)

    def test_head_pan_and_tilt(self):
        env = env_of(pos=(5, 5), facing=0.0)
        result, seconds = step(env, "look", "", pan=40.0, tilt=-20.0)
        self.assertEqual(result["message"], "You look with the head at pan 40, tilt -20")
        self.assertEqual((view(env)["pan_deg"], view(env)["tilt_deg"]), (40.0, -20.0))
        self.assertAlmostEqual(seconds, round(40.0 / 120.0 + 0.4, 3))
        step(env, "look", "", pan=170.0)                                # beyond the limit: the body turns
        self.assertEqual((env.world.s["robot"]["facing_deg"], view(env)["pan_deg"]), (170.0, 0.0))
        step(env, "look", "", tilt=-90.0)
        self.assertEqual(view(env)["tilt_deg"], -60.0)                  # clamped to the head's limit
        result = env.step("look", "around", pan=10.0)
        self.assertEqual((result["ok"], result["category"]), (False, "argument"))

    def test_track_keeps_people_in_view_and_false_stops(self):
        people = {"ann": {"location": "lab", "pos": [9, 5]}, "bob": {"location": "lab", "pos": [1, 5]}}
        env = env_of(pos=(5, 5), facing=0.0, people=people)
        result, _ = step(env, "look", "", track=True)
        self.assertEqual(result["message"], "You look ahead; keeping ann in view")
        self.assertEqual(view(env)["track"], ["ann"])
        self.assertTrue(camera.keep_awake(env.world))
        result, _ = step(env, "look", "bob", track=True)
        self.assertEqual(view(env)["track"], ["bob"])
        result, _ = step(env, "look", "", track=False)
        self.assertEqual((result["message"], view(env)["track"]), ("You look ahead; tracking stopped", []))
        alone = env_of(pos=(5, 5), facing=0.0)
        self.assertTrue(alone.step("look", "around", track=True)["message"].endswith("nobody to track in view"))

    def test_cameras_off_refuse_look_in_both_camera_modes(self):
        env = env_of(pos=(5, 5), robot={"privacy_mode": True})
        result = env.step("look", "around")
        self.assertEqual((result["ok"], result["message"], result["category"]),
                         (False, camera.CAMERA_OFF_TEXT, "perception"))
        room = env_of(camera_mode="room", pos=(5, 5), robot={"privacy_mode": True})
        result = room.step("look", "around")                          # BODY-CORE: both camera modes (conflict 20)
        self.assertEqual((result["ok"], result["message"], result["category"]),
                         (False, camera.CAMERA_OFF_TEXT, "perception"))

    def test_a_stopped_robot_turns_only_its_head(self):
        env = env_of(pos=(5, 5), facing=0.0, robot={"stopped": True})
        self.assertTrue(env.step("look", "behind")["ok"])               # look works while stopped
        self.assertEqual((env.world.s["robot"]["facing_deg"], view(env)["pan_deg"]), (0.0, 150.0))
        refused = env.step("face", "west")
        self.assertEqual((refused["ok"], refused["category"]), (False, "precondition"))


# ----- face (9.1) ----------------------------------------------------------------------------------------------------

class FaceTests(unittest.TestCase):
    def test_room_mode_refuses_the_camera_verbs_and_does_not_offer_them(self):
        for perception in ("oracle", "realistic"):
            env = env_of(BED_SCENE, camera_mode="room", perception=perception, pos=(5, 5))
            bed = env.realism.handles.handle("bed_1") if env.realism is not None else "bed_1"
            before = digest(env.world.s)
            for cmd, kw in ((("face", "north"), {}), (("peek", bed), {"where": "under"})):
                result = env.step(*cmd, **kw)
                self.assertEqual((result["ok"], result["message"], result["category"]),
                                 (False, camera.ROOM_MODE_TEXT, "capability"), (perception, cmd))
            self.assertNotIn("face", env.action_names())
            self.assertNotIn("peek", env.action_names())
            self.assertEqual(env.world.s["stats"]["invalid"], 2)
            self.assertNotEqual(before, digest(env.world.s))            # two refused steps: steps and time moved
            self.assertNotIn("view", env.world.s["robot"])
        self.assertTrue({"face", "peek"} <= env_of().action_names())

    def test_compass_relative_degrees_and_targets(self):
        env = env_of({"box_1": item("lab", size=(0.4, 0.3, 0.3), pos=[9, 1], label="box")}, pos=(5, 5),
                     facing=0.0, people={"ann": {"location": "lab", "pos": [5, 9]}})
        result, seconds = step(env, "face", "north")
        self.assertEqual((result["message"], env.world.s["robot"]["facing_deg"]), ("Facing north (270 degrees)", 270.0))
        self.assertAlmostEqual(seconds, round(90.0 / 90.0 + 0.3, 3))
        for word, expected in (("left", 180.0), ("right", 270.0), ("back", 90.0), ("east", 0.0), ("south", 90.0),
                               ("west", 180.0)):
            step(env, "face", word)
            self.assertEqual(env.world.s["robot"]["facing_deg"], expected, word)
        result, seconds = step(env, "face", "", deg=45.0)
        self.assertEqual((env.world.s["robot"]["facing_deg"], seconds), (45.0, round(135.0 / 90.0 + 0.3, 3)))
        step(env, "face", "", deg=-90.0)
        self.assertEqual(env.world.s["robot"]["facing_deg"], 270.0)
        step(env, "look", "left")
        step(env, "face", "box_1")
        box = sight.entity_box(env.world.s, "box_1")
        centre = ((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2)
        self.assertAlmostEqual(env.world.s["robot"]["facing_deg"],
                               math.degrees(math.atan2(centre[1] - 2.75, centre[0] - 2.75)) % 360.0, places=1)
        self.assertEqual(view(env)["pan_deg"], 0.0)
        self.assertLess(view(env)["tilt_deg"], -10.0)                   # aimed down at the box
        step(env, "face", "ann")
        self.assertEqual(env.world.s["robot"]["facing_deg"], 90.0)
        step(env, "face", "[0, 5]")
        self.assertEqual((env.world.s["robot"]["facing_deg"], frames(env)[-1][7]), (180.0, "look"))

    def test_arguments_and_guards(self):
        env = env_of(pos=(5, 5))
        for args, kw in ((("face",), {}), (("face", "north"), {"deg": 10.0})):
            result = env.step(*args, **kw)
            self.assertEqual((result["ok"], result["category"]), (False, "argument"))
        self.assertEqual(env.step("face", "", deg=400.0)["category"], "argument")
        result = env.step("face", "nowhere")
        self.assertEqual((result["message"], result["category"]), ("That is not visible here", "perception"))
        for robot in ({"fallen": True}, {"actuator_ok": False}):
            refused = env_of(pos=(5, 5), robot=robot).step("face", "north")
            self.assertEqual((refused["ok"], refused["message"], refused["category"]),
                             (False, "Robot cannot move", "capability"), robot)
        flat = env_of(pos=(5, 5), robot={"battery": 0.0}).step("face", "north")    # powered down (BODY-ENGINE)
        self.assertEqual((flat["ok"], flat["message"], flat["category"]),
                         (False, "The robot is powered down (battery depleted); it must be charged or have its "
                                 "battery swapped", "capability"))
        perched = env_of({"stool_1": item("lab", size=(0.4, 0.4, 0.4), pos=[5, 5], standing_height_m=0.4)},
                         pos=(5, 5), robot={"standing_on": "stool_1"})
        refused = perched.step("face", "north")
        self.assertEqual((refused["message"], refused["category"]), ("Step down first", "safety"))
        self.assertEqual(perched.world.s["stats"]["safety_blocks"], 1)

    def test_face_works_with_the_cameras_off(self):
        env = env_of(pos=(5, 5), facing=0.0, robot={"privacy_mode": True})
        result = env.step("face", "south")
        self.assertTrue(result["ok"], result["message"])
        self.assertEqual(env.world.s["robot"]["facing_deg"], 90.0)
        self.assertEqual(frames(env)[-1][4], 90.0)


# ----- peek (9.1) ----------------------------------------------------------------------------------------------------

class PeekTests(unittest.TestCase):
    def kneel(self, env):
        self.assertTrue(env.step("kneel")["ok"])

    def test_peek_under_needs_a_low_posture_and_reveals_within_the_wrist_depth(self):
        env = env_of(BED_SCENE, pos=(5, 5), facing=0.0)
        refused = env.step("peek", "bed_1", where="under")
        self.assertEqual((refused["ok"], refused["message"], refused["category"]),
                         (False, camera.NO_GAP_TEXT, "precondition"))
        self.kneel(env)
        result, seconds = step(env, "peek", "bed_1", where="under")
        self.assertEqual(result["message"], "You look under it")
        self.assertGreaterEqual(seconds, 8.0)                           # 6 s + 2 s under (+ the head turning to it)
        state = env.world.s
        self.assertTrue(state["entities"]["toy_1"].get("revealed"))
        self.assertFalse(state["entities"]["toy_2"].get("revealed"))   # 1.7 m past the edge: deeper than the wrist
        self.assertIn("bed_1:under", state["camera_log"]["peeked"])
        wrist = [f for f in frames(env) if f[6] == "wrist"]
        self.assertEqual([(f[7], f[9]) for f in wrist],
                         [("peek", {"target": "bed_1", "where": "under", "lift": None})])
        self.assertLessEqual(wrist[0][3], 0.12)                         # under the bed rails
        obs = seen(env)
        self.assertEqual(obs["toy_1"]["view"]["via"], "wrist")
        self.assertNotIn("toy_2", obs)
        self.assertTrue(camera.can_see(state, "toy_1", env.world, env.perception))
        self.assertFalse(camera.can_see(state, "toy_2", env.world, env.perception))
        self.assertTrue(env.step("pick_up", "toy_1")["ok"])
        self.assertFalse(env.step("pick_up", "toy_2")["ok"])

    def test_peek_takes_its_documented_time_whichever_way_the_head_looks(self):
        """Stage-B review (camera, low): peek charged the head's auto-gaze on top of its 9.1 time (9.334 s for a 6 + 2 s
        peek under a bed with the body turned away). The wrist camera does the looking; the head stays put."""
        for facing in (0.0, 180.0):
            env = env_of(BED_SCENE, pos=(5, 5), facing=facing, robot={"posture": "kneeling"})
            pan = env.world.s["robot"]["view"]["pan_deg"]
            result, seconds = step(env, "peek", "bed_1", where="under")
            self.assertTrue(result["ok"], result["message"])
            self.assertEqual(seconds, 8.0, facing)
            self.assertEqual(env.world.s["robot"]["view"]["pan_deg"], pan)
            _, seconds = step(env, "inspect", "bed_1", where="under")
            self.assertEqual(seconds, 5.0, facing)                      # 3 s + 2 s through the wrist

    def test_lifting_a_light_cushion_shows_what_is_under_it(self):
        env = env_of(SOFA_SCENE, pos=(5, 2), facing=270.0)
        result, seconds = step(env, "peek", "cushion_1", where="under")
        self.assertTrue(result["ok"], result["message"])
        self.assertGreaterEqual(seconds, 9.0)                           # 6 + 2 under + 1 lifting
        wrist = [f for f in frames(env) if f[6] == "wrist"][0]
        self.assertEqual(wrist[9]["lift"], "cushion_1")
        self.assertTrue(env.world.s["entities"]["coin_1"].get("revealed"))
        self.assertEqual(seen(env)["coin_1"]["view"]["via"], "wrist")
        self.assertEqual(env.world.s["entities"]["cushion_1"]["location"], "sofa_1")    # put back
        full = env_of(dict(SOFA_SCENE, cup_1=item("robot", label="cup"), mug_1=item("robot", label="mug")),
                      pos=(5, 2), facing=270.0, robot={"hands": {"left": "cup_1", "right": "mug_1"}})
        refused = full.step("peek", "cushion_1", where="under")
        self.assertEqual((refused["ok"], refused["message"]), (False, "No free hand; place something first"))

    def test_peek_behind_needs_a_gap(self):
        scene = {"dresser_1": {"type": "dresser", "location": "lab", "pos": [4, 0], "accepts_items": True},
                 "photo_1": item("dresser_1", size=(0.2, 0.03, 0.25), relation="behind", label="photo")}
        env = env_of(scene, pos=(5, 1), facing=270.0)
        result, seconds = step(env, "peek", "dresser_1", where="behind")
        self.assertTrue(result["ok"], result["message"])
        self.assertGreaterEqual(seconds, 8.0)
        self.assertTrue(env.world.s["entities"]["photo_1"].get("revealed"))
        self.assertIn("dresser_1:behind", env.world.s["camera_log"]["peeked"])
        flush = copy.deepcopy(scene)
        flush["dresser_1"]["view"] = {"behind_gap_m": 0.0}
        refused = env_of(flush, pos=(5, 1), facing=270.0).step("peek", "dresser_1", where="behind")
        self.assertEqual((refused["ok"], refused["message"]), (False, camera.NOT_BEHIND_TEXT))
        island = copy.deepcopy(flush)
        island["dresser_1"]["pos"] = [4, 4]                             # out in the room: free floor at its back
        env = env_of(island, pos=(5, 5), facing=270.0)
        result = env.step("peek", "dresser_1", where="behind")
        self.assertTrue(result["ok"], result["message"])
        self.assertIsNone(_wrist_frames(env)[0][9]["lift"])

    def test_peek_inside_looks_into_every_shelf_and_needs_it_open(self):
        scene = {"cab_1": {"type": "cabinet", "location": "lab", "pos": [4, 0], "open": True, "accepts_items": True},
                 "jar_1": item("cab_1", size=(0.08, 0.08, 0.12), relation="in", shelf=0, label="jar"),
                 "jar_2": item("cab_1", size=(0.08, 0.08, 0.12), relation="in", shelf=1, label="jar")}
        env = env_of(scene, pos=(4, 2), facing=270.0)
        result, _ = step(env, "peek", "cab_1", where="inside")
        self.assertTrue(result["ok"], result["message"])
        wrist = [f for f in frames(env) if f[6] == "wrist"]
        self.assertEqual(len(wrist), 6)                                 # 3 shelf bands x 2 bays of the 0.8 m opening
        self.assertEqual(sorted(set(round(f[3], 3) for f in wrist)), [0.05, 0.275, 0.675])
        self.assertIn("cab_1", env.world.s["camera_log"]["looked_in"])
        self.assertIn("cab_1:inside", env.world.s["camera_log"]["peeked"])
        obs = seen(env)
        self.assertTrue({"jar_1", "jar_2"} <= set(obs))
        closed = copy.deepcopy(scene)
        closed["cab_1"]["open"] = False
        refused = env_of(closed, pos=(4, 2), facing=270.0).step("peek", "cab_1", where="inside")
        self.assertEqual((refused["ok"], refused["message"]), (False, camera.NOT_OPEN_TEXT))
        table = env_of({"table_1": {"type": "table", "location": "lab", "pos": [4, 0], "accepts_items": True}},
                       pos=(4, 2), facing=270.0)
        refused = table.step("peek", "table_1", where="inside")
        self.assertEqual((refused["ok"], refused["message"]), (False, camera.NOTHING_INSIDE_TEXT))

    def test_peek_on_top_needs_the_top_within_reach(self):
        scene = {"ward_1": {"type": "wardrobe", "location": "lab", "pos": [4, 0], "accepts_items": True},
                 "hat_1": item("ward_1", size=(0.3, 0.3, 0.15), label="hat")}
        env = env_of(scene, pos=(5, 2), facing=270.0)
        result = env.step("peek", "ward_1", where="top")
        self.assertTrue(result["ok"], result["message"])                # 2.0 m: within the 1.85 m reach + 0.2
        self.assertIn("ward_1:top", env.world.s["camera_log"]["peeked"])
        wrist = [f for f in frames(env) if f[6] == "wrist"][0]
        self.assertAlmostEqual(wrist[3], 2.25)
        tall = copy.deepcopy(scene)
        tall["ward_1"]["size_m"] = [1.2, 0.6, 2.4]
        refused = env_of(tall, pos=(5, 2), facing=270.0).step("peek", "ward_1", where="top")
        self.assertEqual((refused["ok"], refused["message"]), (False, camera.TOO_HIGH_TEXT))

    def test_peek_closely_at_a_held_item_sees_every_face(self):
        box = item("robot", size=(0.3, 0.2, 0.2), label="parcel", label_text="Ann Smith", label_facing="back")
        env = env_of({"parcel_1": box}, pos=(5, 5), robot={"hands": {"left": "parcel_1", "right": None}})
        self.assertNotIn("label", seen(env)["parcel_1"]["view"].get("faces", []) or [])
        sv = camera.step_view(env)
        self.assertNotIn("label", sv.entities["parcel_1"]["faces"])     # its back faces away from the robot
        result, seconds = step(env, "peek", "parcel_1", where="closely")
        self.assertEqual((result["message"], seconds), ("You look at it closely", 6.0))
        self.assertIn("label", camera.step_view(env).entities["parcel_1"]["faces"])
        refused = env.step("peek", "parcel_1", where="under")
        self.assertEqual((refused["ok"], refused["message"]), (False, camera.PUT_DOWN_TEXT))

    def test_peek_guards(self):
        off = env_of(BED_SCENE, pos=(5, 5), robot={"privacy_mode": True, "posture": "kneeling"})
        refused = off.step("peek", "bed_1", where="under")
        self.assertEqual((refused["message"], refused["category"]), (camera.CAMERA_OFF_TEXT, "perception"))
        far = env_of(BED_SCENE, pos=(0, 9), robot={"posture": "kneeling"})
        refused = far.step("peek", "bed_1", where="under")
        self.assertEqual(refused["message"], "Approach it first; it is out of arm's reach")
        fallen = env_of(BED_SCENE, pos=(5, 5), robot={"fallen": True})
        self.assertEqual(fallen.step("peek", "bed_1", where="under")["message"], "The robot has fallen; recover first")
        private = copy.deepcopy(BED_SCENE)
        private["bed_1"]["private"] = True
        refused = env_of(private, pos=(5, 5), robot={"posture": "kneeling"}).step("peek", "bed_1", where="under")
        self.assertEqual((refused["message"], refused["category"]), ("Private inspection requires permission", "privacy"))
        bad = env_of(BED_SCENE, pos=(5, 5)).step("peek", "bed_1", where="sideways")
        self.assertEqual(bad["category"], "argument")
        missing = env_of(BED_SCENE, pos=(5, 5)).step("peek", "bed_1")
        self.assertEqual(missing["category"], "argument")


# ----- inspect and read (9.2) ----------------------------------------------------------------------------------------

class InspectTests(unittest.TestCase):
    def test_inspect_under_is_a_wrist_peek_in_geometric_mode_and_reveals_all_in_room_mode(self):
        geo = env_of(BED_SCENE, pos=(5, 5), robot={"posture": "kneeling"})
        result, seconds = step(geo, "inspect", "bed_1", where="under")
        self.assertTrue(result["ok"], result["message"])
        self.assertEqual(json.loads(result["message"])["under"], ["toy_1"])
        self.assertGreaterEqual(seconds, 5.0)                           # 3 s + 2 s with the wrist
        self.assertIn("bed_1:under", geo.world.s["camera_log"]["peeked"])
        room = env_of(BED_SCENE, camera_mode="room", pos=(5, 5))
        result, seconds = step(room, "inspect", "bed_1", where="under")
        self.assertEqual((json.loads(result["message"])["under"], seconds), (["toy_1", "toy_2"], 3.0))
        standing = env_of(BED_SCENE, pos=(5, 5))
        refused = standing.step("inspect", "bed_1", where="under")
        self.assertEqual((refused["ok"], refused["message"]), (False, camera.NO_GAP_TEXT))

    def test_inspect_needs_acuity_or_the_wrist(self):
        scene = {"ward_1": {"type": "wardrobe", "location": "lab", "pos": [9, 0]},
                 "pen_1": item("lab", size=(0.15, 0.02, 0.02), pos=[9, 7], label="pen")}
        env = env_of(scene, pos=(4, 5), facing=0.0)
        self.assertTrue(env.step("inspect", "ward_1")["ok"])           # a 2 m fixture resolves from 3 m
        refused = env.step("inspect", "pen_1")
        self.assertEqual((refused["ok"], refused["message"], refused["category"]),
                         (False, camera.TOO_FAR_TEXT, "precondition"))
        near = env_of(scene, pos=(8, 7), facing=0.0)
        result, seconds = step(near, "inspect", "pen_1")
        self.assertTrue(result["ok"], result["message"])
        self.assertEqual(camera.inspect_check(near.world.s, "pen_1", near.world)[2], "wrist")
        self.assertGreaterEqual(seconds, 5.0)                           # 3 s + 2 s through the wrist
        room = env_of(scene, camera_mode="room", pos=(8, 7))
        self.assertEqual(step(room, "inspect", "pen_1")[1], 3.0)

    def test_inspect_closely_turns_a_held_item_in_the_hand(self):
        env = env_of({"cup_1": item("robot", label="cup")}, pos=(5, 5), robot={"hands": {"left": "cup_1", "right": None}})
        _, plain = step(env, "inspect", "cup_1")
        _, closely = step(env, "inspect", "cup_1", where="closely")
        self.assertEqual((plain, closely), (3.0, 6.0))
        self.assertIn("cup_1:closely", env.world.s["camera_log"]["peeked"])

    def test_inspect_shows_only_the_text_the_cameras_can_read(self):
        def scene(**label):
            props = dict(size=(0.2, 0.15, 0.1), label="parcel", label_text="Ann Smith, 12 Elm St")
            props.update(label)
            return {"table_1": {"type": "table", "location": "lab", "pos": [6, 4], "accepts_items": True},
                    "parcel_1": item("table_1", **props)}
        near = env_of(scene(), pos=(5, 4), facing=0.0)
        self.assertEqual(json.loads(near.step("inspect", "parcel_1")["message"])["label_text"], "Ann Smith, 12 Elm St")
        far = env_of({"box_1": item("lab", size=(0.5, 0.5, 0.4), pos=[8, 4], label="box", label_text="FRAGILE")},
                     pos=(4, 4), facing=0.0)
        with mock.patch.object(core, "INSPECTORS", []):           # 2 m: the box resolves, its 4 mm print does not
            details = json.loads(far.step("inspect", "box_1")["message"])
        self.assertEqual(details["label"], "box")
        self.assertNotIn("label_text", details)
        walled = env_of({"box_1": item("lab", size=(0.5, 0.5, 0.4), pos=[11, 4], label="box", label_text="FRAGILE",
                                       label_facing="back")}, pos=(10, 4), facing=0.0)
        with mock.patch.object(core, "INSPECTORS", []):           # core's own gating (domains adopt it later)
            self.assertNotIn("label_text", json.loads(walled.step("inspect", "box_1")["message"]))
        room = env_of({"box_1": item("lab", size=(0.5, 0.5, 0.4), pos=[11, 4], label="box", label_text="FRAGILE",
                                     label_facing="back")}, camera_mode="room", pos=(10, 4))
        self.assertEqual(json.loads(room.step("inspect", "box_1")["message"])["label_text"], "FRAGILE")

    def test_inspect_lists_only_contents_a_frame_saw(self):
        """Stage-B review (camera, high): a plain geometric inspect listed things under or behind the target that no
        frame ever saw (the wrist could reach them: a coin under a light cushion, a toy under the bed beside a
        kneeling robot), handing out realistic handles without a peek. They are listed once a peek saw them."""
        for perception in (None, "realistic"):
            env = env_of(SOFA_SCENE, perception=perception, pos=(5, 2))
            handle = env.realism.to_handles if perception else (lambda x: x)
            details = json.loads(env.step("inspect", handle("cushion_1"))["message"])
            self.assertEqual(details["contents"], [], perception)
            self.assertEqual(env.world.s["camera_log"]["peeked"], {})
            self.assertIsNone(env.world.s["entities"]["coin_1"].get("revealed"))
            if perception:
                self.assertNotIn("coin_1", env.realism.handles.by_id, "a handle for something nobody saw")
            kneel = env_of(BED_SCENE, perception=perception, pos=(5, 5), robot={"posture": "kneeling"})
            handle = kneel.realism.to_handles if perception else (lambda x: x)
            details = json.loads(kneel.step("inspect", handle("bed_1"))["message"])
            self.assertEqual(details["contents"], [], perception)
            seen = json.loads(kneel.step("inspect", handle("bed_1"), where="under")["message"])["under"]
            self.assertEqual(len(seen), 1, perception)                  # the wrist's peek: toy_1, now revealed
            details = json.loads(kneel.step("inspect", handle("bed_1"))["message"])
            self.assertEqual(len(details["contents"]), 1, perception)

    def test_inspecting_a_person_needs_line_of_sight(self):
        scene = {"ward_1": {"type": "wardrobe", "location": "lab", "pos": [5, 4], "rot": 90}}
        people = {"ann": {"location": "lab", "pos": [8, 5]}}
        hidden = env_of(scene, pos=(1, 5), facing=0.0, people=people)
        refused = hidden.step("inspect", "ann")
        self.assertEqual((refused["ok"], refused["message"]), (False, camera.NOT_SEEN_TEXT))
        clear = env_of(pos=(1, 5), facing=180.0, people=people)
        result, seconds = step(clear, "inspect", "ann")
        self.assertTrue(result["ok"], result["message"])
        self.assertGreater(seconds, 3.0)                                # the head turned round to her
        self.assertEqual(frames(clear)[0][7], "gaze")


class ReadTests(unittest.TestCase):
    def label_scene(self, **extra):
        # a parcel with 4 mm address print and a display in 3 cm digits (readable from 0.37 m and 2.8 m)
        props = dict(size=(0.2, 0.15, 0.1), label="parcel", label_text="Ann Smith, 12 Elm St", display="12:00",
                     view={"text_m": {"display": 0.03}})
        props.update(extra)
        return {"table_1": {"type": "table", "location": "lab", "pos": [6, 4], "accepts_items": True},
                "parcel_1": item("table_1", **props)}

    def test_read_returns_only_what_the_camera_reads(self):
        far = env_of(self.label_scene(), pos=(4, 4), facing=0.0)     # 1.5 m and out of reach: 3 cm, not 4 mm
        result, seconds = step(far, "read", "parcel_1")
        self.assertTrue(result["ok"], result["message"])
        self.assertEqual(json.loads(result["message"]), {"display": "12:00"})
        self.assertEqual(far.world.s["facts"]["read"]["parcel_1"], far.world.s["time_s"] - seconds)
        near = env_of(self.label_scene(), pos=(5, 4), facing=0.0)
        result, seconds = step(near, "read", "parcel_1")
        self.assertEqual(json.loads(result["message"]), {"display": "12:00", "label_text": "Ann Smith, 12 Elm St"})
        self.assertGreaterEqual(seconds, 6.0)                           # 4 s + 2 s through the wrist
        room = env_of(self.label_scene(), camera_mode="room", pos=(5, 4))
        result, seconds = step(room, "read", "parcel_1")
        self.assertEqual((sorted(json.loads(result["message"])), seconds), (["display", "label_text"], 4.0))

    def test_the_wrist_camera_needs_a_working_arm(self):
        """Stage-B review (camera, medium): with both arms failed the robot still read 4 mm print through the wrist
        camera (peek was refused, read was not). Now read falls back to the head and its refusal; held things stay."""
        broken = {"arm_health": {"left": 0.0, "right": 0.0}}
        env = env_of(self.label_scene(), pos=(5, 4), facing=0.0, robot=broken)
        self.assertEqual(camera.wrist_access(env.world.s, "parcel_1"), (False, "Both arms have failed; request help"))
        result = env.step("read", "parcel_1")
        self.assertTrue(result["ok"], result["message"])
        self.assertEqual(json.loads(result["message"]), {"display": "12:00"})     # the head's 3 cm digits only
        small = env_of(self.label_scene(display=None, view={}), pos=(5, 4), facing=0.0, robot=broken)
        refused = small.step("read", "parcel_1")
        self.assertEqual((refused["ok"], refused["message"]), (False, camera.TOO_SMALL_TEXT))
        self.assertEqual(camera.wrist_access(env.world.s, "table_1", where="under")[0], False)
        held = env_of({"cup_1": item("robot", label="cup")}, pos=(5, 5), robot=dict(broken, hands={"left": "cup_1"}))
        self.assertEqual(camera.wrist_access(held.world.s, "cup_1"), (True, None))

    def test_read_grants_only_the_fields_it_read(self):
        env = env_of(self.label_scene(), pos=(4, 4), facing=0.0)
        granted = []

        class Recorder(object):
            def grant(self, eid, level, fields=None):
                granted.append((eid, level, fields))

        class Proxy(object):
            def __init__(self, inner):
                self.inner, self.realism = inner, Recorder()

            def __getattr__(self, name):
                return getattr(self.inner, name)

        env.world.begin()
        env.world.set_scalar("steps", env.world.s["steps"] + 1)
        camera.begin_step(env.world)
        ctx = Ctx(Proxy(env), ACTIONS["read"], {"target": "parcel_1"}, random.Random(0))
        ACTIONS["read"].fn(ctx)
        env.world.rollback()
        self.assertEqual(granted, [("parcel_1", "read", ["display"])])

    def test_big_text_reads_from_afar_and_small_print_needs_reach(self):
        sign = {"poster_1": item("lab", size=(0.6, 0.02, 0.8), pos=[9, 4], label="poster", display="EXIT",
                                 mounted=True, mount_height_m=1.2, view={"text_m": {"display": 0.03}})}
        env = env_of(sign, pos=(4, 4), facing=0.0)
        result, seconds = step(env, "read", "poster_1")
        self.assertEqual(json.loads(result["message"]), {"display": "EXIT"})
        self.assertGreaterEqual(seconds, 4.0)                           # 4 s (+ the head turning to it)
        self.assertFalse(env_of(sign, camera_mode="room", pos=(4, 4)).step("read", "poster_1")["ok"])   # room: reach
        small = env_of(self.label_scene(display=None, view={}), pos=(4, 4), facing=0.0)
        refused = small.step("read", "parcel_1")
        self.assertEqual((refused["ok"], refused["message"], refused["category"]),
                         (False, camera.TOO_SMALL_TEXT, "precondition"))
        dirty = env_of(self.label_scene(), pos=(5, 4), robot={"camera_clean": 0.3})
        self.assertEqual(dirty.step("read", "parcel_1")["message"], camera.DIRTY_TEXT)
        off = env_of(self.label_scene(), pos=(5, 4), robot={"camera_on": False})
        refused = off.step("read", "parcel_1")
        self.assertEqual((refused["message"], refused["category"]), (camera.CAMERA_OFF_TEXT, "perception"))

    def test_a_held_item_reads_at_a_free_angle(self):
        env = env_of({"jar_1": item("robot", label="jar", label_text="Basil", material="glass")}, pos=(5, 5),
                     robot={"hands": {"left": "jar_1", "right": None}})
        result, seconds = step(env, "read", "jar_1")
        self.assertEqual((json.loads(result["message"]), seconds), ({"label_text": "Basil"}, 5.0))

    def test_specular_glare_refuses_and_another_angle_reads(self):
        # a glossy tray on a table, the ceiling light beyond it: from the west the light mirrors into the head camera
        # the eye 0.73 m above the tray and 0.9 m west of it; the light 1.63 m above it and 2.1 m east: the mirror
        # angles agree to about a degree (glare within 10 degrees)
        scene = {"table_1": {"type": "table", "location": "lab", "pos": [6, 4], "accepts_items": True},
                 "tray_1": item("table_1", size=(0.3, 0.3, 0.02), label="tray", display="WELCOME", spos=[0.0, 0.1],
                                view={"glossy": True, "text_m": {"display": 0.03}}),
                 "light_1": {"type": "ceiling_light", "location": "lab", "pos": [10, 4], "on": True, "mounted": True,
                             "mount_height_m": 2.4}}
        glare = env_of(scene, pos=(4, 4), facing=0.0)
        refused = glare.step("read", "tray_1")
        self.assertEqual((refused["ok"], refused["message"], refused["category"]),
                         (False, camera.GLARE_TEXT, "perception"))
        side = env_of(scene, pos=(6, 8), facing=270.0)
        result = side.step("read", "tray_1")
        self.assertEqual(json.loads(result["message"]), {"display": "WELCOME"})


# ----- setup ops (10.5) ----------------------------------------------------------------------------------------------

def spec_of(setup, camera_mode="geometric", **extra):
    out = {"catalog_id": "T-camera-actions-test", "title": "camera ops", "house": {}, "setup": setup,
           "instruction": "x", "phrasings": ["x", "y", "z"],
           "success": [{"path": "facts.last_action.action", "op": "eq", "value": "look"}],
           "reference": [{"do": "look"}], "wrong": [{"name": "nothing", "plan": []}], "risk_level": 0}
    if camera_mode is not None:
        out["camera"] = camera_mode
    out.update(extra)
    return out


class SetupOpTests(unittest.TestCase):
    def build(self, setup, camera_mode="geometric", camera=None, **extra):
        return build(spec_of(setup, camera_mode, **extra), seed=0, camera=camera)

    def start_room(self):
        config, _, _ = self.build([])
        return config["robot"]["room"], config["robot"]["pos"]

    def test_the_camera_op_sets_pose_track_and_hardware(self):
        start, _ = self.start_room()
        setup = [{"op": "person", "var": "p", "id": "ann_1", "room": start},
                 {"op": "camera", "face": "north", "pan": 30, "tilt": -20, "track": ["{p}"],
                  "hw": {"head": {"hfov_deg": 90.0}}}]
        config, task, oracle = self.build(setup)
        self.assertEqual(task["camera"], "geometric")
        self.assertEqual(config["robot"]["facing_deg"], 270.0)
        self.assertEqual(config["robot"]["view"], {"pan_deg": 30.0, "tilt_deg": -20.0, "track": ["ann_1"],
                                                   "hw": {"head": {"hfov_deg": 90.0}}})
        env = make_env(config, task, 0)
        self.assertEqual(env.camera_mode, "geometric")
        robot = env.world.s["robot"]
        self.assertEqual((robot["facing_deg"], robot["view"]["pan_deg"], robot["view"]["tilt_deg"],
                          robot["view"]["track"]), (270.0, 30.0, -20.0, ["ann_1"]))
        self.assertEqual(camera.hw(env.world.s)["head"]["hfov_deg"], 90.0)
        room_config, room_task, _ = self.build(setup, camera="room")    # a run forced to the room camera skips them
        self.assertNotIn("view", room_config["robot"])
        self.assertNotIn("facing_deg", room_config["robot"])
        room_env = make_env(room_config, room_task, 0, camera="room")
        self.assertNotIn("view", room_env.world.s["robot"])

    def test_the_camera_op_faces_a_target_and_validates(self):
        start, pos = self.start_room()
        corner = [0, 0] if list(pos) != [0, 0] else [1, 1]
        config, _, _ = self.build([{"op": "camera", "face": corner}])
        x, y = (pos[0] + 0.5) * 0.5, (pos[1] + 0.5) * 0.5
        cx, cy = (corner[0] + 0.5) * 0.5, (corner[1] + 0.5) * 0.5
        self.assertEqual(config["robot"]["facing_deg"], round(math.degrees(math.atan2(cy - y, cx - x)) % 360.0, 2))
        config, _, oracle = self.build([{"op": "spawn", "var": "b", "type": "box", "floor": start},
                                        {"op": "camera", "face": "{b}"}])
        box = sight.entity_box(dict(config, people={}, pets={}), oracle["bindings"]["b"])
        bx, by = (box.x0 + box.x1) / 2.0, (box.y0 + box.y1) / 2.0
        self.assertEqual(config["robot"]["facing_deg"], round(math.degrees(math.atan2(by - y, bx - x)) % 360.0, 2))
        for op, words in (({"op": "camera", "face": "left"}, "face must be"),
                          ({"op": "camera", "camera_on": False}, "robot op"),
                          ({"op": "camera", "zoom": 2}, "unknown keys"),
                          ({"op": "camera", "pan": 200}, "pan must be"),
                          ({"op": "camera", "tilt": -80}, "tilt must be"),
                          ({"op": "camera", "track": ["nobody"]}, "track must"),
                          ({"op": "camera", "hw": {"head": {"zoom": 2}}}, "not a camera hardware value"),
                          ({"op": "camera", "hw": {"head": {"hfov_deg": -5}}}, "positive number"),
                          ({"op": "camera", "hw": {"head": {"tilt_limits_deg": [1]}}}, "list of 2 numbers")):
            with self.assertRaises(SpecError) as caught:
                self.build([op])
            self.assertIn(words, str(caught.exception), op)
        with self.assertRaises(SpecError) as caught:
            self.build([{"op": "camera", "face": 90}], camera_mode=None)
        self.assertIn("\"camera\": \"geometric\"", str(caught.exception))

    def test_the_view_op_merges_validated_properties(self):
        start, _ = self.start_room()
        setup = [{"op": "spawn", "var": "jar", "type": "generic_item", "floor": start},
                 {"op": "view", "target": "{jar}", "props": {"text_m": {"label_text": 0.01}, "glossy": True}},
                 {"op": "view", "target": "{jar}", "props": {"text_m": {"care_label": 0.003}}}]
        config, _, oracle = self.build(setup)
        jar = oracle["bindings"]["jar"]
        self.assertEqual(config["entities"][jar]["view"],
                         {"text_m": {"label_text": 0.01, "care_label": 0.003}, "glossy": True})
        room_config, _, room_oracle = self.build(setup, camera="room")
        self.assertNotIn("view", room_config["entities"][room_oracle["bindings"]["jar"]])
        for props, words in (({"zoom": 1}, "not a view property"), ({"porosity": 2}, "porosity must be"),
                             ({"opening": "side"}, "opening must be"), ({"faces": {"label": "north"}}, "faces must"),
                             ({"text_m": {"label_text": -1}}, "text_m must"), ({"camera": {"zoom": 1}}, "camera must"),
                             ({"beam": {"power": 1}}, "beam must"), ({}, "non-empty")):
            with self.assertRaises(SpecError) as caught:
                self.build(setup[:1] + [{"op": "view", "target": "{jar}", "props": props}])
            self.assertIn(words, str(caught.exception), props)

    def test_track_view_and_view_watch_write_the_camera_log(self):
        start, _ = self.start_room()
        setup = [{"op": "spawn", "var": "toy", "type": "generic_item", "floor": start},
                 {"op": "track_view", "ids": ["{toy}"]},
                 {"op": "view_watch", "name": "corner", "viewer": {"room": start, "cell": [0, 0], "z": 2.0,
                                                                   "yaw": 45.0, "hfov": 90.0},
                  "targets": ["{toy}", "robot"], "every_s": 30}]
        config, task, oracle = self.build(setup)
        log = config["camera_log"]
        toy = oracle["bindings"]["toy"]
        self.assertEqual(log["track_ids"], [toy])
        self.assertEqual(log["watch_specs"]["corner"]["every_s"], 30.0)
        env = make_env(config, task, 0)
        env.step("wait", seconds=60)
        self.assertIn("corner", env.world.s["camera_log"]["watch"])
        room_config, _, _ = self.build(setup, camera="room")
        self.assertNotIn("camera_log", room_config)
        bad = [({"op": "track_view", "ids": []}, "track_view: ids"),
               ({"op": "track_view", "ids": ["ghost_1"]}, "track_view: ids"),
               ({"op": "view_watch", "name": "a", "viewer": {"aperture": "window_1"}, "targets": ["robot"]}, "phase 3"),
               ({"op": "view_watch", "name": "a", "viewer": {"entity": "ghost"}, "targets": ["robot"]}, "unknown device"),
               ({"op": "view_watch", "name": "a", "viewer": {"room": start, "cell": [99, 0]}, "targets": ["robot"]},
                "a room viewer"),
               ({"op": "view_watch", "name": "a", "viewer": {"person": "ghost"}, "targets": ["robot"]}, "unknown person"),
               ({"op": "view_watch", "name": "a", "viewer": {"room": start, "cell": [0, 0]}, "targets": ["ghost"]},
                "targets must"),
               ({"op": "view_watch", "name": "a", "viewer": {"room": start, "cell": [0, 0]}, "targets": ["robot"],
                 "every_s": 5}, "every_s"),
               ({"op": "view_watch", "name": "", "viewer": {"room": start, "cell": [0, 0]}, "targets": ["robot"]},
                "name must")]
        for op, words in bad:
            with self.assertRaises(SpecError) as caught:
                self.build([op])
            self.assertIn(words, str(caught.exception), op)

    def test_spawn_rows_place_items_front_and_back_and_the_front_hides_the_back(self):
        start, _ = self.start_room()
        setup = [{"op": "spawn", "var": "shelf", "type": "shelf_unit", "furniture": start},
                 {"op": "spawn", "var": "tall", "type": "generic_item", "in": "{shelf}", "shelf": 1, "row": "front",
                  "props": {"size_m": [0.9, 0.12, 0.4], "label": "cereal boxes"}},
                 {"op": "spawn", "var": "jar", "type": "generic_item", "in": "{shelf}", "shelf": 1, "row": "back",
                  "props": {"size_m": [0.08, 0.08, 0.1], "label": "jar"}}]
        for mode in ("geometric", None):
            config, task, oracle = self.build(setup, camera_mode=mode)
            b = oracle["bindings"]
            depth = min(config["entities"][b["shelf"]]["size_m"][:2])
            self.assertEqual(config["entities"][b["tall"]]["inner_spos"], [0.0, 0.0])
            self.assertEqual(config["entities"][b["jar"]]["inner_spos"], [0.0, round(depth - 0.08, 3)])
        env = make_env(config, task, 0, camera="geometric")
        state = env.world.s
        shelf = sight.entity_box(state, b["shelf"])
        front = sight.front_deg(state, b["shelf"])
        cx, cy = (shelf.x0 + shelf.x1) / 2.0, (shelf.y0 + shelf.y1) / 2.0
        ex, ey = cx + math.cos(math.radians(front)) * 1.5, cy + math.sin(math.radians(front)) * 1.5
        eye = sight.Eye(shelf.rid, ex, ey, 1.5, ("robot",))
        self.assertEqual(sight.visible_fraction(state, b["jar"], eye)[0], 0.0)      # behind the taller front row
        self.assertGreater(sight.visible_fraction(state, b["tall"], eye)[0], 0.0)
        with self.assertRaises(SpecError):
            self.build(setup[:1] + [{"op": "spawn", "type": "generic_item", "floor": start, "row": "front"}])
        with self.assertRaises(SpecError):
            self.build(setup[:1] + [{"op": "spawn", "type": "generic_item", "in": "{shelf}", "row": "middle"}])
        with self.assertRaises(SpecError):
            self.build(setup[:1] + [{"op": "spawn", "type": "generic_item", "in": "{shelf}", "shelf": 1, "row": "front",
                                     "count": 12, "props": {"size_m": [0.12, 0.1, 0.1]}}])


# ----- spec key, runner, verifier, CLI ---------------------------------------------------------------------------------

class SpecAndRunnerTests(unittest.TestCase):
    def test_the_spec_accepts_camera_and_lints_its_use(self):
        self.assertIn("camera", spec_mod.ALLOWED)
        spec_mod.expand(spec_of([]))
        self.assertIn("camera must be room or geometric", spec_mod.check_structure(spec_of([], camera_mode="wide")))
        with self.assertRaises(SpecError):
            build(spec_of([], camera_mode="wide"), seed=0)
        problems = spec_mod.check_structure(spec_of([{"op": "camera", "face": 90}], camera_mode=None,
                                                    reference=[{"do": "peek", "target": "x", "where": "under"}],
                                                    success=[{"path": "camera_log.coverage.a", "op": "gte",
                                                              "value": 0.9}]))
        self.assertEqual(problems, ["uses the geometric camera (camera, camera_log.coverage.a, peek) without "
                                    "\"camera\": \"geometric\""])
        self.assertEqual(spec_mod.check_structure(spec_of([{"op": "camera", "face": 90}])), [])
        new = spec_of([], schema=3)
        self.assertIn("camera\": true", spec_mod.camera_problems(new)[0])
        new["wrong"][0]["camera"] = True
        self.assertEqual(spec_mod.camera_problems(new), [])
        variants = spec_of([], camera_mode=None, variants=[{"+setup": [{"op": "view", "target": "x", "props": {}}]}, {}])
        self.assertTrue(any("geometric camera" in p for p in spec_mod.check_structure(variants)))

    def test_every_authored_spec_still_passes_the_camera_lint(self):
        for path in sorted((ROOT / "tasks" / "authored").glob("*.json")):
            for spec in json.loads(path.read_text(encoding="utf-8")).get("specs", []):
                self.assertEqual(spec_mod.camera_problems(spec), [], spec.get("catalog_id"))

    def test_build_and_make_env_follow_the_precedence(self):
        config, task, _ = build(spec_of([]), seed=0)
        self.assertEqual(make_env(config, task, 0).camera_mode, "geometric")
        self.assertEqual(make_env(config, task, 0, camera="room").camera_mode, "room")
        config, task, _ = build(spec_of([], camera_mode=None), seed=0)
        self.assertNotIn("camera", task)
        self.assertEqual(make_env(config, task, 0).camera_mode, "room")
        self.assertEqual(make_env(config, task, 0, camera="geometric").camera_mode, "geometric")
        self.assertEqual(setup_mod.SetupCtx(config, default_registry(), random.Random(0), 0, {}).camera, "room")
        with self.assertRaises(SpecError):
            setup_mod.SetupCtx(config, default_registry(), random.Random(0), 0, {}, camera="wide")

    def test_trials_record_and_force_the_camera(self):
        plain = spec_of([], camera_mode=None)
        trial = run_trial(plain, 0)
        self.assertEqual((trial["camera"], trial["passed"]), ("room", True))
        forced = run_trial(plain, 0, camera="geometric")
        self.assertEqual((forced["camera"], forced["passed"]), ("geometric", True))
        own = run_trial(spec_of([]), 0)
        self.assertEqual(own["camera"], "geometric")
        record = verify_many([plain], seeds=(0,), workers=1, camera="geometric")[0]
        self.assertEqual([t["camera"] for t in record["trials"]], ["geometric"])
        self.assertEqual(run_trial(plain, 0, camera="room")["reference_state_hash"], trial["reference_state_hash"])

    def test_the_cli_takes_camera_and_reads_camera_commands(self):
        self.assertEqual(cli.command_of("peek bed_1 under"), {"action": "peek", "target": "bed_1", "where": "under"})
        self.assertEqual(cli.command_of("face 90"), {"action": "face", "deg": 90.0})
        self.assertEqual(cli.command_of("face north"), {"action": "face", "target": "north"})
        self.assertEqual(cli.command_of("inspect bed_1 under"),
                         {"action": "inspect", "target": "bed_1", "where": "under"})
        self.assertEqual(cli.command_of("look around"), {"action": "look", "target": "around", "destination": ""})
        self.assertEqual(cli.command_of('{"action": "look", "rows": 2}'), {"action": "look", "rows": 2})
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "specs.json")
            with open(path, "w", encoding="utf-8", newline="\n") as stream:
                json.dump({"specs": [spec_of([], camera_mode=None)]}, stream)
            out = io.StringIO()
            with redirect_stdout(out):
                code = cli.main(["--check", path, "--camera", "geometric", "--seeds", "0", "--workers", "1"])
            self.assertEqual(code, 0, out.getvalue())
            self.assertIn("PASS T-camera-actions-test", out.getvalue())
            state = os.path.join(tmp, "state.json")
            env_of().save(state)
            with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
                with open(os.devnull, "w") as devnull:
                    old, sys.stderr = sys.stderr, devnull
                    try:
                        cli.main(["--load-state", state, "--camera", "room"])
                    finally:
                        sys.stderr = old


# ----- reference skills (15) -----------------------------------------------------------------------------------------

class SkillTests(unittest.TestCase):
    def run_fetch(self, mode, entities, pos, obj, **robot):
        env = env_of(entities, camera_mode=mode, pos=pos, facing=0.0, robot=robot or None)
        env.trace = True
        run_plan(env, [{"skill": "fetch", "obj": obj}], {}, strict=True)
        return env, [(h["command"]["action"], h["command"].get("where")) for h in env._history]

    def test_fetch_under_a_bed_kneels_peeks_and_stands_up_in_geometric_mode(self):
        env, steps = self.run_fetch("geometric", BED_SCENE, (1, 6), "toy_1")
        self.assertIn(("kneel", None), steps)
        self.assertIn(("peek", "under"), steps)
        self.assertNotIn(("inspect", "under"), steps)
        self.assertEqual(steps[-2:], [("pick_up", None), ("stand", None)])
        self.assertEqual(env.world.s["robot"]["posture"], "standing")
        self.assertIn("toy_1", env.world.s["robot"]["hands"].values())
        room, room_steps = self.run_fetch("room", BED_SCENE, (1, 6), "toy_1")
        self.assertEqual([a for a, _ in room_steps], ["approach", "inspect", "crouch", "pick_up", "stand"])
        self.assertEqual(room_steps[1], ("inspect", "under"))

    def test_fetch_under_a_light_cushion_needs_no_kneeling(self):
        env, steps = self.run_fetch("geometric", SOFA_SCENE, (5, 4), "coin_1")
        self.assertNotIn(("kneel", None), steps)                       # the wrist lifts the cushion (C9)
        self.assertIn("coin_1", env.world.s["robot"]["hands"].values())

    def test_approach_walks_to_a_viewpoint_when_the_target_is_out_of_sight(self):
        scene = {"ward_1": {"type": "wardrobe", "location": "lab", "pos": [5, 4], "rot": 90},
                 "box_1": item("lab", size=(0.2, 0.2, 0.2), pos=[8, 5], label="box")}
        env = env_of(scene, pos=(1, 5), facing=0.0)
        self.assertFalse(env.perception.visible(env.world.s, "box_1"))
        env.trace = True
        run_plan(env, [{"skill": "approach", "target": "box_1"}], {}, strict=True)
        actions = [h["command"]["action"] for h in env._history]
        self.assertEqual(actions[0], "walk_to")
        self.assertIsNone(camera.geometry.reach_problem(env.world.s, "box_1"))
        room = env_of(scene, camera_mode="room", pos=(1, 5))
        run_plan(room, [{"skill": "approach", "target": "box_1"}], {}, strict=True)
        self.assertEqual([h["command"]["action"] for h in room._history], ["approach"])


# ----- sight fixes for the camera verbs (seats, things on things, lifted supports, rows) --------------------------------

class SightFixTests(unittest.TestCase):
    def eye(self, cell, z=1.5, ignore=("robot",)):
        return sight.Eye("lab", (cell[0] + 0.5) * 0.5, (cell[1] + 0.5) * 0.5, z, ignore)

    def test_a_sofa_is_solid_to_its_seat_and_its_back_hides_the_seat_from_behind(self):
        scene = {"sofa_1": {"type": "sofa", "location": "lab", "pos": [4, 4], "accepts_items": True},
                 "phone_1": item("sofa_1", size=(0.15, 0.07, 0.01), label="phone", spos=[1.0, 0.45]),
                 "cushion_1": item("sofa_1", size=(0.5, 0.5, 0.12), label="cushion", spos=[0.2, 0.2],
                                   accepts_items=True),
                 "remote_1": item("cushion_1", size=(0.18, 0.05, 0.03), label="remote"),
                 "toy_1": item("sofa_1", size=(0.1, 0.1, 0.05), label="toy", relation="under", spos=[1.0, 0.4])}
        state = env_of(scene, pos=(5, 8)).world.s
        self.assertEqual(sight.front_deg(state, "sofa_1"), 90.0)        # it faces south; its back is the north side
        for cell in ((5, 8), (5, 7), (1, 5), (10, 5)):                 # in front and from the sides
            self.assertEqual(sight.visible_fraction(state, "phone_1", self.eye(cell))[0], 1.0, cell)
            self.assertEqual(sight.visible_fraction(state, "remote_1", self.eye(cell))[0], 1.0, cell)
        for cell in ((5, 0), (5, 1)):                                   # behind its 0.85 m back
            self.assertEqual(sight.visible_fraction(state, "phone_1", self.eye(cell))[0], 0.0, cell)
            self.assertEqual(sight.visible_fraction(state, "remote_1", self.eye(cell))[0], 0.0, cell)
        self.assertEqual(sight.visible_fraction(state, "toy_1", self.eye((5, 8)))[0], 0.0)     # under the seat
        geom = sight.room_geom(state, "lab")
        spans = sorted((round(b[4], 2), round(b[5], 2)) for b in geom.boxes if b[7] == "sofa_1")
        self.assertEqual(spans, [(0.1, 0.45), (0.45, 0.85)])            # the seat body and the back strip

    def test_an_eye_that_ignores_a_support_sees_what_is_under_it(self):
        state = env_of(SOFA_SCENE, pos=(5, 2)).world.s
        plain = self.eye((5, 2), z=0.75)
        lifted = self.eye((5, 2), z=0.75, ignore=("robot", "cushion_1"))
        self.assertEqual(sight.visible_fraction(state, "coin_1", plain)[0], 0.0)
        self.assertEqual(sight.visible_fraction(state, "coin_1", lifted)[0], 1.0)

    def test_things_packed_into_one_box_do_not_hide_each_other(self):
        # B3 merge: an overfull shelf packs rows past the back at the back, so two things can share one box; neither
        # may hide the other (each blocked every ray to the other before, so a wrist peek never showed either), while
        # a thing in the front row still hides the back row
        pair = {"cab_1": {"type": "cabinet", "location": "lab", "pos": [4, 0], "open": True, "accepts_items": True},
                "tin_1": item("cab_1", size=(0.8, 0.6, 0.12), relation="in", shelf=0, label="tin"),
                "tin_2": item("cab_1", size=(0.8, 0.6, 0.12), relation="in", shelf=0, label="box")}
        state = env_of(pair, pos=(4, 2), facing=270.0).world.s
        self.assertEqual(sight.entity_box(state, "tin_1")[1:], sight.entity_box(state, "tin_2")[1:])
        low = sight.Eye("lab", 2.25, 1.25, 0.3, ("robot",))
        self.assertEqual([sight.visible_fraction(state, k, low)[0] for k in ("tin_1", "tin_2")], [1.0, 1.0])
        env = env_of(pair, pos=(4, 2), facing=270.0)
        self.assertTrue(env.step("peek", "cab_1", where="inside")["ok"])
        self.assertTrue({"tin_1", "tin_2"} <= set(seen(env)))
        rows = {"cab_1": pair["cab_1"],
                "jar_1": item("cab_1", size=(0.5, 0.3, 0.12), relation="in", shelf=0, label="jar"),
                "jar_2": item("cab_1", size=(0.5, 0.3, 0.12), relation="in", shelf=0, label="tin"),
                "jar_3": item("cab_1", size=(0.5, 0.3, 0.12), relation="in", shelf=0, label="box")}
        state = env_of(rows, pos=(4, 2), facing=270.0).world.s
        front = sight.entity_box(state, "jar_1")
        self.assertEqual(sight.entity_box(state, "jar_2")[1:], sight.entity_box(state, "jar_3")[1:])
        self.assertGreater(front.y1, sight.entity_box(state, "jar_2").y1)          # jar_1 is in front of the pair
        self.assertEqual([sight.visible_fraction(state, k, low)[0] for k in ("jar_1", "jar_2", "jar_3")],
                         [1.0, 0.5, 0.5])                                        # the front row still hides half

    def test_row_places_against_the_opening_or_the_back(self):
        config_ = config({"shelf_1": {"type": "shelf_unit", "location": "lab", "pos": [4, 0], "accepts_items": True},
                          "a_1": item("shelf_1", size=(0.3, 0.1, 0.2), relation="in", shelf=1, inner_spos=[0.0, 0.0]),
                          "b_1": item("shelf_1", size=(0.3, 0.1, 0.2), relation="in", shelf=1, label="new")})
        state = HouseEnv(config_, seed=0).world.s
        self.assertEqual(sight.row_spos(state, "shelf_1", "b_1", "front"), [0.3, 0.0])     # beside a_1
        self.assertEqual(sight.row_spos(state, "shelf_1", "b_1", "back"), [0.0, 0.35])
        self.assertIsNone(sight.row_spos(state, "shelf_1", "b_1", "middle"))
        state["entities"]["b_1"]["size_m"] = [1.2, 0.1, 0.2]
        self.assertIsNone(sight.row_spos(state, "shelf_1", "b_1", "front"))                # wider than the shelf


# ----- determinism, persistence, legacy frames, realistic perception --------------------------------------------------

SCRIPT = [("look", "around", {}), ("face", "east", {}), ("kneel", "", {}), ("peek", "bed_1", {"where": "under"}),
          ("inspect", "bed_1", {"where": "under"}), ("look", "", {"track": True}), ("pick_up", "toy_1", {}),
          ("stand", "", {}), ("look", "", {"pan": -60.0, "tilt": 10.0}), ("wait", "", {"seconds": 60})]


def run_script(env, script=SCRIPT):
    out = []
    for action, target, kw in script:
        result = env.step(action, target, **kw)
        out.append((result["ok"], result["message"], digest(env.world.s), digest(env.observe())))
    return out


class DeterminismTests(unittest.TestCase):
    def test_the_camera_verbs_are_deterministic(self):
        a = run_script(env_of(BED_SCENE, pos=(5, 5), people={"ann": {"location": "lab", "pos": [1, 1]}}))
        b = run_script(env_of(BED_SCENE, pos=(5, 5), people={"ann": {"location": "lab", "pos": [1, 1]}}))
        self.assertEqual(a, b)
        self.assertTrue(all(ok for ok, _, _, _ in a), [m for ok, m, _, _ in a if not ok])

    def test_save_and_load_mid_script_continue_identically(self):
        env = env_of(BED_SCENE, pos=(5, 5))
        run_script(env, SCRIPT[:4])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            env.save(path)
            loaded = HouseEnv.load(path)
        self.assertEqual(digest(loaded.world.s), digest(env.world.s))
        self.assertEqual(run_script(loaded, SCRIPT[4:]), run_script(env, SCRIPT[4:]))

    def test_observations_do_not_depend_on_the_hash_seed(self):
        code = ("import sys; sys.path.insert(0, {root!r}); sys.path.insert(0, {tests!r});"
                "from test_camera_actions import env_of, run_script, BED_SCENE;"
                "print(run_script(env_of(BED_SCENE, pos=(5, 5)))[-1][2:])").format(root=str(ROOT),
                                                                                  tests=str(ROOT / "tests"))
        outs = set()
        for seed in ("1", "987"):
            env = dict(os.environ, PYTHONHASHSEED=seed)
            proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=str(ROOT))
            self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
            outs.add(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(len(outs), 1)

    def test_legacy_nine_element_wrist_frames_render(self):
        env = env_of(BED_SCENE, pos=(5, 5), robot={"posture": "kneeling"})
        env.step("peek", "bed_1", where="under")
        w = env.world
        w.begin()
        view_ = w.robot_m()["view"]
        view_["frames"] = [f[:9] for f in view_["frames"]]              # frames written before CAM-ACTIONS
        sv = camera.compute_view(w.s, w, env.perception)
        w.rollback()
        self.assertEqual(sv.entities["toy_1"]["via"], "wrist")

    @unittest.skipUnless(realistic_geometric_available(), "realistic perception with the geometric camera (CAM-RENDER)")
    def test_realistic_perception_with_the_camera_verbs(self):
        env = env_of(BED_SCENE, perception="realistic", pos=(5, 5))
        handle = env.realism.handles.handle("bed_1")
        for action, target, kw in (("look", "around", {}), ("face", "east", {}), ("kneel", "", {}),
                                   ("peek", handle, {"where": "under"})):
            result = env.step(action, target, **kw)
            self.assertTrue(result["ok"], (action, result["message"]))
            self.assertNotIn("bed_1", result["message"])
        self.assertTrue(env.world.s["entities"]["toy_1"].get("revealed"))
        obs = env.observe()
        self.assertNotIn("toy_1", json.dumps(obs))
        self.assertEqual(env.step("face", "bed_1")["message"], "That is not visible here")   # true ids never work


# ----- cost (TIER1_INTEGRATION 3.4, TIER1_CAMERA 16; tests fail at 3x the budget) ---------------------------------------

def best_ms(fn, repeat=5):
    out = []
    for _ in range(repeat):
        start = time.perf_counter()
        fn()
        out.append(time.perf_counter() - start)
    return min(out) * 1000.0


class PerformanceTests(unittest.TestCase):
    def end_step_ms(self, env, frames_):
        w = env.world

        def run():
            w.begin()
            w.set_scalar("steps", w.s["steps"] + 1)
            camera.begin_step(w)
            view_ = w.robot_m()["view"]
            view_["frames"] = copy.deepcopy(frames_)
            camera.end_step(env)
            w.rollback()
        run()
        return best_ms(run)

    def test_look_around_and_peek_frames_stay_within_budget(self):
        for seed in (0, 7):
            env = HouseEnv(seed=seed, camera="geometric", observe_on_step=False)
            env.step("look")
            one = self.end_step_ms(env, frames(env))
            env.step("look", "around")
            around = self.end_step_ms(env, frames(env))
            self.assertLess(around - one, 12.0, seed)                   # budget: 4 ms extra for a look-around
        env = env_of(BED_SCENE, pos=(5, 5), robot={"posture": "kneeling"})
        env.step("peek", "bed_1", where="under")
        self.assertLess(self.end_step_ms(env, frames(env)), 9.0)       # budget: end_step 3 ms indoors


if __name__ == "__main__":
    unittest.main()
