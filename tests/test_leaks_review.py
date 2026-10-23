"""Regression tests for the kernel perception leaks of the 2026-10-22 independent review (K-LEAK, phase 1; review
sections 1 and 4, phases/p3_leaks_identity.md and p3_leaks_side.md). Each test fails on the tree before the fix.

  L1  wait(until), watch and guards judge what the robot perceives (rendered values), never the true state: no
      exact-value oracle, no asleep / awake oracle (review H6, C-ORACLE)
  I1  the printed address does not encode the seed and the variant is not the seed's residue (review H5, V-VAR)
  I5  handles and fixed draws follow a per-episode nonce; only handles this episode issued are accepted (review H8,
      R5, SECRECY)
  I6  free text and handle-taking parameters are no existence oracle (review I6, R6)
  L2  the geometric camera's pixels follow the size the record shows (review M1 "view.px")
  I3  ctx.grant refuses a close look the robot could not have had from where it is (review H7, R4)
  L3  the battery is no scale: hold power and walking work follow the load word (body.py / proprio.py; INT)

Scenes use core types (generic_item, ceiling_light), so the file runs with HOUSE_SIM_DOMAINS=core too; the look-alike
pixel tests need domain look-alike groups and skip without them.
"""
import copy
import json
import os
import random
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import house_sim.generator as generator_module
from house_sim.generator import generate_house
from house_sim.kernel import camera, sight, timeline
from house_sim.kernel import realism as realism_module
from house_sim.kernel.actions import Ctx
from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.plugins import ACTIONS
from house_sim.kernel.registry import default_registry
from house_sim.tasks import spec as spec_module
from house_sim.tasks.runner import build

COLS, ROWS = 12, 10


def config(entities=None, pos=(1, 1), facing=0.0, people=None, pets=None, robot=None):
    """A lit 6 x 5 m living room ("lab", a ceiling light in a corner) with a lit hallway next door."""
    ents = {"light_1": {"type": "ceiling_light", "location": "lab", "pos": [COLS - 1, 0], "on": True, "mounted": True,
                        "mount_height_m": 2.4},
            "light_2": {"type": "ceiling_light", "location": "hall", "pos": [1, 1], "on": True, "mounted": True,
                        "mount_height_m": 2.4}}
    ents.update(copy.deepcopy(entities or {}))
    rob = {"room": "lab", "pos": list(pos), "facing_deg": facing}
    rob.update(copy.deepcopy(robot or {}))
    return {"schema_version": 2, "start_room": "lab",
            "rooms": {"lab": {"size_m": [COLS * 0.5, ROWS * 0.5], "type": "living_room", "exits": ["hall"]},
                      "hall": {"size_m": [2.0, 3.0], "type": "hallway", "exits": ["lab"]}},
            "entities": ents, "people": copy.deepcopy(people or {}), "pets": copy.deepcopy(pets or {}), "robot": rob}


def thing(location="lab", size=(0.12, 0.12, 0.3), **props):
    out = {"type": "generic_item", "location": location, "size_m": list(size), "label": props.pop("label", "thing")}
    out.update(props)
    return out


def wall_thing(col, row=1, height=1.4, **props):
    """A thing mounted at eye height in front of a robot at (1, 1) facing east: in every frame at identify level."""
    return thing("lab", pos=[col, row], mounted=True, mount_height_m=height, **props)


def env_of(entities=None, camera_mode="room", seed=0, task=None, perception="realistic", **kw):
    return HouseEnv(config(entities, **kw), task=task, seed=seed, perception=perception, camera=camera_mode)


def shown_handle(env, eid, obs=None):
    """The handle an observation shows for eid (never issues one)."""
    obs = obs if obs is not None else env.observe()
    handle = env.realism.handles.by_id.get(eid)
    return handle if handle in (obs.get("visible_entities") or {}) else None


def wait_until(env, path, op, value):
    result = env.step("wait", seconds=1, until={"path": path, "op": op, "value": value})
    return result["ok"], result["category"], result["message"]


# ----- L1: conditions on rendered values ------------------------------------------------------------------------------

