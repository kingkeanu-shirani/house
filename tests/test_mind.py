"""The robot's memory: the Mind, recall, snapshots and prior knowledge (PEOPLE-IO, kernel v3.1;
docs/design/TIER1_SWEEP.md 7 and 16; TIER1_INTEGRATION 1.9 rule 5 and 1.12).

What this file pins:

* perception.perceived_ids is exactly what observe() renders, in both perception modes and both camera modes, and
  costs little;
* beliefs update only from what the robot perceives: things in other rooms are not believed, a thing moved while the
  robot looked elsewhere keeps its old belief (stale), and the Mind is the same whether or not the agent observes
  (observe_on_step on and off), in oracle and realistic perception;
* recall: stored memories with when they were stored, map, seen: / seen:<handle> / seen:room:<rid>, snap:; realistic
  messages name the issued handle of everything the robot remembers (out of view too) and never a handle it was not
  issued; an unresolved look-alike is remembered by what it looked like; times in whole seconds;
* snapshot and diff keep their scenes in the Mind (oracle messages as before kernel v3.1);
* the prior_knowledge setup op: map all, seen with times, seen_rooms with a size floor, snapshots, memory; changes
  made by later setup ops stay unknown; realistic handles are issued at reset and knowledge granted at the op's level
  lapses once the thing has moved;
* dump / load, env save / load and replay keep the Mind (a realistic replay also rebuilds the prior knowledge's
  handles and grants in its new Realism: stage-C merge); "mind.*" evaluation paths; the update budget.

Hand-built houses with core types only: the file passes with HOUSE_SIM_DOMAINS=core.
"""
import copy
import json
import random
import tempfile
import time
import unittest
from pathlib import Path

from house_sim.generator import generate_house
from house_sim.kernel import mind as mind_mod, perception
from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.registry import default_registry
from house_sim.tasks.setup import apply_setup
from house_sim.kernel.errors import SpecError

COLS, ROWS = 12, 8


def config(extra=None):
    """A lit living room ("lab": a lamp, a table with a mug, two look-alike jars and a small coin on it, a 0.6 m crate)
    and a dark-free hallway with a box; the robot in the lab."""
    ents = {"light_1": {"type": "lamp", "location": "lab", "pos": [COLS - 1, 0], "on": True},
            "light_2": {"type": "lamp", "location": "hall", "pos": [0, 0], "on": True},
            "table_1": {"type": "table", "location": "lab", "pos": [3, 3], "accepts_items": True},
            "mug_1": {"type": "generic_item", "label": "mug", "location": "table_1", "relation": "on",
                      "size_m": [0.09, 0.09, 0.11], "color": "red"},
            "jar_1": {"type": "generic_item", "label": "salt jar", "location": "table_1", "relation": "on",
                      "looks_like": "white_powder_jar", "label_text": "SALT", "size_m": [0.08, 0.08, 0.12]},
            "jar_2": {"type": "generic_item", "label": "sugar jar", "location": "table_1", "relation": "on",
                      "looks_like": "white_powder_jar", "label_text": "SUGAR", "size_m": [0.08, 0.08, 0.12]},
            "crate_1": {"type": "generic_item", "label": "crate", "location": "lab", "pos": [8, 5],
                        "size_m": [0.6, 0.5, 0.5]},
            "coin_1": {"type": "generic_item", "label": "coin", "location": "table_1", "relation": "on",
                       "size_m": [0.02, 0.02, 0.003]},
            "box_1": {"type": "box", "location": "hall", "pos": [1, 2], "accepts_items": True}}
    cfg = {"schema_version": 2, "start_room": "lab",
           "rooms": {"lab": {"size_m": [COLS * 0.5, ROWS * 0.5], "type": "living_room", "label": "Living room",
                             "exits": ["hall"]},
                     "hall": {"size_m": [2.0, 3.0], "type": "hallway", "label": "Hall", "exits": ["lab"]}},
           "entities": ents,
           "people": {"ann": {"label": "Ann", "role": "adult", "location": "lab", "pos": [6, 6]}},
           "robot": {"room": "lab", "pos": [2, 3]}}
    for key, value in (extra or {}).items():
        cfg[key] = copy.deepcopy(value)
    return cfg


