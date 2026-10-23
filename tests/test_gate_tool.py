"""The hardened tier-1 gate and process tooling (phase 1 of the stabilisation after the independent review).

REVIEW_REPORT H12 / top-10 item 3 and phases/p2_tests_gate.md T3-T5, section 5.2 ("gatelab" cases E0-E6), P4 A3,
H15 (ORCHESTRATOR-VERIFY), M7 (CI, Python 3.9):
* absolute rules beside the delta rule: G2-ABS (every spec passes in its own mode, new ones included) and every test
  passes, except entries of the dated allowlist (owner, expiry);
* a reviewed row is bound to the files its package owns, and every verdict change, edited spec and coverage loss
  also needs the main session's second signature, read from the committed policy files only;
* wrong-plan reason classes are recorded in trial records and compared;
* SKIP counts as FAIL, G10 fails without git, compare fails on any G4/G6 leak, crash, defect or nondeterminism
  (absolute and as a regression) and on the record's own G1c/G9/G10 failures; the adoption-style --shared-tree
  compare works;
* the fuzzer reports per-action success coverage, replays every house, and --coverage drives the actions toward
  success from every spec's setup;
* run_check.py runs the checks itself and keeps a hash-chained log; green_tests.py exits non-zero on a failure;
* every Python file parses as Python 3.9 and avoids 3.10+ runtime features; the CI workflow covers 3.9 and 3.14 on
  Windows and Linux.
Phase-1 red team (catalog/analysis/phase1/red_defects.json, FIX2; RedTeamGateTests): a skipped or deleted test fails
record and compare unless allowlisted ("skip") or signed ("test-removal"); a record merged from runs on different trees
is STALE; compare honours the record's own statuses and missing data, keeps G2-ABS on the full rows and calls a
narrowed previous record INCOMPLETE; policy files come from the project's commits only, never from --root or an
isolated copy.
Everything runs on temporary folders; nothing is committed and no policy file of the project is written.
"""
import ast
import contextlib
import copy
import fnmatch
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("tier1_gate_hardened", str(ROOT / "tools" / "orchestration" /
                                                                          "tier1_gate.py"))
gate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gate)
_RC = importlib.util.spec_from_file_location("run_check_under_test", str(ROOT / "tools" / "orchestration" /
                                                                         "run_check.py"))
run_check = importlib.util.module_from_spec(_RC)
_RC.loader.exec_module(run_check)

FILE_A = "admin_inventory__examples.json"
TREE = "t" * 64                 # the tree fingerprint of the synthetic records (tree_fingerprint is patched to it)
FILE_C = "cleaning__examples.json"


def trial(key, seed, file=FILE_A, passed=True, state="s0", spec_hash=None, wrong=None):
    return {"key": key, "catalog_id": key, "seed": seed, "file": file, "spec_hash": spec_hash or "h-" + key,
            "passed": passed, "initial_fails": True, "idle_fails": True, "reference_passes": passed,
            "perceivable": None, "wrong_fails": True, "wrong_ran": True,
            "wrong": wrong if wrong is not None else [{"name": "w", "fails": True, "ran": True, "reasons": ["goal"]}],
            "reference_state_hash": state, "reference_obs_hash": "o"}


def write_record(folder, meta, own=(), realistic=None, audits=None, tests=None):
    folder.mkdir(parents=True, exist_ok=True)
    gate.write_json(folder / "meta.json", meta)
    for name, rows in (("specs_own.jsonl", own), ("specs_realistic.jsonl", own if realistic is None else realistic)):
        with open(folder / name, "w", encoding="utf-8", newline="\n") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
    if audits is not None:
        gate.write_json(folder / "audits.json", audits)
    if tests is not None:
        gate.write_json(folder / "tests.json", tests)


def policy_files(known=None, signoffs=None):
    """A stand-in for gate.trusted_json: the committed policy files of a fake project."""
    files = {}
    if known is not None:
        files[gate.KNOWN_FAILING] = {"format": gate.KNOWN_FAILING_FORMAT, "entries": known}
    for gid, rows in (signoffs or {}).items():
        files["{}/{}.json".format(gate.SIGNOFF_DIR, gid)] = {"format": gate.SIGNOFF_FORMAT, "gate": gid,
                                                             "signed_by": "main session", "rows": rows}

    def fake(root, rel):
        return (files[rel], "committed at HEAD (test)") if rel in files else (None, "not committed")
    return fake


class GateCase(unittest.TestCase):
    """Synthetic gate records in a temporary gates folder (C1 the previous gate, X the package's record)."""
    PKG = "ADOPT:admin_inventory"

    def setUp(self):
        self.gates = Path(tempfile.mkdtemp(prefix="gate_hard_"))
        self.addCleanup(shutil.rmtree, str(self.gates), True)
        self.prev_meta = {"id": "C1", "stage": "C1", "finished": "2026-10-15"}
        self.meta = {"id": "X", "packages": [self.PKG], "stage": "ADOPT", "prev": "C1", "finished": "2026-10-20",
                     "tree": TREE}

    def record(self, prev_rows, cur_rows, prev_audits=None, cur_audits=None, cur_tests=None, meta=None):
        write_record(self.gates / "C1", self.prev_meta, own=prev_rows, audits=prev_audits)
        write_record(self.gates / "X", dict(self.meta, **(meta or {})), own=cur_rows, audits=cur_audits,
                     tests=cur_tests)

    def compare(self, *extra, policy=None, package=None):
        out = io.StringIO()
        with mock.patch.object(gate, "trusted_json", policy or policy_files()), \
                mock.patch.object(gate, "tree_fingerprint", lambda root: TREE), \
                mock.patch.object(gate, "changed_files", lambda root: ([], None)), contextlib.redirect_stdout(out):
            code = gate.main(["--root", str(ROOT), "--gates-root", str(self.gates), "compare", "C1", "X",
                              "--package", package or self.PKG] + list(extra))
        return code, out.getvalue()


class AbsoluteRuleTests(GateCase):
    def test_e0_a_spec_failing_in_both_records_fails(self):
        rows = [trial("A-1", 0, passed=False), trial("A-2", 0)]
        self.record(rows, rows)
        code, text = self.compare()
        self.assertEqual(code, 1, text)
        self.assertIn("G2-ABS: 1 spec(s) fail in their own mode: A-1 (seeds 0)", text)

    def test_e1_a_new_spec_failing_on_every_seed_fails(self):
        self.record([trial("A-1", 0)], [trial("A-1", 0)] + [trial("A-new", s, passed=False) for s in (0, 1)])
        code, text = self.compare()
        self.assertEqual(code, 1, text)
        self.assertIn("A-new (seeds 0,1)", text)
        self.assertIn("2 new trial(s)", text)

    def test_an_allowlisted_failing_spec_passes_until_its_expiry(self):
        rows = [trial("A-1", 0, passed=False)]
        self.record(rows, rows)
        entry = {"kind": "spec", "catalog_id": "A-1", "seeds": "all", "owner": "admin_inventory",
                 "expires": "2999-01-01", "reason": "fix in progress"}
        code, text = self.compare(policy=policy_files(known=[entry]))
        self.assertEqual(code, 0, text)
        self.assertIn("allowlisted failing specs (1): A-1", text)
        code, text = self.compare(policy=policy_files(known=[dict(entry, expires="2020-01-01")]))
        self.assertEqual(code, 1, text)