class ConditionsJudgeWhatTheRobotPerceivesTests(unittest.TestCase):
    """Review H6 / L1: a wait's until, a standing order's condition and a guard compare the value the observation shows
    (realism.shown_entity), so twins whose true values differ but render alike answer alike."""

    VALUES = {"wetness": (0.1732, 0.0321, "0.1", "0.17", "0.0", "looks dry", "wet"),
              "level": (0.4371, 0.3611, "0.4", "0.3", "half", "low", "full")}
    OPS = ("eq", "ne", "in", "not_in", "startswith", "contains", "is_true", "is_false")

    def twin(self, wet, level, camera_mode):
        entities = {"towel_1": wall_thing(4, wetness=wet, label="towel"),
                    "pail_1": wall_thing(5, row=2, level=level, label="pail")}
        return env_of(entities, camera_mode=camera_mode)

    def answers(self, env):
        obs = env.observe()
        out = []
        for eid, field in (("towel_1", "wetness"), ("pail_1", "level")):
            handle = shown_handle(env, eid, obs)
            self.assertIsNotNone(handle, eid)
            out.append(("shown", obs["visible_entities"][handle].get(field)))
            for op in self.OPS:
                for value in self.VALUES[field]:
                    value = [value] if op in ("in", "not_in") else str(value) if op in ("startswith", "contains") \
                        else value
                    out.append((eid, op, value, wait_until(env, "entities.{}.{}".format(handle, field), op, value)))
        return out

    def test_until_answers_alike_for_values_that_render_alike(self):
        for camera_mode in ("room", "geometric"):
            a = self.answers(self.twin(0.1732, 0.4371, camera_mode))
            b = self.answers(self.twin(0.0321, 0.3611, camera_mode))
            self.assertEqual(a[0], b[0], camera_mode)          # the twins look the same ...
            self.assertEqual(a, b, camera_mode)                # ... so every answer is the same

    def test_until_ends_on_the_shown_word_never_on_the_true_number(self):
        for camera_mode in ("room", "geometric"):
            env = self.twin(0.1732, 0.4371, camera_mode)
            towel = shown_handle(env, "towel_1")
            self.assertEqual(env.observe()["visible_entities"][towel]["wetness"], "looks dry")
            ok, _, message = wait_until(env, "entities.{}.wetness".format(towel), "eq", "looks dry")
            self.assertTrue(ok and message.startswith("Waited 0 s"), (camera_mode, message))
            ok, _, message = wait_until(env, "entities.{}.wetness".format(towel), "eq", 0.1732)
            self.assertTrue(ok and message.startswith("Waited 1 s"), (camera_mode, message))
            ok, _, message = wait_until(env, "entities.{}.wetness".format(towel), "startswith", "0.17")
            self.assertTrue(ok and message.startswith("Waited 1 s"), (camera_mode, message))

    def test_oracle_perception_still_compares_the_true_value(self):
        env = env_of({"towel_1": wall_thing(4, wetness=0.1732, label="towel")}, perception="oracle")
        result = env.step("wait", seconds=1, until={"path": "entities.towel_1.wetness", "op": "eq", "value": 0.1732})
        self.assertTrue(result["message"].startswith("Waited 0 s"), result["message"])

    def people_twin(self, posture, pet_posture):
        people = {"resident_1": {"label": "Sam", "role": "adult", "location": "lab", "pos": [4, 3],
                                 "posture": posture, "awake": posture != "sleeping",
                                 "activity": "sleeping" if posture == "sleeping" else "resting"}}
        pets = {"dog_1": {"label": "Rex", "species": "dog", "location": "lab", "pos": [6, 4],
                          "posture": pet_posture, "activity": "sleeping" if pet_posture == "sleeping" else "resting"}}
        return env_of({}, people=people, pets=pets)

    def posture_answers(self, env):
        obs = env.observe()
        out = [("shown", obs["people"]["resident_1"]["posture"], obs["pets"]["dog_1"]["posture"])]
        for path, values in (("people.resident_1.posture", ("sleeping", "lying", "resting")),
                             ("pets.dog_1.posture", ("sleeping", "resting", "lying"))):
            for op in ("eq", "ne", "in", "startswith"):
                for value in values:
                    out.append((path, op, value, wait_until(env, path, op, [value] if op == "in" else value)))
        return out

    def test_asleep_and_lying_awake_answer_alike(self):
        asleep = self.posture_answers(self.people_twin("sleeping", "sleeping"))
        awake = self.posture_answers(self.people_twin("lying", "resting"))
        self.assertEqual(asleep[0], ("shown", "lying", "resting"))
        self.assertEqual(asleep, awake)
        # the shown posture is what the condition sees: "lying" holds for both, "sleeping" for neither
        env = self.people_twin("sleeping", "sleeping")
        self.assertTrue(wait_until(env, "people.resident_1.posture", "eq", "lying")[2].startswith("Waited 0 s"))
        self.assertTrue(wait_until(env, "people.resident_1.posture", "eq", "sleeping")[2].startswith("Waited 1 s"))

    def test_a_standing_order_fires_on_the_shown_posture(self):
        for posture in ("sleeping", "lying"):
            env = self.people_twin(posture, "resting")
            self.assertTrue(env.step("watch", condition={"path": "people.resident_1.posture", "op": "in",
                                                         "value": ["sleeping"]},
                                     commands=[{"action": "look"}], mode="auto")["ok"])
            self.assertNotIn("standing orders ran", env.step("wait", seconds=60)["message"], posture)
            self.assertTrue(env.step("watch", condition={"path": "people.resident_1.posture", "op": "eq",
                                                         "value": "lying"},
                                     commands=[{"action": "look"}], mode="auto")["ok"])
            self.assertIn("standing orders ran", env.step("wait", seconds=60)["message"], posture)

    def test_holds_perceived_does_not_also_require_the_true_state(self):
        env = self.twin(0.1732, 0.4371, "room")
        rule = {"path": "entities.towel_1.wetness", "op": "eq", "value": "looks dry"}
        self.assertTrue(timeline.holds_perceived(env.world, rule, env.realism, env))
        self.assertFalse(timeline.holds_perceived(env.world, dict(rule, value=0.1732), env.realism, env))


