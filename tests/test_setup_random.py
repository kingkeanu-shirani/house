"""Setup randomisation (VERIFY_HARDENING 9.2, 9.7, 22.5; WP9): the ops shuffle, sample, arrange, time and weighted
choose (determinism, ranges, shares over many seeds, validation), an event at a null time, the schema-4 seeded
order of spawn count (on a surface, in a container, on the floor; schema 3 unchanged), Placer.order / permutation /
arrange, the look-based floor ring of things whose identity the robot cannot see (closeout_INT3: unfamiliar twins
land alike), sample_roles for scenario helpers, the audit fast path (bindings and placements of a full build, the
physics lint skipped, both camera modes, audit_diff), the disk cache of generated houses (exact round trip, hits,
the once-per-process check against a fresh build, planted and corrupt files, keys, the fingerprint's inputs, off,
prune, digests of environments built from it in both perception and both camera modes) and the .gitignore entries
(reports/cache/ ignored; build/ and dist/ only at the project root, closeout_REV finding 1)."""
import copy
import glob
import gzip
import hashlib
import json
import os
import pickle
import random
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from house_sim.generator import disk_cache, generate_house
from house_sim.generator.place import Placer
from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.errors import SpecError
from house_sim.kernel.plugins import enabled_domains, load_domains
from house_sim.kernel.predicates import digest
from house_sim.kernel.registry import default_registry
from house_sim.tasks import setup as setup_mod
from house_sim.tasks.runner import build
from house_sim.tasks.setup import apply_setup, audit_diff
from house_sim.tasks.spec import choose_variant, expand, schema_of

ROOT = Path(__file__).resolve().parents[1]
CID = "T-wp9random-01"
EMPTY = {"rooms": {}, "entities": {}, "people": {}, "pets": {}, "connections": {}, "start_room": None}
SEEDS = range(200)
NOVEL_TWINS = ("electric_kettle", "smartphone", "hair_dryer", "remote_control", "spray_bottle", "trigger_bottle")
NOVEL_LOOK = {"novel": True, "size_m": [0.2, 0.15, 0.25], "color": "grey", "material": "plastic"}
PROBE = {"catalog_id": "T-auditprobe-01", "title": "probe", "perception": "realistic", "instruction": "probe",
         "house": {"residents": "minimal", "pets": "none"}, "success": [{"path": "time_s", "op": "gt", "value": 1e12}],
         "budget": {"steps": 100, "time_s": 1000}, "reference": [], "wrong": []}
KITCHEN = [{"op": "room", "var": "k", "type": "kitchen"},
           {"op": "find", "var": "counter", "type": "counter", "room": "{k}", "pick": "first"}]


def run(ops, seed=0, schema=4, config=None, cid=CID, **kw):
    """(config, bindings) of the ops on a copy of config (an empty one by default)."""
    config = pickle.loads(pickle.dumps(EMPTY if config is None else config, -1))     # an exact copy, faster
    spec = {"catalog_id": cid}
    if schema is not None:
        spec["schema"] = schema
    return config, apply_setup(config, ops, default_registry(), seed, spec, **kw)


def share_ok(count, total, k, slack=0.1):
    return abs(count / float(total) - 1.0 / k) <= slack + 1e-9


def known(name, fallback):
    """A domain type, or a core type when the domains are not loaded (HOUSE_SIM_DOMAINS=core)."""
    load_domains()
    return name if default_registry().known(name) else fallback


def width(type_name):
    return setup_mod.geometry.footprint_m(default_registry().make(type_name, "x"))[0]


BOTTLE = known("glass_bottle", "key")         # small, portable, no surface
ITEM = known("mug", "book")


class _House(unittest.TestCase):
    house = None

    @classmethod
    def setUpClass(cls):
        load_domains()
        if _House.house is None:
            _House.house = generate_house(0)

    def table_ops(self, n=3, extra=(), tables=1):
        ops = [{"op": "room", "var": "lr", "type": "living_room"}]
        for i in range(tables):
            ops.append({"op": "spawn", "var": "t{}".format(i), "type": "side_table" if tables > 1 else "table",
                        "furniture": "{lr}", "wall": False})
        ops.append({"op": "spawn", "var": "b", "type": BOTTLE, "on": "{t0}", "count": n})
        return ops + list(extra)

    _prepared = {}

    def prepared(self, n=3, tables=1, ops=None):
        """(config, bindings) of a house with the tables and bottles in place (built once, schema 3: no draws of the
        new order), for runs of further ops with other seeds."""
        key = (n, tables, json.dumps(ops))
        if key not in _House._prepared:
            _House._prepared[key] = run(ops if ops is not None else self.table_ops(n, tables=tables), 0, schema=3,
                                        config=self.house)
        config, bindings = _House._prepared[key]
        return config, {k: v for k, v in bindings.items() if not k.startswith("_") and k != "seed"}

    def then(self, prepared, ops, seed, **kw):
        config, bindings = prepared
        return run(ops, seed, config=config, bindings=bindings, **kw)