def make(perception_mode="oracle", observe=True, cfg=None, camera="room"):
    return HouseEnv(cfg or config(), seed=4, perception=perception_mode, trace=False, observe_on_step=observe,
                    camera=camera)


def rendered(env, obs):
    out = set()
    realism = env.realism
    for key in obs.get("visible_entities", {}):
        out.add(realism.handles.resolve(key) if realism is not None else key)
    for group in ("people", "pets"):
        out |= {k for k in obs.get(group, {}) if k in env.world.s.get(group, {})}
    return out


SCRIPT = [{"action": "look"}, {"action": "approach", "target": "mug_1"}, {"action": "pick_up", "target": "mug_1"},
          {"action": "move", "target": "hall"}, {"action": "approach", "target": "box_1"},
          {"action": "place", "target": "mug_1", "destination": "box_1", "relation": "in"},
          {"action": "move", "target": "lab"}, {"action": "wait", "seconds": 600}]


def play(env, script=SCRIPT):
    for command in script:
        cmd = dict(command)
        action = cmd.pop("action")
        target = cmd.pop("target", "")
        if target and env.realism is not None and target in env.world.s["entities"]:
            target = env.realism.handles.handle(target)
        dest = cmd.pop("destination", "")
        if dest and env.realism is not None and dest in env.world.s["entities"]:
            dest = env.realism.handles.handle(dest)
        env.step(action, target, dest, **cmd)
    return env


class PerceivedTests(unittest.TestCase):
    def test_perceived_ids_are_what_observe_renders(self):
        for mode in ("oracle", "realistic"):
            for camera in ("room", "geometric"):
                for seed in (0, 2):
                    env = HouseEnv(generate_house(seed), seed=seed, perception=mode, camera=camera, trace=False)
                    rng = random.Random(seed)
                    obs = env.observe()
                    for _ in range(12):
                        self.assertEqual(set(perception.perceived_ids(env)), rendered(env, obs), (mode, camera, seed))
                        exits = obs.get("room", {}).get("exits") or []
                        handles = sorted(obs.get("visible_entities", {}))
                        if rng.random() < 0.4 and exits:
                            result = env.step("move", rng.choice(exits))
                        elif handles and rng.random() < 0.5:
                            result = env.step("approach", rng.choice(handles))
                        else:
                            result = env.step("look", "around" if camera == "geometric" else "")
                        obs = result["observation"]

    def test_perceived_ids_are_cached_per_step(self):
        env = make()
        first = perception.perceived_ids(env)
        self.assertIs(first, perception.perceived_ids(env))
        env.step("wait", seconds=5)
        self.assertEqual(first - {"ann"}, perception.perceived_ids(env) - {"ann"})

    def test_people_in_the_dark_are_shapes_not_people(self):
        cfg = config()
        cfg["entities"]["light_1"]["on"] = False
        cfg["clock"] = {"start": "2026-10-09T23:30:00"}
        env = make("realistic", cfg=cfg)
        self.assertNotIn("ann", perception.perceived_ids(env))
        oracle = make("oracle", cfg=copy.deepcopy(cfg))
        self.assertIn("ann", perception.perceived_ids(oracle))
        self.assertIn("crate_1", perception.perceived_ids(env))       # a dark shape is still something seen
        self.assertEqual(env.mind.seen["crate_1"]["label_seen"], "dark shape")


class BeliefTests(unittest.TestCase):
    def test_beliefs_come_only_from_perception(self):
        env = make()
        self.assertIn("mug_1", env.mind.seen)
        self.assertNotIn("box_1", env.mind.seen)                      # in the hall, never seen
        self.assertEqual(sorted(env.mind.map), ["lab"])
        play(env, SCRIPT[:5])
        self.assertIn("box_1", env.mind.seen)
        self.assertEqual(sorted(env.mind.map), ["hall", "lab"])
        crate = env.mind.seen["crate_1"]
        env.world.s["entities"]["crate_1"]["pos"] = [1, 7]          # moved while the robot is in the hall
        env.step("wait", seconds=30)
        self.assertEqual(env.mind.seen["crate_1"]["pos"], crate["pos"])
        self.assertTrue(env.mind.belief("crate_1")["stale"])
        env.step("move", "lab")
        self.assertEqual(env.mind.seen["crate_1"]["pos"], [1, 7])
        self.assertFalse(env.mind.belief("crate_1")["stale"])
        self.assertEqual(env.mind.seen["ann"]["agent"], "people")

    def test_the_mind_does_not_depend_on_observing(self):
        for mode in ("oracle", "realistic"):
            a = play(make(mode, observe=True))
            b = play(make(mode, observe=False))
            self.assertEqual(a.mind.dump(), b.mind.dump(), mode)

    def test_an_unresolved_look_alike_is_remembered_by_its_look(self):
        env = make("realistic")
        belief = env.mind.seen["jar_1"]
        self.assertEqual(belief["ident"], "unresolved")
        self.assertNotIn("salt", belief["label_seen"])
        self.assertEqual(belief["label_seen"], env.mind.seen["jar_2"]["label_seen"])
        oracle = make()
        self.assertEqual(oracle.mind.seen["jar_1"]["label_seen"], "salt jar")


