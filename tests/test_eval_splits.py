"""Splits and the hidden test set (house_sim/eval/splits.py).

Fully offline. Every test runs with a temporary HOME/USERPROFILE and a fixed throwaway key in HOUSE_AI_SPLIT_KEY, and
the public commitment path is redirected to a temp dir, so the user's real ~/.house_ai and the repository's
reports/test_commitment.json are never touched.
"""
import contextlib
import fnmatch
import hashlib
import hmac
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from house_sim.eval import splits as S

ROOT = Path(__file__).resolve().parents[1]
TEST_KEY_HEX = hashlib.sha256(b"house_ai unit-test split key (not a real key)").hexdigest()
OTHER_KEY_HEX = hashlib.sha256(b"another unit-test split key").hexdigest()
SHRED = "T-dde0975c7a26b8-01"     # "Shred documents when instructed": 2 variants with different instructions
PAPER = "T-0242c820d56e7e-01"     # "Refill printer paper": no variants
EXAMPLES = ROOT / "tasks" / "authored" / "admin_inventory__examples.json"
PRIVATE_PHRASINGS = ["The office printer has run dry of paper; please top up its tray.",
                     "Somebody needs to feed the printer in the office some paper. Could you do that?"]


def example_specs(*ids):
    specs = {s["catalog_id"]: s for s in json.loads(EXAMPLES.read_text(encoding="utf-8"))["specs"]}
    return [specs[i] for i in ids]


@contextlib.contextmanager
def fresh_home(base, keep_key_env=False):
    """A new empty home directory; the env key is removed unless keep_key_env."""
    home = Path(tempfile.mkdtemp(dir=str(base), prefix="home_"))
    with mock.patch.dict(os.environ, {"HOME": str(home), "USERPROFILE": str(home)}):
        if not keep_key_env:
            os.environ.pop(S.KEY_ENV, None)
        S._MANIFEST_CACHE.clear()
        try:
            yield home
        finally:
            S._MANIFEST_CACHE.clear()