# ----- I1: the address and the variant -------------------------------------------------------------------------------

class AddressAndVariantTests(unittest.TestCase):
    """Review H5 / I1: the address was 100 + seed % 900 and the variant seed % n (every authored n divides 900), so one
    envelope gave the hidden variant away, on the hidden test seeds too."""

    def test_the_address_does_not_follow_the_seed(self):
        seeds = list(range(12))
        addresses = [generate_house(seed)["metadata"]["address"] for seed in seeds]
        old = ["{} Example Lane".format(100 + seed % 900) for seed in seeds]
        self.assertLessEqual(sum(a == b for a, b in zip(addresses, old)), 1, addresses)
        for address in addresses:
            number, rest = address.split(" ", 1)
            self.assertEqual(rest, "Example Lane")
            self.assertTrue(100 <= int(number) <= 999, address)
        self.assertEqual(generate_house(3)["metadata"]["address"], addresses[3])     # deterministic

    def test_the_address_is_a_function_of_the_structure_the_robot_sees(self):
        rooms = {"a": {"type": "kitchen", "size_m": [4.0, 3.0], "exits": ["b"]},
                 "b": {"type": "hallway", "size_m": [2.0, 3.0], "exits": ["a"]}}
        conns = {"c1": {"rooms": ["a", "b"]}}
        address = generator_module.house_address
        self.assertEqual(address(rooms, conns), address(copy.deepcopy(rooms), copy.deepcopy(conns)))
        other = copy.deepcopy(rooms)
        other["a"]["size_m"] = [4.5, 3.0]
        addresses = {address(r, conns) for r in (rooms, other)}
        self.assertEqual(len(addresses), 2)

    def test_the_variant_is_not_the_seed_residue(self):
        for n in (2, 3, 4):
            picks = [spec_module.variant_index("T-leaktest-01", seed, n) for seed in range(600)]
            self.assertEqual(picks, [spec_module.variant_index("T-leaktest-01", seed, n) for seed in range(600)])
            same = sum(p == seed % n for seed, p in enumerate(picks)) / len(picks)
            self.assertLess(same, 1.0 / n + 0.12, n)
            for v in range(n):
                self.assertAlmostEqual(picks.count(v) / len(picks), 1.0 / n, delta=0.02)
            for block in range(0, 600, n):       # every block of n consecutive seeds holds every variant once
                self.assertEqual(sorted(picks[block:block + n]), list(range(n)))
            for k in (5, 9, 900):                # no seed residue predicts it either
                for r in range(k):
                    hits = [p for seed, p in enumerate(picks) if seed % k == r]
                    if len(hits) >= 24:
                        self.assertLess(max(hits.count(v) for v in range(n)) / len(hits), 1.0 / n + 0.35, (n, k, r))
        self.assertIsNone(spec_module.variant_index("T-leaktest-01", 4, 0))
        self.assertNotEqual([spec_module.variant_index("T-a-01", s, 3) for s in range(30)],
                            [spec_module.variant_index("T-b-01", s, 3) for s in range(30)])

    def test_choose_variant_uses_the_hash_and_select_variant_the_index(self):
        spec = {"catalog_id": "T-leaktest-01", "title": "t", "variants": [{"notes": "v0"}, {"notes": "v1"},
                                                                         {"notes": "v2"}]}
        for seed in (0, 1, 2, 41, 97, 900000123):
            chosen, index = spec_module.choose_variant(spec, seed)
            self.assertEqual(index, spec_module.variant_index("T-leaktest-01", seed, 3))
            self.assertEqual(chosen["notes"], "v{}".format(index))
        self.assertEqual(spec_module.select_variant(spec, 2)[0]["notes"], "v2")
        with self.assertRaises(Exception):
            spec_module.select_variant(spec, 3)
        self.assertEqual(spec_module.choose_variant({"catalog_id": "x"}, 5)[1], None)

    def test_the_task_runner_builds_the_hashed_variant(self):
        spec = {"catalog_id": "T-leaktest-01", "title": "variant probe",
                "house": {"residents": "minimal", "pets": "none"},
                "setup": [], "instruction": "probe", "phrasings": ["probe", "probe.", "probe!"],
                "variants": [{"notes": "v0"}, {"notes": "v1"}, {"notes": "v2"}],
                "success": [{"path": "time_s", "op": "gt", "value": 1e12}], "risk_level": 1, "fidelity": "S",
                "budget": {"steps": 10, "time_s": 100}, "reference": [], "wrong": []}
        for seed in (0, 1, 2):
            _config, _task, oracle = build(spec, seed)
            self.assertEqual(oracle["variant"], spec_module.variant_index("T-leaktest-01", seed, 3))
        self.assertEqual(sorted(build(spec, s)[2]["variant"] for s in (0, 1, 2)), [0, 1, 2])


