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
Second pass (K-GATE2, SecondPassGateTests): a comparison with anything but the package's hand-over base (its own
earlier record, itself, no record) is INCOMPLETE; a package keeps its own stage; fewer seeds or extras narrow a record;
a record of a folder that is neither the project nor an isolated copy of it fails TREE; compare recomputes the G2/G3
determinism check, worker crashes and the required audit variants; per-test ids, once recorded, are required; the
wall-clock bounds leave the correctness runs for G7's perf job (best of 3, alone) and must run there; the golden takes
no self-named known failure.
Phase-1 close-out (K-GATE3, CloseOutGateTests; finish_VER.json's four holes): the gate's rules are committed policy
(gate_policy/rules.json, every former value kept) and a record made by gate code that differs from the committed one
fails GATE (a gate-tool package's needs a "gate-change" signature); --shared-tree never attributes a protected file and
only to a running peer that owns the file, listing every attribution; every re-recorded room-golden entry needs a
reviewed and a signed "golden" row, and a known-failing pin counts only while the committed allowlist excuses it; the
wall-clock bounds run relaxed in the correctness runs (their bodies run), are registered (gate_policy/perf_tests.json)
and one that skips its body counts as a skip; the phase-2 packages; the Python 3.9 lint in G1 and CI.
Everything runs on temporary folders; nothing is committed and no policy file of the project is written.
"""
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
# the gate's committed rules and bound registry as the working tree has them (the close-out's policy files; the tests
# serve them as committed through a fake trusted_json)
RULES_DATA = json.loads((ROOT / gate.RULES_FILE).read_text(encoding="utf-8"))
REGISTRY_DATA = json.loads((ROOT / gate.PERF_REGISTRY).read_text(encoding="utf-8"))
# the gate code of the synthetic records and of the "committed" project (running_gate / committed_gate are patched)
FAKE_CODE = {"tools/orchestration/tier1_gate.py": "a" * 40, "tools/orchestration/green_tests.py": "b" * 40,
             "tools/orchestration/wallclock.py": "c" * 40, "tools/py39_lint.py": "d" * 40}
FAKE_COMMIT = "f" * 40
GATE_META = {"code": FAKE_CODE, "digest": gate.code_digest(FAKE_CODE), "commit": FAKE_COMMIT}


@contextlib.contextmanager
def sealed(code=None, committed=None):
    """The gate code as committed: the running gate and the commit's gate files are the fake ones (or `code` running
    against `committed`), so a test of the comparison rules does not depend on this working tree's own edits."""
    running = dict(code or FAKE_CODE)
    info = {"script": "tools/orchestration/tier1_gate.py", "files": running, "code": running,
            "digest": gate.code_digest(running)}
    with mock.patch.object(gate, "running_gate", lambda: info), \
            mock.patch.object(gate, "committed_gate", lambda commit="HEAD": dict(committed or FAKE_CODE)), \
            mock.patch.object(gate, "head_commit", lambda: FAKE_COMMIT):
        yield


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


def policy_files(known=None, signoffs=None, rules=None, registry=None):
    """A stand-in for gate.trusted_json: the committed policy files of a fake project (the rules and the bound registry
    default to the working tree's)."""
    files = {gate.RULES_FILE: RULES_DATA if rules is None else rules,
             gate.PERF_REGISTRY: REGISTRY_DATA if registry is None else registry}
    if known is not None:
        files[gate.KNOWN_FAILING] = {"format": gate.KNOWN_FAILING_FORMAT, "entries": known}
    for gid, rows in (signoffs or {}).items():
        files["{}/{}.json".format(gate.SIGNOFF_DIR, gid)] = {"format": gate.SIGNOFF_FORMAT, "gate": gid,
                                                             "signed_by": "main session", "rows": rows}

    def fake(root, rel):
        return (files[rel], "committed at HEAD (test)") if rel in files else (None, "not committed")
    return fake


class GateCase(unittest.TestCase):
    """Synthetic gate records in a temporary gates folder (C1 the previous gate, X the package's record), made by the
    committed gate code (sealed)."""
    PKG = "ADOPT:admin_inventory"

    def setUp(self):
        self.gates = Path(tempfile.mkdtemp(prefix="gate_hard_"))
        self.addCleanup(shutil.rmtree, str(self.gates), True)
        self.prev_meta = {"id": "C1", "stage": "C1", "finished": "2026-10-15"}
        self.meta = {"id": "X", "packages": [self.PKG], "stage": "ADOPT", "prev": "C1", "finished": "2026-10-20",
                     "tree": TREE, "gate": dict(GATE_META)}

    def record(self, prev_rows, cur_rows, prev_audits=None, cur_audits=None, cur_tests=None, meta=None):
        write_record(self.gates / "C1", self.prev_meta, own=prev_rows, audits=prev_audits)
        write_record(self.gates / "X", dict(self.meta, **(meta or {})), own=cur_rows, audits=cur_audits,
                     tests=cur_tests)

    def compare(self, *extra, policy=None, package=None):
        out = io.StringIO()
        with mock.patch.object(gate, "trusted_json", policy or policy_files()), \
                mock.patch.object(gate, "tree_fingerprint", lambda root: TREE), \
                mock.patch.object(gate, "changed_files", lambda root: ([], None)), sealed(), \
                contextlib.redirect_stdout(out):
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
        with contextlib.redirect_stdout(io.StringIO()), sealed():
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
              "G6": {v: {"defects": 0, "nondeterministic": 0, "rc": 0, "commands": 10, "succeeded": 4}
                     for v in ("room", "geometric", "battery")}}

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
        # K-GATE3: a protected file (the gate script, its policy) is never attributed to a peer -- phase 1 attributed
        # both to K-GATE / K-EVAL and a domain package that edited them passed G10 with --shared-tree
        self.assertEqual(domain["attributed"], {})
        self.assertEqual((domain["status"], sorted(domain["outside"])), ("FAIL", sorted(files)))
        self.assertEqual(sorted(domain["protected"]), sorted(files))

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
                                                "tree": TREE, "gate": dict(GATE_META)}, own=rows)
        changed = (["house_sim/systems/cleaning/floors.py", "house_sim/systems/food/model.py"], None)

        def run(rules):
            out = io.StringIO()
            with mock.patch.object(gate, "changed_files", lambda root: changed), \
                    mock.patch.object(gate, "tree_fingerprint", lambda root: TREE), sealed(), \
                    mock.patch.object(gate, "trusted_json", policy_files(rules=rules)), contextlib.redirect_stdout(out):
                code = gate.main(["--root", str(ROOT), "--gates-root", str(gates), "compare", "--package",
                                  "ADOPT:cleaning", "--shared-tree"])
            return code, out.getvalue()
        running = copy.deepcopy(RULES_DATA)
        running["running_substages"] = ["ADOPT"]            # the adoption round, while it ran
        code, text = run(running)
        self.assertEqual(code, 0, text)
        self.assertIn("compare C1 -> ADOPT_cleaning", text)
        self.assertIn("G10  PASS", text)
        self.assertIn("house_sim/systems/food/model.py -> ADOPT:food", text)       # listed for review (K-GATE3)
        self.assertIn("compare result: PASS", text)
        # once the round is over its sub-stage is no longer running: the attribution is no hand-over (K-GATE3)
        code, text = run(None)
        self.assertEqual(code, 0, text)
        self.assertIn("G10  PASS", text)
        self.assertIn("compare result: INCOMPLETE", text)
        self.assertIn("sub-stage(s) ADOPT, P1 that are not running", text)


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
        cur = tests_json([row("test_x.py", skipped=1, ids=self.IDS, outcomes={"skip": [self.IDS[1]]})])
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
                mock.patch.object(gate, "tree_fingerprint", lambda root: TREE), sealed():
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


BOUNDS_FILE = (
    "import os\nimport time\nimport unittest\nfrom pathlib import Path\n\n"
    "COUNTER = Path(__file__).with_name('attempts.txt')\n\n\n"
    "class T(unittest.TestCase):\n"
    "    def test_flaky_bound(self):\n"
    "        if os.environ.get('HOUSE_SIM_PERF_TESTS', '1') == '0':\n"
    "            self.skipTest('wall-clock bound off (HOUSE_SIM_PERF_TESTS=0)')\n"
    "        n = int(COUNTER.read_text()) if COUNTER.exists() else 0\n"
    "        COUNTER.write_text(str(n + 1))\n"
    "        start = time.perf_counter()\n"
    "        time.sleep(0.3 if n == 0 else 0.0)        # over budget the first time only: a busy moment\n"
    "        self.assertEqual(sum(range(10)), 45)     # its functional assertion\n"
    "        self.assertLess(time.perf_counter() - start, 0.2, 'over budget')\n\n"
    "    def test_slow_bound(self):\n"
    "        if os.environ.get('HOUSE_SIM_PERF_TESTS', '1') == '0':\n"
    "            self.skipTest('wall-clock bound off (HOUSE_SIM_PERF_TESTS=0)')\n"
    "        start = time.perf_counter()\n"
    "        time.sleep(0.25)\n"
    "        self.assertLess(time.perf_counter() - start, 0.2, 'always over budget')\n\n"
    "    def test_plain(self):\n"
    "        pass\n")
