"""Realistic (imperfect) perception: handles, tiers, knowledge, noise, look-alikes, determinism, no leaks."""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
import warnings
from pathlib import Path
from unittest import mock

from house_sim.generator import generate_house
from house_sim.kernel import realism as realism_module
from house_sim.kernel import registry as registry_module
from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.plugins import HOOKS, enabled_domains
from house_sim.kernel.predicates import digest
from house_sim.world import make_house, object_record

ROOT = Path(__file__).resolve().parents[1]
TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


def fixture():
    c = make_house(3, 1, 3)
    c["goal"] = None
    c["entities"]["counter_1"] = object_record("counter", "room_1", surface=True, accepts_items=True, size_m=[1.2, 0.6, 0.9],
                                             blocks_path=True, pos=[2, 2], soil={"grease": 0.45, "crumbs": 0.1})
    c["entities"]["salt_jar_1"] = object_record("salt jar", "counter_1", portable=True, size_m=[0.08, 0.08, 0.12], category="jar",
                                              looks_like="white_powder_jar", label_text="SALT", relation="on")
    c["entities"]["sugar_jar_1"] = object_record("sugar jar", "counter_1", portable=True, size_m=[0.08, 0.08, 0.12], category="jar",
                                               looks_like="white_powder_jar", label_text="SUGAR", relation="on")
    c["entities"]["pan_1"] = object_record("frying pan", "counter_1", portable=True, size_m=[0.3, 0.3, 0.06], temperature_c=45.0,
                                         relation="on", mass_kg=1.4, owner="resident_1")
    c["entities"]["note_1"] = object_record("note", "counter_1", portable=True, size_m=[0.1, 0.1, 0.01], text="Dentist at 3pm",
                                          relation="on")
    for eid in ("salt_jar_1", "sugar_jar_1", "pan_1", "note_1"):
        c["entities"][eid].pop("pos", None)
    return c


def leaks(obj, entity_ids):
    entity_ids = {e for e in entity_ids if "_" in e}   # legacy fixture ids like "sink" coincide with labels
    found = set()

    def walk(v):
        if isinstance(v, str):
            for t in TOKEN.findall(v):
                if t in entity_ids:
                    found.add(t)
        elif isinstance(v, dict):
            for k, x in v.items():
                walk(k)
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(obj)
    return found