class ScalarOpTests(unittest.TestCase):
    def test_shuffle_binds_a_seeded_permutation(self):
        firsts, orders = {}, set()
        for seed in SEEDS:
            _, b = run([{"op": "shuffle", "var": "o", "from": ["a", "b", "c"]}], seed)
            _, again = run([{"op": "shuffle", "var": "o", "from": ["a", "b", "c"]}], seed)
            self.assertEqual(b["o"], again["o"])
            self.assertEqual(sorted(b["o"]), ["a", "b", "c"])
            self.assertEqual([b["o_1"], b["o_2"], b["o_3"]], b["o"])
            firsts[b["o"][0]] = firsts.get(b["o"][0], 0) + 1
            orders.add(tuple(b["o"]))
        self.assertEqual(len(orders), 6)
        for value in "abc":
            self.assertTrue(share_ok(firsts[value], len(SEEDS), 3), firsts)
        with self.assertRaises(SpecError):
            run([{"op": "shuffle", "var": "o", "from": "abc"}])

    def test_shuffle_of_a_binding(self):
        _, b = run([{"op": "bind", "var": "g", "value": ["x", "y"]}, {"op": "shuffle", "var": "o", "from": "{g}"}])
        self.assertEqual(sorted(b["o"]), ["x", "y"])

    def test_sample_draws_a_subset_of_a_random_size_in_list_order(self):
        pool = ["p1", "p2", "p3", "p4"]
        sizes, sets, member = {}, set(), {p: 0 for p in pool}
        for seed in SEEDS:
            _, b = run([{"op": "sample", "var": "dry", "from": pool, "k": {"min": 1, "max": 3}, "rest": "wet"}], seed)
            self.assertTrue(1 <= len(b["dry"]) <= 3)
            self.assertEqual(b["dry"], [p for p in pool if p in b["dry"]])
            self.assertEqual(b["wet"], [p for p in pool if p not in b["dry"]])
            sizes[len(b["dry"])] = sizes.get(len(b["dry"]), 0) + 1
            sets.add(tuple(b["dry"]))
            for p in b["dry"]:
                member[p] += 1
        self.assertEqual(sorted(sizes), [1, 2, 3])
        for size in (1, 2, 3):
            self.assertTrue(share_ok(sizes[size], len(SEEDS), 3), sizes)
        self.assertGreaterEqual(len(sets), 12)
        mean = sum(member.values()) / 4.0
        for p in pool:
            self.assertLess(abs(member[p] - mean), 0.15 * len(SEEDS), member)
        _, b = run([{"op": "sample", "var": "s", "from": pool, "k": 2}])
        self.assertEqual(len(b["s"]), 2)
        _, b = run([{"op": "sample", "var": "s", "from": pool}])         # default 1 .. n - 1
        self.assertTrue(1 <= len(b["s"]) <= 3)
        self.assertNotIn("s_rest", b)

    def test_sample_rejects_bad_arguments(self):
        for op in ({"op": "sample", "var": "s", "from": ["a", "b"], "k": 3},
                   {"op": "sample", "var": "s", "from": ["a", "b"], "k": {"min": 2, "max": 1}},
                   {"op": "sample", "var": "s", "from": ["a", "b"], "k": {"min": 1, "most": 2}},
                   {"op": "sample", "var": "s", "from": ["a", "b"], "k": 1.5},
                   {"op": "sample", "var": "s", "from": ["a", "b"], "k": True},
                   {"op": "sample", "var": "s", "from": []},
                   {"op": "sample", "var": "s", "from": ["a", "a"]},
                   {"op": "sample", "var": "s", "from": "ab"}):
            with self.assertRaises(SpecError, msg=str(op)):
                run([op])

    def test_time_draws_whole_seconds_on_its_step_or_never(self):
        values, nulls = [], 0
        op = {"op": "time", "var": "onset", "min": 600, "max": 3000, "never_p": 0.25, "step_s": 30}
        for seed in range(400):
            _, b = run([op], seed)
            if b["onset"] is None:
                nulls += 1
                continue
            self.assertIsInstance(b["onset"], int)
            self.assertTrue(600 <= b["onset"] <= 3000)
            self.assertEqual((b["onset"] - 600) % 30, 0)
            values.append(b["onset"])
        self.assertLess(abs(nulls / 400.0 - 0.25), 0.06, nulls)
        self.assertGreaterEqual(max(values) - min(values), 2200)
        self.assertGreater(len(set(values)), 50)
        for seed in range(100):
            _, b = run([{"op": "time", "var": "t", "min": 5, "max": 9}], seed)
            self.assertIn(b["t"], range(5, 10))

    def test_time_rejects_bad_arguments(self):
        for op in ({"op": "time", "var": "t", "min": 10, "max": 5},
                   {"op": "time", "var": "t", "min": -1, "max": 5},
                   {"op": "time", "var": "t", "min": 1.5, "max": 5},
                   {"op": "time", "var": "t", "max": 5},
                   {"op": "time", "var": "t", "min": 0, "max": 5, "never_p": 1},
                   {"op": "time", "var": "t", "min": 0, "max": 5, "never_p": "x"},
                   {"op": "time", "var": "t", "min": 0, "max": 5, "step_s": 0},
                   {"op": "time", "var": "t", "min": 0, "max": 5, "at": 3}):
            with self.assertRaises(SpecError, msg=str(op)):
                run([op])

    def test_an_event_at_a_null_time_is_not_scheduled(self):
        ops = [{"op": "time", "var": "onset", "min": 600, "max": 3000, "never_p": 0.5},
               {"op": "event", "at_s": "{onset}", "effect": "start_rain", "args": {}}]
        seen = set()
        for seed in range(40):
            config, b = run(ops, seed)
            events = config.get("events") or []
            if b["onset"] is None:
                self.assertEqual(events, [])
            else:
                self.assertEqual([e["at_s"] for e in events], [b["onset"]])
            seen.add(b["onset"] is None)
        self.assertEqual(seen, {True, False})
        config, _ = run([{"op": "event", "at_s": 30, "effect": "start_rain"}])
        self.assertEqual(len(config["events"]), 1)

    def test_weighted_choose_follows_the_weights(self):
        counts = {"a": 0, "b": 0, "c": 0}
        for seed in range(1000):
            _, b = run([{"op": "choose", "var": "x", "from": ["a", "b", "c"], "weights": [3, 1, 0]}], seed)
            counts[b["x"]] += 1
        self.assertEqual(counts["c"], 0)
        self.assertLess(abs(counts["a"] / 1000.0 - 0.75), 0.04, counts)
        self.assertEqual(setup_mod.weighted_index(random.Random(1), [0, 0, 2]), 2)
        for weights in ([1, 2], [1, -1, 1], [0, 0, 0], [1, "2", 1], [True, 1, 1], "abc"):
            with self.assertRaises(SpecError, msg=str(weights)):
                run([{"op": "choose", "var": "x", "from": ["a", "b", "c"], "weights": weights}])

    def test_unweighted_choose_draws_as_before(self):
        options = ["oven", "sink", "fridge", "toaster"]
        for seed in range(60):
            _, b = run([{"op": "choose", "var": "x", "from": options}], seed, schema=None)
            self.assertEqual(b["x"], random.Random("setup:{}:{}".format(CID, seed)).choice(options))

    def test_the_ops_are_deterministic(self):
        ops = [{"op": "shuffle", "var": "o", "from": [1, 2, 3, 4]},
               {"op": "sample", "var": "s", "from": "{o}", "k": {"min": 1, "max": 4}},
               {"op": "time", "var": "t", "min": 0, "max": 10000, "never_p": 0.1},
               {"op": "choose", "var": "c", "from": ["x", "y"], "weights": [1, 1]}]
        runs = [run(ops, seed)[1] for seed in (7, 7, 8)]
        self.assertEqual(runs[0], runs[1])
        self.assertNotEqual(runs[0], runs[2])