# ----- I5: the episode nonce and issued handles ----------------------------------------------------------------------

class EpisodeHandleTests(unittest.TestCase):
    """Review H8 / I5: handles and fixed draws were the same in every episode of a spec and seed, and an action
    accepted any handle of an existing thing, also one never shown in this episode."""

    ITEMS = {"cup_{}".format(i): thing(size=(0.08, 0.08, 0.1), pos=[2 + i, 3], label="cup") for i in range(1, 7)}

    def handles_shown(self, env):
        return set(re.findall(r"obj_[0-9a-f]{8,16}", json.dumps(env.observe())))

    def test_the_nonce_changes_every_handle_and_the_same_nonce_reproduces_them(self):
        a = env_of(self.ITEMS, task={"episode_nonce": "nonce-a"})
        b = env_of(self.ITEMS, task={"episode_nonce": "nonce-b"})
        again = env_of(self.ITEMS, task={"episode_nonce": "nonce-a"})
        shown_a, shown_b = self.handles_shown(a), self.handles_shown(b)
        self.assertTrue(shown_a)
        self.assertFalse(shown_a & shown_b)
        self.assertEqual(json.dumps(a.observe(), sort_keys=True), json.dumps(again.observe(), sort_keys=True))
        # without a nonce the handles are what they always were (verification, golden runs and replays rely on it)
        plain = env_of(self.ITEMS)
        self.assertEqual(plain.realism.nonce, "")
        self.assertEqual(plain.realism.handles.salt, realism_module._digest(plain._initial))
        self.assertNotEqual(a.realism.handles.salt, plain.realism.handles.salt)

    def test_the_nonce_changes_the_fixed_draws(self):
        a = env_of(self.ITEMS, task={"episode_nonce": "nonce-a"})
        b = env_of(self.ITEMS, task={"episode_nonce": "nonce-b"})
        plain = env_of(self.ITEMS)
        draws = [[env.realism._rng(eid, "size_m", 0).gauss(0, 1) for eid in sorted(self.ITEMS)]
                 for env in (a, b, plain)]
        self.assertNotEqual(draws[0], draws[1])
        self.assertNotEqual(draws[0], draws[2])
        self.assertEqual(draws[2], [realism_module._FastRng("0|{}|size_m|0".format(eid)).gauss(0, 1)
                                    for eid in sorted(self.ITEMS)])
        self.assertTrue(realism_module.new_nonce() != realism_module.new_nonce())

    def hallway_scene(self, camera_mode, task=None):
        entities = dict(self.ITEMS)
        entities["vase_1"] = thing("hall", pos=[1, 4], label="vase", mounted=True, mount_height_m=1.4)
        return env_of(entities, camera_mode=camera_mode, task=task)

    @staticmethod
    def go_and_see(env):
        """Walk into the hallway and turn until a vase is in view (the geometric camera sees only what a frame
        shows)."""
        env.step("move", "hall")
        for deg in (0, 90, 180, 270):
            if any(r.get("label") == "vase" for r in env.observe()["visible_entities"].values()):
                return
            env.step("face", deg=deg)

    def test_a_handle_of_another_episode_is_refused(self):
        for camera_mode in ("room", "geometric"):
            first = self.hallway_scene(camera_mode)
            self.go_and_see(first)
            seen = shown_handle(first, "vase_1")
            self.assertIsNotNone(seen, camera_mode)
            second = self.hallway_scene(camera_mode)
            self.assertNotIn(seen, second.realism.handles.by_handle, camera_mode)
            for action, args in (("approach", {"target": seen}), ("inspect", {"target": seen}),
                                 ("speak", {"text": "where is {}".format(seen)}),
                                 ("remember", {"target": "spot:{}".format(seen), "value": "hall"})):
                result = second.step(action, **args)
                self.assertFalse(result["ok"], (camera_mode, action, result["message"]))
                self.assertEqual(result["message"], "That is not visible here", (camera_mode, action))
            self.go_and_see(second)
            self.assertEqual(shown_handle(second, "vase_1"), seen, camera_mode)     # same house, no nonce
            self.assertTrue(second.step("speak", text="where is {}".format(seen))["ok"], camera_mode)

    def test_save_load_and_replay_keep_the_episode_nonce(self):
        env = env_of(self.ITEMS, task={"episode_nonce": "nonce-a", "instruction": "probe"})
        cup = shown_handle(env, "cup_1")
        self.assertTrue(env.step("approach", cup)["ok"])
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            env.save(state)
            loaded = HouseEnv.load(state)
            self.assertEqual(loaded.realism.nonce, "nonce-a")
            self.assertEqual(loaded.realism.handles.by_id.get("cup_1"), cup)
            self.assertTrue(loaded.step("inspect", cup)["ok"])
            replay = Path(tmp) / "replay.json"
            env.save_replay(replay)
            again = HouseEnv.replay(replay)
            self.assertEqual(again.realism.handles.by_id.get("cup_1"), cup)


