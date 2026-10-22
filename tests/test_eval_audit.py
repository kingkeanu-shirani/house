"""house_sim/audit.py verify (adoption round, K-EVAL; TIME-EVAL request 3): `python -m house_sim --verify --gen 2`
forces the house generation (the M-GEN2 migration check), like --camera and --perception. A forced run is a check of
the specs under that mode: its records carry "forced" and go to reports/verification_forced.jsonl, never into the
catalog evidence (reports/verification.jsonl) that the coverage reports, the harness, the dataset exporter and the
leaderboard trust. Runs on a temporary root with the compiler and the verifier stubbed (no spec is simulated)."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from house_sim import audit, cli


def candidate(cid, section="S", source="x__examples.json"):
    return {"catalog_id": cid, "status": "CANDIDATE", "section": section, "source_file": source,
            "spec": {"catalog_id": cid}}


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="house_audit_test_"))
        (self.root / "reports").mkdir()
        self.calls = []
        self.records = [candidate("T-aaaaaaaaaaaaaa-01"), candidate("T-bbbbbbbbbbbbbb-01", section="Other")]

        def fake_verify_many(specs, seeds=(), workers=None, progress=None, perception=None, camera=None, gen=None):
            self.calls.append({"ids": [s["catalog_id"] for s in specs], "perception": perception, "camera": camera,
                               "gen": gen})
            return [{"catalog_id": s["catalog_id"], "spec_hash": "h", "passed": gen != 2, "trials": []} for s in specs]
        patches = [mock.patch.object(audit, "compile_specs", lambda root: ([], self.records, {"source_sha256": "s"})),
                   mock.patch.object(audit, "verify_many", fake_verify_many),
                   mock.patch.object(audit, "fingerprint", lambda root: "impl"),
                   mock.patch.object(audit, "report", self.fake_report)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.reports = 0

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def fake_report(self, root):
        self.reports += 1
        return {"statuses": {}}

    def read(self, name):
        path = self.root / name
        if name.endswith(".jsonl"):
            return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return json.loads(path.read_text(encoding="utf-8"))

    def test_an_unforced_run_writes_the_catalog_evidence_and_the_coverage(self):
        run = audit.verify(root=self.root, emit=lambda *a, **k: None, workers=1)
        self.assertEqual(self.calls, [{"ids": ["T-aaaaaaaaaaaaaa-01", "T-bbbbbbbbbbbbbb-01"], "perception": None,
                                       "camera": None, "gen": None}])
        self.assertEqual([r["catalog_id"] for r in self.read(audit.EVIDENCE)],
                         ["T-aaaaaaaaaaaaaa-01", "T-bbbbbbbbbbbbbb-01"])
        self.assertNotIn("forced", self.read(audit.EVIDENCE)[0])
        self.assertNotIn("forced", run)
        self.assertEqual(self.reports, 1)
        self.assertFalse((self.root / audit.FORCED_EVIDENCE).exists())
        self.assertNotIn(b"\r\n", (self.root / audit.EVIDENCE_RUN).read_bytes())          # LF reports

    def test_a_forced_generation_is_a_check_beside_the_evidence(self):
        audit.verify(root=self.root, emit=lambda *a, **k: None, workers=1)
        evidence = (self.root / audit.EVIDENCE).read_bytes()
        lines = []
        run = audit.verify(root=self.root, emit=lambda *a, **k: lines.append(a[0]), workers=1, gen=2)
        self.assertEqual(self.calls[-1]["gen"], 2)
        self.assertEqual(run["forced"], {"gen": 2})
        self.assertEqual(run["evidence"], audit.FORCED_EVIDENCE)
        self.assertEqual(run["failed_entries"], ["T-aaaaaaaaaaaaaa-01", "T-bbbbbbbbbbbbbb-01"])
        self.assertEqual((self.root / audit.EVIDENCE).read_bytes(), evidence)        # the catalog evidence is untouched
        self.assertEqual(self.reports, 1)                                            # and so is the coverage
        forced = self.read(audit.FORCED_EVIDENCE)
        self.assertEqual([r["forced"] for r in forced], [{"gen": 2}, {"gen": 2}])
        self.assertEqual(self.read(audit.FORCED_RUN)["forced"], {"gen": 2})
        self.assertIn("Forced check (gen=2)", lines[-1])

    def test_a_narrowed_forced_run_merges_only_with_the_same_forced_mode(self):
        audit.verify(root=self.root, emit=lambda *a, **k: None, workers=1, gen=2)
        audit.verify(root=self.root, emit=lambda *a, **k: None, workers=1, gen=2, only={"T-bbbbbbbbbbbbbb-01"})
        self.assertEqual(self.calls[-1]["ids"], ["T-bbbbbbbbbbbbbb-01"])
        self.assertEqual(len(self.read(audit.FORCED_EVIDENCE)), 2)                   # merged with the same check
        audit.verify(root=self.root, emit=lambda *a, **k: None, workers=1, camera="geometric",
                     only={"T-bbbbbbbbbbbbbb-01"})
        forced = self.read(audit.FORCED_EVIDENCE)
        self.assertEqual([(r["catalog_id"], r["forced"]) for r in forced],
                         [("T-bbbbbbbbbbbbbb-01", {"camera": "geometric"})])           # never mixed with another mode
        audit.verify(root=self.root, emit=lambda *a, **k: None, workers=1, perception="realistic")
        self.assertEqual(self.read(audit.FORCED_RUN)["forced"], {"perception": "realistic"})
        self.assertFalse((self.root / audit.EVIDENCE).exists())

    def test_the_cli_passes_gen_and_camera_to_verify(self):
        seen = []

        def fake(**kwargs):
            seen.append(kwargs)
            return {"failed_entries": []}
        with mock.patch.object(audit, "verify", fake):
            self.assertEqual(cli.main(["--verify", "--gen", "2"]), 0)
            self.assertEqual(cli.main(["--verify-only", "T-aaaaaaaaaaaaaa-01", "--camera", "geometric",
                                       "--perception", "realistic"]), 0)
        self.assertEqual((seen[0]["gen"], seen[0]["camera"], seen[0]["perception"]), (2, None, None))
        self.assertEqual((seen[1]["gen"], seen[1]["camera"], seen[1]["perception"], seen[1]["only"]),
                         (None, "geometric", "realistic", {"T-aaaaaaaaaaaaaa-01"}))


if __name__ == "__main__":
    unittest.main()
