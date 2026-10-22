"""Dataset exporter / validator (house_sim/eval/dataset.py, docs/DATASET.md). Offline: no network anywhere.

Specs are verified on the fly (verify_now=True, dev seeds) because reports/verification.jsonl goes stale while other
components edit the simulator. Every test runs with a temporary HOME/USERPROFILE and a throwaway HOUSE_AI_SPLIT_KEY,
so the user's real ~/.house_ai is never read; exports go to temporary directories only.
"""
import contextlib
import copy
import hashlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from house_sim.eval import dataset
from house_sim.eval import splits

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "datasets" / "sample"
# short reference plans in different domains; the first one that verifies and exports is used
CANDIDATES = ("T-5ad61b6881821b-01", "T-e780c9db18b3bf-01", "A-8910a09f802fbf-01")
TEST_KEY_HEX = hashlib.sha256(b"house_ai dataset unit-test split key (not a real key)").hexdigest()
FINGERPRINTS = ("code_fingerprint", "harness_fingerprint", "perception_fingerprint", "sha256")
_SAVED = {}


def _no_network(*args, **kwargs):
    raise AssertionError("network access attempted in an offline test")


def setUpModule():
    tmp = Path(tempfile.mkdtemp(prefix="house_ds_env_"))
    home = tmp / "home"
    home.mkdir()
    _SAVED["tmp"] = tmp
    _SAVED["env"] = mock.patch.dict(os.environ, {"HOME": str(home), "USERPROFILE": str(home),
                                                 splits.KEY_ENV: TEST_KEY_HEX,
                                                 splits.COMMITMENT_ENV: str(tmp / "test_commitment.json")})
    _SAVED["env"].start()
    splits._MANIFEST_CACHE.clear()
    _SAVED["urlopen"], _SAVED["create_connection"] = urllib.request.urlopen, socket.create_connection
    urllib.request.urlopen = socket.create_connection = _no_network
    if Path.home().resolve() != home.resolve():     # never run against the real profile
        tearDownModule()
        raise RuntimeError("could not redirect the home directory for the dataset tests")


def tearDownModule():
    urllib.request.urlopen = _SAVED.get("urlopen", urllib.request.urlopen)
    socket.create_connection = _SAVED.get("create_connection", socket.create_connection)
    if "env" in _SAVED:
        _SAVED.pop("env").stop()
    splits._MANIFEST_CACHE.clear()
    if "tmp" in _SAVED:
        shutil.rmtree(str(_SAVED.pop("tmp")), ignore_errors=True)


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path, records):
    Path(path).write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in records), encoding="utf-8")


def rewrite(folder, fmt, records, edit=None, name=None):
    """Write records to folder/<name or fmt.jsonl> and keep manifest.json consistent (sha256, bytes, record count),
    as a careful tamperer would; edit(manifest) changes the manifest further."""
    folder = Path(folder)
    path = folder / (name or fmt + ".jsonl")
    write_jsonl(path, records)
    mpath = folder / dataset.MANIFEST
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    raw = path.read_bytes()
    if name is None and fmt in manifest["formats"]:
        manifest["formats"][fmt].update(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw), records=len(records))
    if edit is not None:
        edit(manifest)
    mpath.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def strip_fingerprints(value):
    """Drop the fingerprints (and hashes of files that contain them): other components edit the simulator and the
    evaluation tooling concurrently, so two exports moments apart may carry different fingerprints with identical
    content."""
    if isinstance(value, dict):
        return {k: strip_fingerprints(v) for k, v in value.items() if k not in FINGERPRINTS}
    if isinstance(value, list):
        return [strip_fingerprints(v) for v in value]
    return value


class FirstDivergenceTests(unittest.TestCase):
    def steps(self, *pairs):
        return [{"obs": obs, "action": action} for obs, action in pairs]

    def test_different_action(self):
        a1, a2, a3, w3 = {"action": "approach", "target": "obj_00000001"}, {"action": "pick_up", "target": "obj_00000001"}, \
            {"action": "place", "target": "obj_00000001", "destination": "obj_00000002"}, \
            {"action": "place", "target": "obj_00000001", "destination": "obj_00000003"}
        ref = self.steps(("o1", a1), ("o2", a2), ("o3", a3))
        wrong = self.steps(("o1", a1), ("o2", a2), ("o3", w3), ("o4", a1))
        div = dataset.first_divergence(ref, wrong, "of", "wf")
        self.assertEqual(div["index"], 2)
        self.assertEqual(div["kind"], "different_action")
        self.assertEqual(div["chosen"], a3)
        self.assertEqual(div["rejected"], w3)
        self.assertTrue(div["same_observation"])

    def test_parameter_difference_is_a_divergence(self):
        ref = self.steps(("o1", {"action": "pick_up", "target": "obj_00000001", "grip": "gentle"}))
        wrong = self.steps(("o1", {"action": "pick_up", "target": "obj_00000001", "grip": "firm"}))
        div = dataset.first_divergence(ref, wrong, "of", "wf")
        self.assertEqual((div["index"], div["kind"]), (0, "different_action"))

    def test_observations_differ_flagged(self):
        ref = self.steps(("o1", {"action": "look"}), ("o2", {"action": "wait", "seconds": 5}))
        wrong = self.steps(("o1", {"action": "look"}), ("DIFFERENT", {"action": "wait", "seconds": 60}))
        div = dataset.first_divergence(ref, wrong, "of", "wf")
        self.assertEqual(div["index"], 1)
        self.assertFalse(div["same_observation"])

    def test_premature_done_and_extra_action(self):
        a, b = {"action": "look"}, {"action": "wait", "seconds": 5}
        div = dataset.first_divergence(self.steps(("o1", a), ("o2", b)), self.steps(("o1", a)), "ref_final", "o2")
        self.assertEqual((div["index"], div["kind"]), (1, "premature_done"))
        self.assertEqual(div["chosen"], b)
        self.assertEqual(div["rejected"], {"action": "done"})
        self.assertTrue(div["same_observation"])
        div = dataset.first_divergence(self.steps(("o1", a)), self.steps(("o1", a), ("o2", b)), "o2", "wrong_final")
        self.assertEqual((div["index"], div["kind"]), (1, "extra_action"))
        self.assertEqual(div["chosen"], {"action": "done"})
        self.assertEqual(div["rejected"], b)
        self.assertTrue(div["same_observation"])

    def test_identical_sequences(self):
        seq = self.steps(("o1", {"action": "look"}))
        self.assertIsNone(dataset.first_divergence(seq, list(seq), "f", "f"))


class SeedGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="house_ds_guard_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_test_split_refused(self):
        with self.assertRaisesRegex(ValueError, "test seed refused"):
            dataset.export(self.tmp / "out", split="test", catalog_ids=[CANDIDATES[0]])
        self.assertFalse((self.tmp / "out").exists())

    def test_private_seed_refused_before_anything_runs(self):
        private = splits.PRIVATE_BASE + 12345
        with mock.patch.object(dataset, "verify_records_now", side_effect=AssertionError("must not verify")):
            with self.assertRaisesRegex(ValueError, "test seed refused"):
                dataset.export(self.tmp / "out", seeds=[private], catalog_ids=[CANDIDATES[0]], verify_now=True)
        self.assertFalse((self.tmp / "out").exists())

    def test_splits_module_handing_out_private_seeds_is_still_refused(self):
        class Rogue(object):
            def __getattr__(self, name):
                return getattr(splits, name)

            def train_seeds(self, catalog_id, n, start=0):
                return [splits.PRIVATE_BASE + 7]
        records = [r for r in dataset.spec_records() if r["catalog_id"] == CANDIDATES[0]]
        self.assertTrue(records)
        with mock.patch.object(dataset, "splits_module", return_value=(Rogue(), dataset.SPLITS_SOURCE)), \
                mock.patch.object(dataset, "verified_from_evidence", return_value=(records, {})), \
                mock.patch.object(dataset, "_splits_verified_ids", return_value=None), \
                mock.patch.object(dataset, "_run_jobs", side_effect=AssertionError("must not run")):
            with self.assertRaisesRegex(ValueError, "test seed refused"):
                dataset.export(self.tmp / "out", catalog_ids=[CANDIDATES[0]])

    def test_train_seeds_are_in_the_train_range(self):
        seeds = splits.train_seeds("T-x", 5)
        self.assertTrue(all(splits.TRAIN_BASE <= s < splits.PRIVATE_BASE for s in seeds))
        self.assertEqual(dataset.train_schedule(splits, "T-x", 5), frozenset(seeds))
        with self.assertRaises(ValueError):
            dataset.refuse_test_seed("T-x", splits.PRIVATE_BASE)
        with self.assertRaises(ValueError):
            dataset.refuse_test_seed("T-x", 2 ** 40)          # above the private range: refused as well
        dataset.refuse_test_seed("T-x", seeds[0])

    def test_unreadable_private_manifest_refuses_every_seed(self):
        """Fail closed: when the private test manifest cannot be checked, no seed is accepted."""
        home = Path.home() / splits.PRIVATE_DIR
        home.mkdir(parents=True, exist_ok=True)
        (home / "test_manifest.json").write_text("{not json", encoding="utf-8")
        splits._MANIFEST_CACHE.clear()
        try:
            with self.assertRaisesRegex(ValueError, "test seed refused"):
                dataset.refuse_test_seed("T-x", splits.train_seeds("T-x", 1)[0])
        finally:
            shutil.rmtree(str(home), ignore_errors=True)
            splits._MANIFEST_CACHE.clear()

    def test_seed_outside_the_split_rejected(self):
        with self.assertRaisesRegex(ValueError, "train range"):
            dataset.export(self.tmp / "out", seeds=[5], catalog_ids=[CANDIDATES[0]])
        with self.assertRaisesRegex(ValueError, "not a dev seed"):
            dataset.export(self.tmp / "out", split="dev", seeds=[5], catalog_ids=[CANDIDATES[0]])
        with self.assertRaisesRegex(ValueError, "split must be"):
            dataset.export(self.tmp / "out", split="validation", catalog_ids=[CANDIDATES[0]])
        seed = splits.train_seeds(CANDIDATES[0], 1)[0]
        with self.assertRaisesRegex(ValueError, "duplicate seeds"):
            dataset.export(self.tmp / "out", seeds=[seed, seed], catalog_ids=[CANDIDATES[0]])
        self.assertFalse((self.tmp / "out").exists())