class RecallTests(unittest.TestCase):
    def test_recall_forms(self):
        env = play(make())
        out = json.loads(env.step("recall", "map")["message"])
        self.assertEqual(sorted(out), ["hall", "lab"])
        self.assertEqual(out["hall"]["exits"], ["lab"])
        self.assertIn("seen", out["lab"])
        seen = json.loads(env.step("recall", "seen:")["message"])
        self.assertIn("box_1", seen)
        one = json.loads(env.step("recall", "seen:mug_1")["message"])
        self.assertEqual((one["in"], one["room"]), ("box_1", "hall"))
        self.assertTrue(one["stale"])
        room = json.loads(env.step("recall", "seen:room:hall")["message"])
        self.assertEqual(sorted(room), sorted(k for k, v in env.mind.seen.items() if v.get("room") == "hall"))
        self.assertEqual(json.loads(env.step("recall", "seen:nothing_9")["message"]), {})
        env.step("remember", "rule:quiet", value=True)
        stored = json.loads(env.step("recall", "rule:")["message"])
        self.assertEqual(stored["rule:quiet"]["value"], True)
        self.assertTrue(stored["rule:quiet"]["stored"].startswith("today"))

    def test_realistic_recall_names_issued_handles_out_of_view(self):
        env = play(make("realistic", observe=False))
        handle = env.realism.handles.by_id["mug_1"]
        message = env.step("recall", "seen:")["message"]
        self.assertIn(handle, message)                              # the mug is in the hall's box, out of view
        self.assertNotIn("something", message)
        self.assertNotIn("mug_1", message)
        issued = set(env.realism.handles.by_handle)
        import re
        self.assertTrue(set(re.findall(r"obj_[0-9a-f]{8,16}", message)) <= issued)
        data = json.loads(message)
        self.assertTrue(all(float(v["seen_s"]).is_integer() for v in data.values() if "seen_s" in v))

    def test_a_thing_never_perceived_stays_something(self):
        env = make("realistic")
        env.step("remember", "where:secret", value="box_1")      # a true id from the agent's own words: neutralised
        message = env.step("recall", "where:")["message"]
        self.assertNotIn("box_1", message)

    def test_snapshot_and_diff(self):
        env = make()
        self.assertEqual(env.step("snapshot", "before")["message"], "Snapshot before")
        self.assertIn("before", env.mind.snapshots)
        env.world.s["entities"]["crate_1"]["pos"] = [1, 7]
        changes = json.loads(env.step("diff", "before")["message"])
        self.assertEqual([c["entity"] for c in changes], ["crate_1"])
        self.assertEqual(changes[0]["before"]["pos"], [8, 5])
        self.assertFalse(env.step("diff", "nope")["ok"])
        snaps = json.loads(env.step("recall", "snap:")["message"])
        self.assertEqual(list(snaps), ["before"])
        real = make("realistic")
        real.step("snapshot", "s")
        real.world.s["entities"]["crate_1"]["pos"] = [1, 7]
        message = real.step("diff", "s")["message"]
        self.assertIn(real.realism.handles.by_id["crate_1"], message)
        self.assertNotIn("crate_1", message)