# a failing correctness test disguised as a wall-clock bound (finish_VER.json, perf_disguise.py), and one that keeps
# skipping its body even when relaxed
DISGUISED_FILE = (
    "import os\nimport unittest\n\n\n"
    "class DisguisedTests(unittest.TestCase):\n"
    "    def test_a_correctness_check_that_fails(self):\n"
    "        if os.environ.get('HOUSE_SIM_PERF_TESTS', '1') == '0':\n"
    "            self.skipTest('wall-clock bound off (HOUSE_SIM_PERF_TESTS=0)')\n"
    "        self.assertEqual(1 + 1, 3)\n\n"
    "    def test_one_that_never_runs_its_body(self):\n"
    "        if os.environ.get('HOUSE_SIM_PERF_TESTS', '1') != '1':\n"
    "            self.skipTest('wall-clock bound off (HOUSE_SIM_PERF_TESTS is not 1)')\n"
    "        self.assertEqual(1 + 1, 3)\n")


class SecondPassGateTests(GateCase):
    """The phase-1 red team's gate findings, holes FIX2's fixes left open (K-GATE2): each test reproduces the attack
    and fails on the FIX2 gate."""
    IDS = RedTeamGateTests.IDS

    def compare_with(self, prev, cur="X", *extra, policy=None):
        out = io.StringIO()
        with mock.patch.object(gate, "trusted_json", policy or policy_files()), \
                mock.patch.object(gate, "tree_fingerprint", lambda root: TREE), \
                mock.patch.object(gate, "changed_files", lambda root: ([], None)), sealed(), \
                contextlib.redirect_stdout(out):
            code = gate.main(["--root", str(ROOT), "--gates-root", str(self.gates), "compare", prev, cur, "--package",
                              self.PKG] + list(extra))
        return code, out.getvalue()

    # ---- red_defects 2 / 3: the delta rules against a record nobody accepted
    def test_a_record_compared_with_its_own_earlier_record_is_no_hand_over(self):
        """Laundering: the package records MINE1 (a test skipped; its compare against the base C1 fails), then X with
        --prev MINE1: every delta rule passed and the standalone compare (PREV from X's meta) said PASS."""
        rows = [trial("A-1", 0)]
        skipped = tests_json([row("test_x.py", skipped=1, ids=self.IDS, outcomes={"skip": [self.IDS[1]]})])
        write_record(self.gates / "C1", self.prev_meta, own=rows, tests=tests_json([row("test_x.py", ids=self.IDS)]))
        write_record(self.gates / "MINE1", dict(self.meta, id="MINE1"), own=rows, tests=skipped)
        write_record(self.gates / "X", dict(self.meta, prev="MINE1"), own=rows, tests=skipped)
        code, text = self.compare_with("C1")
        self.assertEqual(code, 1, text)                                     # against its base: the skipped test
        for prev in ("MINE1", "X"):                                         # its own earlier record; itself
            code, text = self.compare_with(prev)
            self.assertIn("compare result: INCOMPLETE", text)
            self.assertIn("is not the package's hand-over base (C1)", text)
        self.assertEqual([p.name for p in gate.handover_bases(gate.policy_for([self.PKG]), self.gates, "X")], ["C1"])
        # phase 1: a domain owner's ADOPT:<domain> record hands over against ADOPT (the P1 sub-stage's base) too
        write_record(self.gates / "ADOPT", {"id": "ADOPT", "stage": "ADOPT", "finished": "2026-10-21"}, own=rows)
        self.assertEqual(sorted(p.name for p in gate.handover_bases(gate.policy_for([self.PKG]), self.gates, "X")),
                         ["ADOPT", "C1"])

    def test_a_record_compared_with_no_previous_record_is_no_hand_over(self):
        """`record --prev none` dropped every delta rule (test removals, verdict changes, coverage losses) and the
        record still said PASS and "handover": true."""
        write_record(self.gates / "C1", self.prev_meta, own=[trial("A-1", 0)])
        args = SimpleNamespace(root=str(ROOT), gates_root=str(self.gates), id="NOPREV", out=str(self.gates / "NOPREV"),
                               work=str(self.gates / "work"), package=self.PKG, stage=None, only=None, skip=None,
                               seeds=None, jobs=1, extras=None, bench_repeats=1, prev="none", spec_files=None,
                               reviewed=None, camera=None, shared_tree=False)
        with mock.patch.object(gate, "trusted_json", policy_files()), \
                mock.patch.object(gate, "tree_fingerprint", lambda root: TREE), \
                mock.patch.object(gate, "changed_files", lambda root: ([], None)), sealed():
            g = gate.Gate(args)
            self.assertIsNone(g.prev_dir())
            g.fingerprints = {}
            g.results = {c: {"status": "PASS", "note": "ok"} for c in gate.CHECKS}
            g.spec_rows = {"own": [], "realistic": []}
            g.spec_summary = {m: {"specs": 0, "passed": 0, "trials": 0, "failing": {}} for m in ("own", "realistic")}
            (self.gates / "NOPREV").mkdir()
            with contextlib.redirect_stdout(io.StringIO()):
                g.write(None, gate.now_iso(), {})
        meta = json.loads((self.gates / "NOPREV" / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["verdict"], "INCOMPLETE", meta["summary"])
        self.assertFalse(meta["handover"])
        self.assertIn("no previous record although its hand-over base C1 exists", meta["incomplete"])

    def test_a_partial_run_against_another_previous_record_is_stale(self):
        """A full run against the package's own earlier record (every delta passes), then `record --only G10 --prev
        C1` on the same tree: the merged record named C1 as its previous record, though only G10 ran against it."""
        write_record(self.gates / "C1", self.prev_meta, own=[trial("A-1", 0)])
        write_record(self.gates / "MINE1", dict(self.meta, id="MINE1"), own=[trial("A-1", 0)])
        args = SimpleNamespace(root=str(ROOT), gates_root=str(self.gates), id="MERGED", out=str(self.gates / "MERGED"),
                               work=str(self.gates / "work"), package=self.PKG, stage=None, only="G10", skip=None,
                               seeds=None, jobs=1, extras=None, bench_repeats=1, prev="C1", spec_files=None,
                               reviewed=None, camera=None, shared_tree=False)
        mine = str((self.gates / "MINE1").resolve())
        old_meta = {"recorded": list(gate.CHECKS), "checks": {c: {"status": "PASS", "note": "vs MINE1", "tree": TREE,
                                                                    "prev": mine} for c in gate.CHECKS}}
        with mock.patch.object(gate, "trusted_json", policy_files()), \
                mock.patch.object(gate, "tree_fingerprint", lambda root: TREE), \
                mock.patch.object(gate, "changed_files", lambda root: ([], None)), sealed():
            g = gate.Gate(args)
            prev = g.prev_dir()
            g.fingerprints = {}
            g.results = {"G10": {"status": "PASS", "note": "0 changed files"}}
            (self.gates / "MERGED").mkdir()
            with contextlib.redirect_stdout(io.StringIO()):
                g.write(prev, gate.now_iso(), old_meta)
        meta = json.loads((self.gates / "MERGED" / "meta.json").read_text(encoding="utf-8"))
        self.assertIn("G2", meta["stale"])
        self.assertNotIn("G10", meta["stale"])
        self.assertFalse(meta["handover"])
        self.assertEqual(meta["checks"]["G10"]["prev"], str(prev))
        with mock.patch.object(gate, "tree_fingerprint", lambda root: TREE):
            section, _ = gate.tree_check(meta, ROOT)
        self.assertEqual(section["status"], "FAIL")
        self.assertIn("judged against another previous record", " ".join(section["lines"]))

    def test_a_package_cannot_choose_another_stage(self):
        """`record --package P1 --stage A0`: no previous gate (A0 has none) and none of the later stages' feature
        requirements (golden, kernel_core, camera tools) -- a lenient gate chosen by its runner."""
        args = SimpleNamespace(root=str(ROOT), gates_root=str(self.gates), id="P1X", out=None, work=None, package="P1",
                               stage="A0", only=None, skip=None, seeds=None, jobs=1, extras=None, bench_repeats=1,
                               prev=None, spec_files=None, reviewed=None, camera=None, shared_tree=False)
        with self.assertRaises(SystemExit) as caught:
            gate.Gate(args)
        self.assertIn("contradicts the stage", str(caught.exception))
        self.assertEqual(gate.Gate(dict_args(args, stage="P1")).policy["stage"], "P1")      # its own stage is fine
        self.assertEqual(gate.Gate(dict_args(args, package=None, id="B0", stage="B0")).policy["stage"], "B0")

    def test_fewer_seeds_or_extras_narrow_a_record(self):
        """`--seeds 0` ran a fifth of the trials (G2-ABS passed on them) and `--extras ''` left G9 N/A."""
        self.assertIn("seeds limited to 0", gate.record_incomplete({"seeds": [0]}))
        self.assertIsNone(gate.record_incomplete({"seeds": [0, 1, 2, 41, 97, 5]}))
        self.assertIn("extras without physics", gate.record_incomplete({"packages": ["P1-ALL"], "extras": []}))
        self.assertIsNone(gate.record_incomplete({"packages": ["P1-ALL"], "extras": gate.PACKAGES["P1-ALL"]["extras"]}))
        rows = [trial("A-1", 0)]
        self.record(rows, rows, meta={"seeds": [0]})
        code, text = self.compare()
        self.assertEqual(code, 0, text)
        self.assertIn("compare result: INCOMPLETE", text)
        self.assertIn("seeds limited to 0", text)

    # ---- red_defects 4: the tree a record describes
    def test_a_record_of_another_folder_fails_tree(self):
        """`record --root <any folder with its own git history>` passed TREE (the fingerprint of that folder) and G10
        (its own HEAD): the record described nothing that is handed over."""
        other = Path(tempfile.mkdtemp(prefix="gate_other_tree_"))
        self.addCleanup(shutil.rmtree, str(other), True)
        rows = [trial("A-1", 0)]
        self.record(rows, rows, meta={"tree_root": str(other)})
        code, text = self.compare()
        self.assertEqual(code, 1, text)
        self.assertIn("neither this project", text)
        if shutil.which("git"):
            head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True)
            if head.returncode == 0:
                gate.write_json(other / gate.ISOLATE_MANIFEST, {"format": gate.ISOLATE_FORMAT,
                                                                "base": head.stdout.strip()})
                self.assertEqual(self.compare()[0], 0)                      # an isolated copy of a project commit
                gate.write_json(other / gate.ISOLATE_MANIFEST, {"format": gate.ISOLATE_FORMAT, "base": "0" * 40})
                code, text = self.compare()
                self.assertEqual(code, 1, text)
                self.assertIn("is not a commit of the project", text)

    # ---- red_defects 3: what failed the record's own G3 / G6 is recomputed
    def test_compare_recomputes_g3_determinism_and_worker_crashes(self):
        own = [dict(trial("R-1", 0), perception="realistic")]
        differs = [dict(trial("R-1", 0), perception="realistic", reference_state_hash="s-other")]
        write_record(self.gates / "C1", self.prev_meta, own=own, realistic=differs)
        write_record(self.gates / "X", self.meta, own=own, realistic=differs)
        code, text = self.compare_with("C1")
        self.assertEqual(code, 1, text)
        self.assertIn("NONDETERMINISTIC: 1 realistic-spec trial(s) differ between the G2 and G3 runs", text)
        crashed = [dict(trial("A-1", 0), passed=False, reference_passes=False, error="worker failed: rc -9 TIMEOUT")]
        write_record(self.gates / "C1", self.prev_meta, own=[trial("A-1", 0)], realistic=crashed)
        write_record(self.gates / "X", self.meta, own=[trial("A-1", 0)], realistic=crashed)
        code, text = self.compare_with("C1")
        self.assertEqual(code, 1, text)
        self.assertIn("1 trial(s) lost to a spec-worker crash", text)

    def test_compare_requires_the_packages_variants_even_when_the_previous_record_lacks_them(self):
        rows = [trial("A-1", 0)]
        audits = {"G4": {"room": {"leaks": 0, "errors": 0, "rc": 0}, "geometric": {"leaks": 0, "errors": 0, "rc": 0}},
                  "G6": {"room": {"defects": 0, "nondeterministic": 0, "rc": 0}}}
        self.record(rows, rows, prev_audits=audits, cur_audits=audits)
        code, text = self.compare()
        self.assertEqual(code, 1, text)
        self.assertIn("required variant(s) missing: geometric, battery, coverage", text)
        self.assertEqual(gate.required_variants(gate.policy_for([self.PKG]), ROOT),
                         {"G4": ["room", "geometric"], "G6": ["room", "geometric", "battery", "coverage"]})

    # ---- red_defects 1: per-test ids, once recorded, are required
    def test_a_file_reporting_counts_only_after_per_test_ids_is_a_regression(self):
        """A deleted test and an added one keep the count: once the previous record has per-test ids, a record
        whose file reports counts only cannot show that."""
        prev = tests_json([row("test_x.py", ids=self.IDS)])
        counts = tests_json([row("test_x.py", ran=3)])
        bad, _ = gate.test_regressions(prev, counts)
        self.assertEqual([b[4][:28] for b in bad], ["per-test outcomes missing (t"])
        self.assertEqual(gate.test_regressions(prev, tests_json([row("test_x.py", ran=3, rc=1)]))[0], [])   # G1 fails

    def test_counts_that_disagree_with_the_outcomes_and_missing_g5_data_fail(self):
        """The red team's S1 / S3 / S8 edits on a record with per-test ids: two more skips or 20 fewer tests in the
        counts only (the ids, compared instead, unchanged) and 55 of 60 generation-1 digests and the layout counts
        dropped -- compare passed all three on FIX2's gate."""
        prev = tests_json([row("test_x.py", ids=self.IDS)])
        for edited in (row("test_x.py", skipped=2, ids=self.IDS), row("test_x.py", ran=1, ids=self.IDS)):
            bad, _ = gate.test_regressions(prev, tests_json([edited]))
            self.assertEqual(len(bad), 1, bad)
            self.assertIn("disagree with its per-test outcomes", bad[0][4])
        g5 = {"layout": {"problems": 0, "by_kind_owner": {}}, "gen1": {"entries": 3, "digests": {"a": "1", "b": "2",
                                                                                                   "c": "3"}}}
        dropped = {"layout": {}, "gen1": {"entries": 3, "digests": {"a": "1"}}}
        rows = [trial("A-1", 0)]
        self.record(rows, rows, prev_audits={"G5": g5}, cur_audits={"G5": dropped})
        code, text = self.compare()
        self.assertEqual(code, 1, text)
        self.assertIn("layout counts missing", text)
        self.assertIn("generation-1 digests missing: 2 of the previous gate's, 1 of 3 pinned entries present", text)

    # ---- red_defects 7: wall-clock bounds out of the correctness suite, into G7's perf job
    def test_a_bound_moved_to_the_perf_job_must_run_there(self):
        bound = self.IDS[2]
        prev = tests_json([row("test_x.py", ids=self.IDS)])
        moved = tests_json([row("test_x.py", skipped=1, ids=self.IDS, outcomes={"perf": [bound]})])
        bad, _ = gate.test_regressions(prev, moved)
        self.assertEqual([(b[3], b[4][:22]) for b in bad], [(bound, "a wall-clock bound ski")])
        moved["perf"] = [{"file": "test_x.py", "mode": "normal", "ids": [bound], "failed": [], "attempts": {bound: 1}}]
        self.assertEqual(gate.test_regressions(prev, moved), ([], []))
        # against a record without per-test ids (counts only): a bound the perf job ran is no extra skip
        old = tests_json([row("test_x.py", ran=3, skipped=0)])
        self.assertEqual(gate.test_regressions(old, moved), ([], []))
        self.assertEqual(len(gate.test_regressions(old, dict(moved, perf=[]))[0]), 1)
        # a bound failing every attempt fails compare (PERF), whatever the previous record ran
        rows = [trial("A-1", 0)]
        moved["perf"][0]["failed"] = [bound]
        self.record(rows, rows, cur_tests=moved)
        code, text = self.compare()
        self.assertEqual(code, 1, text)
        self.assertIn("PERF FAIL", text)
        self.assertIn("1 failing every attempt", text)

    def test_the_worker_relaxes_bounds_and_the_perf_job_retries_them(self):
        """K-GATE2 moved the bounds to G7's perf job (best of 3, alone); K-GATE3 runs their bodies in the correctness
        runs too, with the timing relaxed (a counting clock), so a slow moment there never fails them."""
        tmp = Path(tempfile.mkdtemp(prefix="gate_perf_"))
        self.addCleanup(shutil.rmtree, str(tmp), True)
        (tmp / "tests").mkdir()
        (tmp / "tests" / "test_bounds.py").write_text(BOUNDS_FILE, encoding="utf-8")
        flaky, slow, plain = ("tests.test_bounds.T.test_flaky_bound", "tests.test_bounds.T.test_slow_bound",
                              "tests.test_bounds.T.test_plain")
        out = tmp / "out.json"
        env = dict(os.environ, HOUSE_SIM_PERF_TESTS="0")
        proc = subprocess.run([sys.executable, str(ROOT / "tools" / "orchestration" / "tier1_gate.py"), "_unittest",
                               str(out), "tests/test_bounds.py"], cwd=str(tmp), env=env, capture_output=True,
                              text=True, timeout=300)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["tests"],
                         {flaky: "ok", slow: "ok", plain: "ok"})      # their bodies ran (K-GATE2's worker: "perf")
        self.assertEqual((tmp / "tests" / "attempts.txt").read_text(), "1")
        (tmp / "tests" / "attempts.txt").unlink()
        green = gate._green_tests()
        self.assertEqual(green.perf_tests([str(tmp / "tests" / "test_bounds.py")]), {"test_bounds.py": [flaky, slow]})
        rows = green.run_perf({"test_bounds.py": [flaky, slow]}, ("normal",), 3, str(tmp), str(tmp / "work"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["failed"], [slow])
        self.assertEqual(rows[0]["attempts"], {flaky: 2, slow: 3})
        self.assertIn("always over budget", rows[0]["tail"])
        self.assertEqual(gate.perf_failures({"perf": rows}), [("test_bounds.py", "normal", slow)])
        # the correctness runs: green_tests.py relaxes the bounds unless the caller set the switch
        quiet = dict(os.environ)
        quiet.pop("HOUSE_SIM_PERF_TESTS", None)
        proc = subprocess.run([sys.executable, str(ROOT / "tools" / "orchestration" / "green_tests.py"), "1",
                               "--modes", "normal", "--tests-dir", str(tmp / "tests"), "--json", str(tmp / "g.json")],
                              env=quiet, capture_output=True, text=True, timeout=300)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertEqual(json.loads((tmp / "g.json").read_text(encoding="utf-8"))[0]["skipped"], 0)
        self.assertIn("assert them alone with --perf", proc.stdout)
        proc = subprocess.run([sys.executable, str(ROOT / "tools" / "orchestration" / "green_tests.py"), "--perf",
                               "--modes", "normal", "--tests-dir", str(tmp / "tests")], env=quiet,
                              capture_output=True, text=True, timeout=300)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("FAIL " + slow, proc.stdout)

    def test_the_gates_correctness_runs_relax_the_bounds(self):
        args = SimpleNamespace(root=str(ROOT), gates_root=str(self.gates), id="ENVCASE", out=None,
                               work=str(self.gates / "work"), package=None, stage="B0", only="G1,G1b,G8", skip=None,
                               seeds=None, jobs=1, extras="", bench_repeats=1, prev="none", spec_files=None,
                               reviewed=None, camera=None, shared_tree=False, test_timeout=60)
        with mock.patch.object(gate, "trusted_json", policy_files()):
            g = gate.Gate(args)
            g.prev_dir_cached = None
            jobs = g.test_jobs()
        units = [j for j in jobs if not j.meta.get("lint")]
        self.assertTrue(units)
        self.assertEqual({j.env.get(gate.PERF_ENV) for j in units}, {"0"})
        plan = json.loads(Path(units[0].env[gate._wallclock().PLAN_ENV]).read_text(encoding="utf-8"))
        self.assertEqual(plan["tests.test_mind.BudgetTests.test_update_budget_on_a_generated_house"], "count")
        self.assertEqual(plan["tests.test_physics_thermal.ThermalStepTests."
                              "test_faster_than_the_kernel_thermal_system_on_a_settled_house"], "helper")
        self.assertEqual({j.check for j in jobs}, {"G1", "G1b", "G8"})
        self.assertEqual([j.name for j in jobs if j.meta.get("lint")], ["G1 py39_lint"])      # K-GATE3: in G1
        bounds = g.perf_bounds()
        self.assertIn("tests.test_mind.BudgetTests.test_update_budget_on_a_generated_house", bounds["test_mind.py"])


def dict_args(args, **changes):
    """A copy of an argparse-like namespace with some fields changed."""
    return SimpleNamespace(**dict(vars(args), **changes))


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

    def test_the_head_cameras_bookkeeping_is_no_effect(self):
        """K-GATE2: in geometric mode every step logs a camera frame (robot.view.frames / step) and a step on a target
        re-aims the head, so FIX2's rule still counted open_valve on an open valve ("It is already fully open") as
        driven to success in the geometric specs."""
        sys.path.insert(0, str(ROOT))
        from tools import fuzz
        from house_sim.kernel.plugins import load_domains
        load_domains()
        env = fuzz._env(3, {}, camera="geometric")
        if env.world.s["robot"].get("posture") != "standing":
            self.skipTest("the robot does not start standing in this house")
        result = env.step("stand")
        self.assertTrue(result["ok"])
        self.assertTrue(env.last_step_effect and all(p.startswith("robot.view.") for p in env.last_step_effect),
                        env.last_step_effect)
        self.assertTrue(fuzz._noop(env, "stand", True))
        self.assertEqual(fuzz.world_effect(["robot.view.frames", "robot.view.aim", "robot.view.pan_deg"]), [])
        self.assertEqual(fuzz.world_effect(["robot.view.frames", "entities.valve_1.open_frac", "robot.facing_deg"]),
                         ["entities.valve_1.open_frac", "robot.facing_deg"])

    def test_the_golden_refuses_to_pin_a_failing_reference(self):
        sys.path.insert(0, str(ROOT / "tools"))
        import golden
        specs = {"A-1": {"passes": False, "state": "s1"}, "B-1": {"passes": True, "state": "s2"}}
        self.assertEqual(golden.failing_references(specs), (["A-1"], []))
        self.assertEqual(golden.failing_references(specs, committed={"A-1"}), ([], ["A-1"]))     # committed pin
        self.assertEqual(golden.failing_references(specs, allowlisted=lambda keys: {"A-1"}), ([], ["A-1"]))
        pinned = {"runs": {}, "specs": {"A-1": {"passes": False, "state": "s1", "known_failing": True},
                                        "B-1": {"passes": True, "state": "s2"}}}
        fixed = {"A-1": {"passes": True, "state": "s9"}, "B-1": {"passes": True, "state": "s2"}}
        # the fix the entry waited for -- while the committed allowlist vouches for the pin (K-GATE3)
        self.assertEqual(golden.compare(pinned, {}, fixed, vouched={"A-1"}), [])
        self.assertEqual(len(golden.expected_changes(pinned, fixed, vouched={"A-1"})), 1)
        broken = {"A-1": {"passes": False, "state": "s1"}, "B-1": {"passes": False, "state": "s3"}}
        self.assertEqual(len(golden.compare(pinned, {}, broken, vouched={"A-1"})), 1)    # B-1 regressed
        self.assertEqual(len(golden.compare(pinned, {}, fixed, vouched=set())), 2)      # the file's flag alone
        with self.assertRaises(SystemExit):                              # record() refuses before writing anything
            with mock.patch.object(golden, "compute", lambda g, keys, workers: ({}, specs)), \
                    mock.patch.object(golden, "allowlisted_failing", lambda keys: set()), \
                    mock.patch.object(golden, "committed_known_failing", lambda path: set()), \
                    mock.patch.object(golden, "save", lambda *a, **k: self.fail("saved a failing reference")), \
                    mock.patch.object(golden, "load", lambda path: copy.deepcopy(pinned)), \
                    mock.patch.object(golden, "_quiet_imports", lambda: None):
                golden.record("TEST", ["x"], path=ROOT / "tests" / "data" / "room_golden.json")

    def test_a_recorder_cannot_excuse_a_failing_reference_itself(self):
        """K-GATE2 (red_defects 12 residual): FIX2's `record --known-failing ID` pinned any failing reference its
        recorder named. Only the main session's acceptance counts: the committed allowlist (K-GATE3: a known-failing
        pin of the committed golden no longer does -- it is the file's own flag); a named id outside it is refused
        before anything runs."""
        sys.path.insert(0, str(ROOT / "tools"))
        import golden
        specs = {"A-1": {"passes": False, "state": "s1"}, "B-1": {"passes": True, "state": "s2"}}
        saved = []
        base = {"format": golden.FORMAT, "runs": {}, "specs": {}, "history": []}

        def record(committed, allowlisted, known=()):
            with mock.patch.object(golden, "compute", lambda g, keys, workers: ({}, copy.deepcopy(specs))), \
                    mock.patch.object(golden, "allowlisted_failing", lambda keys: set(keys) & allowlisted), \
                    mock.patch.object(golden, "committed_known_failing", lambda path: set(committed)), \
                    mock.patch.object(golden, "save", lambda g, path: saved.append(g)), \
                    mock.patch.object(golden, "load", lambda path: copy.deepcopy(base)), \
                    mock.patch.object(golden, "_quiet_imports", lambda: None):
                return golden.record("TEST", ["x"], path=ROOT / "tests" / "data" / "room_golden.json",
                                     known_failing=known)
        with self.assertRaises(SystemExit) as caught:
            record(set(), set(), known=["A-1"])                 # self-named: refused
        self.assertIn("cannot excuse a failure itself", str(caught.exception))
        self.assertEqual(saved, [])
        with self.assertRaises(SystemExit) as caught:
            record({"A-1"}, set())                              # the committed golden pins it: no longer enough
        self.assertIn("refusing to pin failing reference", str(caught.exception))
        with self.assertRaises(SystemExit) as caught:
            record({"A-1"}, set(), known=["A-1"])
        self.assertIn("excuses nothing by itself", str(caught.exception))
        self.assertEqual(saved, [])
        pinned = record(set(), {"A-1"})                          # allowlisted: kept
        self.assertTrue(pinned["specs"]["A-1"]["known_failing"])
        pinned = record(set(), {"A-1"}, known=["A-1"])          # allowlisted: accepted, also when named
        self.assertEqual(pinned["history"][-1]["known_failing"], ["A-1"])
        outside = Path(tempfile.mkdtemp(prefix="golden_out_"))
        self.addCleanup(shutil.rmtree, str(outside), True)
        self.assertEqual(golden.committed_known_failing(outside / "room_golden.json"), set())   # not the project's
        self.assertIsInstance(golden.committed_known_failing(), set)


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
        """The project targets Python 3.9 (setup.py) and is developed on 3.14: tools/py39_lint.py (K-GATE3; it replaced
        the checks this test made itself) finds 3.10+ grammar, run-time unions and newer standard-library calls; CI
        and the gate's G1 run it (tests/test_py39_lint.py covers the lint's rules)."""
        spec = importlib.util.spec_from_file_location("py39_lint_for_gate_test", str(ROOT / "tools" / "py39_lint.py"))
        lint = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lint)
        count, problems = lint.lint(ROOT)
        self.assertGreater(count, 300)
        self.assertEqual([str(p) for p in problems], [], "\n".join(str(p) for p in problems[:40]))
        self.assertTrue(lint.lint_source("x = isinstance(1, int | str)\n"))       # the lint bites

    def test_the_ci_workflow_covers_both_pythons_and_both_systems(self):
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        for needle in ('"3.9"', '"3.14"', "ubuntu-latest", "windows-latest", "green_tests.py",
                       "run_check.py run --all", "core.autocrlf false", "HOUSE_AI_FORBID_NETWORK",
                       'HOUSE_SIM_PERF_TESTS: "0"', "continue-on-error: true", "--allowlist",
                       "python tools/py39_lint.py"):
            self.assertIn(needle, text)
        self.assertNotIn("secrets.", text)                  # no secrets, no paid services
        # K-GATE2: the wall-clock bounds are a separate, informational perf job (best of 3, alone), not a step of the
        # correctness job filtered by test-name fragments
        tests_job, _, perf_job = text.partition("\n  perf:\n")
        self.assertTrue(perf_job, "no perf job")
        self.assertIn("green_tests.py --perf --attempts 3", perf_job)
        self.assertIn("continue-on-error: true", perf_job)
        self.assertNotIn("--perf", tests_job)
        self.assertNotIn("-k budget", text)
        self.assertNotIn("\t", text)