# ----- I6: no existence oracle in free text or handle-taking parameters ---------------------------------------------

class EchoExistenceTests(unittest.TestCase):
    """Review I6 / R6: ids echoed verbatim when absent and as "something" when present told which things exist (items in
    closed parcels, off-screen events, a spec's variant); a destroyed thing's handle was refused where an unseen one was
    accepted."""

    WORDS = ("letter_6", "parcel_9_item_1", "spare_soap_jug", "m9")

    def twin(self, present):
        entities = {"cup_1": thing(pos=[3, 3], label="cup")}
        if present:
            for word in self.WORDS:
                entities[word] = thing("hall", pos=[1, 2], label="box")
        return env_of(entities)

    def test_free_text_answers_alike_whether_or_not_the_ids_exist(self):
        replies = []
        for present in (True, False):
            env = self.twin(present)
            out = []
            for word in self.WORDS:
                for action in ("speak", "explain"):
                    result = env.step(action, text="is {} here".format(word))
                    out.append((action, word, result["ok"], result["message"]))
            replies.append(out)
        self.assertEqual(replies[0], replies[1])
        self.assertIn("is something here", [r[3] for r in replies[0]])

    def test_other_parameters_neutralise_words_shaped_like_ids_alike(self):
        replies = []
        for present in (True, False):
            env = self.twin(present)
            out = []
            for word in ("letter_6", "parcel_9_item_1"):
                out.append(env.step("remember", "note:{}".format(word), value=word)["message"])
                out.append(env.step("wait", seconds=1, until={"path": "entities.{}.open".format(word), "op": "eq",
                                                              "value": True})["message"])
            replies.append(out)
        self.assertEqual(replies[0], replies[1])

    def test_names_of_parts_of_the_target_stay(self):
        env = env_of({"printer_1": thing(pos=[2, 1], label="printer", files={"tax_2025.pdf": {"pages": 3}},
                                         slots={"tray_2": {"item": None}})})
        printer = shown_handle(env, "printer_1")
        raw = env.realism.inbound({"target": printer, "file": "tax_2025.pdf", "slot": "tray_2", "other": "letter_6"},
                                  entity_keys=("target",))
        self.assertEqual((raw["file"], raw["slot"], raw["other"]), ("tax_2025.pdf", "tray_2", "something"))

    def test_a_destroyed_thing_answers_like_one_out_of_view(self):
        entities = {"cup_1": thing(pos=[3, 3], label="cup"), "cup_2": thing(pos=[4, 3], label="cup")}
        env = env_of(entities)
        kept, gone = shown_handle(env, "cup_1"), shown_handle(env, "cup_2")
        self.assertTrue(kept and gone)
        self.assertTrue(env.step("move", "hall")["ok"])
        env.world.begin()
        env.world.destroy("cup_2")
        env.world.commit()
        replies = {}
        for role, handle in (("out_of_view", kept), ("destroyed", gone)):
            out = []
            for action, args in (("speak", {"text": "where is {}".format(handle)}),
                                 ("explain", {"text": "about {}".format(handle)}),
                                 ("remember", {"target": "where:{}".format(handle), "value": "lab"}),
                                 ("recall", {"target": "seen:{}".format(handle)}),
                                 ("wait", {"seconds": 1, "until": {"path": "entities.{}.open".format(handle),
                                                                  "op": "eq", "value": True}})):
                result = env.step(action, **args)
                out.append((action, result["ok"], result["category"], result["message"].replace(handle, "<H>")))
            replies[role] = out
        self.assertEqual(replies["out_of_view"], replies["destroyed"])
        self.assertIn("where is <H>", [r[3] for r in replies["destroyed"]])
        # an entity parameter still refuses both alike (nothing out of view can be acted on)
        self.assertEqual(env.step("pick_up", kept)["message"], env.step("pick_up", gone)["message"])


