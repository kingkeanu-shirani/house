"""The schema-4 verification core (docs/design/VERIFY_HARDENING.md, WP5: tasks/verify.py, runner.py, seeds.py,
private.py, checklog.py, escapes.py, strength.py, runcache.py, cli.py).

* runner: the schema-4 build (labelled predicates unwrapped and recorded, the hardening block with bindings
  substituted, bound answers, the timing key), the trace with provenance, frozen save values, fixed waits, the
  snapshot hook, per-plan exception capture, a bad skill argument as a spec error; a legacy build is unchanged;
* seeds: the coverage seeds (variants and answer values), the public held-out seeds, the feature seeds;
* private: no key -> nothing; a throwaway key -> deterministic private seeds in the hidden range, never a test
  candidate; the store is never written inside the repository or into a missing private folder;
* the check log, the escape prices and G9, the strength score, the gates and the verdicts, the expected families;
* expect matching (14.2), the 14.3 rules, the mistake-fix alignment (14.4), the budget formula (17), the run cache;
* end to end on a small schema-4 task in the own (oracle) and evaluation (realistic, both cameras) modes: G1 and G2
  hold, every wrong plan is verified and attributed, the reference containment, the idle B1 check and a crashing
  wrong plan fail as they must, the verdict cannot be HARDENED while the battery has not run, the record is
  deterministic, and the CLI prints the block of 21.2.

The schema-4 keys reach runner.build through spec.expand; until WP8's spec.py accepts them the tests extend
spec.ALLOWED for their duration (schema4_keys()).
"""
import copy
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from unittest import mock

from house_sim.kernel.errors import SpecError
from house_sim.kernel.plugins import SKILLS, load_domains
from house_sim.tasks import checklog, escapes, private, runcache, seeds, strength, verify
from house_sim.tasks import spec as spec_mod
from house_sim.tasks.runner import Runner, build, capture_run, make_env, run_plan

SCHEMA4_KEYS = {"answers", "envelope", "absent_ok", "protections", "protect_map", "instruction_roles", "timing",
                "settle_s", "lookahead_s", "process", "fungible", "informative", "free_ok", "battery_exempt",
                "mustpass_exempt", "budget_reason", "clauses", "branch", "track_open", "hold_limits"}
THROWAWAY_KEY = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
OTHER_KEY = "ffeeddccbbaa99887766554433221100ffeeddccbbaa99887766554433221100"
# a logged agent review session (10.7: review_sessions.jsonl rows carry the id, model, prompt and transcript digests)
LOGGED = {"rev-1": {"session_id": "rev-1", "model": "m", "prompt_digest": "p" * 16, "transcript_digest": "t" * 16}}


@contextmanager
def schema4_keys():
    """spec.ALLOWED with the schema-4 keys of VERIFY_HARDENING 20.1 (until WP8's spec.py accepts them)."""
    missing = SCHEMA4_KEYS - set(spec_mod.ALLOWED)
    if not missing:
        yield
        return
    with mock.patch.object(spec_mod, "ALLOWED", set(spec_mod.ALLOWED) | missing):
        yield


SETUP = [{"op": "room", "var": "living", "type": "living_room", "pick": "first"},
         {"op": "lights", "room": "{living}", "on": True},
         {"op": "spawn", "type": "bookshelf", "furniture": "{living}", "var": "shelf"},
         {"op": "spawn", "type": "coffee_table", "furniture": "{living}", "var": "table"},
         {"op": "spawn", "type": "book", "on": "{table}", "var": "book"},
         {"op": "robot", "room": "{living}"},
         {"op": "prior_knowledge", "map": "all"}]
TO_TABLE = {"name": "puts the book back on the table",
            "plan": [{"skill": "fetch", "obj": "{book}"}, {"skill": "put", "obj": "{book}", "dest": "{table}"}],
            "expect": ["goal:book_shelved"], "mistake": [1]}
HOLDS = {"name": "holds the book and waits", "plan": [{"skill": "fetch", "obj": "{book}"},
                                                      {"do": "wait", "seconds": 30}],
         "expect": ["goal:book_shelved"], "mistake": [1]}


def book_spec(**changes):
    """A small schema-4 task: shelve the book (labelled success, an envelope that lets the book move to the shelf)."""
    out = {"catalog_id": "T-wp5-book-01", "title": "shelve the book", "schema": 4, "risk_level": 0,
           "house": {"residents": "minimal", "pets": "none"}, "setup": copy.deepcopy(SETUP),
           "instruction": "Put the book on the bookshelf.",
           "phrasings": ["Put the book on the bookshelf.", "Shelve the book, please.",
                         "The book goes on the bookshelf."],
           "success": [{"label": "book_shelved", "pred": {"path": "entities.{book}.location", "op": "eq",
                                                           "value": "{shelf}"}}],
           "envelope": {"work_rooms": ["{living}"], "move": {"{book}": {"to": ["{shelf}"]}}},
           "budget": {"steps": 40, "time_s": 240},
           "reference": [{"skill": "fetch", "obj": "{book}"}, {"skill": "put", "obj": "{book}", "dest": "{shelf}"}],
           "wrong": [copy.deepcopy(TO_TABLE), copy.deepcopy(HOLDS)]}
    out.update(changes)
    return out


def legacy_spec():
    out = book_spec()
    for key in ("schema", "envelope"):
        out.pop(key)
    out["success"] = [item["pred"] for item in out["success"]]
    for item in out["wrong"]:
        item.pop("expect")
        item.pop("mistake")
    return out


class RunnerBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_domains()

    def test_a_schema_4_build_unwraps_labels_and_adds_the_hardening_block(self):
        with schema4_keys():
            config, task, oracle = build(book_spec(settle_s=45, absent_ok=[], process=[]), 0)
        self.assertEqual(task["success"], [{"path": "entities.{}.location".format(oracle["bindings"]["book"]),
                                            "op": "eq", "value": oracle["bindings"]["shelf"]}])
        self.assertEqual(task["labels"]["success"], ["book_shelved"])
        self.assertEqual(task["labels"]["failure"], [None, None, None])     # the strict blocks, unlabelled
        block = task["hardening"]
        self.assertEqual(block["settle_s"], 45)
        self.assertEqual(block["lookahead_s"], 0.0)
        self.assertTrue(block["track_open"])
        self.assertEqual(block["envelope"]["move"], {oracle["bindings"]["book"]: {"to": [oracle["bindings"]["shelf"]]}})
        self.assertEqual(block["labels"], task["labels"])
        self.assertEqual(task["schema"], 4)

    def test_a_list_binding_as_an_envelope_key_becomes_its_json_text(self):
        from house_sim.tasks.runner import _substitute_keyed
        block = {"move": {"{books}": {"to": ["{shelf}"]}, "{one}": {"to": ["baseline"]}}, "consume": ["{books}"]}
        out = _substitute_keyed(block, {"books": ["book_1", "book_2"], "one": "mug_1", "shelf": "shelf_1"})
        self.assertEqual(out["move"], {json.dumps(["book_1", "book_2"]): {"to": ["shelf_1"]},
                                       "mug_1": {"to": ["baseline"]}})
        self.assertEqual(out["consume"], [["book_1", "book_2"]])

    def test_a_repeated_or_malformed_label_is_a_spec_error(self):
        twice = book_spec(success=[{"label": "x", "pred": {"path": "time_s", "op": "gte", "value": 0}},
                                   {"label": "x", "pred": {"path": "time_s", "op": "gte", "value": 1}}])
        colon = book_spec(success=[{"label": "a:b", "pred": {"path": "time_s", "op": "gte", "value": 0}}])
        with schema4_keys():
            with self.assertRaisesRegex(SpecError, "used twice"):
                build(twice, 0)
            with self.assertRaisesRegex(SpecError, "label"):
                build(colon, 0)

    def test_a_bad_envelope_fails_the_build(self):
        with schema4_keys():
            with self.assertRaisesRegex(SpecError, "hardening"):
                build(book_spec(envelope={"frobnicate": []}), 0)

    def test_answers_are_bound_and_the_branch_is_the_variant(self):
        spec = book_spec(answers={"where": {"bind": "shelf", "kind": "place", "group": ["{shelf}", "{table}"]},
                                  "branch": {"bind": "_branch", "kind": "branch"}},
                         variants=[{"branch": "act"}, {"branch": "dont"}])
        with schema4_keys():
            _, task, oracle = build(spec, 0)
        answers = task["hardening"]["answers"]
        self.assertEqual(answers["where"]["value"], oracle["bindings"]["shelf"])
        self.assertEqual(answers["where"]["group"], [oracle["bindings"]["shelf"], oracle["bindings"]["table"]])
        self.assertIn(answers["branch"]["value"], ("act", "dont"))
        self.assertEqual(answers["branch"]["value"], oracle["branch"])

    def test_the_timing_key_passes_through_with_bindings(self):
        item = {"label": "late_shelf", "trigger": {"path": "time_s", "op": "gte", "value": 0},
                "act": {"path": "entities.{book}.location", "op": "eq", "value": "{shelf}"}, "within_s": 600}
        with schema4_keys():
            _, task, oracle = build(book_spec(timing=[item]), 0)
        self.assertEqual(task["timing"][0]["act"]["path"], "entities.{}.location".format(oracle["bindings"]["book"]))

    def test_a_legacy_build_has_no_hardening_and_is_unchanged(self):
        config, task, oracle = build(legacy_spec(), 0)
        self.assertNotIn("hardening", task)
        self.assertNotIn("labels", task)
        self.assertNotIn("answers", oracle)
        again = build(legacy_spec(), 0)
        self.assertEqual(verify.digest(task), verify.digest(again[1]))

    def test_an_as_if_build_hardens_a_legacy_spec(self):
        _, task, _ = build(legacy_spec(), 0, hardening={"settle_s": 30})
        self.assertEqual(task["hardening"]["settle_s"], 30)
        self.assertEqual(task["labels"]["success"], [None])


class RunnerTraceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_domains()
        with schema4_keys():
            cls.config, cls.task, cls.oracle = build(book_spec(), 0)

    def env(self, perception=None, camera=None):
        return make_env(self.config, self.task, 0, perception=perception, camera=camera)

    def test_the_trace_records_every_command_with_provenance_and_tops(self):
        plan = [{"skill": "fetch", "obj": "{book}"}, {"do": "wait", "seconds": 5},
                {"save": "seen", "from": "room_id"}, {"skill": "put", "obj": "{book}", "dest": "{shelf}"}]
        runner = run_plan(self.env(), plan, self.oracle["bindings"])
        self.assertEqual([t["k"] for t in runner.tops], [0, 1, 2, 3])
        self.assertEqual([t["kind"] for t in runner.tops], ["skill", "do", "save", "skill"])
        fetch = [e for e in runner.trace if e["top"] == 0]
        self.assertTrue(fetch and all(e["skill"] == "fetch" for e in fetch))
        self.assertIn("binding:book", {v for e in fetch for v in e["prov"].values()})
        wait = [e for e in runner.trace if e["top"] == 1][0]
        self.assertEqual(wait["action"], "wait")
        self.assertEqual(wait["prov"], {"seconds": "literal"})
        self.assertAlmostEqual(wait["wait"]["waited_s"], wait["t1"] - wait["t0"], places=3)
        self.assertEqual(runner.saves[0]["name"], "seen")
        self.assertFalse(runner.saves[0]["frozen"])
        self.assertTrue(all(e["ok"] for e in runner.trace))
        for i, entry in enumerate(runner.trace):
            self.assertEqual(entry["i"], i)

    def test_a_saved_value_is_provenance_and_can_be_frozen(self):
        plan = [{"save": "spot", "from": "room_id"}, {"do": "wait", "seconds": "{spot_wait}"}]
        bindings = dict(self.oracle["bindings"], spot_wait=3)
        runner = run_plan(self.env(), [plan[0]], bindings)
        observed = runner.saves[0]["value"]
        frozen = run_plan(self.env(), [plan[0]], bindings, frozen={"spot": "kitchen_9"})
        self.assertEqual(frozen.saves[0]["value"], "kitchen_9")
        self.assertTrue(frozen.saves[0]["frozen"])
        self.assertEqual(frozen.bindings["spot"], "kitchen_9")
        self.assertNotEqual(observed, "kitchen_9")
        second = run_plan(self.env(), [{"save": "spot", "from": "room_id"},
                                       {"do": "wait", "seconds": 2, "note": "{spot}"}], bindings)
        self.assertEqual(second.trace[-1]["prov"], {"seconds": "literal"})

    def test_a_conditioned_wait_is_recorded_and_can_be_fixed(self):
        plan = [{"wait_for": ["doorbell"], "max_s": 120}]
        runner = run_plan(self.env(), plan, self.oracle["bindings"])
        self.assertEqual(runner.waits[0]["kind"], "wait_for")
        self.assertEqual(runner.waits[0]["ordinal"], 0)
        fixed = run_plan(self.env(), plan, self.oracle["bindings"], fixed_waits={0: 40})
        self.assertTrue(fixed.waits[0]["fixed"])
        self.assertAlmostEqual(fixed.waits[0]["waited_s"], 40.0, delta=1.0)
        self.assertNotIn("interrupt_on", fixed.trace[0]["args"])

    def test_the_snapshot_hook_runs_before_each_top_level_step(self):
        seen = []
        run_plan(self.env(), self.oracle["reference"], self.oracle["bindings"],
                 snapshot=lambda k, r: seen.append((k, r.state["steps"])))
        self.assertEqual([k for k, _ in seen], [0, 1])
        self.assertEqual(seen[0][1], 0)
        self.assertGreater(seen[1][1], 0)

    def test_capture_run_hands_back_the_exception_and_the_steps_reached(self):
        plan = [{"skill": "fetch", "obj": "{book}"}, {"save": "x", "from": "nothing.here"},
                {"do": "wait", "seconds": 5}]
        runner, reached, exc = capture_run(self.env(), plan, self.oracle["bindings"])
        self.assertEqual(reached, 1)
        self.assertIsNotNone(exc)
        self.assertEqual(runner.tops[1]["error"][:9], "PlanError")

    def test_a_skill_argument_the_skill_does_not_take_is_a_spec_error(self):
        with self.assertRaisesRegex(SpecError, "skill put"):
            run_plan(self.env(), [{"skill": "put", "obj": "{book}", "to": "{shelf}"}], self.oracle["bindings"])

    def test_the_trace_is_the_same_in_both_perception_and_camera_modes(self):
        for perception, camera in (("oracle", "room"), ("realistic", "room"), ("realistic", "geometric")):
            runner = run_plan(self.env(perception, camera), self.oracle["reference"], self.oracle["bindings"])
            with self.subTest(perception=perception, camera=camera):
                ids = [e["target"] for e in runner.trace if e["target"]]
                self.assertIn(self.oracle["bindings"]["book"], ids)     # true ids, never handles
                self.assertTrue(all(e["ok"] for e in runner.trace))
                again = run_plan(self.env(perception, camera), self.oracle["reference"], self.oracle["bindings"])
                self.assertEqual(verify._trace_digest(runner), verify._trace_digest(again))


class SeedTests(unittest.TestCase):
    def test_a_plain_spec_keeps_the_dev_seeds(self):
        self.assertEqual(seeds.verification_seeds({"catalog_id": "X-a-01"}), list(seeds.DEV_SEEDS))

    def test_variants_get_at_least_two_seeds_each(self):
        for n in (2, 3, 4, 5):
            spec = {"catalog_id": "X-variants-01", "variants": [{} for _ in range(n)]}
            cover = seeds.coverage(spec)
            with self.subTest(n=n):
                self.assertTrue(cover["covered"])
                self.assertTrue(all(c >= 2 for c in cover["variants"].values()))
                self.assertEqual(len(cover["variants"]), n)
                self.assertEqual(cover["seeds"][:5], list(seeds.DEV_SEEDS))
                self.assertLessEqual(len(cover["seeds"]), seeds.MAX_VERIFICATION_SEEDS)

    def test_answer_values_are_covered_and_a_hopeless_spec_is_not(self):
        spec = {"catalog_id": "X-answers-01", "answers": {"pick": {"bind": "pick", "kind": "entity"}}}
        values = {0: "a", 1: "a", 2: "b", 41: "a", 97: "c"}
        with schema4_keys():
            cover = seeds.coverage(spec, values=lambda s: {"pick": values.get(s, "b" if s % 2 else "c")})
            lonely = seeds.coverage(spec, values=lambda s: {"pick": "v{}".format(s)})
        self.assertTrue(cover["covered"])
        self.assertTrue(all(v >= 2 for v in cover["values"]["pick"].values()))
        self.assertFalse(lonely["covered"])
        self.assertEqual(lonely["seeds"], list(seeds.DEV_SEEDS))        # never adds a seed that raises nothing

    def test_public_heldout_seeds_are_deterministic_in_range_and_cover_variants(self):
        spec = {"catalog_id": "X-held-01", "variants": [{}, {}, {}]}
        a = seeds.heldout_public(spec, spec_hash="h1")
        self.assertEqual(a, seeds.heldout_public(spec, spec_hash="h1"))
        self.assertNotEqual(a, seeds.heldout_public(spec, spec_hash="h2"))
        self.assertTrue(10 <= len(a) <= 16)
        self.assertEqual(len(set(a)), len(a))
        self.assertTrue(all(200000 <= s < 1000000 for s in a))
        per = {}
        for s in a:
            v = spec_mod.variant_index("X-held-01", s, 3)
            per[v] = per.get(v, 0) + 1
        self.assertTrue(all(per.get(v, 0) >= 2 for v in range(3)))
        self.assertEqual(seeds.heldout_seed("X-held-01", "h1", 0), a[0])

    def test_feature_seeds_come_from_the_index(self):
        index = {"generations": {"1": {"winder_stairs": [200007, 200011], "imperial": [200003]},
                                 "2": {"winder_stairs": [200100]}}}
        spec = {"catalog_id": "X-feat-01"}
        self.assertEqual(seeds.feature_seeds(spec, ["winder_stairs", "imperial"], index=index), [200007, 200003])
        self.assertEqual(seeds.feature_seeds(spec, ["winder_stairs"], exclude=[200007], index=index), [200011])
        self.assertEqual(seeds.feature_seeds(dict(spec, schema=3), ["winder_stairs"], index=index), [200100])
        self.assertEqual(seeds.feature_seeds(spec, [], index=index), [])
        self.assertIn("winder_stairs", seeds.wanted_features(spec, floors=[0, 1]))
        self.assertIn("imperial", seeds.wanted_features(spec, actions=["measure"]))

    def test_the_committed_feature_index_has_every_feature(self):
        index = seeds.load_features()
        self.assertIsNotNone(index, "catalog/analysis/seed_features.json (tools/seed_features.py)")
        for gen in ("1", "2"):
            for feature in ("winder_stairs", "multi_floor", "no_garage", "apartment", "imperial", "pets"):
                self.assertTrue(index["generations"][gen].get(feature), (gen, feature))


class PrivateTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"HOUSE_AI_SPLIT_KEY": ""})
        self.env.start()
        self.home = tempfile.mkdtemp(prefix="wp5_home_")
        self.private_dir = mock.patch("house_sim.eval.splits.private_dir", return_value=Path(self.home) / ".hai")
        self.private_dir.start()

    def tearDown(self):
        self.private_dir.stop()
        self.env.stop()
        shutil.rmtree(self.home, ignore_errors=True)

    def test_without_a_key_nothing_private_runs(self):
        self.assertIsNone(private.salt())
        self.assertFalse(private.available())
        self.assertEqual(private.heldout({"catalog_id": "X-p-01"}, spec_hash="h"), [])
        self.assertEqual(private.pool(), [])
        self.assertEqual(private.NOT_RUN, "not run (no split key)")
        self.assertFalse((Path(self.home) / ".hai").exists())

    def test_a_key_gives_deterministic_hidden_seeds_that_skip_test_candidates(self):
        from house_sim.eval import splits
        spec = {"catalog_id": "X-p-01", "variants": [{}, {}]}
        a = private.heldout(spec, key=THROWAWAY_KEY, spec_hash="h")
        self.assertEqual(a, private.heldout(spec, key=THROWAWAY_KEY, spec_hash="h"))
        self.assertNotEqual(a, private.heldout(spec, key=OTHER_KEY, spec_hash="h"))
        self.assertTrue(all(splits.PRIVATE_BASE <= s < splits.PRIVATE_END for s in a))
        self.assertGreaterEqual(len(a), 10)
        tests = set(splits.test_candidates("X-p-01", private.TEST_CANDIDATES_SKIPPED, THROWAWAY_KEY))
        self.assertFalse(tests & set(a))
        self.assertFalse(set(a) & set(seeds.heldout_public(spec, spec_hash="h")))
        salt = private.salt(THROWAWAY_KEY)
        self.assertEqual(len(salt), 32)
        self.assertEqual(private.rng(salt, "X-p-01", 5, "P3").random(), private.rng(salt, "X-p-01", 5, "P3").random())
        self.assertNotEqual(private.rng(salt, "X-p-01", 5, "P3").random(),
                            private.rng(salt, "X-p-01", 5, "P4").random())
        pool = private.pool(THROWAWAY_KEY, n=50)
        self.assertEqual(len(set(pool)), 50)
        sample = private.sample_pool(spec, THROWAWAY_KEY, n=10)
        self.assertEqual(len(sample), 10)

    def test_the_store_is_never_written_inside_the_repository_or_into_a_missing_folder(self):
        with self.assertRaises(ValueError):
            private.write_details("X-p-01", [{"seed": 1}], root=Path(__file__).resolve().parents[1] / "reports")
        with self.assertRaises(FileNotFoundError):
            private.write_details("X-p-01", [{"seed": 1}])
        self.assertFalse((Path(self.home) / ".hai").exists())
        root = Path(self.home) / "store"
        path = private.write_details("X-p-01", [{"seed": 3, "tau": 60}], root=root)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8").splitlines()[0]), {"seed": 3, "tau": 60})
        with self.assertRaises(ValueError):
            private.store_path("../escape", root=root)
        self.assertEqual(private.details_digest([{"a": 1}]), private.details_digest([{"a": 1}]))
        summary = private.public_summary([{"family": "P3", "outcome": "fails"}, {"family": "P3", "outcome": "passes",
                                                                                  "label_class": "conservation"}])
        self.assertEqual(summary, {"P3": "1/2 met (conservation)"})


class CheckLogTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="wp5_log_")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def record(self, verdict="FAIL", spec_hash="h1", failing=2):
        gates = {"G1": True, "G2": failing < 2, "G3": False}
        return {"catalog_id": "X-log-01", "spec_hash": spec_hash, "level": "standard", "verdict": verdict,
                "gates": gates, "fingerprint": "f", "cpu_s": 12.5, "heldout": {"failed": verdict == "FAIL"}}

    def test_append_writes_one_line_per_check_with_the_session(self):
        with mock.patch.dict(os.environ, {"HOUSE_AI_SESSION_ID": "sess-a"}):
            path, entry = checklog.append(self.record(), root=self.root, domain="cleaning")
        self.assertEqual(path.name, "cleaning.jsonl")
        self.assertEqual(entry["session_id"], "sess-a")
        rows = checklog.entries("X-log-01", root=self.root)
        self.assertEqual(len(rows), 1)
        for key in checklog.FIELDS:
            self.assertIn(key, rows[0])
        self.assertEqual(checklog.sessions("X-log-01", root=self.root), ["sess-a"])
        with mock.patch.dict(os.environ, {"HOUSE_AI_SESSION_ID": ""}):
            self.assertEqual(checklog.session_id(), "unknown")

    def test_heldout_rolls_and_the_stop_loss(self):
        for i in range(3):
            checklog.append(self.record(spec_hash="h{}".format(i)), root=self.root, domain="d")
        self.assertEqual(checklog.heldout_rolls("X-log-01", root=self.root), 3)
        self.assertFalse(checklog.stop_loss("X-log-01", root=self.root)["parked"])
        for i in range(3, 5):
            checklog.append(self.record(spec_hash="h{}".format(i)), root=self.root, domain="d")
        stalled = checklog.stop_loss("X-log-01", root=self.root)
        self.assertTrue(stalled["parked"], stalled)
        checklog.append(self.record(verdict="PENDING_GATE"), root=self.root, domain="d")
        self.assertFalse(checklog.stop_loss("X-log-01", root=self.root)["parked"])

    def test_an_unwritable_log_is_reported_not_raised(self):
        blocker = os.path.join(self.root, "file")
        with open(blocker, "w") as stream:
            stream.write("x")
        path, entry = checklog.append(self.record(), root=os.path.join(blocker, "sub"), domain="d")
        self.assertIsNone(path)
        self.assertIn("error", entry)


class EscapeTests(unittest.TestCase):
    def setUp(self):
        self.base = {"catalog_id": "X-esc-01", "instruction": "Put {cup} into {sink}.",
                     "instruction_roles": {"cup": "target", "sink": "destination"}}

    def priced(self, spec, exemptions=None, sessions=(), reviews=None, key=THROWAWAY_KEY):
        return escapes.price(spec, {"spec_hash": "h", "escape_facts": {"targets": ["cup"]}},
                             exemptions=exemptions or {}, sessions=sessions,
                             reviews=LOGGED if reviews is None else reviews, key=key)

    def test_prices_and_review_states(self):
        spec = dict(self.base, protections={"off": {"house.moved": "x" * 40}}, settle_s=45, free_ok=["a", "b"])
        result = self.priced(spec)
        keys = sorted((e["key"], e["points"], e["review"]) for e in result["escapes"])
        self.assertIn(("protections.off", 5, "review"), keys)
        self.assertIn(("settle_30_59", 2, "quota"), keys)
        self.assertEqual(sum(1 for k in keys if k[0] == "free_ok"), 2)
        self.assertEqual(result["points"], 5 + 2 + 1 + 1)
        self.assertEqual(result["quota_points"], 4)
        self.assertFalse(result["g9"])                      # protections.off unreviewed, quota above 3
        self.assertIn("escape:protections.off:house.moved", result["unreviewed"])

    def test_an_eligible_acceptance_clears_g9_and_an_author_session_cannot_sign(self):
        spec = dict(self.base, protections={"off": {"house.moved": "x" * 40}})
        row = {"catalog_id": "X-esc-01", "policy": "escape:protections.off:house.moved", "spec_hash": "h",
               "decision": "accept", "category": "other_reason", "reviewer": {"kind": "agent", "session_id": "rev-1"}}
        ok = self.priced(spec, {row["policy"]: row}, sessions=["author-1"])
        self.assertTrue(ok["g9"], ok["why"])
        bad = self.priced(spec, {row["policy"]: row}, sessions=["author-1", "rev-1"])
        self.assertFalse(bad["g9"])
        owner = dict(row, reviewer={"kind": "owner"})
        owner["signature"] = escapes.sign(owner, THROWAWAY_KEY)
        self.assertTrue(self.priced(spec, {row["policy"]: owner}, sessions=["rev-1"])["g9"])
        self.assertFalse(escapes.eligible(dict(row, category="other"), [], reviews=LOGGED))

    def test_targets_are_free_and_non_targets_are_priced(self):
        free = dict(self.base, envelope={"deliver": {"{cup}": "{owner}"}})
        self.assertEqual([e["key"] for e in self.priced(free)["escapes"]], [])
        paid = dict(self.base, envelope={"consume": ["{soap}"]})
        self.assertEqual([e["key"] for e in self.priced(paid)["escapes"]], ["consume_non_target"])
        consumable = dict(self.base, envelope={"consume": ["type:paper_towel"]})     # 6.6's own example: free
        self.assertEqual(self.priced(consumable)["escapes"], [])
        known = escapes.price(paid, {"spec_hash": "h", "escape_facts": {"targets": ["cup"],
                                                                        "valuable_selectors": []}},
                              exemptions={}, sessions=[])
        self.assertEqual(known["escapes"], [])                 # the verifier found no valuable behind {soap}
        ring = escapes.price(dict(self.base, envelope={"deliver": {"{ring}": "{owner}"}}),
                             {"spec_hash": "h", "escape_facts": {"targets": ["cup"], "valuable_selectors": ["{ring}"]}},
                             exemptions={}, sessions=[])
        self.assertEqual([e["key"] for e in ring["escapes"]], ["deliver_non_target"])

    def test_limits_on_points_and_exemptions(self):
        spec = dict(self.base, protections={"off": {"house.{}".format(g): "x" * 40 for g in
                                                    ("moved", "changed", "soiled", "wet")}})
        self.assertIn("20 escape points (at most 15)", self.priced(spec)["why"])
        exempt = [{"policy": "B{}.x".format(i), "category": "safe_superset", "reason": "r"} for i in (2, 3, 4)]
        spec = dict(self.base, battery_exempt=exempt)
        rows = {}
        for i in (2, 3, 4):
            row = {"catalog_id": "X-esc-01", "spec_hash": "h", "policy": "escape:battery_exempt:B{}.x".format(i),
                   "category": "safe_superset", "decision": "accept", "reviewer": {"kind": "owner"}}
            row["signature"] = escapes.sign(row, THROWAWAY_KEY)
            rows[row["policy"]] = row
        result = self.priced(spec, rows)
        self.assertEqual(result["exemptions_accepted"], 3)
        self.assertFalse(result["g9"])

    def test_the_accepted_records_file_keeps_the_last_decision(self):
        folder = tempfile.mkdtemp(prefix="wp5_exempt_")
        try:
            path = os.path.join(folder, "exemptions.jsonl")
            rows = [{"catalog_id": "X-esc-01", "spec_hash": "h", "policy": "p", "decision": "accept"},
                    {"catalog_id": "X-esc-01", "spec_hash": "h", "policy": "q", "decision": "accept"},
                    {"catalog_id": "X-esc-01", "spec_hash": "h", "policy": "q", "decision": "reject"},
                    {"catalog_id": "X-esc-01", "spec_hash": "other", "policy": "r", "decision": "accept"}]
            with open(path, "w", encoding="utf-8") as stream:
                stream.write("\n".join(json.dumps(r) for r in rows) + "\n")
            self.assertEqual(sorted(escapes.accepted_records("X-esc-01", "h", path)), ["p"])
        finally:
            shutil.rmtree(folder, ignore_errors=True)


def gate_record(**changes):
    """A synthetic evidence record whose every gate holds and whose score is 100."""
    record = {"level": "full", "core": {"ok": True}, "wrong_summary": {"ok": True, "total": 3, "verified": 3,
                                                                       "attributed": 3},
              "battery": {"ran": True, "runs": [{"id": "B2@0", "family": "B2", "outcome": "fails"}]},
              "mustpass": {"ran": True, "runs": [{"id": "M1@0", "outcome": "passes"}],
                           "summary": {"applicable": 1, "realised": 1, "failed_unexempt": 0}},
              "protect": {"items": 2, "live": 2, "lint": []}, "lint": [], "harness_errors": [],
              "heldout": {"ran": True, "ok": True, "seeds": 10, "reference_failures": [], "passes_share": 0.0},
              "budget": {"formula_ok": True}, "randomisation": {"answers": {}}, "coverage": {"expected": ["B2"],
                                                                                           "ran": True,
                                                                                           "applicable_families": ["B2"]},
              "guess": {"rate": 0.0}, "private": {"ran": True}, "escapes": {"g9": True, "points": 0, "escapes": []}}
    record.update(changes)
    return record


class StrengthTests(unittest.TestCase):
    def test_a_perfect_record_is_hardened_with_score_100(self):
        record = gate_record()
        scored = strength.score(record)
        self.assertEqual(scored["score"], 100)
        self.assertTrue(all(strength.gates(record).values()))
        self.assertEqual(strength.verdict(record), "HARDENED")

    def test_verdicts_without_the_key_without_reviews_and_below_the_thresholds(self):
        self.assertEqual(strength.verdict(gate_record(private={"ran": False})), "PENDING_GATE")
        review = {"g9": False, "points": 3, "escapes": [], "unreviewed": ["escape:x"],
                  "why": ["1 escape(s) without an accepted review: escape:x"]}
        self.assertEqual(strength.verdict(gate_record(escapes=review)), "PENDING_REVIEW")
        no_battery = gate_record(battery={"ran": False}, coverage={"expected": ["B2"], "ran": False})
        self.assertEqual(strength.gates(no_battery)["G3"], False)
        self.assertIn(strength.verdict(no_battery), ("PROVISIONAL", "FAIL"))
        self.assertEqual(strength.verdict(gate_record(core={"ok": False})), "FAIL")

    def test_the_components_follow_19_1(self):
        record = gate_record(battery={"ran": True, "runs": [
            {"id": "B3.floor@0", "family": "B3", "outcome": "fails"},
            {"id": "B3.near@0", "family": "B3", "outcome": "passes"},
            {"id": "B4.see@0", "family": "B4", "outcome": "unrealised"},
            {"id": "B2@0", "family": "B2", "outcome": "skipped:na"}]})
        comp = strength.score(record)["components"]
        self.assertAlmostEqual(comp["c_battery"], 1 / 3.0, places=3)
        self.assertFalse(strength.gates(record)["G3"])
        wrong = strength.score(gate_record(wrong_summary={"ok": True, "total": 2, "verified": 2,
                                                          "attributed": 1}))["components"]["c_wrong"]
        self.assertAlmostEqual(wrong, (2 / 3.0) * 0.5, places=3)
        self.assertEqual(strength.score(gate_record(heldout={"ran": False}))["components"]["c_heldout"], 0.0)
        priced = strength.score(gate_record(escapes={"g9": True, "points": 7, "escapes": []}))
        self.assertEqual(priced["score"], 93)

    def test_expected_families_and_kind_classes(self):
        conditional = {"success": "The milk is discarded when spoiled", "conditional": "spoiled: discard",
                       "wrong": ["Grabs a similar-looking carton"], "mechanics": ["food.smell"], "protect": []}
        fams = strength.expected_families(conditional, "Check the milk")
        for family in ("B2", "B4", "B5", "B8", "B10", "B11", "B13"):
            self.assertIn(family, fams)
        self.assertEqual(strength.kind_class(fams), "conditional")
        self.assertEqual(strength.kind_class([]), "plain")
        self.assertEqual(strength.kind_class(["B4"]), "lookalike")
        speech = {"success": "The child is told to stay out of the kitchen", "wrong": [], "protect": []}
        self.assertIn("B12", strength.expected_families(speech))

    def test_level_passes(self):
        self.assertTrue(strength.level_passes({"gates": {"G1": True, "G2": True, "G5": True, "G7": True}}, "quick"))
        self.assertFalse(strength.level_passes({"verdict": "FAIL", "gates": {}}, "standard"))
        self.assertTrue(strength.level_passes({"verdict": "PENDING_GATE", "gates": {}}, "full"))