# the digests of the rules the committed gate script held before the close-out (HEAD e5e8727; sha256 of their
# canonical JSON): the committed rules must keep every one of them exactly -- no rule gets weaker
FORMER_RULES = {
    "packages": "edd3a123e16cc3725e338006a48b6aec599bb2dcc717adf11c3b6b86c2409cf1",
    "substages": "1c25821f1264d347b2d45b22c6ee3b67e66cc83148239648df8c445568400cf6",
    "default_prev": "82e020c11527a1fd1eacf65ad92dfa3960eff5cfab1ee5e47990c623be8599da",
    "substage_base": "f81d2bdf738bccb44c7dcd65a75f2b26479ae8659fe239c390f1db0f7dda8ca7",
    "stage_order": "2d24f1e8d1eb660593eb8f556460533ef385c640964bb598f5685e398af7b8fb",
    "domains": "e83901e4f5887bb7ad600cc0f6a0d6f1ec25975c44336d1a51222514623ea669",
    "seeds": "f8058eee3f5a5c59491129d53737615877bb1a31095cf5f4f90178445e37108b",
    "kernel_tests": "fbceed967f5c751bee6339030911617e128b36342685e69b09b35cc8e6dabc76",
    "features": "6cd71367103735c63411e553c91543b7d702dc07626388c231838c624363a674",
    "extra_tools": "9b8e89cf9fe126466ccc72f68e8552d7ac1000d544839cf4a498e8ce361ff020",
    "protected": "9d960a0d6d2e0bb24847c0d322426042d066310c48ccf8b88c3ddefff0a4f769",
    "perf_attempts": "4e07408562bedb8b60ce05c1decfe3ad16b72230967de01f640b7e4729b49fce",
}
NEW_STAGES = ("P1C", "P2-S1", "P2-S2", "P2-S3", "P2-S4", "P2-S5", "P2-S6", "P2")