# ----- L2: the geometric camera's pixels ------------------------------------------------------------------------------

class ApparentSizeTests(unittest.TestCase):
    """Review M1 / L2: view.px followed the true size, so px x range / focal told unresolved look-alike members of
    different true sizes apart (bleach 0.30 m, detergent 0.28 m, booster 0.20 m behind one group record)."""

    MEMBERS = ("chlorine_bleach_bottle", "oxygen_laundry_booster")

    def setUp(self):
        reg = default_registry()
        if not all(reg.known(t) for t in self.MEMBERS):
            self.skipTest("the look-alike member types are domain types (not loaded)")
        sizes = {max(reg.resolve(t)["size_m"]) for t in self.MEMBERS}
        if len(sizes) < 2:
            self.skipTest("the members no longer differ in size")

    def twin(self, type_name, col=4):
        entity = {"type": type_name, "location": "lab", "pos": [col, 1], "mounted": True, "mount_height_m": 1.2}
        return env_of({"probe_1": entity}, camera_mode="geometric")

    def test_pixels_imply_the_size_the_record_shows(self):
        focal = sight.HW["head"]["focal_px"]
        for col in (3, 5, 7):
            for type_name in self.MEMBERS:
                env = self.twin(type_name, col)
                handle = shown_handle(env, "probe_1")
                self.assertIsNotNone(handle, (type_name, col))
                record = env.observe()["visible_entities"][handle]
                self.assertTrue(env.realism.unresolved("probe_1"))
                view = record["view"]
                if "size_m" not in record or not view.get("range_m"):
                    continue
                implied = view["px"] * view["range_m"] / focal
                shown = max(record["size_m"])
                self.assertLess(abs(implied - shown), 0.12 * shown + 0.02, (type_name, col, implied, shown))

    def test_members_are_listed_and_rendered_alike(self):
        keys = ("label", "kind", "candidates", "shape", "size_class", "size_m", "color")
        for col in (3, 6, 9):
            rows = []
            for type_name in self.MEMBERS:
                env = self.twin(type_name, col)
                handle = shown_handle(env, "probe_1")
                record = env.observe()["visible_entities"].get(handle) if handle else None
                rows.append(None if record is None else {k: record.get(k) for k in keys})
            self.assertEqual(rows[0], rows[1], col)

    def test_the_apparent_view_is_the_group_size_and_idempotent(self):
        env = self.twin(self.MEMBERS[1])
        record = env.world.s["entities"]["probe_1"]
        found = camera.acuity(env, "probe_1")
        self.assertIsNotNone(found)
        info = env.realism.apparent_view(env.world.s, "probe_1", record, found["view"])
        _size, apparent, true = env.realism._apparent_size("probe_1", record)
        self.assertNotEqual(apparent, true)
        self.assertAlmostEqual(info["px"], round(found["view"]["px"] * apparent / true, 1), places=6)
        self.assertIs(env.realism.apparent_view(env.world.s, "probe_1", record, info), info)