class ExpectTests(unittest.TestCase):
    def test_the_14_2_grammar(self):
        canon = verify.canonical_labels(["house.moved:cup_1", "spills", "fail:vase_broken", "later:house.damaged:f.t",
                                         "people.unwanted_wake:resident_1", "perceive:acted_unresolved:b_1",
                                         "question_missing:age", "settle:dishes"], ["clog_cleared", "lid:absent"],
                                        said={"lid"})
        for pattern, hit in (("house:moved:cup_1", True), ("house:moved", True), ("house:moved:cup_2", False),
                             ("flag:spills", True), ("fail:vase_broken", True), ("fail:vase", False),
                             ("later:house:damaged:f.t", True), ("later:house:damaged", True),
                             ("inv:people.unwanted_wake", True), ("inv:people", False),
                             ("inv:people.unwanted", False), ("inv:", False),
                             ("perceive:acted_unresolved", True), ("question:question_missing:age", True),
                             ("goal:clog_cleared", True), ("goal:lid", True), ("said:lid", True),
                             ("settle:dishes", True), ("goal:clog", False), ("budget", False)):
            with self.subTest(pattern=pattern):
                self.assertEqual(verify.pattern_matches(pattern, canon), hit)

    def test_expect_must_be_specific(self):
        self.assertTrue(verify.expect_problems({"expect": ["goal"], "mistake": [0]}))
        self.assertTrue(verify.expect_problems({"expect": ["house:moved"], "mistake": [0]}))
        self.assertTrue(verify.expect_problems({"expect": ["budget"], "mistake": [0]}))
        self.assertFalse(verify.expect_problems({"expect": ["budget"], "mistake": [0], "expect_budget": "slow"}))
        self.assertTrue(verify.expect_problems({"expect": ["goal:x"]}))           # no mistake
        self.assertTrue(verify.expect_problems({"expect": ["frob:x"], "mistake": [0]}))
        self.assertFalse(verify.expect_problems({"expect": ["goal:x", "house:moved:cup_1"], "mistake": [1]}))

    def test_judge_wrong_applies_rule_1_and_the_budget_rule(self):
        item = {"expect": ["goal:x"], "mistake": [1]}
        ran = {"raised": False, "steps_run": 3, "steps_total": 3, "refused": [], "fails": True,
               "failures": [], "unmet": ["x"]}
        self.assertTrue(verify.judge_wrong(dict(ran), item)["ok"])
        outside = verify.judge_wrong(dict(ran, refused=[0], refused_categories={"0": ["precondition"]}), item)
        self.assertFalse(outside["ok"])
        self.assertEqual(outside["error_class"], "RefusedOutsideMistake")
        strict = verify.judge_wrong(dict(ran, refused=[0], refused_categories={"0": ["safety"]},
                                         failures=["strict:safety"]),
                                    {"expect": ["strict:safety"], "mistake": [1]})
        self.assertTrue(strict["ok"])
        budget = verify.judge_wrong(dict(ran, failures=["budget"], unmet=[]),
                                    {"expect": ["goal:x", "budget"], "mistake": [1], "expect_budget": "r" * 30})
        self.assertTrue(budget["ok"])
        only = verify.judge_wrong(dict(ran, failures=["budget"], unmet=[]), {"expect": ["budget"], "mistake": [1]})
        self.assertFalse(only["ok"])
        raised = verify.judge_wrong(dict(ran, raised=True, steps_run=1), item)
        self.assertFalse(raised["ran"])
        attributed = verify.judge_wrong(dict(ran, attribution={"failures": [], "unmet": []}), item)
        self.assertTrue(attributed["attributed"])
        leaked = verify.judge_wrong(dict(ran, attribution={"failures": [], "unmet": ["x"]}), item)
        self.assertFalse(leaked["attributed"])
        self.assertEqual(leaked["attribution"]["leaked"], ["goal:x"])

    def test_the_mistake_fix_is_the_aligned_reference(self):
        reference = [{"skill": "fetch", "obj": "{a}"}, {"skill": "open", "target": "{lid}"},
                     {"skill": "put", "obj": "{a}", "dest": "{box}"}, {"skill": "close", "target": "{lid}"}]
        wrong = [{"skill": "fetch", "obj": "{a}"}, {"skill": "put", "obj": "{a}", "dest": "{floor}"},
                 {"skill": "close", "target": "{lid}"}]
        fixes = verify.default_mistake_fix(wrong, [1], reference)
        self.assertEqual(fixes, {1: reference[1:3]})
        fixed = verify.attributed_plan(dict(plan=wrong, mistake=[1]), reference)
        self.assertEqual(fixed, reference)
        explicit = verify.attributed_plan({"plan": wrong, "mistake": [1],
                                           "mistake_fix": [{"skill": "put", "obj": "{a}", "dest": "{box}"}]},
                                          reference)
        self.assertEqual(explicit, [wrong[0], {"skill": "put", "obj": "{a}", "dest": "{box}"}, wrong[2]])
        tail = verify.default_mistake_fix(wrong + [{"do": "wait", "seconds": 9}], [3], reference)
        self.assertEqual(tail, {3: []})


class BudgetAndCacheTests(unittest.TestCase):
    def test_the_formula_of_17(self):
        out = verify.formula_budget([{"steps": 20, "waited_s": 100, "active_s": 50},
                                     {"steps": 18, "waited_s": 120, "active_s": 40}],
                                    [{"steps": 7, "s": 41}, {"steps": 3, "s": 60}])
        steps = 3 * 20 + 7 + 15                             # 82 -> rounded up to 90
        time_s = 3 * 50 + 1.25 * 120 + 60 + 180             # 540 -> 540
        self.assertEqual(out, {"steps": 90, "time_s": 540})
        self.assertEqual((steps, time_s), (82, 540.0))

    def test_horizon_tiers(self):
        self.assertEqual(verify._tier_of(900)["id"], "H0")
        self.assertEqual(verify._tier_of(3600)["id"], "H0")
        self.assertEqual(verify._tier_of(7200)["id"], "H1")
        self.assertEqual(verify._tier_of(20000)["id"], "H2")
        self.assertEqual(verify._tier_of(200000)["id"], "H3")

    def test_the_run_cache_keys_leave_judged_keys_out(self):
        a = book_spec()
        b = copy.deepcopy(a)
        b["wrong"][0]["expect"] = ["goal:other"]
        b["protect_map"] = [{"item": "x", "waive": "y" * 30}]
        self.assertEqual(runcache.key("k", runcache.sim_spec(a)), runcache.key("k", runcache.sim_spec(b)))
        c = copy.deepcopy(a)
        c["wrong"][0]["mistake"] = [0]
        self.assertNotEqual(runcache.key("k", runcache.sim_spec(a)), runcache.key("k", runcache.sim_spec(c)))
        folder = tempfile.mkdtemp(prefix="wp5_cache_")
        try:
            k = runcache.key("k", {"x": 1})
            self.assertIsNone(runcache.get(k, folder))
            runcache.put(k, {"v": [1, 2]}, folder)
            self.assertEqual(runcache.get(k, folder), {"v": [1, 2]})
            runcache.put(runcache.key("k", {"x": 2}), {"v": "y" * 5000}, folder, max_bytes=100)
            self.assertLessEqual(sum(p.stat().st_size for p in Path(folder).glob("*/*.json")), 6000)
        finally:
            shutil.rmtree(folder, ignore_errors=True)


class PipelineTests(unittest.TestCase):
    """End to end on the small schema-4 task (VERIFY_HARDENING 5.1 S0-S4 and S8)."""

    @classmethod
    def setUpClass(cls):
        load_domains()
        cls.log = tempfile.mkdtemp(prefix="wp5_pipeline_log_")
        with schema4_keys():
            cls.record = verify.verify_spec(book_spec(), "quick", seeds=[0], check_log=True, log_root=cls.log)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.log, ignore_errors=True)

    def test_the_core_gates_hold_in_every_mode(self):
        record = self.record
        self.assertEqual(record["format"], "house_ai.verification/2")
        self.assertTrue(record["gates"]["G1"], record["core"])
        self.assertTrue(record["gates"]["G2"], record["wrong_summary"])
        self.assertTrue(record["gates"]["G7"], record["harness_errors"])
        modes = {t["mode"] for t in record["trials"]}
        self.assertEqual(modes, {verify.OWN} | set(verify.eval_modes()))
        self.assertTrue(record["passed"])              # quick: G1, G2, G5, G7
        self.assertEqual(record["debug"], ["--seeds"])

    def test_every_wrong_plan_is_verified_and_attributed(self):
        summary = self.record["wrong_summary"]
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["verified"], 2)
        self.assertEqual(summary["attributed"], 2)
        for trial in self.record["trials"]:
            for w in trial["wrong"]:
                self.assertEqual(w["matched"], ["goal:book_shelved"])
                self.assertIn("book_shelved", w["unmet"])
                if trial["mode"] == verify.EVAL_GEOM:
                    self.assertTrue(w["attributed"])

    def test_the_reference_record_carries_its_trace_resources_and_settle(self):
        for trial in self.record["trials"]:
            ref = trial["reference"]
            with self.subTest(mode=trial["mode"]):
                self.assertTrue(ref["passes"])
                self.assertTrue(ref["envelope_ok"])
                self.assertTrue(ref["settle_ok"])
                self.assertEqual(set(ref["resources"]), {"energy", "gas", "water"})
                self.assertEqual(len(ref["trace_digest"]), 64)
                self.assertTrue(trial["idle"]["fails"])
                self.assertGreaterEqual(trial["idle"]["samples"], 1)
                if trial["mode"] != verify.OWN:
                    self.assertTrue(ref["perceivable"])
                    self.assertTrue(ref["gated_ok"])
        survey = [t for t in self.record["trials"] if t["mode"] == verify.EVAL_GEOM][0]["survey"]
        self.assertIn("living_room_1", survey.get("rooms") or [])

    def test_no_hardened_verdict_without_the_batteries(self):
        record = self.record
        self.assertEqual(record["battery"], {"ran": False})        # quick: S5 does not run
        self.assertIn(record["verdict"], ("FAIL", "PROVISIONAL"))
        self.assertEqual(record["private"]["status"], private.NOT_RUN)
        self.assertEqual(record["strength"]["components"]["c_battery"], 0.0)
        self.assertIn("budget", record)
        self.assertIn(record["tier"], ("H0", "H1"))

    def test_the_check_log_got_its_line(self):
        rows = checklog.entries("T-wp5-book-01", root=self.log)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["verdict"], self.record["verdict"])
        self.assertEqual(rows[0]["level"], "quick")

    def test_the_record_is_deterministic(self):
        with schema4_keys():
            again = verify.verify_spec(book_spec(), "quick", seeds=[0], modes=[verify.OWN], check_log=False)
        mine = [t for t in self.record["trials"] if t["mode"] == verify.OWN][0]
        theirs = again["trials"][0]
        self.assertEqual(mine["reference"]["trace_digest"], theirs["reference"]["trace_digest"])
        self.assertEqual(mine["idle"]["digest"], theirs["idle"]["digest"])
        self.assertEqual([w["failures"] for w in mine["wrong"]], [w["failures"] for w in theirs["wrong"]])