def run_cli(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = S.main(argv)
    return code, out.getvalue()


class Isolated(unittest.TestCase):
    """Temp HOME/USERPROFILE, a fixed env key, and the commitment path redirected to the temp dir."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="house_ai_splits_")
        cls.tmp = Path(cls._tmp.name)
        cls.home = cls.tmp / "home"
        cls.home.mkdir()
        cls.commit_path = cls.tmp / "reports" / "test_commitment.json"
        cls._patchers = [mock.patch.dict(os.environ, {"HOME": str(cls.home), "USERPROFILE": str(cls.home),
                                                      S.KEY_ENV: TEST_KEY_HEX}),
                         mock.patch.object(S, "COMMITMENT_FILE", cls.commit_path)]
        for patcher in cls._patchers:
            patcher.start()
        os.environ.pop(S.COMMITMENT_ENV, None)    # restored by the env patcher; the redirect above must win
        S._MANIFEST_CACHE.clear()
        if Path.home().resolve() != cls.home.resolve():   # never run against the real profile
            cls.tearDownClass()
            raise RuntimeError("could not redirect the home directory for the splits tests")
        cls.key = bytes.fromhex(TEST_KEY_HEX)

    @classmethod
    def tearDownClass(cls):
        for patcher in reversed(cls._patchers):
            try:
                patcher.stop()
            except RuntimeError:
                pass
        S._MANIFEST_CACHE.clear()
        cls._tmp.cleanup()


class DerivationAndKeyTests(Isolated):
    def test_constants_and_ranges(self):
        from house_sim.tasks.verify import SEEDS
        self.assertEqual(S.DEV_SEEDS, tuple(SEEDS))
        self.assertFalse(set(S.DEV_EXTRA) & set(S.DEV_SEEDS))
        self.assertTrue(all(0 <= s < S.TRAIN_BASE for s in S.DEV_SEEDS + S.DEV_EXTRA))
        self.assertEqual((S.TRAIN_BASE, S.PRIVATE_BASE, S.PRIVATE_END), (1_000_000, 900_000_000, 2 ** 31 - 1))
        self.assertEqual(S.COMMITMENT_FILE, self.commit_path)
        self.assertEqual(S.key_path(), self.home / ".house_ai" / "split_key")
        self.assertEqual(S.manifest_path(), self.home / ".house_ai" / "test_manifest.json")

    def test_train_seeds_deterministic_in_range_and_cycle_variants(self):
        a, b = S.train_seeds(SHRED, 50), S.train_seeds(SHRED, 50)
        self.assertEqual(a, b)
        self.assertEqual(len(set(a)), 50)
        self.assertTrue(all(S.TRAIN_BASE <= s < S.PRIVATE_BASE for s in a))
        self.assertEqual({s % 2 for s in a[:2]}, {0, 1})
        self.assertEqual({s % 3 for s in a[:3]}, {0, 1, 2})
        self.assertEqual(S.train_seeds(SHRED, 3, start=2), a[2:5])
        self.assertNotEqual(S.train_seeds(PAPER, 5), a[:5])
        self.assertFalse(any(S.is_test_seed(SHRED, s) for s in a))
        self.assertEqual(S.dev_seeds(SHRED), list(S.DEV_SEEDS))
        self.assertEqual(S.dev_seeds(SHRED, extra=True), list(S.DEV_SEEDS + S.DEV_EXTRA))

    def test_test_candidates_are_hmac_derived_and_deterministic(self):
        seeds = S.test_candidates(SHRED, 6, self.key)
        manual = []
        for i in range(6):
            mac = hmac.new(self.key, "{}:{}".format(SHRED, i).encode(), hashlib.sha256).digest()
            manual.append(S.PRIVATE_BASE + int.from_bytes(mac[:8], "big") % (S.PRIVATE_END - S.PRIVATE_BASE))
        self.assertEqual(seeds, manual)
        self.assertEqual(seeds, S.test_candidates(SHRED, 6, TEST_KEY_HEX))
        self.assertEqual(S.test_candidates(SHRED, 3, self.key), seeds[:3])
        self.assertTrue(all(S.is_private_seed(s) for s in seeds))
        self.assertNotEqual(S.test_candidates(SHRED, 6, bytes.fromhex(OTHER_KEY_HEX)), seeds)
        self.assertNotEqual(S.test_candidates(PAPER, 6, self.key), seeds)
        self.assertEqual(S.test_candidates(SHRED, 6, None), seeds)   # None -> load_key() (env key here)

    def test_key_from_env_and_digest(self):
        self.assertEqual(S.load_key(), self.key)
        self.assertEqual(S.key_source(), "env")
        self.assertEqual(S.key_digest(self.key), hashlib.sha256(self.key).hexdigest())

    def test_missing_key_raises_clear_error(self):
        with fresh_home(self.tmp):
            self.assertIsNone(S.load_key(required=False))
            self.assertIsNone(S.key_source())
            with self.assertRaises(S.SplitKeyMissing) as caught:
                S.load_key()
            message = str(caught.exception)
            self.assertIn("--init-split-key", message)
            self.assertIn(S.KEY_ENV, message)
            with self.assertRaises(S.SplitKeyMissing):
                S.test_candidates(SHRED, 2, None)
            with self.assertRaises(S.SplitKeyMissing):
                S.episodes("test", example_specs(PAPER))
            with self.assertRaises(S.SplitKeyMissing):
                S.build_test_manifest(example_specs(PAPER), k=1, workers=1)
            code, out = run_cli(["--build-test-manifest", "--file", str(EXAMPLES), "--only", PAPER])
            self.assertEqual(code, 2)
            self.assertIn("No split key", out)

    def test_init_key_creates_refuses_overwrite_and_force_replaces(self):
        with fresh_home(self.tmp) as home:
            path = S.init_key()
            self.assertEqual(path, home / ".house_ai" / "split_key")
            text = path.read_text(encoding="utf-8").strip()
            self.assertEqual(len(text), 64)
            self.assertEqual(S.load_key(), bytes.fromhex(text))
            self.assertEqual(S.key_source(), "file")
            with self.assertRaises(FileExistsError):
                S.init_key()
            S.init_key(force=True)
            self.assertNotEqual(path.read_text(encoding="utf-8").strip(), text)
        with fresh_home(self.tmp) as home:
            code, out = run_cli(["--init-split-key"])
            self.assertEqual(code, 0)
            secret = (home / ".house_ai" / "split_key").read_text(encoding="utf-8").strip()
            self.assertNotIn(secret, out)
            self.assertIn(hashlib.sha256(bytes.fromhex(secret)).hexdigest(), out)   # the digest is public
            code, out = run_cli(["--init-split-key"])
            self.assertEqual(code, 2)
            self.assertNotIn(secret, out)

    def test_invalid_keys_rejected_without_echo(self):
        with mock.patch.dict(os.environ, {S.KEY_ENV: "not-hex-SECRETVALUE"}):
            with self.assertRaises(ValueError) as caught:
                S.load_key()
            self.assertNotIn("SECRETVALUE", str(caught.exception))
        with mock.patch.dict(os.environ, {S.KEY_ENV: "abcd"}):
            with self.assertRaises(ValueError):
                S.load_key()

    def test_private_files_never_written_inside_repository(self):
        target = ROOT / "reports" / "unit_test_manifest_should_not_exist.json"
        with self.assertRaises(ValueError):
            S.save_manifest({}, self.key, path=target, commitment_path=self.tmp / "c.json")
        self.assertFalse(target.exists())


class GuardTests(Isolated):
    def test_private_range_refused_without_key_or_manifest(self):
        with fresh_home(self.tmp):
            for seed in (S.PRIVATE_BASE, S.PRIVATE_BASE + 12345, S.PRIVATE_END - 1, str(S.PRIVATE_BASE + 7)):
                self.assertTrue(S.is_test_seed(PAPER, seed))
                with self.assertRaises(ValueError) as caught:
                    S.guard_not_test(PAPER, seed)
                self.assertEqual(str(caught.exception), "test seed refused")
            self.assertFalse(S.is_private_seed(S.PRIVATE_END))
            self.assertFalse(S.is_private_seed(S.PRIVATE_BASE - 1))
            for seed in S.DEV_SEEDS + S.DEV_EXTRA + (S.TRAIN_BASE, S.PRIVATE_BASE - 1):
                S.guard_not_test(PAPER, seed)
            for split in ("train", "dev"):
                with self.assertRaises(ValueError):
                    S.episodes(split, example_specs(PAPER), seeds=[5, S.PRIVATE_BASE + 1])

    def test_manifest_listed_seed_refused(self):
        with fresh_home(self.tmp):
            doc = {"format": S.MANIFEST_FORMAT, "key_digest": S.key_digest(self.key),
                   "entries": {SHRED: {"seeds": [123457], "variants": [1], "phrasings": [], "skipped": []}}}
            S.manifest_path().parent.mkdir(parents=True)
            S.manifest_path().write_text(json.dumps(doc), encoding="utf-8")
            self.assertTrue(S.is_test_seed(SHRED, 123457))
            self.assertTrue(S.is_test_seed(PAPER, 123457))    # a seed fixes the house: refused for every entry
            with self.assertRaises(ValueError):
                S.guard_not_test(PAPER, 123457)
            with self.assertRaises(S.SplitKeyMissing):
                S.seed_tag(SHRED, 123457)                    # test tags need the key (the env key is removed here)
            tag = S.seed_tag(SHRED, 123457, key=self.key)
            self.assertTrue(tag.startswith("test:"))
            self.assertNotIn("123457", tag)
            S.manifest_path().unlink()
            self.assertFalse(S.is_test_seed(SHRED, 123457))
            S.manifest_path().write_text("{not json", encoding="utf-8")
            with self.assertRaises(ValueError):
                S.is_test_seed(SHRED, 5)                     # unreadable manifest: fail closed

    def test_seed_tags(self):
        self.assertEqual(S.seed_tag(PAPER, 0), "dev:0")
        self.assertEqual(S.seed_tag(PAPER, 55), "dev:55")
        self.assertEqual(S.seed_tag(PAPER, S.TRAIN_BASE + 9), "train:{}".format(S.TRAIN_BASE + 9))
        self.assertEqual(S.seed_tag(PAPER, 500), "seed:500")
        seed = S.test_candidates(PAPER, 1, self.key)[0]
        tag = S.seed_tag(PAPER, seed)
        self.assertRegex(tag, r"^test:[0-9a-f]{12}$")
        self.assertEqual(tag, S.seed_tag(PAPER, seed, key=self.key))
        self.assertNotIn(str(seed), tag)
        self.assertNotEqual(tag, S.seed_tag(PAPER, seed, key=bytes.fromhex(OTHER_KEY_HEX)))
        self.assertNotEqual(tag, S.seed_tag(SHRED, seed))
        self.assertNotIn(":", S.safe_tag(tag))


class SelectionTests(unittest.TestCase):
    """The seed-selection walk, with a fake verifier (no simulator)."""

    def walk(self, candidates, k, n_variants, failing=(), limit=S.FAIL_LIMIT):
        calls = []

        def trial(seed):
            calls.append(seed)
            return {"passed": seed not in failing, "initial_fails": True, "idle_fails": True,
                    "reference_passes": seed not in failing, "reference_error": "plan refused", "wrong_fails": True,
                    "wrong_ran": True}
        return S._select(candidates, k, n_variants, trial, fail_limit=limit), calls

    def test_failures_skipped_deterministically_with_reason(self):
        candidates = [30, 31, 32, 33, 34, 35, 36, 37, 38, 39]
        result, calls = self.walk(candidates, 4, 3, failing={31, 33})
        again, _ = self.walk(candidates, 4, 3, failing={31, 33})
        self.assertEqual(result, again)
        self.assertIsNone(result["error"])
        self.assertEqual(result["seeds"], [30, 32, 34, 35])
        self.assertEqual(result["variants"], [s % 3 for s in result["seeds"]])
        self.assertEqual(set(result["variants"]), {0, 1, 2})
        failed = [s for s in result["skipped"] if s["reason"] == "verification_failed"]
        self.assertEqual([(s["index"], s["seed"]) for s in failed], [(1, 31), (3, 33)])
        self.assertTrue(all("reference_passes" in s["detail"] for s in failed))

    def test_variant_quota_makes_every_variant_appear(self):
        candidates = [2, 4, 6, 8, 10, 12, 15, 16]
        result, calls = self.walk(candidates, 3, 2)
        self.assertIsNone(result["error"])
        self.assertEqual(result["seeds"], [2, 4, 15])
        self.assertEqual(set(result["variants"]), {0, 1})
        quota = [s["seed"] for s in result["skipped"] if s["reason"] == "variant_quota"]
        self.assertEqual(quota, [6, 8, 10, 12])
        self.assertFalse(set(quota) & set(calls))            # passed over without running the verifier

    def test_broken_variant_or_exhausted_pool_marks_incomplete(self):
        candidates = list(range(100, 160))
        result, _ = self.walk(candidates, 4, 2, failing={s for s in candidates if s % 2}, limit=5)
        self.assertIn("variant 1", result["error"])
        self.assertEqual(result["missing_variants"], [1])
        result, _ = self.walk([1, 2, 3], 3, 0, failing={2})
        self.assertIn("exhausted", result["error"])
        result, _ = self.walk(list(range(20)), 3, 0, failing=set(range(20)), limit=4)
        self.assertIn("the entry", result["error"])

    def test_phrasing_assignment_and_pool_checks(self):
        from house_sim.tasks.spec import expand
        shred, paper = (expand(s) for s in example_specs(SHRED, PAPER))
        flat, index, public = S._assign_phrasings(paper, [11, 12, 13], [None] * 3, ["a", "b"])
        self.assertEqual((flat, index, public), (["a", "b"], [0, 1, 0], False))
        flat, index, public = S._assign_phrasings(shred, [1, 2, 3, 4], [0, 1, 0, 1], {"0": ["x", "y"], "1": ["z"]})
        self.assertEqual([flat[i] for i in index], ["x", "z", "y", "z"])
        flat, index, public = S._assign_phrasings(shred, [1, 2], [0, 1], None)
        self.assertTrue(public)
        self.assertEqual(index, [None, None])
        self.assertEqual(len(flat), 6)                       # both variants' public phrasings
        with self.assertRaises(ValueError):
            S._check_pool(SHRED, shred, ["one phrasing for two different instructions"])
        with self.assertRaises(ValueError):
            S._check_pool(SHRED, shred, {"0": ["only variant 0"]})
        S._check_pool(SHRED, shred, {"0": ["a"], "1": ["b"]})
        S._check_pool(PAPER, paper, ["a"])


class ManifestBuildTests(Isolated):
    """One small real build (two example specs, k=2) shared by the tests below."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            cls.specs = example_specs(SHRED, PAPER)
            S.phrasings_path().parent.mkdir(parents=True, exist_ok=True)
            S.phrasings_path().write_text(json.dumps({PAPER: PRIVATE_PHRASINGS}), encoding="utf-8")
            cls.manifest = S.build_test_manifest(cls.specs, k=2, workers=1)
            cls.public = S.save_manifest(cls.manifest)
        except BaseException:
            super().tearDownClass()
            raise

    def test_hidden_seeds_valid_private_and_variants_covered(self):
        from house_sim.tasks.verify import spec_hash
        for spec in self.specs:
            cid = spec["catalog_id"]
            entry = self.manifest[cid]
            self.assertTrue(entry["complete"], entry.get("error"))
            self.assertEqual(len(entry["seeds"]), 2)
            self.assertTrue(all(S.is_private_seed(s) for s in entry["seeds"]))
            stream = S.test_candidates(cid, entry["candidates_considered"], self.key)
            self.assertTrue(set(entry["seeds"]) <= set(stream))
            self.assertEqual(entry["perception"], "oracle")
            self.assertEqual(entry["spec_hash"], spec_hash(spec))
            for item in entry["skipped"]:
                self.assertIn(item["reason"], ("verification_failed", "variant_quota"))
                self.assertEqual(stream[item["index"]], item["seed"])
        shred = self.manifest[SHRED]
        self.assertEqual(shred["n_variants"], 2)
        self.assertEqual(shred["variants"], [s % 2 for s in shred["seeds"]])
        self.assertEqual(set(shred["variants"]), {0, 1})
        self.assertEqual(self.manifest[PAPER]["variants"], [None, None])

    def test_a_hidden_seed_passes_the_verifier(self):
        from house_sim.tasks.verify import run_trial
        trial = run_trial(self.specs[1], self.manifest[PAPER]["seeds"][0])
        self.assertTrue(trial["passed"], trial)

    def test_private_phrasings_and_public_fallback_recorded(self):
        paper, shred = self.manifest[PAPER], self.manifest[SHRED]
        self.assertFalse(paper["public_phrasings"])
        self.assertEqual(paper["phrasings"], PRIVATE_PHRASINGS)
        self.assertEqual(paper["phrasing_index"], [0, 1])
        self.assertTrue(shred["public_phrasings"])
        self.assertEqual(shred["phrasing_index"], [None, None])
        doc = S.load_manifest(with_meta=True)
        self.assertTrue(doc["public_phrasings"])
        self.assertEqual(doc["key_digest"], S.key_digest(self.key))
        self.assertTrue(self.public["public_phrasings"])

    def test_files_written_privately_and_commitment_reveals_no_seeds(self):
        self.assertTrue(S.manifest_path().is_file())
        self.assertIn(self.home.resolve(), S.manifest_path().resolve().parents)
        self.assertEqual(S.load_manifest(), json.loads(json.dumps(self.manifest)))
        text = self.commit_path.read_text(encoding="utf-8")
        for entry in self.manifest.values():
            for seed in entry["seeds"] + [s["seed"] for s in entry["skipped"]]:
                self.assertNotIn(str(seed), text)
        self.assertNotIn(TEST_KEY_HEX, text)
        for phrasing in PRIVATE_PHRASINGS:
            self.assertNotIn(phrasing, text)
        public = json.loads(text)
        self.assertEqual(public["key_digest"], S.key_digest(self.key))
        self.assertEqual(sorted(public["per_entry"]), sorted(self.manifest))
        self.assertEqual(public["seeds_total"], 4)
        self.assertEqual(S.commitment_root(), public["root"])

    def test_commitment_verifies_and_detects_tampering(self):
        manifest = S.load_manifest()
        public = S.load_commitment()
        self.assertTrue(S.verify_commitment(manifest, self.key, public))
        self.assertTrue(S.verify_commitment(S.load_manifest(with_meta=True), self.key, public))
        self.assertTrue(S.verify_commitment(manifest, None, public))      # revealed manifest, no key
        self.assertFalse(S.verify_commitment(manifest, bytes.fromhex(OTHER_KEY_HEX), public))

        def tampered(change):
            copy_ = json.loads(json.dumps(manifest))
            change(copy_)
            return copy_
        self.assertFalse(S.verify_commitment(tampered(lambda m: m[PAPER]["seeds"].__setitem__(0, m[PAPER]["seeds"][0] + 1)), self.key, public))
        self.assertFalse(S.verify_commitment(tampered(lambda m: m[PAPER]["phrasings"].append("leaked")), self.key, public))
        self.assertFalse(S.verify_commitment(tampered(lambda m: m.pop(SHRED)), self.key, public))
        self.assertFalse(S.verify_commitment(tampered(lambda m: m.__setitem__("T-extra", m[PAPER])), self.key, public))
        bad = json.loads(json.dumps(public))
        bad["per_entry"][PAPER] = "0" * 64
        self.assertFalse(S.verify_commitment(manifest, self.key, bad))
        self.assertFalse(S.commitment_consistent(bad))
        bad = json.loads(json.dumps(public))
        bad["root"] = "f" * 64
        self.assertFalse(S.verify_commitment(manifest, self.key, bad))
        self.assertFalse(S.commitment_consistent(bad))
        for cid, digest in public["per_entry"].items():
            proof = S.merkle_proof(public["per_entry"], cid)
            self.assertTrue(S.verify_merkle_proof(cid, digest, proof, public["root"]))
            self.assertFalse(S.verify_merkle_proof(cid, "1" * 64, proof, public["root"]))

    def test_cli_verify_commitment(self):
        code, out = run_cli(["--verify-commitment"])
        self.assertEqual(code, 0, out)
        self.assertIn("OK", out)
        bad = json.loads(S.manifest_path().read_text(encoding="utf-8"))
        bad["entries"][SHRED]["skipped"] = []
        bad["entries"][SHRED]["seeds"].reverse()
        path = self.tmp / "tampered_manifest.json"
        path.write_text(json.dumps(bad), encoding="utf-8")
        code, out = run_cli(["--verify-commitment", "--manifest", str(path)])
        self.assertEqual(code, 1)
        self.assertIn(SHRED, out)
        for seed in bad["entries"][SHRED]["seeds"]:
            self.assertNotIn(str(seed), out)

    def test_test_episodes(self):
        eps = S.episodes("test", self.specs)
        self.assertEqual(len(eps), 4)
        for ep in eps:
            self.assertEqual(set(ep), {"catalog_id", "split", "seed", "seed_tag", "phrasing", "perception"})
            self.assertEqual(ep["split"], "test")
            self.assertEqual(ep["perception"], "realistic")
            self.assertRegex(ep["seed_tag"], r"^test:[0-9a-f]{12}$")
            self.assertIn(ep["seed"], self.manifest[ep["catalog_id"]]["seeds"])
        self.assertEqual([e["phrasing"] for e in eps if e["catalog_id"] == PAPER], PRIVATE_PHRASINGS)
        self.assertEqual([e["phrasing"] for e in eps if e["catalog_id"] == SHRED], [None, None])
        self.assertEqual(len(S.episodes("test", self.specs, seeds_per_entry=1)), 2)
        with self.assertRaises(ValueError):
            S.episodes("test", self.specs, perception="oracle")
        extra = example_specs("T-0cd7f8f3019b40-01")
        with self.assertRaises(ValueError):
            S.episodes("test", self.specs + extra)
        self.assertEqual(len(S.episodes("test", self.specs + extra, strict=False)), 4)
        changed = json.loads(json.dumps(self.specs[1]))
        changed["title"] = "Refill printer paper (edited)"
        with self.assertRaises(ValueError):
            S.episodes("test", [changed])
        with self.assertRaises(ValueError):
            S.episodes("test", self.specs, key=bytes.fromhex(OTHER_KEY_HEX))
        dev = S.episodes("dev", self.specs)
        self.assertEqual([e["seed"] for e in dev if e["catalog_id"] == PAPER], list(S.DEV_SEEDS))
        self.assertTrue(all(e["perception"] == "oracle" and e["phrasing"] is None for e in dev))
        self.assertEqual(len(S.episodes("dev_extra", self.specs[:1])), 10)
        self.assertTrue(all(e["perception"] == "realistic" for e in S.episodes("train", self.specs, 2, perception="realistic")))

    def test_build_episode_applies_held_out_phrasing_and_tags_resolve(self):
        ep = [e for e in S.episodes("test", self.specs[1:]) if e["phrasing"]][0]
        config, task, oracle = S.build_episode(self.specs[1], ep)
        self.assertEqual(task["instruction"], ep["phrasing"])
        self.assertEqual(S.resolve_test_tag(PAPER, ep["seed_tag"]), ep["seed"])
        self.assertIsNone(S.resolve_test_tag(PAPER, "test:000000000000"))

    def test_split_info_reveals_no_secrets(self):
        code, out = run_cli(["--split-info", "--json"])
        self.assertEqual(code, 0, out)
        info = json.loads(out)
        self.assertTrue(info["key"]["present"])
        self.assertTrue(info["manifest"]["built_with_this_key"])
        self.assertTrue(info["commitment"]["matches_manifest"])
        self.assertEqual(info["packaging_private_files"], {"sdist": [], "wheel": []})
        self.assertEqual(info["packaging_private_reasons"], {})   # the hidden seeds here appear in no packaged file
        self.assertNotIn(TEST_KEY_HEX, out)
        for entry in self.manifest.values():
            for seed in entry["seeds"]:
                self.assertNotIn(str(seed), out)
        for phrasing in PRIVATE_PHRASINGS:
            self.assertNotIn(phrasing, out)

    def test_cli_build_raises_k_to_cover_every_variant(self):
        manifest_file, commit_file = self.tmp / "m2.json", self.tmp / "c2.json"
        code, out = run_cli(["--build-test-manifest", "--file", str(EXAMPLES), "--only", SHRED, "--k", "1",
                             "--specs", "candidates", "--workers", "1", "--manifest", str(manifest_file),
                             "--commitment", str(commit_file)])
        self.assertEqual(code, 0, out)
        entry = S.load_manifest(path=manifest_file)[SHRED]
        self.assertEqual((entry["k_requested"], entry["k"], len(entry["seeds"])), (1, 2, 2))
        self.assertEqual(set(entry["variants"]), {0, 1})
        self.assertEqual(entry["seeds"], self.manifest[SHRED]["seeds"])     # same key, same deterministic choice
        for seed in entry["seeds"]:
            self.assertNotIn(str(seed), out)
        self.assertNotIn(TEST_KEY_HEX, out)
        public = json.loads(commit_file.read_text(encoding="utf-8"))
        self.assertTrue(S.verify_commitment(S.load_manifest(path=manifest_file), self.key, public))

    # Regression: per_entry was sha256(canonical(entry) + "|" + key_digest) over low-entropy, mostly public data (the
    # public phrasings, counters, spec hash, code fingerprint), so a k=1 entry's hidden seed was recovered blind from
    # the public commitment in about two minutes. Every entry now carries a private random nonce inside the hash.
    def test_entries_are_blinded_by_a_private_random_nonce(self):
        digest = S.key_digest(self.key)
        nonces = [self.manifest[cid]["nonce"] for cid in sorted(self.manifest)]
        for nonce in nonces:
            self.assertRegex(nonce, r"^[0-9a-f]{64}$")
        self.assertEqual(len(set(nonces)), len(nonces))
        self.assertTrue(self.public["blinded"])
        self.assertIn("nonce", self.public["algorithm"])
        text = self.commit_path.read_text(encoding="utf-8")
        for nonce in nonces:
            self.assertNotIn(nonce, text)
        for cid, entry in self.manifest.items():
            self.assertEqual(S._entry_hash(cid, entry, digest), self.public["per_entry"][cid])
        tampered = json.loads(json.dumps(self.manifest))
        tampered[PAPER]["nonce"] = "0" * 64
        self.assertFalse(S.verify_commitment(tampered, self.key, self.public))

    def test_seed_search_with_everything_but_the_nonce_cannot_confirm_a_seed(self):
        # the attacker's best case: every field of the entry known exactly except the seed (and the nonce)
        digest = S.key_digest(self.key)
        entry = json.loads(json.dumps(self.manifest[PAPER]))
        true_seed = entry["seeds"][0]
        known = {k: v for k, v in entry.items() if k != "nonce"}

        def search(target):
            for seed in range(true_seed - 300, true_seed + 300):
                if S._entry_hash(PAPER, dict(known, seeds=[seed] + known["seeds"][1:]), digest) == target:
                    return seed
            return None
        self.assertEqual(search(S._entry_hash(PAPER, known, digest)), true_seed, "an unblinded hash gives the seed away")
        self.assertIsNone(search(self.public["per_entry"][PAPER]), "the published, blinded hash confirms no seed")

    def test_save_manifest_blinds_entries_without_a_nonce(self):
        legacy = json.loads(json.dumps(self.manifest))
        for entry in legacy.values():
            entry.pop("nonce")
        manifest_file, commit_file = self.tmp / "legacy_m.json", self.tmp / "legacy_c.json"
        public = S.save_manifest(legacy, self.key, path=manifest_file, commitment_path=commit_file)
        for cid, entry in legacy.items():
            self.assertRegex(entry["nonce"], r"^[0-9a-f]{64}$")
            self.assertNotEqual(entry["nonce"], self.manifest[cid]["nonce"])
        self.assertTrue(public["blinded"])
        self.assertTrue(S.verify_commitment(legacy, self.key, public))
        self.assertTrue(S.verify_commitment(S.load_manifest(path=manifest_file), None, public))   # opened later, no key

    def test_cli_verify_commitment_warns_about_unblinded_entries(self):
        doc = S.load_manifest(with_meta=True)
        for entry in doc["entries"].values():
            entry.pop("nonce")
        manifest_file, commit_file = self.tmp / "unblinded_m.json", self.tmp / "unblinded_c.json"
        manifest_file.write_text(json.dumps(doc), encoding="utf-8")
        commit_file.write_text(json.dumps(S._commitment_for_digest(doc["entries"], S.key_digest(self.key))), encoding="utf-8")
        code, out = run_cli(["--verify-commitment", "--manifest", str(manifest_file), "--commitment", str(commit_file)])
        self.assertEqual(code, 0, out)
        self.assertIn("no blinding nonce", out)
        code, out = run_cli(["--verify-commitment"])
        self.assertEqual(code, 0, out)
        self.assertNotIn("no blinding nonce", out)

    def test_k_below_one_refused_and_single_seed_entries_warned(self):
        with self.assertRaises(ValueError):
            S.build_test_manifest(example_specs(PAPER), k=0, workers=1)
        manifest_file, commit_file = self.tmp / "k1_m.json", self.tmp / "k1_c.json"
        base = ["--build-test-manifest", "--file", str(EXAMPLES), "--only", PAPER, "--specs", "candidates", "--workers", "1",
                "--manifest", str(manifest_file), "--commitment", str(commit_file)]
        code, out = run_cli(base + ["--k", "0"])
        self.assertEqual(code, 2, out)
        self.assertFalse(manifest_file.exists())
        code, out = run_cli(base + ["--k", "1"])
        self.assertEqual(code, 0, out)
        self.assertIn("single hidden seed", out)
        self.assertIn(PAPER, out)
        entry = S.load_manifest(path=manifest_file)[PAPER]
        self.assertEqual(len(entry["seeds"]), 1)
        self.assertRegex(entry["nonce"], r"^[0-9a-f]{64}$")
        self.assertEqual(S.weak_entries({PAPER: entry}), [PAPER])
        self.assertEqual(S.weak_entries(self.manifest), [])
        public = json.loads(commit_file.read_text(encoding="utf-8"))
        self.assertEqual(public["k"], [1])
        self.assertTrue(public["blinded"])
        self.assertTrue(S.verify_commitment(S.load_manifest(path=manifest_file), self.key, public))
        self.assertNotIn(str(entry["seeds"][0]), out)


class VerifiedSpecsTests(Isolated):
    def test_report_evidence_must_be_current(self):
        from house_sim.audit import fingerprint
        from house_sim.tasks.verify import spec_hash
        root = self.tmp / "root"
        (root / "catalog" / "raw").mkdir(parents=True)
        shutil.copy2(ROOT / "catalog" / "raw" / "household_catalog.txt", root / "catalog" / "raw")
        (root / "tasks" / "authored").mkdir(parents=True)
        shutil.copy2(EXAMPLES, root / "tasks" / "authored")
        shutil.copytree(ROOT / "tasks" / "templates", root / "tasks" / "templates")
        (root / "reports").mkdir()
        current = fingerprint(root)
        specs = {s["catalog_id"]: s for s in example_specs(SHRED, PAPER, "T-0cd7f8f3019b40-01", "T-89d7a649052266-01")}
        rows = [{"catalog_id": SHRED, "passed": True, "implementation_hash": current, "spec_hash": spec_hash(specs[SHRED])},
                {"catalog_id": PAPER, "passed": True, "implementation_hash": "stale", "spec_hash": spec_hash(specs[PAPER])},
                {"catalog_id": "T-0cd7f8f3019b40-01", "passed": False, "implementation_hash": current,
                 "spec_hash": spec_hash(specs["T-0cd7f8f3019b40-01"])},
                {"catalog_id": "T-89d7a649052266-01", "passed": True, "implementation_hash": current, "spec_hash": "old"}]
        (root / "reports" / "verification.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        self.assertEqual([s["catalog_id"] for s in S.verified_specs(root=root)], [SHRED])
        records = S.verified_specs(root=root, records=True)
        self.assertEqual(records[0]["evidence"], "report")
        self.assertEqual(records[0]["domain"], "admin_inventory")
        self.assertEqual(S.verified_specs(root=root, only=[PAPER]), [])

    def test_on_the_fly_verification(self):
        records = S.verified_specs(verify=True, only=[PAPER], workers=1, records=True)
        self.assertEqual([r["catalog_id"] for r in records], [PAPER])
        self.assertEqual(records[0]["evidence"], "on_the_fly")
        self.assertEqual([t["seed"] for t in records[0]["verification"]["trials"]], list(S.DEV_SEEDS))
        broken = json.loads(json.dumps(example_specs(PAPER)[0]))
        broken["reference"] = []
        kept, rejected = S.filter_verified([broken], verify=True, seeds=(0,), workers=1)
        self.assertEqual(kept, [])
        self.assertEqual(len(rejected), 1)


def gitignored(path, rules):
    """Enough of .gitignore matching for the rules this repository uses: a pattern without "/" matches any path
    component (a trailing "/" restricts it to directories); a pattern with "/" is anchored at the root."""
    parts = path.split("/")
    for rule in rules:
        directory = rule.endswith("/")
        pattern = rule.rstrip("/")
        if "/" in pattern:
            pattern = pattern.lstrip("/")
            candidates = ["/".join(parts[:i]) for i in range(1, len(parts) + (0 if directory else 1))]
        else:
            candidates = parts[:-1] if directory else parts
        if any(fnmatch.fnmatchcase(c, pattern) for c in candidates):
            return True
    return False


def plant(root, files):
    for name, content in files.items():
        path = Path(root) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")


def viewer_page(meta):
    """A viewer page as house_sim/eval/viewer.py renders it (falls back to the same shape if the viewer is unusable)."""
    data = {"format": "house_ai.viewer/1", "meta": meta, "steps": [], "evals": []}
    try:
        from house_sim.eval import viewer
        return viewer.render_html(data)
    except Exception:  # noqa: BLE001 - another component; the audit must not depend on it
        title = ("PRIVATE - " if meta.get("private") else "") + meta.get("catalog_id", "episode") + " - episode viewer"
        return ('<!doctype html><html><head><title>{}</title></head><body><div id="privateBanner" hidden>PRIVATE - '
                'test split</div><script type="application/json" id="episode-data">{}</script></body></html>'
                .format(title, json.dumps(data)))


class PackagingTests(Isolated):
    """Isolated like the other tests: the audit compares packaged files with the split key and the private manifest
    of the (temporary) home directory, never the user's real ~/.house_ai."""

    def test_repository_package_lists_contain_no_private_files(self):
        self.assertEqual(S.private_files_in_package(ROOT), {"sdist": [], "wheel": []})
        self.assertEqual(S.packaging_audit(ROOT)["reasons"], {})
        listing = S.packaged_files(ROOT)
        data_file = next((ROOT / "house_sim" / "data").rglob("*.json")).relative_to(ROOT).as_posix()
        for name in ("house_sim/eval/splits.py", "setup.py", "MANIFEST.in", "tests/test_eval_splits.py",
                     "docs/SPLITS.md", data_file):
            self.assertIn(name, listing["sdist"])
        self.assertFalse(any(f.startswith("results/") for f in listing["sdist"] + listing["wheel"]))
        # setup.py's package_data (read statically) is part of both lists, so the audit sees those files too
        self.assertIn("eval/*.html", S._package_data(ROOT)["house_sim"])
        for kind in ("sdist", "wheel"):
            self.assertIn("house_sim/eval/viewer_template.html", listing[kind])

    def make_planted_tree(self, base):
        root = Path(base) / "tree"
        files = {"house_sim/__init__.py": "", "house_sim/data/types.json": "{}", "house_sim/split_key": "00" * 32,
                 "reports/ok.json": "{}", "reports/test_manifest.json": "{}", "reports/test_commitment.json": "{}",
                 "tasks/authored/x.json": "{}", "tasks/test_phrasings.json": "{}", "results/run1/episodes.jsonl": "",
                 "tests/test_a.py": "", "docs/x.md": "", ".house_ai/split_key": "00" * 32, "README.md": "x",
                 "catalog/split_key": "00" * 32, "reports/T-x__test-abc__PRIVATE.json": "{}",
                 "reports/private_results/run1/transcripts/t.json": "{}", "tasks/.house_ai/test_phrasings.json": "{}",
                 "demos/private_results/run1/replays/r.jsonl": ""}
        plant(root, files)
        shutil.copy2(ROOT / "MANIFEST.in", root / "MANIFEST.in")
        shutil.copy2(ROOT / "setup.py", root / "setup.py")
        return root

    def test_manifest_rules_exclude_planted_private_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_planted_tree(tmp)
            sdist = S.packaged_files(root)["sdist"]
            for name in ("reports/ok.json", "reports/test_commitment.json", "tasks/authored/x.json",
                         "house_sim/data/types.json", "house_sim/__init__.py", "tests/test_a.py", "docs/x.md"):
                self.assertIn(name, sdist)
            self.assertEqual(S.private_files_in_package(root)["sdist"], [])
            # setup.py's build step copies catalog/tasks/reports wholesale into the wheel; the audit catches that
            wheel = S.private_files_in_package(root)["wheel"]
            for name in ("reports/test_manifest.json", "reports/T-x__test-abc__PRIVATE.json",
                         "reports/private_results/run1/transcripts/t.json", "tasks/.house_ai/test_phrasings.json",
                         "demos/private_results/run1/replays/r.jsonl"):
                self.assertIn(name, wheel)

    def test_manifest_globs_follow_setuptools(self):
        glob = S._glob_re("**/private_results/**")
        for path in ("private_results/a.json", "reports/private_results/r/a.json", "a/b/private_results/c/d.html"):
            self.assertTrue(glob.match(path), path)
        for path in ("reports/private_results_x/a.json", "reports/my_private_results/a.json"):
            self.assertFalse(glob.match(path), path)
        glob = S._glob_re("reports/**/*.json")
        self.assertTrue(glob.match("reports/a.json"))
        self.assertTrue(glob.match("reports/x/y/z.json"))
        self.assertFalse(glob.match("reports/a.jsonl"))
        self.assertFalse(glob.match("other/reports/a.json"))
        self.assertFalse(S._glob_re("house_sim/data/*.json").match("house_sim/data/types/a.json"))
        self.assertTrue(S._glob_re("**/*__PRIVATE.*").match("reports/viewer/a__PRIVATE.html"))
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(S._package_data(Path(tmp)), S.DEFAULT_PACKAGE_DATA)    # no setup.py
            Path(tmp, "setup.py").write_text("from setuptools import setup\nsetup(name='x')\n", encoding="utf-8")
            self.assertEqual(S._package_data(Path(tmp)), {})

    # Regression (red team): .gitignore did not list test_phrasings.json although this doc said it did, and nothing
    # covered private_results/ or the viewer's *__PRIVATE.html pages.
    def test_gitignore_covers_private_artifacts(self):
        rules = [line.strip() for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
                 if line.strip() and not line.strip().startswith(("#", "!"))]
        for path in ("test_phrasings.json", "drafts/test_phrasings.json", "split_key", "tasks/test_manifest.json",
                     "results/run1/episodes.jsonl", ".house_ai/split_key", "private_results/run1/replays/a.replay.json",
                     "reports/private_results/run1/viewer/T-x__test-abc__PRIVATE.html",
                     "reports/T-x__test-abc__PRIVATE.html", "reports/T-x__test-abc__PRIVATE.json"):
            self.assertTrue(gitignored(path, rules), path)
        # the demo replays are committed again: compact house-replay-v3 files of about 100 KB (kernel/replays.py)
        for path in ("reports/leaderboard.html", "reports/test_commitment.json", "demos/manifest.json",
                     "demos/replays/T-x.json", "house_sim/eval/splits.py", "house_sim/eval/viewer_template.html",
                     "docs/SPLITS.md"):
            self.assertFalse(gitignored(path, rules), path)

    # Regression (red team): _private_file matched only split_key, test_manifest.json, test_phrasings.json, results/
    # and .house_ai/, so a private test replay (raw seed embedded) or a "PRIVATE - test split" viewer page copied
    # into reports/ was listed for the sdist and the wheel while private_files_in_package stayed empty.
    def test_audit_flags_private_replays_viewer_pages_and_directories(self):
        seed, listed = S.PRIVATE_BASE + 1234567, 123457
        replay = {"format": "house-replay-v2", "seed": seed, "perception": "realistic",
                  "task": {"instruction": "held-out phrasing"}, "steps": []}
        with tempfile.TemporaryDirectory() as tmp, fresh_home(self.tmp, keep_key_env=True):
            S.manifest_path().parent.mkdir(parents=True)
            S.manifest_path().write_text(json.dumps({"format": S.MANIFEST_FORMAT, "key_digest": S.key_digest(self.key),
                                                     "entries": {PAPER: {"seeds": [listed]}}}), encoding="utf-8")
            root = Path(tmp) / "fakeroot"
            stem = "T-6af2ea41755bbe-01__test-2419b6bf6fb0"
            private = {   # path -> packaged in the sdist too
                "reports/{}.replay.json".format(stem): True,                    # the red team's repro
                "reports/{}__PRIVATE.json".format(stem): False,                 # (MANIFEST.in now excludes it)
                "reports/viewer/{}__PRIVATE.html".format(stem): False,
                "demos/replays/T-6af2ea41755bbe-01.json": True,                 # a test replay under a public name
                "reports/episodes_copy.jsonl": True,                            # a record with a raw hidden seed
                "reports/legacy.json": True,                                    # a seed listed in the private manifest
                "reports/renamed_page.html": False,                             # private page, innocuous name
                "reports/data_only_page.html": False,                           # meta.private without the title
                "reports/hidden.json": True,                                    # a renamed copy of the manifest
                "house_sim/eval/stray_manifest.json": True,                     # shipped through package_data too
                "catalog/notes.txt": True,                                      # the split key pasted into a note
                "tasks/private_results/run1/viewer/page.html": False,
                "reports/.house_ai/key_backup.json": False,
            }
            files = {
                "house_sim/__init__.py": "", "house_sim/eval/__init__.py": "",
                "reports/{}.replay.json".format(stem): replay, "reports/{}__PRIVATE.json".format(stem): {},
                "reports/viewer/{}__PRIVATE.html".format(stem): viewer_page({"catalog_id": PAPER, "private": True}),
                "demos/replays/T-6af2ea41755bbe-01.json": json.dumps(replay, indent=1),
                "reports/episodes_copy.jsonl": json.dumps({"catalog_id": PAPER, "split": "test", "seed": seed}) + "\n",
                "reports/legacy.json": {"seed": listed},
                "reports/renamed_page.html": viewer_page({"catalog_id": PAPER, "private": True, "split": "test"}),
                "reports/data_only_page.html": '<html><head><title>{} - episode viewer</title></head><body><script '
                                               'type="application/json" id="episode-data">{}</script></body></html>'
                                               .format(PAPER, json.dumps({"meta": {"private": True}})),
                "reports/hidden.json": {"format": S.MANIFEST_FORMAT, "entries": {}},
                "house_sim/eval/stray_manifest.json": {"format": S.MANIFEST_FORMAT, "entries": {}},
                "catalog/notes.txt": "backup: " + TEST_KEY_HEX.upper() + "\n",
                "tasks/private_results/run1/viewer/page.html": "<html></html>",
                "reports/.house_ai/key_backup.json": "{}",
                # public files that must stay unflagged
                "reports/dev_replay.json": dict(replay, seed=41),
                "reports/train.jsonl": "".join(json.dumps({"seed": s, "seeds": [s, 0]}) + "\n"
                                               for s in S.train_seeds(PAPER, 3)),
                "reports/public_page.html": viewer_page({"catalog_id": PAPER, "private": False, "seed": 41,
                                                         "split": "dev", "seed_tag": "dev:41"}),
                "reports/test_tags.json": {"seed_tag": "test:0123456789ab", "split": "test"},   # tags are public
                "reports/leaderboard.html": "<html><head><title>Agent Leaderboard</title></head></html>",
                "tests/test_fixture.py": "SEED = {}\nROW = {{'seed': {}}}\n".format(seed, seed),
                "docs/notes.md": 'The manifest format is {"format": "' + S.MANIFEST_FORMAT + '", ...}.\n',
            }
            plant(root, files)
            shutil.copy2(ROOT / "MANIFEST.in", root / "MANIFEST.in")
            shutil.copy2(ROOT / "setup.py", root / "setup.py")
            listing = S.packaged_files(root)
            audit = S.packaging_audit(root)
            self.assertEqual(S.private_files_in_package(root), {"sdist": audit["sdist"], "wheel": audit["wheel"]})
            self.assertEqual(sorted(audit["reasons"]), sorted(private))
            for path, in_sdist in private.items():
                self.assertIn(path, listing["wheel"], path)
                self.assertIn(path, audit["wheel"], path)
                self.assertEqual(path in listing["sdist"], in_sdist, path)
                self.assertEqual(path in audit["sdist"], in_sdist, path)
            self.assertIn("seed", audit["reasons"]["reports/{}.replay.json".format(stem)])
            self.assertIn("seed", audit["reasons"]["reports/legacy.json"])
            self.assertIn("PRIVATE - test split", audit["reasons"]["reports/renamed_page.html"])
            self.assertIn("manifest", audit["reasons"]["reports/hidden.json"])
            self.assertIn("split key", audit["reasons"]["catalog/notes.txt"])
            for reason in audit["reasons"].values():        # the reasons never repeat a secret
                for secret in (str(seed), str(listed), TEST_KEY_HEX, TEST_KEY_HEX.upper(), "held-out phrasing"):
                    self.assertNotIn(secret, reason)
            for name in ("reports/public_page.html", "tests/test_fixture.py", "docs/notes.md"):
                self.assertIn(name, listing["sdist"] + listing["wheel"], name)
            # without the key and the manifest in the home directory, only the key and manifest-seed checks go away
            with fresh_home(self.tmp):
                reasons = S.packaging_audit(root)["reasons"]
            self.assertEqual(sorted(reasons), sorted(set(private) - {"catalog/notes.txt", "reports/legacy.json"}))

    def test_real_sdist_when_setuptools_is_available(self):
        if importlib.util.find_spec("setuptools") is None:
            self.skipTest("setuptools not installed; MANIFEST.in rules are emulated above")
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_planted_tree(tmp)
            dist = Path(tmp) / "dist"
            proc = subprocess.run([sys.executable, "setup.py", "-q", "sdist", "--formats=zip", "--dist-dir", str(dist)],
                                  cwd=str(root), capture_output=True, text=True, timeout=300)
            self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
            names = [n for z in dist.glob("*.zip") for n in zipfile.ZipFile(z).namelist()]
            self.assertTrue(names)
            for name in names:
                parts = name.split("/")
                self.assertNotIn(parts[-1], S.PRIVATE_NAMES, name)
                self.assertNotIn("results", parts[1:-1], name)
                self.assertNotIn(S.PRIVATE_RESULTS_DIR, parts[1:-1], name)
                self.assertNotIn(S.PRIVATE_DIR, parts[1:-1], name)
                self.assertNotIn(S.PRIVATE_MARK, parts[-1], name)


if __name__ == "__main__":
    unittest.main()