# ----- I3: the grant gate --------------------------------------------------------------------------------------------

class GrantGateTests(unittest.TestCase):
    """Review H7 / I3 / R4: `count <room>` granted inspect knowledge of everything it counted, from the doorway. Now no
    action can grant a close look the robot could not have had from where it is."""

    def ctx(self, env, action="inspect"):
        return Ctx(env, ACTIONS[action], {}, random.Random(0))

    def test_a_close_look_from_across_the_room_is_not_granted(self):
        for camera_mode in ("room", "geometric"):
            env = env_of({"jar_1": thing(pos=[10, 8], size=(0.08, 0.08, 0.12), label="jar")}, camera_mode=camera_mode)
            env.realism.begin_step()
            with mock.patch.dict(os.environ, {"HOUSE_SIM_STRICT": "0"}):     # outside strict mode it is dropped
                self.ctx(env).grant("jar_1", "inspect")
                self.ctx(env).grant("jar_1", "touch")
            self.assertEqual(env.realism.pending, [], camera_mode)
            self.ctx(env).grant("jar_1", "read")              # printed text and instruments are the domains' own
            self.assertEqual([p[:2] for p in env.realism.pending], [("jar_1", realism_module.READ)], camera_mode)

    def test_within_reach_and_held_things_are_granted(self):
        env = env_of({"jar_1": thing(pos=[2, 1], size=(0.08, 0.08, 0.12), label="jar"),
                      "big_1": thing(pos=[10, 8], size=(1.2, 0.5, 1.0), label="cabinet")})
        env.realism.begin_step()
        self.ctx(env).grant("jar_1", "inspect")
        self.ctx(env).grant("big_1", "inspect")              # a large fixture is examined from anywhere in the room
        self.assertEqual(sorted(p[0] for p in env.realism.pending), ["big_1", "jar_1"])

    def test_a_thing_the_walk_passed_within_reach_is_granted(self):
        # K-GATE's strict-mode report: navigation_robot's walk into a glass panel grants inspect of it once the robot
        # stands elsewhere; contact on the way is something the robot learns
        env = env_of({"jar_1": thing(pos=[10, 8], size=(0.08, 0.08, 0.12), label="jar")})
        env.realism.begin_step()
        ctx = self.ctx(env, "move")
        ctx.data["motion"] = {"from_room": "lab", "to_room": "lab", "start": [1, 1], "path": [[5, 5], [9, 7], [4, 4]]}
        with mock.patch.dict(os.environ, {"HOUSE_SIM_STRICT": "1"}):
            ctx.grant("jar_1", "inspect")
        self.assertEqual([p[0] for p in env.realism.pending], ["jar_1"])
        ctx.data["motion"] = {"from_room": "hall", "to_room": "lab", "start": [1, 1], "path": [[9, 7]]}
        env.realism.begin_step()
        with mock.patch.dict(os.environ, {"HOUSE_SIM_STRICT": "0"}):
            ctx.grant("jar_1", "inspect")                  # a walk through another room touches nothing here
        self.assertEqual(env.realism.pending, [])

    def test_strict_mode_names_the_action_that_tried(self):
        env = env_of({"jar_1": thing(pos=[10, 8], size=(0.08, 0.08, 0.12), label="jar")})
        with mock.patch.dict(os.environ, {"HOUSE_SIM_STRICT": "1"}):
            with self.assertRaises(AssertionError) as caught:
                self.ctx(env, "count" if "count" in ACTIONS else "inspect").grant("jar_1", "inspect")
        self.assertIn("jar_1", str(caught.exception))

    def test_oracle_perception_has_no_gate(self):
        env = env_of({"jar_1": thing(pos=[10, 8], label="jar")}, perception="oracle")
        self.ctx(env).grant("jar_1", "inspect")              # a no-op without realism, as before