class PriorKnowledgeTests(unittest.TestCase):
    def build(self, ops, perception_mode="oracle"):
        cfg = config()
        reg = default_registry()
        spec = {"catalog_id": "test", "setup": ops}
        apply_setup(cfg, ops, reg, 4, spec)
        return cfg, make(perception_mode, cfg=cfg)

    def test_map_seen_snapshots_and_memory(self):
        ops = [{"op": "prior_knowledge", "map": "all",
                "seen": [{"entity": "box_1", "at": "-1d 18:40", "level": "glance"}],
                "seen_rooms": {"rooms": ["lab"], "at": "-2h", "min_size_m": 0.15},
                "snapshots": {"tidy": {"room": "lab", "at": "-1d"}},
                "memory": {"rule:no_vacuum_after_21": {"value": True, "at": "-3d"}}},
               {"op": "move", "target": "crate_1", "to": "hall"}]
        cfg, env = self.build(ops)
        prior = cfg["prior_knowledge"]
        self.assertEqual(sorted(prior["map"]), ["hall", "lab"])
        self.assertIn("crate_1", prior["seen"])
        self.assertNotIn("coin_1", prior["seen"])                  # under the size floor
        self.assertEqual(prior["seen"]["box_1"]["seen_s"], -(12 * 3600 + 20 * 60))     # 18:40 the day before 07:00
        self.assertEqual(cfg["memory"]["rule:no_vacuum_after_21"]["time_s"], -3 * 86400)
        self.assertEqual(env.mind.prior["source"], "setup")
        self.assertLess(env.mind.prior["from_s"], 0)
        belief = env.mind.belief("box_1")
        self.assertTrue(belief["seen"].startswith("yesterday 18:40"))
        self.assertEqual(env.mind.seen["crate_1"]["room"], "lab")   # moved after the op: the robot does not know
        changes = json.loads(env.step("diff", "tidy")["message"])
        self.assertIn("crate_1", [c["entity"] for c in changes])
        stored = json.loads(env.step("recall", "rule:")["message"])
        self.assertTrue(stored["rule:no_vacuum_after_21"]["stored"].startswith("3 days ago"))

    def test_realistic_prior_knowledge_issues_handles_and_lapsing_grants(self):
        ops = [{"op": "prior_knowledge", "seen": [{"entity": "jar_1", "level": "read"},
                                                  {"entity": "box_1", "level": "inspect"}]},
               {"op": "move", "target": "box_1", "to": "lab"}]
        cfg, env = self.build(ops, "realistic")
        self.assertIn("jar_1", env.realism.handles.by_id)
        self.assertIn("box_1", env.realism.handles.by_id)
        record = env.world.s["entities"]["jar_1"]
        self.assertGreaterEqual(env.realism.knowledge.entity_level("jar_1", record), 3)
        self.assertFalse(env.realism.unresolved("jar_1", record))   # read yesterday, not moved since
        box = env.world.s["entities"]["box_1"]
        self.assertEqual(env.realism.knowledge.entity_level("box_1", box), 0)   # it moved: the knowledge lapsed

    def test_prior_look_alikes_are_remembered_at_the_level_they_were_seen(self):
        """Stage-C fix: a look-alike known from a glance is remembered by its look (the group's descriptor, ident
        unresolved), never its true label; one read before by its label; oracle runs keep the labels."""
        ops = [{"op": "prior_knowledge", "seen": [{"entity": "jar_1", "level": "glance"},
                                                  {"entity": "jar_2", "level": "read"}]},
               {"op": "move", "target": "jar_1", "to": "hall"}, {"op": "move", "target": "jar_2", "to": "hall"}]
        cfg, env = self.build(ops, "realistic")
        self.assertEqual((cfg["prior_knowledge"]["seen"]["jar_1"]["level"],
                          cfg["prior_knowledge"]["seen"]["jar_2"]["level"]), ("glance", "read"))
        seen = json.loads(env.step("recall", "seen:")["message"])
        glance = seen[env.realism.handles.handle("jar_1")]
        read = seen[env.realism.handles.handle("jar_2")]
        self.assertEqual(glance["ident"], "unresolved")
        self.assertNotIn("salt", json.dumps(glance))
        self.assertEqual(glance["label"], env.realism._identify("jar_1", env.world.s["entities"]["jar_1"], 0)[0]
                         ["label"])
        self.assertEqual(read["label"], "sugar jar")
        self.assertNotIn("ident", read)
        cfg, oracle = self.build(ops)
        self.assertEqual(oracle.mind.seen["jar_1"]["label_seen"], "salt jar")

    def test_bad_ops_are_spec_errors(self):
        for op in ({"op": "prior_knowledge", "seen": [{"entity": "nope"}]},
                   {"op": "prior_knowledge", "seen": [{"entity": "mug_1", "at": "+2h"}]},
                   {"op": "prior_knowledge", "seen": [{"entity": "mug_1", "level": "touch"}]},
                   {"op": "prior_knowledge", "bogus": 1},
                   {"op": "prior_knowledge", "map": "some"}):
            with self.assertRaises(SpecError, msg=str(op)):
                apply_setup(config(), [op], default_registry(), 4, {"catalog_id": "t"})

    def test_import_restores_a_dump(self):
        env = play(make())
        dump = env.mind.dump()
        cfg, again = self.build([{"op": "prior_knowledge", "import": dump}])
        self.assertIn("box_1", again.mind.seen)
        self.assertIn("hall", again.mind.map)