class SampleRolesTests(unittest.TestCase):
    def ctx(self, seed=0):
        return setup_mod.SetupCtx(copy.deepcopy(EMPTY), default_registry(), random.Random(seed), seed,
                                  {"catalog_id": CID, "schema": 4})

    def test_random_lists_and_errors(self):
        pool = ["fern", "violet", "cactus", "palm"]
        sizes = set()
        for seed in range(100):
            roles = self.ctx(seed).sample_roles("random", pool)
            self.assertEqual(roles, [p for p in pool if p in roles])
            self.assertTrue(1 <= len(roles) <= 3)
            sizes.add(len(roles))
            self.assertEqual(roles, self.ctx(seed).sample_roles("random", pool))
        self.assertEqual(sizes, {1, 2, 3})
        self.assertEqual(self.ctx().sample_roles("random", pool, k=4), pool)
        self.assertEqual(self.ctx().sample_roles(["palm", "fern"], pool), ["fern", "palm"])
        self.assertEqual(self.ctx().sample_roles(None, pool), [])
        self.assertEqual(self.ctx().sample_roles("random", ["one"]), ["one"])
        for value in (["rose"], ["fern", "fern"], "all", 3):
            with self.assertRaises(SpecError, msg=str(value)):
                self.ctx().sample_roles(value, pool)
        with self.assertRaises(SpecError):
            self.ctx().sample_roles("random", [])


class PlacerOrderTests(unittest.TestCase):
    def test_order_permutes_only_in_shuffle_mode(self):
        for shuffle in (False, True):
            rng = random.Random(3)
            placer = Placer(copy.deepcopy(EMPTY), default_registry(), rng, shuffle=shuffle)
            state = rng.getstate()
            out = placer.order(range(8))
            if shuffle:
                self.assertNotEqual(rng.getstate(), state)
                self.assertEqual(sorted(out), list(range(8)))
            else:
                self.assertEqual(rng.getstate(), state)      # legacy setups draw nothing more
                self.assertEqual(out, list(range(8)))
        rng = random.Random(3)
        placer = Placer(copy.deepcopy(EMPTY), default_registry(), rng)
        self.assertEqual(sorted(placer.permutation("abcd")), list("abcd"))
        self.assertFalse(Placer(EMPTY, default_registry(), random.Random(0)).shuffle)

    def test_setup_turns_shuffle_on_from_schema_4(self):
        for schema, expected in ((None, False), (2, False), (3, False), (4, True), (5, True), (True, False)):
            spec = {"catalog_id": CID} if schema is None else {"catalog_id": CID, "schema": schema}
            ctx = setup_mod.SetupCtx(copy.deepcopy(EMPTY), default_registry(), random.Random(0), 0, spec)
            self.assertEqual(ctx.placer.shuffle, expected, schema)