def canonical(value):
    import hashlib
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


class CloseOutGateTests(GateCase):
    """The phase-1 close-out (K-GATE3): the four holes the independent verifier found (catalog/analysis/phase1/
    finish_VER.json; its scripts budget_attack.py, scope_attacks.py, golden_attacks.py, perf_disguise.py). Each test
    replays its attack and fails on the K-GATE2 gate."""

    # ---- 1. the gate's rules lived in the unprotected gate script
    def test_the_rules_are_committed_policy_with_every_former_value(self):
        old_ids = [p for p in RULES_DATA["packages"] if not (p.startswith(("P1C", "P2-")) or p == "K-GATE3")]
        fields = ("stage", "files", "allow", "codes", "extras", "inert", "bench", "requires", "aka", "note", "policy")
        now = {
            "packages": {p: {k: RULES_DATA["packages"][p][k] for k in fields} for p in old_ids},
            "substages": {k: v for k, v in RULES_DATA["substages"].items() if k not in NEW_STAGES},
            "default_prev": {k: v for k, v in RULES_DATA["default_prev"].items() if k not in NEW_STAGES},
            "substage_base": {k: v for k, v in RULES_DATA["substage_base"].items() if k not in NEW_STAGES},
            "stage_order": [s for s in RULES_DATA["stage_order"] if s not in NEW_STAGES],
            "domains": RULES_DATA["domains"], "seeds": RULES_DATA["seeds"], "kernel_tests": RULES_DATA["kernel_tests"],
            "features": {k: [v["stage"], v["introducer"], v["what"]] for k, v in RULES_DATA["features"].items()},
            "extra_tools": {k: [v["tool"], v["introducer"]] for k, v in RULES_DATA["extra_tools"].items()},
            "protected": RULES_DATA["scope"]["policy_files"], "perf_attempts": RULES_DATA["perf"]["attempts"]}
        self.assertEqual({k: canonical(v) for k, v in now.items()}, FORMER_RULES)
        self.assertEqual(RULES_DATA["bench"]["default"], [1.05, "prev"])
        self.assertEqual((RULES_DATA["bench"]["tolerance"], RULES_DATA["gen2_timing"]["max_ratio"]), (1e-9, 1.2))
        self.assertEqual(RULES_DATA["bench"]["budgeted"], ["substep_ms", "wait_1h_s"])
        # the script holds no rule any more: the two lines budget_attack.py edited are gone
        source = (ROOT / "tools" / "orchestration" / "tier1_gate.py").read_text(encoding="utf-8")
        for needle in ('"bench": [1.05, "prev"]', 'bench=(1.05, "prev")', "PACKAGES = {", "SUBSTAGES = {",
                       "PERF_ATTEMPTS = 3", "DEFAULT_PREV = {"):
            self.assertNotIn(needle, source)
        with mock.patch.object(gate, "trusted_json", policy_files()):
            self.assertEqual(gate.PACKAGES["PHYS-INT"]["bench"], [1.0, "A0"])          # read from the rules
            self.assertEqual(gate.policy_for(["P2-WP2"])["bench"], [1.15, "prev"])   # a package's own budget
            self.assertEqual(gate.policy_for(["ADOPT:cleaning", "PHYS-INT"])["bench"], [1.0, "A0"])

    def test_the_budget_comes_from_the_committed_rules_not_the_working_tree(self):
        """budget_attack.py: two edited lines raised G7's budget to 1.5 and a 20 % slower record compared PASS. The
        budget is committed policy now: a working-tree edit of the rules counts for nothing."""
        rows = [trial("A-1", 0)]
        self.record(rows, rows)
        gate.write_json(self.gates / "C1" / "bench.json", {"G7": {"substep_ms": 2.69, "wait_1h_s": 1.0201}})
        gate.write_json(self.gates / "X" / "bench.json", {"G7": {"substep_ms": 3.228, "wait_1h_s": 1.224}})
        code, text = self.compare()
        self.assertEqual(code, 1, text)
        self.assertIn("limit 2.825 = prev x 1.05)  OVER BUDGET", text)
        lenient = copy.deepcopy(RULES_DATA)
        lenient["packages"][self.PKG]["bench"] = [1.5, "prev"]
        self.assertEqual(self.compare(policy=policy_files(rules=lenient))[0], 0)    # the rules decide, if committed
        if not shutil.which("git"):
            return
        repo = Path(tempfile.mkdtemp(prefix="gate_rules_"))
        self.addCleanup(shutil.rmtree, str(repo), True)

        def git(*args):
            return subprocess.run(["git", "-C", str(repo)] + list(args), capture_output=True, text=True)
        git("init", "-q")
        git("config", "user.email", "test@example.invalid")
        git("config", "user.name", "gate test")
        git("config", "commit.gpgsign", "false")
        path = repo / gate.RULES_FILE
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(RULES_DATA), encoding="utf-8")
        git("add", "-A")
        self.assertEqual(git("commit", "-q", "-m", "rules").returncode, 0)
        path.write_text(json.dumps(lenient), encoding="utf-8")          # the attack: edit the working-tree rules
        with mock.patch.object(gate, "policy_root", lambda: repo):
            self.assertEqual(gate.rules()["packages"][self.PKG]["bench"], [1.05, "prev"])
            self.assertTrue(gate.rules()["_committed"])
        gate._RULES.clear()

    def test_a_record_made_by_an_edited_gate_script_is_no_hand_over(self):
        """The script half of budget_attack.py: a record made by a gate script that differs from the committed one
        (its blob hashes in meta "gate") fails GATE; only the gate tool's own package may record so, with the main
        session's gate-change signature naming that code."""
        rows = [trial("A-1", 0)]
        edited = dict(FAKE_CODE, **{"tools/orchestration/tier1_gate.py": "e" * 40})
        self.record(rows, rows, meta={"gate": {"code": edited, "digest": gate.code_digest(edited),
                                               "commit": FAKE_COMMIT}})
        code, text = self.compare()
        self.assertEqual(code, 1, text)
        self.assertIn("GATE FAIL  made by a gate script that differs", text)
        self.assertIn("tools/orchestration/tier1_gate.py", text)
        # the gate tool's own package: FAIL without, PASS with the gate-change signature
        self.meta.update(packages=["K-GATE3"], stage="P1C")
        self.record(rows, rows, meta={"gate": {"code": edited, "digest": gate.code_digest(edited),
                                               "commit": FAKE_COMMIT}})
        code, text = self.compare(package="K-GATE3")
        self.assertEqual(code, 1, text)
        self.assertIn('"kind": "gate-change"', text)
        signed = policy_files(signoffs={"X": [{"kind": "gate-change", "digest": gate.code_digest(edited)}]})
        code, text = self.compare(package="K-GATE3", policy=signed)
        self.assertEqual(code, 0, text)
        self.assertIn("gate-change", text)
        # a record without its gate code cannot be verified, and a comparing script that differs is no hand-over
        meta = dict(self.meta)
        meta.pop("gate")
        write_record(self.gates / "X", meta, own=rows)
        self.assertIn("no gate-code digest in the record", self.compare(package="K-GATE3")[1])
        self.meta.update(packages=[self.PKG], stage="ADOPT")
        self.record(rows, rows)
        out = io.StringIO()
        with mock.patch.object(gate, "trusted_json", policy_files()), \
                mock.patch.object(gate, "tree_fingerprint", lambda root: TREE), \
                mock.patch.object(gate, "changed_files", lambda root: ([], None)), \
                mock.patch.object(gate, "running_gate", lambda: {"script": "x", "files": edited, "code": edited,
                                                                 "digest": gate.code_digest(edited)}), \
                mock.patch.object(gate, "committed_gate", lambda commit="HEAD": dict(FAKE_CODE)), \
                contextlib.redirect_stdout(out):
            code = gate.main(["--root", str(ROOT), "--gates-root", str(self.gates), "compare", "C1", "X", "--package",
                              self.PKG])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("compare result: INCOMPLETE", out.getvalue())
        self.assertIn("the comparing gate script differs from the committed one", out.getvalue())

    def test_a_record_written_by_changed_gate_code_fails(self):
        out = Path(tempfile.mkdtemp(prefix="gate_code_"))
        self.addCleanup(shutil.rmtree, str(out), True)
        args = SimpleNamespace(root=str(ROOT), gates_root=str(out), id="EDITED", out=str(out / "EDITED"),
                               work=str(out / "work"), package=self.PKG, stage=None, only=None, skip=None, seeds=None,
                               jobs=1, extras=None, bench_repeats=1, prev="none", spec_files=None, reviewed=None,
                               camera=None, shared_tree=False)
        edited = dict(FAKE_CODE, **{"tools/orchestration/tier1_gate.py": "e" * 40})
        with mock.patch.object(gate, "trusted_json", policy_files()), \
                mock.patch.object(gate, "tree_fingerprint", lambda root: TREE), \
                mock.patch.object(gate, "changed_files", lambda root: ([], None)), \
                sealed(code=edited, committed=FAKE_CODE):
            g = gate.Gate(args)
            g.fingerprints = {}
            g.results = {c: {"status": "PASS", "note": "ok"} for c in gate.CHECKS}
            g.spec_rows = {"own": [], "realistic": []}
            g.spec_summary = {m: {"specs": 0, "passed": 0, "trials": 0, "failing": {}} for m in ("own", "realistic")}
            (out / "EDITED").mkdir()
            with contextlib.redirect_stdout(io.StringIO()):
                g.write(None, gate.now_iso(), {})
        meta = json.loads((out / "EDITED" / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["verdict"], "FAIL", meta["summary"])
        self.assertIn("failing: GATE", meta["summary"])
        self.assertEqual(meta["gate"]["differs"], ["tools/orchestration/tier1_gate.py"])
        self.assertEqual(meta["gate"]["code"], edited)
        self.assertEqual({c["gate"] for c in meta["checks"].values()}, {gate.code_digest(edited)})

    # ---- 2. G10 attributed protected files and other owners' tests to concurrent peers
    def test_protected_files_are_never_attributed_and_peers_must_own_the_file(self):
        """scope_attacks.py: with --shared-tree a domain package passed G10 after editing the gate script (attributed
        to K-EVAL), planting an allowlist entry (to K-GATE) or loosening another owner's test (to K-CAM)."""
        protected = ["tools/orchestration/tier1_gate.py", gate.KNOWN_FAILING, ".github/workflows/ci.yml",
                     "tests/data/room_golden.json", "tests/data/gen1_digests.json", gate.RULES_FILE]
        running = copy.deepcopy(RULES_DATA)
        running["running_substages"] = ["P1C"]              # the close-out round, while it ran (not the live policy)
        with mock.patch.object(gate, "trusted_json", policy_files(rules=running)):
            for pkg in ("ADOPT:cleaning", "P1C:pets"):
                for f in protected:
                    with mock.patch.object(gate, "changed_files", lambda root, f=f: ([f], None)):
                        result = gate.scope_check(ROOT, gate.policy_for([pkg]), pkg, shared_tree=True)
                    self.assertEqual((result["status"], result["attributed"], result["protected"]), ("FAIL", {}, [f]),
                                     (pkg, f))
            # the running sub-stage (P1C): a file outside the package goes to a peer only when its own scope has it
            changed = (["house_sim/systems/pets/agents.py", "tests/test_cleaning.py", "tests/test_perception.py",
                        "house_sim/systems/food/model.py", "catalog/analysis/tier1/gate/P1C_electrical/meta.json"], None)
            with mock.patch.object(gate, "changed_files", lambda root: changed):
                result = gate.scope_check(ROOT, gate.policy_for(["P1C:pets"]), "P1C_pets", shared_tree=True)
        self.assertEqual(result["attributed"], {"tests/test_cleaning.py": "P1C:mechanisms",
                                                "catalog/analysis/tier1/gate/P1C_electrical/meta.json": "P1C:electrical"})
        self.assertEqual(sorted(result["outside"]), ["house_sim/systems/food/model.py", "tests/test_perception.py"])
        self.assertIn("tests/test_cleaning.py -> P1C:mechanisms", result["note"])        # listed for review
        self.assertEqual(result["historical"], [])

    def test_an_attribution_in_a_sub_stage_that_is_not_running_is_no_hand_over(self):
        out = Path(tempfile.mkdtemp(prefix="gate_hist_"))
        self.addCleanup(shutil.rmtree, str(out), True)
        write_record(out / "C1", self.prev_meta, own=[trial("C-1", 0, file=FILE_C)])
        args = SimpleNamespace(root=str(ROOT), gates_root=str(out), id="ADOPT_cleaning", out=str(out / "ADOPT_cleaning"),
                               work=str(out / "work"), package="ADOPT:cleaning", stage=None, only="G10", skip=None,
                               seeds=None, jobs=1, extras=None, bench_repeats=1, prev=None, spec_files=None,
                               reviewed=None, camera=None, shared_tree=True)
        changed = (["house_sim/systems/cleaning/floors.py", "house_sim/kernel/camera.py"], None)
        with mock.patch.object(gate, "trusted_json", policy_files()), \
                mock.patch.object(gate, "tree_fingerprint", lambda root: TREE), \
                mock.patch.object(gate, "changed_files", lambda root: changed), sealed():
            g = gate.Gate(args)
            scope = gate.scope_check(ROOT, g.policy, g.id, True)
            g.data["scope"]["G10"] = scope
            g.fingerprints = {}
            g.results = {"G10": {"status": scope["status"], "note": scope["note"]}}
            (out / "ADOPT_cleaning").mkdir()
            with contextlib.redirect_stdout(io.StringIO()):
                g.write(g.prev_dir(), gate.now_iso(), {})
        self.assertEqual((scope["status"], scope["attributed"]), ("PASS", {"house_sim/kernel/camera.py": "K-CAM"}))
        self.assertEqual(scope["historical"], ["ADOPT", "P1"])
        meta = json.loads((out / "ADOPT_cleaning" / "meta.json").read_text(encoding="utf-8"))
        self.assertFalse(meta["handover"], meta["summary"])
        self.assertIn("sub-stage(s) ADOPT, P1 that are not running", meta["incomplete"])

    # ---- 3. a hand-edited room golden neutralised any pin
    def golden_records(self, prev_entries, cur_entries, reviewed=None, package="P1C-INT"):
        self.meta.update(packages=[package], stage="P1C")
        rows = [trial("A-1", 0)]
        self.record(rows, rows, cur_tests={"golden": {"entries": cur_entries, "base": "x", "changes": []}})
        gate.write_json(self.gates / "C1" / "tests.json", {"golden": {"entries": prev_entries}})
        if reviewed is not None:
            gate.write_json(self.gates / "X" / "reviewed.json", reviewed)

    def test_every_re_recorded_golden_entry_needs_a_reviewed_and_a_signed_row(self):
        """golden_attacks.py G2: an entry's digest changed by hand and marked known-failing passed G8. The gate now
        lists every entry that differs from the previous record's as a re-record needing two signatures."""
        before = {"specs/T-1": "a" * 16, "specs/T-2": "b" * 16, "runs/0/oracle": "c" * 16}
        after = dict(before, **{"specs/T-1": "e" * 16})
        self.golden_records(before, before)
        code, text = self.compare(package="P1C-INT")
        self.assertEqual(code, 0, text)
        self.assertIn("GOLD PASS  room golden: 0 entr(ies) re-recorded vs the previous record C1", text)
        self.golden_records(before, after)
        code, text = self.compare(package="P1C-INT")
        self.assertEqual(code, 1, text)
        self.assertIn("1 without the package's reviewed golden row: specs/T-1 (edited)", text)
        self.assertIn("1 without the main session's signed golden row", text)
        review = [{"kind": "golden", "key": "specs/*", "code": "P1C"}]
        self.golden_records(before, after, reviewed=review)
        self.assertEqual(self.compare(package="P1C-INT")[0], 1)                     # reviewed, not signed
        signed = policy_files(signoffs={"X": [{"kind": "golden", "key": "specs/T-1", "digest": "e" * 16}]})
        code, text = self.compare(package="P1C-INT", policy=signed)
        self.assertEqual(code, 0, text)
        stale = policy_files(signoffs={"X": [{"kind": "golden", "key": "specs/T-1", "digest": "a" * 16}]})
        self.assertEqual(self.compare(package="P1C-INT", policy=stale)[0], 1)       # a signature names one digest
        # a package that does not own the golden cannot review it (a domain package's row counts for nothing)
        self.golden_records(before, after, reviewed=review, package="P1C:pets")
        self.assertEqual(self.compare(package="P1C:pets", policy=signed)[0], 1)
        # the signoff draft lists the row for the main session
        with mock.patch.object(gate, "trusted_json", policy_files()), sealed():
            draft = gate.signoff_draft(self.gates / "C1", self.gates / "X", gate.policy_for(["P1C:pets"]), [])
        self.assertIn({"kind": "golden", "key": "specs/T-1", "digest": "e" * 16, "change": "edited",
                       "reviewed_by_package": False}, draft["rows"])

    def test_golden_entries_and_the_base_of_a_record(self):
        data = {"format": "f", "camera": "room", "fixed_specs": ["T-1"], "history": [{"by": "x"}],
                "runs": {"0/oracle": {"steps": []}}, "specs": {"T-1": {"state": "s"}}}
        entries = gate.golden_entries(data)
        self.assertEqual(sorted(entries), ["meta/camera", "meta/fixed_specs", "meta/format", "runs/0/oracle",
                                           "specs/T-1"])
        edited = copy.deepcopy(data)
        edited["specs"]["T-1"].update(known_failing=True, passes=False)         # the attack's hand edit
        edited["history"].append({"by": "y"})                                  # the history is no entry
        self.assertEqual(gate.golden_changes(entries, gate.golden_entries(edited)),
                         [{"key": "specs/T-1", "change": "edited", "digest": gate.golden_entries(edited)["specs/T-1"]}])
        self.assertEqual([c["change"] for c in gate.golden_changes(None, entries)], ["added"] * 5)
        # the real golden at HEAD: the base of a record against a previous record without entries
        if shutil.which("git"):
            head = gate.golden_at("HEAD")
            self.assertTrue(head and any(k.startswith("specs/") for k in head))

    # ---- 4. a failing correctness test disguised as a wall-clock bound
    def test_a_failing_test_disguised_as_a_bound_fails_the_correctness_run(self):
        """perf_disguise.py: a test that skipped with the bound's message when HOUSE_SIM_PERF_TESTS=0 and otherwise
        asserted 1 + 1 == 3 kept CI's correctness step green. Bounds run relaxed now: the body runs, so the failure
        fails; a test that still skips its body fails the run; an unregistered perf mark fails it too."""
        tmp = Path(tempfile.mkdtemp(prefix="gate_disguise_"))
        self.addCleanup(shutil.rmtree, str(tmp), True)
        (tmp / "tests").mkdir()
        (tmp / "tests" / "test_ver_disguise.py").write_text(DISGUISED_FILE, encoding="utf-8")
        fails = "tests.test_ver_disguise.DisguisedTests.test_a_correctness_check_that_fails"
        skips = "tests.test_ver_disguise.DisguisedTests.test_one_that_never_runs_its_body"
        out = tmp / "out.json"
        proc = subprocess.run([sys.executable, str(ROOT / "tools" / "orchestration" / "tier1_gate.py"), "_unittest",
                               str(out), "tests/test_ver_disguise.py"], cwd=str(tmp),
                              env=dict(os.environ, HOUSE_SIM_PERF_TESTS="0"), capture_output=True, text=True,
                              timeout=300)
        self.assertEqual(proc.returncode, 1, proc.stderr)                       # the CI correctness step: red
        self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["tests"], {fails: "fail", skips: "perf"})
        green = str(ROOT / "tools" / "orchestration" / "green_tests.py")
        registry = tmp / "registry.json"
        gate.write_json(registry, {"format": gate._wallclock().REGISTRY_FORMAT, "bounds": [
            {"test": fails, "owner": "o"}, {"test": skips, "owner": "o"}]})
        quiet = {k: v for k, v in os.environ.items() if k != "HOUSE_SIM_PERF_TESTS"}
        proc = subprocess.run([sys.executable, green, "1", "--modes", "normal", "--tests-dir", str(tmp / "tests"),
                               "--registry", str(registry), "--json", str(tmp / "g.json")], env=quiet,
                              capture_output=True, text=True, timeout=300)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        result = json.loads((tmp / "g.json").read_text(encoding="utf-8"))[0]
        self.assertEqual(result["bounds_skipped"], [skips])
        self.assertIn(fails, result["outcomes"]["fail"])
        # registered and passing, but unregistered: a new perf mark is visible in review
        (tmp / "tests" / "test_ver_disguise.py").write_text(DISGUISED_FILE.replace("1 + 1, 3", "1 + 1, 2").replace(
            "!= '1'", "== '0'"), encoding="utf-8")
        gate.write_json(registry, {"format": gate._wallclock().REGISTRY_FORMAT, "bounds": [{"test": fails,
                                                                                             "owner": "o"}]})
        proc = subprocess.run([sys.executable, green, "1", "--modes", "normal", "--tests-dir", str(tmp / "tests"),
                               "--registry", str(registry), "--json", str(tmp / "g.json")], env=quiet,
                              capture_output=True, text=True, timeout=300)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("UNREGISTERED wall-clock bound(s)", proc.stdout)
        self.assertIn(skips, proc.stdout)
        gate.write_json(registry, {"format": gate._wallclock().REGISTRY_FORMAT, "bounds": [
            {"test": fails, "owner": "o"}, {"test": skips, "owner": "o"}]})
        proc = subprocess.run([sys.executable, green, "1", "--modes", "normal", "--tests-dir", str(tmp / "tests"),
                               "--registry", str(registry)], env=quiet, capture_output=True, text=True, timeout=300)
        self.assertEqual(proc.returncode, 0, proc.stdout)

    def test_a_bound_that_skips_its_body_counts_as_a_skip_in_the_record(self):
        """G1b (and G1, G8) count a perf-marked test that never ran its body as a skip, whatever the previous record
        ran -- unless the committed registry gives it a dated skip exception pinned to its exact source."""
        bound = "tests.test_x.T.test_c"
        cur = {"G1b": [row("test_x.py", mode="core", skipped=1, ids=RedTeamGateTests.IDS,
                           outcomes={"perf": [bound]})],
               "perf": [{"file": "test_x.py", "mode": "normal", "ids": [bound], "failed": [], "attempts": {bound: 1}}]}
        bad, _ = gate.test_regressions({}, cur, perf_ok=set())                  # no previous rows at all
        self.assertEqual([(b[0], b[3]) for b in bad], [("G1b", bound)])
        self.assertIn("skipped its body", bad[0][4])
        self.assertEqual(gate.test_regressions({}, cur, perf_ok={bound}), ([], []))     # its registry exception
        self.assertEqual(gate.test_regressions({}, cur), ([], []))             # a record from before K-GATE3
        wc = gate._wallclock()
        mark = {"kind": "guard", "source_sha256": "s" * 64}
        entry = {"test": bound, "owner": "o", "skip": {"source_sha256": "s" * 64, "expires": "2999-01-01",
                                                       "reason": "r"}}
        self.assertTrue(wc.skip_excused(entry, mark))
        self.assertFalse(wc.skip_excused(entry, dict(mark, source_sha256="t" * 64)))   # the method was edited
        self.assertFalse(wc.skip_excused(dict(entry, skip=dict(entry["skip"], expires="2000-01-01")), mark))
        self.assertEqual(wc.perf_plan({bound: mark}, {bound: entry}), {bound: "skip"})
        self.assertEqual(wc.perf_plan({bound: mark}, {}), {bound: "count"})
        self.assertEqual(wc.unregistered({bound: mark}, {}), [bound])

    def test_compare_requires_the_bound_scan_and_the_lint_of_a_new_record(self):
        rows = [trial("A-1", 0)]
        tests = tests_json([row("test_x.py", ids=RedTeamGateTests.IDS)])
        bound = "tests.test_x.T.test_c"
        tests.update(bounds={"marks": {bound: {"kind": "guard", "source_sha256": "s" * 64, "file": "test_x.py"}}},
                     py39={"rc": 0, "files": 3, "count": 0, "violations": []})
        self.record(rows, rows, cur_tests=tests)
        code, text = self.compare()
        self.assertEqual(code, 1, text)
        self.assertIn("1 unregistered wall-clock bound(s)", text)
        registry = dict(REGISTRY_DATA, bounds=REGISTRY_DATA["bounds"] + [{"test": bound, "owner": "o"}])
        code, text = self.compare(policy=policy_files(registry=registry))
        self.assertEqual(code, 0, text)
        self.assertIn("Python 3.9 lint: 3 file(s), 0 violations", text)
        tests["py39"] = {"rc": 1, "files": 3, "count": 1, "violations": ["tools/x.py:3: zip(strict=) is 3.10+"]}
        self.record(rows, rows, cur_tests=tests)
        code, text = self.compare(policy=policy_files(registry=registry))
        self.assertEqual(code, 1, text)
        self.assertIn("zip(strict=) is 3.10+", text)
        del tests["py39"], tests["bounds"]
        self.record(rows, rows, cur_tests=tests, meta={"recorded": list(gate.CHECKS)})
        code, text = self.compare(policy=policy_files(registry=registry))
        self.assertEqual(code, 1, text)
        self.assertIn("no wall-clock bound scan in this record", text)
        self.assertIn("no Python 3.9 lint result in this record", text)

    def test_the_registry_lists_every_bound_of_the_project(self):
        wc = gate._wallclock()
        marks = wc.flat_marks(wc.perf_marks(sorted((ROOT / "tests").glob("test_*.py"))))
        bounds = wc.registry_entries(REGISTRY_DATA)["bounds"]
        self.assertEqual(wc.unregistered(marks, bounds), [])
        self.assertEqual(sorted(bounds), sorted(marks))
        self.assertEqual(wc.registry_entries(REGISTRY_DATA)["invalid"], [])
        # the thermal bound's dated skip exception went with its conversion to wall_clock_bound (P1C countersign)
        self.assertEqual([t for t, e in bounds.items() if e.get("skip")], [])

    # ---- 5. the phase-2 packages (docs/design/VERIFY_HARDENING.md section 25)
    def test_the_phase_2_packages(self):
        with mock.patch.object(gate, "trusted_json", policy_files()):
            pk, sub = gate.PACKAGES, gate.SUBSTAGES
            self.assertEqual(list(sub["P2-S1"]), ["P2-WP1", "P2-WP2", "P2-WP3", "P2-WP4", "P2-WP7", "P2-WP9",
                                                  "P2-WP11"])
            self.assertEqual(list(sub["P2-S2"]), ["P2-WP5", "P2-WP8"])
            self.assertEqual(list(sub["P2-S3"]), ["P2-WP6"] + ["P2-WPD:" + d for d in gate.DOMAINS])
            self.assertEqual(list(sub["P2-S4"]), ["P2-WP10"])
            prev = gate.DEFAULT_PREV
            self.assertEqual([prev[s] for s in ("P1C", "P2-S1", "P2-S2", "P2-S3", "P2-S4", "P2-S5", "P2-S6", "P2")],
                             ["P1F", "P1C", "P2-S1", "P2-S2", "P2-S3", "P2-S4", "P2-S5", "P1C"])
            self.assertEqual(gate.resolve_package("P2"), "P2-ALL")
            union = set(pk["P2-ALL"]["files"])
            for pid in [p for p in pk if p.startswith("P2-") and p != "P2-ALL"]:
                self.assertTrue(set(pk[pid]["files"]) <= union, pid)
            for pid, needle in (("P2-WP1", "house_sim/kernel/ledger.py"), ("P2-WP2", "house_sim/kernel/world.py"),
                                ("P2-WP5", "house_sim/tasks/verify.py"), ("P2-WP6", "house_sim/tasks/battery/hidden.py"),
                                ("P2-WP8", "house_sim/tasks/lint.py"), ("P2-WP10", "tools/orchestration/tier1_gate.py"),
                                ("P2-WPD:pets", "house_sim/systems/pets/*")):
                self.assertIn(needle, pk[pid]["files"], pid)
            # only the gate tool's packages (WP10, its stage, the exit fixes, the union) reach the gate script
            reach = sorted(p for p in pk if p.startswith("P2") and any(
                fnmatch.fnmatchcase("tools/orchestration/tier1_gate.py", g) for g in pk[p]["files"]))
            self.assertEqual(reach, ["P2-ALL", "P2-S4", "P2-S6", "P2-WP10"])
            self.assertTrue(all(pk[p]["gate_tool"] for p in reach))
            # one owner per file inside a sub-stage, apart from the shared re-records and the phase's report folder
            shared = {"tests/data/room_golden.json", "catalog/analysis/phase2/*"} | set(
                ["tasks/specs.jsonl", "tasks/id_to_family.jsonl"])
            for key in ("P2-S1", "P2-S2", "P2-S3"):
                seen = {}
                for pid in sub[key]:
                    for f in set(pk[pid]["files"]) - shared:
                        self.assertNotIn(f, seen, (key, pid, f, seen.get(f)))
                        seen[f] = pid
            self.assertEqual(pk["P2-WP2"]["bench"], [1.15, "prev"])            # WP2's 15 % attribution overhead
            self.assertEqual(pk["P2-WP1"]["allow"], [])                        # no schema 2/3 outcome may change
            self.assertEqual(pk["P1C"]["members"], ["P1C:" + d for d in (
                "admin_inventory", "electrical", "manipulation", "mechanisms", "navigation_robot", "pets")] +
                ["K-GATE3"])
            self.assertIn("house_sim/data/types/cleaning.json", pk["P1C:mechanisms"]["files"])
            self.assertTrue(pk["P1C"]["gate_tool"] and pk["P1C"]["policy"])


if __name__ == "__main__":
    unittest.main()