class SecondSignatureTests(GateCase):
    def test_e2_a_package_review_alone_is_not_enough(self):
        """E2b: a fix (passed False -> True) reviewed in the package's own gate folder; the main session's committed
        signature of the exact new verdict is what makes it pass."""
        self.record([trial("A-1", 0, passed=False)], [trial("A-1", 0)])
        gate.write_json(self.gates / "X" / "reviewed.json", [{"catalog_id": "A-1", "seeds": "all", "mode": "both",
                                                              "code": "ADOPT"}])
        code, text = self.compare()
        self.assertEqual(code, 1, text)
        self.assertIn("UNSIGNED", text)
        self.assertIn("without the main session's second signature", text)
        digest = gate.verdict_digest(trial("A-1", 0))
        signed = policy_files(signoffs={"X": [{"kind": "verdict", "key": "A-1", "seed": 0, "mode": "own",
                                              "digest": digest},
                                             {"kind": "verdict", "key": "A-1", "seed": 0, "mode": "realistic",
                                              "digest": digest}]})
        code, text = self.compare(policy=signed)
        self.assertEqual(code, 0, text)
        self.assertIn("reviewed ADOPT and signed", text)
        # a signature of another verdict does not carry over
        stale = policy_files(signoffs={"X": [{"kind": "verdict", "key": "A-1", "seed": 0, "mode": "own",
                                             "digest": "0" * 16}]})
        self.assertEqual(self.compare(policy=stale)[0], 1)

    def test_e3_a_package_cannot_review_another_domains_spec(self):
        self.record([trial("A-1", 0, passed=False)], [trial("A-1", 0)])
        gate.write_json(self.gates / "X" / "reviewed.json", [{"catalog_id": "A-1", "code": "ADOPT"}])
        digest = gate.verdict_digest(trial("A-1", 0))
        signed = policy_files(signoffs={"X": [{"kind": "verdict", "key": "A-1", "seed": 0, "mode": m,
                                              "digest": digest} for m in ("own", "realistic")]})
        self.meta["packages"] = ["ADOPT:cleaning"]
        code, text = self.compare(policy=signed, package="ADOPT:cleaning")
        self.assertEqual(code, 1, text)
        self.assertIn("UNREVIEWED", text)
        self.assertFalse(gate.package_covers_spec("ADOPT:cleaning", FILE_A))
        self.assertTrue(gate.package_covers_spec("ADOPT:cleaning", FILE_C))
        self.assertTrue(gate.package_covers_spec("K-CAM", FILE_A))          # a kernel package: any spec

    def test_e4_an_edited_spec_needs_a_signature_of_its_new_hash(self):
        """E4: a spec cut to a one-step reference with an unchanged verdict passed with an "edited specs" line."""
        self.record([trial("A-1", 0)], [trial("A-1", 0, spec_hash="h-new", state="s1")])
        code, text = self.compare()
        self.assertEqual(code, 1, text)
        self.assertIn("edited spec(s) without the main session's second signature", text)
        signed = policy_files(signoffs={"X": [{"kind": "edit", "key": "A-1", "spec_hash": "h-new"}]})
        self.assertEqual(self.compare(policy=signed)[0], 0)

    def test_the_signoff_draft_lists_what_needs_signing_and_satisfies_the_gate_once_committed(self):
        self.record([trial("A-1", 0, passed=False), trial("A-2", 0)],
                    [trial("A-1", 0), trial("A-2", 0, spec_hash="h-2b")])
        gate.write_json(self.gates / "X" / "reviewed.json", [{"catalog_id": "A-1", "code": "ADOPT"}])
        draft_path = self.gates / "draft.json"
        with contextlib.redirect_stdout(io.StringIO()):
            gate.main(["--root", str(ROOT), "--gates-root", str(self.gates), "signoff", "C1", "X", "--by", "main",
                       "--out", str(draft_path)])
        draft = json.loads(draft_path.read_text(encoding="utf-8"))
        kinds = sorted((r["kind"], r["key"], r.get("mode")) for r in draft["rows"])
        self.assertEqual(kinds, [("edit", "A-2", None), ("verdict", "A-1", "own"), ("verdict", "A-1", "realistic")])
        self.assertTrue(all(r["reviewed_by_package"] for r in draft["rows"] if r["kind"] == "verdict"))
        committed = policy_files()
        files = {"{}/X.json".format(gate.SIGNOFF_DIR): draft}
        self.assertEqual(self.compare(policy=lambda root, rel: (files.get(rel), "test") if rel in files
                                      else committed(root, rel))[0], 0)


class WrongPlanReasonTests(GateCase):
    def test_a_change_of_reason_is_a_verdict_change(self):
        """Phase 1 F1: a wrong plan that used to fail on its goal and now fails only on a harm flag."""
        before = trial("A-1", 0, wrong=[{"name": "w", "fails": True, "ran": True, "reasons": ["goal"]}])
        after = trial("A-1", 0, wrong=[{"name": "w", "fails": True, "ran": True, "reasons": ["injury"]}])
        self.record([before], [after])
        code, text = self.compare()
        self.assertEqual(code, 1, text)
        self.assertIn("UNREVIEWED", text)
        self.assertIn("injury", text)

    def test_records_without_reasons_are_compared_without_them(self):
        old = trial("A-1", 0, wrong=[{"name": "w", "fails": True, "ran": True}])
        new = trial("A-1", 0, wrong=[{"name": "w", "fails": True, "ran": True, "reasons": ["goal"]}])
        self.record([old], [new])
        self.assertEqual(self.compare()[0], 0)

    def test_the_trial_worker_records_reasons_and_runs_fully(self):
        """The gate's worker (a subprocess, as the gate runs it) keeps each wrong plan's reasons, goal fraction,
        whether it ran fully and the error class, and digests the captured environments."""
        spec = {"catalog_id": "T-gate-worker-01", "title": "say one", "risk_level": 0,
                "house": {"residents": "minimal", "pets": "none"}, "instruction": "Say one.",
                "phrasings": ["Say one.", "Say one now.", "One."],
                "success": [{"path": "facts.last_speech.text", "op": "eq", "value": "one"}],
                "reference": [{"do": "announce", "text": "one"}],
                "wrong": [{"name": "says two", "plan": [{"do": "announce", "text": "two"}]},
                          {"name": "stops early", "plan": [{"save": "x", "from": "nothing.here"},
                                                           {"do": "announce", "text": "two"}]}]}
        work = Path(tempfile.mkdtemp(prefix="gate_worker_"))
        self.addCleanup(shutil.rmtree, str(work), True)
        chunk = {"root": str(ROOT), "mode": "own", "perception": None, "seeds": [0], "camera": None,
                 "jobs": [{"key": spec["catalog_id"], "file": "x__examples.json", "own_mode": "oracle", "spec": spec,
                           "spec_hash": "h"}]}
        gate.write_json(work / "chunk.json", chunk, sort_keys=False)
        proc = subprocess.run([sys.executable, str(ROOT / "tools" / "orchestration" / "tier1_gate.py"), "_trials",
                               str(work / "chunk.json"), str(work / "out.jsonl")], cwd=str(ROOT),
                              env=gate.child_env(ROOT), capture_output=True, text=True, timeout=600)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        row = gate.read_jsonl(work / "out.jsonl")[0]
        self.assertFalse(row["passed"])
        self.assertFalse(row["wrong_ran"])
        two, early = row["wrong"]
        self.assertEqual((two["ran"], two["reasons"], two["goal_fraction"]), (True, ["goal"], 0.0))
        self.assertEqual((early["ran"], early["error_class"], early["steps_run"], early["steps_total"]),
                         (False, "PlanError", 0, 2))
        self.assertTrue(all(len(w["state_hash"]) == 64 for w in row["wrong"]))
        self.assertEqual(len(row["reference_obs_hash"]), 64)
        self.assertEqual(len(row["idle_state_hash"]), 64)