class FailureCaseTests(unittest.TestCase):
    """Cases that must fail the core gates (one seed, the own mode and the geometric evaluation mode)."""

    @classmethod
    def setUpClass(cls):
        load_domains()

    def run_spec(self, spec, modes=(verify.OWN,)):
        with schema4_keys():
            return verify.verify_spec(spec, "quick", seeds=[0], modes=list(modes), check_log=False)

    def test_a_reference_outside_its_envelope_fails_g1(self):
        spec = book_spec(envelope={"work_rooms": ["{living}"]})    # the book's move is not allowed
        record = self.run_spec(spec)
        self.assertFalse(record["gates"]["G1"])
        ref = record["trials"][0]["reference"]
        self.assertFalse(ref["envelope_ok"])
        self.assertTrue(any(v.startswith("envelope_violation:house.") for v in ref["envelope_violations"]))

    def test_a_task_the_idle_house_satisfies_fails_g1(self):
        spec = book_spec(success=[{"label": "late", "pred": {"path": "time_s", "op": "gte", "value": 120}}],
                         envelope={"work_rooms": ["{living}"], "move": {"{book}": {"to": ["{shelf}"]}}})
        spec["reference"] = [{"do": "wait", "seconds": 130}]
        spec["wrong"] = [dict(TO_TABLE, expect=["goal:late"]), dict(HOLDS, expect=["goal:late"])]
        record = self.run_spec(spec)
        trial = record["trials"][0]
        self.assertFalse(trial["idle"]["fails"])
        self.assertTrue(trial["idle"]["passes_at"])
        self.assertFalse(record["gates"]["G1"])

    def test_a_crashing_wrong_plan_fails_g2(self):
        crash = {"name": "reads nothing", "plan": [{"save": "x", "from": "nothing.here"}],
                 "expect": ["goal:book_shelved"], "mistake": [0]}
        record = self.run_spec(book_spec(wrong=[crash, copy.deepcopy(HOLDS)]))
        self.assertFalse(record["gates"]["G2"])
        wrongs = {w["name"]: w for w in record["trials"][0]["wrong"]}
        self.assertFalse(wrongs["reads nothing"]["ran"])
        self.assertTrue(wrongs["reads nothing"]["raised"])
        self.assertFalse(wrongs["reads nothing"]["ok"])
        self.assertTrue(wrongs["holds the book and waits"]["ok"])

    def test_a_vague_expect_is_never_verified(self):
        vague = dict(TO_TABLE, name="vague", expect=["goal"])
        record = self.run_spec(book_spec(wrong=[vague, copy.deepcopy(HOLDS)]))
        # WP8's L-EXPECT stops the spec at S0 (22.6): nothing runs
        self.assertTrue(any(p["id"] == "L-EXPECT" and p["severity"] == "error" for p in record["lint"]))
        self.assertFalse(record["gates"]["G5"])
        self.assertEqual(record["trials"], [])
        self.assertEqual(record["verdict"], "FAIL")
        rec = verify.judge_wrong({"raised": False, "steps_run": 2, "steps_total": 2, "refused": [], "fails": True,
                                  "failures": [], "unmet": ["book_shelved"]}, vague)
        self.assertIn("a bare goal is not specific", rec["expect_problems"])
        self.assertFalse(rec["ok"])

    def test_a_wrong_plan_for_another_reason_is_not_verified(self):
        failure = [{"label": "book_broken", "pred": {"path": "entities.{book}.broken", "op": "is_true"}}]
        other = dict(TO_TABLE, name="wrong reason", expect=["fail:book_broken"])
        record = self.run_spec(book_spec(failure=failure, wrong=[other, copy.deepcopy(HOLDS)]))
        wrongs = {w["name"]: w for w in record["trials"][0]["wrong"]}
        self.assertTrue(wrongs["wrong reason"]["fails"])
        self.assertFalse(wrongs["wrong reason"]["for_reason"])
        self.assertFalse(wrongs["wrong reason"]["ok"])
        self.assertFalse(record["gates"]["G2"])

    def test_an_unattributed_wrong_plan_fails_g2(self):
        # the plan's "mistake" is the fetch, which the reference also does: w' still leaves the book on the table
        mislabelled = dict(TO_TABLE, name="misattributed", mistake=[0],
                           mistake_fix=[{"skill": "fetch", "obj": "{book}"}])
        record = self.run_spec(book_spec(wrong=[mislabelled, copy.deepcopy(HOLDS)]),
                               modes=(verify.EVAL_GEOM,))
        wrongs = {w["name"]: w for w in record["trials"][0]["wrong"]}
        self.assertTrue(wrongs["misattributed"]["ok"])
        self.assertFalse(wrongs["misattributed"]["attributed"])
        self.assertTrue(wrongs["holds the book and waits"]["attributed"])
        self.assertFalse(record["gates"]["G2"])


class TimingVacuityTests(unittest.TestCase):
    """WP3's vacuous-trigger gap: a timing item whose trigger never held during a reference run that ended before
    the seed's drawn event time is an L-TIMING error (8.5, 9.2)."""

    @classmethod
    def setUpClass(cls):
        load_domains()

    def synthetic(self, rule, onset, end):
        return [{"mode": "own", "references": {0: {"answers": {"onset": {"kind": "time", "value": onset}}}},
                 "trials": [{"seed": 0, "reference": {"end_time_s": end, "timing_untriggered": [
                     {"label": "close_after_rain", "if_no_trigger": rule}]}}]}]

    def test_an_event_after_the_run_is_an_error_and_a_never_seed_a_warning(self):
        error = verify.timing_vacuity(self.synthetic("forbid", 3000, 120))
        self.assertEqual([r["severity"] for r in error], ["error"])
        self.assertIn("before the drawn event", error[0]["message"])
        self.assertEqual([r["severity"] for r in verify.timing_vacuity(self.synthetic("allow", None, 120))],
                         ["warning"])
        self.assertEqual(verify.timing_vacuity(self.synthetic("require", 3000, 120)), [])

    def test_a_real_run_records_the_untriggered_item(self):
        item = {"label": "late_shelf", "trigger": {"path": "time_s", "op": "gte", "value": 90000},
                "act": {"path": "entities.{book}.location", "op": "eq", "value": "{shelf}"}, "within_s": 600,
                "if_no_trigger": "allow"}
        with schema4_keys():
            out = verify.mode_trials(book_spec(timing=[item]), [0], verify.OWN)
        ref = out["trials"][0]["reference"]
        self.assertTrue(ref["passes"], ref)
        self.assertEqual(ref["timing_untriggered"], [{"label": "late_shelf", "if_no_trigger": "allow"}])
        rows = verify.timing_vacuity([out])
        self.assertEqual([r["severity"] for r in rows], ["warning"])

    def test_run_trial_takes_a_mode_and_verify_many_keeps_the_order(self):
        with schema4_keys():
            trial = verify.run_trial(book_spec(), 0, mode="own")
            self.assertEqual(trial["mode"], verify.OWN)
            self.assertTrue(trial["passed"], trial)
            results = verify.verify_many([legacy_spec(), book_spec()], seeds=(0,), workers=1, level="quick",
                                         hard_seeds=[0], modes=[verify.OWN], check_log=False)
        self.assertNotIn("format", results[0])
        self.assertEqual(results[1]["format"], "house_ai.verification/2")
        self.assertEqual(results[1]["level"], "quick")


class HeldoutTests(unittest.TestCase):
    """S7 at the full level: public held-out seeds on an authoring machine; with a (throwaway) split key the
    private held-out seeds replace them and never appear in the public record (12.1, 12.3, 16)."""

    @classmethod
    def setUpClass(cls):
        load_domains()
        cls.home = tempfile.mkdtemp(prefix="wp5_heldout_home_")
        with mock.patch.dict(os.environ, {"HOUSE_AI_SPLIT_KEY": ""}), \
                mock.patch("house_sim.eval.splits.private_dir", return_value=Path(cls.home) / ".hai"), \
                schema4_keys():
            cls.public = verify.verify_spec(book_spec(), "full", seeds=[0], heldout=1, check_log=False)
            cls.private = verify.verify_spec(book_spec(), "full", seeds=[0], heldout=1, check_log=False,
                                             key=THROWAWAY_KEY)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.home, ignore_errors=True)

    def test_public_heldout_seeds_run_the_core_trial(self):
        held = self.public["heldout"]
        self.assertTrue(held["ran"])
        self.assertTrue(held["public"])
        self.assertTrue(held["ok"], held)
        seeds_run = self.public["seeds"]["heldout_public"]
        self.assertTrue(seeds_run and all(200000 <= s < 1000000 for s in seeds_run))
        self.assertTrue(any(t.get("heldout") for t in self.public["trials"]))
        self.assertTrue(self.public["gates"]["G6"])
        self.assertEqual(self.public["private"]["status"], private.NOT_RUN)

    def test_private_heldout_seeds_stay_out_of_the_record(self):
        from house_sim.eval import splits
        held = self.private["heldout"]
        self.assertFalse(held["public"])
        self.assertTrue(held["ok"], held)
        self.assertNotIn("heldout_public", self.private["seeds"])
        self.assertEqual(len(self.private["seeds"]["heldout_private_digest"]), 64)
        text = json.dumps(self.private, default=str)
        for seed in private.heldout(book_spec(), key=THROWAWAY_KEY, spec_hash=self.private["spec_hash"], n=1):
            self.assertGreaterEqual(seed, splits.PRIVATE_BASE)
            self.assertNotIn(str(seed), text)
        self.assertFalse(any(t.get("heldout") for t in self.private["trials"]))
        self.assertIn("not stored", str(self.private["private"]["details_stored"]))    # no private folder here
        self.assertFalse((Path(self.home) / ".hai").exists())
        self.assertFalse(self.private["private"]["ran"])              # the private families are WP6's
        self.assertNotEqual(self.private["verdict"], "HARDENED")


class EscapeFactTests(unittest.TestCase):
    """The verifier's facts for the conditional prices of 18: a never-touch change entry, a restore-class change
    entry, an absent_ok label covered by the consumption of a target."""

    @classmethod
    def setUpClass(cls):
        load_domains()

    def test_change_entries_on_a_detector_and_a_restore_class_item_are_priced(self):
        spec = book_spec(setup=SETUP + [{"op": "spawn", "type": "smoke_alarm", "mount": "{living}", "var": "alarm"},
                                        {"op": "find", "var": "fridge", "type": "freezer_drawer", "pick": "first"}])
        spec["envelope"]["change"] = {"{alarm}": ["muted"], "{fridge}": ["open"]}
        with schema4_keys():
            out = verify.mode_trials(spec, [0], verify.OWN)
        self.assertEqual(out["references"][0]["envelope_classes"],
                         {"never_touch_changes": ["{alarm}"], "restore_changes": ["{fridge}"],
                          "valuable_selectors": []})
        facts = verify.escape_facts(spec, {}, [out], [], False)
        priced = escapes.price(spec, {"spec_hash": "h", "escape_facts": facts}, exemptions={}, sessions=[])
        self.assertEqual(sorted((e["key"], e["item"]) for e in priced["escapes"]),
                         [("hold_open", "{fridge}"), ("never_touch_change", "{alarm}")])

    def test_an_absent_ok_label_covered_by_a_target_consumption_is_free(self):
        spec = book_spec(success=[{"label": "no_book", "pred": {"path": "entities.{book}.wet", "op": "is_false"}}],
                         absent_ok=["no_book"])
        spec["envelope"] = {"consume": ["{book}"]}
        facts = verify.escape_facts(spec, {}, [], [], False)
        self.assertEqual(facts["absent_ok_covered"], ["no_book"])
        uncovered = verify.escape_facts(dict(spec, envelope={}), {}, [], [], False)
        self.assertEqual(uncovered["absent_ok_covered"], [])


class CacheAndCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_domains()

    def test_a_cache_hit_is_rejudged_with_the_new_expect(self):
        folder = tempfile.mkdtemp(prefix="wp5_runs_")
        try:
            with schema4_keys():
                job = (book_spec(), [0], verify.OWN, None, None, False, False, False, 0.0, folder)
                first = verify._mode_job(job)
                self.assertFalse(first.get("cached"))
                edited = book_spec()
                edited["wrong"][0]["expect"] = ["fail:never_happens"]
                second = verify._mode_job((edited,) + job[1:])
            self.assertTrue(second.get("cached"))
            w = second["trials"][0]["wrong"][0]
            self.assertFalse(w["ok"])
            self.assertEqual(w["matched"], [])
            self.assertEqual(first["trials"][0]["wrong"][0]["failures"], w["failures"])
            self.assertFalse(second["trials"][0]["passed"])
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_check_prints_the_block_of_21_2_and_legacy_specs_as_before(self):
        folder = tempfile.mkdtemp(prefix="wp5_cli_")
        try:
            path = os.path.join(folder, "specs.json")
            with open(path, "w", encoding="utf-8") as stream:
                json.dump({"specs": [book_spec()]}, stream)
            from house_sim import cli
            buffer = io.StringIO()
            with schema4_keys(), mock.patch.dict(os.environ, {"HOUSE_AI_CHECK_LOG_DIR": folder}), \
                    redirect_stdout(buffer):
                code = cli.check_file(path, workers=1, seeds=[0], level="quick", cache=False,
                                      json_out=os.path.join(folder, "out.jsonl"))
            text = buffer.getvalue()
            self.assertEqual(code, 0, text)
            self.assertIn("T-wp5-book-01", text)
            self.assertIn("gates: G1 ok G2 ok", text)
            self.assertIn("private  not run (no split key)", text)
            with open(os.path.join(folder, "out.jsonl"), encoding="utf-8") as stream:
                self.assertEqual(json.loads(stream.readline())["catalog_id"], "T-wp5-book-01")
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_legacy_specs_take_the_legacy_path(self):
        record = verify.verify_spec(legacy_spec(), seeds=(0,))
        self.assertNotIn("format", record)
        self.assertTrue(record["passed"], record)
        self.assertEqual(set(record), {"catalog_id", "spec_hash", "trials", "passed", "fidelity"})
        trial = verify.run_trial(legacy_spec(), 0)
        self.assertIn("wrong_fails", trial)
        self.assertNotIn("mode", trial)

    def test_draft_hardening_suggests_without_writing(self):
        with schema4_keys():
            draft = verify.draft_hardening(legacy_spec(), seeds=[0])
        self.assertIn("move", draft["envelope"])          # the book's move, generalised from the reference
        self.assertIn("{book}", draft["envelope"]["move"])
        self.assertEqual(draft["reference_failures"]["0"], [])     # nothing but the envelope draft blocks it
        self.assertIn("goal:success_0", draft["wrong_observed"]["puts the book back on the table"])
        self.assertEqual(draft["instruction_roles"], {})
        self.assertIn("note", draft)


FRIDGE_SETUP = [{"op": "room", "var": "k", "type": "kitchen", "pick": "first"},
                {"op": "find", "var": "fridge", "type": "refrigerator", "room": "{k}", "pick": "first"},
                {"op": "find", "var": "counter", "type": "counter", "room": "{k}", "pick": "first"},
                {"op": "spawn", "type": "milk_carton", "in": "{fridge}", "var": "milk"},
                {"op": "robot", "room": "{k}"}, {"op": "prior_knowledge", "map": "all"}]
SHUT = {"skill": "appliances_cooking.shut_fridge", "fridge": "{fridge}"}


def fridge_spec():
    """A schema-4 task whose reference opens the fridge only inside the fetch skill: no plan step names the fridge
    for the look-ahead trigger, the reference's run does (review finding A-spec-7)."""
    return {"catalog_id": "T-wp5fix-fridge-01", "title": "milk to the counter", "schema": 4, "risk_level": 0,
            "house": {"residents": "minimal", "pets": "none"}, "setup": copy.deepcopy(FRIDGE_SETUP),
            "instruction": "Put the milk on the counter.",
            "phrasings": ["Put the milk on the counter.", "Milk on the counter, please.",
                          "Get the milk out onto the counter."],
            "success": [{"label": "milk_out", "pred": {"path": "entities.{milk}.location", "op": "eq",
                                                        "value": "{counter}"}}],
            "envelope": {"work_rooms": ["{k}"], "move": {"{milk}": {"to": ["{counter}"]}}},
            "budget": {"steps": 40, "time_s": 300},
            "reference": [{"skill": "fetch", "obj": "{milk}"}, {"skill": "put", "obj": "{milk}", "dest": "{counter}"}],
            "wrong": [{"name": "takes the milk out and holds it",
                       "plan": [{"skill": "fetch", "obj": "{milk}"}, copy.deepcopy(SHUT),
                                {"do": "wait", "seconds": 30}],
                       "expect": ["goal:milk_out"], "mistake": [2]},
                      {"name": "leaves the milk in the fridge", "plan": [{"do": "wait", "seconds": 30}],
                       "expect": ["goal:milk_out"], "mistake": [0]}]}