class ArrangeTests(_House):
    def test_left_to_right_with_gaps_and_no_overlap(self):
        w, d = setup_mod.geometry.footprint_m(default_registry().make(BOTTLE, "x"))
        for seed in range(20):
            config, b = run(self.table_ops(4, [{"op": "arrange", "items": "{b}", "on": "{t0}",
                                                "gap_m": [0.05, 0.2], "var": "row"}]), seed, config=self.house)
            table = config["entities"][b["t0"]]
            sw, sd = table["size_m"][:2]
            row = b["row"]
            self.assertEqual(sorted(row), sorted(b["b"]))
            xs = [config["entities"][e]["spos"][0] for e in row]
            self.assertEqual(xs, sorted(xs))
            self.assertLessEqual(xs[0], 0.2 + 0.001)
            for left, right in zip(row, row[1:]):
                gap = config["entities"][right]["spos"][0] - (config["entities"][left]["spos"][0] + w)
                self.assertTrue(0.05 - 0.002 <= gap <= 0.2 + 0.002, gap)
            for eid in row:
                rec = config["entities"][eid]
                self.assertEqual((rec["location"], rec["relation"]), (b["t0"], "on"))
                x, y = rec["spos"]
                self.assertTrue(0 <= x and x + w <= sw + 1e-9 and 0 <= y and y + d <= sd + 1e-9)
            boxes = [(config["entities"][e]["spos"], (w, d)) for e in row]
            for i, (a, sa) in enumerate(boxes):
                for c, sc in boxes[i + 1:]:
                    self.assertFalse(setup_mod.geometry.rect_overlap(a, sa, c, sc))

    def test_shuffled_ranks_are_uniform_and_ignore_the_list_order(self):
        ranks = {}
        base = self.prepared(4)
        for seed in SEEDS:
            config, b = self.then(base, [{"op": "arrange", "items": "{b}", "on": "{t0}", "var": "row"}], seed)
            role = b["b_1"]                                      # the first member spawned, a fixed id
            ranks[b["row"].index(role)] = ranks.get(b["row"].index(role), 0) + 1
            if seed < 10:
                _, again = self.then(base, [{"op": "shuffle", "var": "rev", "from": "{b}"},
                                            {"op": "arrange", "items": "{rev}", "on": "{t0}", "var": "row"}], seed)
                _, plain = self.then(base, [{"op": "shuffle", "var": "rev", "from": "{b}"},
                                            {"op": "arrange", "items": "{b}", "on": "{t0}", "var": "row"}], seed)
                self.assertEqual(again["row"], plain["row"])     # the list order does not matter
        self.assertEqual(sorted(ranks), [0, 1, 2, 3])
        for rank in range(4):
            self.assertTrue(share_ok(ranks[rank], len(SEEDS), 4), ranks)

    def test_given_order_keeps_the_list(self):
        config, b = run(self.table_ops(3, [{"op": "arrange", "items": "{b}", "on": "{t0}", "order": "given",
                                            "var": "row"}]), 5, config=self.house)
        self.assertEqual(b["row"], b["b"])

    def test_wraps_to_the_next_surface(self):
        n = int(0.5 / (width(BOTTLE) + 0.1)) + 3                 # more than one side table holds with these gaps
        ops = self.table_ops(n, [{"op": "arrange", "items": "{b}", "on": ["{t0}", "{t1}"], "gap_m": [0.1, 0.1],
                                  "var": "row"}], tables=2)
        both = 0
        for seed in range(10):
            config, b = run(ops, seed, config=self.house)
            where = [config["entities"][e]["location"] for e in b["row"]]
            self.assertEqual(where, sorted(where, key=[b["t0"], b["t1"]].index))     # the first surface first
            if set(where) == {b["t0"], b["t1"]}:
                both += 1
            for sid in (b["t0"], b["t1"]):
                xs = [config["entities"][e]["spos"][0] for e in b["row"] if config["entities"][e]["location"] == sid]
                self.assertEqual(xs, sorted(xs))
        self.assertEqual(both, 10)

    def test_no_room_changes_nothing_and_narrower_gaps_are_tried(self):
        fit = int(0.5 / width(BOTTLE) + 1e-9)                    # a side table is 0.5 m wide
        self.assertGreaterEqual(fit, 3)
        config, b = run(self.table_ops(fit + 1, tables=2), 0, config=self.house)
        placer = Placer(config, default_registry(), random.Random(0))
        before = copy.deepcopy(config["entities"])
        self.assertIsNone(placer.arrange(b["b"], [b["t0"]], gap_m=[0.0, 0.0]))     # one too many for a row
        self.assertEqual(config["entities"], before)
        config, b = run(self.table_ops(fit, tables=2), 0, config=self.house)
        layout = Placer(config, default_registry(), random.Random(0)).arrange(b["b"], [b["t0"]], gap_m=[0.5, 0.6])
        self.assertEqual(len(layout), fit)                                       # fits only without gaps
        self.assertEqual({sid for _, sid in layout}, {b["t0"]})
        with self.assertRaises(SpecError):
            run(self.table_ops(fit + 1, [{"op": "arrange", "items": "{b}", "on": "{t0}"}], tables=2), 0,
                config=self.house)

    def test_rejects_bad_arguments(self):
        cases = [{"items": "{b}", "on": "nowhere"}, {"items": "{b}", "on": "{b_1}"}, {"items": ["{t0}"], "on": "{t0}"},
                 {"items": ["{b_1}", "{b_1}"], "on": "{t0}"}, {"items": "{b}", "on": "{t0}", "gap_m": [0.3, 0.1]},
                 {"items": "{b}", "on": "{t0}", "gap_m": "wide"}, {"items": "{b}", "on": "{t0}", "order": "sorted"},
                 {"items": "{b}", "on": "{t0}", "spacing": 1}, {"items": [], "on": "{t0}"},
                 {"items": ["ghost"], "on": "{t0}"}, {"items": "{b_1}", "on": "{t0}"}]
        for case in cases:
            with self.assertRaises(SpecError, msg=str(case)):
                run(self.table_ops(2, [dict(case, op="arrange")]), 0, config=self.house)
        held = self.table_ops(2, [{"op": "robot", "hold": {"right": "{b_1}"}},
                                  {"op": "arrange", "items": "{b}", "on": "{t0}"}])
        with self.assertRaises(SpecError):
            run(held, 0, config=self.house)

    def test_an_arranged_scene_resets_in_every_mode(self):
        ops = self.table_ops(3, [{"op": "arrange", "items": "{b}", "on": "{t0}", "var": "row"}])
        for perception in ("oracle", "realistic"):
            for camera in ("room", "geometric"):
                digests = []
                for _ in range(2):
                    config, b = run(ops, 4, config=self.house, camera=camera, perception=perception)
                    env = HouseEnv(config, seed=4, perception=perception, camera=camera)
                    self.assertEqual([env.world.s["entities"][e]["spos"] for e in b["row"]],
                                     [config["entities"][e]["spos"] for e in b["row"]])
                    digests.append((digest(env.world.s), digest(env.observe())))
                self.assertEqual(digests[0], digests[1], (perception, camera))