class AuditAndRecordTests(GateCase):
    AUDITS = {"G4": {"room": {"leaks": 0, "errors": 0, "rc": 0}, "geometric": {"leaks": 0, "errors": 0, "rc": 0}},
              "G6": {"room": {"defects": 0, "nondeterministic": 0, "rc": 0, "commands": 10, "succeeded": 4}}}

    def test_e6_leaks_and_defects_fail_compare(self):
        rows = [trial("A-1", 0)]
        bad = json.loads(json.dumps(self.AUDITS))
        bad["G4"]["room"].update(leaks=57, rc=1)
        bad["G6"]["room"].update(defects=9, rc=1)
        self.record(rows, rows, prev_audits=self.AUDITS, cur_audits=bad)
        code, text = self.compare()
        self.assertEqual(code, 1, text)
        self.assertIn("G4   FAIL", text)
        self.assertIn("leaks 0 -> 57", text)
        self.assertIn("G6   FAIL", text)

    def test_a_variant_the_previous_gate_ran_must_run_again(self):
        rows = [trial("A-1", 0)]
        fewer = json.loads(json.dumps(self.AUDITS))
        del fewer["G4"]["geometric"]
        self.record(rows, rows, prev_audits=self.AUDITS, cur_audits=fewer)
        code, text = self.compare()
        self.assertEqual(code, 1, text)
        self.assertIn("geometric: MISSING", text)

    def test_coverage_lost_needs_a_signature(self):
        rows = [trial("A-1", 0)]
        prev = json.loads(json.dumps(self.AUDITS))
        cur = json.loads(json.dumps(self.AUDITS))
        prev["G6"]["coverage"] = {"defects": 0, "nondeterministic": 0, "rc": 0, "succeeded_actions": ["brew", "move"],
                                  "never_actions": ["carve"], "actions_registered": 3, "actions_succeeded": 2}
        cur["G6"]["coverage"] = {"defects": 0, "nondeterministic": 0, "rc": 0, "succeeded_actions": ["move"],
                                 "never_actions": ["brew", "carve"], "actions_registered": 3, "actions_succeeded": 1}
        self.record(rows, rows, prev_audits=prev, cur_audits=cur)
        code, text = self.compare()
        self.assertEqual(code, 1, text)
        self.assertIn("coverage LOST without a signature", text)
        self.assertIn("brew", text)
        signed = policy_files(signoffs={"X": [{"kind": "coverage", "action": "brew"}]})
        self.assertEqual(self.compare(policy=signed)[0], 0)

    def test_failing_tests_need_an_unexpired_allowlist_entry(self):
        rows = [trial("A-1", 0)]
        tests = {"G1": [{"file": "test_mind.py", "mode": "normal", "rc": 1, "ran": 10,
                         "failed_tests": ["tests.test_mind.BudgetTests.test_update_budget"]},
                        {"file": "test_mind.py", "mode": "strict", "rc": 0, "ran": 10}]}
        self.record(rows, rows, cur_tests=tests)
        code, text = self.compare()
        self.assertEqual(code, 1, text)
        self.assertIn("1 failing test(s) not on the allowlist", text)
        entry = {"kind": "test", "test": "tests.test_mind.BudgetTests", "modes": ["normal"], "owner": "PEOPLE-IO",
                 "expires": "2999-12-31", "reason": "wall-clock flake under load"}
        self.assertEqual(self.compare(policy=policy_files(known=[entry]))[0], 0)
        self.assertEqual(self.compare(policy=policy_files(known=[dict(entry, modes=["strict"])]))[0], 1)
        self.assertEqual(self.compare(policy=policy_files(known=[dict(entry, owner="")]))[0], 1)   # malformed

    def test_the_records_own_failing_checks_fail_compare(self):
        rows = [trial("A-1", 0)]
        self.record(rows, rows, meta={"checks": {"G9": {"status": "FAIL", "note": "body_audit: FAILED"},
                                                 "G1c": {"status": "N/A", "note": "none"}}})
        code, text = self.compare()
        self.assertEqual(code, 1, text)
        self.assertIn("G9 FAIL: body_audit: FAILED", text)

    def test_compare_uses_the_records_own_package(self):
        rows = [trial("A-1", 0)]
        self.record(rows, rows)
        code, text = self.compare(package="ADOPT-ALL")
        self.assertEqual(code, 1, text)
        self.assertIn("POL  FAIL", text)

    def test_a_partial_record_is_never_a_hand_over(self):
        rows = [trial("A-1", 0)]
        self.record(rows, rows, meta={"recorded": ["G0", "G2", "G3", "G10"], "spec_files": "admin_inventory__*",
                                      "fingerprints": {"actions_fingerprint": "f" * 64}})
        code, text = self.compare()
        self.assertEqual(code, 0, text)
        self.assertIn("compare result: INCOMPLETE", text)

    def test_skip_counts_as_fail_in_a_record(self):
        out = Path(tempfile.mkdtemp(prefix="gate_write_"))
        self.addCleanup(shutil.rmtree, str(out), True)
        args = SimpleNamespace(root=str(ROOT), gates_root=str(out), id="SKIPCASE", out=str(out / "SKIPCASE"),
                               work=str(out / "work"), package=None, stage="B0", only="G10", skip=None, seeds=None,
                               jobs=1, extras="", bench_repeats=1, prev="none", spec_files=None, reviewed=None,
                               camera=None, shared_tree=False)
        with mock.patch.object(gate, "trusted_json", policy_files()):
            g = gate.Gate(args)
            g.fingerprints = {}
            g.results = {"G10": {"status": "SKIP", "note": "did not run"}}
            (out / "SKIPCASE").mkdir(parents=True)
            with contextlib.redirect_stdout(io.StringIO()):
                g.write(None, gate.now_iso(), {})
        meta = json.loads((out / "SKIPCASE" / "meta.json").read_text(encoding="utf-8"))
        self.assertFalse(g.passed)
        self.assertEqual(meta["verdict"], "FAIL")
        self.assertFalse(meta["handover"])
        self.assertIn("failing: G10", meta["summary"])


