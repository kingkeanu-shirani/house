"""Integration glue of the evaluation package (house_sim/eval/common.py and cross-component behaviour).

Fully offline: no network, no split key or manifest of the user's, nothing written outside temporary folders."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from house_sim.eval import common
from house_sim.eval import run as R
from house_sim.eval import splits as S

WET = "T-089c40fb507289-01"
GLASSES = "T-6af2ea41755bbe-01"


class TolerantAuthoredLoaderTests(unittest.TestCase):
    def test_an_id_authored_twice_is_left_out_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "tasks" / "authored"
            folder.mkdir(parents=True)
            (folder / "a__examples.json").write_text(json.dumps({"specs": [{"catalog_id": "X-1", "title": "a"},
                                                                          {"catalog_id": "X-2", "title": "a"}]}), encoding="utf-8")
            (folder / "a__perception.json").write_text(json.dumps({"specs": [{"catalog_id": "X-2", "title": "b"},
                                                                            {"catalog_id": "X-3", "title": "b"}]}), encoding="utf-8")
            specs, sources, conflicts = common.load_authored(tmp)
        self.assertEqual(sorted(specs), ["X-1", "X-3"])
        self.assertEqual(sources["X-3"], "a__perception.json")
        self.assertEqual(conflicts, {"X-2": ["a__examples.json", "a__perception.json"]})
        self.assertIn("X-2", common.conflict_note(conflicts))
        self.assertEqual(common.conflict_note({}), "")

    def test_real_authored_files(self):
        specs, sources, conflicts = common.load_authored()
        self.assertTrue(specs)
        self.assertFalse(set(conflicts) & set(specs))
        self.assertTrue(all(spec["catalog_id"] == cid and sources[cid].endswith(".json") for cid, spec in specs.items()))
        self.assertTrue(all(len(files) > 1 for files in conflicts.values()))


class CommitmentFileTests(unittest.TestCase):
    def test_env_override_and_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "sandbox_commitment.json"
            with mock.patch.dict(os.environ, {S.COMMITMENT_ENV: str(target)}):
                self.assertEqual(S.commitment_file(), target)
                self.assertIsNone(S.commitment_root())
                target.write_text(json.dumps({"root": "ab" * 32, "per_entry": {}}), encoding="utf-8")
                self.assertEqual(S.commitment_root(), "ab" * 32)
                self.assertEqual(R._commitment_root(), "ab" * 32)
                from house_sim.eval import leaderboard
                self.assertEqual(leaderboard._public_commitment_root(), "ab" * 32)
            with mock.patch.dict(os.environ, {}):
                os.environ.pop(S.COMMITMENT_ENV, None)
                self.assertEqual(S.commitment_file(), Path(S.COMMITMENT_FILE))


class DryRunVerificationTests(unittest.TestCase):
    def test_dry_run_with_on_the_fly_verification_estimates_only_passing_specs(self):
        records = [r for r in R.load_specs() if r["catalog_id"] in (WET, GLASSES)]
        self.assertEqual(len(records), 2)
        calls = []

        def fake_verify(recs, seeds=None, perception=None, workers=None):
            calls.append(sorted(r["catalog_id"] for r in recs))
            return {r["catalog_id"]: {"verified": r["catalog_id"] == WET, "source": "on_the_fly", "detail": "fake"} for r in recs}
        with mock.patch.dict(os.environ, {"HOUSE_AI_FORBID_NETWORK": "1"}), mock.patch.object(R, "verify_on_the_fly", fake_verify):
            with redirect_stdout(io.StringIO()) as out:
                report = R.run("llm:anthropic:claude-opus-5-5", "dev", records, None, dry_run=True, verify="fly", seeds=[0],
                               caps={"max_steps": 5})
        self.assertEqual(calls, [sorted([WET, GLASSES])])          # verification ran once, in the dry run
        self.assertEqual({row["catalog_id"] for row in report["episodes"]}, {WET})
        self.assertFalse(report["verification"][GLASSES]["verified"])
        self.assertIn("failed verification", out.getvalue())


class PipelineWithoutReplaysTests(unittest.TestCase):
    """run (no replays) -> verify_submission -> leaderboard -> viewer rebuilt from the log and the spec."""

    def test_end_to_end_without_saved_replays(self):
        from house_sim.eval import leaderboard, viewer
        records = [r for r in R.load_specs() if r["catalog_id"] == WET]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            summary = R.run("oracle", "dev", records, out, verify="none", seeds=[0], emit=None, save_replays=False,
                            run_id="no-replays")
            self.assertEqual(summary["successes"], 1)
            self.assertFalse((out / "replays").exists())
            info = json.loads((out / "run.json").read_text(encoding="utf-8"))
            self.assertFalse(info["replays_saved"])
            rec = json.loads((out / "episodes.jsonl").read_text(encoding="utf-8").splitlines()[0])
            log = json.loads((out / rec["log"]).read_text(encoding="utf-8"))
            self.assertIsNone(log["replay"])
            # allow_mixed: other components' code may change between the run and the replay while this test runs
            result = leaderboard.verify_submission(out, write=True, allow_mixed=True)
            self.assertTrue(result["passed"], result)
            self.assertEqual((result["verified"], result["rejected"]), (1, []))
            board = leaderboard.aggregate(leaderboard.load_runs([out]), resamples=50)
            self.assertIn(board["rows"][0]["submission_verified"], ("passed", "passed (other code, --allow-mixed)"))
            page = viewer.export_viewer(str(out / rec["log"]), Path(tmp) / "page.html")
            data = viewer.extract_data(page.read_text(encoding="utf-8"))
            self.assertEqual(len(viewer.decode_deltas(data["frames"])), len(data["steps"]) + 1)
            self.assertEqual(data["meta"]["perception"], "realistic")


if __name__ == "__main__":
    unittest.main()
