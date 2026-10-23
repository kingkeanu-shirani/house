"""Tier-1 camera rendering (CAM-RENDER, kernel v3.0): realistic perception with the geometric camera
(docs/design/TIER1_CAMERA.md 10.6, 11, 15, 16, 17; TIER1_INTEGRATION.md 1.9 rule 5, 1.12, 1.14, conflict 21).

What is pinned here: the view levels (dark shape, detect, recognize, identify) and what each renders, the 10-24 px
detection band (a fixed draw per viewpoint), per-target context, per-field acuity, face gating, tracked identity
(updated once per step by note_step, persisted), the hidden camera fields, the realistic "view" and camera / lidar
blocks, mirrors in frame, hazards of undetected sources, people the camera cannot identify (sighting handles),
look-alike field-set discipline, determinism, persistence, the observe-cost bound, the leak audit and fuzz flags, the
harness / dataset / leaderboard / viewer camera plumbing, the LLM prompt and the SearchAgent. Room-mode realistic
observations are pinned byte for byte by tests/test_room_golden.py; here direct checks make sure they carry nothing new.

Every scene is built from core types, so the file passes with HOUSE_SIM_DOMAINS=core as well as with every domain
loaded, normal and HOUSE_SIM_STRICT=1.
"""
import copy
import importlib.util
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from house_sim.generator import generate_house
from house_sim.kernel import camera, sight
from house_sim.kernel import realism as realism_module
from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.plugins import ACTIONS
from house_sim.kernel.predicates import digest
from house_sim.kernel.registry import default_registry
from house_sim.kernel.realism import INSPECT, LIGHT5, RECOGNIZE_FIELDS, VIEW_LEVELS

ROOT = Path(__file__).resolve().parent.parent
COLS, ROWS = 12, 10
DETECT_KEYS = {"color", "kind", "label", "location", "pos", "relation", "shape", "size_class", "view"}


# ----- scenes ---------------------------------------------------------------------------------------------------------

def config(entities=None, pos=(1, 1), facing=0.0, people=None, pets=None, light=True, robot=None, cols=COLS,
           rows=ROWS, dim=None):
    """A lit 6 x 5 m living room ("lab", a ceiling light in its north-east corner; dim: its dim_level) with a lit
    hallway next door."""
    lamp = {"type": "ceiling_light", "location": "lab", "pos": [cols - 1, 0], "on": light, "mounted": True,
            "mount_height_m": 2.4}
    if dim is not None:
        lamp["dim_level"] = dim
    ents = {"light_1": lamp,
            "light_2": {"type": "ceiling_light", "location": "hall", "pos": [3, 5], "on": True, "mounted": True,
                        "mount_height_m": 2.4}}
    ents.update(copy.deepcopy(entities or {}))
    rob = {"room": "lab", "pos": list(pos), "facing_deg": facing}
    rob.update(copy.deepcopy(robot or {}))
    return {"schema_version": 2, "start_room": "lab",
            "rooms": {"lab": {"size_m": [cols * 0.5, rows * 0.5], "type": "living_room", "exits": ["hall"]},
                      "hall": {"size_m": [2.0, 3.0], "type": "hallway", "exits": ["lab"]}},
            "entities": ents, "people": copy.deepcopy(people or {}), "pets": copy.deepcopy(pets or {}), "robot": rob}


def item(location, size=(0.1, 0.1, 0.1), **props):
    out = {"type": "generic_item", "location": location, "size_m": list(size), "label": props.pop("label", "thing")}
    out.update(props)
    return out


def wall_item(col, size=0.1, height=1.4, row=1, **props):
    """A small thing mounted at eye height (in every level frame whatever its distance)."""
    return item("lab", (size, size, size), pos=[col, row], mounted=True, mount_height_m=height, **props)


def env_of(entities=None, perception="realistic", camera_mode="geometric", seed=0, task=None, **kw):
    return HouseEnv(config(entities, **kw), task=task, seed=seed, perception=perception, camera=camera_mode)


def handle(env, eid):
    return env.realism.handles.handle(eid)


def shown(env, eid, obs=None):
    """The realistic record of eid in an observation (None when it is not listed)."""
    obs = env.observe() if obs is None else obs
    h = env.realism.handles.by_id.get(eid)
    return obs["visible_entities"].get(h) if h else None


def info(env, eid):
    return camera.step_view(env).entities.get(eid)


def move_robot(env, pos=None, facing=None, room=None):
    """Put the robot elsewhere between steps (a plain state edit) and take a 1 s step there, so the step view, its
    note and the observation follow (the camera's end-of-step fallback frames the new pose)."""
    robot = env.world.s["robot"]
    if room is not None:
        robot["room"] = room
    if pos is not None:
        robot["pos"] = list(pos)
    if facing is not None:
        robot["facing_deg"] = float(facing)
    result = env.step("wait", seconds=1)
    assert result["ok"], result["message"]
    return result


def walk_strings(value, path="obs"):
    if isinstance(value, dict):
        for k, v in value.items():
            yield path, k
            for item_ in walk_strings(v, "{}.{}".format(path, k)):
                yield item_
    elif isinstance(value, list):
        for v in value:
            for item_ in walk_strings(v, path):
                yield item_