class SplitsRequiredTests(unittest.TestCase):
    """Regression: the exporter used to fall back silently to a local splits stand-in with another train_seeds formula
    (same code fingerprint, different data). splits.py is now required and the validator checks the seed schedule."""

    def test_no_stand_in(self):
        self.assertFalse(hasattr(dataset, "LOCAL_SPLITS"))
        self.assertFalse(hasattr(dataset, "_LocalSplits"))
        module, source = dataset.splits_module()
        self.assertIs(module, splits)
        self.assertEqual(source, "house_sim.eval.splits")

    def test_export_validate_and_cli_refuse_when_splits_cannot_be_imported(self):
        tmp = Path(tempfile.mkdtemp(prefix="house_ds_nosplits_"))
        try:
            with mock.patch.dict(sys.modules, {"house_sim.eval.splits": None}):    # import fails, as mid-edit
                with self.assertRaises(dataset.SplitsUnavailable):
                    dataset.export(tmp / "out", catalog_ids=[CANDIDATES[0]], seeds_per_entry=1, workers=1)
                report = dataset.validate(SAMPLE, deep=False)
                err = io.StringIO()
                with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                    code = dataset.main(["--export-dataset", str(tmp / "out"), "--catalog-ids", CANDIDATES[0]])
            self.assertFalse((tmp / "out").exists())
            self.assertFalse(report["ok"])
            self.assertIn("splits", report["error_counts"])
            self.assertEqual(code, 2)
            self.assertIn("cannot be imported", err.getvalue())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class EvidenceTests(unittest.TestCase):
    def test_only_current_passing_evidence_counts(self):
        from house_sim.tasks.verify import spec_hash
        records = [r for r in dataset.spec_records() if r["status"] == "CANDIDATE"][:4]
        self.assertEqual(len(records), 4)
        fp = "f" * 64
        evidence = {
            records[0]["catalog_id"]: {"passed": True, "implementation_hash": fp, "spec_hash": spec_hash(records[0]["spec"])},
            records[1]["catalog_id"]: {"passed": True, "implementation_hash": "0" * 64, "spec_hash": spec_hash(records[1]["spec"])},
            records[2]["catalog_id"]: {"passed": False, "implementation_hash": fp, "spec_hash": spec_hash(records[2]["spec"])},
        }
        verified, unverified = dataset.verified_from_evidence(records, evidence, fp)
        self.assertEqual([r["catalog_id"] for r in verified], [records[0]["catalog_id"]])
        self.assertIn("stale", unverified[records[1]["catalog_id"]])
        self.assertIn("failing", unverified[records[2]["catalog_id"]])
        self.assertIn("no verification evidence", unverified[records[3]["catalog_id"]])
        evidence[records[0]["catalog_id"]]["spec_hash"] = "1" * 64
        verified, unverified = dataset.verified_from_evidence(records, evidence, fp)
        self.assertEqual(verified, [])
        self.assertIn("spec changed", unverified[records[0]["catalog_id"]])

    def test_spec_authored_twice_is_excluded_and_reported(self):
        tmp = Path(tempfile.mkdtemp(prefix="house_ds_dup_"))
        try:
            (tmp / "catalog" / "raw").mkdir(parents=True)
            shutil.copy(str(ROOT / "catalog" / "raw" / "household_catalog.txt"), str(tmp / "catalog" / "raw"))
            (tmp / "tasks" / "authored").mkdir(parents=True)
            spec = next(r["spec"] for r in dataset.spec_records() if r["catalog_id"] == CANDIDATES[0])
            other = dict(spec, title="a conflicting edit")
            (tmp / "tasks" / "authored" / "a__x.json").write_text(json.dumps({"specs": [spec]}), encoding="utf-8")
            (tmp / "tasks" / "authored" / "b__y.json").write_text(json.dumps({"specs": [other]}), encoding="utf-8")
            records = dataset.spec_records(tmp)
            self.assertEqual([(r["catalog_id"], r["status"]) for r in records], [(CANDIDATES[0], "DUPLICATE")])

            def no_jobs(jobs, workers, progress=None):
                self.assertEqual(jobs, [])
                return []
            with mock.patch.object(dataset, "_run_jobs", side_effect=no_jobs):
                manifest = dataset.export(tmp / "out", catalog_ids=[CANDIDATES[0]], root=tmp, verify_now=True)
            self.assertEqual(manifest["verification"]["verified"], [])
            self.assertTrue(manifest["verification"]["unverified"][CANDIDATES[0]].startswith("excluded: authored twice"))
            # the validator rebuilds with the alternative whose hash matches the record
            from house_sim.tasks.verify import spec_hash
            ctx = dataset._DeepContext(tmp)
            self.assertEqual(ctx._spec(records[0], spec_hash(other))["title"], "a conflicting edit")
            self.assertEqual(ctx._spec(records[0], None), spec)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_stale_evidence_exports_nothing(self):
        tmp = Path(tempfile.mkdtemp(prefix="house_ds_stale_"))
        try:
            stale = {CANDIDATES[0]: {"catalog_id": CANDIDATES[0], "passed": True, "implementation_hash": "0" * 64}}
            with mock.patch.object(dataset, "load_evidence", return_value=stale):
                manifest = dataset.export(tmp, catalog_ids=[CANDIDATES[0]], workers=1)
            self.assertEqual(manifest["verification"]["verified"], [])
            self.assertIn(CANDIDATES[0], manifest["verification"]["unverified"])
            self.assertEqual(manifest["size"]["total_records"], 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class LeakCheckTests(unittest.TestCase):
    def test_key_leaks(self):
        found = dataset.key_leaks({"visible_entities": {"obj_12ab34cd": {"label": "jar", "_true_label": "salt"}},
                                   "nested": [{"secret_fields": ["x"]}]})
        self.assertEqual(sorted(k for _, _, k in found), ["_true_label", "secret_fields"])

    def test_id_tokens(self):
        value = {"message": "Picked up salt_jar_1 from kitchen_1", "target": "obj_12ab34cd", "label": "salt jar 1"}
        found = dataset.id_tokens(value, true_ids={"salt_jar_1"}, plain={"kitchen_1"})
        self.assertEqual([t for _, _, t in found], ["salt_jar_1"])
        # heuristic: <registered type>_<n> is an id even when the exact id set is unknown
        found = dataset.id_tokens({"m": "near fridge_2 and resident_1"}, types={"fridge"}, plain={"resident_1"})
        self.assertEqual([t for _, _, t in found], ["fridge_2"])
        self.assertEqual(dataset.id_tokens({"m": "obj_12345678"}, types={"obj"}), [])

    def test_static_id_check_knows_ids_not_shaped_type_n(self):
        """Regression: the static check (--no-deep, or a spec that cannot be rebuilt) missed true ids that are not
        <type>_<n>: fixed ids (main_water_valve), appliance parts (refrigerator_1_gasket) and room-derived ids."""
        types, vocab = dataset.registered_types(), dataset.static_id_vocabulary()
        self.assertGreater(vocab["houses"], 0)
        text = ("Closed main_water_valve and water_heater_inlet_valve, checked refrigerator_1_gasket, range_radiant_1_lf, "
                "dishwasher_1_filter, house_key_1 and floor_sunroom_3 next to linen_closet_1 via conn_hallway_1__linen_closet_1; "
                "the water_safe flag and hdmi1 port are fine")
        found = {t for _, _, t in dataset.id_tokens({"m": text}, types=types, vocab=vocab)}
        self.assertTrue({"main_water_valve", "water_heater_inlet_valve", "refrigerator_1_gasket", "range_radiant_1_lf",
                         "dishwasher_1_filter", "house_key_1", "floor_sunroom_3"} <= found, found)
        self.assertFalse({"linen_closet_1", "conn_hallway_1__linen_closet_1", "water_safe", "hdmi1"} & found, found)

    def test_entity_field_leaks(self):
        obs = {"visible_entities": {"obj_aaaaaaaa": {"label": "letter", "addressee": "x", "owner": "resident_1", "color": "white"}}}
        entities = {"letter_1": {"secret_fields": ["addressee"], "owner": "resident_1", "color": "white", "addressee": "x"}}
        found = dataset.entity_field_leaks(obs, {"obj_aaaaaaaa": "letter_1"}.get, entities,
                                           lambda rec, field: "never" if field == "owner" else "glance")
        self.assertEqual(sorted((k, f) for k, _, f in found), [("hidden", "owner"), ("secret", "addressee")])

    def test_live_audit_catches_ids_of_entities_removed_by_a_step(self):
        from types import SimpleNamespace
        state = {"entities": {"chili_2": {}, "bowl_1": {}}, "rooms": {"kitchen_1": {}}, "people": {}, "pets": {}}
        env = SimpleNamespace(world=SimpleNamespace(s=state), realism=None)
        audit = dataset._Audit()
        audit.command(env, {"action": "look"}, "step 1")          # chili_2 exists here
        self.assertEqual(audit.dropping(), [])
        del state["entities"]["chili_2"]                          # consumed by the next step...
        audit.message(env, "transfer", "", '{"source": "chili_2", "into": "kitchen_1"}', "step 2")
        self.assertEqual(audit.dropping(), ["id"])                # ...but still named in its message

    def test_perception_fingerprint_ignores_unrelated_code(self):
        fp = dataset.perception_fingerprint()
        self.assertRegex(fp, r"^[0-9a-f]{64}$")
        self.assertEqual(fp, dataset.perception_fingerprint())

    def test_compact_observation(self):
        obs = {"instruction": "do it", "battery": 90.0, "robot": {"battery": 90.0, "hands": {"left": None}, "stopped": False},
               "weather": {"c": 1}, "weather_now": {"c": 1}, "sounds": [], "dark": False, "steps": 0,
               "visible_entities": {"obj_aaaaaaaa": {"label": "cup", "color": None, "open": False, "pos": [0, 0]}}}
        compact = dataset.shape_observation(obs, "compact", "do it")
        self.assertEqual(compact, {"robot": {"battery": 90.0}, "weather": {"c": 1}, "steps": 0,
                                   "visible_entities": {"obj_aaaaaaaa": {"label": "cup", "pos": [0, 0]}}})
        self.assertIs(dataset.shape_observation(obs, "full"), obs)


class ExportRoundTripTests(unittest.TestCase):
    """One real export (verified now on the dev seeds), repeated to check determinism, then validated and tampered."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="house_ds_export_"))
        cls.cid, cls.manifest, tried = None, None, {}
        for cid in CANDIDATES:
            out = cls.tmp / ("a_" + cid)
            manifest = dataset.export(out, catalog_ids=[cid], seeds_per_entry=1, verify_now=True, workers=1)
            if manifest["formats"]["sft"]["records"] and manifest["formats"]["critique"]["records"]:
                cls.cid, cls.manifest, cls.out = cid, manifest, out
                break
            tried[cid] = (manifest["verification"]["unverified"], manifest["skipped"])
        if cls.cid is None:
            raise AssertionError("no candidate spec exported (domain code broken?): {}".format(tried))
        cls.out2 = cls.tmp / "b"
        cls.manifest2 = dataset.export(cls.out2, catalog_ids=[cls.cid], seeds_per_entry=1, verify_now=True, workers=1)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def copy(self, name):
        target = self.tmp / name
        shutil.copytree(str(self.out), str(target))
        return target

    def test_deterministic_output(self):
        for fmt in dataset.FORMATS:
            a, b = (self.out / (fmt + ".jsonl")).read_bytes(), (self.out2 / (fmt + ".jsonl")).read_bytes()
            if all(self.manifest[k] == self.manifest2[k] for k in dataset.FINGERPRINT_KEYS):
                self.assertEqual(a, b, fmt)
            else:   # simulator or tooling edited between the two exports by another component: compare content
                self.assertEqual(strip_fingerprints(read_jsonl(self.out / (fmt + ".jsonl"))),
                                 strip_fingerprints(read_jsonl(self.out2 / (fmt + ".jsonl"))), fmt)
        self.assertEqual(strip_fingerprints(self.manifest), strip_fingerprints(self.manifest2))
        self.assertEqual(strip_fingerprints(json.loads((self.out / "manifest.json").read_text(encoding="utf-8"))),
                         strip_fingerprints(self.manifest))

    def test_manifest_and_metadata(self):
        m = self.manifest
        self.assertEqual(m["schema"], dataset.SCHEMA)
        self.assertEqual(m["splits_source"], "house_sim.eval.splits")
        self.assertEqual(m["verification"]["mode"], "now")
        self.assertEqual(m["verification"]["verified"], [self.cid])
        self.assertEqual(m["options"]["perception"], "realistic")
        self.assertGreater(m["size"]["total_bytes"], 0)
        self.assertEqual(m["perception_fingerprint"], dataset.perception_fingerprint())
        for key in dataset.FINGERPRINT_KEYS:
            self.assertRegex(m[key], r"^[0-9a-f]{16,64}$")
        for fmt in dataset.FORMATS:
            info = m["formats"][fmt]
            self.assertEqual(info["records"], len(read_jsonl(self.out / info["file"])))
        schedule = splits.train_seeds(self.cid, 1)
        for fmt in dataset.FORMATS:
            for rec in read_jsonl(self.out / (fmt + ".jsonl")):
                meta = rec["meta"]
                for key in dataset.META_KEYS:
                    self.assertIn(key, meta)
                for key in dataset.FINGERPRINT_KEYS:
                    self.assertEqual(meta[key], m[key])
                self.assertEqual(meta["catalog_id"], self.cid)
                self.assertEqual(meta["split"], "train")
                self.assertEqual(meta["seed"], schedule[0])         # splits.train_seeds, never another formula
                self.assertEqual(meta["seed_tag"], "train:{}".format(meta["seed"]))
                self.assertEqual(rec["id"], dataset.content_id(rec))

    def test_record_shapes(self):
        sft = read_jsonl(self.out / "sft.jsonl")[0]
        # the record's instruction is the agent-facing one: compact mode dropped the observation's identical copy
        self.assertNotIn("instruction", sft["turns"][0]["observation"])
        self.assertEqual(sft["turns"][-1]["action"], {"action": "done"})
        self.assertTrue(all(t["result"]["ok"] for t in sft["turns"][:-1]))
        self.assertTrue(sft["outcome"]["success"])
        for turn in sft["turns"]:
            for handle in turn["observation"].get("visible_entities", {}):
                self.assertRegex(handle, dataset.HANDLE_RE)
        chosen = [t["action"] for t in sft["turns"][:-1]]
        for pair in read_jsonl(self.out / "pairs_trajectory.jsonl"):
            self.assertEqual(pair["chosen"], chosen)
            self.assertNotEqual(pair["rejected"], chosen)
            self.assertTrue(pair["rejected_reason"]["wrong_plan"])
        for pair in read_jsonl(self.out / "pairs_step.jsonl"):
            k = pair["reason"]["divergence_step"]
            self.assertEqual([h["action"] for h in pair["context"]["history"]], chosen[:k])
            self.assertEqual(pair["chosen"], chosen[k] if k < len(chosen) else {"action": "done"})
        for crit in read_jsonl(self.out / "critique.jsonl"):
            self.assertEqual(crit["verdict"], "fail")
            self.assertTrue(crit["reasons"]["wrong_plan"])

    def test_export_validates_clean(self):
        report = dataset.validate(self.out)
        self.assertTrue(report["ok"], report["errors"][:5])
        self.assertEqual(report["error_counts"], {})
        self.assertEqual(report["records"], self.manifest["size"]["total_records"])
        self.assertEqual(report["deep_checked_records"], report["records"])     # environments rebuilt for every record
        static = dataset.validate(self.out, deep=False)                          # the static id check: no false positives
        self.assertTrue(static["ok"], static["errors"][:5])

    def test_validator_flags_injected_true_id_and_secret_field(self):
        bad = self.copy("tampered")
        records = read_jsonl(bad / "sft.jsonl")
        rec = records[0]
        ctx = dataset._DeepContext(ROOT).get(rec["meta"]["catalog_id"], rec["meta"]["seed"])
        self.assertNotIn("error", ctx)
        true_id = sorted(ctx["ids"])[0]
        rec["turns"][0]["action"]["target"] = true_id                       # a true id in a command
        rec["turns"][0]["result"]["message"] = "Approached " + true_id        # and inside a message
        handle = sorted(rec["turns"][0]["observation"]["visible_entities"])[0]
        rec["turns"][0]["observation"]["visible_entities"][handle]["_true_type"] = "decoy"   # a secret field
        write_jsonl(bad / "sft.jsonl", records)
        report = dataset.validate(bad)
        self.assertFalse(report["ok"])
        self.assertGreaterEqual(report["error_counts"].get("id", 0), 2)
        self.assertGreaterEqual(report["error_counts"].get("secret", 0), 1)
        self.assertIn("hash_mismatch", report["error_counts"])
        self.assertIn("manifest", report["error_counts"])    # sha256 / size no longer match manifest.json
        self.assertFalse(dataset.validate(bad / "sft.jsonl")["ok"])

    def test_validator_flags_never_tier_field_and_true_id_keys(self):
        bad = self.copy("tampered_hidden")
        records = read_jsonl(bad / "pairs_trajectory.jsonl")
        rec = records[0]
        obs = rec["prompt"]["observation"]
        ctx = dataset._DeepContext(ROOT).get(rec["meta"]["catalog_id"], rec["meta"]["seed"])
        handle = next(h for h in sorted(obs["visible_entities"]) if ctx["resolve"](h) in ctx["entities"])
        obs["visible_entities"][handle]["owner"] = "resident_1"              # never-tier field
        obs["visible_entities"][handle]["secret_fields"] = ["owner"]
        eid = sorted(ctx["ids"])[-1]
        obs["visible_entities"][eid] = {"label": "thing"}                    # a true id as an entity key
        write_jsonl(bad / "pairs_trajectory.jsonl", records)
        report = dataset.validate(bad)
        self.assertFalse(report["ok"])
        self.assertGreaterEqual(report["error_counts"].get("id", 0), 1)
        self.assertGreaterEqual(report["error_counts"].get("secret", 0), 1)
        self.assertGreaterEqual(report["error_counts"].get("hidden", 0), 1)  # owner: never downgraded

    def test_validator_flags_test_seed_records(self):
        bad = self.copy("tampered_seed")
        records = read_jsonl(bad / "critique.jsonl")
        private = splits.PRIVATE_BASE + 99
        records[0]["meta"].update(seed=private, seed_tag="train:{}".format(private))
        records[-1]["meta"].update(split="test", seed_tag="test:0123456789ab")
        write_jsonl(bad / "critique.jsonl", records)
        report = dataset.validate(bad)
        self.assertFalse(report["ok"])
        self.assertGreaterEqual(report["error_counts"].get("test_seed", 0), 2)

    # ----- regression: unvalidated JSONL in a dataset directory (stale formats, smuggled test records) -----
    def test_reexport_refuses_a_nonempty_directory_and_overwrite_removes_stale_formats(self):
        target = self.copy("reexport")
        with self.assertRaisesRegex(ValueError, "not empty"):
            dataset.export(target, formats="sft", catalog_ids=[self.cid], seeds_per_entry=1, verify_now=True, workers=1)
        (target / "notes.txt").write_text("mine", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not part of a dataset export"):
            dataset.export(target, formats="sft", catalog_ids=[self.cid], seeds_per_entry=1, verify_now=True, workers=1,
                           overwrite=True)
        (target / "notes.txt").unlink()
        self.assertEqual(len(list(target.glob("*.jsonl"))), 4)          # nothing was touched by the refusals
        manifest = dataset.export(target, formats="sft", catalog_ids=[self.cid], seeds_per_entry=1, verify_now=True,
                                  workers=1, overwrite=True)
        self.assertEqual(sorted(p.name for p in target.iterdir()), ["manifest.json", "sft.jsonl"])
        self.assertEqual(sorted(manifest["formats"]), ["sft"])
        report = dataset.validate(target)
        self.assertTrue(report["ok"], report["errors"][:5])

    def test_validator_rejects_unlisted_jsonl_files(self):
        crit = read_jsonl(self.out / "critique.jsonl")[0]
        smuggled = json.loads(json.dumps(crit))
        smuggled["meta"].update(split="test", seed=900000123, seed_tag="test:0123456789ab")
        smuggled["id"] = dataset.content_id(smuggled)
        # (A) an extra JSONL file next to the listed ones
        a = self.copy("unlisted_a")
        write_jsonl(a / "extra_test.jsonl", [smuggled])
        report = dataset.validate(a, deep=False)
        self.assertFalse(report["ok"])
        self.assertGreaterEqual(report["error_counts"].get("unlisted_file", 0), 1)
        self.assertGreaterEqual(report["error_counts"].get("test_seed", 0), 1)       # validated anyway
        # (B) a format file whose format was removed from the manifest (everything else kept consistent)
        b = self.copy("unlisted_b")
        records = read_jsonl(b / "critique.jsonl") + [smuggled]

        def drop_critique(manifest):
            manifest["formats"].pop("critique")
            manifest["options"]["formats"].remove("critique")
            for entry in manifest["entries"].values():
                entry["records"].pop("critique", None)
        rewrite(b, "critique", records, edit=drop_critique)
        report = dataset.validate(b, deep=False)
        self.assertFalse(report["ok"])
        self.assertTrue(any(e["kind"] == "unlisted_file" and "does not list format critique" in e["detail"]
                            for e in report["errors"]), report["errors"][:5])
        self.assertGreaterEqual(report["error_counts"].get("test_seed", 0), 1)
        # (C) JSONL hidden in a subdirectory, (D) other data files; a plain note is only a warning
        c = self.copy("unlisted_c")
        (c / "nested").mkdir()
        shutil.copy(str(c / "sft.jsonl"), str(c / "nested" / "sft.jsonl"))
        report = dataset.validate(c, deep=False)
        self.assertFalse(report["ok"])
        self.assertIn("unlisted_file", report["error_counts"])
        d = self.copy("unlisted_d")
        (d / "README.txt").write_text("notes", encoding="utf-8")
        report = dataset.validate(d, deep=False)
        self.assertTrue(report["ok"], report["errors"][:5])
        self.assertIn("unexpected_file", report["warning_counts"])
        (d / "more.json").write_text("[]", encoding="utf-8")
        self.assertIn("unlisted_file", dataset.validate(d, deep=False)["error_counts"])
        # (E) a backup copy of a record file holding a test-split record is an error and is validated anyway
        e = self.copy("unlisted_e")
        write_jsonl(e / "critique.jsonl.bak", [smuggled])
        report = dataset.validate(e, deep=False)
        self.assertFalse(report["ok"])
        self.assertIn("unlisted_file", report["error_counts"])
        self.assertGreaterEqual(report["error_counts"].get("test_seed", 0), 1)

    def test_validator_checks_manifest_entries_against_records(self):
        bad = self.copy("entries")
        records = read_jsonl(bad / "critique.jsonl")
        rewrite(bad, "critique", records[1:])                 # a record deleted, sha / bytes / count updated
        report = dataset.validate(bad, deep=False)
        self.assertFalse(report["ok"])
        self.assertTrue(any("manifest.entries" in e["detail"] for e in report["errors"]), report["errors"][:5])
        # a whole verified entry deleted, manifest.entries edited to match: its seed is neither exported nor skipped
        gone = self.copy("entry_deleted")

        def drop(manifest):
            manifest["entries"].pop(self.cid)
            manifest["skipped"] = [s for s in manifest["skipped"] if s["catalog_id"] != self.cid]
            for info in manifest["formats"].values():
                info.update(duplicates_removed=0, capped=0)
        for fmt in dataset.FORMATS:
            rewrite(gone, fmt, [], edit=drop if fmt == dataset.FORMATS[-1] else None)
        report = dataset.validate(gone, deep=False)
        self.assertFalse(report["ok"])
        self.assertTrue(any("neither exported nor skipped" in e["detail"] for e in report["errors"]), report["errors"][:5])

    # ----- regression: never-tier leaks downgraded to warnings on any simulator edit -----
    def test_never_tier_leak_stays_an_error_after_unrelated_code_changes(self):
        bad = self.copy("stale_hidden")
        records = read_jsonl(bad / "pairs_trajectory.jsonl")
        rec = records[0]
        ctx = dataset._DeepContext(ROOT).get(rec["meta"]["catalog_id"], rec["meta"]["seed"], rec["meta"]["spec_hash"])
        obs = rec["prompt"]["observation"]

        def never(handle, field):
            true = ctx["entities"].get(ctx["resolve"](handle)) or {}
            return field in true and field not in (true.get("secret_fields") or ()) and ctx["tier"](true, field) == "never"
        handle = next(h for h in sorted(obs["visible_entities"]) if never(h, "value") and never(h, "risk"))
        true = ctx["entities"][ctx["resolve"](handle)]
        obs["visible_entities"][handle]["value"] = true["value"]      # core privacy field, its true value
        obs["visible_entities"][handle]["risk"] = true["risk"]        # never-tier simulation field, not core
        rec["id"] = dataset.content_id(rec)
        rewrite(bad, "pairs_trajectory", records)
        # another component edited unrelated simulator code: the code fingerprint moved, perception tiers did not
        with mock.patch("house_sim.audit.fingerprint", return_value="0" * 64):
            report = dataset.validate(bad)
        self.assertTrue(report["stale"])
        self.assertIn("stale", report["warning_counts"])
        self.assertFalse(report["ok"])
        self.assertGreaterEqual(report["error_counts"].get("hidden", 0), 2)
        self.assertNotIn("hidden", report["warning_counts"])
        # the type perception tiers changed: only the non-core never-tier field is downgraded
        with mock.patch.object(dataset, "perception_fingerprint", return_value="f" * 64):
            report = dataset.validate(bad)
        self.assertTrue(report["perception_stale"])
        self.assertFalse(report["ok"])
        self.assertEqual(report["error_counts"].get("hidden", 0), 1)
        self.assertEqual(report["warning_counts"].get("hidden", 0), 1)
        self.assertTrue(any(e["kind"] == "hidden" and e["detail"].endswith(".value is a hidden field") for e in report["errors"]))

    # ----- regression: seeds from another formula and fingerprints that do not cover the exporter -----
    def test_validator_requires_splits_source_and_seed_schedule(self):
        bad = self.copy("fallback")
        rewrite(bad, "sft", read_jsonl(bad / "sft.jsonl"), edit=lambda m: m.update(splits_source="local-fallback"))
        report = dataset.validate(bad, deep=False)
        self.assertFalse(report["ok"])
        self.assertIn("splits", report["error_counts"])
        bad = self.copy("off_schedule")
        records = read_jsonl(bad / "sft.jsonl")
        first = splits.train_seeds(self.cid, 1)[0]
        other = splits.TRAIN_BASE + (first - splits.TRAIN_BASE + splits.TRAIN_SPAN // 2) % splits.TRAIN_SPAN
        old_tag, new_tag = records[0]["meta"]["seed_tag"], "train:{}".format(other)
        records[0]["meta"].update(seed=other, seed_tag=new_tag)       # the content id does not cover meta

        def retag(manifest):
            tags = manifest["entries"][self.cid]["seed_tags"]
            if new_tag not in tags:
                tags.append(new_tag)
        rewrite(bad, "sft", records, edit=retag)
        report = dataset.validate(bad, deep=False)
        self.assertFalse(report["ok"])
        self.assertIn("off_schedule_seed", report["error_counts"])
        single = dataset.validate(bad / "sft.jsonl", deep=False)       # without a manifest: far off schedule, warned
        self.assertIn("off_schedule_seed", single["warning_counts"])
        self.assertNotEqual(old_tag, new_tag)

    def test_fingerprints_recorded_and_checked(self):
        with mock.patch("house_sim.audit.harness_fingerprint", return_value="1" * 64):
            report = dataset.validate(self.out, deep=False)
        self.assertTrue(report["ok"], report["errors"][:5])            # tooling changed: a warning, not an error
        self.assertTrue(report["harness_stale"])
        self.assertIn("stale_harness", report["warning_counts"])
        bad = self.copy("mixed")
        records = read_jsonl(bad / "critique.jsonl")
        records[0]["meta"]["harness_fingerprint"] = "2" * 64
        rewrite(bad, "critique", records)
        report = dataset.validate(bad, deep=False)
        self.assertFalse(report["ok"])
        self.assertIn("mixed_fingerprint", report["error_counts"])

    # ----- regression: static checks reported ok when the deep checks could not run -----
    def test_static_fallback_is_not_ok_unless_allowed(self):
        bad = self.copy("static_ids")
        records = read_jsonl(bad / "sft.jsonl")
        records[0]["turns"][0]["result"]["message"] = "Closed main_water_valve and refrigerator_1_gasket"
        records[0]["id"] = dataset.content_id(records[0])
        rewrite(bad, "sft", records)
        for deep in (False, True):
            report = dataset.validate(bad, deep=deep)
            self.assertFalse(report["ok"])
            self.assertGreaterEqual(report["error_counts"].get("id", 0), 2, (deep, report["errors"][:5]))
        # a record whose spec cannot be rebuilt (unknown / retired catalog id)
        single = self.tmp / "static_single"
        single.mkdir()
        clean = read_jsonl(self.out / "sft.jsonl")[0]
        clean["meta"]["catalog_id"] = "T-ffffffffffffff-01"
        write_jsonl(single / "sft.jsonl", [clean])
        report = dataset.validate(single / "sft.jsonl")
        self.assertFalse(report["ok"])
        self.assertIn("deep_unavailable", report["error_counts"])
        report = dataset.validate(single / "sft.jsonl", allow_static=True)
        self.assertTrue(report["ok"], report["errors"][:5])
        self.assertIn("deep_unavailable", report["warning_counts"])
        records[0]["meta"]["catalog_id"] = "T-ffffffffffffff-01"
        write_jsonl(single / "sft.jsonl", [records[0]])
        report = dataset.validate(single / "sft.jsonl", allow_static=True)
        self.assertFalse(report["ok"])
        self.assertGreaterEqual(report["error_counts"].get("id", 0), 2)

    def test_dedup_and_max_pairs(self):
        out = self.tmp / "capped"
        real = dataset._run_jobs

        def doubled(jobs, workers, progress=None):
            results = real(jobs, workers, progress)
            return results + results            # every record now appears twice
        with mock.patch.object(dataset, "_run_jobs", side_effect=doubled):
            manifest = dataset.export(out, catalog_ids=[self.cid], seeds_per_entry=1, max_pairs=1, verify_now=True,
                                      formats="pairs_trajectory,pairs_step,critique", workers=1)
        for fmt in ("pairs_trajectory", "pairs_step"):
            n = self.manifest["formats"][fmt]["records"]
            self.assertEqual(manifest["formats"][fmt]["duplicates_removed"], n)
            self.assertEqual(manifest["formats"][fmt]["records"], min(1, n))
            self.assertEqual(manifest["formats"][fmt]["capped"], max(0, n - 1))
        n = self.manifest["formats"]["critique"]["records"]
        self.assertEqual((manifest["formats"]["critique"]["records"], manifest["formats"]["critique"]["duplicates_removed"]), (n, n))
        self.assertEqual(strip_fingerprints(read_jsonl(out / "critique.jsonl")), strip_fingerprints(read_jsonl(self.out / "critique.jsonl")))
        self.assertFalse((out / "sft.jsonl").exists())
        self.assertTrue(dataset.validate(out)["ok"])

    def test_cap_is_round_robin_over_entries(self):
        recs = [{"meta": {"catalog_id": c}, "n": i} for i, c in enumerate("AAAB")]
        kept, dropped = dataset._cap(recs, 2)
        self.assertEqual(([r["n"] for r in kept], dropped), ([0, 3], 2))
        self.assertEqual(dataset._cap(recs, None), (recs, 0))


SAY = {"path": "facts.last_speech.text", "op": "eq"}
TWO_PHASES = {
    "catalog_id": "T-dataset-phases-01", "title": "two requests", "risk_level": 0, "fidelity": "S",
    "house": {"residents": "minimal", "pets": "none"},
    "phases": [
        {"id": "p1", "request": {"from": "resident_1", "channel": "app", "phrasings": ["Say one."]},
         "success": [dict(SAY, value="one")], "budget": {"steps": 20, "time_s": 900}},
        {"id": "p2", "start": {"after": "p1", "gap_s": 600},
         "request": {"from": "resident_1", "channel": "app", "phrasings": ["Say two."]},
         "success": [dict(SAY, value="two")], "budget": {"steps": 20, "time_s": 900}}],
    "reference": [{"do": "announce", "text": "one"}, {"phase_done": True}, {"do": "announce", "text": "two"},
                  {"phase_done": True}],
    "wrong": [{"name": "says one twice", "plan": [{"do": "announce", "text": "one"}, {"phase_done": True},
                                                  {"do": "announce", "text": "one"}, {"phase_done": True}]},
              {"name": "stops after the first request", "plan": [{"do": "announce", "text": "one"}]}]}


class MultiPhaseTests(unittest.TestCase):
    """TIME-EVAL request 7: a multi-phase episode is exported in the harness's protocol -- each phase ends with the
    agent's done, a turn whose result carries "phase" -- and validates."""

    @classmethod
    def setUpClass(cls):
        record = {"catalog_id": TWO_PHASES["catalog_id"], "spec": copy.deepcopy(TWO_PHASES), "section": "Test",
                  "kind": "task", "domain": "core", "family": None, "title": TWO_PHASES["title"], "risk_level": 0,
                  "fidelity": "S"}
        cls.job = {"record": record, "seed": 0, "split": "train", "seed_tag": "train:0", "obs": "compact",
                   "formats": list(dataset.FORMATS), "spec_hash": "0" * 64, "fingerprint": "1" * 64,
                   "harness_fingerprint": "2" * 64, "perception_fingerprint": "3" * 64}
        cls.out = dataset._run_job(cls.job)

    def test_phase_ends_are_done_turns_carrying_the_phase(self):
        self.assertTrue(self.out["exported"], self.out["skipped"])
        sft = self.out["records"]["sft"][0]
        actions = [t["action"]["action"] for t in sft["turns"]]
        self.assertEqual(actions, ["announce", "done", "announce", "done"])     # no extra done after the last phase
        ends = [t["result"]["phase"] for t in sft["turns"] if t["action"]["action"] == "done"]
        self.assertEqual(ends, [{"ended": "p1", "active": "p2", "waiting": False},
                                {"ended": "p2", "active": None, "waiting": False}])
        self.assertEqual(sft["meta"]["phases"], ["p1", "p2"])
        self.assertEqual([p["id"] for p in sft["outcome"]["phases"]], ["p1", "p2"])
        self.assertTrue(all(p["success"] for p in sft["outcome"]["phases"]))
        # the observation after the first phase end carries the second phase's instruction
        self.assertNotEqual(sft["turns"][2]["observation"].get("request"), sft["turns"][0]["observation"].get("request"))
        for fmt in dataset.FORMATS:
            for rec in self.out["records"][fmt]:
                self.assertEqual(dataset._schema_problems(rec, fmt), [], (fmt, rec.get("meta")))

    def test_wrong_plans_and_their_pairs(self):
        critiques = {c["reasons"]["wrong_plan"]: c for c in self.out["records"]["critique"]}
        self.assertEqual(sorted(critiques), ["says one twice", "stops after the first request"])
        self.assertIn("p2:0", critiques["says one twice"]["reasons"]["unmet_success"])
        steps = self.out["records"]["pairs_step"]
        self.assertEqual([p["reason"]["wrong_plan"] for p in steps], ["says one twice"])
        self.assertEqual(steps[0]["reason"]["divergence_step"], 2)
        # the plan that stops where the reference ends a phase gives no step pair (the same done command)
        self.assertTrue(any("ends a phase" in s["reason"] for s in self.out["skipped"]), self.out["skipped"])

    def test_the_validator_accepts_phase_ends_and_refuses_other_early_dones(self):
        sft = copy.deepcopy(self.out["records"]["sft"][0])
        self.assertEqual(dataset._schema_problems(sft, "sft"), [])
        sft["turns"][1]["result"].pop("phase")
        problems = dataset._schema_problems(sft, "sft")
        self.assertTrue(any("done is only allowed as the last turn" in p for p in problems), problems)


class SampleTests(unittest.TestCase):
    def test_sample_validates_clean(self):
        self.assertTrue((SAMPLE / "manifest.json").exists(), "datasets/sample is missing")
        report = dataset.validate(SAMPLE)
        self.assertTrue(report["ok"], report["errors"][:5])
        self.assertEqual(report["error_counts"], {})
        self.assertGreater(report["records"], 0)
        self.assertEqual(report["deep_checked_records"], report["records"])

    def test_sample_is_small_train_only_and_covers_all_formats(self):
        manifest = json.loads((SAMPLE / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema"], dataset.SCHEMA)
        self.assertEqual(manifest["splits_source"], "house_sim.eval.splits")
        self.assertEqual(manifest["options"]["split"], "train")
        self.assertEqual(manifest["options"]["seeds_per_entry"], 1)
        self.assertLess(manifest["size"]["total_bytes"], 4 * 1024 * 1024)
        self.assertGreaterEqual(len(manifest["entries"]), 3)
        for fmt in dataset.FORMATS:
            self.assertGreater(manifest["formats"][fmt]["records"], 0, fmt)
        for cid, entry in manifest["entries"].items():
            self.assertEqual(entry["seed_tags"], ["train:{}".format(splits.train_seeds(cid, 1)[0])])
        self.assertEqual(sorted(p.name for p in SAMPLE.iterdir() if p.name.lower() not in dataset.IGNORED_FILES),
                         sorted(["manifest.json"] + [f + ".jsonl" for f in dataset.FORMATS]))


class CliTests(unittest.TestCase):
    def test_cli_refuses_test_split(self):
        tmp = Path(tempfile.mkdtemp(prefix="house_ds_cli_"))
        try:
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(dataset.main(["--export-dataset", str(tmp / "o"), "--split", "test"]), 2)
                self.assertEqual(dataset.main(["--export-dataset", str(tmp / "o"), "--seeds", str(splits.PRIVATE_BASE + 3),
                                               "--catalog-ids", CANDIDATES[0]]), 2)
            self.assertIn("test seed refused", err.getvalue())
            self.assertFalse((tmp / "o").exists())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_cli_refuses_nonempty_output_without_overwrite(self):
        tmp = Path(tempfile.mkdtemp(prefix="house_ds_cli_"))
        try:
            (tmp / "o").mkdir()
            (tmp / "o" / "sft.jsonl").write_text("", encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()), \
                    mock.patch.object(dataset, "verify_records_now", side_effect=AssertionError("must not verify")):
                self.assertEqual(dataset.main(["--export-dataset", str(tmp / "o"), "--catalog-ids", CANDIDATES[0],
                                               "--verify-now"]), 2)
            self.assertIn("--overwrite", err.getvalue())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_cli_dispatch_and_wrapper_validate_sample(self):
        from house_sim.eval import cli as eval_cli
        self.assertTrue(eval_cli.wants(["--validate-dataset", str(SAMPLE)]))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(eval_cli.main(["--validate-dataset", str(SAMPLE), "--no-deep", "--quiet"]), 0)
        self.assertIn("OK", out.getvalue())
        proc = subprocess.run([sys.executable, str(ROOT / "tools" / "validate_dataset.py"), str(SAMPLE), "--quiet"],
                              cwd=str(ROOT), capture_output=True, text=True, timeout=600)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