class PersistenceTests(unittest.TestCase):
    def test_dump_and_load_roundtrip(self):
        env = play(make("realistic"))
        dump = env.mind.dump()
        restored = mind_mod.Mind().load(json.loads(json.dumps(dump)))
        self.assertEqual(restored.dump(), dump)

    def test_save_load_and_replay_keep_the_mind(self):
        env = play(make("realistic", cfg=config()))
        env.trace = True
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.json"
            env.save(str(path))
            loaded = HouseEnv.load(str(path))
            self.assertEqual(loaded.mind.dump(), env.mind.dump())
        traced = HouseEnv(config(), seed=4, perception="realistic", trace=True)
        play(traced)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "r.json"
            traced.save_replay(str(path))
            replayed = HouseEnv.replay(str(path))
            self.assertEqual(replayed.mind.dump(), traced.mind.dump())

    def test_replay_rebuilds_prior_knowledge_in_the_realism(self):
        """Stage-C merge: HouseEnv.replay re-creates the Realism after reset, so the Mind is started again against it;
        before, the prior knowledge's handles and read-level grants were lost on replay (the replayed robot no longer
        knew the jar it read yesterday, and recall, the Mind and realism all differed from the recorded episode)."""
        cfg = config()
        ops = [{"op": "prior_knowledge", "seen": [{"entity": "jar_1", "level": "read"},
                                                  {"entity": "box_1", "level": "inspect"}]}]
        apply_setup(cfg, ops, default_registry(), 4, {"catalog_id": "t", "setup": ops})
        env = HouseEnv(cfg, seed=4, perception="realistic", trace=True)
        play(env, [{"action": "look"}, {"action": "wait", "seconds": 60}])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "r.json"
            env.save_replay(str(path))
            again = HouseEnv.replay(str(path))
        jar = again.world.s["entities"]["jar_1"]
        self.assertGreaterEqual(again.realism.knowledge.entity_level("jar_1", jar), 3)     # read yesterday
        self.assertIn("box_1", again.realism.handles.by_id)                                 # known from before
        self.assertEqual(again.realism.dump(), env.realism.dump())
        self.assertEqual(again.mind.dump(), env.mind.dump())
        self.assertEqual(again.step("recall", "seen:")["message"], env.step("recall", "seen:")["message"])

    def test_mind_paths_in_evaluation(self):
        task = {"instruction": "Explore", "success": [{"path": "mind.map", "op": "len_gte", "value": 2}]}
        env = HouseEnv(config(), task=task, seed=4, trace=False)
        self.assertFalse(env.evaluate()["success"])
        env.step("move", "hall")
        self.assertTrue(env.evaluate()["success"])


class BudgetTests(unittest.TestCase):
    def test_update_budget_on_a_generated_house(self):
        env = HouseEnv(generate_house(1), seed=1, trace=False)
        ids = perception.perceived_ids(env)
        fresh = mind_mod.Mind()
        start = time.perf_counter()
        for _ in range(20):
            fresh.update(env, ids)
        self.assertLess((time.perf_counter() - start) / 20, 0.003)       # budget 1 ms, 3x
        start = time.perf_counter()
        for _ in range(20):
            env.perception._perceived = None
            perception.perceived_ids(env)
        self.assertLess((time.perf_counter() - start) / 20, 0.006)       # the perceived set, shared by the update


if __name__ == "__main__":
    unittest.main()