def _audit_module():
    spec = importlib.util.spec_from_file_location("house_ai_perception_audit_cr", str(ROOT / "tools" / "perception_audit.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ----- the mode and the guard (conflict 21) ----------------------------------------------------------------------------

class ModeTests(unittest.TestCase):
    def test_realistic_perception_runs_with_the_geometric_camera(self):
        env = env_of({"cup": wall_item(5, label="cup", category="dish")})
        obs = env.observe()
        self.assertEqual(env.camera_mode, "geometric")
        self.assertEqual(obs["perception"], "realistic")
        self.assertIn("camera", obs)
        self.assertIn("lidar", obs)
        self.assertNotIn("camera_log", obs)
        for path, key in walk_strings(obs):
            self.assertNotEqual(key, "_view", path)
        for cmd in (("look", "around"), ("move", "hall"), ("move", "lab")):
            result = env.step(*cmd)
            self.assertTrue(result["ok"], result["message"])
            for t in realism_module.TOKEN_RE.findall(json.dumps(result)):
                self.assertNotIn(t, ("cup", "light_1", "light_2"))

    def test_room_mode_realistic_observations_carry_no_camera_data(self):
        env = env_of({"cup": wall_item(5, label="cup", category="dish")}, camera_mode="room")
        obs = env.observe()
        self.assertNotIn("camera", obs)
        self.assertNotIn("lidar", obs)
        for rec in obs["visible_entities"].values():
            self.assertNotIn("view", rec)
        self.assertEqual(env.realism.dump().get("geo"), None)
        self.assertFalse(any("ident" in entry for entry in env.realism.knowledge.data.values()))

    def test_view_and_inner_spos_are_hidden_but_a_domain_view_word_keeps_its_tier(self):
        env = env_of({"box": wall_item(4, size=0.3, label="box", view={"porosity": 0.3}, inner_spos=[0.1, 0.2])})
        rec = shown(env, "box")
        self.assertIsNotNone(rec)
        self.assertEqual(set(rec["view"]) - set(_audit_module().VIEW_KEYS), set())
        self.assertNotIn("porosity", json.dumps(rec))
        self.assertNotIn("inner_spos", rec)
        record = env.world.s["entities"]["box"]
        self.assertEqual(env.realism.tier(record, "view"), "never")
        self.assertEqual(env.realism.tier(record, "inner_spos"), "never")
        # outdoor_plants' roof faces carry a "view" word (clear / partial / blocked): not the camera's, tier unchanged
        self.assertEqual(env.realism.tier(dict(record, view="clear"), "view"), "inspect")


# ----- view levels (11.1) ----------------------------------------------------------------------------------------------

class LevelTests(unittest.TestCase):
    def scene(self, col, size, **props):
        props.setdefault("label", "red block")
        props.setdefault("category", "toy")
        props.setdefault("color", "red")
        env = env_of({"block": wall_item(col, size=size, **props)})
        return env, info(env, "block"), shown(env, "block")

    def test_each_view_level_renders_its_contract(self):
        seen = set()
        for col, size in ((3, 0.1), (5, 0.1), (9, 0.1), (11, 0.1), (3, 0.3)):
            env, view, rec = self.scene(col, size, soil={"dust": 0.5}, wetness=0.4, open=True, openable=True)
            if view is None or rec is None:
                continue
            level = view["level"]
            seen.add(level)
            if level == "identify":
                self.assertEqual(rec["label"], "red block")
                self.assertIn("size_m", rec)
            elif level == "recognize":
                self.assertEqual((rec["label"], rec["kind"]), ("toy", "toy"))
                self.assertIn("open", rec)                        # a large-scale state
                self.assertNotIn("size_m", rec)
                self.assertNotIn("soil", rec)
                self.assertNotIn("wetness", rec)
            elif level == "detect":
                self.assertEqual((rec["label"], rec["kind"]), ("object", "unknown"))
                self.assertEqual(set(rec), DETECT_KEYS)
        self.assertEqual(seen, {"identify", "recognize", "detect"})

    def test_detect_shows_the_same_fields_for_any_type(self):
        keys = []
        for props in ({"label": "red block", "category": "toy"},
                      {"label": "glass vase", "category": "decor", "material": "glass", "fragile": True,
                       "openable": True, "open": False, "temperature_c": 30.0}):
            env, view, rec = self.scene(9, 0.1, color="blue", **props)
            self.assertEqual(view["level"], "detect")
            self.assertGreaterEqual(view["px"], 24.0)
            keys.append(sorted(rec))
        self.assertEqual(keys[0], keys[1])

    def test_colour_at_detect_needs_light(self):
        env = env_of({"block": wall_item(9, color="red", category="toy")})
        self.assertEqual(info(env, "block")["level"], "detect")
        self.assertIn("color", shown(env, "block"))
        dim = env_of({"block": wall_item(9, size=0.2, color="red", category="toy")}, dim=0.15)
        view = info(dim, "block")
        self.assertLess(view["light"], realism_module.COLOR_LIGHT)
        rec = shown(dim, "block")
        if rec is not None:
            self.assertNotIn("color", rec)

    def test_fill_bucket_at_recognize_from_48_px(self):
        found = {}
        for col in (4, 6, 8, 10):
            env = env_of({"jar": wall_item(col, size=0.2, label="jar", category="jar", fill_level=0.5,
                                           openable=True, open=True, transparent=True)})
            view, rec = info(env, "jar"), shown(env, "jar")
            if view and view["level"] == "recognize" and rec is not None:
                found[view["px"] >= realism_module.FILL_PX] = rec
        self.assertTrue(found, "no recognize-level view of the jar")
        if True in found:
            self.assertEqual(found[True]["fill_level"], "half")
        if False in found:
            self.assertNotIn("fill_level", found[False])

    def test_imaging_off_shows_depth_shapes_only(self):
        people = {"ann": {"label": "Ann", "role": "adult", "location": "lab", "pos": [6, 1], "posture": "standing"}}
        env = env_of({"crate": item("lab", (0.6, 0.6, 0.6), pos=[4, 1], label="crate"),
                      "key": wall_item(3, label="key")}, people=people, robot={"privacy_mode": True})
        obs = env.observe()
        listed = {env.realism.handles.resolve(h): rec for h, rec in obs["visible_entities"].items()}
        self.assertIn("crate", listed)
        self.assertNotIn("key", listed)
        self.assertEqual(listed["crate"]["label"], "dark shape")
        self.assertEqual(listed["crate"]["view"]["via"], "depth")
        self.assertNotIn("light", listed["crate"]["view"])      # the depth camera measures no light
        self.assertNotIn("ann", obs["people"])                  # who it is is not visible
        (key, person), = obs["people"].items()
        self.assertEqual(person["label"], "person")
        self.assertTrue(key.startswith("obj_"))


# ----- the detection band (11.2) ---------------------------------------------------------------------------------------

class BandTests(unittest.TestCase):
    def test_band_draw_is_fixed_per_viewpoint_and_rerolls_with_it(self):
        env = env_of({})
        state, realism = env.world.s, env.realism

        def view(px, yaw=10.0, via="head"):
            return {"level": "detect", "px": px, "frame_yaw": yaw, "via": via}
        self.assertTrue(realism.geo_detected(state, "x", None, view(24.0)))
        self.assertFalse(realism.geo_detected(state, "x", None, view(10.0)))
        draws = [realism.geo_detected(state, "thing_{}".format(i), None, view(17.0)) for i in range(400)]
        self.assertTrue(0.4 <= sum(draws) / 400.0 <= 0.6, sum(draws))         # p = (17 - 10) / 14 = 0.5
        again = [realism.geo_detected(state, "thing_{}".format(i), None, view(17.0)) for i in range(400)]
        self.assertEqual(draws, again)
        turned = [realism.geo_detected(state, "thing_{}".format(i), None, view(17.0, yaw=100.0)) for i in range(400)]
        self.assertNotEqual(draws, turned)                     # another 45-degree sector re-rolls
        same_sector = [realism.geo_detected(state, "thing_{}".format(i), None, view(17.0, yaw=40.0)) for i in range(400)]
        self.assertEqual(draws, same_sector)
        env.world.s["robot"]["pos"] = [2, 1]
        stepped = [realism.geo_detected(state, "thing_{}".format(i), None, view(17.0)) for i in range(400)]
        self.assertNotEqual(draws, stepped)                    # another cell re-rolls

    def test_clutter_lowers_the_band(self):
        env = env_of({"shelf": item("lab", (1.0, 0.4, 1.0), pos=[5, 0], surface=True, accepts_items=True),
                      **{"bit_{}".format(i): item("shelf", (0.03, 0.03, 0.03), relation="on") for i in range(9)}})
        state, realism = env.world.s, env.realism
        view = {"level": "detect", "px": 20.0, "frame_yaw": 0.0, "via": "head"}
        self.assertAlmostEqual(sight.detect_probability(20.0, 8), (20.0 - 10.0) / 14.0 - 0.25)
        hits = sum(realism.geo_detected(state, "bit_{}".format(i), state["entities"]["bit_{}".format(i)], view)
                   for i in range(9))
        self.assertLessEqual(hits, 9)

    def test_a_small_thing_is_sure_near_and_unsure_far(self):
        near = env_of({"bead": wall_item(3, size=0.04, label="bead")})
        far = env_of({"bead": wall_item(10, size=0.04, label="bead")})
        self.assertGreaterEqual(info(near, "bead")["px"], 24.0)
        self.assertIsNotNone(shown(near, "bead"))
        view = info(far, "bead")
        self.assertTrue(view is None or view["px"] < 24.0)

    def test_the_band_is_stable_while_the_robot_stands_still(self):
        ents = {"bead_{}".format(i): wall_item(9 + i % 3, size=0.03 + 0.003 * i, row=1 + i // 3, label="bead")
                for i in range(9)}
        env = env_of(ents)
        first = sorted(env.observe()["visible_entities"])
        self.assertEqual(sorted(env.observe()["visible_entities"]), first)
        result = env.step("wait", seconds=5)
        self.assertEqual(sorted(result["observation"]["visible_entities"]), first)

    def test_inspected_things_and_missed_follow_the_band(self):
        env = env_of({"bead_{}".format(i): wall_item(9, size=0.06, row=i, label="bead") for i in range(1, 9)})
        state, realism = env.world.s, env.realism
        sv = camera.step_view(env)
        band = [e for e, v in sv.entities.items() if v["level"] == "detect" and v["px"] < 24.0]
        self.assertTrue(band)
        missed = [e for e in band if not realism.geo_detected(state, e, state["entities"][e], sv.entities[e])]
        for eid in band:
            self.assertEqual(realism.missed(state, eid, state["entities"][eid], realism.context(state)),
                             eid in missed)
        if missed:
            eid = missed[0]
            realism.knowledge.grant(eid, state["entities"][eid], INSPECT)
            self.assertTrue(realism.geo_detected(state, eid, state["entities"][eid], sv.entities[eid]))
            self.assertIsNotNone(shown(env, eid))


# ----- tracked identity (11.2) -----------------------------------------------------------------------------------------

class TrackingTests(unittest.TestCase):
    def scene(self):
        env = env_of({"mug": wall_item(3, label="blue mug", category="dish", color="blue", soil={"stain": 0.5}),
                      "other": wall_item(10, label="red cup", category="dish", color="red", row=3)})
        self.assertEqual(info(env, "mug")["level"], "identify")
        return env

    def test_identity_is_tracked_until_the_thing_moves_unseen(self):
        env = self.scene()
        self.assertEqual(shown(env, "mug")["label"], "blue mug")
        self.assertEqual(env.realism.knowledge.ident_level("mug", env.world.s["entities"]["mug"]), 3)
        move_robot(env, pos=(10, 1), facing=180.0)
        self.assertEqual(info(env, "mug")["level"], "detect")
        rec = shown(env, "mug")
        self.assertEqual((rec["label"], rec["kind"]), ("blue mug", "dish"))       # who it is, tracked ...
        self.assertNotIn("soil", rec)                                              # ... not how it is
        self.assertNotIn("size_m", rec)
        env.world.s["entities"]["mug"]["pos"] = [3, 2]                             # moved while nobody looked
        env.step("wait", seconds=1)
        self.assertEqual(info(env, "mug")["level"], "detect")
        self.assertEqual(shown(env, "mug")["label"], "object")

    def test_a_thing_first_seen_far_is_an_object(self):
        env = env_of({"mug": wall_item(8, label="blue mug", category="dish")}, pos=(1, 1))
        self.assertEqual(info(env, "mug")["level"], "detect")
        self.assertEqual(shown(env, "mug")["label"], "object")
        self.assertEqual(env.realism.knowledge.ident_level("mug", env.world.s["entities"]["mug"]), 1)

    def test_tracking_survives_save_load_and_replay(self):
        env = self.scene()
        move_robot(env, pos=(10, 1), facing=180.0)
        env.step("look")
        obs = env.observe()
        with tempfile.TemporaryDirectory() as tmp:
            spath, rpath = os.path.join(tmp, "s.json"), os.path.join(tmp, "r.json")
            env.save(spath)
            loaded = HouseEnv.load(spath)
            self.assertEqual(loaded.camera_mode, "geometric")
            self.assertEqual(digest(loaded.observe()), digest(obs))
            dumped = loaded.realism.dump()
            self.assertIn("ident", dumped["knowledge"]["mug"])

    def test_note_step_runs_once_per_step_whatever_observe_does(self):
        commands = [("wait", "", {"seconds": 1}), ("look", "", {}), ("move", "hall", {}), ("move", "lab", {}),
                    ("wait", "", {"seconds": 1})]
        finals = []
        for observes in (0, 1, 3):
            env = env_of({"mug": wall_item(3, label="blue mug", category="dish"),
                          "bead": wall_item(9, size=0.03, label="bead")}, pos=(1, 1))
            env.observe_on_step = False
            for action, target, kw in commands:
                env.step(action, target, **kw)
                for _ in range(observes):
                    env.observe()
            finals.append((digest(env.observe()), digest(env.realism.dump())))
        self.assertEqual(len(set(finals)), 1)


# ----- context, acuity and faces ---------------------------------------------------------------------------------------

class ContextTests(unittest.TestCase):
    def test_view_context_follows_the_spec_formula(self):
        env = env_of({}, robot={"camera_clean": 0.55, "speed_mode": "fast"})
        state = env.world.s
        base = 1.0 + 1.5 * (0.7 - 0.55) / 0.3 + 1.0
        ctx = env.realism.view_context(state, {"light": 0.25, "px": 32.0, "visible": 0.75, "glare": "backlit",
                                               "range_m": 2.0})
        expected = base + 0.6 * math.log(128.0 / 32.0, 2) + 0.8 * 0.25 + 1.5 + 1.5
        self.assertAlmostEqual(ctx["m"], expected)
        self.assertEqual((ctx["light"], ctx["dist"]), (0.25, 4.0))
        bright = env.realism.view_context(state, {"light": 0.9, "px": 400.0, "visible": 1.0, "glare": "none",
                                                  "range_m": 1.0})
        self.assertAlmostEqual(bright["m"], base)

    def test_far_things_are_judged_more_coarsely_than_near_ones(self):
        env = env_of({"near": wall_item(3, size=0.3, label="crate"), "far": wall_item(10, size=0.3, label="crate",
                                                                                   row=2)})
        state = env.world.s
        near = env.realism.context(state, "near", state["entities"]["near"])
        far = env.realism.context(state, "far", state["entities"]["far"])
        self.assertLess(near["m"], far["m"])


class AcuityAndFaceTests(unittest.TestCase):
    def test_a_field_that_needs_inspect_acuity_shows_only_up_close(self):
        props = {"label": "plate", "category": "dish", "crack": "hairline",
                 "_perception": {"glance": ["crack"], "acuity": {"crack": "inspect"}}}
        near = env_of({"plate": wall_item(4, size=0.3, **props)})
        far = env_of({"plate": wall_item(9, size=0.3, **props)})
        self.assertTrue(sight.inspect_capable(info(near, "plate")["px"], info(near, "plate")["light"]))
        self.assertEqual(shown(near, "plate")["crack"], "hairline")
        self.assertEqual(info(far, "plate")["level"], "identify")
        self.assertNotIn("crack", shown(far, "plate"))
        room = env_of({"plate": wall_item(9, size=0.3, **props)}, camera_mode="room")
        self.assertEqual(shown(room, "plate")["crack"], "hairline")        # room mode ignores acuity
        self.assertEqual(near.realism.acuity_need(near.world.s["entities"]["plate"], "open"), "recognize")
        self.assertEqual(near.realism.acuity_need(near.world.s["entities"]["plate"], "color"), "identify")

    def test_a_type_acuity_block_is_read(self):
        reg = default_registry()
        block = dict(reg.perception.get("generic_item") or {}, acuity={"crack": "recognize"})
        with mock.patch.dict(reg.perception, {"generic_item": block}):
            env = env_of({"plate": wall_item(9, size=0.3, label="plate", crack="hairline",
                                             _perception={"glance": ["crack"]})})
            record = env.world.s["entities"]["plate"]
            self.assertEqual(env.realism.acuity_need(record, "crack"), "recognize")

    def test_face_gated_fields_need_their_face_seen(self):
        reg = default_registry()
        block = dict(reg.perception.get("generic_item") or {}, faces={"stain": "label"})
        out = {}
        with mock.patch.dict(reg.perception, {"generic_item": block}):
            for facing in ("front", "back"):
                ents = {"tin": item("lab", (0.3, 0.1, 0.3), pos=[5, 0], mounted=True, mount_height_m=1.3,
                                    label="tin", stain="coffee", label_facing=facing, _perception={"glance": ["stain"]})}
                env = env_of(ents, pos=(5, 4), facing=270.0)
                view = info(env, "tin")
                self.assertEqual(view["level"], "identify")
                out[facing] = (view["faces"], shown(env, "tin"))
        self.assertIn("label", out["front"][0])
        self.assertEqual(out["front"][1]["stain"], "coffee")
        self.assertNotIn("label", out["back"][0])
        self.assertNotIn("stain", out["back"][1])


# ----- views, blocks, mirrors, hazards ---------------------------------------------------------------------------------

class ViewAndBlockTests(unittest.TestCase):
    def test_the_realistic_view_is_coarse(self):
        env = env_of({"mug": wall_item(4, label="mug"), "box": wall_item(11, size=0.4, row=6, label="box")})
        problems_of = _audit_module().view_problems
        for eid in ("mug", "box"):
            rec, view = shown(env, eid), info(env, eid)
            self.assertEqual(problems_of(rec["view"], set(LIGHT5[1])), [])
            self.assertEqual(rec["view"]["bearing_deg"] % 5.0, 0.0)
            self.assertEqual(rec["view"]["occluded"], round(1.0 - view["visible"], 2))
            if "range_m" in rec["view"]:
                self.assertLess(abs(rec["view"]["range_m"] - view["range_m"]), 0.35)
        oracle = env_of({"mug": wall_item(4, label="mug")}, perception="oracle")
        self.assertIsInstance(oracle.observe()["visible_entities"]["mug"]["view"]["light"], float)

    def test_range_only_within_the_depth_cameras_reach(self):
        env = env_of({"far": item("lab", (0.6, 0.6, 1.2), pos=[11, 9], label="cabinet")}, pos=(0, 0), facing=40.0,
                     cols=30, rows=20)
        view = info(env, "far")
        rec = shown(env, "far")
        self.assertGreater(view["range_m"], 6.0)
        self.assertNotIn("range_m", rec["view"])

    def test_the_camera_block_is_coarsened_and_its_ids_translated(self):
        env = env_of({"mug": wall_item(4, label="mug"), "cup": wall_item(4, label="cup", row=8)})
        w = env.world
        w.begin()
        camera.witness(w, "phys.fall", source="mug", size_m=0.5)
        camera.witness(w, "phys.fall", source="cup", size_m=0.5)
        w.commit()
        obs = env.observe()
        block = obs["camera"]
        self.assertIn(block["light"], LIGHT5[1])
        self.assertEqual(set(block) - set(_audit_module().CAMERA_KEYS), set())
        self.assertNotIn("on", block)
        self.assertEqual(block["pose"]["cell"], obs["robot"]["pos"])
        ids = [e.get("id") for e in block["events"]]
        self.assertIn(handle(env, "mug"), ids)
        for event in block["events"]:
            self.assertEqual(event["bearing_deg"] % 5.0, 0.0)
            self.assertNotIn(event.get("id"), ("mug", "cup"))

    def test_a_mirror_looks_like_a_doorway_only_while_in_frame(self):
        mirror = item("lab", (0.6, 0.05, 1.0), pos=[5, 0], mounted=True, mount_height_m=0.8, label="mirror",
                      category="mirror")
        facing = env_of({"mirror": mirror}, pos=(5, 4), facing=270.0)
        obs = facing.observe()
        doors = [c for c in obs["connections"] if c.get("kind") == "doorway" and c.get("to") == "lab"]
        self.assertEqual(len(doors), 1)
        self.assertNotIn(handle(facing, "mirror"), obs["visible_entities"])
        away = env_of({"mirror": mirror}, pos=(5, 4), facing=90.0)
        obs = away.observe()
        self.assertEqual([c for c in obs["connections"] if c.get("to") == "lab"], [])

    def test_hazards_of_undetected_sources_are_dropped(self):
        ents = {"pin_{}".format(i): item("lab", (0.02, 0.02, 0.02), pos=[6 + i % 4, 2 + i // 4], portable=True,
                                         label="pin") for i in range(12)}
        env = env_of(ents, robot={"view": {"tilt_deg": -45}})
        obs = env.observe()
        perceived = {env.realism.handles.resolve(h) for h in obs["visible_entities"]}
        for hz in obs["grid"]["hazards"]:
            src = env.realism.handles.resolve(hz["source"]) if str(hz["source"]).startswith("obj_") else hz["source"]
            if src in env.world.s["entities"]:
                self.assertIn(src, perceived)


# ----- people and pets -------------------------------------------------------------------------------------------------

class PeopleTests(unittest.TestCase):
    PEOPLE = {"ann": {"label": "Ann", "role": "adult", "location": "lab", "pos": [6, 1], "posture": "standing",
                      "mood": "happy"}}

    def test_identified_people_keep_their_ids_and_get_a_view(self):
        env = env_of({}, people=self.PEOPLE)
        person = env.observe()["people"]["ann"]
        self.assertEqual(person["label"], "Ann")
        self.assertIn("smiling", person["cues"])
        self.assertNotIn("_view", person)
        self.assertEqual(person["view"]["via"], "head")

    def test_people_the_camera_cannot_identify_are_shapes_per_sighting(self):
        env = env_of({}, people=self.PEOPLE, robot={"privacy_mode": True})
        (first, rec), = env.observe()["people"].items()
        self.assertTrue(first.startswith("obj_"))
        self.assertEqual(rec["label"], "person")
        self.assertEqual(env.realism.inbound({"target": first}, entity_keys=("target",))["target"], "ann")
        with self.assertRaises(Exception) as caught:
            env.realism.inbound({"target": "ann"}, entity_keys=("target",))
        self.assertIn("too dark to tell", str(caught.exception).lower())
        env.world.s["robot"]["privacy_mode"] = False
        env.step("wait", seconds=1)
        self.assertIn("ann", env.observe()["people"])
        env.world.s["robot"]["privacy_mode"] = True
        env.step("wait", seconds=1)
        (second, _), = env.observe()["people"].items()
        self.assertNotEqual(first, second)            # a new sighting cannot be linked to the last

    def test_inspecting_a_backlit_person_tells_nothing_about_who_it_is(self):
        """Stage-B review (camera, high and low): a person seen against a sunlit window from across the room is a
        "person" shape, yet inspecting the shape's handle returned her name, role, activity and mood; naming her by id
        was refused with the darkness text in a lit room. Up close (identified) the inspection reads her."""
        people = {"ann_1": {"label": "Ann", "role": "resident", "location": "lab", "pos": [9, 5],
                            "posture": "standing", "activity": "reading", "mood": "sad"}}

        def backlit(lamp):
            cfg = config({"win_1": {"type": "window", "location": "lab", "pos": [11, 5], "covered": 0.0}},
                         pos=(2, 5), facing=0.0, light=lamp, people=people)
            cfg["clock"] = {"start": "2026-10-09T12:00:00"}
            return HouseEnv(cfg, seed=0, camera="geometric", perception="realistic")
        env = backlit(False)
        env.step("look")
        (shape, rec), = env.observe()["people"].items()
        self.assertEqual(rec["label"], "person")
        result = env.step("inspect", shape)
        self.assertTrue(result["ok"], result["message"])
        details = json.loads(result["message"])
        self.assertEqual((details["label"], details["note"]), ("person", camera.WHO_TEXT))
        for key in ("role", "activity", "cues", "mood", "posture"):
            self.assertNotIn(key, details)
        self.assertNotIn("Ann", result["message"])
        self.assertNotIn("ann_1", env.world.s["facts"].get("inspected", {}))
        refused = env.step("inspect", "ann_1")
        self.assertEqual((refused["ok"], refused["message"]), (False, realism_module.FAR_WHO))
        lit = backlit(True)                                # the room light on: she is identified and read
        lit.step("look")
        self.assertIn("ann_1", lit.observe()["people"])
        details = json.loads(lit.step("inspect", "ann_1")["message"])
        self.assertEqual(details.get("activity"), "reading")

    def test_a_person_the_camera_identifies_is_read_by_inspect_as_the_observation_shows_her(self):
        """Stage-B fix round: inspect follows the observation's identify level (64 px, light 0.2, not backlit), not the
        160 px of an object's close look. Ann 9.5 m down a long lit room is named in the observation with her
        activity, so inspecting her reads her too (a shape answer there would contradict the observation)."""
        people = {"ann_1": {"label": "Ann", "role": "resident", "location": "lab", "pos": [20, 2],
                            "posture": "standing", "activity": "reading", "mood": "sad"}}
        cfg = config({}, pos=(1, 2), facing=0.0, cols=24, rows=5, people=people)
        cfg["entities"]["light_1"]["pos"] = [20, 2]
        env = HouseEnv(cfg, seed=0, camera="geometric", perception="realistic")
        env.step("look")
        found = camera.acuity(env, "ann_1")
        self.assertEqual(found["level"], "identify")
        self.assertLess(found["px"], 160.0)
        self.assertEqual(env.observe()["people"]["ann_1"]["activity"], "reading")
        result = env.step("inspect", "ann_1")
        self.assertTrue(result["ok"], result["message"])
        self.assertEqual(json.loads(result["message"]).get("activity"), "reading")
        self.assertIn("ann_1", env.world.s["facts"].get("inspected", {}))

    def test_people_in_a_dark_room_are_dark_spell_shapes_with_views(self):
        env = env_of({}, people=self.PEOPLE, light=False)
        obs = env.observe()
        self.assertTrue(obs["dark"])
        for key, rec in obs["people"].items():
            self.assertTrue(key.startswith("obj_"))
            self.assertEqual(rec["label"], "person")


# ----- look-alikes -----------------------------------------------------------------------------------------------------

class LookAlikeTests(unittest.TestCase):
    def test_look_alikes_render_identically_below_identify(self):
        for col, level in ((9, "detect"), (6, "recognize")):
            records = []
            for name, text in (("salt", "SALT"), ("sugar", "SUGAR")):
                env = env_of({"jar": wall_item(col, size=0.12, label=name + " jar", category="jar",
                                               looks_like="white_powder_jar", label_text=text)})
                view = info(env, "jar")
                if view is None or view["level"] != level:
                    continue
                rec = shown(env, "jar")
                rec.pop("view")
                records.append(rec)
            if len(records) == 2:
                self.assertEqual(records[0], records[1], level)


# ----- determinism, persistence, cost -----------------------------------------------------------------------------------

class DeterminismTests(unittest.TestCase):
    def test_observations_are_identical_across_hash_seeds(self):
        code = ("import sys; sys.path.insert(0, {root!r}); import warnings; warnings.simplefilter('ignore'); "
                "from house_sim.generator import generate_house; from house_sim.kernel.engine import HouseEnv; "
                "from house_sim.kernel.predicates import digest; "
                "e=HouseEnv(generate_house(2), seed=2, perception='realistic', camera='geometric'); "
                "r=e.step('look', 'around'); rid=e.world.s['robot']['room']; "
                "r=e.step('move', e.world.s['rooms'][rid]['exits'][0]); "
                "print(digest(e.observe()), digest(e.realism.dump()))").format(root=str(ROOT))
        outs = set()
        for hashseed in ("1", "987"):
            env = dict(os.environ, PYTHONHASHSEED=hashseed)
            proc = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=600)
            self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
            outs.add(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(len(outs), 1)

    def test_replay_reproduces_the_realistic_geometric_episode(self):
        env = HouseEnv(generate_house(3), seed=3, perception="realistic", camera="geometric")
        rid = env.world.s["robot"]["room"]
        for cmd in (("look", "around"), ("move", env.world.s["rooms"][rid]["exits"][0]), ("wait",)):
            env.step(*cmd)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.json")
            env.save_replay(path)
            replayed = HouseEnv.replay(path)
        self.assertEqual(replayed.camera_mode, "geometric")
        self.assertEqual(digest(replayed.observe()), digest(env.observe()))
        self.assertEqual(digest(replayed.realism.dump()), digest(env.realism.dump()))

    def test_geometric_realistic_observe_overhead_is_small(self):
        # TIER1_CAMERA 16: target 3 ms over realistic room mode, test bound 10 ms (seed 7, best of 5)
        geo = HouseEnv(generate_house(7), seed=7, perception="realistic", camera="geometric", trace=False)
        room = HouseEnv(generate_house(7), seed=7, perception="realistic", trace=False)

        if os.environ.get("HOUSE_SIM_PERF_TESTS", "1") == "0":
            self.skipTest("wall-clock bound off (HOUSE_SIM_PERF_TESTS=0: CI runs the bounds alone)")
        def timed(e, fresh=False):
            t = time.perf_counter()
            for _ in range(10):
                if fresh:
                    e._camera_view = None            # the step view computed inside observe(), as after an edit
                e.observe()
            return (time.perf_counter() - t) / 10
        timed(geo), timed(room)
        extra = min(timed(geo) for _ in range(5)) - min(timed(room) for _ in range(5))
        fresh = min(timed(geo, True) for _ in range(5)) - min(timed(room) for _ in range(5))
        self.assertLess(extra, 0.010)
        self.assertLess(fresh, 0.010)


# ----- tools ----------------------------------------------------------------------------------------------------------

class ToolTests(unittest.TestCase):
    def test_perception_audit_geometric_walk_is_clean(self):
        audit = _audit_module()
        result = audit.run_house((4, {}, 25, "geometric"))
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["leaks"], [])

    def test_camera_leak_checks_catch_planted_leaks(self):
        audit = _audit_module()
        env = env_of({"mug": wall_item(4, label="mug")})
        obs = env.observe()
        leaks = []
        audit.check_camera_obs(env, obs, leaks, "clean")
        self.assertEqual(leaks, [])
        bad = copy.deepcopy(obs)
        h = handle(env, "mug")
        bad["visible_entities"][h]["view"]["bearing_deg"] = 12.3
        bad["visible_entities"][h]["_view"] = {"level": "identify"}
        bad["camera"]["light"] = 0.71
        bad["camera"]["lens"] = "clean"
        bad["camera_log"] = {}
        leaks = []
        audit.check_camera_obs(env, bad, leaks, "bad")
        text = " ".join(d for _, _, d in leaks)
        for needle in ("bearing_deg", "_view", "camera_log", "light 0.71", "keys ['lens']"):
            self.assertIn(needle, text)
        room = env_of({"mug": wall_item(4, label="mug")}, camera_mode="room")
        leaks = []
        planted = room.observe()
        planted["lidar"] = {}
        audit.check_camera_obs(room, planted, leaks, "room")
        self.assertTrue(leaks)

    def test_fuzz_replays_a_realistic_geometric_house_deterministically(self):
        spec = importlib.util.spec_from_file_location("house_ai_fuzz_cr", str(ROOT / "tools" / "fuzz.py"))
        fuzz = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fuzz)
        result = fuzz.run_house((1, {}, 25, "geometric", "realistic"))
        self.assertEqual(result["defects"], [], result["defects"][:1])
        self.assertEqual(fuzz.replay(1, {}, result["log"], "geometric", "realistic"), result["digest"])
        self.assertEqual(fuzz._perception_of("mixed", 3), "realistic")
        self.assertEqual(fuzz._perception_of("mixed", 4), "oracle")


# ----- harness, dataset, leaderboard, viewer, prompt -------------------------------------------------------------------

class HarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from house_sim.eval import run as R
        from house_sim.tasks.runner import build
        cls.R = R
        cls.record = None
        for record in R.load_specs():
            if record["spec"].get("perception") == "realistic":
                continue
            try:
                config, task, _plans = build(record["spec"], 0)
                HouseEnv(config, task, 0, trace=False)
            except Exception:       # noqa: BLE001 -- needs a domain this process did not load (HOUSE_SIM_DOMAINS)
                continue
            cls.record = record
            break

    def test_episode_records_and_logs_carry_the_camera_mode(self):
        from house_sim.eval.agents import make_agent
        if self.record is None:
            self.skipTest("no authored spec builds with the loaded domains")
        R = self.R
        for forced, expected in ((None, "room"), ("geometric", "geometric")):
            episode = {"catalog_id": self.record["catalog_id"], "split": "dev", "seed": 0, "seed_tag": "dev:0",
                       "phrasing": None, "perception": "realistic"}
            with tempfile.TemporaryDirectory() as tmp:
                agent = make_agent("random", seed=0, max_actions=3)
                record = R.run_episode(self.record, episode, agent, caps={"max_steps": 4}, out_dir=tmp,
                                       camera=forced, save_replay=False)
                self.assertEqual(record["camera"], expected)
                log = json.loads((Path(tmp) / record["log"]).read_text(encoding="utf-8"))
                self.assertEqual(log["camera"], expected)
                rescored = R.replay_log(log, self.record)
                self.assertEqual(rescored["steps"], record["steps"])
        with self.assertRaises(ValueError):
            R.run_episode(self.record, episode, make_agent("random", seed=0), camera="panoramic")
        self.assertIn("camera", R.RECORD_KEYS)
        parser = R.build_parser()
        self.assertEqual(parser.parse_args(["--run-agent", "random", "--camera", "geometric"]).camera, "geometric")

    def test_the_harness_builds_episodes_in_the_run_camera_like_the_verifier(self):
        # B3 merge: runner.build applies a geometric spec's camera setup ops only in a geometric run (CAM-ACTIONS), so
        # every harness rebuild (the episode, the oracle's shadow, re-scoring, leaderboard replay, the viewer) passes
        # the run's camera and perception, as verify does; a --camera room run must not keep the camera ops' effects
        from house_sim.eval import agents as A
        from house_sim.eval import leaderboard as lb
        from house_sim.eval.agents import make_agent
        from house_sim.tasks import runner
        if self.record is None:
            self.skipTest("no authored spec builds with the loaded domains")
        spec = copy.deepcopy(self.record["spec"])
        built = runner.build(spec, 0)[0]
        target = sorted(e for e in built["entities"] if "view" not in built["entities"][e])[0]
        spec["camera"] = "geometric"
        spec["setup"] = list(spec.get("setup") or []) + [{"op": "view", "target": target, "props": {"glossy": True}}]
        record = dict(self.record, spec=spec)
        calls, real = [], runner.build

        def spy(spec_, seed=0, registry=None, camera=None, perception=None):
            calls.append((camera, perception))
            return real(spec_, seed, registry, camera=camera, perception=perception)
        for forced, expected in (("room", "room"), ("geometric", "geometric"), (None, "geometric")):
            verified = real(spec, 0, camera=forced, perception="realistic")[0]
            self.assertEqual("view" in verified["entities"][target], expected == "geometric")
            episode = {"catalog_id": self.record["catalog_id"], "split": "dev", "seed": 0, "seed_tag": "dev:0",
                       "phrasing": None, "perception": "realistic"}
            del calls[:]
            with tempfile.TemporaryDirectory() as tmp, mock.patch.object(runner, "build", spy):
                result = self.R.run_episode(record, episode, make_agent("random", seed=0, max_actions=2),
                                            caps={"max_steps": 3}, out_dir=tmp, camera=forced)
                from house_sim.kernel import replays       # compact replays: read() unpacks house-replay-v3
                replay = replays.read(next((Path(tmp) / "replays").glob("*.json")))
                log = json.loads((Path(tmp) / result["log"]).read_text(encoding="utf-8"))
                self.R.replay_log(log, record)
                lb.replay_episode(spec, 0, log, camera=log.get("camera"))
                A.ScriptedOracle().prepare(copy.deepcopy(spec), dict(episode), perception="realistic",
                                           camera=result["camera"])
            self.assertEqual((replay["camera"], result["camera"]), (expected, expected))
            initial = replay["initial"]["entities"][target]
            self.assertEqual((initial.get("view") or {}).get("glossy"), True if expected == "geometric" else None)
            # the episode with the forced mode; re-scoring, the leaderboard replay and the oracle with the logged one
            self.assertEqual(calls, [(forced, "realistic")] + [(expected, "realistic")] * 3)

    def test_a_spy_agent_sees_only_realistic_geometric_observations(self):
        from house_sim.eval import agents as A
        if self.record is None:
            self.skipTest("no authored spec builds with the loaded domains")

        class Spy(A.Agent):
            name = "spy"

            def __init__(self):
                self.seen, self.results = [], []

            def reset(self, instruction, observation, action_catalog):
                self.seen.append(observation)
                self.catalog = [a["name"] for a in action_catalog]

            def act(self, observation, last_result):
                self.seen.append(observation)
                self.results.append(last_result)
                if len(self.seen) > 4:
                    return {"action": "done"}
                return {"action": "look", "target": "around"}
        spy = Spy()
        episode = {"catalog_id": self.record["catalog_id"], "split": "dev", "seed": 0, "seed_tag": "dev:0",
                   "phrasing": None, "perception": "realistic"}
        record = self.R.run_episode(self.record, episode, spy, camera="geometric")
        self.assertEqual(record["camera"], "geometric")
        from house_sim.tasks.runner import build
        config, _task, _plans = build(self.record["spec"], 0)
        true_ids = {e for e in config["entities"] if "_" in e}
        for obs in spy.seen:
            self.assertEqual(obs["perception"], "realistic")
            self.assertIn("camera", obs)
            text = json.dumps(obs)
            self.assertNotIn("_view", text)
            self.assertNotIn("camera_log", text)
            self.assertEqual(set(realism_module.TOKEN_RE.findall(text)) & true_ids, set())
        for result in spy.results[1:]:
            self.assertEqual(set(result), set(A.RESULT_KEYS))

    def test_leaderboard_config_labels_the_camera(self):
        from house_sim.eval import leaderboard as lb
        run = {"perception": "realistic", "camera": None, "require_done": True, "caps": [], "options": None,
               "completeness": {"complete": True}}
        room = lb.episode_config({"perception": "realistic"}, run)
        geo = lb.episode_config({"perception": "realistic", "camera": "geometric"}, run)
        self.assertEqual(room["camera"], "room")
        self.assertEqual(geo["camera"], "geometric")
        self.assertNotIn("geometric camera", lb.config_tags(room))
        self.assertIn("geometric camera", lb.config_tags(geo))
        self.assertEqual(lb.config_track(geo), "standard")
        self.assertIn("camera", lb.CSV_FIELDS)
        self.assertEqual(lb._meta_camera({"camera": None, "camera_modes": ["geometric"]}), "geometric")
        self.assertEqual(lb._meta_camera({}), None)

    def test_llm_prompt_mentions_the_camera_only_when_offered(self):
        from house_sim.eval import llm
        from house_sim.eval.agents import action_catalog
        room = env_of({}, camera_mode="room")
        geo = env_of({})
        room_prompt = llm.system_prompt(action_catalog(room))
        self.assertNotIn("obs.camera", room_prompt)
        catalog = action_catalog(geo)
        if not any(item["name"] in llm.CAMERA_VERBS for item in catalog):
            catalog = catalog + [{"name": "face", "params": {}, "doc": ""}]
        prompt = llm.system_prompt(catalog)
        for word in ("obs.camera", "obs.lidar", "face", "look around", "peek"):
            self.assertIn(word, prompt)

    def test_dataset_meta_carries_the_camera(self):
        from house_sim.eval import dataset
        self.assertIn("camera", dataset.__doc__)
        source = Path(dataset.__file__).read_text(encoding="utf-8")
        self.assertIn('"camera": ref.get("camera") or "room"', source)

    def test_viewer_shows_the_camera(self):
        from house_sim.eval import viewer
        env = HouseEnv(generate_house(3), seed=3, perception="realistic", camera="geometric")
        rid = env.world.s["robot"]["room"]
        env.step("look", "around")
        env.step("move", env.world.s["rooms"][rid]["exits"][0])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.json")
            env.save_replay(path)
            data = viewer.build_viewer_data(path)
            html_path = viewer.export_viewer(path, os.path.join(tmp, "v.html"))
            page = Path(html_path).read_text(encoding="utf-8")
        self.assertEqual(data["meta"]["camera"], "geometric")
        oracle = viewer.decode_deltas(data["frames"])[-1]["cam"]
        perceived = viewer.decode_deltas(data["pframes"])[-1]["cam"]
        self.assertEqual(oracle["fd"], round(env.world.s["robot"]["facing_deg"], 1))
        self.assertIn("sc", oracle)
        self.assertNotIn("sc", perceived)                 # the seen-cells overlay is privileged
        self.assertEqual(perceived["fc"], env.observe()["camera"]["frames"])
        self.assertIn(".fov {", page)
        room = HouseEnv(generate_house(3), seed=3, perception="realistic")
        room.step("look")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.json")
            room.save_replay(path)
            data = viewer.build_viewer_data(path)
        self.assertEqual(data["meta"]["camera"], "room")
        self.assertNotIn("cam", viewer.decode_deltas(data["frames"])[-1])


# ----- the SearchAgent (TIER1_INTEGRATION 4) ------------------------------------------------------------------------------

class SearchAgentTests(unittest.TestCase):
    def test_the_search_agent_fetches_with_the_geometric_camera(self):
        if "rows" not in ACTIONS["look"].params:
            self.skipTest("needs look around with rows (CAM-ACTIONS)")
        from house_sim.agent import SearchAgent
        from house_sim.world import make_house
        # test_kernel's fetch houses exactly as its baseline builds them (K-CAM, CAM-RENDER / B3-MERGE not_done): all
        # eight seeds, the cup in a 10 cm cabinet on the odd ones, seed 7 the 22-room house, both perception modes,
        # within test_kernel's 600 steps
        for mode in ("oracle", "realistic"):
            for seed in range(8):
                with self.subTest(seed=seed, mode=mode):
                    c = make_house(1 + seed * 3, 1 + seed % 3 if seed else 1, seed)
                    if seed % 2:
                        c["entities"]["cup"]["location"] = "cabinet_" + str(1 + seed)
                    e, policy, last = HouseEnv(c, perception=mode, camera="geometric"), SearchAgent(), None
                    for _ in range(600):
                        if e.task_complete():
                            break
                        command = policy.act(e.observe(), last)
                        outcome = e.step(**command)
                        last = {k: outcome[k] for k in ("ok", "message", "category")}
                        if command["action"] == "handoff":
                            break
                    self.assertTrue(e.task_complete())

    def test_the_search_agent_finds_a_cup_inside_a_cabinet(self):
        # B3 merge: test_kernel's fetch houses with the cup moved into a cabinet, found by opening it and peeking inside
        # with the wrist camera. K-CAM: the 10 cm cabinets as they are in both modes (the agent remembers that a small
        # cabinet showed "openable" from close by, and inspects what the wrist sees half hidden inside), and a
        # cabinet of a real size in realistic perception as before
        if "rows" not in ACTIONS["look"].params or "peek" not in ACTIONS:
            self.skipTest("needs the camera verbs (CAM-ACTIONS)")
        from house_sim.agent import SearchAgent
        from house_sim.world import make_house
        for mode, size in (("oracle", None), ("realistic", None), ("realistic", 0.4)):
            for seed in (1, 3):
                with self.subTest(seed=seed, mode=mode):
                    c = make_house(1 + seed * 3, 1 + seed % 3, seed)
                    cabinet = "cabinet_" + str(1 + seed)
                    c["entities"]["cup"]["location"] = cabinet
                    if size:
                        c["entities"][cabinet]["size_m"] = [size, size, size]
                    e, policy, last, peeked = HouseEnv(c, perception=mode, camera="geometric"), SearchAgent(), None, False
                    for _ in range(600):
                        if e.task_complete():
                            break
                        command = policy.act(e.observe(), last)
                        outcome = e.step(**command)
                        last = {k: outcome[k] for k in ("ok", "message", "category")}
                        peeked = peeked or (command["action"] == "peek" and command.get("where") == "inside")
                        if command["action"] == "handoff":
                            break
                    self.assertTrue(e.task_complete())
                    self.assertTrue(peeked)

    def test_room_mode_commands_are_unchanged(self):
        from house_sim.agent import SearchAgent
        from house_sim.world import make_house
        c = make_house(4, 1, 1)
        e, policy = HouseEnv(c), SearchAgent()
        first = policy.act(e.observe())
        self.assertNotEqual(first.get("action"), "look")
        self.assertEqual(policy.looked, set())

    def test_unsupported_camera_forms_are_dropped(self):
        from house_sim.agent import SearchAgent
        agent = SearchAgent()
        agent.room_now = "lab"
        agent.last = {"action": "look", "target": "around", "rows": 2}
        agent.looked.add("lab")
        agent._note_refusal({"ok": False, "message": "Unknown parameter for look: rows", "category": "argument"})
        self.assertIn("rows", agent.unsupported)
        self.assertNotIn("lab", agent.looked)
        agent.last = {"action": "peek", "target": "obj_1", "where": "under"}
        agent._note_refusal({"ok": False, "message": "Unknown action: peek", "category": "argument"})
        self.assertIn("peek", agent.unsupported)


if __name__ == "__main__":
    unittest.main()