class RealisticPerceptionTests(unittest.TestCase):
    def env(self, seed=0, config=None):
        return HouseEnv(config or fixture(), seed=seed, perception="realistic")

    def handle(self, env, eid):
        return env.realism.handles.handle(eid)

    def test_oracle_mode_is_unchanged(self):
        oracle = HouseEnv(fixture(), seed=0)
        self.assertIsNone(oracle.realism)
        obs = oracle.observe()
        self.assertIn("counter_1", obs["visible_entities"])
        self.assertEqual(obs["visible_entities"]["counter_1"]["soil"], {"grease": 0.45, "crumbs": 0.1})

    def test_no_true_ids_and_handles_are_stable(self):
        env = self.env()
        obs = env.observe()
        ids = set(env.world.s["entities"])
        self.assertEqual(leaks(obs, ids), set())
        self.assertTrue(all(k.startswith("obj_") for k in obs["visible_entities"]))
        again = self.env().observe()
        self.assertEqual(sorted(obs["visible_entities"]), sorted(again["visible_entities"]))
        other = self.env(seed=5).observe()
        self.assertNotEqual(sorted(obs["visible_entities"]), sorted(other["visible_entities"]))

    def test_true_ids_are_refused_like_invisible_things(self):
        env = self.env()
        bad = env.step("approach", "counter_1")
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["message"], "That is not visible here")
        guessed = env.step("approach", "obj_deadbeef")
        self.assertEqual(guessed["message"], "That is not visible here")
        good = env.step("approach", self.handle(env, "counter_1"))
        self.assertTrue(good["ok"], good["message"])

    def test_messages_use_handles(self):
        env = self.env()
        env.step("approach", self.handle(env, "counter_1"))
        result = env.step("pick_up", self.handle(env, "pan_1"))
        self.assertTrue(result["ok"], result["message"])
        self.assertIn(self.handle(env, "pan_1"), result["message"])
        self.assertNotIn("pan_1", TOKEN.findall(result["message"]))

    def test_observe_never_mutates_the_world(self):
        env = self.env()
        before = digest(env.world.s)
        for _ in range(3):
            env.observe()
        self.assertEqual(digest(env.world.s), before)

    def test_glance_hides_inspect_tier_truth_and_inspect_reveals_buckets(self):
        env = self.env()
        h = self.handle(env, "counter_1")
        glance = env.observe()["visible_entities"][h]
        self.assertEqual(set(glance["soil"]), {"overall"})
        self.assertNotIn("material", glance)
        env.step("approach", h)
        res = env.step("inspect", h)
        details = json.loads(res["message"])
        self.assertIn("grease", details["soil"])
        self.assertIsInstance(details["soil"]["grease"], str)          # a bucket, not 0.45
        later = env.observe()["visible_entities"][h]
        self.assertIn("grease", later["soil"])
        self.assertIn("material", later)

    def test_read_tier_needs_read(self):
        env = self.env()
        env.step("approach", self.handle(env, "counter_1"))
        h = self.handle(env, "note_1")
        self.assertNotIn("text", env.observe()["visible_entities"].get(h, {}))
        self.assertTrue(env.step("read", h)["ok"])
        self.assertEqual(env.observe()["visible_entities"][h]["text"], "Dentist at 3pm")

    def test_measure_tier_is_coarse_until_measured(self):
        env = self.env()
        env.step("approach", self.handle(env, "counter_1"))
        h = self.handle(env, "pan_1")
        temp = env.observe()["visible_entities"][h].get("temperature_c")
        self.assertIsInstance(temp, str)                               # thermal glance: a bucket
        self.assertNotIn("mass_kg", env.observe()["visible_entities"][h])
        env.step("pick_up", h)
        held = env.observe()["visible_entities"][h]
        self.assertIn(held["mass_kg"], ("light", "medium", "heavy", "very heavy"))
        # an explicit measurement grant gives a number within tool precision
        env.realism.knowledge.grant("pan_1", env.world.s["entities"]["pan_1"], 4, ["temperature_c", "mass_kg"])
        measured = env.observe()["visible_entities"][h]
        self.assertAlmostEqual(measured["temperature_c"], env.world.s["entities"]["pan_1"]["temperature_c"], delta=2.0)
        self.assertAlmostEqual(measured["mass_kg"], 1.4, places=2)

    def test_never_tier_fields_are_hidden(self):
        env = self.env()
        env.step("approach", self.handle(env, "counter_1"))
        h = self.handle(env, "pan_1")
        env.step("inspect", h)
        rec = env.observe()["visible_entities"][h]
        for field in ("owner", "home", "value", "type", "is_a", "sentimental"):
            self.assertNotIn(field, rec)

    def test_look_alikes_confused_at_glance_resolved_by_read(self):
        env = self.env()
        env.step("approach", self.handle(env, "counter_1"))
        salt, sugar = self.handle(env, "salt_jar_1"), self.handle(env, "sugar_jar_1")
        obs = env.observe()["visible_entities"]
        self.assertEqual(obs[salt]["label"], obs[sugar]["label"])
        self.assertIn("candidates", obs[salt])
        env.step("read", salt)
        obs = env.observe()["visible_entities"]
        self.assertEqual(obs[salt]["label"], "salt jar")
        self.assertNotEqual(obs[sugar]["label"], "sugar jar")

    def test_knowledge_survives_own_moves_and_lapses_after_unseen_moves(self):
        env = self.env()
        env.step("approach", self.handle(env, "counter_1"))
        h = self.handle(env, "salt_jar_1")
        env.step("read", h)
        self.assertEqual(env.observe()["visible_entities"][h]["label"], "salt jar")
        env.step("pick_up", h)
        env.step("place", h, "room_1")
        self.assertEqual(env.observe()["visible_entities"][h]["label"], "salt jar")   # it moved with the robot
        # someone else moves it while unobserved: it may have been swapped
        env.world.begin()
        env.world.mut("salt_jar_1")["location"] = "counter_1"
        env.world.mut("salt_jar_1").pop("pos", None)
        env.world.commit()
        self.assertNotEqual(env.observe()["visible_entities"][h]["label"], "salt jar")

    def test_read_does_not_unlock_touch_or_measure_values(self):
        env = self.env()
        env.step("approach", self.handle(env, "counter_1"))
        h = self.handle(env, "pan_1")
        env.step("inspect", h)
        rec = env.observe()["visible_entities"][h]
        self.assertNotIn("mass_kg", rec)               # inspecting does not weigh it
        self.assertIsInstance(rec.get("temperature_c"), str)

    def test_repeated_glances_are_identical(self):
        env = self.env()
        a = env.observe()
        b = env.observe()
        self.assertEqual(a["visible_entities"], b["visible_entities"])

    def test_people_moods_are_cues(self):
        c = fixture()
        c["people"]["sam"] = {"label": "Sam", "role": "adult", "location": "room_1", "pos": [0, 1], "mood": "distressed",
                              "awake": True, "authority": 2, "consent": True}
        env = self.env(config=c)
        person = env.observe()["people"]["sam"]
        self.assertNotIn("mood", person)
        self.assertIn("crying", person.get("cues", []))

    def test_save_load_and_replay_keep_knowledge(self):
        env = self.env()
        env.step("approach", self.handle(env, "counter_1"))
        env.step("read", self.handle(env, "note_1"))
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s.json")
            env.save(path)
            loaded = HouseEnv.load(path)
            self.assertEqual(loaded.perception_mode, "realistic")
            self.assertEqual(loaded.observe()["visible_entities"], env.observe()["visible_entities"])
            rpath = os.path.join(tmp, "r.json")
            env.save_replay(rpath)
            replayed = HouseEnv.replay(rpath)
            self.assertEqual(replayed.observe()["visible_entities"], env.observe()["visible_entities"])

    def test_generated_houses_leak_no_ids(self):
        for seed in (0, 3):
            env = HouseEnv(generate_house(seed), seed=seed, perception="realistic")
            obs = env.observe()
            self.assertEqual(leaks(obs, set(env.world.s["entities"])), set(), seed)
            self.assertEqual(leaks(env.known_map(), set(env.world.s["entities"])), set())

    def test_deterministic_across_processes(self):
        code = ("import json,sys; sys.path.insert(0, {root!r}); from house_sim.generator import generate_house; "
                "from house_sim.kernel.engine import HouseEnv; from house_sim.kernel.predicates import digest; "
                "e=HouseEnv(generate_house(2), seed=2, perception='realistic'); print(digest(e.observe()))").format(root=str(ROOT))
        outs = set()
        for hashseed in ("1", "987"):
            env = dict(os.environ, PYTHONHASHSEED=hashseed)
            proc = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=600)
            self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
            outs.add(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(len(outs), 1)

    def test_realistic_observe_overhead_is_small(self):
        env = HouseEnv(generate_house(7), seed=7, perception="realistic", trace=False)
        oracle = HouseEnv(generate_house(7), seed=7, trace=False)
        def timed(e):
            t = time.perf_counter()
            for _ in range(15):
                e.observe()
            return (time.perf_counter() - t) / 15
        timed(env), timed(oracle)
        # best of 5 interleaved rounds: other processes loading the machine only ever add time
        extra = min(timed(env) for _ in range(5)) - min(timed(oracle) for _ in range(5))
        self.assertLess(extra, 0.010)       # target 3 ms; generous bound for slow CI machines


class RedTeamRegressionTests(unittest.TestCase):
    """Leaks found by the independent red team (each must stay closed)."""

    def env(self, config=None, seed=0):
        return HouseEnv(config or fixture(), seed=seed, perception="realistic")

    def h(self, env, eid):
        return env.realism.handles.handle(eid)

    def test_true_ids_in_any_letter_case_are_refused(self):
        env = self.env()
        for guess in ("counter_1", "Counter_1", "COUNTER_1"):
            res = env.step("approach", guess)
            self.assertFalse(res["ok"])
            self.assertEqual(res["message"], "That is not visible here")

    def test_free_text_never_maps_ids_to_handles(self):
        env = self.env()
        res = env.step("speak", text="sugar_jar_1 salt_jar_1 made_up_9")
        self.assertNotIn(self.h(env, "sugar_jar_1"), res["message"])
        self.assertNotIn(self.h(env, "salt_jar_1"), res["message"])
        self.assertNotIn("sugar_jar_1", res["message"])

    def test_step_results_carry_no_evaluator_signal(self):
        env = self.env()
        res = env.step("look")
        for key in ("evaluation", "reward", "terminated", "truncated", "raw_message"):
            self.assertNotIn(key, res)

    def test_affordances_do_not_probe_or_leak_knowledge(self):
        env = self.env()
        env.step("approach", self.h(env, "counter_1"))
        listing = env.affordances(self.h(env, "note_1"))
        self.assertTrue(all(set(item) == {"action", "params"} for item in listing))
        env.step("wait", seconds=1)
        self.assertNotIn("text", env.observe()["visible_entities"][self.h(env, "note_1")])

    def test_watch_only_observable_things(self):
        c = fixture()
        c["people"]["sam_1"] = {"label": "Sam", "role": "adult", "location": "room_1", "pos": [0, 1], "mood": "sad",
                                "awake": True, "authority": 2, "consent": True}
        env = self.env(c)
        cmds = [{"action": "look"}]
        for cond in ({"path": "people.sam_1.mood", "op": "eq", "value": "sad"},
                     {"path": "people.sam_1.awake", "op": "eq", "value": True},
                     {"path": "entities.{}.temperature_c".format(self.h(env, "pan_1")), "op": "gt", "value": 40},
                     {"path": "entities.{}.open".format(self.h(env, "counter_1")), "op": "eq", "ref": "entities.{}.private".format(self.h(env, "pan_1"))}):
            self.assertFalse(env.step("watch", condition=cond, commands=cmds)["ok"], cond)

    def test_sleep_is_not_visible_at_a_glance(self):
        c = fixture()
        c["people"]["sam_1"] = {"label": "Sam", "role": "adult", "location": "room_1", "pos": [0, 1], "posture": "sleeping",
                                "awake": False, "authority": 2, "consent": True, "activity": "sleeping"}
        c["people"]["kim_1"] = {"label": "Kim", "role": "adult", "location": "room_1", "pos": [1, 1], "posture": "lying",
                                "awake": True, "authority": 2, "consent": True, "activity": "resting"}
        obs = self.env(c).observe()
        self.assertEqual(obs["people"]["sam_1"]["posture"], obs["people"]["kim_1"]["posture"])
        self.assertFalse(any(h.get("kind") == "sleeping" for h in obs["grid"]["hazards"]))

    def test_refusals_do_not_name_hidden_hazards_or_flags(self):
        c = fixture()
        c["entities"]["salt_jar_1"].update(hazards=["corrosive"], safe_grips=["tongs"])
        c["entities"]["pan_1"].update(private=True, temperature_c=21.0)
        env = self.env(c)
        env.step("approach", self.h(env, "counter_1"))
        env.step("crouch")
        haz = env.step("pick_up", self.h(env, "salt_jar_1"))
        # an instance hazard the robot cannot see does not refuse at all (kernel round 5 red-team fix: the exposure
        # is recorded); a refusal, when there is one, names no hazard
        if haz["ok"]:
            self.assertTrue(env.world.s["stats"].get("safety_violations"))
        self.assertNotIn("corrosive", haz["message"])
        priv = env.step("pick_up", self.h(env, "pan_1"))
        # a never-tier privacy flag may not refuse at all in realistic mode (kernel round 5: the act proceeds and the
        # violation is recorded); when it does refuse, the refusal is generic
        if not priv["ok"]:
            self.assertNotIn("Owner", priv["message"])
            self.assertEqual(priv["category"], "permission")
        text, category = env.realism.scrub_refusal("Owner permission is required", "privacy")
        self.assertNotIn("Owner", text)
        self.assertEqual(category, "permission")

    def test_estimates_do_not_average_out_across_viewpoints(self):
        env = self.env()
        h = self.h(env, "counter_1")
        first = env.observe()["visible_entities"][h]["size_m"]
        env.step("approach", h)
        self.assertEqual(env.observe()["visible_entities"][h]["size_m"], first)

    def test_private_things_look_like_anything_else(self):
        keys = []
        for private in (True, False):
            c = fixture()
            c["entities"]["pan_1"]["private"] = private
            env = self.env(c)
            env.step("approach", self.h(env, "counter_1"))
            keys.append(set(env.observe()["visible_entities"][self.h(env, "pan_1")]))
        self.assertEqual(keys[0], keys[1])


# test-only types: a read-resolved look-alike group whose members differ in color/size, a group sharing the word "food",
# and a gadget with dict fields declaring dotted sub-field tiers
R5_TYPES = {
    "domain": "r5_realism_test",
    "looks_like": {
        "r5_gel": {"descriptor": "squeeze bottle of gel", "resolve": "read", "kind": "gel bottle",
                   "members": ["r5_squeeze_bottle", "r5_hand_gel", "r5_glue_gel", "r5_refill_bottle"],
                   "portion_descriptor": "blob of clear gel"},
        "r5_feed": {"descriptor": "small tub of food", "resolve": "read", "members": ["r5_fish_food", "r5_bird_food"]},
    },
    "types": {
        "r5_squeeze_bottle": {"is_a": "portable_object", "props": {
            "label": "squeeze bottle", "category": "r5_dispenser", "size_m": [0.06, 0.06, 0.18], "color": "white",
            "label_text": "", "mass_kg": 0.05}},
        "r5_hand_gel": {"is_a": "r5_squeeze_bottle", "props": {"label": "hand gel", "label_text": "HAND GEL", "mass_kg": 0.3}},
        "r5_glue_gel": {"is_a": "r5_squeeze_bottle", "props": {"label": "glue gel", "label_text": "GLUE", "color": "blue",
                                                               "size_m": [0.07, 0.07, 0.2]}},
        "r5_refill_bottle": {"is_a": "portable_object", "props": {
            "label": "refill bottle", "category": "r5_dispenser", "size_m": [0.06, 0.06, 0.18], "color": "white",
            "product": "hand_gel"}, "perception": {"read": ["product"]}},
        "r5_fish_food": {"is_a": "portable_object", "props": {"label": "fish food", "category": "pet_food",
                                                              "label_text": "FISH FLAKES", "size_m": [0.05, 0.05, 0.1]}},
        "r5_bird_food": {"is_a": "portable_object", "props": {"label": "bird food", "category": "pet_food",
                                                              "label_text": "BIRD SEED", "size_m": [0.05, 0.05, 0.1]}},
        "r5_gadget": {"is_a": "portable_object", "props": {
            "label": "gadget", "category": "r5_gizmo", "size_m": [0.2, 0.2, 0.2], "glossiness": 0.5, "liquid_temp_c": 20.0,
            "bladder": {"kpa": 30.0, "max_kpa": 80.0},
            "switches": {"a": {"holds_n": 4.2, "position_error_mm": 0.31, "name": "A"},
                         "b": {"holds_n": 3.9, "position_error_mm": 0.12, "name": "B"}},
            "deep": {"outer": {"inner": {"secret_n": 7, "shown": 1}}},
            "sprouts": {"healthy": 31, "wilted": 2},
            "bin_load": {"kg": 3.25, "kind": "seed"}},
            "perception": {"measure": {"bladder": "pump_gauge", "liquid_temp_c": "thermometer", "bin_load.kg": "scale"},
                           "hidden": ["bladder.max_kpa", "switches.*.holds_n", "deep.outer.inner.secret_n"],
                           "inspect": ["switches.*.position_error_mm"],
                           "glance": ["sprouts", "bin_load", "switches", "deep"],
                           "count": ["sprouts.healthy"]}},
    },
}


class KernelRound5RealismTests(unittest.TestCase):
    """Kernel round 5 (K-REALISM R1-R18): each test failed before its change."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = os.path.join(cls.tmp.name, "r5_realism_test.json")
        with open(cls.path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(R5_TYPES, f)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cls.reg = registry_module.TypeRegistry(extra_files=[cls.path])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def env(self, extra=None, config=None, seed=0):
        c = config or fixture()
        for eid, rec in (extra or {}).items():
            c["entities"][eid] = rec
        return HouseEnv(c, seed=seed, perception="realistic", registry=self.reg)

    @staticmethod
    def on_counter(type_name, **props):
        return dict({"type": type_name, "location": "counter_1", "relation": "on"}, **props)

    @staticmethod
    def h(env, eid):
        return env.realism.handles.handle(eid)

    def seen(self, env, eid):
        return env.observe()["visible_entities"].get(self.h(env, eid), {})

    # R1 ------------------------------------------------------------------------------------------------------
    def test_r1_unresolved_inspect_keeps_identity_values_and_members_read_alike(self):
        messages = []
        for type_name in ("r5_hand_gel", "r5_glue_gel", "r5_squeeze_bottle"):
            env = self.env({"gel_1": self.on_counter(type_name)})
            env.step("approach", self.h(env, "counter_1"))
            res = env.step("inspect", self.h(env, "gel_1"))
            self.assertTrue(res["ok"], res["message"])
            data = json.loads(res["message"])
            self.assertEqual(data["label"], "squeeze bottle of gel", type_name)       # not "... of gel of gel"
            self.assertEqual(data["candidates"], ["r5 glue gel", "r5 hand gel", "r5 refill bottle", "r5 squeeze bottle"], type_name)
            messages.append(re.sub(r"obj_[0-9a-f]+", "obj", res["message"]))
        self.assertEqual(len(set(messages)), 1, messages)       # byte-identical for every member

    def test_r1_label_substitution_is_single_pass(self):
        env = self.env({"gel_1": self.on_counter("r5_squeeze_bottle")})
        out = env.realism.outbound_message("The squeeze bottle is here; a squeeze bottle of gel was named", involved={"gel_1"})
        self.assertEqual(out, "The squeeze bottle of gel is here; a squeeze bottle of gel was named")
        out = json.loads(env.realism.outbound_message(json.dumps({"label": "squeeze bottle of gel", "note": "a squeeze bottle"}),
                                                      involved={"gel_1"}))
        self.assertEqual(out, {"label": "squeeze bottle of gel", "note": "a squeeze bottle of gel"})

    # R2 ------------------------------------------------------------------------------------------------------
    def test_r2_ids_of_things_destroyed_during_the_step_do_not_leak(self):
        env = self.env()
        r, w = env.realism, env.world
        issued = self.h(env, "note_1")
        w.begin()
        w.create("vault_box_7", object_record("box", "room_2", pos=[0, 0]))
        w.commit()
        r.begin_step()
        w.begin()
        w.destroy("note_1")
        w.destroy("vault_box_7")
        r.commit_grants(True)
        w.commit()
        self.assertEqual(r.outbound_message("removed note_1 and vault_box_7"), "removed {} and something".format(issued))
        data = json.loads(r.outbound_message(json.dumps({"removed": ["note_1", "vault_box_7"]})))
        self.assertEqual(leaks(data, {"note_1", "vault_box_7"}), set())
        res = env.step("speak", text="vault_box_7")
        self.assertNotIn("vault_box_7", res["message"])

    # R3 ------------------------------------------------------------------------------------------------------
    def test_r3_json_messages_are_ordered_by_handle_not_true_id(self):
        env = self.env()
        r = env.realism
        ids = sorted(e for e in env.world.s["entities"] if "_" in e)
        a, b = next((a, b) for a in ids for b in ids if a < b and r.handles.handle(a) > r.handles.handle(b))
        ha, hb = r.handles.handle(a), r.handles.handle(b)
        raw = json.dumps({"counts": {a: 1, b: 2}, "items": [a, b], "path": [a, b],
                          "found": [{"id": a, "n": 1}, {"id": b, "n": 2}]}, sort_keys=True)
        data = json.loads(r.outbound_message(raw, involved={a, b}))
        self.assertEqual(list(data["counts"]), [hb, ha])
        self.assertEqual(data["items"], [hb, ha])
        self.assertEqual([x["id"] for x in data["found"]], [hb, ha])
        self.assertEqual(data["path"], [ha, hb])              # a list under an order-naming key keeps its order
        self.assertEqual(list(r.out_obj({"seen": {a: 1, b: 2}})["seen"]), [hb, ha])     # observer channels too

    # R4 ------------------------------------------------------------------------------------------------------
    def test_r4_heft_and_scale_include_the_liquid_inside(self):
        env = self.env({"bottle_9": object_record("bottle", "counter_1", relation="on", portable=True, mass_kg=0.08,
                                                  liquid_l=0.5, size_m=[0.07, 0.07, 0.25], capacity_l=0.6)})
        rec = env.world.s["entities"]["bottle_9"]
        env.step("approach", self.h(env, "counter_1"))
        env.realism.knowledge.grant("bottle_9", rec, realism_module.TOUCH, ["mass_kg"])
        self.assertEqual(self.seen(env, "bottle_9")["mass_kg"], "medium")      # 0.58 kg, not 0.08 kg ("light")
        env.realism.knowledge.grant("bottle_9", rec, realism_module.MEASURE, ["mass_kg"])
        self.assertAlmostEqual(self.seen(env, "bottle_9")["mass_kg"], 0.58, places=2)

    # R5 ------------------------------------------------------------------------------------------------------
    def test_r5_perceivable_value_hook_decides_what_is_rendered(self):
        def visible_soil(state, eid, record, field):
            return {"crumbs": 0.1} if eid == "counter_1" else None      # grease hidden under the bread bin
        visible_soil.fields = ("soil",)
        entry = (100, "tests.visible_soil", visible_soil)
        HOOKS["perceivable_value"].append(entry)
        try:
            env = self.env()
            self.assertEqual(self.seen(env, "counter_1")["soil"], {"overall": "light"})     # 0.1, not 0.45
            env.step("approach", self.h(env, "counter_1"))
            details = json.loads(env.step("inspect", self.h(env, "counter_1"))["message"])
            self.assertEqual(set(details["soil"]), {"crumbs"})
        finally:
            HOOKS["perceivable_value"].remove(entry)

    # R6 ------------------------------------------------------------------------------------------------------
    def test_r6_dict_fields_render_buckets_and_dotted_rules(self):
        gadget = self.on_counter("r5_gadget", odor={"mildew": 0.44, "smoke": 0.01}, damage={"scratched": 0.33, "dented": 0.7},
                                 pellets={"kind": "kibble", "grams": 40.0}, _perception={"hidden": ["pellets.kind"]})
        env = self.env({"gadget_1": gadget})
        env.step("approach", self.h(env, "counter_1"))
        glance = self.seen(env, "gadget_1")
        self.assertEqual(glance["sprouts"], {"healthy": "many", "wilted": 2})          # a count kind: a bucket
        self.assertEqual(glance["bin_load"], {"kind": "seed"})                         # measure sub-field: not yet
        self.assertEqual(glance["switches"], {"a": {"name": "A"}, "b": {"name": "B"}})   # wildcard hidden + inspect
        self.assertEqual(glance["deep"], {"outer": {"inner": {"shown": 1}}})            # nested hidden path
        self.assertTrue(env.step("inspect", self.h(env, "gadget_1"))["ok"])
        seen = self.seen(env, "gadget_1")
        self.assertEqual(seen["odor"], {"mildew": "noticeable"})
        self.assertEqual(seen["damage"], {"dented": "severe", "scratched": "moderate"})
        self.assertEqual(seen["pellets"], {"grams": 40.0})                               # per-entity dotted hidden
        self.assertEqual(set(seen["switches"]["a"]), {"name", "position_error_mm"})
        self.assertIsInstance(seen["sprouts"]["healthy"], int)
        rec = env.world.s["entities"]["gadget_1"]
        env.realism.knowledge.grant("gadget_1", rec, realism_module.MEASURE, ["bladder", "bin_load.kg"])
        seen = self.seen(env, "gadget_1")
        self.assertEqual(seen["bladder"], {"kpa": 30.0})                                 # measured: max_kpa still hidden
        self.assertEqual(seen["bin_load"], {"kg": 3.25, "kind": "seed"})

    # R7 ------------------------------------------------------------------------------------------------------
    def test_r7_noisy_fractions_stay_within_zero_and_one(self):
        extra = {"unit_{}".format(i): self.on_counter("r5_gadget", remaining=0.995, shine=0.99, glossiness=0.995)
                 for i in range(8)}
        env = self.env(extra)
        env.step("approach", self.h(env, "counter_1"))
        for eid in extra:
            env.realism.knowledge.grant(eid, env.world.s["entities"][eid], realism_module.INSPECT)
        obs = env.observe()["visible_entities"]
        values = [obs[self.h(env, eid)][f] for eid in extra for f in ("remaining", "shine", "glossiness")]
        self.assertTrue(all(0.0 <= v <= 1.0 for v in values), values)

    # R8 ------------------------------------------------------------------------------------------------------
    def test_r8_every_thermometer_field_has_the_thermometer_error(self):
        env = self.env({"gadget_1": self.on_counter("r5_gadget", liquid_temp_c=60.123)})
        rec = env.world.s["entities"]["gadget_1"]
        env.realism.knowledge.grant("gadget_1", rec, realism_module.MEASURE, ["liquid_temp_c"])
        shown = self.seen(env, "gadget_1")["liquid_temp_c"]
        ctx = env.realism.context(env.world.s, "gadget_1", rec)
        self.assertEqual(shown, env.realism._noisy(60.123, 0.3, "gadget_1", "liquid_temp_c", realism_module.MEASURE, ctx, 1))
        self.assertNotEqual(shown, 60.12)

    # R9 ------------------------------------------------------------------------------------------------------
    def test_r9_unresolved_members_show_the_group_color_and_size(self):
        env = self.env({"gel_1": self.on_counter("r5_hand_gel"), "gel_2": self.on_counter("r5_glue_gel")})
        env.step("approach", self.h(env, "counter_1"))
        a, b = self.seen(env, "gel_1"), self.seen(env, "gel_2")
        self.assertEqual((a["color"], b["color"]), ("white", "white"))
        self.assertEqual(a["size_class"], b["size_class"])
        rec = env.world.s["entities"]["gel_2"]
        ctx = env.realism.context(env.world.s, "gel_2", rec)
        self.assertEqual(b["size_m"], env.realism.render("gel_2", rec, "size_m", [0.06, 0.06, 0.18], 0, ctx))
        self.assertTrue(env.step("read", self.h(env, "gel_2"))["ok"])
        self.assertEqual(self.seen(env, "gel_2")["color"], "blue")              # identified: its own color
        # a color this instance was given itself (not its type's) is simply what the camera sees
        env = self.env({"gel_3": self.on_counter("r5_hand_gel", color="red")})
        env.step("approach", self.h(env, "counter_1"))
        self.assertEqual(self.seen(env, "gel_3")["color"], "red")

    def test_r9_registry_warns_about_groups_whose_members_differ_at_a_glance(self):
        category = getattr(registry_module, "LookalikeWarning", None)
        self.assertIsNotNone(category)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            reg = registry_module.TypeRegistry(extra_files=[self.path])
        self.assertTrue(any(issubclass(w.category, category) for w in caught))
        self.assertTrue(any(c.startswith("r5_gel: color") for c in getattr(reg, "lookalike_warnings", [])))
        self.assertFalse(any(c.startswith("r5_feed") for c in reg.lookalike_warnings))

    # R10 -----------------------------------------------------------------------------------------------------
    def test_r10_per_entity_glance_tier_shows_at_a_glance(self):
        env = self.env({"crate_3": object_record("crate", "room_1", pos=[1, 1], sticker="red", size_m=[0.5, 0.4, 0.4],
                                                 _perception={"glance": ["sticker"]})})
        self.assertEqual(self.seen(env, "crate_3").get("sticker"), "red")

    # R11 -----------------------------------------------------------------------------------------------------
    def test_r11_contents_are_not_a_label(self):
        env = self.env({"refill_1": self.on_counter("r5_refill_bottle")})
        rec = env.world.s["entities"]["refill_1"]
        env.realism.knowledge.grant("refill_1", rec, realism_module.READ)
        ident = env.realism.identity("refill_1", rec, realism_module.READ)
        self.assertIn("candidates", ident)
        self.assertEqual(ident["kind"], "unidentified gel bottle")

    # R12 -----------------------------------------------------------------------------------------------------
    def test_r12_portions_follow_their_source_and_show_their_own_identity(self):
        blob = object_record("glue blob", "counter_1", relation="on", portable=True, category="adhesive",
                             derived_from="gel_1", size_m=[0.03, 0.03, 0.01])
        env = self.env({"gel_1": self.on_counter("r5_glue_gel"), "blob_1": blob})
        r, ents = env.realism, env.world.s["entities"]
        self.assertEqual(r.identity("blob_1", ents["blob_1"], 0)["label"], "blob of clear gel")
        r.knowledge.grant("blob_1", ents["blob_1"], realism_module.READ)       # a label-less portion: reading tells nothing
        self.assertIn("candidates", r.identity("blob_1", ents["blob_1"], realism_module.READ))
        r.knowledge.grant("gel_1", ents["gel_1"], realism_module.READ)         # reading its bottle does
        self.assertEqual(r.identity("blob_1", ents["blob_1"], realism_module.GLANCE), {"label": "glue blob", "kind": "adhesive"})

    # R13 -----------------------------------------------------------------------------------------------------
    def test_r13_extras_named_with_words_every_member_shares_are_kept(self):
        env = self.env({"tub_1": self.on_counter("r5_fish_food")})
        out = env.realism.filter_details("tub_1", {"object": "tub_1", "food_view": "flakes", "fish_count": 3},
                                         realism_module.INSPECT)
        self.assertIn("candidates", out)
        self.assertEqual(out.get("food_view"), "flakes")
        self.assertNotIn("fish_count", out)

    # R14 -----------------------------------------------------------------------------------------------------
    def test_r14_per_entity_read_fields_are_legible(self):
        rec = {"type": "r5_gadget", "evidence_of": "chewing", "_perception": {"read": ["evidence_of"]}}
        self.assertIn("evidence_of", realism_module.read_tier_fields(self.reg, rec))

    # R15 -----------------------------------------------------------------------------------------------------
    def test_r15_contents_from_an_unresolved_source_stay_unidentified(self):
        cup = object_record("cup", "counter_1", relation="on", portable=True, liquid="glue", liquid_l=0.1, capacity_l=0.3,
                            size_m=[0.08, 0.08, 0.1], contents_from="gel_1")
        bowl = object_record("bowl", "counter_1", relation="on", portable=True, product="fish flakes",
                             size_m=[0.15, 0.15, 0.06], contents_from="tub_1")
        env = self.env({"gel_1": self.on_counter("r5_glue_gel"), "tub_1": self.on_counter("r5_fish_food"),
                        "cup_9": cup, "bowl_9": bowl})
        r, ents = env.realism, env.world.s["entities"]
        env.step("approach", self.h(env, "counter_1"))
        for eid in ("cup_9", "bowl_9"):
            r.knowledge.grant(eid, ents[eid], realism_module.INSPECT)
        self.assertEqual(self.seen(env, "cup_9")["liquid"], "blob of clear gel")
        self.assertNotIn("contents_from", self.seen(env, "cup_9"))          # the provenance link itself is hidden
        self.assertEqual(self.seen(env, "bowl_9")["product"], "unidentified contents from small tub of food")
        r.knowledge.grant("gel_1", ents["gel_1"], realism_module.READ)
        self.assertEqual(self.seen(env, "cup_9")["liquid"], "glue")

    # R16 -----------------------------------------------------------------------------------------------------
    def test_r16_lying_awake_looks_like_asleep_and_dozing_seated_keeps_the_posture(self):
        c = fixture()
        people = {"sam_1": ("sleeping", "sleeping", [0, 1]), "kim_1": ("lying", "resting", [1, 1]),
                  "lee_1": ("sitting", "resting", [0, 2])}
        for pid, (posture, activity, pos) in people.items():
            c["people"][pid] = {"label": pid, "role": "adult", "location": "room_1", "pos": pos, "posture": posture,
                                "awake": posture != "sleeping", "authority": 2, "consent": True, "activity": activity}
        c["pets"]["rex_1"] = {"label": "Rex", "species": "dog", "location": "room_1", "pos": [1, 2], "posture": "resting"}
        c["pets"]["tom_1"] = {"label": "Tom", "species": "cat", "location": "room_1", "pos": [2, 1], "posture": "sleeping"}
        patches = []
        only = enabled_domains()   # importing a disabled domain would register it in a core-only process
        if only is None or "navigation_robot" in only:
            # a build without the navigation_robot / people observers that even this out on their own
            from house_sim.systems.navigation_robot import hazards as nav_hazards
            patches.append(mock.patch.object(nav_hazards, "scrub_observation", lambda env, state, obs: None))
        if only is None or "people" in only:
            from house_sim.systems.people import perception as people_perception
            patches.append(mock.patch.object(people_perception, "glance_activity", lambda p, view: None))
        for p in patches:
            p.start()
        try:
            obs = self.env(config=c).observe()
        finally:
            for p in patches:
                p.stop()
        kinds = {}
        for item in obs["grid"]["hazards"]:
            kinds.setdefault(item.get("source"), set()).add(item.get("kind"))
        self.assertEqual(kinds.get("sam_1"), {"lying"})
        self.assertEqual(kinds.get("kim_1"), {"lying"})
        self.assertEqual(kinds.get("rex_1"), kinds.get("tom_1"))
        self.assertEqual(obs["people"]["lee_1"]["posture"], "sitting")
        self.assertNotIn("activity", obs["people"]["lee_1"])

    # R17 -----------------------------------------------------------------------------------------------------
    def test_r17_public_unresolved_and_sealed_helpers(self):
        env = self.env({"gel_1": self.on_counter("r5_hand_gel"), "gadget_1": self.on_counter("r5_gadget", novel=True)})
        r, ents = env.realism, env.world.s["entities"]
        self.assertTrue(r.unresolved("gel_1"))
        self.assertTrue(r.unresolved("gadget_1"))
        self.assertFalse(r.unresolved("pan_1"))
        self.assertFalse(r.unresolved("no_such_thing_9"))
        r.knowledge.grant("gel_1", ents["gel_1"], realism_module.READ)
        self.assertFalse(r.unresolved("gel_1", ents["gel_1"]))
        closed = {"openable": True, "open": False, "material": "cardboard"}
        self.assertTrue(r.sealed(closed))
        self.assertEqual(r._sealed(closed), r.sealed(closed))

    # R18 -----------------------------------------------------------------------------------------------------
    def test_r18_knowledge_of_things_the_robot_displaced_survives(self):
        env = self.env()
        r, w = env.realism, env.world
        h = self.h(env, "salt_jar_1")
        env.step("approach", self.h(env, "counter_1"))
        self.assertTrue(env.step("read", h)["ok"])
        r.begin_step()
        w.begin()
        w.mut("salt_jar_1").update(location="room_1", relation="on", pos=[1, 2], displaced=True)
        r.commit_grants(True)
        w.commit()
        self.assertEqual(self.seen(env, "salt_jar_1").get("label"), "salt jar")


def _norm_handles(text):
    return re.sub(r"obj_[0-9a-f]{8,16}", "obj", text)


class KernelRound5RedTeamFixTests(unittest.TestCase):
    """Kernel round 5 red-team findings (identity & messages, privacy, darkness): each test failed before its fix."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = os.path.join(cls.tmp.name, "r5_redteam_test.json")
        with open(cls.path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(R5_TYPES, f)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cls.reg = registry_module.TypeRegistry(extra_files=[cls.path])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def env(self, extra=None, config=None, seed=0, mode="realistic"):
        c = config or fixture()
        for eid, rec in (extra or {}).items():
            c["entities"][eid] = rec
        return HouseEnv(c, seed=seed, perception=mode, registry=self.reg)

    @staticmethod
    def on_counter(type_name, **props):
        return dict({"type": type_name, "location": "counter_1", "relation": "on"}, **props)

    @staticmethod
    def h(env, eid):
        return env.realism.handles.handle(eid)

    # speech echo (R1 follow-up) ----------------------------------------------------------------------------------
    def test_speaking_member_names_about_an_unresolved_lookalike_is_identical_for_every_member(self):
        text = "Is it hand gel, glue gel, squeeze bottle or refill bottle? Or fish food?"
        out = []
        for type_name in ("r5_hand_gel", "r5_glue_gel", "r5_squeeze_bottle"):
            env = self.env({"probe_1": self.on_counter(type_name)})
            res = env.step("speak", self.h(env, "probe_1"), text=text)
            self.assertTrue(res["ok"])
            out.append(_norm_handles(res["message"]))
        self.assertEqual(len(set(out)), 1, out)
        self.assertIn("fish food", out[0])          # not a name of this group: untouched

    def test_speaking_a_guessed_name_of_a_novel_object_is_echoed_unchanged(self):
        text = "Is this a gadget, a hand gel or a frying pan?"
        out = []
        for type_name in ("r5_gadget", "r5_hand_gel"):
            env = self.env({"thing_1": self.on_counter(type_name, novel=True, size_m=[0.2, 0.2, 0.2], color="gray")})
            res = env.step("speak", self.h(env, "thing_1"), text=text)
            out.append(_norm_handles(res["message"]))
            # text the agent wrote earlier and gets back later (a label it wrote, read back) is not rewritten either
            later = env.realism.outbound_message(json.dumps({"label_text": text}), involved={"thing_1"})
            out.append(json.loads(later)["label_text"])
            # while a domain's own mention of the true name still is
            out.append(env.realism.outbound_message("The gadget hums", involved={"thing_1"}))
        self.assertEqual(out[0], text)
        self.assertEqual(out[0:2], out[3:5])
        self.assertEqual(out[1], text)
        self.assertNotIn("gadget", out[2])

    def test_a_generic_word_equal_to_one_member_name_is_rewritten_for_every_member(self):
        # a domain message that lists kinds of things: "hand gel" is the true name of one member only
        message = "Hold a towel, a hand gel dispenser or a sponge"
        out = []
        for type_name in ("r5_hand_gel", "r5_glue_gel", "r5_refill_bottle"):
            env = self.env({"probe_1": self.on_counter(type_name)})
            out.append(env.realism.outbound_message(message, involved={"probe_1"}))
        self.assertEqual(len(set(out)), 1, out)
        self.assertNotIn("hand gel", out[0])

    # id-existence oracle ---------------------------------------------------------------------------------------
    def test_free_text_ids_are_neutralised_whether_or_not_they_exist(self):
        if not (self.reg.known("fabric_softener") and self.reg.known("dishwasher")) or self.reg.known("layer"):
            self.skipTest("registry types used by this test changed")
        hidden = {"type": "fabric_softener", "location": "room_3", "relation": "on", "pos": [1, 1]}
        env = self.env({"fabric_softener_1": hidden})
        text = "Is fabric_softener_1 or fabric_softener_2 or salt_jar_1 near room_1 or dishwasher_1_basket? Try layer_2."
        res = env.step("speak", text=text)
        self.assertTrue(res["ok"])
        # an existing id, a guessed one built on a type name and an existing hand-written id all read "something";
        # rooms and words that are not ids (a slot name) stay
        self.assertEqual(res["message"], "Is something or something or something near room_1 or something? Try layer_2.")
        raw = env.realism.inbound({"text": "fabric_softener_1 fabric_softener_2"}, text_keys=("text",))
        self.assertEqual(raw["text"], "something something")
        self.assertTrue(realism_module.id_shaped("dishwasher_1_basket"))
        self.assertFalse(realism_module.id_shaped("main_water_valve"))
        # type names that contain digits themselves ("combination_wrench_1_2in") count as stems too
        self.assertTrue(realism_module._typed_id("combination_wrench_1_2in_3", {"combination_wrench_1_2in"}))
        self.assertTrue(realism_module._typed_id("dishwasher_1_basket", {"dishwasher"}))
        self.assertFalse(realism_module._typed_id("layer_2", {"dishwasher"}))
        # a part name of the command's own target is kept even when it is shaped like an id on a type name
        c = fixture()
        c["entities"]["counter_1"]["slots"] = {"dishwasher_2": {"item": None}}
        env = self.env(config=c)
        raw = env.realism.inbound({"target": self.h(env, "counter_1"), "slot": "dishwasher_2"}, entity_keys=("target",))
        self.assertEqual(raw["slot"], "dishwasher_2")

    # darkness --------------------------------------------------------------------------------------------------
    def _dark_config(self):
        c = fixture()
        c["clock"] = {"start": "2026-10-09T23:30:00", "hemisphere": "north"}
        for rec in c["entities"].values():
            if rec.get("category") == "light":
                rec["on"] = False
        c["people"]["child_1"] = {"label": "Kim", "role": "child", "location": "room_1", "pos": [0, 2], "awake": False,
                                  "posture": "lying", "consent": True}
        c["people"]["resident_1"].update(location="room_1", pos=[1, 0], awake=True, posture="standing")
        c["pets"] = {"dog_1": {"label": "Rex", "species": "dog", "location": "room_1", "pos": [4, 4], "posture": "standing"}}
        c["people"]["elder_1"] = {"label": "Grandpa", "role": "elder", "location": "room_3", "pos": [1, 1], "awake": True,
                                  "posture": "seated", "consent": True}
        return c

    def test_people_and_pets_in_the_dark_are_keyed_by_shape_handles(self):
        env = self.env(config=self._dark_config())
        obs = env.observe()
        self.assertTrue(obs["dark"])
        for group in ("people", "pets"):
            self.assertTrue(obs[group])
            for key, rec in obs[group].items():
                self.assertTrue(key.startswith("obj_"), key)
                self.assertNotIn("role", rec)
            self.assertEqual(list(obs[group]), sorted(obs[group]))
        text = json.dumps(obs)
        for pid in ("child_1", "resident_1", "dog_1"):
            self.assertNotIn(pid, text)
        shape = next(k for k, v in obs["people"].items() if v["pos"] == [0, 2])
        res = env.step("inspect", shape)
        self.assertTrue(res["ok"], res["message"])
        self.assertEqual(json.loads(res["message"])["agent"], shape)
        # naming a resident or pet by id in the dark is refused alike, whether or not they are in this room
        for pid in ("child_1", "resident_1", "elder_1", "dog_1"):
            r = env.step("inspect", pid)
            self.assertFalse(r["ok"])
            self.assertEqual(r["message"], realism_module.DARK_WHO)
        # the shape keeps its handle while it stays dark; a new dark spell (light on, then off) gives new handles
        self.assertIn(shape, env.observe()["people"])
        lights = [e for e, rec in env.world.s["entities"].items() if rec.get("category") == "light" and rec.get("location") == "room_1"]
        self.assertTrue(lights)
        for on in (True, False):
            for e in lights:
                env.world.s["entities"][e]["on"] = on
            seen = env.observe()
            self.assertEqual(bool(seen["dark"]), not on)
        self.assertNotIn(shape, seen["people"])
        self.assertEqual(len(seen["people"]), 2)
        # lit: ids again (oracle mode is unchanged)
        o = self.env(config=self._dark_config(), mode="oracle")
        self.assertIn("child_1", o.observe()["people"])

    # privacy ---------------------------------------------------------------------------------------------------
    def test_private_things_look_and_read_like_anything_else(self):
        views = {}
        for private in (False, True):
            c = fixture()
            c["entities"]["note_1"]["private"] = private
            c["entities"]["box_1"] = object_record("cardboard box", "counter_1", portable=True, size_m=[0.3, 0.3, 0.2], relation="on",
                                                 container=True, openable=True, open=True, accepts_items=True, capacity_l=10,
                                                 private=private)
            c["entities"]["box_1"].pop("pos", None)
            c["entities"]["ball_1"] = object_record("tennis ball", "box_1", portable=True, size_m=[0.07, 0.07, 0.07], relation="in")
            c["entities"]["ball_1"].pop("pos", None)
            env = self.env(config=c)
            env.step("approach", self.h(env, "counter_1"))
            res = env.step("read", self.h(env, "note_1"))
            obs = env.observe()
            views[private] = (res["ok"], _norm_handles(res["message"]),
                              obs["visible_entities"][self.h(env, "note_1")].get("text"),
                              self.h(env, "ball_1") in obs["visible_entities"])
            self.assertEqual(env.world.s["stats"].get("privacy_violations", 0), 1 if private else 0)
        self.assertEqual(views[False], views[True])
        self.assertEqual(views[True][2], "Dentist at 3pm")
        self.assertTrue(views[True][3])          # an open private box shows what is in it
        # oracle mode: reading private papers without permission is refused
        c = fixture()
        c["entities"]["note_1"]["private"] = True
        o = self.env(config=c, mode="oracle")
        o.step("approach", "counter_1")
        self.assertFalse(o.step("read", "note_1")["ok"])


class KnownMapNotWrittenTests(unittest.TestCase):
    """K-CAM (adoption round, K-EVAL request 3): the known map is env.mind.map and env._known_map a read-only view of
    it, so observing no longer writes env._known_map (perception's old table write and realism's write-back were
    dropped). Realism still issues the current room's door handles from it, so the realistic known map and the
    observation name the same doors."""

    def test_observing_never_writes_the_known_map(self):
        from house_sim.kernel import engine as engine_module
        for perception in ("oracle", "realistic"):
            for camera in ("room", "geometric"):
                env = HouseEnv(generate_house(2), seed=2, perception=perception, camera=camera)
                with mock.patch.object(engine_module.KnownMapView, "__setitem__",
                                       side_effect=AssertionError("observation wrote env._known_map")):
                    env.observe()
                    env.step("look")
                    env.observe()
                rid = env.world.s["robot"]["room"]
                self.assertIn(rid, env.known_map(), (perception, camera))

    def test_realistic_known_map_doors_match_the_observation(self):
        for seed in (0, 2):
            env = HouseEnv(generate_house(seed), seed=seed, perception="realistic")
            obs = env.observe()
            entry = env.known_map()[env.world.s["robot"]["room"]]
            observed = {c.get("to"): c.get("door") for c in obs["connections"]}
            mapped = {c.get("to"): c.get("door") for c in entry["connections"]}
            for to, door in observed.items():
                if door is not None:
                    self.assertEqual(mapped.get(to), door, (seed, to))
            self.assertEqual(leaks(env.known_map(), set(env.world.s["entities"])), set(), seed)


if __name__ == "__main__":
    unittest.main()