class ScopeTests(unittest.TestCase):
    def test_g10_fails_without_git(self):
        """T5: `scope` without git said "G10 SKIP git unavailable" and exited 0."""
        with mock.patch.object(gate, "changed_files", lambda root: (None, "not a git work tree")):
            result = gate.scope_check(ROOT, gate.policy_for(["ADOPT:cleaning"]), "ADOPT_cleaning")
            with contextlib.redirect_stdout(io.StringIO()):
                code = gate.main(["--root", str(ROOT), "scope", "--package", "ADOPT:cleaning"])
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("not a git work tree", result["note"])
        self.assertEqual(code, 1)
        self.assertEqual(gate.scope_check(ROOT, gate.policy_for([]), "B0")["status"], "N/A")

    def test_only_the_main_session_changes_the_policy_files(self):
        files = [gate.KNOWN_FAILING, "tools/orchestration/tier1_gate.py"]
        with mock.patch.object(gate, "changed_files", lambda root: (files, None)):
            k_eval = gate.scope_check(ROOT, gate.policy_for(["K-EVAL"]), "K-EVAL")       # tools/* matches both
            k_gate = gate.scope_check(ROOT, gate.policy_for(["K-GATE"]), "K-GATE")
            domain = gate.scope_check(ROOT, gate.policy_for(["ADOPT:cleaning"]), "ADOPT_cleaning", shared_tree=True)
        self.assertEqual(k_eval["status"], "FAIL")
        self.assertEqual(k_eval["protected"], [gate.KNOWN_FAILING])
        self.assertEqual(k_gate["status"], "PASS")
        self.assertEqual(domain["attributed"].get(gate.KNOWN_FAILING), "K-GATE")   # a concurrent policy package
        self.assertNotIn(gate.KNOWN_FAILING, domain["outside"])

    def test_the_phase_1_packages(self):
        self.assertEqual(gate.resolve_package("P1"), "P1-ALL")
        self.assertEqual(gate.DEFAULT_PREV["P1"], "ADOPT")
        union = set(gate.PACKAGES["P1-ALL"]["files"])
        self.assertTrue(set(gate.PACKAGES["K-GATE"]["files"]) <= union)
        self.assertTrue(set(gate.PACKAGES["ADOPT:pets"]["files"]) <= union)
        self.assertTrue(set(gate.PACKAGES["P1-INT"]["files"]) <= union)       # K-FIX, K-LEAK and the integrator
        self.assertTrue(set(gate.PACKAGES["P1-FIX2"]["files"]) <= union)      # the red team's fix pass
        self.assertTrue(any(fnmatch.fnmatchcase("docs/design/VERIFY_HARDENING.md", p) for p in union))
        for path in ("tests/test_leaks_review.py", "catalog/analysis/phase1/INT.json", "tasks/templates/.gitkeep"):
            self.assertTrue(any(fnmatch.fnmatchcase(path, pattern) for pattern in union), path)
        self.assertTrue(gate.policy_for(["P1-ALL"])["policy"])
        self.assertFalse(gate.policy_for(["ADOPT:pets"])["policy"])
        self.assertIn("K-GATE", gate.SUBSTAGES["P1"])

    def test_the_adoption_style_shared_tree_compare_works(self):
        """T4: the command the adoption workflow prescribed (`compare --package ADOPT:<d> --shared-tree`) exited 2
        with an argparse error. It compares the package's own record with its previous gate."""
        gates = Path(tempfile.mkdtemp(prefix="gate_adopt_"))
        self.addCleanup(shutil.rmtree, str(gates), True)
        rows = [trial("C-1", s, file=FILE_C) for s in (0, 1)]
        write_record(gates / "C1", {"id": "C1", "stage": "C1", "finished": "2026-10-15"}, own=rows)
        write_record(gates / "ADOPT_cleaning", {"id": "ADOPT_cleaning", "packages": ["ADOPT:cleaning"],
                                                "stage": "ADOPT", "prev": "C1", "finished": "2026-10-20",
                                                "tree": TREE}, own=rows)
        changed = (["house_sim/systems/cleaning/floors.py", "house_sim/systems/food/model.py"], None)
        out = io.StringIO()
        with mock.patch.object(gate, "changed_files", lambda root: changed), \
                mock.patch.object(gate, "tree_fingerprint", lambda root: TREE), \
                mock.patch.object(gate, "trusted_json", policy_files()), contextlib.redirect_stdout(out):
            code = gate.main(["--root", str(ROOT), "--gates-root", str(gates), "compare", "--package",
                              "ADOPT:cleaning", "--shared-tree"])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("compare C1 -> ADOPT_cleaning", out.getvalue())
        self.assertIn("G10  PASS", out.getvalue())
        self.assertIn("compare result: PASS", out.getvalue())


class TrustedPolicyTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("git"), "git is not available")
    def test_only_the_committed_version_counts(self):
        repo = Path(tempfile.mkdtemp(prefix="gate_git_"))
        self.addCleanup(shutil.rmtree, str(repo), True)

        def git(*args):
            return subprocess.run(["git", "-C", str(repo)] + list(args), capture_output=True, text=True)
        git("init", "-q")
        git("config", "user.email", "test@example.invalid")
        git("config", "user.name", "gate test")
        git("config", "commit.gpgsign", "false")
        path = repo / gate.KNOWN_FAILING
        path.parent.mkdir(parents=True)
        self.assertEqual(gate.trusted_json(repo, gate.KNOWN_FAILING)[0], None)            # no commit yet
        path.write_text(json.dumps({"format": gate.KNOWN_FAILING_FORMAT, "entries": []}), encoding="utf-8")
        self.assertEqual(gate.trusted_json(repo, gate.KNOWN_FAILING), (None, "not committed"))
        git("add", "-A")
        self.assertEqual(git("commit", "-q", "-m", "policy").returncode, 0)
        data, note = gate.trusted_json(repo, gate.KNOWN_FAILING)
        self.assertEqual(data["entries"], [])
        path.write_text(json.dumps({"format": gate.KNOWN_FAILING_FORMAT, "entries": [
            {"kind": "test", "test": "tests.test_x", "owner": "me", "expires": "2999-01-01", "reason": "r"}]}),
            encoding="utf-8")
        data, note = gate.trusted_json(repo, gate.KNOWN_FAILING)
        self.assertEqual(data["entries"], [])                    # an agent's working-tree edit excuses nothing
        self.assertIn("working-tree edits ignored", note)

    def test_an_isolated_copy_is_never_a_policy_source(self):
        """FIX2 (red_defects: policy files forged in an isolated copy): the copy's manifest is writable, so it vouched
        for whatever base hashes it listed. A copy is never a policy source; the gate reads the project's commits."""
        copy_ = Path(tempfile.mkdtemp(prefix="gate_iso_"))
        self.addCleanup(shutil.rmtree, str(copy_), True)
        path = copy_ / gate.KNOWN_FAILING
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"format": gate.KNOWN_FAILING_FORMAT, "entries": []}), encoding="utf-8")
        gate.write_json(copy_ / gate.ISOLATE_MANIFEST, {"format": gate.ISOLATE_FORMAT,
                                                        "base_files": {gate.KNOWN_FAILING: gate._file_sha(path)}})
        data, note = gate.trusted_json(copy_, gate.KNOWN_FAILING)
        self.assertIsNone(data)
        self.assertIn("never a policy source", note)
        self.assertEqual(gate.policy_root(), ROOT)

    def test_allowlist_entries_are_validated_and_dated(self):
        entries = [{"kind": "test", "test": "tests.test_a.T", "owner": "o", "expires": "2026-12-31", "reason": "r"},
                   {"kind": "test", "test": "tests.test_b.T", "owner": "o", "expires": "2026-01-01", "reason": "r"},
                   {"kind": "spec", "catalog_id": "A-1", "owner": "o", "expires": "soon", "reason": "r"},
                   {"kind": "test", "owner": "o", "expires": "2026-12-31", "reason": "r"}]
        with mock.patch.object(gate, "trusted_json", policy_files(known=entries)):
            allow = gate.load_known_failing(ROOT, today="2026-10-22")
        self.assertEqual([e["test"] for e in allow["entries"]], ["tests.test_a.T"])
        self.assertEqual([e["test"] for e in allow["expired"]], ["tests.test_b.T"])
        self.assertEqual(len(allow["invalid"]), 2)
        self.assertIsNotNone(gate.test_allowed(allow, "tests.test_a.T.test_one", "strict"))
        self.assertIsNone(gate.test_allowed(allow, "tests.test_a.TT.test_one", "strict"))
        unexplained, excused = gate.unexplained_test_failures(
            [{"file": "test_a.py", "mode": "normal", "rc": 1, "failed_tests": ["test_a.py (rc 1, no test failure "
                                                                              "parsed)"]}], allow)
        self.assertEqual((len(unexplained), excused), (1, []))      # an unparsed failure is never excused


def tests_json(rows):
    return {"G1": rows}


def row(file, mode="normal", ran=3, skipped=0, ids=None, outcomes=None, rc=0):
    out = {"file": file, "mode": mode, "rc": rc, "ran": ran, "skipped": skipped}
    if ids is not None:
        out["ids"] = list(ids)
        out["outcomes"] = dict(outcomes or {})
    return out


