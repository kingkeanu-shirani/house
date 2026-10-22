"""The tier-1 gate tool (tools/orchestration/tier1_gate.py): the adoption round's fixes (K-EVAL).

* spec chunks keep the authored key order (a skill may depend on a dict's order; C1 request 4);
* G9's physics audit writes into the gate folder, never into %TEMP% (C1 request 5);
* the close steps' file lists name what `--verify` rewrites (coverage.csv, the compiled specs; C1 request 2);
* the adoption packages: ADOPT:<domain>, K-EVAL, K-CAM and the ADOPT union; package ids with a colon as folder names;
* compare restricts the previous gate to the current record's --spec-files (no "missing" trials for a narrowed
  record); compare takes --shared-tree (and its own --spec-files) and defaults to the package's own record and that
  record's previous gate; --shared-tree attributes the concurrently running adoption packages;
* isolated copies (cmd_isolate) carry the empty directories git does not and report exactly their overlay as
  changed (G10), without committing anything.
"""
import contextlib
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
_SPEC = importlib.util.spec_from_file_location("tier1_gate_under_test", str(ROOT / "tools" / "orchestration" /
                                                                             "tier1_gate.py"))
gate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gate)


def trial(key, seed, file, passed=True, state="s0"):
    return {"key": key, "catalog_id": key, "seed": seed, "file": file, "spec_hash": "h-" + key, "passed": passed,
            "initial_fails": True, "idle_fails": True, "reference_passes": passed, "perceivable": None,
            "wrong_fails": True, "wrong_ran": True, "wrong": [], "reference_state_hash": state,
            "reference_obs_hash": "o"}


def write_record(folder, meta, own=(), realistic=()):
    folder.mkdir(parents=True, exist_ok=True)
    gate.write_json(folder / "meta.json", meta)
    for name, rows in (("specs_own.jsonl", own), ("specs_realistic.jsonl", realistic)):
        with open(folder / name, "w", encoding="utf-8", newline="\n") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")