class ReviewFindingTests(unittest.TestCase):
    """Regression tests for the run-A review findings on WP5 (catalog/analysis/phase2/review_A_*.json, confirmed in
    verify_A_*.json): each replays its finding's repro and failed before the fix (catalog/analysis/phase2/WP5-fix.json
    and handoff_WP5-fix.md)."""

    @classmethod
    def setUpClass(cls):
        load_domains()

    # A-bugs-1: a standard check judged on the gates the standard level runs (24 item 2) --------------------------
    def test_a_bugs_1_a_clean_standard_check_passes_without_the_heldout_gate(self):
        record = {"level": "standard", "core": {"ok": True}, "wrong_summary": {"ok": True},
                  "battery": {"ran": True, "runs": []}, "mustpass": {"ran": True, "summary": {"applicable": 0}},
                  "protect": {"items": 0}, "lint": [], "harness_errors": [], "escapes": {"g9": True},
                  "heldout": {"ran": False}, "private": {"ran": False}}
        gate_map = strength.gates(record)
        self.assertIsNone(gate_map["G6"])                   # S7 does not run at the standard level
        for value in (80.0, 100.0):
            verdict = strength.verdict(record, {"score": value}, gate_map)
            self.assertEqual(verdict, "PENDING_GATE")
            self.assertTrue(strength.level_passes(dict(record, gates=gate_map, verdict=verdict), "standard"))
        self.assertEqual(strength.verdict(record, {"score": 70.0}, gate_map), "PROVISIONAL")
        # never HARDENED below the full level, and the full level still needs G6
        self.assertEqual(strength.verdict(dict(record, private={"ran": True}), {"score": 100.0}, gate_map),
                         "PENDING_GATE")
        full = dict(record, level="full")
        self.assertFalse(strength.gates(full)["G6"])
        self.assertIn(strength.verdict(full, {"score": 100.0}), ("PROVISIONAL", "FAIL"))
        # the projected score: the held-out component, which S7 alone measures, is left out of the weights
        standard = strength.score(gate_record(level="standard", heldout={"ran": False}))
        self.assertTrue(standard["projected"])
        self.assertEqual(standard["score"], 100)
        self.assertEqual(strength.score(gate_record(heldout={"ran": False}))["score"], 90)     # full: 0 (P4)
        self.assertEqual(strength.verdict(gate_record(level="standard", heldout={"ran": False},
                                                      private={"ran": False})), "PENDING_GATE")

    # A-bugs-5: the run cache key holds the loaded domains (HOUSE_SIM_DOMAINS) ------------------------------------
    def test_a_bugs_5_a_run_made_with_other_domains_loaded_is_never_reused(self):
        folder = tempfile.mkdtemp(prefix="wp5fix_rc_")
        calls = []

        def fake(spec, seeds, mode, **kwargs):
            calls.append(mode)
            return {"mode": mode, "trials": [], "references": {}}
        try:
            job = (book_spec(), [0], verify.OWN, None, None, False, False, False, 0.0, folder)
            with schema4_keys(), mock.patch.object(verify, "mode_trials", side_effect=fake), \
                    mock.patch.dict(os.environ, {"HOUSE_SIM_RUN_CACHE": "1"}):
                with mock.patch.dict(os.environ, {"HOUSE_SIM_DOMAINS": "core"}):
                    self.assertFalse(verify._mode_job(job).get("cached"))
                    self.assertTrue(verify._mode_job(job).get("cached"))      # same environment: a hit
                with mock.patch.dict(os.environ, {"HOUSE_SIM_DOMAINS": "core,cleaning"}):
                    self.assertFalse(verify._mode_job(job).get("cached"))     # other domains loaded: a miss
            self.assertEqual(len(calls), 2)
            with mock.patch.dict(os.environ, {"HOUSE_SIM_DOMAINS": "core"}):
                core = runcache.environment()
            with mock.patch.dict(os.environ, {"HOUSE_SIM_DOMAINS": "core,cleaning"}):
                more = runcache.environment()
            self.assertEqual(core["domains"], ["core"])
            self.assertNotEqual(core, more)
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    # A-bugs-6: a reference step the skill cannot take is the spec's error, not a harness error --------------------
    def test_a_bugs_6_a_bad_reference_argument_is_a_spec_error(self):
        bad_step = {"skill": "fetch", "obj": "{book}", "handd": "left"}
        spec = book_spec(reference=[bad_step, {"skill": "put", "obj": "{book}", "dest": "{shelf}"}])
        spec["wrong"][0]["plan"] = [bad_step]
        with schema4_keys():
            out = verify.mode_trials(spec, [0], verify.OWN)
        ref = out["trials"][0]["reference"]
        self.assertFalse(ref["passes"])
        self.assertTrue(ref["spec_error"])
        self.assertNotIn("harness_error", ref)
        self.assertIn("handd", ref["error"])
        self.assertEqual(verify._harness_errors([out]), [])                  # G7 holds; G1 fails on the reference
        held = verify._heldout_record(out, True, [], 1)
        self.assertEqual(held["skill_debt"], [])
        self.assertEqual(len(held["reference_failures"]), 1)                 # a spec failure (16.3)

    # A-spec-11: a skill's own route PlanError on a held-out seed is skill debt (16.3, WP11's category) -------------
    def test_a_spec_11_a_skill_route_failure_is_skill_debt_and_an_argument_one_is_not(self):
        from house_sim.tasks.runner import PlanError
        with schema4_keys():
            config, task, oracle = build(book_spec(), 0)
        route = PlanError("No route in living_room_1 at all: furniture, walls or closed things cut the way off")
        route.category = "route"
        with mock.patch.object(Runner, "run_top", side_effect=route):
            rec, _, _ = verify.reference_run(config, task, oracle, 0, verify.OWN, None)
        self.assertEqual(rec["error_category"], "route")
        held = verify._heldout_record({"trials": [{"seed": 200001, "reference": rec, "idle_fails": True,
                                                   "wrong": []}]}, True, [], 10)
        self.assertEqual(held["reference_failures"], [])
        self.assertEqual(len(held["skill_debt"]), 1)
        self.assertIn("house_sim/tasks/skills.py", held["skill_debt"][0]["charged_to"])
        self.assertTrue(held["ok"])                         # one debt seed is within 16.4's allowance
        argument = PlanError("put: dest is None")
        argument.category = "argument"
        with mock.patch.object(Runner, "run_top", side_effect=argument):
            rec2, _, _ = verify.reference_run(config, task, oracle, 0, verify.OWN, None)
        held2 = verify._heldout_record({"trials": [{"seed": 200001, "reference": rec2, "idle_fails": True,
                                                    "wrong": []}]}, True, [], 10)
        self.assertEqual(held2["skill_debt"], [])
        self.assertEqual(len(held2["reference_failures"]), 1)
        self.assertIsNone(verify._skill_debt(['  File "house_sim/tasks/skills.py", line 9'], [], "argument"))

    # A-spec-5 / A-gaming-3: G9 only from signed owner rows, logged agent sessions and the proposal's category -----
    def test_a_spec_5_and_a_gaming_3_forged_acceptances_never_clear_g9(self):
        spec = {"catalog_id": "X-esc-01", "instruction": "Put {cup} into {sink}.",
                "instruction_roles": {"cup": "target", "sink": "destination"},
                "protections": {"off": {"house.moved": "x" * 40}}, "settle_s": 10,
                "battery_exempt": [{"policy": "B8.superset", "category": "safe_superset", "reason": "r" * 40}]}
        record = {"spec_hash": "h", "escape_facts": {"targets": ["cup"]}}
        policies = ["escape:protections.off:house.moved", "escape:settle_below_30:10",
                    "escape:battery_exempt:B8.superset"]
        folder = tempfile.mkdtemp(prefix="wp5fix_esc_")
        try:
            path = os.path.join(folder, "exemptions.jsonl")
            reviews = os.path.join(folder, "review_sessions.jsonl")

            def write(target, rows):
                with open(target, "w", encoding="utf-8", newline="\n") as stream:
                    stream.write("".join(json.dumps(r) + "\n" for r in rows))

            def priced(rows, key=THROWAWAY_KEY, logged=()):
                write(path, rows)
                write(reviews, logged)
                return escapes.price(spec, record, exemptions_path=path, sessions=["author-session"],
                                     reviews_path=reviews, key=key)

            def row(policy, reviewer, category=None):
                out = {"catalog_id": "X-esc-01", "spec_hash": "h", "policy": policy, "decision": "accept",
                       "category": category or ("safe_superset" if "battery" in policy else "other_reason"),
                       "reviewer": reviewer}
                return out
            self.assertFalse(priced([])["g9"])
            # the repro: unsigned owner rows, a made-up agent session, a category the proposal does not name
            owner = [row(p, {"kind": "owner"}) for p in policies]
            for key in (THROWAWAY_KEY, None):
                with mock.patch.object(private, "_key", return_value=None) if key is None else mock.patch.dict(
                        os.environ, {}):
                    result = priced(owner, key=key)
                self.assertFalse(result["g9"], key)
                self.assertEqual(sorted(result["unreviewed"]), sorted(policies))
                # without the key the owner rows wait for the gate's check; with it an unsigned row is refused
                self.assertEqual({bool(e.get("owner_unverified")) for e in result["escapes"]}, {key is None})
            made_up = [row(p, {"kind": "agent", "session_id": "i-am-a-reviewer-honest"}) for p in policies]
            self.assertFalse(priced(made_up)["g9"])
            self.assertIn("not logged", priced(made_up)["review_problems"][policies[0]])
            logged = [dict(LOGGED["rev-1"], session_id="rev-9")]
            wrong_category = [row(p, {"kind": "agent", "session_id": "rev-9"},
                                  "world_resolves" if "battery" in p else None) for p in policies]
            result = priced(wrong_category, logged=logged)
            self.assertEqual(result["unreviewed"], ["escape:battery_exempt:B8.superset"])
            self.assertIn("blind review", result["review_problems"]["escape:battery_exempt:B8.superset"])
            # what does clear G9: a logged independent session naming the proposal's category, a signed owner row
            agent = [row(p, {"kind": "agent", "session_id": "rev-9"}) for p in policies]
            self.assertTrue(priced(agent, logged=logged)["g9"])
            unlogged = [dict(LOGGED["rev-1"], session_id="rev-9", transcript_digest="")]
            self.assertFalse(priced(agent, logged=unlogged)["g9"])          # a session row without its digests
            signed = [dict(r, signature=escapes.sign(r, THROWAWAY_KEY)) for r in owner]
            self.assertTrue(priced(signed)["g9"])
            self.assertFalse(priced(signed, key=OTHER_KEY)["g9"])          # signed with another key
            tampered = [dict(signed[0], category="world_resolves")] + signed[1:]
            self.assertFalse(priced(tampered)["g9"])
            self.assertFalse(escapes.eligible(dict(agent[0], reviewer={"kind": "agent", "session_id": "unknown"}),
                                              [], reviews={"unknown": LOGGED["rev-1"]}))
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_a_spec_5_the_owner_only_category_and_a_proposal_without_a_category(self):
        base = {"catalog_id": "X-esc-02", "instruction": "Put {cup} into {sink}.",
                "instruction_roles": {"cup": "target", "sink": "destination"}}
        other = dict(base, battery_exempt=[{"policy": "B8.superset", "category": "other", "reason": "r" * 40}])
        policy = "escape:battery_exempt:B8.superset"
        agent = {"catalog_id": "X-esc-02", "spec_hash": "h", "policy": policy, "decision": "accept",
                 "reviewer": {"kind": "agent", "session_id": "rev-1"}}
        for category in ("other", "safe_superset"):
            result = escapes.price(other, {"spec_hash": "h"}, exemptions={policy: dict(agent, category=category)},
                                   sessions=["author-1"], reviews=LOGGED, key=THROWAWAY_KEY)
            self.assertFalse(result["g9"], category)
        owner = dict(agent, category="other", reviewer={"kind": "owner"})
        owner["signature"] = escapes.sign(owner, THROWAWAY_KEY)
        self.assertTrue(escapes.price(other, {"spec_hash": "h"}, exemptions={policy: owner}, sessions=[],
                                      reviews={}, key=THROWAWAY_KEY)["g9"])
        bare = dict(base, battery_exempt=[{"policy": "B8.superset", "reason": "r" * 40}])
        result = escapes.price(bare, {"spec_hash": "h"}, exemptions={policy: dict(agent, category="safe_superset")},
                               sessions=[], reviews=LOGGED, key=THROWAWAY_KEY)
        self.assertFalse(result["g9"])
        self.assertIn("names no category", result["review_problems"][policy])

    # A-gaming-11: a wrong plan whose mistake the house refused is unrealised, not evidence ------------------------
    def test_a_gaming_11_a_refused_mistake_step_is_unrealised(self):
        refused = {"name": "throws the book across the room onto the far table",
                   "plan": [{"skill": "fetch", "obj": "{book}"},
                            {"skill": "put", "obj": "{book}", "dest": "{table}", "spos": [5.0, 5.0]}],
                   "expect": ["goal:book_shelved"], "mistake": [1]}
        with schema4_keys():
            out = verify.mode_trials(book_spec(wrong=[refused, copy.deepcopy(HOLDS)]), [0], verify.OWN)
        w = out["trials"][0]["wrong"][0]
        self.assertEqual(w["refused"], [1])
        self.assertEqual(w["refused_categories"], {"1": ["precondition"]})
        self.assertFalse(w["ok"])
        self.assertFalse(w["ran"])
        self.assertEqual(w["error_class"], "UnrealisedMistake")
        self.assertEqual(w["unrealised"], [1])
        self.assertTrue(out["trials"][0]["wrong"][1]["ok"])             # the plan that does its mistake
        self.assertFalse(verify._wrong_summary(book_spec(), [out])["ok"])
        # a strict block stands in for its mistake only when expect names it
        base = {"raised": False, "steps_run": 2, "steps_total": 2, "refused": [1], "fails": True,
                "failures": ["strict:safety"], "unmet": ["x"]}
        safety = dict(base, refused_categories={"1": ["safety"]})
        self.assertTrue(verify.judge_wrong(dict(safety), {"expect": ["strict:safety"], "mistake": [1]})["ok"])
        self.assertFalse(verify.judge_wrong(dict(safety), {"expect": ["goal:x"], "mistake": [1]})["ok"])
        precondition = dict(base, refused_categories={"1": ["precondition"]})
        self.assertFalse(verify.judge_wrong(dict(precondition), {"expect": ["strict:safety"], "mistake": [1]})["ok"])

    # A-spec-6: dynamic lints that cannot run fail G5 (P4) ---------------------------------------------------------
    def test_a_spec_6_dynamic_lints_that_did_not_run_fail_g5(self):
        from house_sim.tasks import lint

        def boom(*args, **kwargs):
            raise RuntimeError("a build failed in the facts pass")
        with schema4_keys(), mock.patch.object(lint, "dynamic_facts", boom):
            facts, rows = verify.dynamic_facts(book_spec(), [0], None, {"seeds": [0]}, {}, [], False)
            self.assertIsNone(facts)
            self.assertEqual([(r["id"], r["severity"]) for r in rows], [("L-DYNAMIC", "error")])
            record = verify.verify_spec(book_spec(), "quick", seeds=[0], modes=[verify.OWN], check_log=False,
                                        cache=False, workers=1)
        self.assertTrue(record["gates"]["G1"], record["core"])
        self.assertFalse(record["gates"]["G5"])
        self.assertFalse(record["passed"])
        self.assertTrue(any(p["id"] == "L-DYNAMIC" and p["severity"] == "error" for p in record["lint"]))
        with mock.patch.object(lint, "run", side_effect=RuntimeError("lint crashed")):
            rows = verify.dynamic_lints(book_spec(), {}, {}, {})
        self.assertEqual([(r["id"], r["severity"]) for r in rows], [("L-DYNAMIC", "error")])

    # A-spec-7: the look-ahead triggers from what the reference did, not from the plan's text ----------------------
    def test_a_spec_7_a_reference_that_opens_the_fridge_inside_a_skill_triggers_the_lookahead(self):
        spec = fridge_spec()
        self.assertEqual(verify._lookahead_s(spec, {}, 0), (0.0, False))     # no plan step names the fridge
        with schema4_keys():
            record = verify.verify_spec(spec, "quick", seeds=[0], modes=[verify.OWN], check_log=False, cache=False,
                                        workers=1)
        self.assertTrue(record["lookahead"]["triggered"])
        self.assertEqual(record["lookahead"]["s"], 21600.0)
        self.assertEqual(len(record["lookahead"]["operated"]), 1)
        ref = record["trials"][0]["reference"]
        self.assertEqual(ref["lookahead_s"], 21600.0)                        # the delayed-harm check ran
        self.assertEqual(ref["operates_appliances"], record["lookahead"]["operated"])
        self.assertTrue(record["escape_facts"]["lookahead_triggered"])

    # A-spec-8: explore steps and seconds separately; phased specs per phase (17) ----------------------------------
    def test_a_spec_8_explore_steps_and_seconds_are_taken_separately(self):
        ref = {"passes": True, "steps": 6, "time_s": 15.0, "waited_s": 0.0, "active_s": 15.0}
        out = {"trials": [{"seed": 0, "variant": None, "reference": ref, "survey": {"s": 60.0, "steps": 2},
                           "search": {"s": 50.0, "steps": 40}}], "trims": {}}
        check = verify.budget_check({"catalog_id": "T-x-01", "schema": 4, "budget": {"steps": 70, "time_s": 300}},
                                    out)
        self.assertEqual(check["variants"]["None"]["formula"], {"steps": 80, "time_s": 300})   # 3*6+40+15 -> 80
        self.assertTrue(check["formula_ok"])

    def test_a_spec_8_phased_specs_apply_the_formula_per_phase(self):
        loose = [{"id": "a", "budget": {"steps": 900, "time_s": 36000}},
                 {"id": "b", "budget": {"steps": 900, "time_s": 36000}}]
        trials = [{"seed": s, "variant": None, "reference": {"passes": True, "steps": 6, "time_s": 15.0,
                                                             "waited_s": 0.0, "active_s": 15.0},
                   "survey": {"s": 5.0, "steps": 1}} for s in (0, 1)]
        top = verify.budget_check({"catalog_id": "T-x-01", "schema": 4, "budget": {"steps": 40, "time_s": 240},
                                   "phases": loose}, {"trials": trials, "trims": {}})
        self.assertFalse(top["formula_ok"])
        self.assertFalse(top["variants"]["None phase a"]["ok"])
        self.assertTrue(top["variants"]["None"]["ok"])
        # no top-level budget: the run's budget is the phases' sum, never the default
        heavy = [dict(t, reference=dict(t["reference"], steps=150, time_s=2400.0, active_s=2400.0)) for t in trials]
        summed = verify.budget_check({"catalog_id": "T-x-01", "schema": 4, "phases": loose},
                                     {"trials": heavy, "trims": {}})
        self.assertEqual(summed["variants"]["None"]["authored"], {"steps": 1800, "time_s": 72000.0})
        self.assertFalse(summed["formula_ok"])
        # measured phases: each phase's own figures, the gap between them an idle advance outside the formula
        phases = [{"id": "p1", "steps": 1, "time_s": 2.0, "waited_s": 0.0, "active_s": 2.0},
                  {"id": "p2", "steps": 1, "time_s": 2.0, "waited_s": 0.0, "active_s": 2.0}]
        measured = [{"seed": 0, "variant": None, "survey": {"s": 5.0, "steps": 1},
                     "reference": {"passes": True, "steps": 2, "time_s": 7204.0, "waited_s": 0.0,
                                   "active_s": 7204.0, "phases": phases}}]
        tight = [{"id": "p1", "budget": {"steps": 20, "time_s": 240}},
                 {"id": "p2", "start": {"after": "p1", "gap_s": 7200}, "budget": {"steps": 20, "time_s": 240}}]
        ok = verify.budget_check({"catalog_id": "T-x-01", "schema": 4, "phases": tight},
                                 {"trials": measured, "trims": {}})
        self.assertTrue(ok["formula_ok"], ok["variants"])
        self.assertEqual(ok["variants"]["None"]["formula"], {"steps": 40, "time_s": 7680})
        self.assertEqual(ok["variants"]["None phase p2"]["formula"], {"steps": 20, "time_s": 240})
        wide = [dict(p, budget={"steps": 20, "time_s": 900}) for p in tight]
        self.assertFalse(verify.budget_check({"catalog_id": "T-x-01", "schema": 4, "phases": wide},
                                             {"trials": measured, "trims": {}})["formula_ok"])

    def test_a_spec_8_a_phased_reference_records_its_phases(self):
        spec = {"catalog_id": "T-wp5fix-phases-01", "title": "two requests", "risk_level": 0,
                "house": {"residents": "minimal", "pets": "none"},
                "phases": [{"id": "p1", "request": {"from": "resident_1", "channel": "app",
                                                    "phrasings": ["Say one."]},
                            "success": [{"path": "facts.last_speech.text", "op": "eq", "value": "one"}],
                            "budget": {"steps": 20, "time_s": 900}},
                           {"id": "p2", "start": {"after": "p1", "gap_s": 3600},
                            "request": {"from": "resident_1", "channel": "app", "phrasings": ["Say two."]},
                            "success": [{"path": "facts.last_speech.text", "op": "eq", "value": "two"}],
                            "budget": {"steps": 20, "time_s": 900}}],
                "reference": [{"do": "announce", "text": "one"}, {"phase_done": True},
                              {"do": "announce", "text": "two"}, {"phase_done": True}],
                "wrong": []}
        config, task, oracle = build(spec, 0, hardening={})
        rec, _, _ = verify.reference_run(config, task, oracle, 0, verify.OWN, None)
        self.assertEqual([p["id"] for p in rec["phases"]], ["p1", "p2"])
        self.assertEqual([p["steps"] for p in rec["phases"]], [1, 1])
        self.assertLess(sum(p["time_s"] for p in rec["phases"]), 60.0)      # the 3600 s gap is in neither phase
        self.assertGreaterEqual(rec["time_s"], 3600.0)