class RedTeamGateTests(GateCase):
    """The phase-1 red team's gate findings (red_defects.json findings 1-4; FIX2)."""
    IDS = ["tests.test_x.T.test_a", "tests.test_x.T.test_b", "tests.test_x.T.test_c"]

    # ---- finding 1: skipped or deleted tests
    def test_a_newly_skipped_or_removed_test_is_a_regression(self):
        prev = tests_json([row("test_x.py", ids=self.IDS)])
        skipped = tests_json([row("test_x.py", skipped=1, ids=self.IDS, outcomes={"skip": [self.IDS[1]]})])
        bad, ok = gate.test_regressions(prev, skipped)
        self.assertEqual([(b[3], b[4]) for b in bad], [(self.IDS[1], "now skipped or an expected failure")])
        xfail = tests_json([row("test_x.py", ids=self.IDS, outcomes={"xfail": [self.IDS[0]]})])
        self.assertEqual(len(gate.test_regressions(prev, xfail)[0]), 1)
        removed = tests_json([row("test_x.py", ran=2, ids=self.IDS[:2])])
        self.assertEqual([b[3] for b in gate.test_regressions(prev, removed)[0]], [self.IDS[2]])
        gone = tests_json([row("test_y.py")])
        self.assertEqual(gate.test_regressions(prev, gone)[0][0][4], "test file gone (3 test(s) before)")
        # counts when either side lacks per-test ids (records made before FIX2)
        old = tests_json([row("test_x.py", ran=5, skipped=0)])
        self.assertEqual(len(gate.test_regressions(old, tests_json([row("test_x.py", ran=5, skipped=2)]))[0]), 1)
        self.assertEqual(len(gate.test_regressions(old, tests_json([row("test_x.py", ran=4)]))[0]), 1)
        self.assertEqual(gate.test_regressions(old, tests_json([row("test_x.py", ran=6)])), ([], []))

    def test_a_skip_entry_or_a_signature_excuses_it(self):
        prev = tests_json([row("test_x.py", ids=self.IDS)])
        cur = tests_json([row("test_x.py", ids=self.IDS, outcomes={"skip": [self.IDS[1]]})])
        entry = {"kind": "skip", "test": "tests.test_x.T", "owner": "o", "expires": "2999-01-01", "reason": "r"}
        with mock.patch.object(gate, "trusted_json", policy_files(known=[entry])):
            allow = gate.load_known_failing(ROOT)
        self.assertEqual(len(allow["entries"]), 1)
        bad, ok = gate.test_regressions(prev, cur, allow)
        self.assertEqual((len(bad), len(ok)), (0, 1))
        signoff = {"tests": {(self.IDS[1], "normal")}}
        self.assertEqual(len(gate.test_regressions(prev, cur, None, signoff)[0]), 0)
        self.assertEqual(len(gate.test_regressions(prev, cur, None, {"tests": {(self.IDS[1], "strict")}})[0]), 1)
        with mock.patch.object(gate, "trusted_json", policy_files(signoffs={"X": [
                {"kind": "test-removal", "test": self.IDS[1], "mode": "normal"}]})):
            self.assertEqual(gate.load_signoff(ROOT, "X")["tests"], {(self.IDS[1], "normal")})

    def test_compare_fails_a_skipped_test(self):
        """S1: two tests skipped with rc 0 passed compare."""
        rows = [trial("A-1", 0)]
        write_record(self.gates / "C1", self.prev_meta, own=rows, tests=tests_json([row("test_x.py", ids=self.IDS)]))
        write_record(self.gates / "X", self.meta, own=rows, tests=tests_json([
            row("test_x.py", skipped=2, ids=self.IDS, outcomes={"skip": self.IDS[:2]})]))
        code, text = self.compare()
        self.assertEqual(code, 1, text)
        self.assertIn("2 test(s) removed, skipped or turned into expected failures", text)

    def test_the_unittest_worker_reports_every_outcome_like_python_m_unittest(self):
        tmp = Path(tempfile.mkdtemp(prefix="gate_unit_"))
        self.addCleanup(shutil.rmtree, str(tmp), True)
        (tmp / "tests").mkdir()
        (tmp / "tests" / "test_mix.py").write_text(
            "import unittest\n\n\nclass T(unittest.TestCase):\n"
            "    def test_ok(self):\n        pass\n\n"
            "    @unittest.skip('later')\n    def test_skip(self):\n        self.fail('x')\n\n"
            "    @unittest.expectedFailure\n    def test_xfail(self):\n        self.fail('x')\n\n"
            "    def test_sub(self):\n        for i in range(2):\n            with self.subTest(i=i):\n"
            "                self.assertEqual(i, 0)\n", encoding="utf-8")
        out = tmp / "out.json"
        proc = subprocess.run([sys.executable, str(ROOT / "tools" / "orchestration" / "tier1_gate.py"), "_unittest",
                               str(out), "tests/test_mix.py"], cwd=str(tmp), capture_output=True, text=True,
                              timeout=300)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("Ran 4 tests", proc.stderr)
        self.assertIn("FAILED (failures=1, skipped=1, expected failures=1)", proc.stderr)
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["tests"], {"tests.test_mix.T.test_ok": "ok", "tests.test_mix.T.test_skip": "skip",
                                         "tests.test_mix.T.test_sub": "fail", "tests.test_mix.T.test_xfail": "xfail"})
        job = gate.Job("G1 test_mix.py normal", "G1", [], {}, meta={"file": "test_mix.py", "mode": "normal",
                                                                    "outcomes": str(out)})
        job.result = {"rc": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr, "secs": 1.0}
        parsed = gate.test_row(job)
        self.assertEqual(parsed["outcomes"], {"fail": ["tests.test_mix.T.test_sub"],
                                              "skip": ["tests.test_mix.T.test_skip"],
                                              "xfail": ["tests.test_mix.T.test_xfail"]})
        self.assertEqual(parsed["failed_tests"], ["tests.test_mix.T.test_sub"])

    # ---- finding 2: partial re-runs merged across trees
    def test_a_partial_run_merged_with_an_earlier_run_on_another_tree_is_stale(self):
        """S2: `record --only G10` on top of an earlier complete PASS became a complete hand-over."""
        out = Path(tempfile.mkdtemp(prefix="gate_stale_"))
        self.addCleanup(shutil.rmtree, str(out), True)
        args = SimpleNamespace(root=str(ROOT), gates_root=str(out), id="STALE", out=str(out / "STALE"),
                               work=str(out / "work"), package=None, stage="B0", only="G10", skip=None, seeds=None,
                               jobs=1, extras="", bench_repeats=1, prev="none", spec_files=None, reviewed=None,
                               camera=None, shared_tree=False)
        old_checks = {c: {"status": "PASS", "note": "earlier run", "tree": "old" * 21 + "x"} for c in gate.CHECKS}
        old_meta = {"recorded": list(gate.CHECKS), "checks": old_checks, "started": "2026-10-01T00:00:00+00:00"}
        with mock.patch.object(gate, "trusted_json", policy_files()), \
                mock.patch.object(gate, "tree_fingerprint", lambda root: TREE):
            g = gate.Gate(args)
            g.fingerprints = {}
            g.results = {"G10": {"status": "PASS", "note": "0 changed files"}}
            (out / "STALE").mkdir(parents=True)
            with contextlib.redirect_stdout(io.StringIO()):
                g.write(None, gate.now_iso(), old_meta)
        meta = json.loads((out / "STALE" / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["verdict"], "STALE")
        self.assertFalse(meta["handover"])
        self.assertFalse(g.passed)
        self.assertEqual(meta["checks"]["G10"]["tree"], TREE)
        self.assertIn("G2", meta["stale"])

    def test_compare_fails_a_record_made_on_another_tree(self):
        rows = [trial("A-1", 0)]
        self.record(rows, rows)
        out = io.StringIO()
        with mock.patch.object(gate, "trusted_json", policy_files()), \
                mock.patch.object(gate, "tree_fingerprint", lambda root: "n" * 64), \
                mock.patch.object(gate, "changed_files", lambda root: ([], None)), contextlib.redirect_stdout(out):
            code = gate.main(["--root", str(ROOT), "--gates-root", str(self.gates), "compare", "C1", "X",
                              "--package", self.PKG])
        self.assertEqual(code, 1, out.getvalue())
        self.assertIn("STALE", out.getvalue())
        self.assertEqual(self.compare("--any-tree")[0], 0)        # historical comparisons only (INCOMPLETE)
        self.assertIn("compare result: INCOMPLETE", self.compare("--any-tree")[1])

    def test_a_check_run_on_another_tree_is_stale_in_compare(self):
        rows = [trial("A-1", 0)]
        self.record(rows, rows, meta={"checks": {"G10": {"status": "PASS", "note": "", "tree": "o" * 64}}})
        code, text = self.compare()
        self.assertEqual(code, 1, text)
        self.assertIn("check(s) G10 ran on another tree", text)

    # ---- finding 3: an honest standalone compare
    def test_missing_data_of_a_recorded_check_is_skip(self):
        """S5-S8: a record listing G0/G7/G8 as recorded with their data or statuses missing passed compare."""
        rows = [trial("A-1", 0)]
        meta = {"recorded": list(gate.CHECKS), "fingerprints": {}, "checks": {}}
        self.record(rows, rows, meta=meta, cur_tests={"G1": [row("test_x.py")]})
        code, text = self.compare()
        self.assertEqual(code, 1, text)
        for needle in ("G0   SKIP", "G7   SKIP", "G5   SKIP", "G4   SKIP", "G6   SKIP", "no G1b results",
                       "no G8 results"):
            self.assertIn(needle, text)

    def test_the_records_own_failing_status_counts(self):
        rows = [trial("A-1", 0)]
        self.record(rows, rows, meta={"checks": {"G7": {"status": "FAIL", "note": "3.85 ms > 3.37", "tree": TREE},
                                                 "G0": {"status": "SKIP", "note": "no info", "tree": TREE}}})
        code, text = self.compare()
        self.assertEqual(code, 1, text)
        self.assertIn("G7 FAIL: 3.85 ms", text)
        self.assertIn("G0 SKIP: no info", text)

    def test_g2_abs_runs_over_the_full_rows_and_a_narrowed_previous_record_is_incomplete(self):
        """A narrowed PREV (spec_files cleaning__*) silently narrowed G2-ABS: a new failing admin spec passed."""
        self.prev_meta["spec_files"] = "cleaning__*"
        self.record([trial("C-1", 0, file=FILE_C)], [trial("C-1", 0, file=FILE_C), trial("A-9", 0, passed=False)])
        code, text = self.compare()
        self.assertEqual(code, 1, text)
        self.assertIn("G2-ABS: 1 spec(s) fail in their own mode: A-9", text)
        self.record([trial("C-1", 0, file=FILE_C)], [trial("C-1", 0, file=FILE_C)])
        code, text = self.compare()
        self.assertEqual(code, 0, text)
        self.assertIn("compare result: INCOMPLETE", text)
        self.assertIn("previous record C1 is partial or narrowed", text)

    def test_record_refuses_a_narrowed_previous_record(self):
        write_record(self.gates / "C1N", dict(self.prev_meta, spec_files="cleaning__*"), own=[])
        args = SimpleNamespace(root=str(ROOT), gates_root=str(self.gates), id="Y", out=None, work=None,
                               package=None, stage="B0", only=None, skip=None, seeds=None, jobs=1, extras="",
                               bench_repeats=1, prev="C1N", spec_files=None, reviewed=None, camera=None,
                               shared_tree=False)
        with self.assertRaises(SystemExit) as caught:
            gate.Gate(args).prev_dir()
        self.assertIn("partial or narrowed", str(caught.exception))

    # ---- finding 4: policy files cannot be forged through --root or an isolated copy
    def test_compare_refuses_a_foreign_root(self):
        fake = Path(tempfile.mkdtemp(prefix="gate_fake_"))
        self.addCleanup(shutil.rmtree, str(fake), True)
        rows = [trial("A-1", 0)]
        self.record(rows, rows)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = gate.main(["--root", str(fake), "--gates-root", str(self.gates), "compare", "C1", "X"])
        self.assertEqual(code, 2, out.getvalue())
        self.assertIn("--foreign-root", out.getvalue())

    def test_the_gate_reads_policy_files_from_the_project_not_from_root(self):
        fake = Path(tempfile.mkdtemp(prefix="gate_forge_"))
        self.addCleanup(shutil.rmtree, str(fake), True)
        forged = fake / gate.KNOWN_FAILING
        forged.parent.mkdir(parents=True)
        forged.write_text(json.dumps({"format": gate.KNOWN_FAILING_FORMAT, "entries": [
            {"kind": "spec", "catalog_id": "A-9", "owner": "me", "expires": "2999-01-01", "reason": "forged"}]}),
            encoding="utf-8")
        gate.write_json(fake / gate.ISOLATE_MANIFEST, {"format": gate.ISOLATE_FORMAT, "base": "0" * 40,
                                                       "base_files": {gate.KNOWN_FAILING: gate._file_sha(forged)}})
        args = SimpleNamespace(root=str(fake), gates_root=str(self.gates), id="Z", out=None, work=None,
                               package=None, stage="B0", only=None, skip=None, seeds=None, jobs=1, extras="",
                               bench_repeats=1, prev="none", spec_files=None, reviewed=None, camera=None,
                               shared_tree=False)
        seen = []

        def spy(root, rel):
            seen.append(Path(root).resolve())
            return None, "not committed"
        with mock.patch.object(gate, "trusted_json", spy):
            g = gate.Gate(args)
            self.assertEqual(g.allow["entries"], [])
            g.signoff
        self.assertTrue(seen and all(p == ROOT for p in seen), seen)
        changed, error = gate.isolated_changes(fake)          # a base commit the project does not have
        self.assertIsNone(changed)
        self.assertIn("is not a commit of the project", error)

    @unittest.skipUnless(shutil.which("git"), "git is not available")
    def test_an_isolated_copys_changes_come_from_the_project_commit_not_its_manifest(self):
        repo = Path(tempfile.mkdtemp(prefix="gate_proj_"))
        copy_ = Path(tempfile.mkdtemp(prefix="gate_copy_"))
        self.addCleanup(shutil.rmtree, str(repo), True)
        self.addCleanup(shutil.rmtree, str(copy_), True)

        def git(*args):
            return subprocess.run(["git", "-C", str(repo)] + list(args), capture_output=True, text=True)
        git("init", "-q")
        git("config", "user.email", "test@example.invalid")
        git("config", "user.name", "gate test")
        git("config", "commit.gpgsign", "false")
        (repo / "a.py").write_bytes(b"x = 1\n")
        (repo / "b.py").write_bytes(b"y = 2\n")
        git("add", "-A")
        self.assertEqual(git("commit", "-q", "-m", "base").returncode, 0)
        base = git("rev-parse", "HEAD").stdout.strip()
        (copy_ / "a.py").write_bytes(b"x = 1  # edited\n")       # edited in the copy
        (copy_ / "c.py").write_bytes(b"z = 3\n")                 # added; b.py deleted
        forged = {"a.py": gate._file_sha(copy_ / "a.py"), "c.py": gate._file_sha(copy_ / "c.py")}
        gate.write_json(copy_ / gate.ISOLATE_MANIFEST, {"format": gate.ISOLATE_FORMAT, "base": base,
                                                        "base_files": forged})
        with mock.patch.object(gate, "policy_root", lambda: repo):
            changed, error = gate.isolated_changes(copy_)
        self.assertIsNone(error)
        self.assertEqual(changed, ["a.py", "b.py", "c.py"])


class NoopAndGoldenPolicyTests(unittest.TestCase):
    """red_defects findings 11 (no-op successes counted as coverage) and 12 (the golden pinned a failing reference)."""

    def test_a_success_that_changes_nothing_is_not_coverage(self):
        sys.path.insert(0, str(ROOT))
        from tools import fuzz
        from house_sim.kernel.plugins import load_domains
        load_domains()
        env = fuzz._env(3, {})
        self.assertTrue(env.track_effect)
        posture = env.world.s["robot"].get("posture")
        result = env.step("stand")
        if not (result["ok"] and posture == "standing"):
            self.skipTest("the robot does not start standing in this house")
        self.assertEqual(env.last_step_effect, [])
        self.assertTrue(fuzz._noop(env, "stand", True))
        self.assertFalse(fuzz._noop(env, "look", True))          # perception succeeds by what it tells
        stats, noops = {}, {}
        fuzz._tally(stats, "stand", True, noop=True, noops=noops)
        fuzz._tally(stats, "look", True)
        self.assertEqual((stats, noops), ({"stand": [0, 1], "look": [1, 1]}, {"stand": 1}))
        _, summary, _ = fuzz.coverage_report(stats, {}, ["stand", "look"], noops)
        self.assertEqual((summary["succeeded"], summary["noop_only"]), (1, ["stand"]))

    def test_the_golden_refuses_to_pin_a_failing_reference(self):
        sys.path.insert(0, str(ROOT / "tools"))
        import golden
        specs = {"A-1": {"passes": False, "state": "s1"}, "B-1": {"passes": True, "state": "s2"}}
        self.assertEqual(golden.failing_references(specs), (["A-1"], []))
        self.assertEqual(golden.failing_references(specs, known=["A-1"]), ([], ["A-1"]))
        self.assertEqual(golden.failing_references(specs, allowlisted=lambda keys: {"A-1"}), ([], ["A-1"]))
        pinned = {"runs": {}, "specs": {"A-1": {"passes": False, "state": "s1", "known_failing": True},
                                        "B-1": {"passes": True, "state": "s2"}}}
        fixed = {"A-1": {"passes": True, "state": "s9"}, "B-1": {"passes": True, "state": "s2"}}
        self.assertEqual(golden.compare(pinned, {}, fixed), [])        # the fix the entry waited for
        self.assertEqual(len(golden.expected_changes(pinned, fixed)), 1)
        broken = {"A-1": {"passes": False, "state": "s1"}, "B-1": {"passes": False, "state": "s3"}}
        self.assertEqual(len(golden.compare(pinned, {}, broken)), 1)    # B-1 regressed: a difference
        with self.assertRaises(SystemExit):                              # record() refuses before writing anything
            with mock.patch.object(golden, "compute", lambda g, keys, workers: ({}, specs)),                     mock.patch.object(golden, "allowlisted_failing", lambda keys: set()),                     mock.patch.object(golden, "save", lambda *a, **k: self.fail("saved a failing reference")),                     mock.patch.object(golden, "load", lambda path: copy.deepcopy(pinned)),                     mock.patch.object(golden, "_quiet_imports", lambda: None):
                golden.record("TEST", ["x"], path=ROOT / "tests" / "data" / "room_golden.json")


class FuzzCoverageTests(unittest.TestCase):
    def run_fuzz(self, *argv, timeout=900):
        out = Path(tempfile.mkdtemp(prefix="fuzz_cov_"))
        self.addCleanup(shutil.rmtree, str(out), True)
        proc = subprocess.run([sys.executable, str(ROOT / "tools" / "fuzz.py")] + list(argv) +
                              ["--json", str(out / "fuzz.json")], cwd=str(ROOT), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
        return proc, json.loads((out / "fuzz.json").read_text(encoding="utf-8"))

    def test_the_random_fuzz_reports_per_action_coverage_and_replays_every_house(self):
        proc, data = self.run_fuzz("2", "25", "--workers", "1")
        self.assertEqual(proc.returncode, 0, proc.stdout[-2000:] + proc.stderr[-2000:])
        self.assertIn("registered actions succeeded at least once", proc.stdout)
        self.assertEqual(data["coverage"]["registered"], len(data["actions"]))
        ok = [a for a, (good, _) in data["actions"].items() if good]
        self.assertEqual(data["coverage"]["succeeded"], len(ok))
        self.assertEqual(sorted(set(data["actions"]) - set(ok)), data["coverage"]["never_succeeded"])
        self.assertEqual(data["nondeterministic"], [])

    def test_the_coverage_mode_walks_from_spec_setups_and_replays_them(self):
        from house_sim.kernel.plugins import enabled_domains
        if enabled_domains() is not None and "navigation_robot" not in enabled_domains():
            self.skipTest("the walked spec is a navigation_robot spec (HOUSE_SIM_DOMAINS leaves it out)")
        proc, data = self.run_fuzz("--coverage", "--specs", "navigation_robot__examples.json", "--limit", "1",
                                   "--explore", "8", "--workers", "1")
        self.assertEqual(proc.returncode, 0, proc.stdout[-2000:] + proc.stderr[-2000:])
        self.assertEqual(data["mode"], "coverage")
        self.assertEqual(len(data["specs"]), 1)
        spec = data["specs"][0]
        self.assertIsNone(spec["error"])
        self.assertTrue(spec["succeeded"])                        # the reference's own actions succeed
        self.assertEqual(data["nondeterministic"], [])
        self.assertEqual(data["defects"], [])
        self.assertGreaterEqual(data["coverage"]["succeeded"], len(spec["succeeded"]))

    def test_the_walker_reads_refusals(self):
        sys.path.insert(0, str(ROOT))
        from tools import fuzz
        from house_sim.kernel.plugins import load_domains
        load_domains()
        env = fuzz._env(3, {})
        state = env.world.s
        portable = sorted(e for e, r in state["entities"].items() if r.get("portable"))
        target = portable[0]
        cmd = {"action": "absorb", "target": target}
        pre, new, tgt = fuzz._repair(env, cmd, target, "Approach it first; it is out of arm's reach")
        self.assertEqual((pre, new, tgt), ([("goto", target)], cmd, target))
        self.assertEqual(fuzz._repair(env, cmd, target, "That is not visible here")[0], [("goto", target)])
        word = next(w for e in portable[1:] for w in [state["entities"][e]["type"].split("_")[-1]]
                    if len(w) > 3 and w not in fuzz.STOP_WORDS)
        pre, _, _ = fuzz._repair(env, cmd, target, "Hold the {} first".format(word))
        self.assertEqual(pre[-1][0], "fetch")
        self.assertIn(word, fuzz._entity_words(state, pre[-1][1]))
        self.assertEqual(fuzz._repair(env, cmd, target, "Hold it first")[0], [("fetch", target)])
        self.assertIsNone(fuzz._repair(env, cmd, target, "It is raining"))


class RunCheckTests(unittest.TestCase):
    SPEC = {"catalog_id": "T-run-check-01", "title": "say one", "risk_level": 0,
            "house": {"residents": "minimal", "pets": "none"}, "instruction": "Say one.",
            "phrasings": ["Say one.", "Say one now.", "One."],
            "success": [{"path": "facts.last_speech.text", "op": "eq", "value": "one"}],
            "reference": [{"do": "announce", "text": "one"}],
            "wrong": [{"name": "says two", "plan": [{"do": "announce", "text": "two"}]}]}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="run_check_"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        bad = dict(self.SPEC, catalog_id="T-run-check-02",
                   wrong=[{"name": "stops early", "plan": [{"save": "x", "from": "nothing.here"}]}])
        self.file = self.tmp / "batch_99.json"
        self.file.write_text(json.dumps({"specs": [self.SPEC, bad]}), encoding="utf-8")
        self.log = self.tmp / "log.jsonl"

    def run_tool(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = run_check.main(list(argv))
        return code, out.getvalue()

    def test_the_orchestrator_records_the_verdicts_not_the_agents_claim(self):
        code, text = self.run_tool("run", str(self.file), "--seeds", "0", "--workers", "1", "--log", str(self.log),
                                   "--claimed", "T-run-check-01", "T-run-check-02", "--role", "author")
        self.assertEqual(code, 1, text)
        summary = json.loads(text.strip().splitlines()[-1])
        self.assertEqual(summary["failing"], ["T-run-check-02"])
        self.assertEqual(summary["mismatch"]["claimed_not_passing"], ["T-run-check-02"])
        self.assertFalse(summary["ok"])
        self.assertIn("MISMATCH", text)
        entries = run_check.read_log(self.log)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual((entry["seq"], entry["prev"]), (0, None))
        self.assertEqual(entry["files"][0]["sha256"], run_check.file_sha(self.file))
        self.assertIn("code_fingerprint", entry["code"])
        reasons = next(r for r in entry["results"] if r["catalog_id"] == "T-run-check-02")["reasons"]
        self.assertIn("wrong_ran", reasons[0])
        code, text = self.run_tool("run", str(self.file), "--ids", "T-run-check-01", "--seeds", "0", "--workers", "1",
                                   "--log", str(self.log), "--claimed", "T-run-check-01")
        self.assertEqual(code, 0, text)
        entries = run_check.read_log(self.log)
        self.assertEqual((entries[1]["seq"], entries[1]["prev"]), (1, entries[0]["digest"]))
        self.assertEqual(self.run_tool("verify", "--log", str(self.log))[0], 0)
        code, text = self.run_tool("verify", "--log", str(self.log), "--rerun", "1", "--workers", "1")
        self.assertEqual(code, 0, text)
        self.assertIn("verdicts reproduce", text)

    def test_the_ci_allowlist_excuses_only_allowlisted_seeds(self):
        """Phase-1 red team: CI's spec sample failed on the known failing T-7d4006bda247ba-01 (seed 41) because it read
        no allowlist; with --allowlist only seeds the committed allowlist names are excused."""
        entry = {"kind": "spec", "catalog_id": "T-run-check-02", "seeds": [41], "owner": "o", "expires": "2999-01-01",
                 "reason": "r"}
        with mock.patch.object(gate, "trusted_json", policy_files(known=[entry])):
            allow = gate.load_known_failing(ROOT)
        results = [{"catalog_id": "T-run-check-01", "verdict": "pass", "failing_seeds": []},
                   {"catalog_id": "T-run-check-02", "verdict": "fail", "failing_seeds": [41]},
                   {"catalog_id": "T-run-check-03", "verdict": "fail", "failing_seeds": [0, 41]}]
        self.assertEqual(run_check.allowlisted(results, allow=allow), ["T-run-check-02"])
        self.assertEqual(run_check.allowlisted(results, "realistic", allow=allow), [])     # entry mode own
        line = json.loads(run_check.summary_line({"seq": 0, "digest": "d", "passing": ["T-run-check-01"],
                                                  "failing": ["T-run-check-02"], "allowlisted": ["T-run-check-02"],
                                                  "mismatch": None}, self.log))
        self.assertTrue(line["ok"])

    def test_an_edited_entry_breaks_the_chain(self):
        self.run_tool("run", str(self.file), "--ids", "T-run-check-01", "--seeds", "0", "--workers", "1", "--log",
                      str(self.log))
        lines = self.log.read_text(encoding="utf-8").splitlines()
        entry = json.loads(lines[0])
        entry["failing"], entry["passing"] = [], ["T-run-check-01", "T-run-check-02"]     # a forged verdict
        self.log.write_text(json.dumps(entry) + "\n", encoding="utf-8")
        code, text = self.run_tool("verify", "--log", str(self.log))
        self.assertEqual(code, 1)
        self.assertIn("digest does not match", text)


class ProcessToolTests(unittest.TestCase):
    def test_green_tests_exits_non_zero_on_a_failure(self):
        tmp = Path(tempfile.mkdtemp(prefix="green_"))
        self.addCleanup(shutil.rmtree, str(tmp), True)
        (tmp / "tests").mkdir()
        (tmp / "tests" / "test_red.py").write_text("import unittest\n\nclass T(unittest.TestCase):\n"
                                                    "    def test_x(self):\n        self.fail('red')\n",
                                                    encoding="utf-8")
        script = str(ROOT / "tools" / "orchestration" / "green_tests.py")
        proc = subprocess.run([sys.executable, script, "1", "--modes", "normal", "--tests-dir", str(tmp / "tests"),
                               "--json", str(tmp / "out.json")], capture_output=True, text=True, timeout=300)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertEqual(json.loads((tmp / "out.json").read_text(encoding="utf-8"))[0]["rc"], 1)
        (tmp / "tests" / "test_red.py").write_text("import unittest\n\nclass T(unittest.TestCase):\n"
                                                    "    def test_x(self):\n        pass\n", encoding="utf-8")
        proc = subprocess.run([sys.executable, script, "1", "--tests-dir", str(tmp / "tests"), "--json",
                               str(tmp / "out.json")], capture_output=True, text=True, timeout=300)
        self.assertEqual(proc.returncode, 0, proc.stdout)

    def test_every_python_file_is_python_3_9(self):
        """The project targets Python 3.9 (setup.py) and is developed on 3.14: grammar (ast feature_version) and the
        common 3.10+ runtime features that would only fail when that line runs."""
        newer = {"pairwise": "itertools.pairwise", "bit_count": "int.bit_count", "cbrt": "math.cbrt",
                 "exp2": "math.exp2", "StrEnum": "enum.StrEnum", "aclosing": "contextlib.aclosing"}
        problems = []
        files = [p for d in ("house_sim", "tools", "tests") for p in (ROOT / d).rglob("*.py")] + list(ROOT.glob("*.py"))
        for path in sorted(p for p in files if "__pycache__" not in p.parts):
            src = path.read_text(encoding="utf-8")
            try:
                tree = ast.parse(src, filename=str(path), feature_version=(3, 9))
            except SyntaxError as exc:
                problems.append("{}: not Python 3.9 syntax: {}".format(path.relative_to(ROOT), exc))
                continue
            future = any(isinstance(n, ast.ImportFrom) and n.module == "__future__" and
                         any(a.name == "annotations" for a in n.names) for n in tree.body)
            for node in ast.walk(tree):
                where = "{}:{}".format(path.relative_to(ROOT).as_posix(), getattr(node, "lineno", "?"))
                if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "zip" and \
                        any(k.arg == "strict" for k in node.keywords):
                    problems.append(where + " zip(strict=) is 3.10+")
                if isinstance(node, ast.Call) and getattr(node.func, "id", None) in ("isinstance", "issubclass") and \
                        len(node.args) == 2 and isinstance(node.args[1], ast.BinOp):
                    problems.append(where + " isinstance(x, A | B) is 3.10+")
                if isinstance(node, ast.Attribute) and node.attr in newer:
                    problems.append(where + " {} is 3.10+".format(newer[node.attr]))
                if isinstance(node, ast.Name) and node.id in ("aiter", "anext"):
                    problems.append(where + " {} is 3.10+".format(node.id))
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                    if "tomllib" in names:
                        problems.append(where + " tomllib is 3.11+")
                    if isinstance(node, ast.ImportFrom) and node.module == "datetime" and \
                            any(a.name == "UTC" for a in node.names):
                        problems.append(where + " datetime.UTC is 3.11+")
                if not future:
                    anns = []
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        anns = [a.annotation for a in node.args.args + node.args.kwonlyargs if a.annotation] + \
                            ([node.returns] if node.returns else [])
                    elif isinstance(node, ast.AnnAssign):
                        anns = [node.annotation]
                    if any(isinstance(n, ast.BinOp) and isinstance(n.op, ast.BitOr) for a in anns for n in ast.walk(a)):
                        problems.append(where + " an X | Y annotation needs from __future__ import annotations on 3.9")
        self.assertEqual(problems, [], "\n".join(problems[:40]))

    def test_the_ci_workflow_covers_both_pythons_and_both_systems(self):
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        for needle in ('"3.9"', '"3.14"', "ubuntu-latest", "windows-latest", "green_tests.py",
                       "run_check.py run --all", "core.autocrlf false", "HOUSE_AI_FORBID_NETWORK",
                       'HOUSE_SIM_PERF_TESTS: "0"', "continue-on-error: true", "--allowlist"):
            self.assertIn(needle, text)
        self.assertNotIn("secrets.", text)                  # no secrets, no paid services


if __name__ == "__main__":
    unittest.main()