class SpawnOrderTests(_House):
    def ranks(self, schema, where, rank_of, before=(), seeds=SEEDS):
        counts, ids = {}, {}
        base = self.prepared(ops=KITCHEN + list(before))
        for seed in seeds:
            ops = [dict({"op": "spawn", "var": "b", "type": BOTTLE, "count": 3}, **where)]
            config, b = self.then(base, ops, seed, schema=schema)
            members = sorted(b["b"])
            self.assertEqual([b["b_1"], b["b_2"], b["b_3"]], b["b"])
            rank = rank_of(config, members, b["b_1"])
            counts[rank] = counts.get(rank, 0) + 1
            ids[b["b_1"]] = ids.get(b["b_1"], 0) + 1
        return counts, ids

    def test_schema_4_roles_land_at_random_ranks_on_a_surface(self):
        def by_x(config, members, role):
            return sorted(members, key=lambda e: config["entities"][e]["spos"]).index(role)
        counts, ids = self.ranks(4, {"on": "{counter}"}, by_x)
        for rank in range(3):
            self.assertTrue(share_ok(counts.get(rank, 0), len(SEEDS), 3), counts)
        self.assertEqual(len(ids), 3)
        counts, ids = self.ranks(3, {"on": "{counter}"}, by_x)
        self.assertEqual(len(counts), 1)                            # schema 3: the first member at one fixed rank
        self.assertEqual(list(ids), [BOTTLE + "_1"])

    def test_schema_4_roles_in_a_container_and_on_the_floor(self):
        def packing(config, members, role):        # sight's in-container packing order (blake2b of the id)
            return sorted(members, key=lambda e: hashlib.blake2b(e.encode("utf-8"), digest_size=8).hexdigest()).index(role)

        def scan(config, members, role):           # free_floor_cell's scan order
            return sorted(members, key=lambda e: (config["entities"][e]["pos"][1], config["entities"][e]["pos"][0])).index(role)
        cabinet = [{"op": "find", "var": "cab", "type": "cabinet", "room": "{k}", "pick": "first"}]
        counts, _ = self.ranks(4, {"in": "{cab}", "shelf": 0}, packing, before=cabinet)
        for rank in range(3):
            self.assertTrue(share_ok(counts.get(rank, 0), len(SEEDS), 3), counts)
        counts, _ = self.ranks(3, {"in": "{cab}", "shelf": 0}, packing, before=cabinet)
        self.assertEqual(len(counts), 1)                            # schema 3: the same member first every time
        counts, _ = self.ranks(4, {"floor": "{k}"}, scan, seeds=range(120))      # floor placement is slower
        for rank in range(3):
            self.assertTrue(share_ok(counts.get(rank, 0), 120, 3), counts)
        counts, _ = self.ranks(3, {"floor": "{k}"}, scan, seeds=range(20))
        self.assertEqual(counts, {0: 20})                           # schema 3: the first member first in scan order

    def test_a_single_spawn_draws_nothing_more(self):
        ops = KITCHEN + [{"op": "spawn", "var": "b", "type": BOTTLE, "on": "{counter}"},
                         {"op": "choose", "var": "x", "from": list(range(100))}]
        for seed in range(5):
            _, four = run(ops, seed, schema=4, config=self.house)
            _, three = run(ops, seed, schema=3, config=self.house)
            self.assertEqual(four, three)


class LookRingTests(_House):
    def test_unfamiliar_twins_land_alike(self):
        """closeout_INT3 (the unfamiliar-twin probe): a novel smartphone (fragile) and a novel kettle with the same look
        landed on different floor cells on seed 1 ([6, 0] vs [5, 0]); every schema. The probe's types where the
        domains are loaded, and a fragile and a sturdy generic thing of that look always."""
        twins = [(t, {}) for t in NOVEL_TWINS if default_registry().known(t)]
        twins += [("generic_item", {"fragile": True}), ("generic_item", {"fragile": False})]
        for seed in (0, 1, 2):
            cells = {}
            for type_name, extra in twins:
                spec = dict(PROBE, setup=[{"op": "room", "var": "lr", "type": "living_room"},
                                          {"op": "spawn", "var": "x", "type": type_name, "floor": "{lr}",
                                           "eid": "probe_twin_1", "props": dict(NOVEL_LOOK, **extra)}])
                config, _, _ = build(spec, seed=seed, perception="realistic")
                cells[(type_name, json.dumps(extra))] = tuple(config["entities"]["probe_twin_1"]["pos"])
            self.assertEqual(len(set(cells.values())), 1, (seed, cells))

    def test_a_moved_unfamiliar_thing_lands_by_its_look(self):
        for seed in (0, 1, 2, 3):
            cells = set()
            for fragile in (False, True):
                ops = [{"op": "room", "var": "lr", "type": "living_room"},
                       {"op": "spawn", "var": "x", "type": "generic_item", "at": "robot_dock_1",
                        "props": dict(NOVEL_LOOK, fragile=fragile)},
                       {"op": "move", "target": "{x}", "to": "{lr}"}]
                config, b = run(ops, seed, schema=None, config=self.house)
                cells.add(tuple(config["entities"][b["x"]]["pos"]))
            self.assertEqual(len(cells), 1, seed)

    def test_schema_4_lookalike_members_land_by_their_look(self):
        reg = default_registry()
        groups = sorted(g for g, types in reg.lookalike_types().items() if len(types) >= 2)
        if groups:
            member, extra = sorted(reg.lookalike_types()[groups[0]])[0], {}
        elif reg.lookalike_groups():
            member, extra = "generic_item", {"looks_like": sorted(reg.lookalike_groups())[0]}
        else:
            self.skipTest("no look-alike groups")
        record = reg.make(member, "x", **extra)
        self.assertEqual(Placer(EMPTY, reg, random.Random(0), shuffle=True).look_ring(record), 1)
        self.assertIsNone(Placer(EMPTY, reg, random.Random(0)).look_ring(record))
        self.assertIsNone(Placer(EMPTY, reg, random.Random(0), shuffle=True).look_ring(reg.make(ITEM, "x")))
        self.assertEqual(Placer(EMPTY, reg, random.Random(0)).look_ring(dict(record, novel=True)), 1)
        for seed in (0, 1, 2, 3):
            cells = set()
            for fragile in (False, True):
                ops = [{"op": "room", "var": "lr", "type": "living_room"},
                       {"op": "spawn", "var": "x", "type": member, "floor": "{lr}",
                        "props": dict(extra, fragile=fragile)}]
                config, b = run(ops, seed, schema=4, config=self.house)
                cells.add(tuple(config["entities"][b["x"]]["pos"]))
            self.assertEqual(len(cells), 1, seed)

    def test_generation_places_nothing_by_look(self):
        placer = Placer(self.house, default_registry(), random.Random(0))
        for eid, record in self.house["entities"].items():
            self.assertIsNone(placer.look_ring(record), eid)       # generated houses hold no unfamiliar things