class PackageTableTests(unittest.TestCase):
    def test_adoption_packages_and_close_lists(self):
        for d in gate.DOMAINS:
            pkg = gate.PACKAGES["ADOPT:" + d]
            self.assertEqual(pkg["stage"], "ADOPT")
            self.assertIn("tasks/authored/{}__*.json".format(d), pkg["files"])
            self.assertIn("tasks/specs.jsonl", pkg["files"])          # the compiled specs follow its spec edits
        self.assertEqual(gate.resolve_package("ADOPT"), "ADOPT-ALL")
        union = gate.PACKAGES["ADOPT-ALL"]["files"]
        for pid in ("K-EVAL", "K-CAM", "ADOPT:cleaning"):
            self.assertTrue(set(gate.PACKAGES[pid]["files"]) <= set(union), pid)
        self.assertEqual(gate.PACKAGES["K-EVAL"]["allow"], [])          # the evaluation side changes no verdict
        self.assertEqual(set(gate.SUBSTAGES["ADOPT"]), {"ADOPT:" + d for d in gate.DOMAINS} | {"K-EVAL", "K-CAM", "ADOPT-INT"})
        # the integrator's own package (kernel-side fixes, re-records) is part of the union; K-CAM owns skills.py
        self.assertTrue(set(gate.PACKAGES["ADOPT-INT"]["files"]) <= set(union))
        self.assertIn("gen1", gate.PACKAGES["ADOPT-ALL"]["allow"])
        self.assertIn("house_sim/tasks/skills.py", gate.PACKAGES["K-CAM"]["files"])
        self.assertFalse(any(f.startswith("docs/domains") or f.startswith("house_sim/systems/") and
                             not f.endswith("core.py") for f in gate.PACKAGES["ADOPT-INT"]["files"]))
        for pid in ("B-CLOSE", "C-CLOSE"):
            for rel in ("coverage.csv", "tasks/specs.jsonl", "tasks/id_to_family.jsonl"):
                self.assertIn(rel, gate.PACKAGES[pid]["files"], (pid, rel))
        self.assertEqual(gate.gate_dirname("ADOPT:cleaning"), "ADOPT_cleaning")
        self.assertEqual(gate.DEFAULT_PREV["ADOPT"], "C1")

    def test_spec_chunks_keep_the_authored_key_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "chunk.json"
            gate.write_json(path, {"fields": {"zeta": 1, "alpha": 2}}, sort_keys=False)
            self.assertEqual(list(json.loads(path.read_text(encoding="utf-8"))["fields"]), ["zeta", "alpha"])
            gate.write_json(path, {"fields": {"zeta": 1, "alpha": 2}})
            self.assertEqual(list(json.loads(path.read_text(encoding="utf-8"))["fields"]), ["alpha", "zeta"])
        source = (ROOT / "tools" / "orchestration" / "tier1_gate.py").read_text(encoding="utf-8")
        self.assertIn('write_json(cpath, chunk, sort_keys=False)', source)
        self.assertIn('write_json(self.work / "all_specs.json", all_specs, sort_keys=False)', source)

    def test_the_physics_extra_writes_into_the_gate_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = SimpleNamespace(root=str(ROOT), gates_root=tmp, id="K-EVAL", out=None, work=tmp, package="K-EVAL",
                                   stage=None, only="G9", skip=None, seeds=None, jobs=1, extras="physics",
                                   bench_repeats=1, prev="none")
            g = gate.Gate(args)
            jobs = g.extra_jobs(quiet=True)
        cmd = jobs[0].cmd
        self.assertIn("--out", cmd)
        self.assertEqual(Path(cmd[cmd.index("--out") + 1]), Path(tmp).resolve() / "K-EVAL" / "physics_audit.json")


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.gates = Path(tempfile.mkdtemp(prefix="gate_cmp_"))
        self.addCleanup(shutil.rmtree, str(self.gates), True)
        rows = [trial("A-1", 0, "cleaning__examples.json"), trial("B-1", 0, "food__examples.json")]
        write_record(self.gates / "C1", {"id": "C1", "stage": "C1", "finished": "2026-10-15"}, own=rows,
                     realistic=rows)
        narrowed = [trial("A-1", 0, "cleaning__examples.json")]
        write_record(self.gates / "ADOPT_cleaning",
                     {"id": "ADOPT_cleaning", "packages": ["ADOPT:cleaning"], "stage": "ADOPT", "prev": "C1",
                      "spec_files": "cleaning__*", "finished": "2026-10-20"}, own=narrowed, realistic=narrowed)

    def run_compare(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = gate.main(["--root", str(ROOT), "--gates-root", str(self.gates), "compare"] + list(argv))
        return code, out.getvalue()

    def test_a_narrowed_record_is_compared_on_its_own_spec_files(self):
        """Adoption-round finding: a record made with --spec-files was compared with every trial of the previous
        gate, so every other domain's trials were "missing" and compare failed."""
        code, text = self.run_compare("C1", "ADOPT_cleaning", "--package", "ADOPT:cleaning")
        self.assertEqual(code, 0, text)
        self.assertIn("1 trials vs 1", text)
        self.assertNotIn("missing", text)
        self.assertIn("spec files: cleaning__*", text)

    def test_compare_defaults_to_the_packages_record_and_its_previous_gate(self):
        code, text = self.run_compare("--package", "ADOPT:cleaning")
        self.assertEqual(code, 0, text)
        self.assertIn("compare C1 -> ADOPT_cleaning", text)

    def test_compare_takes_shared_tree_and_spec_files(self):
        changed = (["house_sim/systems/cleaning/floors.py", "house_sim/systems/food/model.py",
                    "house_sim/kernel/camera.py", "tasks/specs.jsonl", "catalog/analysis/tier1/handoff_K-EVAL.md",
                    "catalog/analysis/tier1/gate/ADOPT_food/meta.json"], None)
        with mock.patch.object(gate, "changed_files", lambda root: changed):
            code, text = self.run_compare("--package", "ADOPT:cleaning", "--shared-tree", "--spec-files",
                                          "cleaning__ex*")
        self.assertEqual(code, 0, text)
        self.assertIn("G10  PASS", text)
        self.assertIn("4 attributed to concurrent packages", text)
        self.assertIn("ADOPT:food 2", text)
        changed[0].append("RESUME.md")                                  # nobody's file in this round
        with mock.patch.object(gate, "changed_files", lambda root: changed):
            code, text = self.run_compare("--package", "ADOPT:cleaning", "--shared-tree")
        self.assertEqual(code, 1)
        self.assertIn("outside: RESUME.md", text)

    def test_compare_without_a_record_says_how_to_make_one(self):
        with mock.patch.object(gate, "changed_files", lambda root: ([], None)):
            code, text = self.run_compare("--package", "ADOPT:food", "--shared-tree")
        self.assertEqual(code, 2)
        self.assertIn("record --id ADOPT_food --package ADOPT:food", text)
        self.assertIn("--spec-files 'food__*'", text)
        self.assertIn("G10  PASS", text)

    def test_the_record_time_comparison_is_restricted_too(self):
        prev_rows = [trial("A-1", 0, "cleaning__examples.json"), trial("B-1", 0, "food__examples.json")]
        globs = gate.spec_scope({}, {"spec_files": "cleaning__*"})
        self.assertEqual([r["key"] for r in gate.scoped_rows(prev_rows, globs)], ["A-1"])
        self.assertEqual(gate.scoped_rows(prev_rows, []), prev_rows)


class ScopeTests(unittest.TestCase):
    def test_shared_tree_attributes_concurrent_adoption_packages(self):
        """Adoption-round finding: --shared-tree attributed only the packages of SUBSTAGES, which had no adoption
        entry, so every other domain's files failed G10."""
        files = ["house_sim/systems/pets/cues.py", "tests/test_pets.py", "docs/domains/pets.md",
                 "house_sim/kernel/sight.py", "catalog/analysis/tier1/handoff_ADOPT_pets.md",
                 "catalog/analysis/tier1/gate/K-CAM/meta.json", "house_sim/eval/viewer.py"]
        with mock.patch.object(gate, "changed_files", lambda root: (files, None)):
            mine = gate.scope_check(ROOT, gate.policy_for(["K-EVAL"]), "K-EVAL", shared_tree=True)
            alone = gate.scope_check(ROOT, gate.policy_for(["K-EVAL"]), "K-EVAL", shared_tree=False)
        self.assertEqual(mine["status"], "PASS", mine)
        self.assertEqual(mine["attributed"]["house_sim/systems/pets/cues.py"], "ADOPT:pets")
        self.assertEqual(mine["attributed"]["catalog/analysis/tier1/handoff_ADOPT_pets.md"], "ADOPT:pets")
        self.assertEqual(mine["attributed"]["house_sim/kernel/sight.py"], "K-CAM")
        self.assertEqual(mine["attributed"]["catalog/analysis/tier1/gate/K-CAM/meta.json"], "K-CAM")
        self.assertEqual(alone["status"], "FAIL")
        self.assertNotIn("house_sim/eval/viewer.py", alone["outside"])


@unittest.skipUnless(shutil.which("git"), "git is not available")
class IsolateTests(unittest.TestCase):
    def test_an_isolated_copy_has_the_empty_dirs_and_reports_its_overlay(self):
        probe = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True)
        if probe.returncode != 0:
            self.skipTest("not a git checkout")
        with tempfile.TemporaryDirectory() as tmp:
            copy_ = Path(tmp) / "iso"
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = gate.main(["--root", str(ROOT), "isolate", "--out", str(copy_), "--files",
                                  "tools/orchestration/tier1_gate.py"])
            self.assertEqual(code, 0, out.getvalue())
            for rel in gate.EMPTY_DIRS:
                self.assertTrue((copy_ / rel).is_dir(), rel)
            manifest = json.loads((copy_ / gate.ISOLATE_MANIFEST).read_text(encoding="utf-8"))
            self.assertEqual(manifest["base"], probe.stdout.strip())
            self.assertEqual(manifest["overlay"], ["tools/orchestration/tier1_gate.py"])
            self.assertEqual((copy_ / "tools/orchestration/tier1_gate.py").read_bytes(),
                             (ROOT / "tools/orchestration/tier1_gate.py").read_bytes())
            changed, error = gate.changed_files(copy_)
            head_gate = subprocess.run(["git", "-C", str(ROOT), "show", "HEAD:tools/orchestration/tier1_gate.py"],
                                       capture_output=True).stdout
            expected = [] if head_gate.replace(b"\r\n", b"\n") == (ROOT / "tools/orchestration/tier1_gate.py").read_bytes() \
                else ["tools/orchestration/tier1_gate.py"]
            self.assertEqual((changed, error), (expected, None))
            (copy_ / "docs" / "NEW_NOTE.md").write_text("x\n", encoding="utf-8")
            (copy_ / "__pycache__").mkdir()
            (copy_ / "__pycache__" / "junk.pyc").write_bytes(b"\0")
            self.assertEqual(gate.changed_files(copy_)[0], sorted(expected + ["docs/NEW_NOTE.md"]))
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit):        # never over an existing folder
                    gate.main(["--root", str(ROOT), "isolate", "--out", str(copy_)])


if __name__ == "__main__":
    unittest.main()
