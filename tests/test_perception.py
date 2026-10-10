"""Realistic (imperfect) perception: handles, tiers, knowledge, noise, look-alikes, determinism, no leaks."""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from house_sim.generator import generate_house
from house_sim.kernel.engine import HouseEnv
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
        self.assertFalse(haz["ok"])
        self.assertNotIn("corrosive", haz["message"])
        priv = env.step("pick_up", self.h(env, "pan_1"))
        self.assertFalse(priv["ok"])
        self.assertNotIn("Owner", priv["message"])
        self.assertEqual(priv["category"], "permission")

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

if __name__ == "__main__":
    unittest.main()