class AuditFastPathTests(_House):
    @classmethod
    def authored(cls):
        specs = []
        for path in sorted(glob.glob(str(ROOT / "tasks" / "authored" / "*.json"))):
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            specs.extend(data if isinstance(data, list) else data.get("specs", []))
        return specs

    def full_and_audit(self, spec, seed, camera=None, perception=None):
        chosen, _ = choose_variant(expand(spec), seed)
        house = dict(chosen.get("house", {}))
        if "gen" not in house and schema_of(chosen) >= 3:
            house["gen"] = 2
        base = generate_house(seed, **house)
        reg = default_registry()
        full, audit = copy.deepcopy(base), copy.deepcopy(base)
        bf = apply_setup(full, chosen.get("setup", []), reg, seed, chosen, camera=camera, perception=perception)
        ba = apply_setup(audit, chosen.get("setup", []), reg, seed, chosen, camera=camera, perception=perception,
                         audit=True)
        return full, bf, audit, ba

    def test_bindings_and_placements_match_a_full_build(self):
        if enabled_domains() is not None:
            self.skipTest("the authored specs need every domain (HOUSE_SIM_DOMAINS is set)")
        specs = self.authored()[::24]            # every authored spec on seeds 0 and 1: the handoff's scratch run
        self.assertGreaterEqual(len(specs), 10)
        for spec in specs:
            for seed in (0,):
                full, bf, audit, ba = self.full_and_audit(spec, seed)
                self.assertTrue(ba["_audit_build"])
                self.assertNotIn("_audit_build", bf)
                self.assertEqual(audit_diff(full, bf, audit, ba), [], (spec.get("catalog_id"), seed))

    def test_both_camera_modes(self):
        spec = {"catalog_id": CID, "schema": 4, "camera": "geometric",
                "setup": KITCHEN + [{"op": "spawn", "var": "b", "type": BOTTLE, "on": "{counter}", "count": 3},
                                    {"op": "arrange", "items": "{b}", "on": "{counter}"},
                                    {"op": "robot", "room": "{k}"}, {"op": "camera", "face": "{counter}", "tilt": -20},
                                    {"op": "time", "var": "t", "min": 0, "max": 600, "never_p": 0.2}]}
        for camera in ("room", "geometric"):
            for perception in ("oracle", "realistic"):
                full, bf, audit, ba = self.full_and_audit(spec, 2, camera=camera, perception=perception)
                self.assertEqual(audit_diff(full, bf, audit, ba), [], (camera, perception))
                self.assertEqual("facing_deg" in full["robot"], camera == "geometric")

    def test_the_physics_lint_and_check_physics_are_skipped(self):
        impossible = KITCHEN + [{"op": "spawn", "var": "m", "type": ITEM, "on": "{counter}"},
                                {"op": "spawn", "var": "g", "type": BOTTLE, "on": "{counter}"},
                                {"op": "set", "target": "{m}", "props": {"location": "{g}", "relation": "on"}}]
        with self.assertRaises(SpecError):
            run(impossible, 0, schema=4, config=self.house)
        _, b = run(impossible, 0, schema=4, config=self.house, audit=True)
        self.assertNotIn("_physics_warnings", b)
        _, b = run(impossible, 0, schema=2, config=self.house)
        self.assertTrue(b["_physics_warnings"])
        checked = impossible + [{"op": "check_physics", "strict": True}]
        with self.assertRaises(SpecError):
            run(checked, 0, schema=2, config=self.house)
        run(checked, 0, schema=2, config=self.house, audit=True)

    def test_prior_knowledge_that_draws_still_runs(self):
        for room, skipped in (("kitchen", False), ("{k}", True)):
            ops = KITCHEN + [{"op": "prior_knowledge", "map": [room]},
                             {"op": "choose", "var": "x", "from": list(range(1000))}]
            for seed in range(4):
                full, bf = run(ops, seed, config=self.house)
                audit, ba = run(ops, seed, config=self.house, audit=True)
                self.assertEqual(bf["x"], ba["x"])
                self.assertEqual("prior_knowledge" in audit, not skipped)
                self.assertEqual(audit_diff(full, bf, audit, ba), [])

    def test_audit_diff_reports_a_difference(self):
        ops = KITCHEN + [{"op": "spawn", "var": "b", "type": BOTTLE, "on": "{counter}", "count": 2}]
        full, bf = run(ops, 0, config=self.house)
        audit, ba = run(ops, 0, config=self.house, audit=True)
        audit["entities"][ba["b_1"]]["spos"] = [9.0, 9.0]
        ba["extra"] = 1
        problems = audit_diff(full, bf, audit, ba)
        self.assertTrue(any("bindings.extra" in p for p in problems), problems)
        self.assertTrue(any(".spos" in p and ba["b_1"] in p for p in problems), problems)
        self.assertEqual(len(audit_diff(full, bf, audit, ba, limit=1)), 1)