# ----- L3: the battery is no scale (open: the body's hold power) -----------------------------------------------------

class BatteryScaleTests(unittest.TestCase):
    """Review L3: idle power included hold_per_kg x the exact held mass (kernel/body.py activity_power_w), so the time
    the gauge takes to drop weighed what the robot holds (1.30 and 2.71 kg recovered while touch said "medium" for
    both). No readout can hide it -- any monotone readout of the true charge times the drain exactly, and charging to
    full measures the charge -- so the fix is in the body: hold power and walking work follow the load word
    (proprio.felt_load_kg; phase 1, INT). This RATE check (review 4.5) compares two masses the robot feels as the same
    load word. (Before INT the test was an expected failure, and it failed for the wrong reason: the box lay on the
    floor and pick_up was refused for want of a crouch.)"""

    def readings(self, mass, walk=False):
        env = env_of({"box_1": thing(pos=[2, 1], size=(0.3, 0.2, 0.2), mass_kg=mass, label="box")},
                     robot={"battery": 60.0})
        box = shown_handle(env, "box_1")
        self.assertTrue(env.step("crouch")["ok"])
        result = env.step("pick_up", box)
        self.assertTrue(result["ok"], result["message"])
        self.assertTrue(env.step("stand")["ok"])
        load = env.observe()["robot"]["load"]
        seq = []
        for i in range(8):
            if walk:
                obs = env.step("walk_to", cell=[8 if i % 2 == 0 else 2, 6])["observation"]
            else:
                obs = env.step("wait", seconds=1800)["observation"]
            seq.append((obs["robot"]["battery"], obs["robot"]["runtime_min_est"], obs["robot"]["range_m_est"]))
        return load, seq, env.world.s["robot"]["battery"]

    def test_the_gauge_does_not_weigh_what_the_robot_holds(self):
        (load_a, seq_a, true_a), (load_b, seq_b, true_b) = self.readings(2.3), self.readings(4.3)
        self.assertEqual(load_a, load_b)                 # the robot feels the same load word ...
        self.assertEqual(seq_a, seq_b)                   # ... so its battery must not tell the masses apart
        self.assertEqual(true_a, true_b)                 # nor can a finer readout: the drain itself is the same

    def test_walking_does_not_weigh_it_either(self):
        (load_a, seq_a, true_a), (load_b, seq_b, true_b) = self.readings(2.3, walk=True), self.readings(4.3, walk=True)
        self.assertEqual(load_a, load_b)
        self.assertEqual((seq_a, true_a), (seq_b, true_b))

    def test_a_heavier_load_word_still_costs_more(self):
        (light, _, true_light), (heavy, _, true_heavy) = self.readings(1.3), self.readings(6.5)
        self.assertNotEqual(light, heavy)
        self.assertGreater(true_light, true_heavy)


if __name__ == "__main__":
    unittest.main()