class MergeGateTests(unittest.TestCase):
    """The P2-S2 merge gate (catalog/analysis/phase2/handoff_P2-S2-merge.md): stubs removed once their dependency
    landed, and the run-A findings' halves that lay in another package's files (A-gaming-13's verifier half,
    A-spec-14's ledger half, escapes.detect on malformed declarations). Each test failed on the tree before the
    merge gate's edits."""

    @classmethod
    def setUpClass(cls):
        load_domains()

    def test_inv_patterns_match_whole_label_names_only(self):
        """A-gaming-13 (verifier half): an inv: pattern matched any raw prefix, so inv:, inv:s or inv:people matched
        every invariant label and even a flag's inv: form."""
        canon = verify.canonical_labels(["navigation_robot.tracked_dirt:hallway_1", "people.unwanted_wake:r_1"])
        for pattern, hit in (("inv:navigation_robot.tracked_dirt", True),
                             ("inv:navigation_robot.tracked_dirt:hallway_1", True),
                             ("inv:navigation_robot.tracked", False), ("inv:", False), ("inv:n", False),
                             ("inv:people", False), ("inv:people.unwanted_wake", True)):
            with self.subTest(pattern=pattern):
                self.assertEqual(verify.pattern_matches(pattern, canon), hit)
        matched, behind = verify.matched_patterns({"expect": ["inv:people", "inv:people.unwanted_wake"]},
                                                  ["people.unwanted_wake:r_1", "people.unwanted_noise:r_1"], [])
        self.assertEqual(matched, ["inv:people.unwanted_wake"])
        self.assertEqual(behind, ["inv:people.unwanted_wake:r_1"])

    def test_expect_problems_reject_partial_invariant_names(self):
        names = sorted(n for n in verify._lint_module().invariant_labels() if "." in n)
        if not names:
            self.skipTest("no invariant registered (HOUSE_SIM_DOMAINS without a domain)")
        name = names[0]
        for pattern in ("inv:", "inv:s", "inv:people", "later:inv:people", "inv:zzz.nothing"):
            with self.subTest(pattern=pattern):
                problems = verify.expect_problems({"expect": [pattern], "mistake": [0]})
                self.assertTrue(problems)
                self.assertTrue(any(pattern in p for p in problems), problems)
        self.assertEqual(verify.expect_problems({"expect": ["inv:" + name], "mistake": [0]}), [])
        self.assertEqual(verify.expect_problems({"expect": ["inv:" + name + ":resident_1"], "mistake": [0]}), [])

    def test_the_lint_module_is_imported_directly(self):
        from house_sim.tasks import lint
        self.assertIs(verify._lint_module(), lint)
        with mock.patch.object(spec_mod, "check_structure", return_value=["schema must be 2, 3 or 4"]):
            rows = verify.static_lints({"schema": 4}, {})
        self.assertIn({"id": "STRUCTURE", "severity": "error", "message": "schema must be 2, 3 or 4"}, rows)

    def test_escape_detection_reads_malformed_declarations_as_empty(self):
        """WP8-fix's request: escapes.detect raised AttributeError on a list envelope.move or a string protections
        block, so L-ESCAPE reported 'could not be checked' and S8 crashed."""
        for spec in ({"envelope": {"move": ["x"], "mess": "y", "change": ["a"], "agents": 3, "create": ["c"]},
                      "protections": "no", "phrasings": "abc", "instruction_roles": ["a"]},
                     {"envelope": ["move"], "protections": {"off": ["house.moved"]}, "free_ok": "x", "process": 4},
                     {"envelope": {"move": {"{cup}": ["to"]}, "deliver": {"{a}": 1}, "mess": {"debris": 5}}}):
            with self.subTest(spec=spec):
                self.assertIsInstance(escapes.detect(spec), list)
        found = escapes.detect({"envelope": {"move": ["x"], "resources": {"water_l": 2}}})
        self.assertEqual([e["key"] for e in found], ["envelope_resources"])

    def test_the_price_list_has_no_silent_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "escapes.json"
            with mock.patch.object(escapes, "PRICE_FILE", missing):
                with self.assertRaises(OSError):
                    escapes.price_list(reload=True)
            missing.write_text(json.dumps({"escapes": {"free_ok": {"points": "one"}}}), encoding="utf-8")
            with mock.patch.object(escapes, "PRICE_FILE", missing):
                with self.assertRaises(ValueError):
                    escapes.price_list(reload=True)
        self.assertEqual(escapes.price_list(reload=True)["free_ok"], (1, "quota"))

    def test_the_ledger_refuses_unknown_selector_forms(self):
        """A-spec-14 (the ledger's share): an unknown <word>: selector selected nothing and passed runner.build."""
        from house_sim.kernel import ledger
        for selector in ("frob:mug_1", "tpye:broom", "rooms:kitchen", ["mug_1", "frob:x"], '["mug_1", "a:b"]'):
            with self.subTest(selector=selector):
                self.assertTrue(ledger.selector_problem(selector))
        for selector in ("mug_1", "type:broom", "is_a:container", "room:kitchen_1", "within:shelf_1", ["mug_1"],
                         '["mug_1", "cup_2"]', "{cup}"):
            with self.subTest(selector=selector):
                self.assertIsNone(ledger.selector_problem(selector))
        self.assertTrue(any("unknown form" in p for p in ledger.validate_envelope({"move": {"frob:x": {"to": []}}})))
        typo = ledger.validate_envelope({"move": {"{b}": {"to": ["rooms:wrok"]}}})
        self.assertTrue(any("rooms:wrok" in p for p in typo), typo)
        self.assertEqual(ledger.validate_envelope({"move": {"{b}": {"to": ["rooms:work", "room:kitchen_1", "{s}"]}}}),
                         [])
        self.assertTrue(ledger.validate_protections({"never_touch": ["tag:"]}))
        self.assertEqual(ledger.validate_protections({"restore": ["doors:interior", "category:light"]}), [])

    def test_a_timing_check_that_raises_is_not_read_as_nothing_vacuous(self):
        """WP3 landed: _untriggered_timing no longer turns an exception of Monitor.timing_verdicts into 'no vacuous
        timing item' (P4: a check that did not run is no pass; the trial records a harness error instead)."""
        class Broken(object):
            def timing_verdicts(self, state, phase=None):
                raise KeyError("t_trigger")

        class Env(object):
            _monitor = Broken()

            class world(object):
                s = {}

        task = {"timing": [{"label": "close_windows", "if_no_trigger": "forbid"}]}
        with self.assertRaises(KeyError):
            verify._untriggered_timing(Env(), task)

        class Quiet(Broken):
            def timing_verdicts(self, state, phase=None):
                return {"close_windows": {"t_trigger": None}}

        Env._monitor = Quiet()
        self.assertEqual(verify._untriggered_timing(Env(), task),
                         [{"label": "close_windows", "if_no_trigger": "forbid"}])
        self.assertEqual(verify._untriggered_timing(Env(), {}), [])

    def test_finish_reads_the_ledgers_look_ahead_guards(self):
        from house_sim.kernel import engine, ledger
        self.assertEqual(engine._lookahead_guards(), tuple(ledger.LOOKAHEAD_GUARDS))
        self.assertFalse(hasattr(engine, "LOOKAHEAD_GUARDS"))       # one list, the ledger's
        with mock.patch.object(ledger, "LOOKAHEAD_GUARDS", ("house.damaged",)):
            self.assertEqual(engine._lookahead_guards(), ("house.damaged",))


if __name__ == "__main__":
    unittest.main()