class DiskCacheTests(unittest.TestCase):
    ARGS = {"dwelling": "apartment", "residents": "none", "pets": "none"}
    SEED = 5

    def setUp(self):
        load_domains()
        self.tmp = tempfile.mkdtemp(prefix="wp9_cache_")
        disk_cache._VERIFIED.clear()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        disk_cache._VERIFIED.clear()

    def test_a_cached_house_is_the_generated_house(self):
        before = dict(disk_cache.STATS)
        first = disk_cache.house(self.SEED, self.ARGS, root=self.tmp)
        path = disk_cache.entry_path(self.SEED, self.ARGS, root=self.tmp)
        self.assertTrue(path.is_file())
        self.assertTrue(path.name.startswith("{}_".format(self.SEED)))
        self.assertEqual(path.parent.name, disk_cache.fingerprint()[:16])
        fresh = generate_house(self.SEED, **self.ARGS)
        self.assertTrue(disk_cache._exact(first, fresh))
        second = disk_cache.house(self.SEED, self.ARGS, root=self.tmp)
        self.assertTrue(disk_cache._exact(second, fresh))
        self.assertEqual(disk_cache.STATS["misses"] - before["misses"], 1)
        self.assertEqual(disk_cache.STATS["hits"] - before["hits"], 1)
        self.assertEqual(disk_cache.STATS["verified"] - before["verified"], 1)
        second["entities"].clear()
        third = disk_cache.house(self.SEED, self.ARGS, root=self.tmp)
        self.assertTrue(disk_cache._exact(third, fresh))                # always a fresh object
        self.assertEqual(disk_cache.STATS["verified"] - before["verified"], 1)       # once per process
        self.assertTrue(disk_cache.check(self.SEED, self.ARGS, root=self.tmp))
        self.assertIsNone(disk_cache.check(self.SEED + 1, self.ARGS, root=self.tmp))
        with gzip.open(str(path), "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertEqual(payload["fingerprint"], disk_cache.fingerprint())
        self.assertEqual(payload["key"], disk_cache.house_key(self.SEED, self.ARGS))
        self.assertEqual(list(payload["config"]), list(fresh))         # key order kept
        self.assertEqual(sorted(os.listdir(str(path.parent))), [path.name])  # no temporary file left

    def test_environments_built_from_it_are_the_same(self):
        cached = disk_cache.house(self.SEED, self.ARGS, root=self.tmp)
        fresh = generate_house(self.SEED, **self.ARGS)
        for perception in ("oracle", "realistic"):
            for camera in ("room", "geometric"):
                a = HouseEnv(copy.deepcopy(cached), seed=self.SEED, perception=perception, camera=camera)
                b = HouseEnv(copy.deepcopy(fresh), seed=self.SEED, perception=perception, camera=camera)
                self.assertEqual(digest(a.world.s), digest(b.world.s), (perception, camera))
                self.assertEqual(digest(a.observe()), digest(b.observe()), (perception, camera))
                a.step("wait", seconds=60)
                b.step("wait", seconds=60)
                self.assertEqual(digest(a.world.s), digest(b.world.s), (perception, camera))

    def test_a_planted_house_is_a_harness_error(self):
        disk_cache.house(self.SEED, self.ARGS, root=self.tmp)
        path = disk_cache.entry_path(self.SEED, self.ARGS, root=self.tmp)
        with gzip.open(str(path), "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        payload["config"]["rooms"] = {}
        with open(str(path), "wb") as handle:
            handle.write(gzip.compress(json.dumps(payload).encode("utf-8")))
        disk_cache._VERIFIED.clear()
        with self.assertRaises(disk_cache.CacheError):
            disk_cache.house(self.SEED, self.ARGS, root=self.tmp)
        self.assertEqual(disk_cache.house(self.SEED, self.ARGS, root=self.tmp, verify="never")["rooms"], {})
        self.assertFalse(disk_cache.check(self.SEED, self.ARGS, root=self.tmp))
        with self.assertRaises(ValueError):
            disk_cache.house(self.SEED, self.ARGS, root=self.tmp, verify="sometimes")

    def test_a_corrupt_or_foreign_file_is_rebuilt(self):
        path = disk_cache.entry_path(self.SEED, self.ARGS, root=self.tmp)
        path.parent.mkdir(parents=True)
        fresh = generate_house(self.SEED, **self.ARGS)
        for blob in (b"not gzip", gzip.compress(b"{not json"),
                     gzip.compress(json.dumps({"format": disk_cache.FORMAT, "fingerprint": "other",
                                               "key": disk_cache.house_key(self.SEED, self.ARGS),
                                               "config": {}}).encode("utf-8"))):
            with open(str(path), "wb") as handle:
                handle.write(blob)
            before = disk_cache.STATS["corrupt"]
            self.assertTrue(disk_cache._exact(disk_cache.house(self.SEED, self.ARGS, root=self.tmp), fresh))
            self.assertEqual(disk_cache.STATS["corrupt"], before + 1)
            with gzip.open(str(path), "rt", encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["fingerprint"], disk_cache.fingerprint())

    def test_keys_follow_generate_house(self):
        key = disk_cache.house_key
        self.assertEqual(key(3, {"features": ("b", "a")}), key(3, {"features": {"a", "b"}}))
        self.assertEqual(key(3, {"features": ("b", "a")}), key(3, {"features": ["a", "b"]}))
        self.assertNotEqual(key(3, {}), key(4, {}))
        self.assertNotEqual(key(3, {}), key(3, {"gen": 2}))
        for bad in ({"registry": default_registry()}, {"report": []}, {"trace": {}}, {"when": object()}):
            with self.assertRaises(TypeError):
                key(3, bad)
        for seed in ("3", 3.0, True):
            with self.assertRaises(TypeError):
                key(seed, {})

    def test_the_fingerprint_covers_sources_and_data_only(self):
        package = Path(self.tmp) / "pkg"
        for rel, text in (("a.py", "x = 1\n"), ("data/t.json", "{}"), ("__pycache__/a.cpython.pyc", "z"),
                          ("eval/e.py", "e = 1\n"), ("notes.md", "n"), ("sub/__pycache__/b.py", "b")):
            (package / rel).parent.mkdir(parents=True, exist_ok=True)
            (package / rel).write_text(text, encoding="utf-8")
        self.assertEqual([rel for rel, _ in disk_cache.source_files(package)], ["a.py", "data/t.json"])
        base = disk_cache.fingerprint(refresh=True, package=package)
        for rel, text, changes in (("eval/e.py", "e = 2\n", False), ("notes.md", "m", False),
                                   ("__pycache__/a.cpython.pyc", "y", False), ("a.py", "x = 2\n", True),
                                   ("data/t.json", "[]", True)):
            (package / rel).write_text(text, encoding="utf-8")
            now = disk_cache.fingerprint(refresh=True, package=package)
            self.assertEqual(now != base, changes, rel)
            base = now
        self.assertEqual(len(disk_cache.fingerprint()), 64)

    def test_off_generates_every_time(self):
        saved = os.environ.get(disk_cache.ENV_DIR)
        try:
            os.environ[disk_cache.ENV_DIR] = "off"
            self.assertIsNone(disk_cache.cache_root())
            self.assertIsNone(disk_cache.entry_path(self.SEED, self.ARGS))
            config = disk_cache.house(self.SEED, self.ARGS)
            self.assertTrue(disk_cache._exact(config, generate_house(self.SEED, **self.ARGS)))
            os.environ[disk_cache.ENV_DIR] = self.tmp
            self.assertEqual(disk_cache.cache_root(), Path(self.tmp))
            del os.environ[disk_cache.ENV_DIR]
            self.assertEqual(disk_cache.cache_root(), disk_cache.DEFAULT_DIR)
            self.assertEqual(disk_cache.DEFAULT_DIR, ROOT / "reports" / "cache" / "houses")
        finally:
            if saved is None:
                os.environ.pop(disk_cache.ENV_DIR, None)
            else:
                os.environ[disk_cache.ENV_DIR] = saved

    def test_prune_keeps_the_current_fingerprint(self):
        disk_cache.house(self.SEED, self.ARGS, root=self.tmp)
        root = Path(self.tmp)
        for name in ("0" * 16, "1" * 16):
            (root / name).mkdir()
            (root / name / "5_x.json.gz").write_bytes(b"old")
        (root / ("2" * 16)).mkdir()
        (root / ("2" * 16) / "keep.txt").write_text("not ours", encoding="utf-8")
        removed = disk_cache.prune(keep=1, root=self.tmp)
        self.assertEqual(sorted(removed), ["0" * 16, "1" * 16])
        self.assertTrue((root / disk_cache.fingerprint()[:16]).is_dir())
        self.assertTrue((root / ("2" * 16) / "keep.txt").is_file())
        self.assertEqual(disk_cache.prune(root=str(root / "missing")), [])


class GitignoreTests(unittest.TestCase):
    def ignored(self, paths):
        """The paths git ignores (one check-ignore call)."""
        result = subprocess.run(["git", "check-ignore", "--no-index", "--stdin"], cwd=str(ROOT),
                                input="\n".join(paths).encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode not in (0, 1):
            self.skipTest("git check-ignore unavailable: " + result.stderr.decode("utf-8", "replace")[:200])
        return set(result.stdout.decode("utf-8").split())

    def test_the_cache_is_ignored_and_source_folders_stay_visible(self):
        text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("reports/cache/", text.splitlines())
        if shutil.which("git") is None or not (ROOT / ".git").exists():
            self.skipTest("not a git work tree")
        hidden = ["reports/cache/houses/0123456789abcdef/5_x.json.gz", "reports/cache/runs/x.json", "build/lib/x.py",
                  "dist/house_ai.whl", "results/run/x.json", "house_sim/__pycache__/x.pyc"]
        # closeout_REV finding 1: a build/ or dist/ folder inside the source trees is code the gate must see
        visible = ["house_sim/systems/build/__init__.py", "house_sim/systems/dist/__init__.py", "tests/build/test_x.py",
                   "tools/dist/x.py", "reports/verification.jsonl", "reports/coverage_summary.json"]
        ignored = self.ignored(hidden + visible)
        self.assertEqual(sorted(p for p in hidden if p not in ignored), [])
        self.assertEqual(sorted(p for p in visible if p in ignored), [])


if __name__ == "__main__":
    unittest.main()
